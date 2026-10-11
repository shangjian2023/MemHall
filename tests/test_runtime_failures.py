from __future__ import annotations

from pathlib import Path

from memhall.adapters.base import AgentUnavailable
from memhall.adapters.mock import MockAdapter
from memhall.cli import load_cases
from memhall.runner.orchestrator import run_suite
from memhall.scoring.engine import evaluate_case
from memhall.schema.evidence import VerdictValue


REPO = Path(__file__).parent.parent


class FailingAdapter(MockAdapter):
    def send(self, session_id: str, message: str):
        raise AgentUnavailable("测试适配器不可用")


def test_adapter_failure_marks_every_probe_invalid_run(tmp_path: Path):
    case = load_cases(REPO / "cases/quick")[0]
    run_id, stores = run_suite(FailingAdapter(), [case], tmp_path, "failing")
    verdicts = evaluate_case(case, stores[0], run_id)
    assert verdicts
    assert all(v.verdict == VerdictValue.INVALID_RUN for v in verdicts)
    assert all(v.decided_by.value == "runtime" for v in verdicts)
