"""重复运行稳定性与 pass^k 分析（择优移植自 leeyu44 PR #3）。

主线 manifest 无 case_capabilities 字段——从 run 的 cases/<id>/case.yaml
快照回填能力标签；输出 stability.json + stability.md（逐探测点翻转明细）。
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import yaml

from memhall.report.metrics import CAP_LABELS_ZH
from memhall.report.report import VERDICT_ZH
from memhall.schema.evidence import Verdict, VerdictValue


def _capabilities_from_cases(run_dir: Path) -> dict[str, str]:
    """主线格式兜底：cases/<id>/case.yaml 的 capability 字段 → {case_id: 能力}。"""
    out: dict[str, str] = {}
    cases_dir = run_dir / "cases"
    if not cases_dir.is_dir():
        return out
    for cdir in sorted(cases_dir.iterdir()):
        path = cdir / "case.yaml"
        try:
            case = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(case, dict) and case.get("case_id"):
                out[str(case["case_id"])] = str(case.get("capability", "unknown"))
        except (OSError, yaml.YAMLError):
            continue
    return out


def _load_run(run_dir: Path) -> tuple[dict, dict, dict[str, Verdict]]:
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads(
        (run_dir / "metrics.json").read_text(encoding="utf-8"))
    verdicts: dict[str, Verdict] = {}
    for line in (run_dir / "verdicts.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            verdict = Verdict.model_validate(json.loads(line))
            verdicts[verdict.probe_id] = verdict
    return manifest, metrics, verdicts


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def analyze_stability(run_dirs: list[Path],
                      out_dir: Path | None = None) -> dict[str, Any]:
    """Compare two or more compatible runs and optionally persist a report."""
    if len(run_dirs) < 2:
        raise ValueError("稳定性分析至少需要两次运行")
    loaded = [_load_run(path.resolve()) for path in run_dirs]
    manifests = [item[0] for item in loaded]
    metrics = [item[1] for item in loaded]
    verdict_sets = [item[2] for item in loaded]

    warnings: list[str] = []
    baseline = manifests[0]
    for manifest in manifests[1:]:
        for field in ("adapter", "cases_version", "code_version"):
            if manifest.get(field) != baseline.get(field):
                warnings.append(
                    f"{field} 不一致: {baseline.get(field)} != "
                    f"{manifest.get(field)}")
        if manifest.get("case_sample_seed") != baseline.get("case_sample_seed"):
            warnings.append("case_sample_seed 不一致")

    common = sorted(set.intersection(*(set(items) for items in verdict_sets)))
    all_ids = set.union(*(set(items) for items in verdict_sets))
    if len(common) != len(all_ids):
        warnings.append(
            f"探测点集合不一致，仅分析交集 {len(common)}/{len(all_ids)}")

    capabilities = (baseline.get("case_capabilities")
                    or _capabilities_from_cases(run_dirs[0].resolve()))
    rows: list[dict[str, Any]] = []
    by_capability: dict[str, list[dict[str, Any]]] = {}
    for probe_id in common:
        verdicts = [items[probe_id] for items in verdict_sets]
        values = [item.verdict.value for item in verdicts]
        case_id = verdicts[0].case_id
        capability = capabilities.get(case_id, "unknown")
        row = {
            "probe_id": probe_id,
            "case_id": case_id,
            "capability": capability,
            "verdicts": values,
            "stable": len(set(values)) == 1,
            "pass_all": all(value == VerdictValue.CORRECT.value
                            for value in values),
            "has_invalid": any(value in {
                VerdictValue.INVALID_RUN.value,
                VerdictValue.HUMAN_REVIEW.value,
            } for value in values),
        }
        rows.append(row)
        by_capability.setdefault(capability, []).append(row)

    scores = [float(item.get("overall_score", 0.0)) for item in metrics]
    invalid_rates = [
        1.0 - (float(item.get("n_valid", 0))
               / max(1, float(item.get("n_probes_total", 0))))
        for item in metrics
    ]
    stable_count = sum(row["stable"] for row in rows)
    pass_all_count = sum(row["pass_all"] for row in rows)
    capability_pass_all = {}
    for capability, cap_rows in sorted(by_capability.items()):
        capability_pass_all[capability] = {
            "label_zh": str(CAP_LABELS_ZH.get(capability, capability)),
            "n_probes": len(cap_rows),
            "n_stable": sum(row["stable"] for row in cap_rows),
            "agreement_rate": round(
                sum(row["stable"] for row in cap_rows) / len(cap_rows), 4),
            "n_pass_all": sum(row["pass_all"] for row in cap_rows),
            "pass_all_rate": round(
                sum(row["pass_all"] for row in cap_rows) / len(cap_rows), 4),
        }

    report: dict[str, Any] = {
        "schema_version": "0.1",
        "adapter": baseline.get("adapter", "?"),
        "run_ids": [item.get("run_id", "") for item in manifests],
        "n_repeats": len(run_dirs),
        "n_common_probes": len(common),
        "compatible": not warnings,
        "warnings": warnings,
        "overall_scores": scores,
        "overall_mean": round(_mean(scores), 4),
        "overall_stddev": round(statistics.pstdev(scores), 4),
        "overall_range": round(max(scores) - min(scores), 4),
        "invalid_rates": [round(value, 4) for value in invalid_rates],
        "verdict_agreement_rate": round(
            stable_count / len(rows), 4) if rows else 0.0,
        "pass_all_rate": round(
            pass_all_count / len(rows), 4) if rows else 0.0,
        "n_flips": len(rows) - stable_count,
        "capability_pass_all": capability_pass_all,
        "flips": [row for row in rows if not row["stable"]],
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "stability.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "stability.md").write_text(
            render_stability_report(report), encoding="utf-8")
    return report


def render_stability_report(report: dict[str, Any]) -> str:
    scores = report["overall_scores"]
    lines = [
        f"# 重复运行稳定性 · {report['adapter']} · "
        f"k={report['n_repeats']}",
        "",
        f"- 总体正确率：{' / '.join(f'{value:.1%}' for value in scores)}",
        f"- 均值 / 标准差 / 极差：{report['overall_mean']:.1%} / "
        f"{report['overall_stddev']:.1%} / {report['overall_range']:.1%}",
        f"- 判定一致率：**{report['verdict_agreement_rate']:.1%}**"
        f"（翻转 {report['n_flips']}/{report['n_common_probes']}）",
        f"- pass^{report['n_repeats']}：**{report['pass_all_rate']:.1%}**",
        "",
        "| 能力 | 判定一致率 | pass^k | 探测点 |",
        "|---|---:|---:|---:|",
    ]
    for _capability, detail in report["capability_pass_all"].items():
        lines.append(
            f"| {detail['label_zh']} | {detail['agreement_rate']:.1%} | "
            f"{detail['pass_all_rate']:.1%} | {detail['n_probes']} |")
    if report["warnings"]:
        lines.extend(["", "## 可比性提示", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    lines.extend(["", f"## 判定翻转（{report['n_flips']}）", ""])
    if not report["flips"]:
        lines.append("所有共同探测点的判定完全一致。")
    else:
        lines.extend([
            "| 探测点 | 各轮判定 |",
            "|---|---|",
        ])
        for row in report["flips"]:
            values = " → ".join(
                VERDICT_ZH.get(value, value) for value in row["verdicts"])
            lines.append(f"| {row['probe_id']} | {values} |")
    lines.append("")
    return "\n".join(lines)
