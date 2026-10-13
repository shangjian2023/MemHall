"""Keep probes from the same case/family together in statistical analyses."""

from __future__ import annotations

import random
from pathlib import Path

from memhall.datasets import family_of
from memhall.report.compatibility import snapshot_cases
from memhall.schema.evidence import Verdict, VerdictValue
from memhall.schema.models_case import probe_role

EXCLUDED = {VerdictValue.INVALID_RUN, VerdictValue.HUMAN_REVIEW}


def score_index(run_dir: Path) -> dict[str, tuple[str, str, str]]:
    cases = snapshot_cases(run_dir)
    if cases is None:
        return {}
    return {
        p.id: (c.capability.value, c.case_id, family_of(c))
        for c in cases
        for p in c.probes
        if probe_role(c, p) == "score"
    }


def countable(verdict: Verdict) -> bool:
    return verdict.verdict not in EXCLUDED and not verdict.degraded


def load_clusters(run_dirs: list[Path]) -> dict:
    """capability -> family -> probe -> fixed-length per-round values (None = unscored)."""
    pool: dict = {"__overall__": {}}
    for round_index, directory in enumerate(run_dirs):
        path = directory / "verdicts.jsonl"
        index = score_index(directory)
        if not path.is_file() or not index:
            return {}
        seen: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            v = Verdict.model_validate_json(line)
            if v.probe_id in seen:
                raise ValueError(f"重复判定: {directory.name}/{v.probe_id}")
            seen.add(v.probe_id)
            if v.probe_id not in index:
                continue
            cap, case_id, family = index[v.probe_id]
            if v.case_id != case_id:
                raise ValueError(f"判定归属与题目快照不一致: {v.probe_id}")
            bit = float(v.verdict == VerdictValue.CORRECT) if countable(v) else None
            for key in (cap, "__overall__"):
                values = (
                    pool.setdefault(key, {})
                    .setdefault(family, {})
                    .setdefault(v.probe_id, [None] * len(run_dirs))
                )
                values[round_index] = bit
    return pool


def bootstrap_ci(pool: dict, cap: str, *, rounds: int, seed: int = 20261013, repetitions: int = 2000) -> dict:
    clusters = list(pool.get(cap, {}).values())
    base: dict = {
        "ci95": None,
        "n_score_probes": sum(len(c) for c in clusters),
        "n_clusters": len(clusters),
        "unit": "family_or_case",
        "method": "resample families and whole rounds; mean per-round probe accuracy",
        "bootstrap_seed": seed,
        "bootstrap_repetitions": repetitions,
    }
    if not clusters:
        return base
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(repetitions):
        sample = [rng.choice(clusters) for _ in clusters]
        round_scores: list[float] = []
        for _ in range(rounds):
            r = rng.randrange(rounds)
            bits = [vs[r] for cluster in sample for vs in cluster.values() if vs[r] is not None]
            if bits:
                round_scores.append(sum(bits) / len(bits))
        if round_scores:
            means.append(sum(round_scores) / len(round_scores))
    if not means:
        return base
    means.sort()
    base["ci95"] = [
        round(means[int(0.025 * len(means))], 4),
        round(means[min(int(0.975 * len(means)), len(means) - 1)], 4),
    ]
    return base


def family_balanced(pool: dict) -> float | None:
    values = []
    for cluster in pool.get("__overall__", {}).values():
        probe_means = [
            sum(bits) / len(bits) for vs in cluster.values() if (bits := [v for v in vs if v is not None])
        ]
        if probe_means:
            values.append(sum(probe_means) / len(probe_means))
    return sum(values) / len(values) if values else None
