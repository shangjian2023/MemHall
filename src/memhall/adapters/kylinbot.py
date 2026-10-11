"""KylinBotAdapter —— openKylin 3.0 内置智能体「小K」适配器（SSH 远程驱动）。

CLI 实测（2026-09-28 send 实测校准，v0.7.5）：
- send:    `kylin-bot agent -m "<msg>"` 免交互单发，每次独立进程（跨会话只靠 brain.db，
           正中考点）；输出 = 运行日志（ISO 时间戳开头）+ 纯文本回复，需过滤
- 后端:    2026-09-28 起改挂评测方网关（config.toml custom 指向，qwen3.7-plus，
           原官方网关 llm-gateway.openkylin.top 因 Token 余额 402 不可用，备份在
           config.toml.bak-okgw）；单发 ~37k token，RPM 低，send 内置 10s 节流
- 记忆库:  ~/.kylinbot/workspace/memory/brain.db（SQLite+FTS5）
           memories(id UUID, key, content, category, superseded_by, created_at)
           key 是语义键（user_name/code_directory），superseded_by = 版本链证据
- reset:   `kylin-bot memory clear --yes`（实测 ✓ Cleared N/N）
- dump_actions: 暂 unknown（W3 接日志后升级）
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime

from memhall.adapters.base import AgentAdapter, AgentUnavailable
from memhall.adapters.remote import SshChannel, b64, elapsed_ms, now_utc
from memhall.schema.evidence import (
    ActionDump,
    MemoryEntry,
    MemorySnapshot,
    Reply,
)

BRAIN_DB = "~/.kylinbot/workspace/memory/brain.db"

# 用例注入的虚构工作区（评测专用 VM，reset 一并清掉防跨轮污染）
EVAL_WORKDIRS = ["~/dev", "~/work", "~/proj", "~/docs", "~/notes",
                 "~/out", "~/scripts", "~/templates", "~/demo"]

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LOG_LINE = re.compile(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# 网关 RPM 限额低且超限掐 TLS（见 memory/vm-openkylin-access），发送间隔下限压节奏
_SEND_MIN_INTERVAL = float(os.environ.get("KYLINBOT_SEND_INTERVAL", "10"))
_send_lock = threading.Lock()
_last_send = 0.0


def _send_throttle() -> None:
    global _last_send
    with _send_lock:
        wait = _SEND_MIN_INTERVAL - (time.monotonic() - _last_send)
        if wait > 0:
            time.sleep(wait)
        _last_send = time.monotonic()

_DUMP_SRC = (
    "import json,sqlite3,os\n"
    f"db=os.path.expanduser('{BRAIN_DB}')\n"
    "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True)\n"
    "rows=c.execute(\"select id,key,content,category,superseded_by,created_at "
    "from memories where category != 'conversation' "
    "order by created_at\").fetchall()\n"
    "print(json.dumps(rows,ensure_ascii=False))\n"
)


class KylinBotAdapter(AgentAdapter):
    """被测智能体 KylinBot（openKylin 3.0 桌面版内置，官方网关后端）。"""

    name = "kylinbot"

    def __init__(self, channel: SshChannel | None = None):
        self.ch = channel or SshChannel()
        self._clock_epoch: int | None = None

    def reset(self) -> None:
        rc, out, err = self.ch.run(
            "kylin-bot memory clear --yes 2>&1 | grep -E 'Cleared|Found' "
            f"; rm -rf {' '.join(EVAL_WORKDIRS)}"
            # workspace 根散落文件：agent 可能把用例产物写进自家工作区（如 README.md），
            # 跨轮残留会污染后续行为；原始出厂文件白名单保留
            "; find ~/.kylinbot/workspace -maxdepth 1 -type f "
            "! -name 'HEARTBEAT.md' ! -name 'IDENTITY.md' ! -name 'SOUL.md' "
            "! -name 'devices.db' -delete", timeout=120)
        if rc != 0 or "Cleared" not in out:
            # 库本来就空时 clear 无 Cleared 行，只要有 Total:0 语义即通过；
            # 这里 rc!=0 才算失败，空库场景由 stats 兜底验证
            rc2, out2, _ = self.ch.run("kylin-bot memory stats 2>&1 | grep Total",
                                       timeout=60)
            if rc2 != 0 or "Total:    0" not in out2:
                raise RuntimeError(f"KylinBot 记忆清零失败: {(out + err).strip()[:300]}")

    def send(self, session_id: str, message: str) -> Reply:
        _send_throttle()
        cmd = (f'timeout 280 kylin-bot agent -m "$(printf %s {b64(message)} | base64 -d)" '
               f"2>/dev/null")
        sent = now_utc()
        t0 = time.time()
        rc, out, _ = self.ch.run(cmd, timeout=300)
        text = _strip_logs(out)
        if not text:
            raise AgentUnavailable(f"kylin-bot 无有效回复(rc={rc}): {out.strip()[:200]}")
        return Reply(session_id=session_id, text=text,
                     sent_at=sent, reply_at=now_utc(),
                     latency_ms=elapsed_ms(t0), token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # agent 单发模式每次独立进程，无长会话

    def dump_memory(self) -> MemorySnapshot:
        script = "python3 -c '" + _DUMP_SRC.replace("'", "'\\''") + "'"
        try:
            rows = self.ch.run_json(script)
        except RuntimeError:
            return MemorySnapshot(format="sqlite", dumped_at=now_utc(),
                                  entries=[], raw=None)
        entries = [
            MemoryEntry(
                entry_id=str(mid)[:8],
                content=(f"[已被{superseded}取代] {content}" if superseded
                         else f"[{category}] {key}: {content}"),
                created_at=_parse_ts(created_at),
                source_turn=f"memories[{key}]",
            )
            for mid, key, content, category, superseded, created_at in rows
        ]
        return MemorySnapshot(format="sqlite", dumped_at=now_utc(),
                              entries=entries, raw=None)

    def dump_actions(self) -> ActionDump:
        return ActionDump(actions=[], coverage="unknown")

    def close(self) -> None:
        self.ch.close()

    def clock_shift(self, days: int) -> None:
        """VM 拨钟（sudo date -s），记录原时刻供恢复。"""
        if days == 0:
            return
        rc, out, _ = self.ch.run("date +%s")
        if rc != 0:
            raise RuntimeError("拨钟前读取系统时间失败")
        self._clock_epoch = int(out.strip())
        rc, _, err = self.ch.run(
            f"sudo -S date -s '+{days} days' >/dev/null 2>&1 && echo ok",
            stdin_data=f"{self.ch.password}\n")
        if rc != 0:
            raise RuntimeError(f"拨钟失败: {err.strip()[:200]}")

    def clock_restore(self) -> None:
        if self._clock_epoch is None:
            return
        self.ch.run(
            f"sudo -S date -s @{self._clock_epoch} >/dev/null 2>&1 && echo ok",
            stdin_data=f"{self.ch.password}\n")
        self._clock_epoch = None

    def fs_snapshot(self) -> list[str] | None:
        cmd = ("find \"$HOME\" -maxdepth 4 \\( -name .hermes -o -name .cache -o -name .config "
               "-o -name node_modules -o -name .local -o -name .kylinbot \\) -prune -o "
               "-printf '%p\\n' 2>/dev/null | sed \"s|^$HOME|~|\"")
        rc, out, _ = self.ch.run(cmd, timeout=60)
        return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else None


def _strip_logs(out: str) -> str:
    """去 ANSI 转义 + 滤运行日志行（ISO 时间戳开头），留纯文本回复。"""
    clean = _ANSI.sub("", out)
    keep = [ln for ln in clean.splitlines()
            if ln.strip() and not _LOG_LINE.match(ln.strip())]
    return "\n".join(keep).strip()


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
