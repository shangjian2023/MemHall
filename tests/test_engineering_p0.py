"""工程化整改 P0（docs/engineering-tasks.md T01-T06）回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import memhall
from memhall.adapters import ADAPTERS, create_adapter
from memhall.adapters.base import AgentAdapter
from memhall.adapters.mock import MockAdapter
from memhall.cli import load_case_set, load_cases_for_run
from memhall.paths import QUICK_IDS
from memhall.runner.orchestrator import run_suite
from memhall.schema.evidence import VerdictValue
from memhall.schema.models_case import MemoryCase, RuleAssert, RuleProbe
from memhall.scoring.engine import evaluate_case
from memhall.scoring.rules import EvidenceMissing, EvidenceStore, run_check

REPO = Path(__file__).resolve().parents[1]


# ---------- T01 版本单一来源 ----------

def test_version_is_not_stale_constant():
    """__version__ 从包元数据取（曾硬编码 0.1.0 与 pyproject 0.2.1 漂移）。"""
    assert memhall.__version__ != "0.1.0"
    if memhall.__version__ != "dev":  # 已安装环境必须与元数据一致
        from importlib.metadata import version
        assert memhall.__version__ == version("memhall")


# ---------- T04 适配器注册表 ----------

def test_adapter_registry_covers_all_names():
    assert set(ADAPTERS) == {"mock", "hermes", "kylinbot", "openclaw",
                             "kylin-agent", "hermes-local", "claude-local",
                             "qwen-local", "opencode"}


def test_create_adapter_mock_and_unknown():
    a = create_adapter("mock")
    assert isinstance(a, MockAdapter) and isinstance(a, AgentAdapter)
    with pytest.raises(ValueError, match="未知适配器"):
        create_adapter("no-such-agent")


# ---------- T05 fs 断言必须有证据 ----------

def _fs_probe() -> RuleProbe:
    return RuleProbe(
        kind="rule", id="reuse-x-p1", after="probe",
        check=[RuleAssert(assert_name="fs.path_exists", args=["~/dev/src"],
                          then="correct_reuse"),
               RuleAssert(assert_name="default", args=[], then="wrong_reuse")])


def test_fs_rule_without_evidence_raises():
    with pytest.raises(EvidenceMissing):
        run_check([b.model_dump(by_alias=True) for b in _fs_probe().check],
                  EvidenceStore())


def _case_with(probe: RuleProbe) -> MemoryCase:
    return MemoryCase(
        case_id=probe.id.rsplit("-", 1)[0], schema_version="0.1",
        capability="reuse", question_type="task_chain", content_type="path",
        difficulty=1,
        meta={"author": "test", "created": "2026-10-02", "source": "seed"},
        phases=[], probes=[probe])


def test_fs_probe_without_evidence_is_invalid_run():
    verdicts = evaluate_case(_case_with(_fs_probe()), EvidenceStore(), "run-x")
    assert verdicts[0].verdict == VerdictValue.INVALID_RUN
    assert "fs_diff 证据缺失" in verdicts[0].explanation


def test_mock_has_empty_workspace_evidence():
    """mock 返回空工作区（非 None）：fs_diff 证据存在且零创建，
    reuse 文件断言确定性失败（设计画像"只说不做"），不依赖判卷机磁盘。"""
    a = MockAdapter()
    a.send("s-01", "在 ~/dev/src 下建一个 demo 目录")
    assert a.fs_snapshot() == []


# ---------- T02/T03 用例快照与 run 自包含 ----------

def test_run_snapshots_cases_for_report(tmp_path: Path):
    cases = load_case_set("cases/quick")
    run_id, stores = run_suite(MockAdapter(), cases, tmp_path, "mock",
                               case_source="cases/quick")
    run_dir = tmp_path / run_id

    snap = run_dir / "cases" / "persist-002" / "case.yaml"
    assert snap.is_file()
    reloaded = MemoryCase.model_validate(
        yaml.safe_load(snap.read_text(encoding="utf-8")))
    assert reloaded.case_id == "persist-002"

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["case_source"] == "cases/quick"

    by_id = load_cases_for_run(run_dir)
    assert set(by_id) == {c.case_id for c in cases}


def test_report_replay_from_snapshot_only(tmp_path: Path, monkeypatch):
    """run 目录自包含：源码用例库不可达（模拟 deb 装机/换机）也能取回用例。"""
    cases = load_case_set("cases/quick")
    run_id, _ = run_suite(MockAdapter(), cases, tmp_path, "mock")
    run_dir = tmp_path / run_id
    monkeypatch.setattr("memhall.cli.resolve_case_dir", lambda spec: None)
    by_id = load_cases_for_run(run_dir)  # 快照优先，不退源码树
    assert len(by_id) == len(cases)


# ---------- T06 quick 虚拟集 ----------

def test_quick_virtual_set_six_capabilities():
    cases = load_case_set("cases/quick")
    assert len(cases) == 6
    assert {c.case_id for c in cases} == set(QUICK_IDS)
    assert len({c.capability.value for c in cases}) == 6  # 六能力各一
    assert not (REPO / "cases" / "quick").exists()  # 物理目录已删


def test_quick_spec_aliases():
    assert [c.case_id for c in load_case_set("cases/quick")] == \
           [c.case_id for c in load_case_set("quick")]
