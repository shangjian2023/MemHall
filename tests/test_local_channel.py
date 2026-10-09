"""LocalChannel：openKylin 原生模式的本机执行通道（2026-10 架构收尾）。

接口与 SshChannel 逐方法对齐（run/sudo/run_json/close），命令语义不变
（POSIX、~ 展开、stdin 三通）——适配器代码零改动即可从 SSH 切本机直跑。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.remote import LocalChannel


@pytest.fixture
def ch():
    if not shutil.which("bash"):
        pytest.skip("本机无 bash（LocalChannel 为 POSIX 命令设计）")
    return LocalChannel(password="dummy")


def test_run_stdin_and_rc(ch):
    rc, out, _ = ch.run("echo hello")
    assert rc == 0 and out.strip() == "hello"
    rc, out, _ = ch.run("cat", stdin_data="via-stdin")
    assert out.strip() == "via-stdin"
    assert ch.run("exit 3")[0] == 3


def test_path_augmented_with_local_bin(ch):
    """~/.local/bin 前置（npm 布局的智能体常装这，服务进程 PATH 未必有）。"""
    rc, out, _ = ch.run("echo $PATH")
    assert rc == 0 and ".local/bin" in out


def test_run_json(ch):
    assert ch.run_json("echo '[1, 2]'") == [1, 2]
    with pytest.raises(RuntimeError):
        ch.run_json("exit 9")


def test_sudo_requires_password(monkeypatch, tmp_path):
    monkeypatch.delenv("VM_PASS", raising=False)
    c = LocalChannel(password="")
    with pytest.raises(ValueError, match="VM_PASS"):
        c.sudo("date -s @0")


def test_default_channel_selects_by_mode(monkeypatch):
    """通道选路：原生（VM_HOST 指向本机）= LocalChannel，宿主+VM = SshChannel。
    三个评测车道适配器都从这个口走（构造时注入才不受影响，测试均注入）。"""
    if not shutil.which("bash"):
        pytest.skip("本机无 bash（LocalChannel 为 POSIX 命令设计）")
    import memhall.discovery as disc
    from memhall.adapters.hermes import HermesAdapter
    from memhall.adapters.kylinbot import KylinBotAdapter
    from memhall.adapters.openclaw import OpenClawAdapter
    from memhall.adapters.remote import SshChannel

    monkeypatch.setenv("VM_HOST", "192.168.61.133")
    monkeypatch.setenv("VM_PASS", "x")
    monkeypatch.setattr(disc, "vm_is_self", lambda: True)
    for cls in (HermesAdapter, KylinBotAdapter, OpenClawAdapter):
        assert isinstance(cls().ch, LocalChannel), cls.__name__
    monkeypatch.setattr(disc, "vm_is_self", lambda: False)
    for cls in (HermesAdapter, KylinBotAdapter, OpenClawAdapter):
        assert isinstance(cls().ch, SshChannel), cls.__name__
