"""契约 02 · MemoryCase 用例数据模型（pydantic）。

owner: B（数据）—— 权威定义在 docs/contracts/case.md，本文件是其实现骨架。
B 在此基础上细化；D 的 runner 解析用同一套模型（消费方 review，team-plan §4.3）。
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------- 枚举（契约 02 §2/§3/§4）----------

class Capability(str, Enum):
    """六能力（赛题原文对齐）。"""

    PERSIST = "persist"                    # 长期保持
    RECALL = "recall"                      # 记忆调用
    DYNAMIC_UPDATE = "dynamic_update"      # 动态更新
    DISCRIMINATE = "discriminate"          # 相近区分
    BOUNDARY = "boundary"                  # 边界识别
    REUSE = "reuse"                        # 任务复用


class QuestionType(str, Enum):
    """七题型（design.md §4.2）。"""

    SESSION_RECALL = "session_recall"
    CROSS_SESSION_RECALL = "cross_session_recall"
    INFO_UPDATE = "info_update"
    TEMPORAL = "temporal"
    SIMILARITY = "similarity"
    FALSE_PREMISE = "false_premise"
    TASK_CHAIN = "task_chain"


class ContentType(str, Enum):
    """六类内容（覆盖矩阵的行）。"""

    PREFERENCE = "preference"
    PATH = "path"
    TEMPLATE = "template"
    FACT = "fact"
    PROJECT_STATE = "project_state"
    SENSITIVE = "sensitive"


# ---------- 剧本（契约 02 §5）----------

class SystemEvents(BaseModel):
    """confound 段的系统级动作（OS 体检套件挂载点，D 实现）。

    全部默认 False——用例作者显式打开；rollback 仅 W2 磐石摸底后允许 true。
    """

    reboot: bool = False
    clock_shift_days: int = 0      # >0 即拨钟
    network_off: bool = False
    rollback: bool = False         # ⚠️ W2 磐石实测后解禁


class Step(BaseModel):
    """剧本单步：user（对话）或 task（行为探测）。

    task 的回复不参与判卷——判卷看 fs_diff + actions。
    """

    user: Optional[str] = None
    task: Optional[str] = None
    expect_agent_reply: bool = True

    def kind(self) -> Literal["user", "task"]:
        if self.user is not None:
            return "user"
        if self.task is not None:
            return "task"
        raise ValueError("step 必须二选一：user 或 task")


class Phase(BaseModel):
    name: Literal["inject", "confound", "probe"]
    steps: list[Step] = Field(default_factory=list)
    end_session: bool = False
    system_events: Optional[SystemEvents] = None
    wait_minutes: int = 0          # 默认 0：用 end_session + 拨钟替代真实等待


# ---------- 探测点（契约 02 §6）----------

class RuleAssert(BaseModel):
    """规则断言分支：按序求值，先命中先得。

    assert 名与参数见契约 02 §6 断言库（C 实现于 scoring/rules/）。
    """

    assert_name: str = Field(alias="assert")
    args: list[Union[str, int, dict]] = Field(default_factory=list)
    then: str                      # 命中后的判定值（五态枚举字符串）
    model_config = {"populate_by_name": True}


class RuleProbe(BaseModel):
    kind: Literal["rule"]
    id: str
    after: Literal["inject", "confound", "probe"] = "probe"
    check: list[RuleAssert]        # 全按序求值；default 分支的 then 必填
    evidence_ref: list[str] = Field(default_factory=list)


class Anchor(BaseModel):
    """judge 锚定例（few-shot，防漂移；每题型至少 2 条，B 写）。"""

    reply: str
    expect_verdict: str            # verdict_map 的 key 之一


class JudgeProbe(BaseModel):
    kind: Literal["judge"]
    id: str
    after: Literal["inject", "confound", "probe"] = "probe"
    ask: str
    expect: str
    rubric: str                    # 判卷标准（B 写，C review 可判定性）
    verdict_map: dict[str, str]    # judge 分类输出 -> 五态
    anchors: list[Anchor] = Field(default_factory=list)


Probe = Union[RuleProbe, JudgeProbe]


# ---------- 用例主体（契约 02 §1）----------

class CaseMeta(BaseModel):
    author: str
    created: date
    source: Literal["seed", "generated"]
    generator: Optional[dict] = None   # source=generated 时：{模板id, 参数, seed}
    notes: str = ""


class MemoryCase(BaseModel):
    case_id: str                   # <能力族>-<三位序号>
    schema_version: str = "0.1"
    capability: Capability
    question_type: QuestionType
    content_type: ContentType
    difficulty: int = Field(ge=1, le=3)
    tags: list[str] = Field(default_factory=list)

    meta: CaseMeta
    phases: list[Phase]            # 至少 inject + probe；confound 可省（会话内题）
    probes: list[Probe]
