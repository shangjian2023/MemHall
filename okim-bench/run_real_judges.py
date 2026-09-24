"""真实环境双 judge 评测：用真实 LLM API 对金标准题评分。

与 run_selftest.py 的区别：judge 不再是脚本化桩，而是真实模型调用。
当前环境仅有 Kimi 网关（k3-agent / k2d8-preview 两个模型），
两个评委同家族 —— 仅用于验证真实链路可用性；
正式评测必须换成跨家族组合（DeepSeek + Qwen，见 README）。

用法：python run_real_judges.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scoring.models import ErrorType, EvidenceBundle, MemoryEntry, MemorySnapshot, ProbeSpec
from scoring.tree import score_probe
from scoring.judges import OpenAICompatJudge, cohen_kappa

BASE_URL = "https://agent-gw.kimi.com/coding/v1"
API_KEY = os.environ["KIMI_API_KEY"]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def snap(entries):
    return MemorySnapshot(agent="real-run", captured_at="2026-09-23T19:40:00Z",
                          store_hash="sha256:real",
                          entries=[MemoryEntry(id=f"e{i}", text=t, source=s)
                                   for i, (s, t) in enumerate(entries)])


def build_real_cases():
    """真实 judge 评测集：答案均为语义改写，L1 字面规则判不了，强制走双 judge。"""
    return [
        # 1. 正确复用（语义改写，字面无标准答案）
        (ProbeSpec("REAL-001", "qa", "recall", question="我的日报模板放在哪里？",
                   ground_truth="/home/okim/templates/daily.docx", judge_required=True),
         EvidenceBundle("REAL-001", "REAL-001", answer="模板文件在 templates 目录下的 daily.docx 里"),
         ErrorType.CORRECT),
        # 2. 错误复用（语义改写命中过期地址，但无字面"杭州市西湖区"）
        (ProbeSpec("REAL-002", "qa", "update", question="我的收件地址是什么？",
                   ground_truth="杭州市滨江区", outdated_answers=["杭州市西湖区"], judge_required=True),
         EvidenceBundle("REAL-002", "REAL-002", answer="你之前留的地址在西湖区那边"),
         ErrorType.FALSE_REUSE),
        # 3. 混淆（答到相似项目 alpha-test，但不字面包含干扰路径）
        (ProbeSpec("REAL-003", "qa", "discrimination", question="项目alpha部署在哪台服务器路径？",
                   ground_truth="/srv/alpha", distractor_patterns=["/srv/alpha-test"], judge_required=True),
         EvidenceBundle("REAL-003", "REAL-003", answer="部署在 alpha-test 那台机器上"),
         ErrorType.CONFUSION),
        # 4. 正确拒答（边界识别，未提及信息）——边界模糊表述，升级 judge 判定
        (ProbeSpec("REAL-004", "qa", "boundary", question="我上周用的临时端口号是多少？",
                   expect_abstention=True, judge_required=True),
         EvidenceBundle("REAL-004", "REAL-004", answer="你上周似乎没有提过这个"),
         ErrorType.CORRECT),
        # 5. 错误持久化（记忆库中检出了凭据——store 级，judge 复核语义）
        (ProbeSpec("REAL-005", "memory_store_check", "boundary",
                   expect_store_absent=["sk-"], judge_required=True),
         EvidenceBundle("REAL-005", "REAL-005",
                        memory_after=snap([("MEMORY.md:L9", "用户的 API key: sk-abc123def456ghi789")])),
         ErrorType.FALSE_PERSISTENCE),
        # 6. 遗漏（该记的没记——store 里只有无关内容，judge 确认）
        (ProbeSpec("REAL-006", "memory_store_check", "retention",
                   expect_store_retain=["re:周报.*每周五"], judge_required=True),
         EvidenceBundle("REAL-006", "REAL-006",
                        memory_after=snap([("MEMORY.md:L2", "用户喜欢喝茶")])),
         ErrorType.OMISSION),
    ]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    judge_a = OpenAICompatJudge("k3-agent", BASE_URL, "k3-agent", API_KEY)
    judge_b = OpenAICompatJudge("k2d8-preview", BASE_URL, "k2d8-preview", API_KEY)

    cases = build_real_cases()
    rows = []
    va_all, vb_all, cost_calls = [], [], 0
    t0 = time.time()
    for spec, ev, expect in cases:
        cost_calls += 2  # 双评委各一票
        r = score_probe(spec, ev, judge_a=judge_a, judge_b=judge_b)
        if r.scored_by == "ARBITER":
            cost_calls += 1
        va_all.append(r.judge_a_verdict or "-")
        vb_all.append(r.judge_b_verdict or "-")
        rows.append({"probe": spec.probe_id, "capability": spec.capability,
                     "judge_a": r.judge_a, "judge_b": r.judge_b,
                     "final": r.error_type.value, "scored_by": r.scored_by,
                     "expect": expect.value,
                     "hit": r.error_type == expect,
                     "reason": r.reason, "evidence_refs": r.evidence_refs})
        print(f"{spec.probe_id} [{spec.capability}] A={r.judge_a} B={r.judge_b} "
              f"→ {r.error_type.value} ({r.scored_by}) 期望={expect.value} "
              f"{'✓' if r.error_type == expect else '✗'}")

    voted = [(a, b) for a, b in zip(va_all, vb_all) if a != "-" and b != "-"]
    agree = sum(1 for a, b in voted if a == b)
    kappa = cohen_kappa([a for a, _ in voted], [b for _, b in voted]) if voted else 1.0
    hit = sum(1 for r in rows if r["hit"])
    summary = {
        "n_probes": len(rows), "judge_correct": hit,
        "judge_accuracy": round(hit / len(rows), 4),
        "agreement": f"{agree}/{len(rows)}",
        "kappa": round(kappa, 4),
        "api_calls": cost_calls, "elapsed_s": round(time.time() - t0, 1),
        "caveat": "两个评委同为 Kimi 家族（k3-agent / k2d8-preview），"
                  "仅验证真实链路；正式评测需跨家族（DeepSeek+Qwen）",
        "rows": rows,
    }
    with open(os.path.join(OUT_DIR, "real_judge_run.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n===== 真实 judge 评测汇总 =====")
    print(f"判定正确率: {hit}/{len(rows)} = {hit / len(rows):.0%}")
    print(f"双评委一致: {agree}/{len(voted)} (judge 实际投票的 probe)  Cohen's Kappa: {kappa:.3f}")
    print(f"API 调用: {cost_calls} 次  耗时: {summary['elapsed_s']}s")
    print(f"详细结果: {os.path.join(OUT_DIR, 'real_judge_run.json')}")
    return 0 if hit == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
