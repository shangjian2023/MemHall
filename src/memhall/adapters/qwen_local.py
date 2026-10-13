"""LocalQwenAdapter —— Qwen Code CLI 本机适配器（子进程驱动，win/linux 通用）。

- 沙箱:   QWEN_HOME 指向评测沙箱（官方支持的环境变量，等价 HERMES_HOME 的思路），
          settings.json 从 ~/.qwen 拷贝并【剥掉 hooks】——用户配置里挂着 clawd-on-desk
          全家桶钩子，不剥会给评测灌假活跃信号
- 认证:   沙箱 settings.json 自带（用户的 modelProviders/envKey 机制原样生效）
- send:   qwen "<msg>" --approval-mode auto-edit（自动批文件编辑=可写记忆，不放行命令）
- 记忆:   QWEN_HOME/QWEN.md（全局）+ 工作区 QWEN.md（项目）两处
          （auto-memory 实测 2026-09-29 因网关 500 未完成，恢复后复核）
- 拨钟:   本机不支持
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from memhall.adapters.base import (
    NO_WINDOW,
    SEND_TIMEOUT_S,
    AgentAdapter,
    AgentUnavailable,
    workspace_fs_snapshot,
)
from memhall.schema.evidence import MemoryEntry, MemorySnapshot, Reply


class LocalQwenAdapter(AgentAdapter):
    """被测智能体 Qwen Code CLI（本机安装，QWEN_HOME 沙箱隔离）。"""

    name = "qwen-local"
    send_interval_env = "QWEN_SEND_INTERVAL"
    send_interval_default = 10.0

    def __init__(self, root: Path | None = None):
        self.root = root or Path.home() / ".memhall" / "qwen-sandbox"
        self.qwen_home = self.root / "qwen-home"       # QWEN_HOME 指向这
        self.workspace = self.root / "workspace"
        self._exe: str | None = None

    # ---------- 沙箱 ----------

    def _resolve_exe(self) -> str:
        if self._exe is None:
            from memhall.discovery import ADAPTER_CLI, find_cli
            exe = find_cli(*ADAPTER_CLI["qwen"])
            if not exe:
                raise AgentUnavailable("PATH 与 ~/.local/bin 均找不到 qwen（npm i -g @qwen-code/qwen-code）")
            self._exe = exe
        return self._exe

    def _settings_src(self) -> Path:
        return Path.home() / ".qwen" / "settings.json"

    def reset(self) -> None:
        src = self._settings_src()
        if not src.is_file():
            raise AgentUnavailable(
                f"qwen 未配置：{src} 不存在（先跑一次 qwen 完成网关配置）")
        data = json.loads(src.read_text(encoding="utf-8"))
        data.pop("hooks", None)  # clawd-on-desk 钩子不进评测沙箱
        # 统一模型模式（GATEWAY_URL）：provider baseUrl 改指网关，envKey 由沙箱
        # env 注入 dummy key——流量必经网关，真凭据只在网关进程
        from memhall.gateway import gateway_settings
        gw = gateway_settings("qwen-local")
        model = (gw["model"] if gw
                 else os.environ.get("AGENT_LLM_MODEL", ""))  # 与 hermes/kylinbot 同一网关口径
        if model:
            data.setdefault("model", {})["name"] = model
        self._gw_env: dict[str, str] = {}
        for provs in data.get("modelProviders", {}).values():
            for prov in (provs if isinstance(provs, list) else [provs]):
                if model:
                    prov["id"] = model
                if gw and prov.get("baseUrl") is not None:
                    prov["baseUrl"] = gw["base_url"]
                if gw and prov.get("envKey"):
                    self._gw_env[prov["envKey"]] = gw["key"]
        shutil.rmtree(self.root, ignore_errors=True)
        self.qwen_home.mkdir(parents=True, exist_ok=True)
        (self.qwen_home / "settings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _sandbox_env(self) -> dict:
        env = os.environ.copy()
        env["QWEN_HOME"] = str(self.qwen_home)
        env.update(getattr(self, "_gw_env", {}))
        return env

    # ---------- 契约 01 ----------

    def send(self, session_id: str, message: str) -> Reply:
        self._send_throttle()
        sent = datetime.now(UTC)
        t0 = time.time()
        try:
            r = subprocess.run(
                [self._resolve_exe(), message, "--approval-mode", "auto-edit"],
                capture_output=True, encoding="utf-8", errors="replace",
                cwd=str(self.workspace), env=self._sandbox_env(),
                timeout=SEND_TIMEOUT_S, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            raise AgentUnavailable(f"qwen 超时: {e}") from e
        text = (r.stdout or "").strip()
        if not text or text.startswith(("[API Error", "Error:")):
            raise AgentUnavailable(
                f"qwen 无有效回复(rc={r.returncode}): {text[:150]} | {(r.stderr or '')[:150]}")
        return Reply(session_id=session_id, text=text, sent_at=sent,
                     reply_at=datetime.now(UTC),
                     latency_ms=int((time.time() - t0) * 1000),
                     token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # 一次性 prompt 每次独立进程，会话隔离天然成立

    def version_info(self) -> str | None:
        try:
            exe = self._resolve_exe()
        except Exception:  # noqa: BLE001 未装/未探测到 = 无版本元数据
            return None
        from memhall.adapters.base import cli_version
        return cli_version([exe, "--version"])


    def dump_memory(self) -> MemorySnapshot:
        """全局 QWEN.md + 工作区 QWEN.md（+ 备未来 auto-memory 目录）合并。"""
        entries: list[MemoryEntry] = []
        sources = [self.qwen_home / "QWEN.md", self.workspace / "QWEN.md"]
        mem_dir = self.qwen_home / "memories"
        if mem_dir.is_dir():
            sources.extend(sorted(mem_dir.rglob("*.md")))
        seen: set[str] = set()
        for f in sources:
            if not f.is_file():
                continue
            rel = f.relative_to(self.root).as_posix()
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                # R58 对齐 hermes.py：只剥"- "/"* "形态的列表标记，不再
                # lstrip("-* ") 误伤"-3°C"类正文首字符
                s = re.sub(r"^[-*]\s+", "", line.strip()).strip()
                if not s or s.startswith("#") or s in seen:
                    continue
                seen.add(s)
                entries.append(MemoryEntry(
                    entry_id=f"m-{len(entries):04d}", content=s,
                    created_at=None, source_turn=rel))
        return MemorySnapshot(format="files",
                              dumped_at=datetime.now(UTC),
                              entries=entries, raw=None)

    def fs_snapshot(self) -> list[str] | None:
        return workspace_fs_snapshot(self.workspace)
