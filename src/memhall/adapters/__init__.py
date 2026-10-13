"""适配器注册表：name → 适配器类的唯一映射。

CLI（cmd_run）与 Web UI（/api/start worker）此前各持一份逐字复制的
if/elif 导入链，加适配器要同步改两处——本模块收敛为单源。

值是 "模块:类名" 字符串，create_adapter 时才 import（保持 lazy，
mock 之外的依赖仅在真用时加载）。
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import entry_points

from memhall.adapters.base import AgentAdapter

ADAPTERS: dict[str, str] = {
    "mock": "memhall.adapters.mock:MockAdapter",
    "hermes": "memhall.adapters.hermes:HermesAdapter",
    "kylinbot": "memhall.adapters.kylinbot:KylinBotAdapter",
    "kylin-agent": "memhall.adapters.kylin_agent:KylinAgentAdapter",
    "openclaw": "memhall.adapters.openclaw:OpenClawAdapter",
    "hermes-local": "memhall.adapters.hermes_local:LocalHermesAdapter",
    "claude-local": "memhall.adapters.claude_local:LocalClaudeAdapter",
    "qwen-local": "memhall.adapters.qwen_local:LocalQwenAdapter",
    "opencode": "memhall.adapters.opencode:OpenCodeAdapter",
}


REGISTERED: dict[str, type[AgentAdapter]] = {}


def register_adapter(name: str, adapter_class: type[AgentAdapter]) -> None:
    """Explicit in-process plugin registration; built-ins cannot be replaced."""
    if name in ADAPTERS or name in REGISTERED:
        raise ValueError(f"适配器名冲突: {name}")
    if not issubclass(adapter_class, AgentAdapter):
        raise TypeError("适配器插件必须继承 AgentAdapter")
    REGISTERED[name] = adapter_class


def adapter_names() -> list[str]:
    external = {ep.name for ep in entry_points(group="memhall.adapters")}
    return sorted(set(ADAPTERS) | set(REGISTERED) | external)


def create_adapter(name: str) -> AgentAdapter:
    """按名实例化适配器；未知名抛 ValueError（调用方转用户错误）。"""
    if name in REGISTERED:
        return REGISTERED[name]()
    if name not in ADAPTERS:
        matches = [ep for ep in entry_points(group="memhall.adapters") if ep.name == name]
        if len(matches) > 1:
            raise ValueError(f"多个插件提供同名适配器: {name}")
        if matches:
            register_adapter(name, matches[0].load())
            return REGISTERED[name]()
    try:
        path = ADAPTERS[name]
    except KeyError:
        raise ValueError(
            f"未知适配器: {name}（可选: {', '.join(ADAPTERS)}）") from None
    module, cls = path.split(":")
    return getattr(import_module(module), cls)()
