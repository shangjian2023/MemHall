"""OpenClaw 适配器（VM SSH 车道）：沙箱配置注入 / exec 信封解析 / 记忆导出。"""

from __future__ import annotations

import json

import pytest

from memhall.adapters.base import AgentUnavailable


class _FakeChannel:
    """脚本化 SSH 通道：按序返回 (rc, out, err)，记录 stdin。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []
        self.stdins: list[str | None] = []

    def run(self, cmd, timeout=300, stdin_data=None):
        self.calls.append(cmd)
        self.stdins.append(stdin_data)
        return self.script.pop(0)

    def run_json(self, cmd, timeout=300):
        self.calls.append(cmd)
        self.stdins.append(None)
        rc, out, err = self.script.pop(0)
        if rc != 0:
            raise RuntimeError(err or out)
        return json.loads(out)

    def sudo(self, cmd, timeout=120):
        self.calls.append(cmd)
        return self.script.pop(0)


def test_reset_writes_sandbox_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    from memhall.adapters.openclaw import OpenClawAdapter
    ch = _FakeChannel([(0, "", ""), (0, "", ""), (0, "", "")])
    a = OpenClawAdapter(channel=ch)
    a.reset()
    cfg_cmd, cat_cmd = ch.calls[0], ch.calls[1]
    # rm 目标钉死为沙箱目录本身（绝不可能是真实 ~/.openclaw 等其它路径）
    assert cfg_cmd.startswith(
        "rm -rf ~/.memhall-openclaw && mkdir -p ~/.memhall-openclaw/workspace")
    assert "chmod 600" in cat_cmd                           # 含 key 的配置 600
    cfg = json.loads(ch.stdins[1])
    prov = cfg["models"]["providers"]["memhall-gw"]
    assert prov["baseUrl"] == "http://192.168.61.1:8311/v1"
    assert prov["apiKey"] == "memhall-openclaw"             # 记账归因 dummy
    assert prov["api"] == "openai-completions"
    assert cfg["models"]["mode"] == "replace"               # 隔绝环境配置漂移
    assert cfg["agents"]["defaults"]["model"] == "memhall-gw/qwen3.7-plus"
    assert a._provider_model == "memhall-gw/qwen3.7-plus"


def test_reset_direct_mode_and_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("GATEWAY_VM_URL", raising=False)
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-their-own")
    monkeypatch.setenv("AGENT_LLM_MODEL", "their-model")
    from memhall.adapters.openclaw import OpenClawAdapter
    a = OpenClawAdapter(channel=_FakeChannel([(0, "", ""), (0, "", ""), (0, "", "")]))
    a.reset()  # 第三方用户自带 key 直连（可移植路径）
    from memhall.adapters.openclaw import OpenClawAdapter as OC
    b = OC(channel=_FakeChannel([]))
    monkeypatch.delenv("AGENT_LLM_BASE_URL")
    with pytest.raises(RuntimeError, match="AGENT_LLM"):
        b._llm_settings()


def test_send_parses_exec_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    from memhall.adapters.openclaw import OpenClawAdapter
    ok_env = json.dumps({"ok": True, "final": "  在 ~/notes/memhall.md\n",
                         "model": "qwen3.7-plus", "provider": "memhall-gw"})
    ch = _FakeChannel([(0, "", ""), (0, "", ""), (0, "", ""),  # reset
                       (0, ok_env, "")])
    a = OpenClawAdapter(channel=ch)
    a.reset()
    reply = a.send("s-01", "你记的我的笔记在哪？")
    assert reply.text == "在 ~/notes/memhall.md"
    send_cmd = ch.calls[-1]
    assert "--message-file" in send_cmd and "agent exec" in send_cmd
    assert "--config ~/.memhall-openclaw/openclaw.json" in send_cmd
    # agent exec otherwise pins workspace to the launch directory, ignoring
    # the workspace in config (installed OpenClaw 2026.9.8 regression).
    assert "--cwd ~/.memhall-openclaw/workspace" in send_cmd
    assert "笔记在哪" not in send_cmd                     # 消息走 stdin 不上命令行
    assert ch.stdins[-1] == "你记的我的笔记在哪？"

    # ok=false → AgentUnavailable（整 case 运行无效，不静默降级）
    ch2 = _FakeChannel([(0, "", ""), (0, "", ""), (0, "", ""),
                        (1, json.dumps({"ok": False, "final": "",
                                        "error": {"message": "boom"}}), "")])
    b = OpenClawAdapter(channel=ch2)
    b.reset()
    with pytest.raises(AgentUnavailable, match="boom"):
        b.send("s-01", "x")

    # 非 JSON 输出（openclaw 崩溃/超时）→ AgentUnavailable
    ch3 = _FakeChannel([(0, "", ""), (0, "", ""), (0, "", ""),
                        (124, "some crash noise", "")])
    c = OpenClawAdapter(channel=ch3)
    c.reset()
    with pytest.raises(AgentUnavailable, match="非 JSON"):
        c.send("s-01", "x")


def test_dump_memory_from_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    from memhall.adapters.openclaw import OpenClawAdapter
    rows = [["MEMORY.md", "memory", 1, "# Memory\n"],
            ["MEMORY.md", "memory", 3, "- 常用笔记位置：~/notes/memhall.md\n"],
            ["sessions/main/x.jsonl", "sessions", 10, "早期会话片段"]]
    ch = _FakeChannel([(0, json.dumps(rows), "")])
    a = OpenClawAdapter(channel=ch)
    snap = a.dump_memory()
    assert snap.format == "sqlite"
    assert any("notes/memhall.md" in e.content for e in snap.entries)
    assert "memory_index_chunks" in ch.calls[0]           # 证据=被检索面
