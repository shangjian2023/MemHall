"""VM 三家适配器（hermes / openclaw / kylinbot）共享的通道侧实现。

拨钟/恢复、版本探测、远端指纹、close 与 EVAL_WORKDIRS 原是三份近
逐字复制（2026-10-13 适配器层收编）；各家行为差异（send/reset 的
CLI 形态、记忆导出）仍留在各自模块。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from memhall.adapters.base import AdapterError
from memhall.adapters.remote import LocalChannel, SshChannel

# 用例注入的虚构工作区 + reuse 交付产物根（评测专用 VM，reset 一并清掉防跨轮
# 污染）。~/memhall-artifacts 是题面点名的产物落点：进沙箱可写白名单
# （_sandboxed 自动并入）、进三家 reset 清理——上轮残留的同名文件会把下一轮
# fs_diff 的 created 证据吃掉（fs_diff 的 created 是快照路径集合差，文件还在
# 就不再算新建），同题重跑必失分。清的是字面量评测路径，不拼动态路径。
EVAL_WORKDIRS = ["~/dev", "~/work", "~/proj", "~/docs", "~/notes",
                 "~/out", "~/scripts", "~/templates", "~/demo",
                 "~/memhall-artifacts"]

# 恢复后实测校验容差：date -s 与回读两次命令间隔的现实抖动是亚秒级，5s 已
# 十分宽裕；预检漂移闸门（默认 ±300s）不在此调——这里只自证"真的拨回来了"。
_CLOCK_VERIFY_TOL_S = 5.0

# "wall_epoch mono_s" 一行两读数（echo $(date +%s) $(cut ... /proc/uptime)）
_PAIR_RE = re.compile(r"^\s*(\d+)\s+(\d+(?:\.\d+)?)\s*$")


def vm_version_line(ch: SshChannel | LocalChannel, cmd: str) -> str | None:
    """跑版本命令取输出第一行（探测尽力而为，失败返回 None）。"""
    rc, out, _ = ch.run(cmd, timeout=30)
    line = out.strip().splitlines()[0] if out.strip() else ""
    return line or None


@dataclass
class _ClockState:
    """首次拨钟前的基准对：wall epoch + 单调钟读数。

    恢复目标 = wall_epoch + 单调增量——已被拨动的 wall 钟不可用来算耗时。"""

    wall_epoch: int
    mono_s: float


def read_vm_clock(ch: SshChannel | LocalChannel) -> tuple[int, float]:
    """一次往返同时读 wall epoch 与单调钟（/proc/uptime 首字段）。

    两读数间隔 <1ms（同一条 shell 命令），配对误差可忽略；date -s 拨不动
    /proc/uptime，耗时一律以它为准。/proc/uptime 读不出（非 Linux/异常）
    抛 AdapterError——没有单调基准就无法正确恢复，宁可本 case 运行无效。"""
    rc, out, err = ch.run(
        "echo $(date +%s) $(cut -d' ' -f1 /proc/uptime 2>/dev/null)", timeout=30)
    if rc != 0:
        raise AdapterError(f"读取系统时间失败(rc={rc}): {err.strip()[:120]}")
    m = _PAIR_RE.match(out)
    if not m:
        raise AdapterError(f"系统时间读数不可解析: {out.strip()[:80]!r}")
    return int(m.group(1)), float(m.group(2))


class VmAdapterMixin:
    """通道驱动适配器的公共钩子：sudo 拨钟/恢复、远端指纹、close、OS 沙箱。

    宿主适配器提供 self.ch（SshChannel / LocalChannel）。

    拨钟/恢复语义（2026-10-14 评审修复）：
    - 恢复目标 = 首次拨钟前 epoch + 单调钟增量（真实耗时），不是把 epoch
      原样回拨——后者每 case 白吃一段耗时，攒过 ±300s 漂移闸门，下一场
      被拒跑；
    - 一个 case 里多次 shift 只认第一份基准；
    - shift 失败也保留基准：restore 按单调增量重算目标，能把半途而废的
      拨钟拉回真实时刻（自愈）；
    - restore 失败（拨不回 / 回读超容差）不清状态——orchestrator 每个
      case 的 finally 都会再调一次，天然重试；close() 不做恢复（恢复归
      runner 管，单一职责），最后一场恢复失败则残留漂移由下一场 A1 预检
      闸门拒跑兜底，不会静默混入成绩。"""

    ch: SshChannel | LocalChannel
    _clock_state: _ClockState | None = None

    # OS 级沙箱（B8）的可写白名单：agent 自家 dotdir（各适配器覆写）；
    # EVAL_WORKDIRS（含产物根）由 _sandboxed 自动并入。默认空 = 沙箱
    # 开启时无额外可写位。
    sandbox_writable: ClassVar[list[str]] = []

    def _sandboxed(self, script: str) -> str:
        """send 脚本出口：MEMHALL_SANDBOX=1 时包 bwrap（默认原样返回）。"""
        from memhall.adapters.sandbox import maybe_wrap
        return maybe_wrap(script, EVAL_WORKDIRS + self.sandbox_writable)

    def clock_shift(self, days: int) -> None:
        """VM 拨钟（sudo date -s，密码走 stdin），记录首份基准供恢复。

        R33：失败抛 AdapterError（不再 RuntimeError）——orchestrator 只兜
        AdapterError，RuntimeError 会走 failed_cases 把已采证据丢掉。"""
        if days == 0:
            return
        if self._clock_state is None:
            wall, mono = read_vm_clock(self.ch)
            self._clock_state = _ClockState(wall_epoch=wall, mono_s=mono)
        rc, _, err = self.ch.sudo(f"date -s '+{days} days' >/dev/null 2>&1 && echo ok")
        if rc != 0:
            # 基准保留：restore 会按单调增量把钟对回真实时刻，丢了基准
            # 反而没机会纠
            raise AdapterError(f"拨钟失败: {err.strip()[:200]}")

    def clock_restore(self) -> None:
        """恢复到基准 + 真实耗时，并回读实测校验。

        R33：时钟残留 +N 天会污染后续所有 case 的时间语义，失败必须暴露
        （orchestrator 记 manifest clock_restore_failed）；失败不清状态，
        下次调用重试。无挂起基准时幂等 no-op（普通 case 零开销）。"""
        state = self._clock_state
        if state is None:
            return
        _, mono_now = read_vm_clock(self.ch)
        target = state.wall_epoch + round(mono_now - state.mono_s)
        rc, _, err = self.ch.sudo(f"date -s @{target} >/dev/null 2>&1 && echo ok")
        if rc != 0:
            raise AdapterError(f"时钟恢复失败: {err.strip()[:200]}")
        wall_after, mono_after = read_vm_clock(self.ch)
        expect = state.wall_epoch + (mono_after - state.mono_s)
        if abs(wall_after - expect) > _CLOCK_VERIFY_TOL_S:
            # date -s"成功"但实际没回（或立刻又被别的什么拨走）：如实报错，
            # 状态保留待重试
            raise AdapterError(
                f"时钟恢复校验失败: 实际 {int(wall_after)} vs 预期 {int(expect)}"
                f"（容差 ±{_CLOCK_VERIFY_TOL_S:.0f}s）")
        self._clock_state = None

    def fs_fingerprints(self, paths: list[str]) -> dict[str, str] | None:
        from memhall.adapters.fs_hashes import remote_hashes
        return remote_hashes(paths, self.ch)

    def close(self) -> None:
        self.ch.close()
