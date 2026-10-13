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
- dump_actions: 默认空壳 coverage=unknown（W3 接日志后覆写升级）
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from memhall.adapters.base import (
    CHANNEL_TIMEOUT_S,
    SEND_TIMEOUT_S,
    AdapterError,
    AgentAdapter,
    AgentUnavailable,
)
from memhall.adapters.remote import (
    LocalChannel,
    SshChannel,
    b64,
    default_channel,
    elapsed_ms,
    now_utc,
)
from memhall.adapters.vm_common import EVAL_WORKDIRS, VmAdapterMixin, vm_version_line
from memhall.schema.evidence import (
    MemoryEntry,
    MemorySnapshot,
    Reply,
)

BRAIN_DB = "~/.kylinbot/workspace/memory/brain.db"
CONFIG_TOML = "~/.kylinbot/config.toml"
CONFIG_BAK = "~/.kylinbot/config.toml.bak-memhall"
DIRECT_PROVIDER = "custom:https://api.mazhuoran.cloud/v1"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LOG_LINE = re.compile(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

_DUMP_SRC = (
    "import json,sqlite3,os\n"
    f"db=os.path.expanduser('{BRAIN_DB}')\n"
    "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True)\n"
    "rows=c.execute(\"select id,key,content,category,superseded_by,created_at "
    "from memories where category != 'conversation' "
    "order by created_at\").fetchall()\n"
    "print(json.dumps(rows,ensure_ascii=False))\n"
)


class KylinBotAdapter(VmAdapterMixin, AgentAdapter):
    """被测智能体 KylinBot（openKylin 3.0 桌面版内置，官方网关后端）。"""

    name = "kylinbot"
    # 网关 RPM 限额低且超限掐 TLS（见 memory/vm-openkylin-access），发送间隔下限压节奏
    send_interval_env = "KYLINBOT_SEND_INTERVAL"
    send_interval_default = 10.0
    sandbox_writable = ["~/.kylinbot"]  # B8：workspace（brain.db 记忆+产物）与配置

    def __init__(self, channel: SshChannel | LocalChannel | None = None):
        self.ch = channel or default_channel()

    def _apply_model_lane(self) -> None:
        """统一模型模式：custom provider 改指宿主网关（首次备份，直连模式自动还原）。

        config.toml 的 provider 编码在表名里（[providers.models."custom:<url>"]，
        wire_api=chat_completions）——sed 换表名 + 表内 api_key 换 dummy
        （入站 Bearer 兼作网关记账的身份标记）。model 字段不动：网关侧强制改写。
        """
        from memhall.gateway import gateway_settings
        gw = gateway_settings("kylinbot", vm_lane=True)
        rc, cur, _ = self.ch.run(f"grep -o 'custom:[^\"]*' {CONFIG_TOML} | head -1")
        current = cur.strip()
        if gw:
            want = f"custom:{gw['base_url']}"
            if current == want:
                return
            if not current.startswith("custom:"):
                raise RuntimeError(f"config.toml provider 形态意外: {current[:80]}")
            # 首次改写前备份（含直连真 key），供直连模式还原
            self.ch.run(f"[ -f {CONFIG_BAK} ] || cp {CONFIG_TOML} {CONFIG_BAK}")
            rc, _, err = self.ch.run(
                f'sed -i "s|{current}|{want}|" {CONFIG_TOML} && '
                f"sed -i '/providers\\.models\\.\"custom:/,/^$/ "
                f"s|^api_key = .*|api_key = \"{gw['key']}\"|' {CONFIG_TOML}")
            if rc != 0:
                raise RuntimeError(f"config.toml 改指网关失败: {err.strip()[:200]}")
        elif current != DIRECT_PROVIDER and "8311" in current:
            # 上轮统一模式残留：还原直连配置
            self.ch.run(f"[ -f {CONFIG_BAK} ] && cp {CONFIG_BAK} {CONFIG_TOML}")

    def reset(self) -> None:
        self._apply_model_lane()
        rc, out, err = self.ch.run(
            "kylin-bot memory clear --yes 2>&1 | grep -E 'Cleared|Found' "
            f"; rm -rf {' '.join(EVAL_WORKDIRS)}"
            # workspace 根散落文件：agent 可能把用例产物写进自家工作区（如 README.md），
            # 跨轮残留会污染后续行为；原始出厂文件白名单保留
            "; find ~/.kylinbot/workspace -maxdepth 1 -type f "
            "! -name 'HEARTBEAT.md' ! -name 'IDENTITY.md' ! -name 'SOUL.md' "
            "! -name 'devices.db' -delete", timeout=120)
        # 空库时 clear 无 Cleared 行；R58：空库判定改结构化查询
        # （brain.db 行数），不再 grep "Total:    0" 脆弱字符串
        if (rc != 0 or "Cleared" not in out) and (rc != 0 or not self._db_empty()):
            raise AdapterError(
                f"KylinBot 记忆清零失败: {(out + err).strip()[:300]}")

    def _db_empty(self) -> bool:
        script = ("python3 -c 'import sqlite3,os;"
                  "c=sqlite3.connect(\"file:\"+os.path.expanduser(\""
                  f"{BRAIN_DB}\")+\"?mode=ro\",uri=True);"
                  "print(c.execute(\"select count(*) from memories\").fetchone()[0])'")
        rc, out, _ = self.ch.run(script, timeout=30)
        return rc == 0 and out.strip().isdigit() and int(out.strip()) == 0

    def send(self, session_id: str, message: str) -> Reply:
        self._send_throttle()
        # R58：CLI 仅支持 -m <MESSAGE>（--help 实测无 --message-file/stdin），
        # argv 暴露为已知限制（/proc 需同 UID 可见）；消息走临时文件 + base64
        # 传输（免 shell 引号劈碎），尾部换行用 sentinel 法原样保留
        # （命令替换会剥尾部 \n：cat 后拼 x 再 ${m%x} 还原）
        cmd = ('d=$(mktemp -t mh-kb.XXXXXX) && '
               f'printf %s {b64(message)} | base64 -d > "$d" && '
               'm=$(cat "$d"; printf x); m=${m%x}; '
               f'timeout {SEND_TIMEOUT_S} kylin-bot agent -m "$m" '
               '2>/dev/null; rm -f "$d"')
        cmd = self._sandboxed(cmd)
        sent = now_utc()
        t0 = time.time()
        rc, out, _ = self.ch.run(cmd, timeout=CHANNEL_TIMEOUT_S)
        text = _strip_logs(out)
        # R32：超时（timeout 杀进程 → rc=124）残句不是答案，不当 Reply 计分；
        # 后端故障文案过滤对齐 hermes（此前 kylinbot 无此层，同一上游故障
        # 在两台适配器产出不同语义）
        if rc == 124:
            raise AgentUnavailable(
                f"kylin-bot 超时({SEND_TIMEOUT_S}s)，残句不计分: {text[:120]}")
        if text and any(k in text for k in
                        ("API failed after", "Final error", "server error",
                         "502 Bad Gateway", "503 Service")):
            raise AgentUnavailable(f"kylin-bot 后端不可用: {text[:200]}")
        if not text:
            raise AgentUnavailable(f"kylin-bot 无有效回复(rc={rc}): {out.strip()[:200]}")
        return Reply(session_id=session_id, text=text,
                     sent_at=sent, reply_at=now_utc(),
                     latency_ms=elapsed_ms(t0), token_usage=None)

    def end_session(self, sid: str) -> None:
        pass  # agent 单发模式每次独立进程，无长会话

    def version_info(self) -> str | None:
        return vm_version_line(
            self.ch, "kylin-bot --version 2>/dev/null || kylin-bot -V 2>/dev/null")

    def dump_memory(self) -> MemorySnapshot:
        script = "python3 -c '" + _DUMP_SRC.replace("'", "'\\''") + "'"
        # R31：导出失败如实标注 dump_ok=False（规则层判运行无效）——
        # 折叠成空 entries 会把泄漏洗白成"没存"、verify_reset 形同虚设
        try:
            rows = self.ch.run_json(script)
        except RuntimeError as e:
            return MemorySnapshot(format="sqlite", dumped_at=now_utc(),
                                  entries=[], raw=None, dump_ok=False,
                                  error=str(e)[:200])
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

    def fs_snapshot(self) -> list[str] | None:
        """~ 用户区 + workspace 子树（R30：原 find 把 .kylinbot 整棵剪掉，
        agent 写进自家 workspace 的产物全被剪没——照 R24-openclaw 方案并入
        workspace，只排 memory/devices.db 等存储噪音）。归一化用远端 $HOME
        展开（防硬编码用户名，见 hermes.fs_snapshot 注）。"""
        cmd = ("(find ~ -maxdepth 4 \\( -name .hermes -o -name .cache -o -name .config "
               "-o -name node_modules -o -name .local -o -name .kylinbot \\) -prune -o "
               "-printf '%p\\n'; "
               "find ~/.kylinbot/workspace \\( -path '*/memory' -o -name devices.db "
               "\\) -prune -o -printf '%p\\n' 2>/dev/null) "
               "2>/dev/null | sed \"s|^$HOME|~|\" | sort -u")
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
