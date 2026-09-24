"""真实闭环评分驱动：OKIM-REAL-001「教→隔→考」@ KylinBot 0.7.5 / openKylin 3.0。

证据来源（2026-09-23 实测）：
- 教学：kylin-bot agent 存入 daily_report_template_path / user_mailing_address
- 间隔：3 个无关 filler 会话
- 考试：agent 两次回答均声称"没有记录"（提取失败原文见 ANSWER_*）
- 更新：agent 新建 user_shipping_address（西湖区），旧 user_mailing_address（滨江区）仍在
- 边界：临时端口 8080 以 daily 类别 + 时效标注入库
- 记忆库快照：out/brain_final.db（虚拟机 brain.db checkpoint 后导出）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from adapters.kylinbot import load_snapshot
from scoring.models import Capability, EvidenceBundle, ProbeSpec
from scoring.tree import score_probe
from scoring.metrics import aggregate
from scoring.radar import render_radar

SNAPSHOT_DB = Path(__file__).parent / "out" / "brain_final.db"
OUT_DIR = Path(__file__).parent / "out" / "realrun"

# ---- 真实 agent 回答原文（2026-09-23 虚拟机实测逐字捕获）----
ANSWER_RECALL = (
    "我没有找到你的日报模板，也没有找到你的收件地址——工作区里没有 AGENTS.md、USER.md、"
    "MEMORY.md 这些文件，长期记忆里也没有相关记录。\n\n方便的话告诉我一下：\n\n"
    "- 日报模板是存在某个文件里，还是需要我帮你新建一个？\n"
    "- 收件地址是指快递收货地址，还是日报的收件邮箱？\n\n"
    "你给出内容后，我可以帮你存到长期记忆里，以后直接问就行。"
)
ANSWER_RECALL_AFTER_UPDATE = (
    "我这边没有你的收件地址信息——工作区里没有相关记录（USER.md、MEMORY.md 都不存在），"
    "也没有历史对话提到过。\n\n你可以直接告诉我，我帮你记下来；或者如果地址存在某个文件里，"
    "告诉我路径我去读。"
)

PROBES = [
    ProbeSpec(
        probe_id="P1-RETAIN",
        kind="memory_store_check",
        capability=Capability.RETENTION.value,
        expect_store_retain=["daily.docx", "日报模板"],
    ),
    ProbeSpec(
        probe_id="P2-RECALL",
        kind="qa",
        capability=Capability.RECALL.value,
        question="我的日报模板在哪里？我的收件地址是什么？",
        ground_truth="日报模板在 /home/lcy/templates/daily.docx，收件地址是杭州市滨江区",
        expect_store_retain=["daily.docx"],
    ),
    ProbeSpec(
        probe_id="P3-UPDATE",
        kind="memory_store_check",
        capability=Capability.UPDATE.value,
        expect_store_retain=["西湖区"],
        expect_store_absent=["滨江区"],  # 旧地址应被取代/清除
    ),
    ProbeSpec(
        probe_id="P4-RECALL-UPD",
        kind="qa",
        capability=Capability.UPDATE.value,
        question="我现在的收件地址是什么？",
        ground_truth="杭州市西湖区",
        expect_store_retain=["西湖区"],
    ),
    ProbeSpec(
        probe_id="P5-BOUNDARY",
        kind="memory_store_check",
        capability=Capability.BOUNDARY.value,
        expect_store_absent=["8080"],  # 临时信息不应进入长期记忆
    ),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = load_snapshot(SNAPSHOT_DB, include_superseded=True)
    snapshot.captured_at = "2026-09-23T15:47:40+08:00"
    print(f"snapshot: {len(snapshot.entries)} entries from {snapshot.store_hash}")

    answers = {
        "P2-RECALL": ANSWER_RECALL,
        "P4-RECALL-UPD": ANSWER_RECALL_AFTER_UPDATE,
    }

    results = []
    for spec in PROBES:
        ev = EvidenceBundle(
            case_id="OKIM-REAL-001",
            probe_id=spec.probe_id,
            memory_after=snapshot,
            answer=answers.get(spec.probe_id, ""),
            transcript_refs=[f"vm:kylin-bot agent -m '{spec.question}'" if spec.question else "vm:kylin-bot memory list"],
        )
        # 本驱动不挂 judge：真实回答均被 L1 规则判定，顺便验证"规则优先、零 AI 成本"
        r = score_probe(spec, ev)
        d = r.to_dict()
        d["capability"] = spec.capability
        results.append(d)
        print(f"[{d['scored_by']:>5}] {spec.probe_id:<15} -> {d['error_type']:<18} "
              f"conf={d['confidence']:.2f}  {d['reason'][:60]}")

    (OUT_DIR / "results.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in results) + "\n",
        encoding="utf-8")

    metrics = aggregate(results)
    (OUT_DIR / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    agent_scores = {"KylinBot-0.7.5": {k: v * 100 for k, v in metrics["capability_scores"].items()}}
    render_radar(agent_scores, str(OUT_DIR / "radar.png"),
                 title="OKIM-Bench 真实闭环 · KylinBot 0.7.5 @ openKylin 3.0")

    print("\n== metrics ==")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
