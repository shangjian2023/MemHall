"""契约 03 核心数据对象（pydantic）。

owner: C（评分）—— Evidence/Verdict 的权威定义在 docs/contracts/evidence-verdict.md，
本文件是其实现。契约与实现不一致以契约为准；改字段走契约变更流程（README.md §版本纪律）。
B 的 MemoryCase 模型在 models_case.py（B 管），此处不重复定义。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------- 枚举 ----------

class EvidenceType(str, Enum):
    """四类证据（design.md §5）。"""

    DIALOGUE = "dialogue"
    MEMORY_SNAPSHOT = "memory_snapshot"
    ACTIONS = "actions"
    FS_DIFF = "fs_diff"


class VerdictValue(str, Enum):
    """六态判定 + 运行无效（design.md §6.2，对齐评审细则原文）。"""

    CORRECT = "correct"              # 记对了
    OMISSION = "omission"            # 忘了
    CONFUSION = "confusion"          # 记混了
    FABRICATION = "fabrication"      # 记错了（瞎编）
    OVER_PERSIST = "over_persist"    # 不该记的记下了
    WRONG_REUSE = "wrong_reuse"      # 用错了
    INVALID_RUN = "invalid_run"      # 运行无效（不计分，单列）


class DecidedBy(str, Enum):
    """判定由谁做出（verdict 的 decided_by 字段）。"""

    RULE = "rule"
    JUDGE_A = "judge_a"
    JUDGE_B = "judge_b"
    ARBITRATION = "arbitration"
    HUMAN_REVIEW = "human_review"


# ---------- 对话（契约 03 §2.1 Reply）----------

class TokenUsage(BaseModel):
    prompt: Optional[int] = None
    completion: Optional[int] = None


class Reply(BaseModel):
    """adapter.send() 的返回。"""

    session_id: str
    text: str
    sent_at: datetime
    reply_at: datetime
    latency_ms: int
    token_usage: Optional[TokenUsage] = None


# ---------- 记忆快照（契约 03 §2.2 MemorySnapshot）----------

class MemoryEntry(BaseModel):
    entry_id: str
    content: str
    created_at: Optional[datetime] = None   # 智能体侧时间戳（写入时机测试用）
    source_turn: Optional[str] = None       # 能对上哪句对话就填


class MemoryRaw(BaseModel):
    path: str        # 原始文件落盘路径（evidence/<run_id>/raw/...）
    sha256: str


class MemorySnapshot(BaseModel):
    format: Literal["sqlite", "json", "files", "none"]
    dumped_at: datetime
    entries: list[MemoryEntry] = Field(default_factory=list)
    raw: Optional[MemoryRaw] = None


# ---------- 操作记录（契约 03 §2.3 Action）----------

class ActionSource(str, Enum):
    MCP_LOG = "mcp_log"          # ⚠️ W2 摸底验证（environment.md §7）
    AGENT_LOG = "agent_log"
    AUDITD = "auditd"            # 兜底


class Action(BaseModel):
    action_id: str
    ts: datetime
    tool: str                    # MCP 工具名或智能体日志里的调用名
    args: dict[str, Any] = Field(default_factory=dict)
    result: Optional[str] = None
    source: ActionSource


class ActionDump(BaseModel):
    """dump_actions() 的返回：条目列表 + 完整度（coverage 决定 wrong_reuse 类判定是否可信）。"""

    actions: list[Action] = Field(default_factory=list)
    coverage: Literal["full", "partial", "unknown"] = "unknown"


# ---------- 证据信封（契约 03 §1）----------

class EvidencePhase(str, Enum):
    INJECT = "inject"
    CONFOUND = "confound"
    PROBE = "probe"


class Evidence(BaseModel):
    """证据统一信封：四类证据各带专属 payload，公共字段在此。

    payload 的类型按 type 分发（runner 负责保证对应关系）：
      dialogue         -> list[Reply]
      memory_snapshot  -> MemorySnapshot
      actions          -> ActionDump
      fs_diff          -> FsDiff
    """

    evidence_id: str
    run_id: str
    case_id: str
    phase: EvidencePhase
    type: EvidenceType
    collected_at: datetime
    clock_offset_days: int = 0    # 模拟隔天时的系统时钟偏移（时间推理题判卷依据）
    payload: dict[str, Any]
    sha256: str                   # payload 序列化后的哈希指纹（runner 计算）


class FsDiffEntry(BaseModel):
    """fs_diff payload 的条目。"""

    path: str
    change: Literal["created", "modified", "deleted"]


class FsDiff(BaseModel):
    before_snapshot: str = ""     # 快照标识/哈希
    after_snapshot: str = ""
    entries: list[FsDiffEntry] = Field(default_factory=list)


# ---------- 判定（契约 03 §3 Verdict）----------

class JudgeMetaItem(BaseModel):
    model: str
    verdict: VerdictValue
    agreed: bool


class JudgeMeta(BaseModel):
    judge_a: Optional[JudgeMetaItem] = None
    judge_b: Optional[JudgeMetaItem] = None
    prompt_version: str = ""
    arbiter: Optional[str] = None   # 双判不一致时：rule | 第三模型 | None(转人工)


class Verdict(BaseModel):
    verdict_id: str
    probe_id: str
    case_id: str
    run_id: str
    verdict: VerdictValue
    confidence: float = 1.0         # 规则判定恒 1.0；judge 判定为 judge 自报 0-1
    decided_by: DecidedBy
    evidence_refs: list[str] = Field(default_factory=list)   # 报告下钻入口
    explanation: str = ""
    judge_meta: Optional[JudgeMeta] = None
