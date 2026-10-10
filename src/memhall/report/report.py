"""Markdown 报告：manifest 摘要 + 六维表 + 判定明细（可下钻证据）。"""

from __future__ import annotations

import json
from pathlib import Path

from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER, compute_metrics
from memhall.schema.evidence import Verdict
from memhall.schema.models_case import MemoryCase

VERDICT_ZH = {
    "correct": "✅ 正确", "omission": "遗漏", "confusion": "混淆",
    "fabrication": "记错(编造)", "over_persist": "错误持久化",
    "wrong_reuse": "错误复用", "invalid_run": "⚠️ 运行无效",
    "needs_review": "待人工复核",
}


def render_report(run_dir: Path, run_id: str, manifest: dict,
                  verdicts: list[Verdict], cases: dict[str, MemoryCase],
                  metrics: dict) -> str:
    lines: list[str] = []
    lines.append(f"# 麟阁 MemHall 评测报告 · {run_id}")
    lines.append("")
    lines.append(f"- 被测智能体：`{manifest.get('adapter', '?')}`（适配器模式）")
    lines.append(f"- 代码版本：`{manifest.get('git_hash', '?')}`　用例数："
                 f"{len(manifest.get('cases', []))}　探测点：{metrics['n_probes_total']}")
    lines.append(f"- 总体正确率：**{metrics['overall_score']:.1%}**"
                 f"（有效 {metrics['n_valid']}/{metrics['n_probes_total']}，"
                 f"规则判卷率 {metrics['rule_scoring_rate']:.0%}，"
                 f"人工复核 {metrics['n_needs_review']} 项 / {metrics['manual_review_rate']:.1%}，"
                 f"运行无效 {metrics['n_invalid_run']} 项）")
    lines.append("")
    lines.append("![六维雷达图](radar.png)")
    lines.append("")
    lines.append("## 六维能力")
    lines.append("")
    lines.append("| 能力 | 得分 | 正确/有效 | 错误分解 |")
    lines.append("|---|---|---|---|")
    for cap in CAP_ORDER:
        d = metrics["capability_detail"][cap.value]
        errs = "、".join(f"{VERDICT_ZH.get(k, k)}×{n}"
                         for k, n in d["error_breakdown"].items()) or "—"
        lines.append(f"| {CAP_LABELS_ZH[cap]} | {d['score']:.0%} | "
                     f"{d['n_correct']}/{d['n_valid']} | {errs} |")
    lines.append("")
    lines.append("## 判定明细")
    lines.append("")
    lines.append("| 探测点 | 能力 | 判定 | 判卷 | 置信 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for v in sorted(verdicts, key=lambda x: x.probe_id):
        cap = cases[v.case_id].capability.value if v.case_id in cases else "?"
        reason = v.explanation.replace("|", "\\|")
        if len(reason) > 60:
            reason = reason[:60] + "…"
        lines.append(f"| {v.probe_id} | {cap} | {VERDICT_ZH.get(v.verdict.value, v.verdict.value)} "
                     f"| {v.decided_by.value} | {v.confidence:.2f} | {reason} |")
    lines.append("")
    lines.append(f"> 证据下钻：`runs/{run_id}/cases/<case_id>/evidence.jsonl`"
                 f"（每条判定引用对应证据哈希）")
    lines.append("")
    return "\n".join(lines)
