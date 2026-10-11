"""SSH 远程执行通道：适配器与被测智能体之间的传输层。

被测智能体跑在 openKylin 虚拟机里（环境见 environment.md），评测器在宿主机。
send = SSH 执行一条命令；凭据从环境变量读（VM_HOST/VM_USER/VM_PASS），不入 git。
消息体走 base64 传递，避免 shell 引号/注入问题。
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone

import paramiko


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class SshChannel:
    """单连接复用的远程命令执行通道。"""

    def __init__(self, host: str | None = None, user: str | None = None,
                 password: str | None = None, port: int = 22):
        self.host = host or _env("VM_HOST", "192.168.61.133")
        self.user = user or _env("VM_USER", "okim")
        self.password = password or _env("VM_PASS")
        if not self.password:
            raise ValueError("缺 VM_PASS（openKylin 虚拟机 SSH 密码）")
        self.port = port
        self._cli: paramiko.SSHClient | None = None

    def _client(self) -> paramiko.SSHClient:
        if self._cli is None:
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cli.connect(self.host, port=self.port, username=self.user,
                        password=self.password, timeout=15)
            self._cli = cli
        return self._cli

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        """执行命令，返回 (exit_code, stdout, stderr)。"""
        cli = self._client()
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err

    def run_json(self, cmd: str, timeout: int = 300) -> list | dict:
        rc, out, err = self.run(cmd, timeout)
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def elapsed_ms(start: float) -> int:
    return int((time.time() - start) * 1000)
