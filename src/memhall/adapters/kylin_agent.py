"""KylinAgentAdapter —— openKylin 系统智能体 kylin-agent 适配器（SSH 远程驱动）。

调研定案（2026-10-14 初稿，2026-10-15 对 Gitee openkylin/agent-runtime
master 浅克隆 v1.0.1 逐条源码复核，克隆在 D:\\_agent-runtime-research）：
- 产品形态: kylin-agent 是 Qt5 桌面前端（apt kylin-agent）；被测后端 =
  kylin-agent-runtime（pyproject [project.scripts]，附带 hermes 兼容别名）。
  GUI 无法无头驱动，本适配器打后端 CLI——赛题点名的 kylin-agent 生态本体。
  discovery 候选只认 kylin-agent-runtime，绝不把 GUI 前端当命中。
- 家目录:   HERMES_HOME 环境变量覆写，回落 ~/.kylin-agent-runtime
            （kylin_agent_runtime_constants.get_hermes_home）。上游 issue
            #18594 明示"子进程 spawner 应显式传 HERMES_HOME"——本适配器在
            env 行里钉死，防 VM 侧杂散 HERMES_HOME 把状态写进 ~/.hermes。
- 记忆:     memories/MEMORY.md + USER.md（默认开启，config.py DEFAULT_CONFIG
            memory_enabled=True）；另有跨会话召回库 memory.db（memory 工具
            全库检索）与会话库 state.db——reset 必须一并清，否则上一个
            case 的对话可被 memory 工具召回（R02 同源泄漏面）。
- 日志:     logs/agent.log（INFO+；行格式 "%(asctime)s %(levelname)s… %(name)s:
            %(message)s"，工具行 logger agent.tool_executor "tool X completed"）
- send:     kylin-agent-runtime -z "<msg>"——oneshot.py 承诺只打最终正文；
            内部自设 HERMES_YOLO_MODE=1 / HERMES_ACCEPT_HOOKS=1（approvals
            全自动），--accept-hooks 旗标冗余但无害，保留兜底 parse 期 hooks 门。
            ⚠ fork 把首跑向导/参数校验错误打到 stdout 且 exit!=0（main.py
            print_noninteractive_setup_guidance）——send 覆写把"非零 rc 的
            stdout"一律降级 AgentUnavailable，不让提示文本冒充答案计分。
- 模型:     --provider deepseek + DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL（auth.py
            ProviderRegistry 实锤）；--model 值经网关改写为真实模型
            （qwen3.7-plus），DEEPSEEK_* 只是 CLI 的 provider 环境名，不是
            换被测模型。--provider 必须配 --model，否则 rc=2。
- 二进制:   env MEMHALL_KYLIN_AGENT_BIN > PATH > /usr|/usr/local/bin（apt
            kylin-agent-runtime-cache 落点）> ~/.local/bin（pip --user）>
            ~/agent-runtime/.venv/bin（Gitee README 部署路径）；惰性解析一次
            缓存；--version 首行须是 "KylinAgent v…" 才认（GUI 前端/异物不冒充）。

⚠ 未实测标注（fake-channel 测试钉住行为，真机验收由 D 按 TODO-1~6 逐项
兑现，日志即证据；无真实验收记录前不下"已联调"结论）：
  TODO-1 backend 定位/版本：
      D: command -v kylin-agent-runtime；对四个部署落点 ls -l；
         kylin-agent-runtime --version | head -1 == "KylinAgent v<semver> (<date>)"
      期待: 适配器 bin 命中实际部署路径；version_info() 非空且含 KylinAgent；
         GUI kylin-agent --version 的输出不算数
  TODO-2 真实单发与首次向导：
      D: 干净家目录（mv ~/.kylin-agent-runtime ~/kar.bak-<ts>）且不 source
         env 文件时 kylin-agent-runtime -z "hi" → 期待 rc!=0 + stdout 为
         Non-interactive guidance → 适配器侧 AgentUnavailable（invalid_run），
         绝不落成计分答案；正常车道（memhall-kylin-agent tag）send 返回纯正文
  TODO-3 网关 tag 与实际模型：
      D: 一轮 send 后查网关记账（usage 日志）→ tag=memhall-kylin-agent、
         模型=qwen3.7-plus（网关改写后）；VM 侧 DEEPSEEK_BASE_URL=宿主 VM 腿
  TODO-4 teach→新 session→probe：
      D: case 内 teach"记住我的代号青鸟"→ 新 send（独立进程）问"我的代号？"
         → 答案含青鸟；cat memories/MEMORY.md|USER.md 有对应行；tail
         logs/agent.log 有 tool_executor 行；记录 *.lock 残留形态（若有）
  TODO-5 reset 后无残留且不动 Hermes：
      D: reset → ls -A memories/ 为空、memory.db*/state.db*/sessions 无；
         md5sum ~/.hermes/memories/*.md 在 reset 前后一致；verify_reset 通过
  TODO-6 sandbox=1 reuse 与 temporal：
      D: MEMHALL_SANDBOX=1 连跑两 case（同 adapter 实例 reuse）→ send 正常、
         落盘都在白名单 ~/.kylin-agent-runtime；clock_shift/clock_restore 后
         date -u 与宿主 UTC 差 < 300s（错钟闸门口径）
"""

