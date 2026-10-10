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
        self._statements: list[str] = []         # 自然语言事实，供模糊召回
        self._created_at: dict[str, datetime] = {}
        self._actions: list[Action] = []
        self._action_seq = 0
        self._reply_seq = 0

    # ---- 契约 01 五方法 ----

    def reset(self) -> None:
        self._memory.clear()
        self._statements.clear()
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
        entries.extend(
            MemoryEntry(
                entry_id=f"s-{i:03d}",
                content=statement,
                created_at=None,
                source_turn=None,
            )
            for i, statement in enumerate(self._statements)
        )
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
        # 疑问句不写入：问句里的"是什么/用哪个"不是陈述，检索优先
        is_question = msg.endswith("？") or msg.endswith("?")

        # 写入："我的代码目录是 ~/dev/src" / "记一下：编辑器用 vim"
        if not is_question:
            m = re.match(
                r"^(?:我的)?(?P<key>[^，,：:。；;]+?)(?:是|放在|在)\s*"
                r"(?P<value>[^，,。；;]+)$", msg,
            )
            if not m:
                m = re.match(
                    r"^(?:我的)?(?P<key>.+?)用\s*(?P<value>[A-Za-z0-9_.-]+)$", msg
                )
            if m and not msg.endswith("?"):
                key = self._clean_key(m.group("key"))
                value = m.group("value").strip()
                if "临时" in msg or "一次性" in msg or re.search(r"canary-\w+", msg):
                    # 模拟边界失效：明说了不该记，还是存了（over_persist 样本）
                    self._remember(key, value)
                    return f"好的。"
                self._remember(key, value)
                self._remember_statement(msg)
                return f"好的，我记住了：{key} = {value}。"

            # 题库中的自然表达不一定是“我的 X 是 Y”。测试适配器保留
            # 一份原句，召回时按主题词匹配；这只用于离线确定性演示。
            if self._looks_like_fact(msg):
                self._remember_statement(msg)
                return "好的，我记住了。"

        # 更新："改到 ~/dev 了" / "以后以这个为准"（句中改口也算）
        m = re.search(r"(?:改到|换成|改为|改成)\s*([^，。；,。!?！？\s]+)", msg)
        if m:
            new_value = m.group(1).strip()
            if self._statements:
                old = self._statements[-1]
                self._statements[-1] = f"{old}；更新后：{msg}"
                self._action_seq += 1
                self._actions.append(Action(
                    action_id=f"a-{self._action_seq:03d}", ts=utc(),
                    tool="memory.update", args={"statement": msg},
                    result="ok", source=ActionSource.AGENT_LOG,
                ))
                if not self._memory:
                    return "好的，已更新。"
            if self._memory:
                key = next(iter(self._memory))   # 简化：更新最近一个 key
                self._remember(key, new_value)
                return f"好的，已更新：{key} = {new_value}。"
            if self._statements:
                self._remember_statement(msg, replace_related=True)
                return "好的，已更新。"
            return "好的。"

        # 检索："我的 X 在哪" / "我用什么编辑器" / "你记的我的 X 是哪个" / "按你记的…用哪个"
        m = re.match(r"(?:按你记的，?|你记的|你之前记的)?我的?(.+?)(?:现在|目前)?"
                     r"(?:在哪|放在哪|是什么|是啥|是哪个|是多少|用哪个)", msg)
        if m:
            key = self._clean_key(m.group(1).strip())
            if key in self._memory:
                return f"{key}是 {self._memory[key]}。"
            fuzzy = self._find_statement(key)
            if fuzzy:
                return fuzzy
            return "这个我不记得了。"   # 模拟忘了

        if is_question:
            fuzzy = self._find_statement(msg)
            if fuzzy:
                return fuzzy

        if "记住了吗" in msg or "还记得" in msg:
            if self._memory:
                items = "、".join(f"{k}={v}" for k, v in self._memory.items())
                return f"记得，目前有：{items}。"
            return "我们还没聊过什么需要记的。"

        # 问句但检索未命中：模拟"问了不知道"（拒答而非答非所问）
        if is_question:
            return "这个我不记得了。"
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

    def _remember_statement(self, statement: str, replace_related: bool = False) -> None:
        """保存自然语言事实，避免把闲聊误当成记忆。"""

        if replace_related:
            new_tokens = self._topic_tokens(statement)
            self._statements = [
                existing for existing in self._statements
                if len(new_tokens & self._topic_tokens(existing)) < 2
            ]
        if statement in self._statements:
            return
        self._statements.append(statement)
        self._action_seq += 1
        self._actions.append(
            Action(
                action_id=f"a-{self._action_seq:03d}",
                ts=utc(),
                tool="memory.write",
                args={"statement": statement},
                result="ok",
                source=ActionSource.AGENT_LOG,
            )
        )

    def _looks_like_fact(self, message: str) -> bool:
        """识别题库注入句；confound 闲聊不会进入记忆。"""

        if any(token in message for token in (
            "顺便", "聊聊", "换个话题", "帮我看看", "最近", "今天", "这周",
            "这两天", "下班", "周末", "食堂", "天气", "晚上想", "对了，UKUI",
        )):
            return False
        return bool(re.search(
            r"记住|记一下|帮我记|习惯|以后|我的|目录|模板|版本|代号|地址|端口|"
            r"分支|状态|部署|内网|账号|模块|数据分析|浏览器主页|评审会|项目|环境",
            message,
        ))

    def _find_statement(self, query: str) -> str | None:
        """用主题词召回最相关的自然语言事实。"""

        q_tokens = self._topic_tokens(query)
        if not q_tokens:
            return None
        ranked: list[tuple[int, str]] = []
        for statement in self._statements:
            overlap = len(q_tokens & self._topic_tokens(statement))
            if overlap:
                ranked.append((overlap, statement))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], self._statements.index(item[1])))
        statement = ranked[0][1]
        if "；更新后：" in statement:
            original, update = statement.split("；更新后：", 1)
            return self._render_statement(original, update, query)
        return self._render_statement(statement, "", query)

    @staticmethod
    def _clean_key(key: str) -> str:
        return re.sub(r"^(?:我的|我平时|我习惯把|我习惯|记一下我的|记住我的)", "", key).strip()

    def _render_statement(self, statement: str, update: str, query: str) -> str:
        """生成简洁的模拟回答，优先取更新后的事实。"""

        text = update or statement
        q = self._topic_tokens(query)
        # 对几类自然语言主题做归一，减少题面措辞差异。
        aliases = {
            "会议": {"开会", "纪要", "会议"},
            "模板": {"周报", "日报", "模板"},
            "地址": {"收货", "地址"},
            "端口": {"生产", "测试", "端口"},
            "状态": {"分支", "项目", "状态"},
            "目录": {"路径", "目录", "文件"},
        }
        for label, words in aliases.items():
            if q & words:
                q.add(label)
        return text

    @staticmethod
    def _topic_tokens(text: str) -> set[str]:
        # 中文使用相邻双字词，ASCII 路径、代号、时间保留为整体。
        tokens = set(re.findall(r"[A-Za-z0-9_./:@-]{2,}", text))
        chinese = re.findall(r"[\u4e00-\u9fff]+", text)
        for group in chinese:
            tokens.update(group[i:i + 2] for i in range(len(group) - 1))
        synonyms = {
            "会议": {"开会"}, "开会": {"会议"},
            "周报": {"周报"}, "日报": {"日报"},
            "地址": {"收货"}, "端口": {"端口"},
            "路径": {"目录", "文档"}, "目录": {"路径"},
            "状态": {"进展"}, "进展": {"状态"},
            "命名": {"规则"}, "规则": {"命名"},
        }
        for token in tuple(tokens):
            tokens.update(synonyms.get(token, set()))
        stop = {"我的", "哪个", "什么", "现在", "之前", "按你", "记的", "是哪个",
                "是什么", "放在哪", "一下", "一下子", "有关", "这次", "那个"}
        return tokens - stop
