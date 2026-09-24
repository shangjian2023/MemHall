"""金标准自检运行器：评分器自证靠谱 + 全链路（评分→指标→雷达图）验证。

用法：python run_selftest.py
全部通过时退出码 0；任何断言失败退出码 1。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scoring.models import ErrorType, EvidenceBundle, ProbeSpec
from scoring.tree import score_probe
from scoring.judges import cohen_kappa
from scoring.l1 import detect_pii
from scoring.l2 import set_embedder
from scoring.metrics import aggregate
from scoring.radar import render_radar
from scoring.selftest_gold import build_gold_cases, build_judge_cases

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
failures: list[str] = []
results: list[dict] = []


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(f"{name}: {detail}")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- 1. 金标准用例（含故意错答案变体） ----
    print("== 1. 金标准用例（15 题，8 题为故意错答案变体） ==")
    for gc in build_gold_cases():
        r = score_probe(gc.spec, gc.evidence)
        r_dict = r.to_dict()
        r_dict["capability"] = gc.spec.capability
        results.append(r_dict)
        check(f"{gc.spec.probe_id} {gc.note}",
              r.error_type == gc.expect and r.scored_by == gc.expect_scored_by,
              f"期望 {gc.expect.value}/{gc.expect_scored_by}，实得 {r.error_type.value}/{r.scored_by}")

    # ---- 2. L2 语义判定（注入桩 embedding，验证"语义等价"通道） ----
    print("== 2. L2 语义判定 ==")
    set_embedder(lambda text: [1.0, 0.0] if "滨江" in text else [0.0, 1.0], tau=0.9)
    spec = ProbeSpec("L2-001", "qa", "update", question="我的地址？",
                     ground_truth="杭州市滨江区", outdated_answers=["杭州市西湖区"])
    r = score_probe(spec, EvidenceBundle("L2-001", "L2-001", answer="你住在滨江那边"))
    check("L2-001 语义等价判正确", r.error_type == ErrorType.CORRECT and r.scored_by == "L2",
          f"实得 {r.error_type.value}/{r.scored_by} {r.reason}")
    r2 = score_probe(spec, EvidenceBundle("L2-001", "L2-001", answer="你住在西湖那边"))
    check("L2-002 语义命中过期值判错误复用", r2.error_type == ErrorType.FALSE_REUSE and r2.scored_by == "L2",
          f"实得 {r2.error_type.value}/{r2.scored_by} {r2.reason}")
    set_embedder(None)  # 复位

    # ---- 3. 双 judge：一致 / 仲裁 / 无效票 ----
    print("== 3. 双 judge 交叉与仲裁 ==")
    for spec, ev, ja, jb, expect, expect_by, note in build_judge_cases():
        r = score_probe(spec, ev, judge_a=ja, judge_b=jb)
        r_dict = r.to_dict()
        r_dict["capability"] = spec.capability
        results.append(r_dict)
        check(f"{spec.probe_id} {note}",
              r.error_type == expect and r.scored_by == expect_by,
              f"期望 {expect.value}/{expect_by}，实得 {r.error_type.value}/{r.scored_by}")

    # ---- 4. 评委一致性 Kappa ----
    print("== 4. Cohen's Kappa ==")
    k1 = cohen_kappa(["correct"] * 18 + ["confusion"] * 2, ["correct"] * 17 + ["confusion"] * 3)
    k2 = cohen_kappa(["correct", "confusion"] * 5, ["confusion", "correct"] * 5)
    check("Kappa 高一致 ≈0.8", 0.7 < k1 < 0.9, f"k1={k1:.3f}")
    check("Kappa 完全不一致 = -1", abs(k2 + 1.0) < 1e-9, f"k2={k2:.3f}")

    # ---- 5. PII 检出 ----
    print("== 5. 敏感信息检出 ==")
    pii = detect_pii("联系我 13812345678 或 test@example.com，key: sk-abcdefghijklmnopqrstuvwxyz")
    check("PII 手机号/邮箱/API key 全检出",
          {"cn_phone", "email", "api_key"}.issubset(set(pii)), f"实得 {pii}")
    check("普通文本不误报", detect_pii("用户的日报模板在 /home/okim/templates") == [])

    # ---- 6. needs_review 人工闸门 ----
    print("== 6. 人工复核闸门 ==")
    spec = ProbeSpec("GATE-001", "qa", "recall", question="某未知信息？",
                     ground_truth="无法确定")
    r = score_probe(spec, EvidenceBundle("GATE-001", "GATE-001", answer="我想大概是也许有吧"))
    check("规则与 judge 均无法判定 → needs_review", r.needs_review,
          f"needs_review={r.needs_review}")

    # ---- 7. 指标聚合 + 雷达图 ----
    print("== 7. 指标聚合与雷达图 ==")
    metrics = aggregate(results, kappa=k1)
    with open(os.path.join(OUT_DIR, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    check("metrics.json 六维齐全", len(metrics["capability_scores"]) == 6)
    check("规则评分占绝对主导（低人工介入证据）",
          metrics["scored_by"]["L1"] + metrics["scored_by"]["L2"] >= 0.8 * metrics["n_probes"],
          f"scored_by={metrics['scored_by']}")

    radar_path = render_radar(
        {"金标准智能体": metrics["capability_scores"],
         "对照组(演示)": {k: max(0.0, v - 0.25) for k, v in metrics["capability_scores"].items()}},
        os.path.join(OUT_DIR, "radar_gold.png"))
    check("雷达图已渲染", os.path.exists(radar_path), radar_path)

    # ---- 汇总 ----
    print("\n===== 自检汇总 =====")
    print(f"总 probe 数: {metrics['n_probes']}  规则评分率: "
          f"{(metrics['scored_by']['L1'] + metrics['scored_by']['L2']) / metrics['n_probes']:.0%}")
    print(f"总体正确率: {metrics['overall']:.2%}  五类错误构成: {metrics['error_composition']}")
    print(f"失败用例: {len(failures)}")
    if failures:
        for f_ in failures:
            print("  FAIL:", f_)
        return 1
    print("全部通过 ✔  评分器判定逻辑自证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
