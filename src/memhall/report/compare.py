"""两次运行对比：对比雷达 + 六维差值 + 判定翻转明细（design.md §10 结果追踪）。

同一智能体两次运行 → 看稳定性（哪些判定翻转）；
两个智能体 → 看能力轮廓差（分数差在哪一维）。

R11/R16（2026-10-04）：判定翻转配二项符号检验（n=2 轮下"谁更好"要过
显著性这道门）；判定一致率 = 1 - 翻转率（design §8 评测质量指标落地）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from memhall.report.cluster import countable, score_index
from memhall.report.compatibility import check_compatibility
from memhall.report.metrics import CAP_LABELS_ZH
from memhall.report.radar import render_radar
from memhall.report.report import VERDICT_ZH
from memhall.schema.evidence import Verdict

_CAP_ORDER = ["persist", "recall", "dynamic_update",
              "discriminate", "boundary", "reuse"]


def _sign_test_p(n: int, k: int) -> float:
    """二项符号检验双侧 p 值（H0: 翻转方向等概率）。"""
    if n <= 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _load(run_dir: Path):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    verdicts: dict[str, Verdict] = {}
    for line in (run_dir / "verdicts.jsonl").read_text(encoding="utf-8").splitlines():
        v = Verdict.model_validate(json.loads(line))
        if v.probe_id in verdicts:
            raise ValueError(f"重复判定: {v.probe_id}")
        verdicts[v.probe_id] = v
    return manifest, metrics, verdicts


def _label(manifest: dict) -> str:
    return f"{manifest.get('adapter', '?')}·{manifest.get('run_id', '')[9:15] or '?'}"


def compare_runs(dir_a: Path, dir_b: Path, out_dir: Path) -> dict:
    compatibility = check_compatibility([dir_a, dir_b])
    ma, mta, va = _load(dir_a)
    mb, mtb, vb = _load(dir_b)
    out_dir.mkdir(parents=True, exist_ok=True)

    la, lb = _label(ma), _label(mb)
    render_radar({la: mta["capability_scores"], lb: mtb["capability_scores"]},
                 str(out_dir / "compare-radar.png"))

    common = sorted(set(va) & set(vb))
    # R28：翻转分桶——只有"一家对、另一家错"才算方向票进符号检验；
    # 错误形态变化（omission→fabrication，双方皆错）与运行有效性差异
    # （invalid/human_review 参与）不进检验，单列展示，不再污染 n 稀释 p。
    def _valid(v: Verdict) -> bool:
        return countable(v)

    dir_flips: list[tuple[str, Verdict, Verdict]] = []
    form_changes: list[tuple[str, Verdict, Verdict]] = []
    validity_diffs: list[tuple[str, Verdict, Verdict]] = []
    for pid in common:
        x, y = va[pid], vb[pid]
        if x.verdict == y.verdict:
            continue
        if not (_valid(x) and _valid(y)):
            validity_diffs.append((pid, x, y))
        elif x.verdict.value == "correct":
            dir_flips.append((pid, x, y))   # a 对 → b 错：偏向 b
        elif y.verdict.value == "correct":
            dir_flips.append((pid, x, y))   # b 对 → a 错：偏向 a
        else:
            form_changes.append((pid, x, y))
    # 方向票计数：x=a 侧、y=b 侧；y 对 x 错 = 偏向 b，x 对 y 错 = 偏向 a
    to_b = sum(1 for _, _x, y in dir_flips if y.verdict.value == "correct")
    to_a = len(dir_flips) - to_b
    index = score_index(dir_a)
    by_case: dict[str, list[float]] = {}
    case_family: dict[str, str] = {}
    for pid in common:
        x, y = va[pid], vb[pid]
        if not (_valid(x) and _valid(y)) or pid not in index:
            continue
        _, cid, family = index[pid]
        if x.case_id != cid or y.case_id != cid:
            raise ValueError(f"判定归属与题目快照不一致: {pid}")
        by_case.setdefault(cid, []).append(float(y.verdict.value == "correct")
                                          - float(x.verdict.value == "correct"))
        case_family[cid] = family
    by_family: dict[str, list[float]] = {}
    for cid, differences in by_case.items():
        by_family.setdefault(case_family[cid], []).append(sum(differences) / len(differences))
    net = [sum(ds) / len(ds) for ds in by_family.values()]
    cluster_a = sum(d < -1e-12 for d in net)
    cluster_b = sum(d > 1e-12 for d in net)
    p = _sign_test_p(cluster_a + cluster_b, cluster_b)
    cluster_test = {"unit": "family_or_case", "n_pairs": len(net),
                    "n_non_ties": cluster_a + cluster_b, "to_a": cluster_a,
                    "to_b": cluster_b, "p_two_sided": round(p, 4),
                    "n_ties": len(net) - cluster_a - cluster_b}
    if not index:
        cluster_test["p_two_sided"] = None
        compatibility["warnings"].append("无题目快照，无法进行按题族／用例配对检验")
    flips = validity_diffs + form_changes + dir_flips  # 明细展示仍全量
    agreement = round(1 - len(flips) / len(common), 4) if common else None

    lines = [f"# 运行对比 · {la} vs {lb}", ""]
    da = mta["overall_score"]
    db = mtb["overall_score"]
    scores_text = (f"{da:.1%} → {db:.1%}（{db - da:+.1%}）"
                   if da is not None and db is not None else "有效计分不足，不能计算差值")
    base = f"- 总体正确率：{scores_text}　共同探测点 {len(common)}　判定翻转 {len(flips)}"
    if agreement is not None:
        base += f"（判定一致率 {agreement:.1%}）"
    lines.append(base)
    if cluster_test["p_two_sided"] is not None:
        verdict_line = ("方向差异不显著" if p >= 0.05
                        else f"方向显著偏向 {lb if cluster_b > cluster_a else la}")
        lines.append(f"- 按题族／用例配对符号检验：共同 {len(net)} 组，非平局 "
                     f"{cluster_a + cluster_b} 组，p={p:.3f}，{verdict_line}")
        lines.append("- 同一题多个探测点先合并；同族变体再合并。该检验仅用于这一对运行，未校正多重比较。")
    for warning in compatibility["warnings"]:
        lines.append(f"- 比较限制：{warning}")
    if form_changes:
        lines.append(f"- 错误形态变化（双方皆错、形态不同，不进检验）：{len(form_changes)}")
    if validity_diffs:
        lines.append(f"- 运行有效性差异（invalid/human_review 参与，不进检验）："
                     f"{len(validity_diffs)}")
    lines.append("")
    lines.append("| 能力 | " + la + " | " + lb + " | Δ |")
    lines.append("|---|---|---|---|")
    for cap in _CAP_ORDER:
        s1 = mta["capability_scores"].get(cap)
        s2 = mtb["capability_scores"].get(cap)
        if s1 is None and s2 is None:
            continue
        t1 = f"{s1:.0%}" if s1 is not None else "未测"
        t2 = f"{s2:.0%}" if s2 is not None else "未测"
        delta = f"{s2 - s1:+.0%}" if s1 is not None and s2 is not None else "—"
        lines.append(f"| {CAP_LABELS_ZH.get(cap, cap)} | {t1} | {t2} | {delta} |")
    lines.append("")
    lines.append(f"## 判定翻转（{len(flips)}/{len(common)}）")
    lines.append("")
    if flips:
        lines.append("| 探测点 | " + la + " | " + lb + " |")
        lines.append("|---|---|---|")
        for pid, x, y in flips:
            lines.append(f"| {pid} | {VERDICT_ZH.get(x.verdict.value, x.verdict.value)} "
                         f"| {VERDICT_ZH.get(y.verdict.value, y.verdict.value)} |")
    else:
        lines.append("两次运行判定完全一致（稳定性满分）。")
    lines.append("")
    # 统一模型对账：两次运行的 model_backend 不一致时，能力差不能全归因于智能体
    pa, pb = ma.get("model_backend"), mb.get("model_backend")
    parity = None
    if pa and pb:
        same = (pa.get("mode") == pb.get("mode")
                and pa.get("model") == pb.get("model"))
        parity = {"same": same, "a": pa, "b": pb}
        if not same:
            lines.insert(2, f"> ⚠️ 模型口径不一致：{la} 用 `{pa.get('model')}`"
                            f"（{pa.get('mode')}），{lb} 用 `{pb.get('model')}`"
                            f"（{pb.get('mode')}）——能力差含模型因素，"
                            "用统一模型网关（memhall gateway）后重跑。")
            lines.insert(3, "")
    (out_dir / "compare.md").write_text("\n".join(lines), encoding="utf-8")

    return {"label_a": la, "label_b": lb, "n_common": len(common),
            "n_flips": len(flips),
            "n_directional_flips": len(dir_flips),
            "n_form_changes": len(form_changes),
            "n_validity_diffs": len(validity_diffs),
            "overall_a": da, "overall_b": db,
            "verdict_agreement_rate": agreement,
            "compatibility": compatibility,
            "clustered_sign_test": cluster_test,
            "flip_sign_test": {"n_flips": len(dir_flips), "to_a": to_a,
                               "to_b": to_b, "p_two_sided": cluster_test["p_two_sided"],
                                "p_unit": "family_or_case"},
            "model_parity": parity,
            "radar": str(out_dir / "compare-radar.png"),
            "report": str(out_dir / "compare.md")}
