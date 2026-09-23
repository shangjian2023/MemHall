"""契约 01 · AgentAdapter 基类与异常（pydantic + abc）。

owner: A（架构）—— 权威定义在 docs/contracts/adapter.md。
三档接入：zero-code（协议模板，W2）/ doctor（自动探测，W3）/ custom（继承本类）。
MockAdapter 在 adapters/mock.py —— 全队第一个能跑的适配器，也是 M1 冒烟的"被测智能体"。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from memhall.schema.evidence import ActionDump, MemorySnapshot, Reply


# ---------- 异常（契约 01 §2）----------
# runner 捕获后的处理：前两个 -> case 标运行无效；后两个 -> 对应降级路径。

class AdapterError(Exception):
    """适配器异常基类。"""


class AgentTimeout(AdapterError):
    """send() 超时（默认 120s，可配）。"""


class AgentUnavailable(AdapterError):
    """进程崩溃 / 无响应 / 答非所问到无法回复。"""


class MemoryResetUnsupported(AdapterError):
    """记忆无法归零 -> runner 降级快照回滚，或标 reset_method 进 evidence。"""


class MemoryNotDumpable(AdapterError):
    """记忆无法导出（纯云端记忆）-> 该 case 降级纯行为判定。"""


# ---------- 基类 ----------

class AgentAdapter(ABC):
    """所有智能体适配器的基类：评测器与被测智能体之间的翻译官。

    只做四件事——转发消息、导出记忆、导出操作、管理会话生命周期。
    不评判、不改写用例、不产生判定（评分层的事一件不碰）。
    """

    name: str = "abstract"

    @abstractmethod
    def reset(self) -> None:
        """清空智能体记忆，回到初始状态。每个 case 开始前调用。"""

    @abstractmethod
    def send(self, session_id: str, message: str) -> Reply:
        """发一条用户消息，等待并返回回复。message 原样透传，不得改写。"""

    @abstractmethod
    def end_session(self, session_id: str) -> None:
        """结束一个会话：关窗口/杀进程/调登出接口，任选能实现的方式。"""

    @abstractmethod
    def dump_memory(self) -> MemorySnapshot:
        """导出当前全部记忆。没有导出接口就直读存储文件，format 如实标注。"""

    @abstractmethod
    def dump_actions(self) -> ActionDump:
        """导出 reset 以来的操作记录。来源优先级：MCP 日志 > 智能体日志 > auditd。"""
