"""okim-bench 评分子系统：数据模型。

对应设计文档 §2 case schema / §3.2 MemorySnapshot / §4 五类错误判定。
所有评分输出统一为 ScoreResult，必须携带 evidence_refs（无证据不得分）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class ErrorType(str, Enum):
    """赛题要求的五类判定 + 内部正确态。"""

    CORRECT = "correct"                      # 正确记忆/正确复用
    OMISSION = "omission"                    # 遗漏：该记的没记 / 该用的没用
    CONFUSION = "confusion"                  # 混淆：相近信息张冠李戴
    FALSE_PERSISTENCE = "false_persistence"  # 错误持久化：不该记的记了
    FALSE_REUSE = "false_reuse"              # 错误复用：用了过期/被覆盖的旧值


class Capability(str, Enum):
    """六维能力（雷达图六轴）。"""

    RETENTION = "retention"            # 长期保持
    RECALL = "recall"                  # 记忆调用
    UPDATE = "update"                  # 动态更新
    DISCRIMINATION = "discrimination"  # 相近区分
    BOUNDARY = "boundary"              # 边界识别
    REUSE = "reuse"                    # 任务复用


@dataclass
class MemoryEntry:
    """统一记忆中间格式中的一条记忆（设计文档 §3.2）。"""

    id: str
    text: str
    source: str = ""  # 如 "MEMORY.md:L12"，用于 evidence_refs


@dataclass
class MemorySnapshot:
    agent: str
    captured_at: str
    store_hash: str
    entries: list[MemoryEntry] = field(default_factory=list)


@dataclass
class ProbeSpec:
    """一个探测项的评分契约。"""

    probe_id: str
    kind: str  # memory_store_check | qa | task
    capability: str
    question: str = ""
    ground_truth: str = ""                       # 客观答案（qa）
    expect_store_retain: list[str] = field(default_factory=list)   # 必须存在的模式
    expect_store_absent: list[str] = field(default_factory=list)   # 不得存在的模式
    distractor_patterns: list[str] = field(default_factory=list)   # 干扰项模式（命中=混淆）
    outdated_answers: list[str] = field(default_factory=list)      # 过期答案（命中=错误复用）
    expect_abstention: bool = False              # 应拒答（测边界/未知信息）
    artifact_assertions: list[dict] = field(default_factory=list)  # task: [{path, contains?, sha256?}]
    judge_required: bool = False                 # L1/L2 判不了时升级双 judge


@dataclass
class EvidenceBundle:
    """一次 probe 评分的全部证据（评分器唯一输入，不接触 agent）。"""

    case_id: str
    probe_id: str
    memory_after: Optional[MemorySnapshot] = None
    answer: str = ""                 # qa 回答原文
    artifacts: dict[str, str] = field(default_factory=dict)  # path -> content（task 产物文本）
    transcript_refs: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    """统一评分输出：verdict + 置信 + 证据引用 + 理由。"""

    probe_id: str
    error_type: ErrorType
    confidence: float
    evidence_refs: list[str]
    reason: str
    scored_by: str = "L1"            # L1 | L2 | JUDGE | ARBITER
    judge_a: Optional[str] = None
    judge_b: Optional[str] = None
    judge_a_verdict: Optional[str] = None   # 评委A原始判定（一致率统计用）
    judge_b_verdict: Optional[str] = None
    needs_review: bool = False       # 评委不一致且仲裁仍存疑 → 人工复核闸门

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["error_type"] = self.error_type.value
        return d


@dataclass
class JudgeVerdict:
    """单个 judge 的结构化输出。"""

    verdict: str          # correct | omission | confusion | false_persistence | false_reuse
    confidence: float
    evidence_refs: list[str]
    reason: str
