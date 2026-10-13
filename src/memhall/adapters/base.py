"""契约 01 · AgentAdapter 基类与异常（pydantic + abc）。

owner: A（架构）—— 权威定义在 docs/contracts/adapter.md。
三档接入：zero-code（协议模板，W2）/ doctor（自动探测，W3）/ custom（继承本类）。
MockAdapter 在 adapters/mock.py —— 全队第一个能跑的适配器，也是 M1 冒烟的"被测智能体"。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from memhall.schema.evidence import ActionDump, MemorySnapshot, Reply

# Windows：无控制台进程（windowed exe）spawn 控制台子程序会弹黑窗，
# 每发一条消息闪一下。统一带此 flag（stdout/stderr 走管道不受影响）。
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# send 统一超时惯例（原为各适配器手抄的字面量，2026-10-13 收编上收）：
# 内层负责杀子进程，外层是通道收包兜底，必须 > 内层。
SEND_TIMEOUT_S = 280
CHANNEL_TIMEOUT_S = 300


# ---------- 异常（契约 01 §2）----------
# runner 捕获后的处理：前两个 -> case 标运行无效；后两个 -> 对应降级路径。

class AdapterError(Exception):
    """适配器异常基类。"""


class AgentTimeout(AdapterError):
    """send() 超时（默认 120s，可配）。"""


class AgentUnavailable(AdapterError):
    """进程崩溃 / 无响应 / 答非所问到无法回复。"""


class MemoryResetUnsupported(AdapterError):
    """记忆无法归零 -> runner 降级快照回滚，或标 reset_method 进 evidence。"""


class MemoryNotDumpable(AdapterError):
    """记忆无法导出（纯云端记忆）-> 该 case 降级纯行为判定。"""


def cli_version(args: list[str]) -> str | None:
    """跑 `<exe> --version` 取第一行（版本探测尽力而为，失败返回 None）。"""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return lines[0] if lines else None


def workspace_fs_snapshot(workspace: Path) -> list[str] | None:
    """本地车道 workspace 文件清单（R24/R30：统一 ~ 相对形式）。

    workspace 就是本适配器的"用户区"（工作区根 = ~），fs 断言写
    ~/dev/src/demo——出口统一 ~ 前缀后 evidence 各车道逐字可比
    （R30 曾靠规则层双向归一兜底）。隐藏目录按 workspace 内相对
    部分判定：绝对路径部件含 ~/.memhall 这类点目录祖先时不误剪
    （2026-10-13 修复：默认沙箱根下快照曾恒空）。
    """
    if not workspace.exists():
        return None
    return [
        "~/" + p.relative_to(workspace).as_posix()
        for p in sorted(workspace.rglob("*"))
        if p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(workspace).parts)
    ]


# ---------- 基类 ----------

class AgentAdapter(ABC):
    """所有智能体适配器的基类：评测器与被测智能体之间的翻译官。

    只做四件事——转发消息、导出记忆、导出操作、管理会话生命周期。
    不评判、不改写用例、不产生判定（评分层的事一件不碰）。
    """

    name: str = "abstract"

    # 发送节流（原为各适配器模块级三件套的六连抄，2026-10-13 收编）：同一
    # 车道类进程内共享上次发送时刻，间隔可用环境变量覆盖；None = 不节流。
    send_interval_env: ClassVar[str | None] = None
    send_interval_default: ClassVar[float] = 10.0
    _send_state: ClassVar[dict[type[AgentAdapter], float]] = {}
    _send_lock: ClassVar[threading.Lock] = threading.Lock()

    def _send_throttle(self) -> None:
        if self.send_interval_env is None:
            return
        with self._send_lock:
            interval = float(os.environ.get(
                self.send_interval_env, str(self.send_interval_default)))
            last = self._send_state.get(type(self), 0.0)
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._send_state[type(self)] = time.monotonic()

    @abstractmethod
    def reset(self) -> None:
        """清空智能体记忆，回到初始状态。每个 case 开始前调用。"""

    @abstractmethod
    def send(self, session_id: str, message: str) -> Reply:
        """发一条用户消息，等待并返回回复。message 原样透传，不得改写。"""

    @abstractmethod
    def end_session(self, session_id: str) -> None:
        """结束一个会话：关窗口/杀进程/调登出接口，任选能实现的方式。"""

    @abstractmethod
    def dump_memory(self) -> MemorySnapshot:
        """导出当前全部记忆。没有导出接口就直读存储文件，format 如实标注。"""

    def dump_actions(self) -> ActionDump:
        """导出 reset 以来的操作记录。来源优先级：MCP 日志 > 智能体日志 > auditd。

        默认空壳（coverage=unknown）：暂无操作日志源的车道如实标注完整度、
        不装作没有（原为六处逐字复制，2026-10-13 收编为默认实现）；
        有源的适配器（如 hermes 解析 agent.log）覆写。"""
        return ActionDump(actions=[], coverage="unknown")

    def version_info(self) -> str | None:
        """被测智能体版本标识（进 manifest/report，可复现性元数据）。

        尽力而为：探测失败返回 None，不阻塞评测。"""
        return None

    def fs_snapshot(self) -> list[str] | None:
        """被测环境用户区文件清单（fs_diff 证据源）。None = 不支持，runner 跳过，
        fs 断言探测点将判运行无效（规则层不查判卷机本地盘）。

        路径统一 ~ 相对形式（如 ~/dev/src/demo）。
        """
        return None

    def fs_fingerprints(self, paths: list[str]) -> dict[str, str] | None:
        """Optional SHA-256 map for captured files; None means unavailable."""
        return None

    def clock_shift(self, days: int) -> None:
        """拨动被测环境系统时钟 N 天（模拟隔天/隔周，temporal 题前提）。

        R38：默认 fail-closed——不覆写就把 temporal 题当普通题跑，智能体在
        零时间间隔下作答分数虚高，还会让"拨钟题分组呈现"的口径失真。
        未实现的适配器在此抛错，temporal 用例判运行无效（单列，不算 0 分）。
        """
        if days == 0:
            return
        raise AdapterError("本适配器未实现拨钟，temporal 用例判运行无效")

    def clock_restore(self) -> None:
        """恢复系统时钟（case 结束由 runner 调用）。默认 no-op。"""
        return

    def close(self) -> None:
        """释放底层资源（SSH 连接等）。run_suite 结束时统一调用；默认 no-op。"""
        return

    def verify_reset(self) -> None:
        """reset 彻底性防线（每个 case 开跑前由 runner 调用，fail fast）。

        默认校验 dump_memory 为空；适配器有 dump 覆盖不到的记忆源
        （会话转录、其他存储文件）时应覆写加强。reset 不彻底 = 跨用例
        污染——同问异答的题库里上一个 case 的答案是定向毒药，宁可中止。
        R31：导出失败（dump_ok=False）无法验证清零，同样 fail fast。"""
        snap = self.dump_memory()
        if not snap.dump_ok:
            raise AdapterError(
                f"reset 后记忆导出失败，无法验证清零: {snap.error[:120]}")
        if snap.entries:
            raise AdapterError(
                f"reset 后记忆非空（{len(snap.entries)} 条残留）："
                "跨用例污染风险，本 case 中止（docs/review-tasks.md R02）")
