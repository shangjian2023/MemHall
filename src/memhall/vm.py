"""VMware lifecycle management for the openKylin evaluation image."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from memhall.adapters.base import NO_WINDOW


class VmError(RuntimeError):
    """Raised when a vmrun lifecycle operation fails."""


def _find_vmrun() -> Path:
    configured = os.environ.get("VMRUN")
    candidates = [
        configured,
        shutil.which("vmrun"),
        r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe",
        r"C:\Program Files\VMware\VMware Workstation\vmrun.exe",
        "/usr/bin/vmrun",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise VmError("未找到 vmrun；请安装 VMware Workstation 或设置 VMRUN")


def _snapshot_name(value: str) -> str:
    if not value or not re.fullmatch(r"[\w .()\-\u4e00-\u9fff]+", value):
        raise VmError(f"非法快照名: {value!r}")
    return value


@dataclass
class VmStatus:
    vmx_path: str
    snapshot: str
    running: bool
    snapshots: list[str]
    ssh_reachable: bool


class VmwareManager:
    """Small vmrun wrapper with explicit post-operation health checks."""

    def __init__(self, vmx_path: Path, snapshot: str = "agents-warm",
                 vmrun: Path | None = None):
        self.vmx_path = vmx_path.expanduser().resolve()
        if not self.vmx_path.is_file():
            raise VmError(f"VMX 不存在: {self.vmx_path}")
        self.snapshot_name = _snapshot_name(snapshot)
        self.vmrun = vmrun.resolve() if vmrun else _find_vmrun()

    @classmethod
    def from_env(cls) -> VmwareManager:
        value = os.environ.get("VMX_PATH", "")
        if not value:
            raise VmError("缺少 VMX_PATH（openKylin 虚拟机 .vmx 路径）")
        return cls(Path(value), os.environ.get("VM_SNAPSHOT", "agents-warm"))

    def _run(self, *args: str, timeout: int = 300,
             check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [str(self.vmrun), "-T", "ws", *map(str, args)]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise VmError(
                f"vmrun {args[0] if args else ''} 失败"
                f"({completed.returncode}): {detail[:600]}")
        return completed

    @staticmethod
    def _same_path(left: str, right: Path) -> bool:
        try:
            return Path(left).resolve() == right.resolve()
        except OSError:
            return os.path.normcase(left) == os.path.normcase(str(right))

    def list_running(self) -> list[str]:
        output = self._run("list").stdout.splitlines()
        return [line.strip() for line in output[1:] if line.strip()]

    def is_running(self) -> bool:
        return any(self._same_path(item, self.vmx_path)
                   for item in self.list_running())

    def snapshots(self) -> list[str]:
        output = self._run(
            "listSnapshots", str(self.vmx_path)).stdout.splitlines()
        return [line.strip() for line in output[1:] if line.strip()]

    def tcp_reachable(self) -> bool:
        host = os.environ.get("VM_HOST", "192.168.61.133")
        port = int(os.environ.get("VM_PORT", "22"))
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    def status(self) -> VmStatus:
        return VmStatus(
            vmx_path=str(self.vmx_path),
            snapshot=self.snapshot_name,
            running=self.is_running(),
            snapshots=self.snapshots(),
            ssh_reachable=self.tcp_reachable(),
        )

    def wait_ssh(self, timeout_s: int = 300) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.tcp_reachable():
                try:
                    from memhall.adapters.remote import SshChannel
                    channel = SshChannel()
                    rc, out, _ = channel.run("printf ready", timeout=15)
                    channel.close()
                    if rc == 0 and out == "ready":
                        return
                except Exception:
                    pass
            time.sleep(5)
        raise VmError(f"虚拟机启动后 {timeout_s}s 内 SSH 未就绪")

    def start(self, *, wait: bool = True, timeout_s: int = 300) -> None:
        if not self.is_running():
            self._run("start", str(self.vmx_path), "nogui", timeout=120)
        if wait:
            self.wait_ssh(timeout_s)

    def stop(self, mode: str = "soft") -> None:
        if mode not in {"soft", "hard"}:
            raise VmError("关机模式必须是 soft 或 hard")
        if self.is_running():
            self._run("stop", str(self.vmx_path), mode, timeout=180)

    def create_snapshot(self, name: str) -> None:
        name = _snapshot_name(name)
        self._run("snapshot", str(self.vmx_path), name, timeout=300)

    def delete_snapshot(self, name: str) -> None:
        name = _snapshot_name(name)
        self._run("deleteSnapshot", str(self.vmx_path), name, timeout=300)

    def revert(self, name: str | None = None, *, start: bool = True,
               timeout_s: int = 300) -> None:
        target = _snapshot_name(name or self.snapshot_name)
        if target not in self.snapshots():
            raise VmError(f"快照不存在: {target}")
        self._run(
            "revertToSnapshot", str(self.vmx_path), target, timeout=300)
        if start:
            self.start(wait=True, timeout_s=timeout_s)

    def prepare(self, timeout_s: int = 300) -> dict[str, Any]:
        """Restore the baseline, boot, wait for SSH, then fingerprint the guest."""
        self.revert(self.snapshot_name, start=True, timeout_s=timeout_s)
        return self.health()

    def health(self) -> dict[str, Any]:
        from memhall.adapters.remote import SshChannel, remote_environment
        channel = SshChannel()
        try:
            environment = remote_environment(channel, "vm-health")
            rc, out, err = channel.run(
                "set -eu; "
                "printf 'disk='; df -Pk / | awk 'NR==2 {print $4}'; "
                "printf 'clock='; date --iso-8601=seconds; "
                "printf 'agents='; "
                "for x in kylin-bot hermes; do command -v $x >/dev/null && "
                "printf '%s,' $x || true; done",
                timeout=30,
            )
            if rc != 0:
                raise VmError(f"VM 健康检查失败: {err.strip()[:300]}")
            facts = {}
            for line in out.splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    facts[key] = value
            return {
                "ok": True,
                "vmx_path": str(self.vmx_path),
                "snapshot": self.snapshot_name,
                "environment": environment,
                "facts": facts,
            }
        finally:
            channel.close()


def status_json(manager: VmwareManager) -> str:
    return json.dumps(asdict(manager.status()), ensure_ascii=False, indent=2)
