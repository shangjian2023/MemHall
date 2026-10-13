"""kylin-agent 适配器回归网（fake-channel 行为锚点，VM 联调由 D 按 TODO-1~6 兑现）。

适配器继承 HermesAdapter，本网钉子类差异面：
- 路径族全部指向 ~/.kylin-agent-runtime（memories / logs / 会话与召回库）
- send 调用行是 -z one-shot + --accept-hooks + deepseek 车道 + 路径引号化
- 二进制惰性解析：env 覆写 > SSH 探测命中 > 裸名兜底；--version 首行须是
  后端自报格式（KylinAgent v…），GUI 前端/异物不冒充
- fork 失败契约：非零 rc 的 stdout（首跑向导/参数校验错误）不是答案，
  一律 AgentUnavailable（invalid_run），不冒充正常答题计分
- reset/verify_reset 盖住 fork 的跨会话召回面（memory.db / state.db / sessions），
  且绝不触碰 ~/.hermes
- 继承面不回退：`; true` 修复（假 invalid_run 事故）在子类同样成立
- discovery 口径：候选只认 kylin-agent-runtime（GUI 前端不算命中），
  env 覆写优先，本机/VM/适配器三处同一份清单
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memhall.adapters.base import SEND_TIMEOUT_S, AgentUnavailable
from memhall.adapters.kylin_agent import HOME, KylinAgentAdapter

_GATEWAY_KEYS = ("GATEWAY_URL", "GATEWAY_VM_URL", "GATEWAY_AGENT_KEY",
                 "AGENT_LLM_KEY", "AGENT_LLM_BASE_URL", "AGENT_LLM_MODEL")

# fork agent.log 行格式实测样本（kylin_agent_runtime_logging._LOG_FORMAT：
# "%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s"）
_FORK_LOG_LINE = ("2026-10-15 09:41:23,517 INFO agent.tool_executor: "
                  "tool memory completed (0.31s, 87 chars)\n")


class _Chan:
    """假通道：记命令与 stdin，按预设应答（探测/清点/抓取/单发四形态）。"""

    def __init__(self, files: dict[str, str] | None = None,
                 probe_out: str = "",
                 send_rc: int = 0, send_out: str = "",
                 version_out: str = "",
                 junk_ls_out: str = "",
                 log_tail: str = ""):
        self.files = files or {}
        self.probe_out = probe_out
        self.send_rc, self.send_out = send_rc, send_out
        self.version_out = version_out
        self.junk_ls_out = junk_ls_out
        self.log_tail = log_tail
        self.cmds: list[str] = []
        self.timeouts: list[int] = []
        self.stdins: list[str | None] = []

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        self.cmds.append(cmd)
        self.timeouts.append(timeout)
        self.stdins.append(stdin_data)
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
        if "--version" in cmd:
            return (0, self.version_out, "") if self.version_out else (1, "", "not found")
        if cmd.startswith("ls -A"):
            return 0, "", ""
        if cmd.startswith(f"ls {HOME}"):
            return 0, self.junk_ls_out, ""
        if cmd.startswith("wc -c"):
            return 0, "0", ""
        if cmd.startswith("tail -c"):
            return 0, self.log_tail, ""
        if cmd.startswith('d=$(mktemp'):    # send 主体脚本
            return self.send_rc, self.send_out, ""
        return 0, "", ""


def _adapter(**kw) -> KylinAgentAdapter:
    return KylinAgentAdapter(channel=_Chan(**kw))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """身份/凭据环境清零：宿主 shell 可能 source 过 .env，不清洗会串车道。"""
    for k in _GATEWAY_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("MEMHALL_KYLIN_AGENT_BIN", raising=False)


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


def test_bin_probe_skips_noise_lines(monkeypatch):
    """探测输出混入杂行（rc 行/告警）只认 / 或 ~ 开头的路径行。"""
    monkeypatch.delenv("MEMHALL_KYLIN_AGENT_BIN", raising=False)
    a = _adapter(probe_out="warning: something\n/opt/k/bin/kylin-agent-runtime\n")
    assert a.bin == "/opt/k/bin/kylin-agent-runtime"


def test_bin_probe_script_quotes_path_tests():
    """探测脚本自身按 shell 规则引用候选（空格路径不再劈碎 [ -x ] 判断）。"""
    from memhall.adapters.kylin_agent import _BIN_PROBE
    assert '[ -x "$p" ] && echo "$p"' in _BIN_PROBE
    assert "/usr/bin/kylin-agent-runtime" in _BIN_PROBE
    assert "$HOME/.local/bin/kylin-agent-runtime" in _BIN_PROBE


def test_send_uses_oneshot_flag_and_deepseek_lane(monkeypatch):
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/usr/bin/kylin-agent-runtime")
    a = _adapter()
    line = a._agent_cmd()
    assert f"timeout {SEND_TIMEOUT_S} /usr/bin/kylin-agent-runtime -z" in line
    assert '--provider deepseek --model "$DEEPSEEK_MODEL"' in line
    assert "--accept-hooks" in line
    assert "chat --query-file" not in line    # 不复用 hermes 的 chat 形态


def test_send_quotes_bin_path_with_spaces(monkeypatch):
    """env 覆写指向带空格的部署路径：shlex 引号化，命令行不劈碎。"""
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/opt/kylin agent/bin/kylin-agent-runtime")
    a = _adapter()
    line = a._agent_cmd()
    assert "'/opt/kylin agent/bin/kylin-agent-runtime'" in line
    assert line.startswith(f"timeout {SEND_TIMEOUT_S} ")


def test_send_zero_rc_returns_reply_text():
    a = _adapter(send_rc=0, send_out="我的代号是青鸟")
    reply = a.send("s1", "我的代号是什么？")
    assert reply.text == "我的代号是青鸟"
    assert reply.session_id == "s1"


def test_send_stdin_carries_pinned_home_and_message():
    """凭据/消息走 stdin（命令行零明文）；env 块钉 HERMES_HOME 指自家目录。"""
    a = _adapter(send_rc=0, send_out="ok")
    a.send("s1", "记住代号青鸟")
    stdin = a.ch.stdins[-1]
    assert stdin is not None
    env_b64, _, msg_b64 = stdin.partition("\n")
    env = base64.b64decode(env_b64).decode()
    msg = base64.b64decode(msg_b64).decode()
    assert 'export HERMES_HOME="$HOME/.kylin-agent-runtime"' in env
    assert ".hermes" not in env.replace("memhall-hermes", "")
    assert msg == "记住代号青鸟"
    assert any(c.startswith('d=$(mktemp') for c in a.ch.cmds)


def test_send_nonzero_rc_stdout_is_not_an_answer():
    """fork 失败契约：首跑向导把引导打到 stdout 且 exit!=0 —— 必须落
    AgentUnavailable（invalid_run），提示文本绝不冒充答案计分。"""
    guidance = ("⟐ KylinAgent Setup — Non-interactive mode\n"
                "The interactive wizard cannot be used here.\n"
                "  kylin-agent-runtime config set model.provider custom\n")
    a = _adapter(send_rc=1, send_out=guidance)
    with pytest.raises(AgentUnavailable, match="rc=1"):
        a.send("s1", "hi")


def test_send_timeout_partial_output_not_scored():
    a = _adapter(send_rc=124, send_out="残句半行")
    with pytest.raises(AgentUnavailable, match="超时"):
        a.send("s1", "hi")


def test_send_channel_restored_after_failure():
    """侦察通道只活在 send 期间：失败后 self.ch 还原，后续 dump/reset 不受影响。"""
    a = _adapter(send_rc=1, send_out="wizard text")
    inner = a.ch
    with pytest.raises(AgentUnavailable):
        a.send("s1", "hi")
    assert a.ch is inner
    a.dump_memory()   # 原通道继续可用


def test_version_info_accepts_backend_format():
    a = _adapter(version_out="KylinAgent v1.0.1 (2026-09-30)\nProject: /opt/kar\n")
    assert a.version_info() == "KylinAgent v1.0.1 (2026-09-30)"
    # 版本探测有时间上限（vm_version_line timeout=30）
    i = next(i for i, c in enumerate(a.ch.cmds) if "--version" in c)
    assert a.ch.timeouts[i] == 30


def test_version_info_rejects_alien_binaries():
    """GUI 前端/异物二进制的 --version 输出不冒充后端版本 → None（宁缺毋假）。"""
    for alien in ("kylin-agent 2.4.1 (Qt 5.15)", "Hermes v0.21.4", ""):
        a = _adapter(version_out=alien)
        assert a.version_info() is None, alien


def test_reset_script_targets_kylin_dirs(monkeypatch):
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/usr/bin/kylin-agent-runtime")
    a = _adapter()
    a.reset()
    reset_cmd = a.ch.cmds[0]
    assert f"rm -f {HOME}/memories/MEMORY.md" in reset_cmd
    assert f"{HOME}/memories/*.lock" in reset_cmd
    junk_cmd = next(c for c in a.ch.cmds if c.startswith("rm -rf"))
    assert f"{HOME}/sessions" in junk_cmd
    assert f"{HOME}/state.db*" in junk_cmd
    assert f"{HOME}/memory.db*" in junk_cmd
    assert a.ch.cmds[-1].startswith(f"wc -c < {HOME}/logs/agent.log")


def test_reset_never_touches_hermes_home(monkeypatch):
    """kylin-agent 的 reset/剪枝只动自家目录，绝不误清 ~/.hermes。"""
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/usr/bin/kylin-agent-runtime")
    a = _adapter()
    a.reset()
    a.verify_reset()
    for c in a.ch.cmds:
        assert "~/.hermes" not in c, c
    assert ".kylin-agent-runtime" in a.fs_prune


def test_verify_reset_flags_recall_db_leftover():
    """memory.db/state.db 残留 = 上一个 case 的对话仍可召回，必须报错。"""
    a = _adapter(junk_ls_out=f"{HOME}/memory.db\n")
    with pytest.raises(Exception, match="召回库仍有残留"):
        a.verify_reset()
    b = _adapter(junk_ls_out="")    # 全净（含空 sessions/ 目录）→ 通过
    b.verify_reset()


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


def test_dump_actions_parses_fork_log_format():
    """fork agent.log 的 tool_executor 行（asctime 逗号毫秒 + logger 名前缀）
    能被继承解析器还原成 Action（工具名/状态/行内时间戳）。"""
    a = _adapter(log_tail=_FORK_LOG_LINE)
    a.reset()                          # 记 _log_offset
    dump = a.dump_actions()
    assert dump.coverage == "partial"
    assert len(dump.actions) == 1
    act = dump.actions[0]
    assert act.tool == "memory"
    assert act.result == "completed"
    assert act.ts is not None and act.ts.year == 2026


def test_registry_entry():
    """注册表条目核对即可——真实例化走 default_channel 需要 VM 环境，
    与 hermes 车道同语义（发车前的环境门槛是特性不是缺陷）。"""
    from memhall.adapters import ADAPTERS, adapter_names
    assert ADAPTERS["kylin-agent"] == "memhall.adapters.kylin_agent:KylinAgentAdapter"
    assert "kylin-agent" in adapter_names()


# ---- discovery 口径：GUI 前端 ≠ 后端，三处清单同源 ----

def test_discovery_candidates_never_include_gui_frontend():
    """GUI 程序 kylin-agent（apt 同名包）不是被测后端：候选里不得出现
    裸 "kylin-agent"，本机扫描与适配器探测（_BIN_PROBE）口径一致。"""
    from memhall.adapters.kylin_agent import _BIN_PROBE
    from memhall.discovery import ADAPTER_CLI, LOCAL_AGENTS, VM_AGENTS
    for cand in ADAPTER_CLI["kylin-agent"]:
        assert cand == "kylin-agent-runtime" or cand.endswith("/kylin-agent-runtime")
    assert "command -v kylin-agent-runtime" in _BIN_PROBE
    kylin_local = next(e for e in LOCAL_AGENTS if e[3] == "kylin-agent")
    assert kylin_local[1] == ADAPTER_CLI["kylin-agent"]
    vm_paths = next(p for n, p, a in VM_AGENTS if a == "kylin-agent")
    assert vm_paths == [p for p in ADAPTER_CLI["kylin-agent"] if p.startswith(("/", "~"))]


def test_discovery_env_override_precedes_candidates(monkeypatch):
    monkeypatch.setenv("MEMHALL_KYLIN_AGENT_BIN", "/opt/custom/kylin-agent-runtime")
    from memhall.discovery import _kylin_agent_candidates
    cands = _kylin_agent_candidates()
    assert cands[0] == "/opt/custom/kylin-agent-runtime"
    assert "kylin-agent-runtime" in cands          # PATH 名仍在，回落可用


def test_vm_probe_script_lists_all_kylin_candidates():
    """VM 侧打包探测与 VM_AGENTS 同源：每个部署落点都进 spec 清单。"""
    from memhall.discovery import _VM_PROBE, VM_AGENTS
    for path in next(p for n, p, _ in VM_AGENTS if n == "kylin-agent-runtime"):
        assert f"kylin-agent-runtime|{path}" in _VM_PROBE, path


def test_adapter_status_lists_kylin_agent_lane(monkeypatch):
    """跑页下拉：remote 形态 VM 实测命中即列 kylin-agent 车道（第四家，
    不是占位）；native 形态本机检出 + 回环凭据齐才可跑。"""
    import memhall.discovery as disc
    from memhall.discovery import Finding, adapter_availability
    for k in ("GATEWAY_URL", "GATEWAY_VM_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(disc, "vm_is_self", lambda: False)
    vm = [Finding("kylin-agent-runtime", "vm", True, "KylinAgent v1.0.1",
                  "/usr/bin/kylin-agent-runtime", adapter="kylin-agent")]
    st = adapter_availability(vm_probe=lambda: vm)
    assert st["adapters"]["kylin-agent"] == {"label": "kylin-agent（VM 连接）", "ok": True}
    # native：本机检出二进制 + 回环 SSH 凭据
    monkeypatch.setattr(disc, "vm_is_self", lambda: True)
    monkeypatch.setattr(disc, "find_cli",
                        lambda *c: "/usr/bin/kylin-agent-runtime"
                        if "kylin-agent-runtime" in c else "")
    monkeypatch.setenv("VM_HOST", "127.0.0.1")
    monkeypatch.setenv("VM_PASS", "x")
    st2 = adapter_availability(vm_probe=lambda: [])
    assert st2["adapters"]["kylin-agent"]["ok"] is True
