"""多次重跑的方差聚合：六维与总分 mean±std + 逐探测点 bootstrap CI（design.md 方差机制化）。

同一智能体同一题库跑 N 轮 → 报均值±样本标准差，替代单轮裸分数；
单轮分数的随机波动（如概率性丢写）由此从"素材"变成"报告字段"。
主流评测（LongMemEval/LoCoMo 系）均以多轮统计为口径。

R11（2026-10-04）：n=2 的 mean±std 支撑不了排名叙事——补逐探测点
bootstrap 95% CI（探测点为重采样单元、轮内取均值，cluster bootstrap；
固定 seed 可复现）。n=2 时这是比 std 诚实得多的不确定度陈述。
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from memhall.report.metrics import CAP_LABELS_ZH
from memhall.schema.evidence import Verdict, VerdictValue
from memhall.schema.models_case import MemoryCase, probe_role

_CAP_ORDER = ["persist", "recall", "dynamic_update",
              "discriminate", "boundary", "reuse"]

_BOOTSTRAP_B = 2000
_BOOTSTRAP_SEED = 20261004


def _mean_std(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    mean = sum(xs) / n
    if n < 2:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)  # 样本方差 ddof=1
    return mean, math.sqrt(var)


def _load_probe_pool(run_dirs: list[Path]) -> dict[str, dict[str, list[float]]]:
    """逐探测点收集跨轮 0/1 结果（cluster bootstrap 的原始数据）。

    key = 能力维（+ __overall__）；value = probe_id -> 每轮结果列表
    （correct=1，其余有效判定=0；invalid/human_review 剔除）。
    缺 verdicts.jsonl 或 case 快照的旧 run：返回空（CI 为 None，不硬凑）。
    """
    import yaml

    pool: dict[str, dict[str, list[float]]] = {"__overall__": {}}
    for d in run_dirs:
        vfile = d / "verdicts.jsonl"
        if not vfile.exists():
            return {}
        # capability/role 从 run 内 case 快照取（run 自包含）
        case_caps: dict[str, tuple[str, set[str], set[str]]] = {}
        for cf in (d / "cases").glob("*/case.yaml"):
            try:
                case = MemoryCase.model_validate(
                    yaml.safe_load(cf.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 旧快照解析失败按 unknown 处理
                continue
            case_caps[case.case_id] = (
                case.capability.value,
                {p.id for p in case.probes if probe_role(case, p) == "score"},
                {p.id for p in case.probes})
        for line in vfile.read_text(encoding="utf-8").splitlines():
            try:
                v = Verdict.model_validate(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
            if v.verdict in (VerdictValue.INVALID_RUN, VerdictValue.HUMAN_REVIEW):
                continue
            entry = case_caps.get(v.case_id)
            if entry is not None and v.probe_id in entry[2] \
                    and v.probe_id not in entry[1]:
                continue  # 快照里标 diagnostic 的探测才剔除；对不上的旧 id 保守计入
            bit = 1.0 if v.verdict == VerdictValue.CORRECT else 0.0
            pool["__overall__"].setdefault(v.probe_id, []).append(bit)
            if entry is not None:
                pool.setdefault(entry[0], {}).setdefault(v.probe_id, []).append(bit)
    return pool


def _bootstrap_ci(pool: dict[str, dict[str, list[float]]], cap: str) -> dict:
    """探测点为重采样单元、轮内随机取一轮的 cluster bootstrap，95% 分位区间。"""
    probes = pool.get(cap)
    if not probes:
        return {"ci95": None, "n_score_probes": 0}
    rng = random.Random(_BOOTSTRAP_SEED)
    stats = list(probes.values())
    means = []
    for _ in range(_BOOTSTRAP_B):
        acc = 0.0
        for _ in range(len(stats)):
            vals = stats[rng.randrange(len(stats))]
            acc += vals[rng.randrange(len(vals))]
        means.append(acc / len(stats))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(int(0.975 * len(means)), len(means) - 1)]
    return {"ci95": [round(lo, 4), round(hi, 4)], "n_score_probes": len(stats)}


def aggregate_runs(run_dirs: list[Path], out_dir: Path) -> dict:
    """聚合 N 轮运行：要求同智能体同题库，否则视为口径混杂直接报错。"""
    runs = []
    for d in run_dirs:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        runs.append((manifest, metrics))

    # manifest.cases 是 list（用例清单），str 化后才能进集合键
    keys = {(m.get("adapter"), str(m.get("cases"))) for m, _ in runs}
    if len(keys) != 1:
        raise ValueError(f"口径混杂，拒绝聚合：{sorted(map(str, keys))}（应同智能体同题库）")

    caps = {}
    dropped: dict[str, dict] = {}
    probe_pooled = _load_probe_pool(run_dirs)
    for cap in _CAP_ORDER:
        scores = [mt["capability_scores"].get(cap) for _, mt in runs]
        if any(s is None for s in scores):
            # R56：缺维不再静默消失——哪轮未测要可见（未测≠0 分，也不该蒸发）
            dropped[cap] = {"label_zh": CAP_LABELS_ZH[cap],
                            "n_missing_runs": sum(s is None for s in scores)}
            continue
        mean, std = _mean_std(scores)
        entry = {"label_zh": CAP_LABELS_ZH[cap], "n": len(scores),
                 "mean": round(mean, 4), "std": round(std, 4),
                 "min": round(min(scores), 4), "max": round(max(scores), 4)}
        entry.update(_bootstrap_ci(probe_pooled, cap))
        caps[cap] = entry
    overalls = [mt["overall_score"] for _, mt in runs]
    if any(o is None for o in overalls):
        raise ValueError("存在无有效计分探测点的轮次（overall=None），拒绝聚合")
    overall_m, overall_s = _mean_std(overalls)

    overall_ci = _bootstrap_ci(probe_pooled, "__overall__")
    result = {
        "adapter": runs[0][0].get("adapter"),
        "cases": runs[0][0].get("cases"),
        "n_runs": len(runs),
        "overall": {"mean": round(overall_m, 4), "std": round(overall_s, 4),
                    **overall_ci},
        "capabilities": caps,
        "dropped_capabilities": dropped,   # R56：缺维标注（含哪几轮未测）
        "runs": [{"run_id": m.get("run_id"),
                  "overall_score": round(mt["overall_score"], 4)
                  if mt.get("overall_score") is not None else None,
                  # 每轮分母（R12）：剔除口径不同的轮次不能裸平均
                  "n_valid": mt.get("n_valid"),
                  "n_invalid_run": mt.get("n_invalid_run"),
                  "n_human_review": mt.get("n_human_review")}
                 for m, mt in runs],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "aggregate.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def format_table(result: dict) -> str:
    lines = [f"{result['adapter']} × {result['cases']}　{result['n_runs']} 轮",
             f"总分：{result['overall']['mean']:.1%} ± {result['overall']['std']:.1%}"]
    ci = result["overall"].get("ci95")
    if ci:
        n_probe = result["overall"].get("n_score_probes", "?")
        lines[-1] += f"（95% CI {ci[0]:.0%}~{ci[1]:.0%}，bootstrap {n_probe} 探测点）"
    if result.get("dropped_capabilities"):
        miss = "、".join(f"{d['label_zh']}({d['n_missing_runs']} 轮未测)"
                         for d in result["dropped_capabilities"].values())
        lines.append(f"未入表维度：{miss}（未测≠0 分）")
    lines.append("维度｜均值±标准差（min~max）")
    for c in result["capabilities"].values():
        row = (f"  {c['label_zh']}：{c['mean']:.1%} ± {c['std']:.1%}"
               f"（{c['min']:.0%}~{c['max']:.0%}，n={c['n']}）")
        if c.get("ci95"):
            row += f" 95% CI {c['ci95'][0]:.0%}~{c['ci95'][1]:.0%}"
        lines.append(row)
    return "\n".join(lines)
