"""P1 凭据卫生（docs/engineering-tasks.md T10/T11）回归测试。"""

from __future__ import annotations

import base64
import shlex

import memhall.adapters.remote as rm
from memhall.adapters.hermes import HermesAdapter


class FakeChannel:
    """记录 run/sudo 调用（命令行、stdin）的假 SSH 通道。"""

    def __init__(self, run_ret=None):
        self.calls: list[tuple[str, str, str | None]] = []
        self._run_ret = run_ret or {}

    def run(self, cmd, timeout=300, stdin_data=None):
        self.calls.append(("run", cmd, stdin_data))
        return self._run_ret.get(cmd, (0, "", ""))

    def sudo(self, cmd, timeout=120):
        self.calls.append(("sudo", cmd, None))
        return self._run_ret.get(("sudo", cmd), (0, "", ""))


# ---------- T10：凭据不落 shell 命令行 ----------

def test_hermes_send_credentials_via_stdin_only(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-secret'with\"quotes")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen-test")
    fake = FakeChannel(run_ret={(0, "", ""): (0, "好的，我记住了。", "")})
    a = HermesAdapter(channel=fake)
    a.send("s-01", "记一下：我的目录是 ~/dev/src'; rm -rf /")

    cmd = next(c for k, c, _ in fake.calls if k == "run")
    assert "sk-secret" not in cmd          # key 不在命令行
    assert "rm -rf" not in cmd             # 消息体不在命令行
    assert "DEEPSEEK_API_KEY" not in cmd   # 凭据变量名也不拼进命令行
    assert "DEEPSEEK_MODEL=" not in cmd    # 变量赋值不进命令行（值走 env 文件）

    stdin = next(s for k, _, s in fake.calls if k == "run")
    env_line, msg_line = stdin.split("\n")
    env = base64.b64decode(env_line).decode("utf-8")
    # export + 单引号包裹（source 后必须导出给 hermes 子进程；shlex 验证转义可还原）
    pair = next(t for t in shlex.split(env) if t.startswith("DEEPSEEK_API_KEY="))
    assert pair == 'DEEPSEEK_API_KEY=sk-secret\'with"quotes'
    assert base64.b64decode(msg_line).decode("utf-8").startswith("记一下")


def test_clock_shift_sudo_not_on_cmdline(monkeypatch):
    fake = FakeChannel()
    monkeypatch.setattr(fake, "run", lambda cmd, timeout=300, stdin_data=None:
                        (0, "1700000000\n", ""))
    sudo_cmds = []
    monkeypatch.setattr(fake, "sudo",
                        lambda cmd, timeout=120: (sudo_cmds.append(cmd), (0, "", ""))[1])
    a = HermesAdapter(channel=fake)
    monkeypatch.setattr(a.ch, "password", "p@ss'w0rd", raising=False)
    a.clock_shift(3)
    joined = " ".join(sudo_cmds)
    assert "date -s" in joined
    assert "p@ss" not in joined            # sudo 密码绝不进命令行
    assert "-S" not in joined              # 由 SshChannel.sudo 统一拼装


def test_remote_sudo_password_via_stdin(tmp_path, monkeypatch):
    seen: dict = {}

    class Stdin:
        def write(self, d): seen.setdefault("stdin", []).append(d)
        def flush(self): pass
        def close(self): pass

    class Chan:
        def exit_status_ready(self): return True
        def recv_exit_status(self): return 0

    class Out:
        channel = Chan()
        def read(self): return b"done"

    class Keys:
        def load(self, p): pass
        def save(self, p): pass

    class Cli:
        def get_host_keys(self): return Keys()
        def set_missing_host_key_policy(self, p): pass
        def connect(self, **kw): pass
        def close(self): pass
        def get_transport(self):
            class T:  # keepalive 假传输层：活跃、接受心跳设置
                def is_active(self): return True
                def set_keepalive(self, s): pass
            return T()
        def exec_command(self, cmd, timeout=None):
            seen["cmd"] = cmd
            return Stdin(), Out(), Out()

    monkeypatch.setattr(rm, "KNOWN_HOSTS", tmp_path / "nonexistent-kh")
    monkeypatch.setattr(rm.paramiko, "SSHClient", Cli)
    ch = rm.SshChannel(host="h", user="u", password="p@ss'w0rd")
    rc, out, _ = ch.sudo("date -s @123")
    assert rc == 0
    assert seen["cmd"] == "sudo -S -p '' -- date -s @123"
    assert "p@ss" not in seen["cmd"]
    assert "".join(seen["stdin"]) == "p@ss'w0rd\n"  # 密码只走 stdin


# ---------- T11：SSH 主机密钥 TOFU 钉扎 ----------

def test_ssh_tofu_first_connect_records_then_rejects(tmp_path, monkeypatch):
    kh = tmp_path / "known_hosts"
    monkeypatch.setattr(rm, "KNOWN_HOSTS", kh)
    policies: list[str] = []

    class Keys:
        def load(self, p): pass
        def save(self, p): kh.write_text("hostkey", encoding="utf-8")

    class Cli:
        def __init__(self): self._keys = Keys()
        def get_host_keys(self): return self._keys
        def set_missing_host_key_policy(self, p): policies.append(type(p).__name__)
        def connect(self, **kw): pass
        def close(self): pass
        def get_transport(self):
            class T:  # keepalive 假传输层：活跃、接受心跳设置
                def is_active(self): return True
                def set_keepalive(self, s): pass
            return T()

    monkeypatch.setattr(rm.paramiko, "SSHClient", Cli)

    rm.SshChannel(host="h", user="u", password="p")._client()   # 首次：TOFU 记录
    assert policies == ["AutoAddPolicy"]
    assert kh.exists()

    policies.clear()
    rm.SshChannel(host="h", user="u", password="p")._client()   # 再次：钉扎校验
    assert policies == ["RejectPolicy"]


def test_ssh_reject_wraps_hostkey_error(tmp_path, monkeypatch):
    """已知主机指纹不匹配 → 清晰报错并指向 known_hosts 条目。"""
    kh = tmp_path / "known_hosts"
    kh.write_text("hostkey", encoding="utf-8")
    monkeypatch.setattr(rm, "KNOWN_HOSTS", kh)

    class Keys:
        def load(self, p): pass
        def save(self, p): pass

    class Cli:
        def get_host_keys(self): return Keys()
        def set_missing_host_key_policy(self, p): pass
        def connect(self, **kw): raise rm.paramiko.SSHException("Host key changed")

    monkeypatch.setattr(rm.paramiko, "SSHClient", Cli)
    ch = rm.SshChannel(host="h", user="u", password="p")
    try:
        ch._client()
        raise AssertionError("应抛 ConnectionError")
    except ConnectionError as e:
        assert "known_hosts" in str(e)