from __future__ import annotations

import os
import re
import shlex
from typing import cast

from memhall.adapters.base import SEND_TIMEOUT_S, AdapterError, AgentUnavailable
from memhall.adapters.hermes import HermesAdapter
from memhall.adapters.remote import LocalChannel, SshChannel
from memhall.schema.evidence import Reply

HOME = "~/.kylin-agent-runtime"

# 后端自报家门格式（cmd_version: "KylinAgent v{__version__} ({date})"）；
# GUI 前端/异物的 --version 输出对不上 → version_info 判 absent
_VER_OK = re.compile(r"(?i)^kylinagent\s+v")

# VM 侧一次性探测：command -v 命中即用，否则按 apt/pip/源码部署落点兜底
# （[ -x "$p" ] 引号化：路径带空格不再劈碎判断）
_BIN_PROBE = (
    "command -v kylin-agent-runtime 2>/dev/null; "
    "for p in /usr/bin/kylin-agent-runtime /usr/local/bin/kylin-agent-runtime "
    "$HOME/.local/bin/kylin-agent-runtime ~/agent-runtime/.venv/bin/kylin-agent-runtime; do "
    '[ -x "$p" ] && echo "$p" && break; done')


class KylinAgentAdapter(HermesAdapter):
    """被测智能体 kylin-agent（系统智能体后端运行时，Hermes fork）。

    继承 HermesAdapter：证据面（memories 两文件 + agent.log 工具行）、
    dump_*/verify_reset 语义同构；覆写 CLI 形态、目录落点、home 钉定与
    fork 特有的"非零 rc + stdout"失败契约。"""

    name = "kylin-agent"
    sandbox_writable = [HOME]
    mem_dir = f"{HOME}/memories"
    agent_log = f"{HOME}/logs/agent.log"
    # fork 实测源码口径的跨会话残留面（reset 一并清，R02：上一个 case 的
    # 对话可经 memory 工具从 memory.db 召回）：
    # - sessions/     config 建 dir；json 快照 opt-in（agent_init 注释）
    # - state.db*     SessionDB 会话转录（含 WAL/SHM 伴文件）
    # - memory.db*    memory 工具的跨会话对话召回库（含 WAL/SHM 伴文件）
    # - kanban.db*    看板库（默认工具集不开，存在即清，无害）
    # 旧名单的 history*/checkpoints 无源码依据（checkpoints 落各工作目录，
    # 已由 EVAL_WORKDIRS 清理覆盖），撤下。
    session_junk = [f"{HOME}/sessions", f"{HOME}/state.db*", f"{HOME}/memory.db*",
                    f"{HOME}/kanban.db*"]
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
        # 路径 shlex 引号化：env 覆写带空格的部署路径不再劈碎命令行。
        # --accept-hooks：SSH 管道无 TTY，未见过的 shell hooks 免交互直批
        # （fork 文档明示 CI/headless 用法；-z 内部亦自设 HERMES_ACCEPT_HOOKS，
        # 此旗标兜 parse 期 hooks 门，冗余无害）
        return (f'timeout {SEND_TIMEOUT_S} {shlex.quote(self.bin)} -z "$(cat "$m")" '
                '--provider deepseek --model "$DEEPSEEK_MODEL" --accept-hooks')

    def _llm_env_lines(self) -> str:
        """网关凭据 env + home 钉定。

        HERMES_HOME 显式指 ~/.kylin-agent-runtime（fork 的 get_hermes_home
        读该变量，上游 #18594 要求 spawner 显式传）：VM 侧任何杂散
        HERMES_HOME（如指 ~/.hermes）都不会把 kylin-agent 的记忆/会话/日志
        写串车道；用户在该目录下的 config.yaml/.env 配置不受影响。"""
        return (
            super()._llm_env_lines()
            + f'export HERMES_HOME="$HOME/{HOME.removeprefix("~/")}"\n'
        )

    def send(self, session_id: str, message: str) -> Reply:
        """复用父类 send 全链路（stdin 凭据/消息、沙箱、超时、后端故障文案），
        只收紧一条 fork 特有契约：非零 rc 的 stdout 不是答案。

        fork 的 -z 把首跑向导引导、--provider/--model 校验错误打到 stdout 且
        exit!=0（oneshot.py/main.py 源码实锤）——父类"rc!=0 且无文本"才判失败
        的口径会把这些提示文本当 Reply 计分。侦察通道记录带 stdin 那次 run
        的 rc（探测/清点等无 stdin 调用不掺），父类放行的 Reply 在此降级。"""
        spy = _SendRcSpy(self.ch)
        self.ch = cast(SshChannel, spy)
        try:
            reply = super().send(session_id, message)
        finally:
            self.ch = spy.inner
        if spy.send_rc not in (0, None):
            raise AgentUnavailable(
                f"{self.name} 调用失败(rc={spy.send_rc})，stdout 非答案不计分: "
                f"{reply.text[:150]}")
        return reply

    def version_info(self) -> str | None:
        """被测版本自动识别——首行须是后端运行时的自报家门（KylinAgent v…）。

        GUI 前端 kylin-agent 或异物二进制的 --version 输出不含该标识 →
        返回 None（宁缺毋假：manifest 记 absent，TODO-1 真机验收兜底）。
        vm_version_line 的 timeout=30 即探测时间上限。"""
        line = super().version_info()
        return line if line and _VER_OK.match(line) else None

    def verify_reset(self) -> None:
        """memories 整目录空（父类）+ fork 召回库无残留（子类加严）。

        memory.db/state.db 残留 = 上一个 case 的对话仍可被 memory 工具召回，
        与 MEMORY.md 残留同级的跨 case 泄漏，reset 后必须不存在。sessions/
        是运行时自建的目录（空目录无害），按内容判不按存在判。"""
        super().verify_reset()
        _, out, _ = self.ch.run(
            f"ls {HOME}/memory.db* {HOME}/state.db* {HOME}/kanban.db* 2>/dev/null; "
            f"ls -A {HOME}/sessions 2>/dev/null")
        if out.strip():   # ls 失败进 /dev/null，out 非空即真有残留（rc 是末条语义，不作数）
            raise AdapterError(
                f"{self.name} reset 后跨会话召回库仍有残留: {out.strip()[:120]}")


class _SendRcSpy:
    """send 通道侦察：只记录带 stdin 的 run 的 rc，行为全透明转发。

    父类 send 唯一带 stdin_data 的调用就是被测 CLI 本体；bin 探测等无
    stdin 调用不掺入 rc 判定。"""

    def __init__(self, inner: SshChannel | LocalChannel):
        self.inner = inner
        self.send_rc: int | None = None

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        rc, out, err = self.inner.run(cmd, timeout=timeout, stdin_data=stdin_data)
        if stdin_data is not None:
            self.send_rc = rc
        return rc, out, err

    def __getattr__(self, item: str):
        return getattr(self.inner, item)
