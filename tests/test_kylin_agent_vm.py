"""kylin-agent 适配器回归网（未实测先行的行为锚点，VM 联调时逐条兑现）。

适配器继承 HermesAdapter，本网只钉子类差异面：
- 路径族全部指向 ~/.kylin-agent-runtime（memories / logs / 会话残留）
- send 调用行是 -z one-shot + --accept-hooks + deepseek 车道
- 二进制惰性解析：env 覆写 > SSH 探测命中 > 裸名兜底
- 继承面不回退：`; true` 修复（假 invalid_run 事故）在子类同样成立
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memhall.adapters.base import SEND_TIMEOUT_S
from memhall.adapters.kylin_agent import HOME, KylinAgentAdapter


class _Chan:
    """假通道：记命令、按预设应答（探测/清点/抓取三形态）。"""

    def __init__(self, files: dict[str, str] | None = None,
                 probe_out: str = ""):
        self.files = files or {}
        self.probe_out = probe_out
        self.cmds: list[str] = []

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        self.cmds.append(cmd)
        if cmd.startswith("command -v kylin-agent-runtime"):
            return 0, self.probe_out, ""
        if cmd.startswith("for f in"):
            out = ""
            last_rc = 1
            for name in (f"{KylinAgentAdapter.mem_dir}/MEMORY.md",
                         f"{KylinAgentAdapter.mem_dir}/USER.md"):
                if name in self.files:
                    out += f"=== {name}\n{self.files[name]}"
                    last_rc = 0
            rc = 0 if cmd.rstrip().endswith("; true") else last_rc
            return rc, out, ""
        if cmd.startswith("ls -A"):
            return 0, "", ""
        if cmd.startswith("wc -c"):
            return 0, "0", ""
        return 0, "", ""


def _adapter(**kw) -> KylinAgentAdapter:
    return KylinAgentAdapter(channel=_Chan(**kw))  # type: ignore[arg-type]


def test_paths_point_at_kylin_runtime_home():
    a = _adapter()
    assert a.mem_dir == f"{HOME}/memories"
    assert a.agent_log == f"{HOME}/logs/agent.log"
    assert all(p.startswith(HOME) for p in a.session_junk)
    assert ".kylin-agent-runtime" in a.fs_prune
    assert a.name == "kylin-agent"
    assert a.sandbox_writable == [HOME]


def test_bin_env_override_wins(monkeypatch):
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/opt/kar/kylin-agent-runtime")
    a = _adapter()
    assert a.bin == "/opt/kar/kylin-agent-runtime"


def test_bin_probe_fallback_order(monkeypatch):
    monkeypatch.delenv("MEMHALL_KYLIN_AGENT_BIN", raising=False)
    a = _adapter(probe_out="/usr/local/bin/kylin-agent-runtime\n")
    assert a.bin == "/usr/local/bin/kylin-agent-runtime"
    b = _adapter(probe_out="")            # 探测全空 → 裸名兜底，send 报错可见
    assert b.bin == "kylin-agent-runtime"


def test_send_uses_oneshot_flag_and_deepseek_lane(monkeypatch):
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/usr/bin/kylin-agent-runtime")
    a = _adapter()
    line = a._agent_cmd()
    assert f"timeout {SEND_TIMEOUT_S} /usr/bin/kylin-agent-runtime -z" in line
    assert '--provider deepseek --model "$DEEPSEEK_MODEL"' in line
    assert "--accept-hooks" in line
    assert "chat --query-file" not in line    # 不复用 hermes 的 chat 形态


def test_reset_script_targets_kylin_dirs(monkeypatch):
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/usr/bin/kylin-agent-runtime")
    a = _adapter()
    a.reset()
    reset_cmd = a.ch.cmds[0]
    assert f"rm -f {HOME}/memories/MEMORY.md" in reset_cmd
    assert f"{HOME}/memories/*.lock" in reset_cmd
    junk_cmd = next(c for c in a.ch.cmds if c.startswith("rm -rf"))
    assert f"{HOME}/sessions" in junk_cmd and f"{HOME}/checkpoints" in junk_cmd
    assert a.ch.cmds[-1].startswith(f"wc -c < {HOME}/logs/agent.log")


def test_dump_memory_inherits_true_guard():
    """`; true` 修复（2026-10-08 假 invalid_run 事故）在子类不得回退。"""
    a = _adapter(files={})
    snap = a.dump_memory()
    assert a.ch.cmds[0].rstrip().endswith("; true")
    assert snap.dump_ok is True
    assert snap.entries == []


def test_dump_memory_reads_kylin_paths():
    a = _adapter(files={f"{HOME}/memories/MEMORY.md": "- 用户代号：青鸟\n"})
    snap = a.dump_memory()
    assert [e.content for e in snap.entries] == ["用户代号：青鸟"]
    assert snap.entries[0].source_turn == f"{HOME}/memories/MEMORY.md"


def test_registry_entry():
    """注册表条目核对即可——真实例化走 default_channel 需要 VM 环境，
    与 hermes 车道同语义（发车前的环境门槛是特性不是缺陷）。"""
    from memhall.adapters import ADAPTERS, adapter_names
    assert ADAPTERS["kylin-agent"] == "memhall.adapters.kylin_agent:KylinAgentAdapter"
    assert "kylin-agent" in adapter_names()
