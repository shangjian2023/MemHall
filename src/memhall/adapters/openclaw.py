"""OpenClawAdapter —— OpenClaw 适配器（SSH 远程驱动，跑在 openKylin VM 里）。

实测对接方式（2026-10-03 VM 联调定案，OpenClaw 2026.9.8）：
- send:    `openclaw agent exec --config <sandbox>/openclaw.json --state-dir <sandbox>
           --model memhall-gw/<m> --message-file <tmp> --json --timeout N`
           —— exec 是官方无头单回合通道：每次独立进程=天然跨会话，长期记忆
           只靠 sqlite 记忆库（memory_search 跨会话召回实测通），正中考点；
           回复取 --json 信封的 final 字段（ok=false 或空 final → AgentUnavailable）
- 记忆:    <sandbox>/agents/main/agent/openclaw-agent.sqlite 的
           memory_index_chunks(path, source, start_line, text)——
           memory 源 = MEMORY.md 虚拟文件（不落磁盘）、sessions 源 = 会话转录，
           memory_search 检索的就是这些 chunk（证据与被检索面一致）
- reset:   rm -rf 沙箱目录整体重建（配置+workspace 一并在内，记忆归零彻底；
           沙箱固定 ~/.memhall-openclaw，绝不碰用户真实 ~/.openclaw）
- LLM:     沙箱配置固定 provider memhall-gw：统一网关（GATEWAY_VM_URL，记账
           归因 memhall-openclaw）优先，直连退 AGENT_LLM_*（第三方用户自带
           key 的可移植路径）；两者都没有 = 未配置，reset 显式报错
"""

from __future__ import annotations

import json
import os
import threading
import time

from memhall.adapters.base import AdapterError, AgentAdapter, AgentUnavailable
from memhall.adapters.remote import SshChannel, elapsed_ms, now_utc
from memhall.schema.evidence import (
    ActionDump,
    MemoryEntry,
    MemorySnapshot,
    Reply,
)

STATE_DIR = "~/.memhall-openclaw"
AGENT_DB = STATE_DIR + "/agents/main/agent/openclaw-agent.sqlite"
OPENCLAW_BIN = "~/.local/bin/openclaw"  # npm 全局前缀 ~/.local（PATH 兜底在命令里带）
EVAL_WORKDIRS = ["~/dev", "~/work", "~/proj", "~/docs", "~/notes",
                 "~/out", "~/scripts", "~/templates", "~/demo"]

# 上游 RPM 低压节奏（同 kylinbot 先例）；一轮消息 ~3 次上游调用
_SEND_MIN_INTERVAL = float(os.environ.get("OPENCLAW_SEND_INTERVAL", "5"))
_send_lock = threading.Lock()
_last_send = 0.0

# R37：沙箱模型参数可外置——评测器替被测者硬编码 contextWindow/maxTokens
# 属于"适配器替被测者做配置选择"，默认值对齐当前实测，可用环境变量覆盖
_OC_CONTEXT = int(os.environ.get("OPENCLAW_CONTEXT_WINDOW", "131072"))
_OC_MAX_TOKENS = int(os.environ.get("OPENCLAW_MAX_TOKENS", "8192"))


def _send_throttle() -> None:
    global _last_send
    with _send_lock:
        wait = _SEND_MIN_INTERVAL - (time.monotonic() - _last_send)
        if wait > 0:
            time.sleep(wait)
        _last_send = time.monotonic()


# sqlite 记忆导出（read-only 连接；列序见模块 docstring）
# R58：导出截断如实上报——count(*) 对照 limit，超限标 truncated
_DUMP_SRC = (
    "import json,sqlite3,os\n"
    f"db=os.path.expanduser('{AGENT_DB}')\n"
    "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True)\n"
    "rows=c.execute(\"select path,source,start_line,text from memory_index_chunks "
    "order by path,start_line limit 401\").fetchall()\n"
    "print(json.dumps(rows,ensure_ascii=False))\n"
)


