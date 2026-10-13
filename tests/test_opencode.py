"""OpenCodeAdapter：沙箱配置生成 / 记忆导出 / fs 快照 / 拨钟不支持。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.base import AdapterError, AgentUnavailable
from memhall.adapters.opencode import OpenCodeAdapter


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-test")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen3.7-plus")
    return OpenCodeAdapter(root=tmp_path)


def test_reset_writes_isolated_config(adapter, tmp_path):
    adapter.reset()
    cfg = adapter.cfg_dir / "opencode.json"
    assert cfg.is_file()
    assert str(cfg).startswith(str(tmp_path))
    import json
    d = json.loads(cfg.read_text(encoding="utf-8"))
    prov = d["provider"]["memhall-gw"]
    assert prov["options"]["baseURL"] == "https://gw.example/v1"
    assert prov["options"]["apiKey"] == "sk-test"
    assert "qwen3.7-plus" in prov["models"]
    assert adapter.workspace.is_dir()


def test_dump_memory_reads_agents_md_and_notes(adapter):
    adapter.workspace.mkdir(parents=True, exist_ok=True)
    (adapter.workspace / "AGENTS.md").write_text(
        "# 记忆\n- 用户代号：青鸟\n", encoding="utf-8")
    notes = adapter.workspace / "notes"
    notes.mkdir()
    (notes / "preferences.md").write_text("喜欢简洁回复", encoding="utf-8")
    (adapter.workspace / ".opencode").mkdir()
    (adapter.workspace / ".opencode" / "session.json").write_text("{}", encoding="utf-8")
    snap = adapter.dump_memory()
    by_id = {e.entry_id: e for e in snap.entries}
    assert "agents-md" in by_id and "青鸟" in by_id["agents-md"].content
    assert "file:notes/preferences.md" in by_id
    assert all(not e.entry_id.startswith("file:.opencode") for e in snap.entries)


def test_fs_snapshot_skips_dotdirs(adapter):
    adapter.workspace.mkdir(parents=True, exist_ok=True)
    (adapter.workspace / "README.md").write_text("x", encoding="utf-8")
    d = adapter.workspace / ".opencode"
    d.mkdir()
    (d / "db.json").write_text("{}", encoding="utf-8")
    assert adapter.fs_snapshot() == ["~/README.md"]


def test_clock_shift_unsupported(adapter):
    with pytest.raises(AdapterError):
        adapter.clock_shift(7)
    adapter.clock_shift(0)  # 0 天是 no-op


def test_missing_gateway_env(tmp_path, monkeypatch):
    for k in ("AGENT_LLM_BASE_URL", "AGENT_LLM_KEY"):
        monkeypatch.delenv(k, raising=False)
    a = OpenCodeAdapter(root=tmp_path)
    with pytest.raises(AgentUnavailable):
        a.reset()
