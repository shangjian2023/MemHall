"""题库加载后的确定性轮换与覆盖选择。

题库文件仍然是普通 YAML，因此可以在不重启评测器的情况下替换或追加题目。
每一轮只把选择结果写入 manifest；给定相同 seed、筛选条件和题库内容时，
选择结果保持一致，方便复现和审计。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from memhall.schema.models_case import MemoryCase


@dataclass(frozen=True)
class CaseSelection:
    """一轮题库选择的可审计摘要。"""

    cases: list[MemoryCase]
    seed: int | None
    requested_count: int | None
    capabilities: tuple[str, ...]
    difficulties: tuple[int, ...]
    source_count: int
    previous_case_ids_count: int = 0
    reused_case_ids: tuple[str, ...] = ()

    def manifest(self) -> dict:
        return {
            "seed": self.seed,
            "requested_count": self.requested_count,
            "source_count": self.source_count,
            "selected_count": len(self.cases),
            "capabilities": list(self.capabilities),
            "difficulties": list(self.difficulties),
            "case_ids": [case.case_id for case in self.cases],
            "previous_case_ids_count": self.previous_case_ids_count,
            "reused_case_ids": list(self.reused_case_ids),
        }


def _case_key(case: MemoryCase) -> str:
    return case.case_id


def _rank(seed: int, case_id: str) -> int:
    """稳定排序键；不使用 Python 内置 hash，避免跨进程漂移。"""

    digest = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _normalise_capabilities(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    out: set[str] = set()
    for value in values:
        out.update(part.strip() for part in value.split(",") if part.strip())
    return tuple(sorted(out))


def _normalise_difficulties(values: Iterable[int | str] | None) -> tuple[int, ...]:
    if not values:
        return ()
    out: set[int] = set()
    for value in values:
        for part in str(value).split(","):
            if part.strip():
                out.add(int(part.strip()))
    invalid = out - {1, 2, 3}
    if invalid:
        raise ValueError(f"difficulty 只能是 1/2/3，收到: {sorted(invalid)}")
    return tuple(sorted(out))


def load_rotation_history(out_dir: Path, case_ids: set[str]) -> list[str]:
    """从已有评测 manifest 按时间读取最近使用顺序。"""

    manifests: list[tuple[str, dict]] = []
    if not out_dir.exists():
        return []
    for path in out_dir.glob("*/manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            finished = str(data.get("finished_at", ""))
            if not finished:
                finished = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
            ids = data.get("case_selection", {}).get("case_ids", data.get("cases", []))
            if isinstance(ids, list):
                manifests.append((finished, data))
        except (OSError, ValueError, TypeError):
            continue
    manifests.sort(key=lambda item: item[0])
    return [
        case_id
        for _, manifest in manifests
        for case_id in manifest.get("case_selection", {}).get("case_ids", manifest.get("cases", []))
        if case_id in case_ids
    ]


def select_cases(
    cases: Iterable[MemoryCase],
    *,
    seed: int | None = None,
    count: int | None = None,
    capabilities: Iterable[str] | None = None,
    difficulties: Iterable[int | str] | None = None,
    history: Iterable[str] = (),
) -> CaseSelection:
    """按筛选条件和 seed 选择一轮题目。

    选择器以能力族为一级覆盖目标，使用确定性贪心轮换：先覆盖尚未出现的
    能力，再平衡各能力和难度的数量，最后用 seed 打破平局。count 省略时
    返回筛选后的全部题目；指定 count 时必须同时指定 seed，防止无法复现。
    """

    all_cases = list(cases)
    ids = [case.case_id for case in all_cases]
    duplicates = sorted(case_id for case_id, n in Counter(ids).items() if n > 1)
    if duplicates:
        raise ValueError(f"题库存在重复 case_id: {duplicates}")

    cap_filter = _normalise_capabilities(capabilities)
    diff_filter = _normalise_difficulties(difficulties)
    allowed_caps = set(cap_filter)
    allowed_diffs = set(diff_filter)
    pool = [
        case for case in all_cases
        if (not allowed_caps or case.capability.value in allowed_caps)
        and (not allowed_diffs or case.difficulty in allowed_diffs)
    ]
    pool.sort(key=_case_key)

    if count is not None:
        if count <= 0:
            raise ValueError("count 必须大于 0")
        if seed is None:
            raise ValueError("指定 count 时必须同时指定 seed，保证题目轮换可复现")
        if count > len(pool):
            raise ValueError(f"请求 {count} 道题，但筛选后只有 {len(pool)} 道")
        history_order: dict[str, int] = {}
        history_list = list(history)
        for index, case_id in enumerate(history_list):
            history_order[case_id] = index
        remaining = sorted(pool, key=lambda case: _rank(seed, case.case_id))
        selected: list[MemoryCase] = []
        cap_counts: Counter[str] = Counter()
        diff_counts: Counter[int] = Counter()
        while remaining and len(selected) < count:
            remaining.sort(key=lambda case: (
                1 if case.case_id in history_order else 0,
                history_order.get(case.case_id, -1),
                cap_counts[case.capability.value],
                diff_counts[case.difficulty],
                _rank(seed, case.case_id),
            ))
            case = remaining.pop(0)
            selected.append(case)
            cap_counts[case.capability.value] += 1
            diff_counts[case.difficulty] += 1
        pool = selected
    elif seed is not None:
        # 全量模式也允许 seed 固定执行顺序，便于复现实验日志；不改变题目集合。
        pool.sort(key=lambda case: _rank(seed, case.case_id))

    return CaseSelection(
        cases=pool,
        seed=seed,
        requested_count=count,
        capabilities=cap_filter,
        difficulties=diff_filter,
        source_count=len(all_cases),
        previous_case_ids_count=len(history_list) if count is not None else 0,
        reused_case_ids=tuple(
            case.case_id for case in pool
            if case.case_id in history_order
        ) if count is not None else (),
    )
