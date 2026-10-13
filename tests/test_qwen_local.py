"""LocalQwenAdapter：沙箱拷配置剥钩子 / QWEN_HOME 映射 / 记忆解析。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.base import AdapterError, AgentUnavailable
from memhall.adapters.qwen_local import LocalQwenAdapter


def _fake_qwen_home(tmp_path: Path, monkeypatch) -> Path:
    """伪造 ~/.qwen/settings.json（Path.home() 走 USERPROFILE/HOME 环境变量）。"""
    home = tmp_path / "home"
    (home / ".qwen").mkdir(parents=True)
    (home / ".qwen" / "settings.json").write_text(json.dumps({
        "modelProviders": {"openai": [{"id": "qwen3.6-plus", "baseUrl":
                                       "https://gw.example/v1", "envKey": "K"}]},
        "env": {"K": "sk-test"},
        "security": {"auth": {"selectedType": "openai"}},
        "model": {"name": "qwen3.6-plus"},
        "hooks": {"SessionStart": [{"hooks": [{"name": "clawd",
                                               "type": "command",
                                               "command": "evil.ps1"}]}]},
    }), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    _fake_qwen_home(tmp_path, monkeypatch)
    return LocalQwenAdapter(root=tmp_path / "sandbox")


def test_reset_copies_settings_and_strips_hooks(adapter):
    adapter.reset()
    copied = adapter.qwen_home / "settings.json"
    assert copied.is_file()
    data = json.loads(copied.read_text(encoding="utf-8"))
    assert "hooks" not in data            # clawd 钩子必须剥掉
    assert data["env"]["K"] == "sk-test"  # 认证原样带过来


def test_reset_applies_gateway_model(adapter, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen3.7-plus")
    adapter.reset()
    data = json.loads((adapter.qwen_home / "settings.json").read_text(encoding="utf-8"))
    assert data["model"]["name"] == "qwen3.7-plus"
    assert data["modelProviders"]["openai"][0]["id"] == "qwen3.7-plus"


def test_sandbox_env(adapter):
    adapter.reset()
    env = adapter._sandbox_env()
    assert env["QWEN_HOME"] == str(adapter.qwen_home)


def test_missing_settings(tmp_path, monkeypatch):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(AgentUnavailable):
        LocalQwenAdapter(root=tmp_path / "sandbox").reset()


def test_dump_memory_reads_global_and_workspace_qwen_md(adapter):
    adapter.reset()
    (adapter.qwen_home / "QWEN.md").write_text(
        "# 全局\n- 用户代号：青鸟\n", encoding="utf-8")
    (adapter.workspace / "QWEN.md").write_text(
        "- 构建产物在 ~/out/build\n", encoding="utf-8")
    snap = adapter.dump_memory()
    contents = [e.content for e in snap.entries]
    assert any("青鸟" in c for c in contents)
    assert any("out/build" in c for c in contents)


def test_dump_memory_keeps_leading_minus_content(adapter):
    """R58：列表标记只剥"- "/"* "形态，"-3°C"类正文首字符不得误剥。"""
    adapter.reset()
    (adapter.workspace / "QWEN.md").write_text(
        "- 最低运行温度 -3°C\n", encoding="utf-8")
    contents = [e.content for e in adapter.dump_memory().entries]
    assert "最低运行温度 -3°C" in contents


def test_clock_shift_unsupported(adapter):
    with pytest.raises(AdapterError):
        adapter.clock_shift(3)
    adapter.clock_shift(0)  # 0 天 no-op
