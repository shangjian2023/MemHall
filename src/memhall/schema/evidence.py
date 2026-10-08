"""契约 03 核心数据对象（pydantic）。

owner: C（评分）—— Evidence/Verdict 的权威定义在 docs/contracts/evidence-verdict.md，
本文件是其实现。契约与实现不一致以契约为准；改字段走契约变更流程（README.md §版本纪律）。
B 的 MemoryCase 模型在 models_case.py（B 管），此处不重复定义。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

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
    HUMAN_REVIEW = "human_review"    # 判卷未决（脚本判不了且无 LLM judge，转人工；不计分）


class DecidedBy(str, Enum):
    """判定由谁做出（verdict 的 decided_by 字段）。

    scripted（R27）：离线脚本判卷的判定——与 LLM judge 的 judge_a/judge_b/
    arbitration 区分，第三方审计不再把 scripted run 误读为 LLM 判卷。
    """

    RULE = "rule"
    SCRIPTED = "scripted"
    JUDGE_A = "judge_a"
    JUDGE_B = "judge_b"
    ARBITRATION = "arbitration"
    HUMAN_REVIEW = "human_review"
    HUMAN = "human"  # 人工复核入口裁决（原判定 human_review → 人的最终决定）


# ---------- 对话（契约 03 §2.1 Reply）----------

class TokenUsage(BaseModel):
    prompt: int | None = None
    completion: int | None = None


class Reply(BaseModel):
    """adapter.send() 的返回。"""

    session_id: str
    text: str
    sent_at: datetime
    reply_at: datetime
    latency_ms: int
    token_usage: TokenUsage | None = None


# ---------- 记忆快照（契约 03 §2.2 MemorySnapshot）----------

class MemoryEntry(BaseModel):
    entry_id: str
    content: str
    created_at: datetime | None = None   # 智能体侧时间戳（写入时机测试用）
    source_turn: str | None = None       # 能对上哪句对话就填


class MemoryRaw(BaseModel):
    path: str        # 原始文件落盘路径（evidence/<run_id>/raw/...）
    sha256: str


class MemorySnapshot(BaseModel):
    format: Literal["sqlite", "json", "files", "none"]
    dumped_at: datetime
    entries: list[MemoryEntry] = Field(default_factory=list)
    raw: MemoryRaw | None = None
    # R31：导出成败与截断如实标注——"导出失败"折叠成空 entries 会把
    # canary 泄漏洗白成"没存"；规则层见 dump_ok=False 判 EvidenceMissing
    # （运行无效），不再与"真空库"混淆
    dump_ok: bool = True
    error: str = ""
    # R58：导出被 limit 截断（openclaw 400 条门）——大容量注入题证据
    # 可能不完整，报告层可见，不静默
    truncated: bool = False


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
    result: str | None = None
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
    """fs_diff payload 的条目。

    stage（R50）：创建/删除发生的阶段窗（inject=base→inject 后首采、
    probe=inject 后→终采）；旧证据无此字段按 "" 解析=不区分阶段。
    """

    path: str
    change: Literal["created", "modified", "deleted"]
    stage: str = ""


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
    judge_a: JudgeMetaItem | None = None
    judge_b: JudgeMetaItem | None = None
    prompt_version: str = ""
    arbiter: str | None = None   # 双判不一致时：rule | 第三模型 | None(转人工)


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
    judge_meta: JudgeMeta | None = None
    # degraded（P0-2，C 角色队友复核 2026-10-05）：LLM judge 端点故障窗口里
    # 脚本兜底的判定。判定值与溯源保留（人工复核可提速），但不作为正式
    # 分数——metrics 剔除计分、保守下界按错计。正常离线 scripted run 不置位。
    degraded: bool = False
