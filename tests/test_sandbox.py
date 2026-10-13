"""B8 OS 级沙箱：bwrap 包装生成 / 开关语义 / 适配器 send 接线。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

import memhall.adapters.sandbox as sb
from memhall.adapters.hermes import HermesAdapter
from memhall.adapters.openclaw import OpenClawAdapter

# ---------- 开关语义 ----------

def test_sandbox_off_by_default(monkeypatch):
    monkeypatch.delenv("MEMHALL_SANDBOX", raising=False)
    assert sb.sandbox_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "on", "bwrap", "BWRAP"])
def test_sandbox_on_values(monkeypatch, val):
    monkeypatch.setenv("MEMHALL_SANDBOX", val)
    assert sb.sandbox_enabled() is True


@pytest.mark.parametrize("val", ["", "0", "off", "no"])
def test_sandbox_off_values(monkeypatch, val):
    monkeypatch.setenv("MEMHALL_SANDBOX", val)
    assert sb.sandbox_enabled() is False


# ---------- 包装生成 ----------

def test_bwrap_wrap_shape():
    wrapped = sb.bwrap_wrap("echo hi", ["~/.hermes"])
    assert wrapped.startswith("mkdir -p $HOME/.hermes && bwrap ")
    assert "--ro-bind / /" in wrapped
    assert "--dev /dev" in wrapped and "--proc /proc" in wrapped
    # tmpfs 临时位：跨 case 不残留的缓存面
    for p in ("/tmp", "$HOME/.cache", "$HOME/.npm"):
        assert f"--tmpfs {p}" in wrapped
    # 可写白名单 ~ → $HOME 展开，双址 bind
    assert "--bind $HOME/.hermes $HOME/.hermes" in wrapped
    assert wrapped.endswith("-- sh -c 'echo hi'")


def test_bwrap_wrap_quotes_script():
    from shlex import quote

    script = "echo 'a b' | grep c"
    wrapped = sb.bwrap_wrap(script, ["~/dev"])
    # 原脚本必须整体作为一个参数传给 sh -c（shlex.quote 契约），不被外层劈碎
    assert wrapped.endswith("-- sh -c " + quote(script))


def test_maybe_wrap_passthrough_when_off(monkeypatch):
    monkeypatch.delenv("MEMHALL_SANDBOX", raising=False)
    assert sb.maybe_wrap("raw cmd", ["~/.hermes"]) == "raw cmd"


# ---------- 适配器 send 接线（FakeChannel，不碰真机） ----------

class _Chan:
    """记录 run 命令的假通道（沙箱只改命令串，不改传输语义）。"""

    def __init__(self, out="好的，我记住了。"):
        self.calls: list[str] = []
        self._out = out

    def run(self, cmd, timeout=300, stdin_data=None):
        self.calls.append(cmd)
        return 0, self._out, ""

    def sudo(self, cmd, timeout=120):
        return 0, "", ""


def test_hermes_send_wrapped_when_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-test")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen-test")
    monkeypatch.setenv("MEMHALL_SANDBOX", "1")
    ch = _Chan()
    HermesAdapter(channel=ch).send("s-01", "记一下")
    cmd = ch.calls[0]
    assert cmd.startswith("mkdir -p ")
    assert "bwrap --ro-bind / /" in cmd
    # EVAL_WORKDIRS（vm_common）+ 自家 dotdir 都在白名单
    assert "--bind $HOME/dev $HOME/dev" in cmd
    assert "--bind $HOME/.hermes $HOME/.hermes" in cmd
    # 原始 send 脚本语义不变：stdin 传凭据/消息、oneshot CLI 仍内嵌
    assert "hermes chat --query-file" in cmd


def test_hermes_send_untouched_when_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-test")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen-test")
    monkeypatch.delenv("MEMHALL_SANDBOX", raising=False)
    ch = _Chan()
    HermesAdapter(channel=ch).send("s-01", "记一下")
    assert ch.calls[0].startswith("d=$(mktemp")  # 未包装：冻结口径零改动


def test_openclaw_send_wrapped_when_enabled(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("MEMHALL_SANDBOX", "1")

    class _JsonChan(_Chan):
        def __init__(self):
            super().__init__(out="")
            self.stdins = []

        def run(self, cmd, timeout=300, stdin_data=None):
            self.calls.append(cmd)
            self.stdins.append(stdin_data)
            return (0, json.dumps({"ok": True, "final": "记好了"}), "")

    ch = _JsonChan()
    OpenClawAdapter(channel=ch).send("s-01", "记一下")
    assert "bwrap --ro-bind / /" in ch.calls[0]
    assert "--bind $HOME/.memhall-openclaw $HOME/.memhall-openclaw" in ch.calls[0]
    assert "agent exec" in ch.calls[0]
