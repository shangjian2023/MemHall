"""SSH 通道韧性测试（10-08 r3 挂死事故的回归网）。

事故：VM 侧会话已消失（网络抖动静默死亡），本地 paramiko 在
recv_exit_status() 上永久阻塞——该调用无超时参数；且 _client 只判
self._cli is None，死连接被复用到马拉松结束。
"""

from __future__ import annotations

import pytest

import memhall.adapters.remote as remote
from memhall.adapters.remote import SshChannel, _recv_exit


class _FakeTransport:
    def __init__(self, active: bool):
        self._active = active

    def is_active(self) -> bool:
        return self._active

    def set_keepalive(self, _: int) -> None:
        pass


class _FakeClient:
    """按脚本演的 SSHClient：n 号实例，transport 活性由实例号决定。"""

    n = 0
    instances: list[_FakeClient] = []

    def __init__(self):
        _FakeClient.n += 1
        self.idx = _FakeClient.n
        self.closed = False
        _FakeClient.instances.append(self)

    def get_host_keys(self):
        class _Keys:
            def load(self, _): ...

            def save(self, _): ...

        return _Keys()

    def set_missing_host_key_policy(self, _): ...

    def connect(self, **_): ...

    def get_transport(self) -> _FakeTransport:
        # 1 号实例连接已死；2 号（重连产物）活着
        return _FakeTransport(active=self.idx >= 2)

    def close(self):
        self.closed = True


class _FakeChan:
    def __init__(self, ready_after: int):
        self._polls = 0
        self._ready_after = ready_after

    def exit_status_ready(self) -> bool:
        self._polls += 1
        return self._polls >= self._ready_after

    def recv_exit_status(self) -> int:
        return 0


def test_recv_exit_times_out_on_dead_channel():
    """通道永不 ready → 超时抛 SSHException 而不是永久阻塞。"""
    import time as _time
    with pytest.raises(Exception, match="通道挂死"):
        _recv_exit(_FakeChan(ready_after=10**9), _time.monotonic() + 1.5)


def test_recv_exit_returns_when_ready():
    assert _recv_exit(_FakeChan(ready_after=2), deadline=1e18) == 0


def test_dead_transport_triggers_reconnect(monkeypatch):
    """传输层不活跃 → 关旧连接重建，而不是复用死连接。"""
    _FakeClient.n = 0
    _FakeClient.instances = []
    monkeypatch.setattr(remote.paramiko, "SSHClient", _FakeClient)
    monkeypatch.setattr(remote, "KNOWN_HOSTS",
                        remote.Path("C:/nonexistent-known-hosts"))
    ch = SshChannel(host="h", user="u", password="p")
    cli1 = ch._client()
    cli2 = ch._client()          # 1 号 transport 已死 → 应触发重连拿 2 号
    assert cli1 is not cli2
    assert cli1.closed
    assert cli2.get_transport().is_active()
