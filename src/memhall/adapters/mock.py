"""MockAdapter —— 假智能体，全队第一个能跑的适配器（A 出的样板代码）。

作用：
1. M1 冒烟（09.27）的"被测智能体"——不依赖任何真机/VM/网络，runner 全链路可跑；
2. 后续真适配器（KylinBot/Hermes）的写法参照——照这个类的样子实现五个方法即可；
3. 单元测试固定靶子——行为确定，两次 reset→send→dump 哈希一致（契约 01 §5 稳定性）。

行为设计（故意做成"有点记性但记不完美"，让 M1 冒烟能看到五种判定都出现）：
- "记住 X" 的话术 -> 存入记忆（keyword 提取，模拟写入）；
- "改到/换成 Y" 的话术 -> 覆盖旧值（模拟更新）；
- 一次性/临时/canary 串 -> 故意还是存（模拟"不该记的记下了"，over_persist 样本）；
- 问"我的 X 在哪" -> 从记忆查并回答（模拟检索）；查不到 -> "我不知道"（模拟忘了）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from memhall.adapters.base import AgentAdapter
from memhall.schema.evidence import (
    Action,
    ActionDump,
    ActionSource,
    MemoryEntry,
    MemorySnapshot,
    Reply,
)

utc = lambda: datetime.now(timezone.utc)  # noqa: E731


class MockAdapter(AgentAdapter):
    """假智能体：一个内存 dict 当记忆库，回固定话术。"""

    name = "mock"

    def __init__(self) -> None:
        self._memory: dict[str, str] = {}        # key -> value（模拟记忆库）
        self._created_at: dict[str, datetime] = {}
        self._actions: list[Action] = []
        self._action_seq = 0
        self._reply_seq = 0

    # ---- 契约 01 五方法 ----

    def reset(self) -> None:
        self._memory.clear()
        self._created_at.clear()
        self._actions.clear()
        self._action_seq = 0
        self._reply_seq = 0

    def send(self, session_id: str, message: str) -> Reply:
        sent_at = utc()
        text = self._respond(message)
        reply_at = utc()
        self._reply_seq += 1
        return Reply(
            session_id=session_id,
            text=text,
            sent_at=sent_at,
            reply_at=reply_at,
            latency_ms=int((reply_at - sent_at).total_seconds() * 1000) + 1,
            token_usage=None,
        )

    def end_session(self, session_id: str) -> None:
        pass  # mock 无进程无窗口，会话隔离靠 runner 换 session_id

    def dump_memory(self) -> MemorySnapshot:
        entries = [
            MemoryEntry(
                entry_id=f"m-{i:03d}",
                content=f"{k}={v}",
                created_at=self._created_at.get(k),
                source_turn=None,
            )
            for i, (k, v) in enumerate(sorted(self._memory.items()))
        ]
        return MemorySnapshot(
            format="json",
            dumped_at=utc(),
            entries=entries,
            raw=None,   # mock 不落原始文件；真适配器直读 sqlite/文件后填 raw
        )

    def dump_actions(self) -> ActionDump:
        return ActionDump(actions=list(self._actions), coverage="full")

    # ---- 假智能体的"脑子"（测试样本工厂，不用真 LLM）----

    def _respond(self, message: str) -> str:
        msg = message.strip()

        # 写入："我的代码目录是 ~/dev/src" / "记一下：编辑器用 vim"
        m = re.match(r"(?:我的|我的)?(.+?)(?:是|放在|用)\s*([~/\w.-]+)", msg)
        if m and not msg.endswith("?"):
            key, value = m.group(1).strip(), m.group(2).strip()
            if "临时" in msg or "一次性" in msg or re.search(r"canary-\w+", msg):
                # 模拟边界失效：明说了不该记，还是存了（over_persist 样本）
                self._remember(key, value)
                return f"好的。"
            self._remember(key, value)
            return f"好的，我记住了：{key} = {value}。"

        # 更新："改到 ~/dev 了" / "以后以这个为准"
        m = re.match(r"(?:改到|换成|改为)\s*([~/\w.-]+)", msg)
        if m:
            new_value = m.group(1).strip()
            if self._memory:
                key = next(iter(self._memory))   # 简化：更新最近一个 key
                self._remember(key, new_value)
                return f"好的，已更新：{key} = {new_value}。"
            return "好的。"

        # 检索："我的 X 在哪" / "我用什么编辑器"
        m = re.match(r"我的?(.+?)(?:在哪|是什么|是啥)", msg)
        if m:
            key = m.group(1).strip()
            if key in self._memory:
                return f"{key}是 {self._memory[key]}。"
            return "这个我不记得了。"   # 模拟忘了

        if "记住了吗" in msg or "还记得" in msg:
            if self._memory:
                items = "、".join(f"{k}={v}" for k, v in self._memory.items())
                return f"记得，目前有：{items}。"
            return "我们还没聊过什么需要记的。"

        return "好的。"

    def _remember(self, key: str, value: str) -> None:
        self._memory[key] = value
        self._created_at[key] = utc()
        self._action_seq += 1
        self._actions.append(
            Action(
                action_id=f"a-{self._action_seq:03d}",
                ts=utc(),
                tool="memory.write",
                args={"key": key, "value": value},
                result="ok",
                source=ActionSource.AGENT_LOG,
            )
        )
