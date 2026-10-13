"""KylinAgentAdapter —— openKylin 系统智能体 kylin-agent 适配器（SSH 远程驱动）。

调研定案（2026-10-14，源码 openKylin/agent-runtime master 浅克隆 + 官方
《openKylin 系统智能体用户手册》V0.9.2）：
- 产品形态: kylin-agent 是 Qt5 桌面前端（apt kylin-agent，软件商店搜"智能体/
  kylin-agent"）；其后端"智能体运行时"= kylin-agent-runtime（Gitee
  openkylin/agent-runtime，Hermes fork，MIT，Python 3.11）。GUI 无法无头
  驱动，本适配器打后端 CLI——正是赛题点名的 kylin-agent 生态本体。
- 家目录:   ~/.kylin-agent-runtime（HERMES_HOME 环境变量可覆写）
- 记忆:     memories/MEMORY.md + USER.md（与 Hermes 同构，doctor.py 实锤），
            SOUL.md 个性文件在家目录根
- 日志:     logs/agent.log（tool_executor 行格式与 hermes 相同）
- send:     kylin-agent-runtime -z "<msg>" —— 官方 one-shot 模式，承诺
            "print ONLY the final response"（无 TUI 框，无需剥框）
- 模型:     --provider deepseek + DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL 环境变量
            （fork 保留该 provider，auth.py 实锤），走评测方网关与 hermes 同道
- 二进制:   env MEMHALL_KYLIN_AGENT_BIN > PATH > /usr|/usr/local/bin（apt
            kylin-agent-runtime-cache 落点）> ~/agent-runtime/.venv/bin
            （Gitee README 部署路径）；惰性解析一次缓存

⚠️ 未实测标注（2026-10-14 写定）：VM 被并行会话占用，以下联调清单待 VM 空闲
后逐条兑现（对齐 kylinbot.py 2026-09-28 send 实测校准的做法）：
  TODO-1 部署与定位：apt kylin-agent-runtime-cache 或 Gitee clone + uv venv，
         实测 `kylin-agent-runtime --version` 与本适配器 bin 探测命中哪个落点
  TODO-2 send 实测：-z 输出是否纯文本（官方承诺 only final response）；
         首跑是否弹 setup 向导（无 TTY 时行为）、--accept-hooks 是否必要
  TODO-3 网关对接：DEEPSEEK_* 指评测方网关（与 hermes 同车道）是否通；
         VM 直连大请求体 TLS 断流问题是否复现（宿主 tcp_relay 同方）
  TODO-4 记忆实锤：一轮 teach→gap 后 memories/MEMORY.md/USER.md 是否落盘、
         *.lock 残留形态是否同 hermes；dump_actions 的 agent.log 行格式核对
  TODO-5 reset 核对：session_junk 三个候选目录的真实存在性（不存在时无害）
"""

from __future__ import annotations

import os

from memhall.adapters.base import SEND_TIMEOUT_S
from memhall.adapters.hermes import HermesAdapter
from memhall.adapters.remote import LocalChannel, SshChannel

HOME = "~/.kylin-agent-runtime"

# VM 侧一次性探测：command -v 命中即用，否则按 apt/源码部署落点兜底
_BIN_PROBE = (
    "command -v kylin-agent-runtime 2>/dev/null; "
    "for p in /usr/bin/kylin-agent-runtime /usr/local/bin/kylin-agent-runtime "
    "~/agent-runtime/.venv/bin/kylin-agent-runtime; do "
    "[ -x $(eval echo $p) ] && eval echo $p && break; done")


class KylinAgentAdapter(HermesAdapter):
    """被测智能体 kylin-agent（系统智能体后端运行时，Hermes fork）。

    继承 HermesAdapter：证据面（memories 两文件 + agent.log 工具行）、
    reset/verify_reset/dump_* 语义全部同构；只覆写 CLI 形态与目录落点。"""

    name = "kylin-agent"
    sandbox_writable = [HOME]
    mem_dir = f"{HOME}/memories"
    agent_log = f"{HOME}/logs/agent.log"
    # 会话转录/检查点候选（fork 有 sessions/checkpoints 子命令；不存在时无害）
    session_junk = [f"{HOME}/sessions", f"{HOME}/history*",
                    f"{HOME}/checkpoints"]
    fs_prune = HermesAdapter.fs_prune + [".kylin-agent-runtime"]

    def __init__(self, channel: SshChannel | LocalChannel | None = None):
        super().__init__(channel)
        self._bin_resolved: str | None = None

    @property
    def bin(self) -> str:
        """惰性定位 kylin-agent-runtime；探测失败退 PATH 裸名（send 报错可见）。"""
        if self._bin_resolved is None:
            env = os.environ.get("MEMHALL_KYLIN_AGENT_BIN", "").strip()
            if env:
                self._bin_resolved = env
            else:
                rc, out, _ = self.ch.run(_BIN_PROBE, timeout=30)
                hit = next((ln.strip() for ln in out.splitlines()
                            if ln.strip().startswith(("/", "~"))), "")
                self._bin_resolved = hit or "kylin-agent-runtime"
        return self._bin_resolved

    @bin.setter
    def bin(self, value: str) -> None:
        """Retain the writable binary override contract inherited from Hermes."""
        self._bin_resolved = value

    def _agent_cmd(self) -> str:
        # --accept-hooks：SSH 管道无 TTY，未见过的 shell hooks 免交互直批
        # （fork 文档明示 CI/headless 用法；避免 curses 挂死 100% CPU）
        return (f'timeout {SEND_TIMEOUT_S} {self.bin} -z "$(cat "$m")" '
                '--provider deepseek --model "$DEEPSEEK_MODEL" --accept-hooks')
