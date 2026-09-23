"""在 openKylin 虚拟机里远程执行命令（SSH 密码认证）。

用法：
    VM_PASS=xxx uv run python scripts/vm_ssh.py "命令"
    VM_PASS=xxx uv run python scripts/vm_ssh.py "echo xxx | sudo -S apt install -y xxx"

背景：openKylin 3.0 桌面版缺 open-vm-tools-desktop，vmrun 的 guest 文件操作不可用
（认证通过但一律报"文件不存在"），故走 SSH 通道。密码只从环境变量读，不进 git。
IP 来自 VMware NAT DHCP 租约（vmnetdhcp.leases，hostname okim-pc）。
"""

from __future__ import annotations

import os
import sys

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.61.133"
USER = "okim"


def run(cmd: str, timeout: int = 900) -> int:
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, username=USER, password=os.environ["VM_PASS"], timeout=15)
    try:
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
    finally:
        cli.close()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print("[stderr]")
        print(err, end="" if err.endswith("\n") else "\n")
    print(f"[exit {rc}]")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2 or "VM_PASS" not in os.environ:
        sys.exit("用法: VM_PASS=xxx uv run python scripts/vm_ssh.py \"命令\"")
    sys.exit(run(sys.argv[1]))
