"""契约 02 · MemoryCase 用例数据模型（pydantic）。

owner: B（数据）—— 权威定义在 docs/contracts/case.md，本文件是其实现骨架。
B 在此基础上细化；D 的 runner 解析用同一套模型（消费方 review，team-plan §4.3）。
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

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

    user: str | None = None
    task: str | None = None
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
    system_events: SystemEvents | None = None
    wait_minutes: int = 0          # 默认 0：用 end_session + 拨钟替代真实等待


# ---------- 探测点（契约 02 §6）----------

class RuleAssert(BaseModel):
    """规则断言分支：按序求值，先命中先得。

    assert 名与参数见契约 02 §6 断言库（C 实现于 scoring/rules/）。
    """

    assert_name: str = Field(alias="assert")
    args: list[str | int | dict] = Field(default_factory=list)
    then: str                      # 命中后的判定值（五态枚举字符串）
    model_config = {"populate_by_name": True}


class RuleProbe(BaseModel):
    kind: Literal["rule"]
    id: str
    after: Literal["inject", "confound", "probe"] = "probe"
    role: Literal["score", "diagnostic", "auto"] = "auto"
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
    role: Literal["score", "diagnostic", "auto"] = "auto"
    ask: str
    expect: str
    rubric: str                    # 判卷标准（B 写，C review 可判定性）
    verdict_map: dict[str, str]    # judge 分类输出 -> 五态
    anchors: list[Anchor] = Field(default_factory=list)


Probe = RuleProbe | JudgeProbe


# role 推断规则（契约 02 §6；显式声明优先，auto 按断言类型落地）：
#   - judge 探测全是行为判定 → score；
#   - boundary 族的 rule 探测（canary 在场/缺席）是本族核心构念 → score；
#   - 跨族 canary（then=over_persist 的存储断言挂在别的族名下）= 边界构念抽查
#     → diagnostic，不计入该族分数（测的不是这个族的构念）；
#   - memory.* 存储态断言（存没存/存了几个）→ diagnostic：故障定位层，
#     一个"没写库"故障不该在 persist/update/reuse 三处重复扣分；
#   - actions.* 断言 → diagnostic：操作记录证据面未成熟（coverage 语义见
#     契约 03），预留为步数/复用证据，验证成熟后可改回 score；
#   - fs.* 断言是行为验收（任务真做了没有）→ score。
_MEMORY_ASSERTS = {"memory.contains", "memory.not_contains",
                   "memory.ever_contained", "memory.entry_count"}
_ACTIONS_ASSERTS = {"actions.contains_action", "actions.count_lt"}


def probe_role(case: MemoryCase, probe: RuleProbe | JudgeProbe) -> str:
    """探测点角色：score 进六维分数；diagnostic 只进故障定位/质检层。"""
    if probe.role in ("score", "diagnostic"):
        return probe.role
    if probe.kind == "judge":
        return "score"
    if case.capability == Capability.BOUNDARY:
        return "score"
    names = {b.assert_name for b in probe.check} - {"default"}  # 兜底分支非证据源
    if any(b.then == "over_persist" for b in probe.check):
        return "diagnostic"
    if names and names <= _MEMORY_ASSERTS:
        return "diagnostic"
    if names and names <= _ACTIONS_ASSERTS:
        return "diagnostic"
    return "score"


# ---------- 用例主体（契约 02 §1）----------

class CaseMeta(BaseModel):
    author: str
    created: date
    source: Literal["seed", "generated"]
    generator: dict | None = None   # source=generated 时：{模板id, 参数, seed}
    notes: str = ""


class MemoryCase(BaseModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,127}$")
    schema_version: str = "0.1"
    capability: Capability
    question_type: QuestionType
    content_type: ContentType
    difficulty: int = Field(ge=1, le=3)
    tags: list[str] = Field(default_factory=list)
    family_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    split: Literal["development", "test", "heldout", "stress"] | None = None

    meta: CaseMeta
    phases: list[Phase]            # 至少 inject + probe；confound 可省（会话内题）
    probes: list[Probe]