class OpenClawAdapter(AgentAdapter):
    """被测智能体 OpenClaw（VM 内 ~ 用户级 npm 安装，内置 sqlite 记忆）。"""

    name = "openclaw"

    def __init__(self, channel: SshChannel | None = None):
        self.ch = channel or SshChannel()
        self._clock_epoch: int | None = None
        self._model = ""
        self._provider_model = ""

    def _llm_settings(self) -> dict:
        from memhall.gateway import gateway_settings
        gw = gateway_settings("openclaw", vm_lane=True)
        if gw:
            return gw
        url = os.environ.get("AGENT_LLM_BASE_URL", "").strip()
        key = os.environ.get("AGENT_LLM_KEY", "").strip()
        model = os.environ.get("AGENT_LLM_MODEL", "").strip()
        if url and key and model:
            return {"base_url": url.rstrip("/"), "key": key, "model": model}
        raise RuntimeError(
            "openclaw 沙箱需要 LLM 端点：配 GATEWAY_VM_URL/GATEWAY_URL（统一网关）"
            "或 AGENT_LLM_BASE_URL/KEY/MODEL（自带 key 直连），见 .env.example")

    def _config_json(self, s: dict) -> str:
        """沙箱 openclaw.json：单一 provider，mode=replace 隔绝环境配置漂移。"""
        return json.dumps({
            "models": {
                "mode": "replace",
                "providers": {
                    "memhall-gw": {
                        "baseUrl": s["base_url"],
                        "apiKey": s["key"],
                        "api": "openai-completions",
                        "models": [{
                            "id": s["model"],
                            "name": s["model"],
                            "reasoning": False,
                            "input": ["text"],
                            "contextWindow": _OC_CONTEXT,
                            "maxTokens": _OC_MAX_TOKENS,
                            "cost": {"input": 0, "output": 0,
                                     "cacheRead": 0, "cacheWrite": 0},
                        }],
                    },
                },
            },
            "agents": {"defaults": {
                "model": f"memhall-gw/{s['model']}",
                "workspace": STATE_DIR + "/workspace",
            }},
        }, ensure_ascii=False, indent=2)

    def reset(self) -> None:
        s = self._llm_settings()
        self._model = s["model"]
        self._provider_model = f"memhall-gw/{s['model']}"
        # 整目录重建 = 记忆/会话/工作区全归零（沙箱自有目录，不碰真实 ~/.openclaw）
        rc, _, err = self.ch.run(
            f"rm -rf {STATE_DIR} && mkdir -p {STATE_DIR}/workspace", timeout=60)
        if rc != 0:
            raise RuntimeError(f"openclaw 沙箱重建失败: {err.strip()[:200]}")
        # 配置走 stdin 落盘（含 key，无论真假都不上命令行；600 权限）
        rc, _, err = self.ch.run(
            f"cat > {STATE_DIR}/openclaw.json && chmod 600 {STATE_DIR}/openclaw.json",
            stdin_data=self._config_json(s), timeout=30)
        if rc != 0:
            raise RuntimeError(f"openclaw 沙箱配置写入失败: {err.strip()[:200]}")
        # 用例注入的虚构工作区顺带清掉（防跨轮污染）
        self.ch.run("rm -rf " + " ".join(EVAL_WORKDIRS), timeout=30)

    def send(self, session_id: str, message: str) -> Reply:
        _send_throttle()
        # 消息体 stdin → 临时文件 → --message-file（不上命令行、免转义）
        script = (
            'd=$(mktemp -t mh-oc.XXXXXX) && cat > "$d" && '
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"timeout 280 {OPENCLAW_BIN} agent exec "
            f"--config {STATE_DIR}/openclaw.json "
            f"--state-dir {STATE_DIR} "
            f"--model {self._provider_model} "
            f'--message-file "$d" --json --timeout 260 2>/dev/null; '
            'rc=$?; rm -f "$d"; exit $rc'
        )
        sent = now_utc()
        t0 = time.time()
        rc, out, err = self.ch.run(script, timeout=300, stdin_data=message)
        try:
            env = json.loads(out.strip() or "{}")
        except json.JSONDecodeError:
            raise AgentUnavailable(
                f"openclaw 输出非 JSON(rc={rc}): {(out or err).strip()[:200]}") from None
        if not env.get("ok"):
            msg = (env.get("error") or {}).get("message", "")[:200]
            raise AgentUnavailable(f"openclaw 回合失败(rc={rc}): {msg or out.strip()[:150]}")
        text = (env.get("final") or "").strip()
        if not text:
            raise AgentUnavailable(f"openclaw 回复为空(rc={rc}): {out.strip()[:150]}")
        return Reply(session_id=session_id, text=text,
                     sent_at=sent, reply_at=now_utc(),
                     latency_ms=elapsed_ms(t0), token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # agent exec 每次独立进程，无长会话

    def dump_memory(self) -> MemorySnapshot:
        script = "python3 -c '" + _DUMP_SRC.replace("'", "'\\''") + "'"
        # R31：导出失败如实标注 dump_ok=False（规则层判运行无效），
        # 不再折叠成空 entries（canary 泄漏洗白面）
        try:
            rows = self.ch.run_json(script)
        except RuntimeError as e:
            return MemorySnapshot(format="sqlite", dumped_at=now_utc(),
                                  entries=[], raw=None, dump_ok=False,
                                  error=str(e)[:200])
        truncated = len(rows) > 400   # R58：limit 401 探测超限
        entries = [
            MemoryEntry(entry_id=f"c{rowid}", content=text,
                        created_at=None, source_turn=path)
            for rowid, (path, _source, _line, text) in enumerate(rows[:400], 1)
            if text.strip()
        ]
        return MemorySnapshot(format="sqlite", dumped_at=now_utc(),
                              entries=entries, raw=None, truncated=truncated)

    def dump_actions(self) -> ActionDump:
        return ActionDump(actions=[], coverage="unknown")

    def version_info(self) -> str | None:
        rc, out, _ = self.ch.run(
            f"export PATH=$HOME/.local/bin:$PATH && {OPENCLAW_BIN} --version",
            timeout=30)
        line = out.strip().splitlines()[0] if out.strip() else ""
        return line or None

    def clock_shift(self, days: int) -> None:
        """VM 拨钟（sudo date -s），记录原时刻供恢复。
        R33：失败抛 AdapterError——orchestrator 只兜 AdapterError。"""
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
        """R33：恢复校验 rc，失败暴露（时钟残留会污染后续所有 case）。"""
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
        """~ 用户区 + 沙箱 workspace 子树（R24：chain 任务建在沙箱内也要被
        fs_diff 看见——剪枝 .memhall-openclaw 只为排除配置/sqlite 噪音，
        不能把智能体的写入面一起剪掉）。"""
        cmd = ("(find ~ -maxdepth 4 \\( -name .hermes -o -name .cache -o -name .config "
               "-o -name node_modules -o -name .local -o -name .kylinbot "
               "-o -name .openclaw -o -name .memhall-openclaw -o -name .memhall \\) -prune -o "
               "-printf '%p\\n'; "
               "find ~/.memhall-openclaw/workspace -maxdepth 6 -printf '%p\\n' 2>/dev/null) "
               "2>/dev/null | sed \"s|^$HOME|~|\" | sort -u")
        rc, out, _ = self.ch.run(cmd, timeout=60)
        return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else None
