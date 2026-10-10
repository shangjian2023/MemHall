from __future__ import annotations

from pathlib import Path

from memhall.adapters.mock import MockAdapter
from memhall.cli import load_cases
from memhall.runner.orchestrator import run_suite


REPO = Path(__file__).parent.parent


def test_run_suite_does_not_overwrite_same_second_runs(tmp_path: Path):
    cases = load_cases(REPO / "cases/quick")[:1]
    first, _ = run_suite(MockAdapter(), cases, tmp_path, "mock")
    second, _ = run_suite(MockAdapter(), cases, tmp_path, "mock")
    assert first != second
    assert (tmp_path / first / "manifest.json").exists()
    assert (tmp_path / second / "manifest.json").exists()
