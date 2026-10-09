"""LocalClaudeAdapter —— Claude Code 本机适配器（子进程驱动，win/linux 通用）。

2026-09-29 win 实测：CLAUDE_CONFIG_DIR 指向沙箱后 auto-memory 照常工作——
teach 写 <config>/projects/<slug>/memory/*.md + MEMORY.md 索引，
新进程（-p oneshot，每次独立进程=天然跨会话）召回正常。
openKylin 侧：npm 装 claude-code 后同一套代码可用，
认证走公网 anthropic 协议端点（如 bigmodel），与 15721 本机代理无关。

- 认证:   CLAUDE_LLM_BASE_URL/CLAUDE_LLM_KEY（anthropic 协议）优先；
          缺省回落进程环境里已有的 ANTHROPIC_BASE_URL/AUTH_TOKEN（从已配置 shell 继承）
- send:   claude -p <msg> --output-format text --permission-mode acceptEdits
          （acceptEdits=放行记忆/工作区文件写入，不放行命令执行）
- 记忆:   projects/*/memory/*.md（auto-memory）+ 工作区 CLAUDE.md（项目记忆）
- 拨钟:   本机不支持（orchestrator 兜 AdapterError，temporal 用例标无效）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from memhall.adapters.base import NO_WINDOW, AdapterError, AgentAdapter, AgentUnavailable
from memhall.schema.evidence import ActionDump, MemoryEntry, MemorySnapshot, Reply

_SEND_MIN_INTERVAL = float(os.environ.get("CLAUDE_SEND_INTERVAL", "2"))
_send_lock = threading.Lock()
_last_send = 0.0


def _send_throttle() -> None:
    global _last_send
    with _send_lock:
        wait = _SEND_MIN_INTERVAL - (time.monotonic() - _last_send)
        if wait > 0:
            time.sleep(wait)
        _last_send = time.monotonic()


class LocalClaudeAdapter(AgentAdapter):
    """被测智能体 Claude Code（本机安装，CLAUDE_CONFIG_DIR 沙箱隔离）。"""

    name = "claude-local"

    def __init__(self, root: Path | None = None):
        self.root = root or Path.home() / ".memhall" / "claude-sandbox"
        self.config_dir = self.root / "claude-home"   # CLAUDE_CONFIG_DIR 指向这
        self.workspace = self.root / "workspace"
        self._exe: str | None = None

    # ---------- 沙箱 ----------

    def _resolve_exe(self) -> str:
        if self._exe is None:
            from memhall.discovery import ADAPTER_CLI, find_cli
            exe = find_cli(*ADAPTER_CLI["claude"])
            if not exe:
                raise AgentUnavailable(
                    "PATH 与 ~/.local/bin 均找不到 claude"
                    "（npm i -g @anthropic-ai/claude-code）")
            self._exe = exe
        return self._exe

    def _sandbox_env(self) -> dict:
        env = os.environ.copy()
        from memhall.gateway import gateway_settings
        exempt = "claude-local" in {
            s.strip() for s in os.environ.get("GATEWAY_EXEMPT", "").split(",")
            if s.strip()}
        if gateway_settings("claude-local") and not exempt:
            # 显式拒绝而非静默绕过：统一对照的车道里混进不同后端 = 口径污染。
            # GATEWAY_EXEMPT 是用户明示的混合形态豁免（openKylin 原生装机
            # 同时要网关车道与 claude 直连，2026-10-09）：manifest 照记
            # claude 自己的 model_backend，compare 口径告警可对账
            raise AgentUnavailable(
                "统一模型网关 v1 仅 OpenAI 协议面，claude-local 走 anthropic 协议"
                "进不来——设 GATEWAY_EXEMPT=claude-local 明示混合形态，"
                "或 unset GATEWAY_URL 后另行评测")
        base = os.environ.get("CLAUDE_LLM_BASE_URL", "").rstrip("/")
        key = os.environ.get("CLAUDE_LLM_KEY", "")
        if base and key:
            env["ANTHROPIC_BASE_URL"] = base
            env["ANTHROPIC_AUTH_TOKEN"] = key
        elif not (env.get("ANTHROPIC_BASE_URL") and env.get("ANTHROPIC_AUTH_TOKEN")):
            raise AgentUnavailable(
                "缺 claude 认证：设 CLAUDE_LLM_BASE_URL/CLAUDE_LLM_KEY"
                "（anthropic 协议端点），或在已配置 ANTHROPIC_* 的 shell 里跑")
        # 注意不映射 AGENT_LLM_MODEL：那是 openai 网关的模型名，claude 不认
        model = os.environ.get("CLAUDE_LLM_MODEL", "")
        if model:
            for tier in ("SONNET", "OPUS", "HAIKU", "FABLE"):
                env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = model
        env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        # openKylin 侧 node 与 claude 同居 ~/.local/bin（npm 布局），服务进程
        # 的 PATH 未必含它——claude 包装脚本的 #!/usr/bin/env node 会扑空
        # （2026-10-09 VM 实测 which claude 空、node --version 127）。pnpm/
        # volta 的 shim 还会自己再找 env node，claude 旁边不一定有——两处
        # 都前置：claude 所在目录 + node 所在目录（布局清单见 discovery）。
        # 非致命：CLI 没装时不在这里炸（认证/网关拒绝仍在前面优先报）
        from memhall.discovery import ADAPTER_CLI, find_cli
        prepend: set[str] = set()
        for cands in (ADAPTER_CLI["claude"], ADAPTER_CLI["node"]):
            hit = find_cli(*cands)
            if hit:
                prepend.add(str(Path(hit).resolve().parent))
        if prepend:
            env["PATH"] = os.pathsep.join([*sorted(prepend), env.get("PATH", "")])
        return env

    # ---------- 契约 01 ----------

    def reset(self) -> None:
        self._sandbox_env()  # 认证缺失提前失败，别等 send 才炸
        shutil.rmtree(self.root, ignore_errors=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def send(self, session_id: str, message: str) -> Reply:
        _send_throttle()
        sent = datetime.now(UTC)
        t0 = time.time()
        try:
            r = subprocess.run(
                [self._resolve_exe(), "-p", message,
                 "--output-format", "text", "--permission-mode", "acceptEdits"],
                capture_output=True, encoding="utf-8", errors="replace",
                cwd=str(self.workspace), env=self._sandbox_env(),
                timeout=280, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            raise AgentUnavailable(f"claude 超时: {e}") from e
        text = (r.stdout or "").strip()
        if not text or r.returncode != 0 or text.startswith(("API Error", "Error:")):
            raise AgentUnavailable(
                f"claude 无有效回复(rc={r.returncode}): {text[:150]} | {(r.stderr or '')[:150]}")
        return Reply(session_id=session_id, text=text, sent_at=sent,
                     reply_at=datetime.now(UTC),
                     latency_ms=int((time.time() - t0) * 1000),
                     token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # -p oneshot 每次独立进程，会话隔离天然成立

    def version_info(self) -> str | None:
        try:
            exe = self._resolve_exe()
        except Exception:  # noqa: BLE001 未装/未探测到 = 无版本元数据
            return None
        from memhall.adapters.base import cli_version
        return cli_version([exe, "--version"])


    def dump_memory(self) -> MemorySnapshot:
        """auto-memory（按项目路径编码分目录）+ 工作区 CLAUDE.md 两处合并。"""
        entries: list[MemoryEntry] = []
        sources: list[Path] = []
        mem_root = self.config_dir / "projects"
        if mem_root.is_dir():
            sources.extend(sorted(mem_root.rglob("*.md")))
        cl = self.workspace / "CLAUDE.md"
        if cl.is_file():
            sources.append(cl)
        seen: set[str] = set()
        for f in sources:
            rel = f.relative_to(self.root).as_posix()
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip().lstrip("-* ").strip()
                if not s or s.startswith("#"):
                    continue
                if s in seen:  # MEMORY.md 索引行与主题文件正文会重复
                    continue
                seen.add(s)
                entries.append(MemoryEntry(
                    entry_id=f"m-{len(entries):04d}", content=s,
                    created_at=None, source_turn=rel))
        return MemorySnapshot(format="files",
                              dumped_at=datetime.now(UTC),
                              entries=entries, raw=None)

    def dump_actions(self) -> ActionDump:
        return ActionDump(actions=[], coverage="unknown")

    def fs_snapshot(self) -> list[str] | None:
        """workspace 清单，路径归一 ~/ 前缀（R24：fs 断言写 ~/dev/src/demo，
        相对路径永不匹配——workspace 就是本适配器的"用户区"）。"""
        if not self.workspace.exists():
            return None
        out = []
        for p in sorted(self.workspace.rglob("*")):
            if (p.is_file()
                    and not any(part.startswith(".") for part in p.parts)):
                out.append("~/" + p.relative_to(self.workspace).as_posix())
        return out

    def clock_shift(self, days: int) -> None:
        if days == 0:
            return
        raise AdapterError("本机不支持拨钟——temporal 用例请在 openKylin VM 评测")

    def clock_restore(self) -> None:
        pass
