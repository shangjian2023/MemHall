"""HermesAdapter —— Hermes Agent 适配器（SSH 远程驱动，跑在 openKylin VM 里）。

实测对接方式（2026-09-28 VM 联调定案）：
- 传输:    provider=deepseek（transport=openai_chat）+ DEEPSEEK_BASE_URL 指自定义网关；
           openai-api provider 会被硬性 overlay 成 codex_responses 传输，网关不吃，弃用
- 网络:    VM 直连网关大请求体 TLS 断流，已配 /etc/hosts 指宿主机 + 宿主 tcp_relay 中转
- send:    hermes chat --query-file - --oneshot（stdin 传消息，免 shell 转义；
           每次独立进程 = 天然跨会话，长期记忆只靠持久层，正中考点）
- 记忆:    内置记忆系统（memory tool → ~/.hermes/memories/MEMORY.md / USER.md）
- reset:   清 memories/*.md（上下文无需清：oneshot 每次新 session）
- dump_actions: 暂无操作日志源，coverage=unknown（W3 接日志后升级）
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC

from memhall.adapters.base import AdapterError, AgentAdapter, AgentUnavailable
from memhall.adapters.remote import SshChannel, b64, elapsed_ms, now_utc
from memhall.schema.evidence import ActionDump, MemoryEntry, MemorySnapshot, Reply

HERMES_BIN = "~/.hermes/bin/hermes"
MEM_DIR = "~/.hermes/memories"
AGENT_LOG = "~/.hermes/logs/agent.log"

# 用例注入的虚构工作区（评测专用 VM，reset 一并清掉防跨轮污染）
EVAL_WORKDIRS = ["~/dev", "~/work", "~/proj", "~/docs", "~/notes",
                 "~/out", "~/scripts", "~/templates", "~/demo"]

_BOX_NOISE = re.compile(r"[╭╮╰╯│┌┐└┘]|\s─")


class HermesAdapter(AgentAdapter):
    """被测智能体 Hermes Agent（v0.21.x，VM 内 ~/.hermes 部署，内置记忆）。"""

    name = "hermes"

    def __init__(self, channel: SshChannel | None = None):
        self.ch = channel or SshChannel()
        self._log_offset = 0
        self._clock_epoch: int | None = None
        # 统一模型模式（GATEWAY_VM_URL，VM 走宿主网关的明文 http 腿）优先：
        # 网关改写 model、真凭据只在网关侧
        from memhall.gateway import gateway_settings
        gw = gateway_settings("hermes", vm_lane=True) or {}
        self._key = gw.get("key") or os.environ.get("AGENT_LLM_KEY", "")
        self._url = gw.get("base_url") or os.environ.get("AGENT_LLM_BASE_URL", "")
        self._model = gw.get("model") or os.environ.get("AGENT_LLM_MODEL", "qwen3.7-plus")

    def _llm_env_lines(self) -> str:
        """网关凭据的 env 文件内容（send 走 stdin；systest 离线脚本 source 用）。

        必须 export——`. file` source 裸赋值只设 shell 局部变量，hermes 子进程
        看不到（2026-10-02 VM 实跑逮到的坑）。值加单引号防空白/特殊字符劈碎。
        """
        def _q(v: str) -> str:
            return "'" + v.replace("'", "'\\''") + "'"
        return (f"export DEEPSEEK_API_KEY={_q(self._key)}\n"
                f"export DEEPSEEK_BASE_URL={_q(self._url)}\n"
                f"export DEEPSEEK_MODEL={_q(self._model)}\n")

    def reset(self) -> None:
        rc, _, err = self.ch.run(
            f"rm -f {MEM_DIR}/MEMORY.md {MEM_DIR}/USER.md && "
            f"rm -rf {' '.join(EVAL_WORKDIRS)} && echo ok")
        if rc != 0:
            raise RuntimeError(f"Hermes 记忆清零失败: {err.strip()[:300]}")
        # 会话转录候选目录一并清（R02：任何"从历史会话回忆"的检索路径都会把
        # 上一个 case 的答案带进下一个 case；路径下次 VM 联调核实，不存在时无害）
        self.ch.run("rm -rf ~/.hermes/sessions ~/.hermes/history* 2>/dev/null; true")
        # 记 agent.log 偏移：dump_actions 只解析本 case 增量
        rc, out, _ = self.ch.run(f"wc -c < {AGENT_LOG} 2>/dev/null || echo 0")
        self._log_offset = int(out.strip() or 0)

    def verify_reset(self) -> None:
        """memories 目录必须整目录空——dump 只读两个 md，残留即污染（R02）。"""
        rc, out, _ = self.ch.run(f"ls -A {MEM_DIR} 2>/dev/null | head -3")
        if rc == 0 and out.strip():
            raise AdapterError(
                f"hermes reset 后 {MEM_DIR} 仍有残留：{out.strip()[:120]}")

    def send(self, session_id: str, message: str) -> Reply:
        # 凭据与消息都走 stdin（base64），命令行零明文：VM 内 ps/history
        # 不可见，值含引号/特殊字符也不会把命令拼碎。
        # stdin 先 cat 成临时文件再按行拆（管道上 head/tail 直接分段会丢数据——
        # head 无法回退 seek，超读部分即吞掉，VM 实跑逮到 query 为空）。
        # env 文件 600 权限即删；hermes 从消息文件读，不再用 stdin。
        script = (
            'd=$(mktemp -t mh-stdin.XXXXXX) && cat > "$d" && '
            'e=$(mktemp -t mh-env.XXXXXX) && head -n1 "$d" | base64 -d > "$e" '
            '&& chmod 600 "$e" && . "$e" && rm -f "$e" && '
            'm=$(mktemp -t mh-msg.XXXXXX) && tail -n +2 "$d" | base64 -d > "$m" '
            '&& rm -f "$d" && '
            f'timeout 280 {HERMES_BIN} chat --query-file "$m" '
            '--oneshot --provider deepseek --model "$DEEPSEEK_MODEL" 2>/dev/null; '
            'rm -f "$m"')
        sent = now_utc()
        t0 = time.time()
        rc, out, _ = self.ch.run(script, timeout=300,
                                 stdin_data=b64(self._llm_env_lines()) + "\n"
                                            + b64(message))
        text = _strip_tui(out)
        # R32：timeout 280 杀进程 rc=124 时输出是残句——残句不是答案，不得
        # 当 Reply 计分（虚高）；超时一律 AgentUnavailable → invalid_run
        if rc == 124:
            raise AgentUnavailable(
                f"hermes 超时(280s)，残句不计分: {text[:120]}")
        if rc != 0 and not text:
            raise AgentUnavailable(f"hermes 调用失败({rc})")
        # hermes 后端故障文案（已实测两种）：不让错误文本混进答案被当行为评分
        if ("API failed after" in text or "Final error" in text
                or "server error" in text.lower()):
            raise AgentUnavailable(f"hermes 后端不可用: {text[:200]}")
        return Reply(session_id=session_id, text=text,
                     sent_at=sent, reply_at=now_utc(),
                     latency_ms=elapsed_ms(t0), token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # oneshot 每次独立进程，会话隔离天然成立

    def version_info(self) -> str | None:
        rc, out, _ = self.ch.run(f"{HERMES_BIN} --version 2>/dev/null", timeout=30)
        line = out.strip().splitlines()[0] if out.strip() else ""
        return line or None

    def dump_memory(self) -> MemorySnapshot:
        cmd = (f"for f in {MEM_DIR}/MEMORY.md {MEM_DIR}/USER.md; do "
               f"[ -f $f ] && echo \"=== $f\" && cat $f; done")
        rc, out, _ = self.ch.run(cmd)
        # R31：导出失败如实标注（dump_ok=False → 规则层判运行无效），
        # 不再与"空记忆"混淆——canary 泄漏洗白面就此关死
        if rc != 0:
            return MemorySnapshot(format="files", dumped_at=now_utc(),
                                  entries=[], raw=None, dump_ok=False,
                                  error=f"SSH cat 失败 rc={rc}")
        entries: list[MemoryEntry] = []
        current = ""
        for line in out.splitlines():
            if line.startswith("=== "):
                current = line[4:].strip()
                continue
            # R58：列表标记只剥"- "/"* "带空格的形态，不再 lstrip("-* ")
            # 连 "-3°C 是最低温" 这类正文首字符一起剥掉
            s = re.sub(r"^[-*]\s+", "", line.strip()).strip()
            if s:
                entries.append(MemoryEntry(entry_id=f"m-{len(entries):04d}",
                                           content=s, created_at=None,
                                           source_turn=current or "unknown"))
        return MemorySnapshot(format="files", dumped_at=now_utc(),
                              entries=entries, raw=None)

    def dump_actions(self) -> ActionDump:
        """解析 agent.log 本 case 增量里的工具调用（agent.tool_executor 行）。

        R58：ts 记日志行内的发生时刻（行首 ISO 时间戳），不再一律记采集
        时刻——时序证据不失真；无时间戳的行退采集时刻。"""
        from datetime import datetime

        from memhall.schema.evidence import Action, ActionSource
        cmd = (f"tail -c +{self._log_offset + 1} {AGENT_LOG} 2>/dev/null | "
               f"grep 'tool_executor: tool '")
        rc, out, _ = self.ch.run(cmd, timeout=30)
        actions: list[Action] = []
        if rc == 0:
            for i, line in enumerate(out.splitlines(), 1):
                m = re.match(r"(\d{4}-\d{2}-\d{2}[T ][0-9:.]+Z?)\s*"
                             r".*?tool_executor: tool ([a-z_0-9-]+) "
                             r"(completed|returned)", line)
                if not m:
                    m2 = re.search(r"tool_executor: tool ([a-z_0-9-]+) "
                                   r"(completed|returned)", line)
                    if not m2:
                        continue
                    ts, tool, status = now_utc(), m2.group(1), m2.group(2)
                else:
                    raw = m.group(1).replace("Z", "+00:00").replace(" ", "T")
                    try:
                        ts = datetime.fromisoformat(raw)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=UTC)
                    except ValueError:
                        ts = now_utc()
                    tool, status = m.group(2), m.group(3)
                actions.append(Action(
                    action_id=f"a-{i:03d}", ts=ts, tool=tool, args={},
                    result=status, source=ActionSource.AGENT_LOG))
        return ActionDump(actions=actions,
                          coverage="partial" if actions else "unknown")

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
        epoch = getattr(self, "_clock_epoch", None)
        if epoch is None:
            return
        self._clock_epoch = None
        rc, _, err = self.ch.sudo(f"date -s @{epoch} >/dev/null 2>&1 && echo ok")
        if rc != 0:
            raise AdapterError(f"时钟恢复失败: {err.strip()[:200]}")

    def close(self) -> None:
        self.ch.close()

    def fs_snapshot(self) -> list[str] | None:
        """VM 用户区文件清单（~ 下 4 层，排除 hermes 自身与缓存噪音）。
        归一化用远端 $HOME 展开（不硬编码 /home/<用户名>——第三方 VM
        用户名不同时硬编码会让全部 fs 断言静默失配）。"""
        cmd = ("find ~ -maxdepth 4 \\( -name .hermes -o -name .cache -o -name .config "
               "-o -name node_modules -o -name .local -o -name .kylinbot \\) -prune -o "
               '-printf \'%p\\n\' 2>/dev/null | sed "s|^$HOME|~|"')
        rc, out, _ = self.ch.run(cmd, timeout=60)
        return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else None


def _strip_tui(out: str) -> str:
    """取 Hermes 回复框（╭─ ☤ Hermes ─╮…╰─╯）内正文；无框时退化为去噪。"""
    lines = out.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        if ("╭" in lines[i] or "┌" in lines[i]) and "Hermes" in lines[i]:
            j = i + 1
            block: list[str] = []
            while j < len(lines) and "╰" not in lines[j] and "└" not in lines[j]:
                block.append(lines[j].lstrip("│ ").rstrip())
                j += 1
            text = "\n".join(block).strip()
            if text:
                blocks.append(text)
            i = j
        i += 1
    if blocks:
        return "\n".join(blocks)
    # 无框退化路径：滤 TUI 状态行（API 重试/加载提示）与噪声头。
    # R58：词形噪声锚定行首——原"子串任意位置命中"会误伤正文里
    # 引用这些词的回答行；符号类（emoji/制表框）不受影响
    noise_start = ("Query:", "Initializing", "Session:", "Resume", "Duration",
                   "Title:", "Messages:", "API failed", "Retrying", "Transient",
                   "Provider said", "rebuilt client")
    keep = [ln.rstrip() for ln in lines
            if ln.strip() and not any(n in ln for n in ("⚠", "⏳", "❌", "🔁", "💀"))
            and not any(ln.strip().startswith(n) for n in noise_start)
            and not set(ln.strip()) & set("╭╮╰╯│┌┐└┘─")]
    return "\n".join(keep).strip()
