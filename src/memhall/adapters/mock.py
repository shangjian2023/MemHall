"""MockAdapter —— 缺陷注入基线（v2，2026-09-29 重构）。

三个角色不变：M1 冒烟被测体 / 适配器写法样板 / 单元测试固定靶子。

v2 与 v1 的本质差别（回应"措辞耦合"批评）：
- v1 靠正则双侧配对（教"我的X是Y"提 key，问"我的X在哪"查 key）——出题措辞
  稍偏就连不上，mock 分数混入了"出题人↔mock 措辞对齐度"，不是纯净基线；
- v2 作答**回显教学原句**：题库的 expect 都是教学内容的子串，原句进原句出，
  判卷子串必然命中——分数与措辞解耦，只剩设计好的缺陷模式。

设计缺陷模式（六维口径，分数是这些模式的确定输出，不是难度地板）：
- persist / recall   ：记住并回显（无缺陷注入，应≈满分）；
- dynamic_update     ：粘连旧值——改口后新旧并存，作答仍取最早一条 → 记混；
- discriminate       ：歧义取早——相近记忆竞争时取重叠最多/最早 → 记混；
- boundary           ：照单全收——明说别记的也存也答 → 过度持久化；
- reuse              ：只说不做——不执行任务，文件系统断言必失败。

回答选择策略：与问题共享"特征 token"（CJK 二元组 / ≥2 位字母数字串）的记忆里，
取重叠数最多者，同分取最早——这一条同时产出更新/区分两个维度的设计缺陷。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from memhall.adapters.base import AgentAdapter
from memhall.schema.evidence import (
    Action,
    ActionDump,
    ActionSource,
    MemoryEntry,
    MemorySnapshot,
    Reply,
)

utc = lambda: datetime.now(UTC)  # noqa: E731

# 设计缺陷模式表（机器可读；报告脚注/校准对账用）
DESIGNED_PROFILE = {
    "persist": "回显式记忆，应≈满分（个别失分=案例内嵌 canary 逮到照单全收）",
    "recall": "回显式记忆，应≈满分（同上）",
    "dynamic_update": "粘连旧值：改口后答旧 → 记混；规则探测因双值并存而通过",
    "discriminate": "首因粘连：相近记忆竞争时取最早 → 部分记混",
    "boundary": "照单全收：明说别记的也存也答 → 过度持久化",
    "reuse": "只说不做：不执行任务，文件系统断言失败",
}

_STOP = {
    "我的", "你的", "他的", "这个", "那个", "就是", "现在", "目前", "一下",
    "帮我", "帮忙", "记了", "记住", "记得", "知道", "告诉", "说过", "好的",
    "谢谢", "什么", "哪个", "哪条", "多少", "在哪", "怎么", "是谁", "用哪个",
    "是不是", "还是", "以前", "上次", "最近", "问题", "事情",
}
_QMARK = re.compile(r"[？?]$|吗[？?]?$")
_ALNUM = re.compile(r"[A-Za-z0-9_~/.\-]{2,}")
_CJK = re.compile(r"[一-鿿]")


def _tokens(text: str) -> set[str]:
    """特征 token：≥2 位字母数字串 + CJK 相邻二元组（停用词剔除）。"""
    out = {t for t in _ALNUM.findall(text) if not t.isdigit()}
    chars = _CJK.findall(text)
    out |= {a + b for a, b in zip(chars, chars[1:], strict=False)}
    return out - _STOP


@dataclass
class _Fact:
    raw: str                     # 教学原句（作答回显用，expect ⊆ raw → 判卷必中）
    toks: set[str] = field(default_factory=set)
    seq: int = 0


class MockAdapter(AgentAdapter):
    """假智能体：回显式记忆 + 设计缺陷模式，行为完全确定。"""

    name = "mock"

    def __init__(self) -> None:
        self._facts: list[_Fact] = []
        self._actions: list[Action] = []
        self._action_seq = 0
        self._reply_seq = 0

    # ---- 契约 01 五方法 ----

    def reset(self) -> None:
        self._facts.clear()
        self._actions.clear()
        self._action_seq = 0
        self._reply_seq = 0

    def send(self, session_id: str, message: str) -> Reply:
        sent_at = utc()
        text = self._respond(message)
        reply_at = utc()
        self._reply_seq += 1
        return Reply(session_id=session_id, text=text,
                     sent_at=sent_at, reply_at=reply_at,
                     latency_ms=int((reply_at - sent_at).total_seconds() * 1000) + 1,
                     token_usage=None)

    def end_session(self, session_id: str) -> None:
        pass  # mock 无进程无窗口，会话隔离靠 runner 换 session_id

    def dump_memory(self) -> MemorySnapshot:
        entries = [
            MemoryEntry(entry_id=f"m-{i:03d}", content=f.raw,
                        created_at=None, source_turn=None)
            for i, f in enumerate(self._facts)
        ]
        return MemorySnapshot(format="json", dumped_at=utc(),
                              entries=entries, raw=None)

    def fs_snapshot(self) -> list[str] | None:
        """空工作区（刻意不返回 None）：设计画像"只说不做"——快照恒空，
        fs_diff 有证据但零创建，reuse 文件断言确定性失败。
        返回 None 会让规则层无 fs 证据可判（探测点全变运行无效）。"""
        return []

    def dump_actions(self) -> ActionDump:
        return ActionDump(actions=list(self._actions), coverage="full")

    def clock_shift(self, days: int) -> None:
        """mock 无真实时钟：拨钟只记账——回显式作答与时间无关，temporal
        用例可正常判定（R38 的 fail-closed 针对第三方"没实现却静默当拨过"，
        mock 在此显式声明语义，不属于静默）。"""
        self._clock_offset_days = getattr(self, "_clock_offset_days", 0) + days

    def clock_restore(self) -> None:
        self._clock_offset_days = 0

    # ---- 假智能体的"脑子"：存一切陈述，答最重叠最早 ----

    def _respond(self, message: str) -> str:
        msg = message.strip()
        if _QMARK.search(msg):
            hits = self._match(_tokens(msg))
            if hits:
                return f"我记的你说过：{hits[0].raw}。"
            return "这个我不记得了。"
        # 陈述/指令一律入记忆（boundary 缺陷：照单全收，别记的也存）
        fact = _Fact(raw=msg, toks=_tokens(msg), seq=len(self._facts))
        if not any(f.raw == msg for f in self._facts):
            self._facts.append(fact)
            self._action_seq += 1
            self._actions.append(
                Action(action_id=f"a-{self._action_seq:03d}", ts=utc(),
                       tool="memory.write", args={"raw": msg}, result="ok",
                       source=ActionSource.AGENT_LOG))
        return f"好的，我记住了：{msg}。"

    def _match(self, q_toks: set[str]) -> list[_Fact]:
        """命中的记忆按"首因粘连"排序：最早教的那条最先被想起——
        这一个缺陷同时产出 update（答旧值）与 discriminate（相近取早）的记混。"""
        hits = [f for f in self._facts if q_toks & f.toks]
        return sorted(hits, key=lambda f: f.seq)
