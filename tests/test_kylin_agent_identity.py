"""kylin-agent 网关身份专项网（P1：请求/token/模型覆盖的归属 tag）。

复现（judge-review-20261014/kylin-agent-review-findings.json）：实例
name=kylin-agent，但 HermesAdapter.__init__ 固定 gateway_settings("hermes",
vm_lane=True) → key=memhall-hermes，网关把 kylin-agent 的请求记到 hermes
头上，run 的模型覆盖也指向另一个 tag。

分工：父类按 self.name 取身份由 B 修（03-vm-adapters.md，hermes.py 归 B）；
本网只立契约锚点，不竞改 hermes.py、不在子类复制构造逻辑绕过。
原 xfail(strict=True) 两条已在 B 合入后摘标记转常绿守护（2026-10-14 整合）：
其余各条现在就绿、B 合入后仍须绿：
- Hermes 车道仍用 memhall-hermes（B 的修复不得矫枉过正）
- GATEWAY_AGENT_KEY 自定义覆写对两家一视同仁（gateway.py 既有约定）
- 无网关模式回落 AGENT_LLM_*（直连形态不受身份修复影响）
- VM 腿 URL 优先于 GATEWAY_URL（vm_lane 语义）
- 模型名走网关统一口径，不因 provider 名换被测模型（DEEPSEEK_* 只是
  CLI 的 provider 环境名，真实模型是网关侧的 qwen3.7-plus）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memhall.adapters.hermes import HermesAdapter
from memhall.adapters.kylin_agent import KylinAgentAdapter

# 身份测试要绝对干净：宿主 shell 可能 source 过 .env，全套相关变量先清零
_ENV_KEYS = ("GATEWAY_URL", "GATEWAY_VM_URL", "GATEWAY_AGENT_KEY", "GATEWAY_MODEL",
             "AGENT_LLM_KEY", "AGENT_LLM_BASE_URL", "AGENT_LLM_MODEL")


class _Chan:
    """假通道：只吃构造期与 env 行生成，不需要应答内容。"""

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        return 0, "", ""

    def close(self) -> None:
        pass


def _scrub(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_kylin_agent_gets_own_gateway_tag(monkeypatch):
    """P1 主断言：统一网关模式下 kylin-agent 的 dummy key 必须是
    memhall-kylin-agent（网关按 Bearer 归因记账、模型覆盖按 tag 生效）。
    （B 已修父类按 self.name 取身份，摘 xfail(strict) 转常绿守护。）"""
    _scrub(monkeypatch)
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    a = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "DEEPSEEK_API_KEY='memhall-kylin-agent'" in a._llm_env_lines()


def test_hermes_lane_keeps_memhall_hermes(monkeypatch):
    """B 修复的另一半契约：Hermes 车道身份不变，仍 memhall-hermes。"""
    _scrub(monkeypatch)
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    h = HermesAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "DEEPSEEK_API_KEY='memhall-hermes'" in h._llm_env_lines()


def test_custom_gateway_agent_key_overrides_both_lanes(monkeypatch):
    """GATEWAY_AGENT_KEY 自定义覆写与 A/B 约定一致：一家设了全体生效，
    两车道同 tag（混合形态单车道实测时的既有语义，不得因身份修复漂移）。"""
    _scrub(monkeypatch)
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_AGENT_KEY", "custom-lane")
    for adapter, ctor in (("kylin-agent", KylinAgentAdapter), ("hermes", HermesAdapter)):
        a = ctor(channel=_Chan())  # type: ignore[arg-type]
        assert "DEEPSEEK_API_KEY='custom-lane'" in a._llm_env_lines(), adapter


def test_no_gateway_mode_falls_back_to_agent_llm_env(monkeypatch):
    """直连形态（无 GATEWAY_*）：凭据回落 AGENT_LLM_*，身份修复不波及。"""
    _scrub(monkeypatch)
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-direct")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen3.7-plus")
    a = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    lines = a._llm_env_lines()
    assert "DEEPSEEK_API_KEY='sk-direct'" in lines
    assert "DEEPSEEK_BASE_URL='https://api.example.com/v1'" in lines
    assert "DEEPSEEK_MODEL='qwen3.7-plus'" in lines


def test_vm_lane_url_wins_over_gateway_url(monkeypatch):
    """vm_lane=True：GATEWAY_VM_URL 优先（VM 内走宿主网关的明文腿），
    GATEWAY_URL 只是回落——与 hermes 车道同语义。"""
    _scrub(monkeypatch)
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_URL", "https://gw.example.com/v1")
    a = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "DEEPSEEK_BASE_URL='http://192.168.61.1:8311/v1'" in a._llm_env_lines()
    monkeypatch.delenv("GATEWAY_VM_URL")
    b = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "DEEPSEEK_BASE_URL='https://gw.example.com/v1'" in b._llm_env_lines()


def test_model_is_gateway_unified_not_deepseek_branding(monkeypatch):
    """DEEPSEEK_* 只是 CLI provider 环境名：被测模型不因此换成 DeepSeek，
    统一网关模式下传网关口径模型名，真身（qwen3.7-plus）由网关按 tag 改写。"""
    _scrub(monkeypatch)
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    a = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "DEEPSEEK_MODEL='unified-model'" in a._llm_env_lines()
    assert not any("deepseek-chat" in ln or "deepseek-reasoner" in ln
                   for ln in a._llm_env_lines().splitlines())
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    b = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "DEEPSEEK_MODEL='qwen3.7-plus'" in b._llm_env_lines()


def test_send_pins_kylin_home_not_hermes(monkeypatch):
    """home 钉定：env 行显式 export HERMES_HOME 指 ~/.kylin-agent-runtime——
    fork 的 get_hermes_home 读该变量（上游 #18594 要求 spawner 显式传），
    VM 侧杂散 HERMES_HOME（如指 ~/.hermes）写不串车道。"""
    _scrub(monkeypatch)
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    a = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    lines = a._llm_env_lines()
    assert 'export HERMES_HOME="$HOME/.kylin-agent-runtime"' in lines
    assert ".hermes" not in lines.replace("memhall-hermes", "")


def test_gateway_settings_receives_adapter_name(monkeypatch):
    """构造期把 self.name 传给 gateway_settings（B 修父类后的调用契约）。
    记录器按名回派生 tag，kylin-agent 侧收到的必须是 "kylin-agent"。
    （B 已修，摘 xfail(strict) 转常绿守护。）"""
    import memhall.gateway as gw_mod

    calls: list[str] = []

    def recorder(agent: str, vm_lane: bool = False):
        calls.append(agent)
        return {"base_url": "http://gw/v1", "key": f"memhall-{agent}",
                "model": "unified-model"}

    monkeypatch.setattr(gw_mod, "gateway_settings", recorder)
    KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert "kylin-agent" in calls
