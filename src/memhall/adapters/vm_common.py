"""VM 三家适配器（hermes / openclaw / kylinbot）共享的通道侧实现。

拨钟/恢复、版本探测、远端指纹、close 与 EVAL_WORKDIRS 原是三份近
逐字复制（2026-10-13 适配器层收编）；各家行为差异（send/reset 的
CLI 形态、记忆导出）仍留在各自模块。
"""

from __future__ import annotations

from typing import ClassVar

from memhall.adapters.base import AdapterError
from memhall.adapters.remote import LocalChannel, SshChannel

# 用例注入的虚构工作区（评测专用 VM，reset 一并清掉防跨轮污染）
EVAL_WORKDIRS = ["~/dev", "~/work", "~/proj", "~/docs", "~/notes",
                 "~/out", "~/scripts", "~/templates", "~/demo"]


def vm_version_line(ch: SshChannel | LocalChannel, cmd: str) -> str | None:
    """跑版本命令取输出第一行（探测尽力而为，失败返回 None）。"""
    rc, out, _ = ch.run(cmd, timeout=30)
    line = out.strip().splitlines()[0] if out.strip() else ""
    return line or None


class VmAdapterMixin:
    """通道驱动适配器的公共钩子：sudo 拨钟/恢复、远端指纹、close、OS 沙箱。

    宿主适配器提供 self.ch（SshChannel / LocalChannel）。"""

    ch: SshChannel | LocalChannel
    _clock_epoch: int | None = None

    # OS 级沙箱（B8）的可写白名单：agent 自家 dotdir（各适配器覆写）；
    # EVAL_WORKDIRS 由 _sandboxed 自动并入。默认空 = 沙箱开启时无额外可写位。
    sandbox_writable: ClassVar[list[str]] = []

    def _sandboxed(self, script: str) -> str:
        """send 脚本出口：MEMHALL_SANDBOX=1 时包 bwrap（默认原样返回）。"""
        from memhall.adapters.sandbox import maybe_wrap
        return maybe_wrap(script, EVAL_WORKDIRS + self.sandbox_writable)

    def clock_shift(self, days: int) -> None:
        """VM 拨钟（sudo date -s，密码走 stdin），记录原时刻供恢复。

        R33：失败抛 AdapterError（不再 RuntimeError）——orchestrator 只兜
        AdapterError，RuntimeError 会走 failed_cases 把已采证据丢掉。"""
        if days == 0:
            return
        rc, out, _ = self.ch.run("date +%s")
        if rc != 0:
            raise AdapterError("拨钟前读取系统时间失败")
        self._clock_epoch = int(out.strip())
        rc, _, err = self.ch.sudo(f"date -s '+{days} days' >/dev/null 2>&1 && echo ok")
        if rc != 0:
            raise AdapterError(f"拨钟失败: {err.strip()[:200]}")

    def clock_restore(self) -> None:
        """R33：恢复校验 rc——时钟残留 +N 天会污染后续所有 case 的时间语义，
        失败必须暴露（orchestrator 记 manifest clock_restore_failed）。"""
        epoch = self._clock_epoch
        if epoch is None:
            return
        self._clock_epoch = None
        rc, _, err = self.ch.sudo(f"date -s @{epoch} >/dev/null 2>&1 && echo ok")
        if rc != 0:
            raise AdapterError(f"时钟恢复失败: {err.strip()[:200]}")

    def fs_fingerprints(self, paths: list[str]) -> dict[str, str] | None:
        from memhall.adapters.fs_hashes import remote_hashes
        return remote_hashes(paths, self.ch)

    def close(self) -> None:
        self.ch.close()
