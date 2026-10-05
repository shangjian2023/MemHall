"""adapter_availability：跑页下拉按部署形态切换口径，且只报真实可跑的。

native（openKylin 原生，本机即评测机）与 remote（宿主机 + 评测 VM）两种
形态下 label/探测方式都不同——VM 里看到的界面应按"本机"讲，不该再说
"跑在 VM 里"（UI 描述同理由此切换，见 index.html deploy-mode 段）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import memhall.discovery as disc
from memhall.discovery import Finding, adapter_availability


def test_native_mode_local_view(monkeypatch):
    """原生：远端适配器按"本机"口径；本机检出 + 回环通道齐才可跑。"""
    monkeypatch.setattr(disc, "vm_is_self", lambda: True)
    monkeypatch.setenv("VM_HOST", "127.0.0.1")
    monkeypatch.setenv("VM_PASS", "x")
    monkeypatch.setattr(disc, "find_cli",
                        lambda *c: "/usr/bin/kylin-bot" if "kylin-bot" in c else "")
    st = adapter_availability(vm_probe=lambda: [])
    assert st["mode"] == "native"
    a = st["adapters"]
    assert a["kylinbot"] == {"label": "kylinbot（本机）", "ok": True}
    assert not a["hermes"]["ok"]            # 本机没检出 hermes → 不进下拉
    assert not a["hermes-local"]["ok"]
    assert a["mock"]["ok"]                  # mock 恒可用，下拉永不空
    assert a["hermes-local"]["label"] == "hermes（本机直连）"  # 与 hermes 区分


def test_native_mode_needs_loopback_channel(monkeypatch):
    """原生但没配回环凭据：适配器起跑就缺 VM_PASS，不算可跑（不装不骗人）。"""
    monkeypatch.setattr(disc, "vm_is_self", lambda: True)
    monkeypatch.setenv("VM_HOST", "127.0.0.1")
    monkeypatch.delenv("VM_PASS", raising=False)
    monkeypatch.setattr(disc, "find_cli", lambda *c: "/bin/any")
    a = adapter_availability(vm_probe=lambda: [])["adapters"]
    assert not a["kylinbot"]["ok"] and not a["hermes"]["ok"] and not a["openclaw"]["ok"]
    assert a["claude-local"]["ok"]           # 本机直连型不受通道影响


def test_remote_mode_vm_probe_decides(monkeypatch):
    """宿主机形态：VM 内 SSH 实测决定可跑性，没探到的不显示。"""
    monkeypatch.setattr(disc, "vm_is_self", lambda: False)
    monkeypatch.setattr(disc, "find_cli", lambda *c: "")
    vm = [Finding("hermes", "vm", True, adapter="hermes"),
          Finding("openclaw", "vm", True, adapter="openclaw")]
    st = adapter_availability(vm_probe=lambda: vm)
    assert st["mode"] == "remote"
    a = st["adapters"]
    assert a["hermes"] == {"label": "hermes（VM 真机）", "ok": True}
    assert a["openclaw"]["ok"]
    assert not a["kylinbot"]["ok"]           # VM 里没探到 → 不进下拉
    assert not a["hermes-local"]["ok"]       # 宿主机也没装 → 不进下拉
