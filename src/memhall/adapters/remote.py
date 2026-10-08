"""SSH 远程执行通道：适配器与被测智能体之间的传输层。

被测智能体跑在 openKylin 虚拟机里（环境见 environment.md），评测器在宿主机。
send = SSH 执行一条命令；凭据从环境变量读（VM_HOST/VM_USER/VM_PASS），不入 git。
消息体与凭据一律走 stdin / base64，不落 shell 命令行（VM 内 ps/history 不可见，
值含引号也不会把命令拼碎）。

主机密钥：TOFU（首次记录指纹到 ~/.memhall/known_hosts，此后指纹变化即拒连）。
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
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
