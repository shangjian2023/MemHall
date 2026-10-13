"""Aggregate repeated runs with case/family and whole-round bootstrap intervals."""

from __future__ import annotations

import json
import math
from pathlib import Path

from memhall.report.cluster import bootstrap_ci, family_balanced, load_clusters
from memhall.report.compatibility import check_compatibility
from memhall.report.metrics import CAP_LABELS_ZH

_CAP_ORDER = ["persist", "recall", "dynamic_update", "discriminate", "boundary", "reuse"]

_BOOTSTRAP_B = 2000
_BOOTSTRAP_SEED = 20261004


def _mean_std(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    mean = sum(xs) / n
    if n < 2:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)  # 样本方差 ddof=1
    return mean, math.sqrt(var)


def _pass_k(pool: dict, key: str, rounds: int) -> dict | None:
    """A6 pass^k（tau-bench 系惯例）：探测点在全部 k 轮都判对的比例。

    分母只算 k 轮全部有有效判定的探测点（中途 invalid/human_review 的
    不进——pass^k 的语义是"k 次都过"，缺一轮无从谈起）。"""
    if rounds < 2:
        return None
    probes = [vs for fam in pool.get(key, {}).values() for vs in fam.values()]
    full = [vs for vs in probes if all(b is not None for b in vs)]
    if not full:
        return None
    rate = sum(all(b == 1.0 for b in vs) for vs in full) / len(full)
    return {"k": rounds, "rate": round(rate, 4), "n_probes": len(full)}


def aggregate_runs(run_dirs: list[Path], out_dir: Path) -> dict:
    """聚合 N 轮运行：要求同智能体同题库，否则视为口径混杂直接报错。"""
    compatibility = check_compatibility(run_dirs, aggregate=True)
    runs = []
    for d in run_dirs:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        runs.append((manifest, metrics))

    caps = {}
    dropped: dict[str, dict] = {}
    probe_pooled = load_clusters(run_dirs)
    for cap in _CAP_ORDER:
        scores = [mt["capability_scores"].get(cap) for _, mt in runs]
        if any(s is None for s in scores):
            # R56：缺维不再静默消失——哪轮未测要可见（未测≠0 分，也不该蒸发）
            dropped[cap] = {"label_zh": CAP_LABELS_ZH[cap], "n_missing_runs": sum(s is None for s in scores)}
            continue
        mean, std = _mean_std(scores)
        entry = {
            "label_zh": CAP_LABELS_ZH[cap],
            "n": len(scores),
            "mean": round(mean, 4),
            "std": round(std, 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
        }
        entry.update(bootstrap_ci(probe_pooled, cap, rounds=len(runs)))
        entry["pass_k"] = _pass_k(probe_pooled, cap, len(runs))
        caps[cap] = entry
    overalls = [mt["overall_score"] for _, mt in runs]
    if any(o is None for o in overalls):
        raise ValueError("存在无有效计分探测点的轮次（overall=None），拒绝聚合")
    overall_m, overall_s = _mean_std(overalls)

    overall_ci = bootstrap_ci(probe_pooled, "__overall__", rounds=len(runs))
    result = {
        "compatibility": compatibility,
        "family_balanced_score": family_balanced(probe_pooled),
        "adapter": runs[0][0].get("adapter"),
        "cases": runs[0][0].get("cases"),
        "n_runs": len(runs),
        "overall": {"mean": round(overall_m, 4), "std": round(overall_s, 4), **overall_ci},
        "pass_k": _pass_k(probe_pooled, "__overall__", len(runs)),
        "capabilities": caps,
        "dropped_capabilities": dropped,  # R56：缺维标注（含哪几轮未测）
        "runs": [
            {
                "run_id": m.get("run_id"),
                "overall_score": round(mt["overall_score"], 4)
                if mt.get("overall_score") is not None
                else None,
                # 每轮分母（R12）：剔除口径不同的轮次不能裸平均
                "n_valid": mt.get("n_valid"),
                "n_invalid_run": mt.get("n_invalid_run"),
                "n_human_review": mt.get("n_human_review"),
                # A6：延迟/成本一等指标进聚合表（缺数据轮如实缺席）
                "p95_latency_ms": (mt.get("latency") or {}).get("p95_ms"),
                "total_tokens": (m.get("token_usage") or {}).get("total_tokens"),
            }
            for m, mt in runs
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "aggregate.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def format_table(result: dict) -> str:
    lines = [
        f"{result['adapter']} × {result['cases']}　{result['n_runs']} 轮",
        f"总分：{result['overall']['mean']:.1%} ± {result['overall']['std']:.1%}",
    ]
    ci = result["overall"].get("ci95")
    if ci:
        n_probe = result["overall"].get("n_clusters", "?")
        lines[-1] += f"（95% CI {ci[0]:.0%}~{ci[1]:.0%}，bootstrap {n_probe} 题族／用例）"
    pk = result.get("pass_k")
    if pk:
        lines.append(
            f"pass^{pk['k']}（探测点 {pk['k']} 轮全对）："
            f"{pk['rate']:.1%}（n={pk['n_probes']}）——比均值严的"
            "稳定性口径，翻转一次即出榜"
        )
    # A6：延迟/成本列（哪轮缺数据就显示 —，不冒充）
    p95s = [r.get("p95_latency_ms") for r in result["runs"]]
    if any(p is not None for p in p95s):
        lines.append(
            "回复延迟 p95（各轮）：" + " / ".join(f"{p / 1000:.0f}s" if p is not None else "—" for p in p95s)
        )
    toks = [r.get("total_tokens") for r in result["runs"]]
    if any(t for t in toks):
        total = sum(t for t in toks if t)
        lines.append(
            "Token 消耗（各轮，网关记账）："
            + " / ".join(f"{t:,}" if t else "—" for t in toks)
            + f"　合计 {total:,}"
        )
    for warning in result.get("compatibility", {}).get("warnings", []):
        lines.append(f"比较限制：{warning}")
    if result.get("dropped_capabilities"):
        miss = "、".join(
            f"{d['label_zh']}({d['n_missing_runs']} 轮未测)" for d in result["dropped_capabilities"].values()
        )
        lines.append(f"未入表维度：{miss}（未测≠0 分）")
    lines.append("维度｜均值±标准差（min~max）")
    for c in result["capabilities"].values():
        row = (
            f"  {c['label_zh']}：{c['mean']:.1%} ± {c['std']:.1%}"
            f"（{c['min']:.0%}~{c['max']:.0%}，n={c['n']}）"
        )
        if c.get("ci95"):
            row += f" 95% CI {c['ci95'][0]:.0%}~{c['ci95'][1]:.0%}"
        cpk = c.get("pass_k")
        if cpk:
            row += f" · pass^{cpk['k']} {cpk['rate']:.0%}"
        lines.append(row)
    return "\n".join(lines)
