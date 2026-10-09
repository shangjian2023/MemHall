"""LocalClaudeAdapter：沙箱布局 / 认证映射 / 记忆解析 / 拨钟不支持。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.base import AdapterError, AgentUnavailable
from memhall.adapters.claude_local import LocalClaudeAdapter


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_LLM_BASE_URL", "https://gw.example/api/anthropic")
    monkeypatch.setenv("CLAUDE_LLM_KEY", "sk-test")
    return LocalClaudeAdapter(root=tmp_path)


def test_reset_layout_and_env_mapping(adapter, tmp_path):
    adapter.reset()
    assert adapter.workspace.is_dir()
    assert adapter.config_dir.is_dir()
    env = adapter._sandbox_env()
    assert env["CLAUDE_CONFIG_DIR"] == str(adapter.config_dir)
    assert str(adapter.config_dir).startswith(str(tmp_path))
    assert env["ANTHROPIC_BASE_URL"] == "https://gw.example/api/anthropic"


def test_sandbox_env_prepends_exe_and_node_dirs(adapter, tmp_path, monkeypatch):
    """PATH 加固（2026-10-09 VM 实测 which claude 空、node 127）：claude/node
    不在服务进程 PATH 上时，沙箱 env 把两者所在目录前置——保证
    #!/usr/bin/env node 的 shebang 与 pnpm/volta shim 都找得到 node。"""
    import os

    import memhall.discovery as disc
    exe = tmp_path / "jsbin" / "claude"
    node = tmp_path / "jsbin" / "node"
    exe.parent.mkdir()
    exe.write_text("")
    node.write_text("")
    monkeypatch.setattr(disc, "find_cli",
                        lambda *c: str(exe) if "claude" in c else str(node))
    env = adapter._sandbox_env()
    assert env["PATH"].startswith(str(tmp_path / "jsbin") + os.pathsep)
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"


def test_model_tier_mapping(adapter, monkeypatch):
    monkeypatch.setenv("CLAUDE_LLM_MODEL", "glm-5.3")
    env = adapter._sandbox_env()
    for tier in ("SONNET", "OPUS", "HAIKU", "FABLE"):
        assert env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] == "glm-5.3"


def test_openai_gateway_model_not_mapped(adapter, monkeypatch):
    # AGENT_LLM_MODEL 是 openai 网关口径（qwen3.7-plus），不得混进 claude
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen3.7-plus")
    env = adapter._sandbox_env()
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env or \
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] != "qwen3.7-plus"


def test_ambient_anthropic_fallback(tmp_path, monkeypatch):
    for k in ("CLAUDE_LLM_BASE_URL", "CLAUDE_LLM_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ambient.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token")
    a = LocalClaudeAdapter(root=tmp_path)
    a.reset()  # 不应抛认证异常
    env = a._sandbox_env()
    assert env["ANTHROPIC_BASE_URL"] == "https://ambient.example"


def test_missing_auth(tmp_path, monkeypatch):
    for k in ("CLAUDE_LLM_BASE_URL", "CLAUDE_LLM_KEY",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(AgentUnavailable):
        LocalClaudeAdapter(root=tmp_path).reset()


def test_dump_memory_merges_auto_memory_and_project_claude_md(adapter):
    # auto-memory：projects/<slug>/memory/ 下主题文件 + MEMORY.md 索引（内容重复需去重）
    mem = adapter.config_dir / "projects" / "C--x-workspace" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text(
        "# Memory Index\n\n- [Editor](editor-preference.md) — 主力编辑器 vim\n",
        encoding="utf-8")
    (mem / "editor-preference.md").write_text(
        "# Editor preference\n\n主力编辑器 vim，备份编辑器 nano\n", encoding="utf-8")
    # 项目记忆：工作区 CLAUDE.md（与全局 auto-memory 写同一行，验证精确去重）
    adapter.workspace.mkdir(parents=True, exist_ok=True)
    (adapter.workspace / "CLAUDE.md").write_text(
        "# 项目\n\n- 构建产物在 ~/out/build\n- 主力编辑器 vim，备份编辑器 nano\n",
        encoding="utf-8")
    snap = adapter.dump_memory()
    contents = [e.content for e in snap.entries]
    assert any("vim" in c for c in contents)
    assert any("out/build" in c for c in contents)
    assert contents.count("主力编辑器 vim，备份编辑器 nano") == 1  # 两处同句只留一条
    assert all(e.source_turn.endswith(".md") for e in snap.entries)


def test_clock_shift_unsupported(adapter):
    with pytest.raises(AdapterError):
        adapter.clock_shift(3)
    adapter.clock_shift(0)  # 0 天 no-op
