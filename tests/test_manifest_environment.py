"""整合接缝回归：run manifest 封存 environment.sandbox（A/C 字段衔接）。

缺口：sandbox.py 文档声称沙箱状态"随 remote_environment 记入
manifest.environment.sandbox"，但运行路径从未写这格——只有 vm 健康检查
用到 remote_environment。C 的口径校验（compatibility.sandbox_state）按
environment.sandbox 归一 on/off，缺字段一律判"沙箱口径未封存"并拒绝
verified=true，等于所有新 run 都会被降级。

本网钉住：_write_manifest 无条件封存 sandbox 开关（与执行侧
adapters.sandbox 同源），且取值能被 C 的 sandbox_state 正确归一。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memhall.report.compatibility import sandbox_state
from memhall.runner.orchestrator import run_suite
from memhall.schema.evidence import ActionDump, MemorySnapshot, Reply
from memhall.schema.models_case import MemoryCase


class _FakeAdapter:
    """最小假适配器：不碰网关、不拨钟，只够 run_suite 走完一个 case。"""

    name = "fake"

    def reset(self):
        pass

    def verify_reset(self):
        pass

    def fs_snapshot(self):
        return None

    def dump_memory(self):
        return MemorySnapshot(format="none", dumped_at=datetime.now(UTC))

    def dump_actions(self):
        return ActionDump(actions=[], coverage="unknown")

    def clock_shift(self, days):
        pass

    def clock_restore(self):
        pass

    def end_session(self, session_id):
        pass

    def close(self):
        pass

    def version_info(self):
        return "fake-1"

    def send(self, session_id, text):
        t = datetime.now(UTC)
        return Reply(session_id=session_id, text="好的，记住了",
                     sent_at=t, reply_at=t, latency_ms=1)


def _mk_case(cid: str) -> MemoryCase:
    phase = {"name": "probe", "steps": [{"user": f"问题 {cid}"}]}
    return MemoryCase(
        case_id=cid, schema_version="0.1", capability="persist",
        question_type="session_recall", content_type="path", difficulty=1,
        meta={"author": "t", "created": "2026-10-14", "source": "seed"},
        phases=[phase],
        probes=[{"kind": "rule", "id": f"{cid}-p1",
                 "check": [{"assert": "default", "then": "correct"}]}],
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """直连形态（无 GATEWAY_*）+ 独享 HOME（run.lock 不与并行测试互串），
    MEMHALL_SANDBOX 清干净，用例自身控制开关。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for k in ("GATEWAY_URL", "GATEWAY_VM_URL", "MEMHALL_SANDBOX"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "runs"


def _sandbox_of(runs: Path, rid: str):
    manifest = json.loads((runs / rid / "manifest.json").read_text(encoding="utf-8"))
    return manifest, sandbox_state(manifest)


def test_manifest_seals_sandbox_off_by_default(env):
    rid, _ = run_suite(_FakeAdapter(), [_mk_case("c1")], env, "fake")
    manifest, state = _sandbox_of(env, rid)
    assert manifest["environment"]["sandbox"] == "off"
    assert state == "off"  # C 侧归一通过


def test_manifest_seals_sandbox_on(env, monkeypatch):
    monkeypatch.setenv("MEMHALL_SANDBOX", "1")
    rid, _ = run_suite(_FakeAdapter(), [_mk_case("c1")], env, "fake")
    manifest, state = _sandbox_of(env, rid)
    assert manifest["environment"]["sandbox"] == "on"
    assert state == "on"  # C 侧归一通过


def test_two_runs_same_state_compatible(env):
    """同一开关下两轮 → C 的 check_compatibility 能读到封存值，
    不再因"沙箱口径未封存"降级告警。"""
    from memhall.report.compatibility import check_compatibility

    r1, _ = run_suite(_FakeAdapter(), [_mk_case("c1")], env, "fake")
    r2, _ = run_suite(_FakeAdapter(), [_mk_case("c1")], env, "fake")
    result = check_compatibility([env / r1, env / r2])
    assert result["identity"]["sandbox"] == "off"
    assert not any("沙箱" in w for w in result["warnings"])
