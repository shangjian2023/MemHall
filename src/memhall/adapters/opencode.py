"""OpenCodeAdapter —— 本机 opencode 智能体适配器（子进程驱动，无 VM）。

实测校准（2026-09-28 v1.18.31，Windows 本机）：
- send:   `opencode run -m memhall-gw/<model> "<msg>"` 无头单发，默认每次
          新会话（跨会话只靠落盘的 AGENTS.md / 工作区文件，正中考点）
- 隔离:   XDG_CONFIG_HOME / XDG_DATA_HOME 指向评测沙箱，配置、凭据、会话库
          与用户日常环境完全隔离；网关走 .env 的 AGENT_LLM_*（qwen3.7-plus），
          send 内置 10s 节流（网关 RPM 低，同 kylinbot）
- 记忆:   opencode 无内置长期记忆库——持久化靠 AGENTS.md（每会话自动加载）
          与工作区内笔记文件；dump_memory 读这两处
- 拨钟:   Windows 本机改系统时间需管理员且影响整机，不支持——temporal
          用例标运行无效（orchestrator 兜 AdapterError，套件继续）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from memhall.adapters.base import NO_WINDOW, AdapterError, AgentAdapter, AgentUnavailable
from memhall.schema.evidence import (
    ActionDump,
    MemoryEntry,
    MemorySnapshot,
    Reply,
)

_SEND_MIN_INTERVAL = float(os.environ.get("OPENCODE_SEND_INTERVAL", "10"))
_send_lock = threading.Lock()
_last_send = 0.0


def _send_throttle() -> None:
    global _last_send
    with _send_lock:
        wait = _SEND_MIN_INTERVAL - (time.monotonic() - _last_send)
        if wait > 0:
            time.sleep(wait)
        _last_send = time.monotonic()


class OpenCodeAdapter(AgentAdapter):
    """被测智能体 opencode（本机安装，XDG 沙箱隔离）。"""

    name = "opencode"

    def __init__(self, root: Path | None = None):
        self.root = (root or Path.home() / ".memhall" / "opencode-sandbox")
        self.cfg_dir = self.root / "xdg-config" / "opencode"
        self.data_dir = self.root / "xdg-data" / "opencode"
        self.workspace = self.root / "workspace"
        self._exe: str | None = None

    # ---------- 沙箱 ----------

    def _resolve_exe(self) -> str:
        if self._exe is None:
            from memhall.discovery import _which
            exe = _which("opencode")
            if not exe:
                raise AgentUnavailable(
                    "PATH 里找不到 opencode（npm i -g opencode-ai 后重开终端）")
            self._exe = exe
        return self._exe

    def _write_config(self) -> None:
        # 统一模型模式（GATEWAY_URL）优先：走本地网关，真凭据只在网关进程
        from memhall.gateway import gateway_settings
        gw = gateway_settings("opencode")
        base = (gw["base_url"] if gw
                else os.environ.get("AGENT_LLM_BASE_URL", "")).rstrip("/")
        key = gw["key"] if gw else os.environ.get("AGENT_LLM_KEY", "")
        self.model = (gw["model"] if gw
                      else os.environ.get("AGENT_LLM_MODEL", "qwen3.7-plus"))
        if not (base and key):
            raise AgentUnavailable("缺 AGENT_LLM_BASE_URL / AGENT_LLM_KEY（检查 .env）")
        self.model_ref = f"memhall-gw/{self.model}"
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg_dir / "opencode.json").write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            # R36：与其他适配器"不放行命令执行"对齐——bash 由 allow 改 deny，
            # 横评能力面一致（此前 opencode 独享 bash 通道属能力面不对齐）；
            # 差异记录在 dataset-card §8
            "permission": {"edit": "allow", "bash": "deny", "webfetch": "deny"},
            "provider": {
                "memhall-gw": {
                    "name": "MemHall Gateway",
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"apiKey": key, "baseURL": base},
                    "models": {self.model: {"name": self.model}},
                },
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _sandbox_env(self) -> dict:
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(self.root / "xdg-config")
        env["XDG_DATA_HOME"] = str(self.root / "xdg-data")
        return env

    # ---------- 契约 01 ----------

    def reset(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._write_config()

    def send(self, session_id: str, message: str) -> Reply:
        _send_throttle()
        sent = datetime.now(UTC)
        t0 = time.time()
        try:
            r = subprocess.run(
                [self._resolve_exe(), "run", "-m", self.model_ref, message],
                capture_output=True, encoding="utf-8", errors="replace",
                cwd=str(self.workspace), env=self._sandbox_env(), timeout=280,
                creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            raise AgentUnavailable(f"opencode 超时: {e}") from e
        text = _strip_noise(r.stdout or "")
        if not text:
            raise AgentUnavailable(
                f"opencode 无有效回复(rc={r.returncode}): "
                f"{(r.stdout or '')[:150]} | {(r.stderr or '')[:150]}")
        return Reply(session_id=session_id, text=text, sent_at=sent,
                     reply_at=datetime.now(UTC),
                     latency_ms=int((time.time() - t0) * 1000),
                     token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # 单发模式每次独立进程，无长会话

    def version_info(self) -> str | None:
        try:
            exe = self._resolve_exe()
        except Exception:  # noqa: BLE001 未装/未探测到 = 无版本元数据
            return None
        from memhall.adapters.base import cli_version
        return cli_version([exe, "--version"])


    def dump_memory(self) -> MemorySnapshot:
        entries: list[MemoryEntry] = []
        agents_md = self.workspace / "AGENTS.md"
        if agents_md.exists():
            mtime = datetime.fromtimestamp(agents_md.stat().st_mtime, UTC)
            entries.append(MemoryEntry(
                entry_id="agents-md",
                content=agents_md.read_text(encoding="utf-8", errors="replace")[:2000],
                created_at=mtime, source_turn="AGENTS.md"))
        for p in sorted(self.workspace.rglob("*")):
            if (p.is_file() and p != agents_md
                    and p.suffix.lower() in (".md", ".txt")
                    and not any(part.startswith(".") for part in p.parts)):
                rel = p.relative_to(self.workspace).as_posix()
                entries.append(MemoryEntry(
                    entry_id=f"file:{rel}",
                    content=f"[{rel}] "
                            + p.read_text(encoding="utf-8", errors="replace")[:500],
                    created_at=datetime.fromtimestamp(
                        p.stat().st_mtime, UTC),
                    source_turn=str(rel)))
        return MemorySnapshot(format="files",
                              dumped_at=datetime.now(UTC),
                              entries=entries, raw=None)

    def dump_actions(self) -> ActionDump:
        return ActionDump(actions=[], coverage="unknown")

    def fs_snapshot(self) -> list[str] | None:
        if not self.workspace.exists():
            return None
        out = []
        for p in sorted(self.workspace.rglob("*")):
            if (p.is_file()
                    and not any(part.startswith(".") for part in p.parts)):
                out.append(p.relative_to(self.workspace).as_posix())
        return out

    def clock_shift(self, days: int) -> None:
        if days == 0:
            return
        # Windows 本机不支持（管理员权限 + 影响整机时钟）；
        # orchestrator 捕获 AdapterError → 该 case 标运行无效，套件继续
        raise AdapterError(
            "本机 Windows 不支持拨钟——temporal 用例请在 openKylin VM 评测")

    def clock_restore(self) -> None:
        pass


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_noise(out: str) -> str:
    """去 ANSI 转义与空行；输出形态以实测为准再校准。"""
    clean = _ANSI.sub("", out)
    return "\n".join(ln for ln in clean.splitlines() if ln.strip()).strip()
