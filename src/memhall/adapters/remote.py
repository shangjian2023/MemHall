"""命令执行通道：适配器与被测智能体之间的传输层。

两种实现，接口逐方法对齐（run/sudo/run_json/close）：
- SshChannel：宿主 + 评测机两机形态——被测智能体在远端（openKylin VM，
  环境见 environment.md），评测器在宿主机，SSH 连过去执行。
- LocalChannel：openKylin 原生形态——被测智能体与评测器同机，直接本机
  bash 执行（2026-10 架构收尾：原生跑不依赖 sshd，也不绕 SSH 回环）。

凭据从环境变量读（VM_HOST/VM_USER/VM_PASS），不入 git。消息体与凭据一律
走 stdin / base64，不落 shell 命令行（ps/history 不可见，值含引号也不会
把命令拼碎）。

主机密钥：TOFU（首次记录指纹到 ~/.memhall/known_hosts，此后指纹变化即拒连）。
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import paramiko

KNOWN_HOSTS = Path.home() / ".memhall" / "known_hosts"
log = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class SshChannel:
    """单连接复用的远程命令执行通道。"""

    def __init__(self, host: str | None = None, user: str | None = None,
                 password: str | None = None, port: int = 22):
        self.host = host or _env("VM_HOST")
        self.user = user or _env("VM_USER", "okim")
        self.password = password or _env("VM_PASS")
        if not self.host:
            raise ValueError("缺 VM_HOST（评测虚拟机地址，见 .env.example）")
        if not self.password:
            raise ValueError("缺 VM_PASS（openKylin 虚拟机 SSH 密码）")
        self.port = port
        self._cli: paramiko.SSHClient | None = None

    def _client(self) -> paramiko.SSHClient:
        # 断线重连：传输层不活跃（NAT 流表超时/网络抖动的静默死亡）就重建——
        # 只认 self._cli is None 会把死连接复用到马拉松结束（2026-10-08 r3
        # 实锤：VM 侧会话已消失，本地还挂死在 recv 上 28 分钟零错误）
        if self._cli is not None:
            t = self._cli.get_transport()
            if t is None or not t.is_active():
                log.warning("SSH 传输层已死，重连 %s@%s:%d", self.user, self.host,
                            self.port)
                with contextlib.suppress(Exception):  # 死连接关闭失败无人在意
                    self._cli.close()
                self._cli = None
        if self._cli is None:
            cli = paramiko.SSHClient()
            keys = cli.get_host_keys()
            if KNOWN_HOSTS.is_file():
                keys.load(str(KNOWN_HOSTS))
                # 已知主机：指纹不匹配即拒（防地址被占/劫持后静默放行）
                cli.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                # 首次连接：TOFU——记录指纹供此后校验
                cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                log.info("SSH 连接 %s@%s:%d%s", self.user, self.host, self.port,
                         "（首连，TOFU 记录指纹）" if not KNOWN_HOSTS.is_file() else "")
                cli.connect(hostname=self.host, port=self.port, username=self.user,
                            password=self.password, timeout=15)
            except paramiko.SSHException as e:
                if KNOWN_HOSTS.is_file():
                    raise ConnectionError(
                        f"SSH 主机密钥校验失败: {e}（指纹变化？确认是换机/重装后，"
                        f"删 {KNOWN_HOSTS} 对应条目再连）") from e
                raise
            if not KNOWN_HOSTS.is_file():
                KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
                keys.save(str(KNOWN_HOSTS))
            # 30s 心跳：半开连接 ~90s 内暴露（TCP 自带 keepalive 要 2 小时）
            t = cli.get_transport()
            if t is not None:
                t.set_keepalive(30)
            self._cli = cli
        return self._cli

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        """执行命令，返回 (exit_code, stdout, stderr)。

        stdin_data：经 stdin 传入的数据（凭据/消息体走这里，不进命令行）。
        """
        t0 = time.monotonic()
        cli = self._client()
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = _recv_exit(stdout.channel, time.monotonic() + timeout)
        dt = time.monotonic() - t0
        log.debug("ssh [%.1fs rc=%d] %s", dt, rc, cmd[:160])
        if rc != 0:
            log.warning("ssh 命令失败 rc=%d (%.1fs): %s", rc, dt, err.strip()[:200])
        return rc, out, err

    def sudo(self, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
        """sudo 执行：密码走 stdin（sudo -S），不落命令行（ps/history 不可见）。"""
        full = f"sudo -S -p '' -- {cmd}"
        cli = self._client()
        stdin, stdout, stderr = cli.exec_command(full, timeout=timeout)
        stdin.write(self.password + "\n")
        stdin.flush()
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = _recv_exit(stdout.channel, time.monotonic() + timeout)
        return rc, out, err

    def run_json(self, cmd: str, timeout: int = 300) -> list | dict:
        rc, out, err = self.run(cmd, timeout=timeout)
        if rc != 0:
            raise RuntimeError(f"远程命令失败({rc}): {err.strip()[:500]}")
        return json.loads(out)

    def close(self) -> None:
        if self._cli is not None:
            self._cli.close()
            self._cli = None

    def host_key_sha256(self) -> str:
        """服务端主机密钥指纹（OpenSSH base64 格式，无填充）——
        TOFU 只记不示人，这里把指纹暴露给环境指纹/报告用。"""
        import hashlib

        t = self._client().get_transport()
        if t is None:
            raise paramiko.SSHException("无活动传输层，取不到主机密钥")
        digest = hashlib.sha256(t.get_remote_server_key().asbytes()).digest()
        return base64.b64encode(digest).rstrip(b"=").decode("ascii")


class LocalChannel:
    """本机执行通道（openKylin 原生模式）：智能体与评测器同机时直接落
    本机 bash，不绕 SSH 回环（2026-10 架构收尾，用户设计初衷：win 和
    openKylin 原生各自成立）。命令语义与 SSH 会话一致——POSIX 命令、
    ~ 展开、stdin/stdout/stderr 三通，适配器代码零改动。

    sudo（拨钟等系统操作）仍需 VM_PASS，密码走 stdin（sudo -S）与 SSH
    通道同一条纪律；不出 sudo 时不需要它。
    """

    def __init__(self, password: str | None = None):
        self.bash = shutil.which("bash")
        if not self.bash:
            raise ValueError("本机执行通道需要 bash（openKylin/Linux 环境）")
        self.password = password or _env("VM_PASS")
        self._env = self._build_env()

    @staticmethod
    def _build_env() -> dict:
        # 智能体常装在 ~/.local/bin（npm 布局），服务进程 PATH 未必含它
        # （10-09 VM 实测 which claude 空、node 127）——把登录 shell 才会
        # 补的目录前置进 PATH
        env = os.environ.copy()
        env["PATH"] = (str(Path.home() / ".local" / "bin") + os.pathsep
                       + env.get("PATH", ""))
        env.setdefault("HOME", str(Path.home()))
        return env

    def _exec(self, cmd: str, timeout: int,
              stdin_data: str | None) -> tuple[int, str, str]:
        t0 = time.monotonic()
        try:
            r = subprocess.run([self.bash, "-c", cmd], input=stdin_data,
                               capture_output=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=self._env)
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(
                f"本机命令 {timeout}s 未返回（超时）: {cmd[:120]}") from e
        dt = time.monotonic() - t0
        log.debug("local [%.1fs rc=%d] %s", dt, r.returncode, cmd[:160])
        if r.returncode != 0:
            log.warning("本机命令失败 rc=%d (%.1fs): %s", r.returncode, dt,
                        (r.stderr or "").strip()[:200])
        return r.returncode, r.stdout or "", r.stderr or ""

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        """执行命令，返回 (exit_code, stdout, stderr)；stdin_data 走 stdin。"""
        return self._exec(cmd, timeout, stdin_data)

    def sudo(self, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
        """sudo 执行：密码走 stdin（sudo -S），不落命令行（ps/history 不可见）。"""
        if not self.password:
            raise ValueError("缺 VM_PASS（本机 sudo：拨钟等系统操作需要）")
        return self._exec(f"sudo -S -p '' -- {cmd}", timeout,
                          self.password + "\n")

    def run_json(self, cmd: str, timeout: int = 300) -> list | dict:
        rc, out, err = self.run(cmd, timeout=timeout)
        if rc != 0:
            raise RuntimeError(f"本机命令失败({rc}): {err.strip()[:500]}")
        return json.loads(out)

    def close(self) -> None:
        pass  # 无连接可关

    def host_key_sha256(self) -> str:
        return ""  # 无 SSH 跳——环境指纹如实缺省（仅 vm.py 健康检查用到）


def default_channel():
    """按部署形态选传输通道：VM_HOST 指向本机（openKylin 原生）= 本机
    直跑，否则 SSH 连评测机。三个 VM 车道适配器共用这一个选路口。"""
    from memhall.discovery import vm_is_self
    if vm_is_self():
        log.info("原生模式：本机执行通道（智能体与评测器同机，不绕 SSH 回环）")
        return LocalChannel()
    return SshChannel()


def remote_environment(channel: SshChannel, adapter_name: str,
                       agent_version_command: str | None = None) -> dict:
    """目标机环境指纹（无凭据）：OS/内核/Python/主机名 + 主机密钥指纹。

    探针脚本经 base64 送进 VM 用系统 python3 跑，输出 JSON 回读——
    报告与 manifest 可追溯"这套分数是在什么环境上测的"。
    """
    source = """import json
