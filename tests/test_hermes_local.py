"""LocalHermesAdapter：沙箱布局 / 网关映射 / 记忆解析 / 拨钟不支持。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.base import AdapterError, AgentUnavailable
from memhall.adapters.hermes_local import LocalHermesAdapter


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-test")
    return LocalHermesAdapter(root=tmp_path)


def test_reset_layout_and_env_mapping(adapter, tmp_path):
    adapter.reset()
    assert adapter.workspace.is_dir()
    assert adapter.mem_dir.is_dir()
    env = adapter._sandbox_env()
    assert env["HERMES_HOME"] == str(adapter.home)
    assert str(adapter.home).startswith(str(tmp_path))
    assert env["DEEPSEEK_BASE_URL"] == "https://gw.example/v1"
    assert env["DEEPSEEK_API_KEY"] == "sk-test"


def test_missing_gateway_env(tmp_path, monkeypatch):
    for k in ("AGENT_LLM_BASE_URL", "AGENT_LLM_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(AgentUnavailable):
        LocalHermesAdapter(root=tmp_path).reset()


def test_dump_memory_parses_bullet_files(adapter):
    adapter.mem_dir.mkdir(parents=True, exist_ok=True)
    (adapter.mem_dir / "MEMORY.md").write_text(
        "# 记忆\n- 用户代号：青鸟\n- 用户在杭州\n", encoding="utf-8")
    (adapter.mem_dir / "USER.md").write_text(
        "* 喜欢简洁回复\n", encoding="utf-8")
    snap = adapter.dump_memory()
    contents = [e.content for e in snap.entries]
    assert any("青鸟" in c for c in contents)
    assert any("杭州" in c for c in contents)
    assert any("简洁回复" in c for c in contents)
    assert all(e.source_turn in ("MEMORY.md", "USER.md") for e in snap.entries)


def test_fs_snapshot_skips_dotdirs(adapter):
    adapter.workspace.mkdir(parents=True, exist_ok=True)
    (adapter.workspace / "README.md").write_text("x", encoding="utf-8")
    d = adapter.workspace / ".cache"
    d.mkdir()
    (d / "junk").write_text("{}", encoding="utf-8")
    assert adapter.fs_snapshot() == ["~/README.md"]


def test_clock_shift_unsupported(adapter):
    with pytest.raises(AdapterError):
        adapter.clock_shift(3)
    adapter.clock_shift(0)  # 0 天 no-op
