from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import memhall.runner.orchestrator as orchestrator
from memhall.adapters.mock import MockAdapter
from memhall.cli import load_case_set
from memhall.runner.case_rotation import rotate_cases
from memhall.runner.orchestrator import run_suite


def test_rotation_is_recorded_and_prefers_unseen_cases(tmp_path: Path):
    cases = load_case_set("cases/full")
    a = rotate_cases(cases, count=12, seed=31, out_dir=tmp_path / "empty")
    b = rotate_cases(cases, count=12, seed=31, out_dir=tmp_path / "empty")
    assert [case.case_id for case in a.cases] == [case.case_id for case in b.cases]
    assert len({case.capability.value for case in a.cases}) == 6

    first, stores_a = run_suite(
        MockAdapter(), cases, tmp_path, "mock", case_source="cases/full",
        rotate_count=6, rotation_seed=20261011,
    )
    first_manifest = json.loads(
        (tmp_path / first / "manifest.json").read_text(encoding="utf-8"))
    first_ids = first_manifest["cases"]
    assert len(first_ids) == 6
    assert len(stores_a) == 6
    assert first_manifest["case_rotation"]["seed"] == 20261011
    assert sum(first_manifest["case_rotation"]["capability_counts"].values()) == 6

    second, stores_b = run_suite(
        MockAdapter(), cases, tmp_path, "mock", case_source="cases/full",
        rotate_count=6, rotation_seed=20261012,
    )
    second_manifest = json.loads(
        (tmp_path / second / "manifest.json").read_text(encoding="utf-8"))
    assert len(stores_b) == 6
    assert not set(first_ids) & set(second_manifest["cases"])


def test_rotation_requires_seed(tmp_path: Path):
    cases = load_case_set("cases/full")
    try:
        run_suite(MockAdapter(), cases, tmp_path, "mock", rotate_count=2)
    except ValueError as error:
        assert "rotation_seed" in str(error)
    else:
        raise AssertionError("缺少 seed 时应拒绝轮换")
    assert not list(tmp_path.iterdir())


def test_same_second_runs_get_distinct_directories(tmp_path: Path, monkeypatch):
    fixed = datetime(2026, 10, 11, tzinfo=UTC)
    monkeypatch.setattr(orchestrator, "_utc", lambda: fixed)
    cases = load_case_set("cases/quick")[:1]
    first, _ = run_suite(MockAdapter(), cases, tmp_path, "mock")
    second, _ = run_suite(MockAdapter(), cases, tmp_path, "mock")
    assert first != second
    assert (tmp_path / first / "manifest.json").is_file()
    assert (tmp_path / second / "manifest.json").is_file()