import platform
from pathlib import Path

release = {}
path = Path('/etc/os-release')
if path.exists():
    for line in path.read_text(errors='replace').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip().strip(chr(34))
print(json.dumps({
    'kind': 'remote',
    'os': release.get('PRETTY_NAME', platform.platform()),
    'os_id': release.get('ID', ''),
    'os_version': release.get('VERSION_ID', ''),
    'kernel': platform.release(),
    'machine': platform.machine(),
    'python': platform.python_version(),
    'hostname': platform.node(),
}, ensure_ascii=False))
"""
    command = f"echo {b64(source)} | base64 -d | python3"
    data = channel.run_json(command, timeout=30)
    if not isinstance(data, dict):
        raise RuntimeError("目标环境指纹格式错误")
    data["adapter"] = adapter_name
    data["agent_version"] = None
    if agent_version_command:
        rc, out, _ = channel.run(agent_version_command, timeout=30)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if rc == 0 and lines:
            data["agent_version"] = lines[0][:200]
    data["ssh_host_key_sha256"] = channel.host_key_sha256()
    data["vm_snapshot"] = _env("VM_SNAPSHOT", "unknown")
    return data


def b64(text: str) -> str:
    """UTF-8 -> base64（消息体跨 SSH 传输的安全编码）。"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _recv_exit(chan: paramiko.Channel, deadline: float) -> int:
    """recv_exit_status() 无超时参数（挂死面：通道半死时永久阻塞）——
    改为轮询 exit_status_ready，超时抛 SSHException 让上层把该命令判失败，
    而不是把整场马拉松卡死在一个 case 上（2026-10-08 r3 实锤）。"""
    while not chan.exit_status_ready():
        if time.monotonic() > deadline:
            raise paramiko.SSHException(
                f"远程命令 {deadline - time.monotonic():.0f}s 内未返回退出码"
                "（SSH 通道挂死）")
        time.sleep(1)
    return chan.recv_exit_status()


def now_utc() -> datetime:
    return datetime.now(UTC)


def elapsed_ms(start: float) -> int:
    return int((time.time() - start) * 1000)
