"""KylinBot 适配器：brain.db → 统一 MemorySnapshot。

真实存储（openKylin 3.0 实测，2026-09-23）：
- 记忆库：~/.kylinbot/workspace/memory/brain.db（SQLite + FTS5 + embedding_cache）
- 核心表 memories(id, key UNIQUE, content, category, embedding,
              created_at, updated_at, session_id, namespace, importance, superseded_by)
- superseded_by 是动态更新的版本链证据；session_id 是记忆来源会话证据
- 会话库：~/.kylinbot/workspace/sessions/sessions.db
- CLI 取证：kylin-bot memory list / stats（适合 runner 调用）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scoring.models import MemoryEntry, MemorySnapshot

DEFAULT_BRAIN_DB = Path.home() / ".kylinbot/workspace/memory/brain.db"


def load_snapshot(brain_db: str | Path = DEFAULT_BRAIN_DB,
                  include_superseded: bool = True) -> MemorySnapshot:
    """把 KylinBot 的 brain.db 归一为评测用 MemorySnapshot。

    include_superseded=False 时只取当前有效记忆（superseded_by 为空的），
    用于 behavior 判定；动态更新类评测必须用 True 保留版本链证据。
    """
    brain_db = Path(brain_db)
    if not brain_db.exists():
        return MemorySnapshot(agent="kylinbot", captured_at="", store_hash="missing", entries=[])

    conn = sqlite3.connect(f"file:{brain_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, key, content, category, session_id, importance, superseded_by "
            "FROM memories ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    entries = []
    for mid, key, content, category, session_id, importance, superseded_by in rows:
        if not include_superseded and superseded_by:
            continue
        source = f"brain.db:memories[{key}]"
        if superseded_by:
            content = f"[已被 {superseded_by} 取代] {content}"
        entries.append(MemoryEntry(id=mid, text=f"[{category}] {content}", source=source))
    return MemorySnapshot(
        agent="kylinbot",
        captured_at="",
        store_hash=f"brain.db:{len(entries)}entries",
        entries=entries,
    )
