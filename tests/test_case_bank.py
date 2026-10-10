from __future__ import annotations

import json
from pathlib import Path

import pytest

from memhall.cli import load_cases
from memhall.runner.case_bank import load_rotation_history, select_cases


REPO = Path(__file__).parent.parent


def test_case_bank_deduplicates_quick_and_balances_capabilities():
    cases = load_cases(REPO / "cases")
    assert len(cases) > 50

    first = select_cases(cases, seed=20261010, count=12)
    second = select_cases(cases, seed=20261010, count=12)
    assert [c.case_id for c in first.cases] == [c.case_id for c in second.cases]
    assert len({c.capability.value for c in first.cases}) == 6
    assert len({c.case_id for c in first.cases}) == 12

    other = select_cases(cases, seed=20261011, count=12)
    assert [c.case_id for c in first.cases] != [c.case_id for c in other.cases]


def test_case_bank_filters_and_requires_seed_for_rotation():
    cases = load_cases(REPO / "cases")
    selection = select_cases(
        cases, seed=9, count=4,
        capabilities=["persist,recall"], difficulties=[1, 2],
    )
    assert {c.capability.value for c in selection.cases} <= {"persist", "recall"}
    assert {c.difficulty for c in selection.cases} <= {1, 2}

    with pytest.raises(ValueError, match="必须同时指定 seed"):
        select_cases(cases, count=4)


def test_case_bank_history_prefers_unseen_cases(tmp_path: Path):
    cases = load_cases(REPO / "cases/full")
    first = select_cases(cases, seed=17, count=5)
    run_dir = tmp_path / "20260101-000000-mock"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "finished_at": "2026-01-01T00:00:00Z",
        "case_selection": {"case_ids": [c.case_id for c in first.cases]},
    }), encoding="utf-8")
    history = load_rotation_history(tmp_path, {c.case_id for c in cases})
    second = select_cases(cases, seed=17, count=5, history=history)
    assert not set(c.case_id for c in first.cases) & {c.case_id for c in second.cases}
    assert second.previous_case_ids_count == 5
