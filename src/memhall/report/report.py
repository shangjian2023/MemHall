"""Markdown 报告：manifest 摘要 + 六维表 + 判定明细（可下钻证据）。"""

from __future__ import annotations

from pathlib import Path

from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER
from memhall.schema.evidence import Verdict
from memhall.schema.models_case import MemoryCase

VERDICT_ZH = {
    "correct": "✅ 正确", "omission": "遗漏", "confusion": "混淆",
    "fabrication": "记错(编造)", "over_persist": "错误持久化",
    "wrong_reuse": "错误复用", "invalid_run": "⚠️ 运行无效",
    "human_review": "⏳ 判卷未决(转人工)",
}


def render_report(run_dir: Path, run_id: str, manifest: dict,
                  verdicts: list[Verdict], cases: dict[str, MemoryCase],
                  metrics: dict) -> str:
    lines: list[str] = []
    lines.append(f"# 麟阁 MemHall 评测报告 · {run_id}")
    lines.append("")
    lines.append(f"- 被测智能体：`{manifest.get('adapter', '?')}`（适配器模式）"
                 + (f"　版本：`{manifest['agent_version']}`"
                    if manifest.get("agent_version") else ""))
    mb = manifest.get("model_backend") or {}
    if mb.get("mode") == "gateway":
        lines.append(f"- 模型口径：统一网关 `{mb.get('model', '?')}`"
                     f"（所有被测流量经 memhall gateway 强制改写）")
    elif mb.get("mode") == "direct" and mb.get("lanes"):
        lanes = "、".join(f"{v.get('model', '?')}@{k}"
                         for k, v in mb["lanes"].items())
        lines.append(f"- 模型口径：直连（{lanes}）")
    tu = manifest.get("token_usage")
    if tu:
        lines.append(f"- Token 消耗（网关记账）：{tu.get('total_tokens', 0):,}"
                     f" tokens / {tu.get('requests', 0)} 次请求"
                     f"（错误 {tu.get('errors', 0)}）")
    lines.append(f"- 代码版本：`{manifest.get('git_hash', '?')}`　用例数："
                 f"{len(manifest.get('cases', []))}　探测点：{metrics['n_probes_total']}"
                 f"（计分 {metrics.get('n_score_probes', '?')}"
                 f" / 诊断 {metrics.get('n_diagnostic_probes', '?')}）")
    overall = metrics["overall_score"]
    lines.append(f"- 总体正确率：**{overall:.1%}**"
                 if overall is not None else "- 总体正确率：**未测**（无有效计分探测点）")
    if overall is not None:
        extra = [f"有效计分 {metrics['n_valid']}/{metrics.get('n_score_probes', '?')}"]
        floor = metrics.get("overall_score_floor")
        if floor is not None:
            extra.append(f"未决按错计下界 {floor:.1%}")
        cw = metrics.get("overall_score_case_weighted")
        if cw is not None:
            extra.append(f"按用例等权 {cw:.1%}")
        lines.append(f"  （{'，'.join(extra)}，规则判卷率 {metrics['rule_scoring_rate']:.0%}）")
    if metrics.get("n_human_review"):
        lines.append(f"- ⚠️ 判卷未决 {metrics['n_human_review']} 个已剔出分母"
                     "——脚本判卷天花板，正式口径建议 `--judge dual` 收尾")
    wh = metrics.get("write_hygiene")
    if wh is not None:
        lines.append(f"- 写入卫生（不该记的记了）：{wh:.1%}")
    si = metrics.get("stale_info_rate")
    if si is not None:
        lines.append(f"- 过期信息调用率（更新维拿旧值答新题）：{si:.1%}")
    jm = manifest.get("judge", {})
    if jm:
        model = f"（{jm.get('model_a', '')}）" if jm.get("model_a") else ""
        lines.append(f"- 判卷口径：{jm.get('mode', '?')}{model}"
                     f" · 提示词版本 {jm.get('prompt_version', '?')}")
    lines.append("")
    lines.append("![六维雷达图](radar.png)")
    lines.append("")
    lines.append("## 六维能力")
    lines.append("")
    lines.append("| 能力 | 得分 | 正确/有效 | 错误分解 |")
    lines.append("|---|---|---|---|")
    for cap in CAP_ORDER:
        d = metrics["capability_detail"][cap]
        errs = "、".join(f"{VERDICT_ZH.get(k, k)}×{n}"
                         for k, n in d["error_breakdown"].items()) or "—"
        score = d["score"]
        # R51：有效探测 <5 的维标注不具区分力——二值探测点 CI 半宽 ±30% 起步，
        # 维度间比较与对外叙事都要让位给这个事实
        thin = (" ⚠样本<5，不具区分力" if 0 < d["n_valid"] < 5 else "")
        score_s = (f"{score:.0%}{thin}" if score is not None else "—（未测）")
        lines.append(f"| {CAP_LABELS_ZH[cap]} | {score_s} | "
                     f"{d['n_correct']}/{d['n_valid']} | {errs} |")
    lines.append("")

    # 故障定位四态（design §6.3）：不只看对错，还定位坏在哪一环
    fl = metrics.get("fault_localization") or {}
    flz = fl.get("summary_zh") or {}
    if flz:
        lines.append("## 故障定位（行为 × 存储交叉）")
        lines.append("")
        lines.append("| 定位 | 用例数 |")
        lines.append("|---|---|")
        for label, n in flz.items():
            lines.append(f"| {label} | {n} |")
        n_nostorage = fl.get("n_cases_without_storage_evidence", 0)
        if n_nostorage:
            lines.append(f"| 无存储证据（无法定位） | {n_nostorage} |")
        lines.append("")
    lines.append("## 判定明细")
    lines.append("")
    lines.append("| 探测点 | 能力 | 判定 | 判卷 | 置信 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for v in sorted(verdicts, key=lambda x: x.probe_id):
        cap_id = cases[v.case_id].capability.value if v.case_id in cases else "?"
        reason = v.explanation.replace("|", "\\|")
        if len(reason) > 60:
            reason = reason[:60] + "…"
        lines.append(f"| {v.probe_id} | {cap_id} | {VERDICT_ZH.get(v.verdict.value, v.verdict.value)} "
                     f"| {v.decided_by.value} | {v.confidence:.2f} | {reason} |")
    lines.append("")
    lines.append(f"> 证据下钻：`runs/{run_id}/cases/<case_id>/evidence.jsonl`"
                 f"（每条判定引用对应证据哈希）")
    lines.append("")
    if manifest.get("adapter") == "mock":
        from memhall.adapters.mock import DESIGNED_PROFILE
        lines.append("---")
        lines.append("**mock 为缺陷注入基线**：分数是下列设计模式的确定输出，"
                     "用作管线回归与判卷自检，不是难度地板。")
        for pkey, mode in DESIGNED_PROFILE.items():
            lines.append(f"- {pkey}: {mode}")
        lines.append("")
    return "\n".join(lines)
