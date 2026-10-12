"""确定性题目轮换：结合能力/难度平衡与历史运行频次。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from memhall.schema.models_case import MemoryCase


@dataclass(frozen=True)
class CaseRotation:
    cases: list[MemoryCase]
    seed: int
    source_count: int
    requested_count: int
    prior_runs: int

    def manifest(self) -> dict:
        by_capability = Counter(case.capability.value for case in self.cases)
        by_difficulty = Counter(case.difficulty for case in self.cases)
        return {
            "seed": self.seed,
            "source_count": self.source_count,
            "selected_count": len(self.cases),
            "case_ids": [case.case_id for case in self.cases],
            "capability_counts": dict(sorted(by_capability.items())),
            "difficulty_counts": dict(sorted(by_difficulty.items())),
            "prior_runs_considered": self.prior_runs,
        }


def _rank(seed: int, case_id: str) -> int:
    digest = hashlib.sha256(f"{seed}:{case_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _load_history(out_dir: Path, known_ids: set[str]) -> tuple[Counter[str], dict[str, int], int]:
    runs: list[tuple[str, str, list[str]]] = []
    if not out_dir.is_dir():
        return Counter(), {}, 0
    for path in out_dir.glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            raw_ids = manifest.get("cases", [])
            if not isinstance(raw_ids, list):
                continue
            stamp = str(manifest.get("finished_at") or manifest.get("started_at") or "")
            if not stamp:
                stamp = str(path.stat().st_mtime_ns)
            ids = [str(case_id) for case_id in raw_ids if str(case_id) in known_ids]
            if ids:
                runs.append((stamp, path.parent.name, ids))
        except (OSError, ValueError, TypeError):
            continue
    runs.sort(key=lambda item: item[0])
    frequencies: Counter[str] = Counter()
    last_seen: dict[str, int] = {}
    for run_index, (_, _, ids) in enumerate(runs):
        for case_id in ids:
            frequencies[case_id] += 1
            last_seen[case_id] = run_index
    return frequencies, last_seen, len(runs)


def rotate_cases(
    cases: Iterable[MemoryCase],
    *,
    count: int,
    seed: int,
    out_dir: Path,
) -> CaseRotation:
    """Pick a reproducible subset, preferring less-tested cases and balancing strata."""

    pool = list(cases)
    ids = [case.case_id for case in pool]
    if len(ids) != len(set(ids)):
        raise ValueError("轮换题库包含重复 case_id")
    if count <= 0:
        raise ValueError("--rotate-count 必须大于 0")
    if count > len(pool):
        raise ValueError(f"请求轮换 {count} 道题，但题库只有 {len(pool)} 道")

    frequencies, last_seen, prior_runs = _load_history(out_dir, set(ids))
    remaining = list(pool)
    selected: list[MemoryCase] = []
    capability_counts: Counter[str] = Counter()
    difficulty_counts: Counter[int] = Counter()
    while len(selected) < count:
        remaining.sort(key=lambda case: (
            frequencies[case.case_id],
            last_seen.get(case.case_id, -1),
            capability_counts[case.capability.value],
            difficulty_counts[case.difficulty],
            _rank(seed, case.case_id),
        ))
        case = remaining.pop(0)
        selected.append(case)
        frequencies[case.case_id] += 1
        capability_counts[case.capability.value] += 1
        difficulty_counts[case.difficulty] += 1

    return CaseRotation(
        cases=selected,
        seed=seed,
        source_count=len(pool),
        requested_count=count,
        prior_runs=prior_runs,
    )
