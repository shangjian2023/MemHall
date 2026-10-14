"""评审修复 B 面回归网：VM 时钟恢复含真实耗时 + 产物根隔离（2026-10-14）。

复现依据 build/judge-review-20261014/review-findings.json：
- clock_restore 丢耗时：起点 1700000000、600s 用例，旧实现恢复到
  1700000000（应为 1700000600），-600s 漂移攒过 ±300s 预检闸门；
- ~/memhall-artifacts 不在 EVAL_WORKDIRS：sandbox=1 时整体只读、创建
  必败；三家 reset 不清、上轮残留吃掉 fs_diff 的 created 证据（created
  是快照路径集合差，文件还在就不再算新建），同题重跑必失分；
- kylin-agent 子类继承 Hermes.__init__，网关身份被固定成 memhall-hermes。

全用假通道 / 假单调钟 / 临时文件，不碰真机（真机验收清单在交付说明）。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.base import AdapterError
from memhall.adapters.fs_hashes import local_hashes
from memhall.adapters.vm_common import EVAL_WORKDIRS, VmAdapterMixin

# ---------- 假 VM 时钟 ----------


class FakeVmClock:
    """wall 钟可被 date -s 拨动，monotonic 独立、由测试显式推进。

    旋钮：run_rc（读时钟命令通道故障）、readable（/proc/uptime 缺席）、
    post_set_skew（date -s"成功"但钟没真回位的偏移，模拟恢复失效）。"""

    def __init__(self, epoch: int = 1700000000, mono: float = 1000.0,
                 post_set_skew: float = 0.0):
        self.wall = float(epoch)
        self.mono = mono
        self.sudo_rc = 0
        self.run_rc = 0
        self.readable = True
        self.post_set_skew = post_set_skew
        self.sudos: list[str] = []
        self.runs: list[str] = []

    def run(self, cmd, timeout=300, stdin_data=None):
        self.runs.append(cmd)
        if "date +%s" in cmd and "/proc/uptime" in cmd:
            if self.run_rc:
                return self.run_rc, "", "ssh dead"
            if not self.readable:
                return 0, f"{int(self.wall)}\n", ""  # uptime 缺席：单字段
            return 0, f"{int(self.wall)} {self.mono:.2f}\n", ""
        return 0, "", ""

    def _apply(self, cmd):
        m = re.match(r"date -s @(\d+)", cmd)
        if m:
            self.wall = int(m.group(1)) + self.post_set_skew
        else:
            m = re.match(r"date -s '\+(\d+) days'", cmd)
            self.wall += int(m.group(1)) * 86400

    def sudo(self, cmd, timeout=120):
        self.sudos.append(cmd)
        if self.sudo_rc:
            return self.sudo_rc, "", "boom"
        self._apply(cmd)
        return 0, "ok", ""


class _Lane(VmAdapterMixin):
    name = "fake-vm"

    def __init__(self, ch):
        self.ch = ch


def test_restore_includes_real_elapsed_time():
    """评审复现的正面用例：600s 用例耗时必须回到钟上。"""
    ch = FakeVmClock(epoch=1700000000)
    a = _Lane(ch)
    a.clock_shift(3)
    assert ch.wall == 1700000000 + 3 * 86400  # 拨钟在 VM 侧生效
    ch.mono += 600  # 用例真实耗时（单调钟，date -s 拨不动它）
    a.clock_restore()
    assert "date -s @1700000600" in ch.sudos[-1]
    assert ch.wall == 1700000600  # 原 epoch + 耗时；旧实现这里回 1700000000
    assert a._clock_state is None


def test_multiple_shifts_keep_first_baseline():
    ch = FakeVmClock(1700000000)
    a = _Lane(ch)
    a.clock_shift(1)
    a.clock_shift(7)
    assert len(ch.sudos) == 2  # 两次拨钟都执行
    ch.mono += 600
    a.clock_restore()
    # 恢复锚定第一次拨钟前的时刻：旧实现会把基准覆盖成第一次拨完的 epoch
    assert ch.sudos[-1] == "date -s @1700000600 >/dev/null 2>&1 && echo ok"
    assert ch.wall == 1700000600


def test_shift_failure_keeps_baseline_then_restore_self_heals():
    ch = FakeVmClock(1700000000)
    ch.sudo_rc = 1
    a = _Lane(ch)
    with pytest.raises(AdapterError, match="拨钟失败"):
        a.clock_shift(7)
    assert a._clock_state is not None  # 基准保留，不因拨钟失败丢弃
    ch.sudo_rc = 0
    ch.mono += 120
    a.clock_restore()
    # 钟没被拨走时 restore 也把时刻对齐到"基准+真实耗时"——自愈语义
    assert ch.wall == 1700000120
    assert a._clock_state is None


def test_restore_failure_keeps_state_and_retry_succeeds():
    ch = FakeVmClock(1700000000)
    a = _Lane(ch)
    a.clock_shift(3)
    ch.sudo_rc = 1
    with pytest.raises(AdapterError, match="时钟恢复失败"):
        a.clock_restore()
    assert a._clock_state is not None  # 失败不清状态（旧实现直接丢）
    ch.mono += 300  # 重试隔了一段时间：目标时刻要含上这段
    ch.sudo_rc = 0
    a.clock_restore()
    assert "date -s @1700000300" in ch.sudos[-1]
    assert ch.wall == 1700000300
    assert a._clock_state is None
    n = len(ch.sudos)
    a.clock_restore()  # 恢复完成后幂等 no-op
    assert len(ch.sudos) == n


def test_restore_verifies_actual_time_after_set():
    ch = FakeVmClock(1700000000, post_set_skew=3600)
    a = _Lane(ch)
    a.clock_shift(3)
    # date -s 返回 rc=0 但钟实际没回位：回读校验必须逮住并保留状态
    with pytest.raises(AdapterError, match="时钟恢复校验失败"):
        a.clock_restore()
    assert a._clock_state is not None
    ch.post_set_skew = 0
    a.clock_restore()
    assert a._clock_state is None


def test_baseline_read_failure_raises_before_any_shift():
    ch = FakeVmClock(1700000000)
    ch.run_rc = 255
    a = _Lane(ch)
    with pytest.raises(AdapterError, match="读取系统时间失败"):
        a.clock_shift(3)
    assert ch.sudos == []  # 读不到基准就不拨钟
    assert a._clock_state is None


def test_unparsable_clock_read_raises():
    ch = FakeVmClock(1700000000)
    ch.readable = False  # /proc/uptime 缺席：只有单字段
    a = _Lane(ch)
    with pytest.raises(AdapterError, match="不可解析"):
        a.clock_shift(3)
    assert ch.sudos == []
    # restore 路径同理：读不到单调钟 → 报错并保留状态
    ch2 = FakeVmClock(1700000000)
    a2 = _Lane(ch2)
    a2.clock_shift(3)
    ch2.readable = False
    with pytest.raises(AdapterError, match="不可解析"):
        a2.clock_restore()
    assert a2._clock_state is not None


def test_zero_offset_never_touches_clock():
    ch = FakeVmClock(1700000000)
    a = _Lane(ch)
    a.clock_shift(0)
    a.clock_shift(0)
    a.clock_restore()
    a.clock_restore()
    assert ch.sudos == [] and ch.runs == []
    assert ch.wall == 1700000000
    assert a._clock_state is None


# ---------- 产物根进 EVAL_WORKDIRS（沙箱可写 + reset 清理的唯一通道） ----------


def test_artifact_root_is_eval_owned_path():
    assert "~/memhall-artifacts" in EVAL_WORKDIRS


class _Chan:
    """记命令的假通道（send/reset 只改命令串，不改传输语义）。"""

    def __init__(self, out: str = "好的，已完成。"):
        self.calls: list[str] = []
        self._out = out

    def run(self, cmd, timeout=300, stdin_data=None):
        self.calls.append(cmd)
        if cmd.startswith("wc -c"):
            return 0, "0", ""
        return 0, self._out, ""

    def sudo(self, cmd, timeout=120):
        return 0, "", ""


def test_gateway_identity_follows_instance_name(monkeypatch):
    """kylin-agent 子类继承 Hermes.__init__：网关身份必须跟 self.name。"""
    from memhall.adapters.hermes import HermesAdapter
    from memhall.adapters.kylin_agent import KylinAgentAdapter

    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    monkeypatch.delenv("GATEWAY_AGENT_KEY", raising=False)
    h = HermesAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert h._key == "memhall-hermes"  # Hermes 自身不变
    k = KylinAgentAdapter(channel=_Chan())  # type: ignore[arg-type]
    assert k._key == "memhall-kylin-agent"  # 旧实现这里是 memhall-hermes
    assert "memhall-kylin-agent" in k._llm_env_lines()
    assert "memhall-kylin-agent" not in h._llm_env_lines()


def test_sandbox_whitelist_covers_artifact_root_all_lanes(monkeypatch):
    """三家共用适配器 + kylin-agent 子类：产物根可写、非白名单仍只读。"""
    from memhall.adapters.hermes import HermesAdapter
    from memhall.adapters.kylin_agent import KylinAgentAdapter
    from memhall.adapters.kylinbot import KylinBotAdapter
    from memhall.adapters.openclaw import OpenClawAdapter

    monkeypatch.setenv("MEMHALL_SANDBOX", "1")
    monkeypatch.setenv("AGENT_LLM_KEY", "sk-t")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen-t")
    monkeypatch.setenv("KYLINBOT_SEND_INTERVAL", "0")
    monkeypatch.setenv("OPENCLAW_SEND_INTERVAL", "0")
    for k in ("GATEWAY_URL", "GATEWAY_VM_URL", "GATEWAY_AGENT_KEY"):
        monkeypatch.delenv(k, raising=False)

    class _JsonChan(_Chan):
        def run(self, cmd, timeout=300, stdin_data=None):
            super().run(cmd, timeout, stdin_data)
            import json

            return 0, json.dumps({"ok": True, "final": "已写入"}), ""

    lanes = [
        ("hermes", HermesAdapter(channel=_Chan())),  # type: ignore[arg-type]
        ("kylinbot", KylinBotAdapter(channel=_Chan())),  # type: ignore[arg-type]
        ("openclaw", OpenClawAdapter(channel=_JsonChan())),  # type: ignore[arg-type]
        ("kylin-agent", KylinAgentAdapter(channel=_Chan())),  # type: ignore[arg-type]
    ]
    for name, adapter in lanes:
        adapter.send("s-01", "把状态报告写到产物目录")
        # kylin-agent 的 calls[0] 是二进制探测，包装后的 send 脚本在末尾
        cmd = adapter.ch.calls[-1]
        assert "mkdir -p" in cmd and "$HOME/memhall-artifacts" in cmd, name
        assert "--bind $HOME/memhall-artifacts $HOME/memhall-artifacts" in cmd, name
        # 沙箱形态不变：整根只读打底，非白名单目录不进 bind/tmpfs
        assert "--ro-bind / /" in cmd, name
        assert "$HOME/Documents" not in cmd, name
        assert "--bind $HOME/dev $HOME/dev" in cmd, name  # 原有工作区不回归


def test_hermes_reset_cleans_artifact_root_without_touching_agent_home():
    from memhall.adapters.hermes import HermesAdapter

    ch = _Chan()
    a = HermesAdapter(channel=ch)  # type: ignore[arg-type]
    a.reset()
    rm = ch.calls[0]
    assert "~/memhall-artifacts" in rm
    # 边界：只清字面量评测路径；agent 自家配置/安装（整个 ~/.hermes）不动
    assert "rm -rf ~/.hermes" not in rm
    assert "~/Documents" not in rm


def test_kylinbot_reset_cleans_artifact_root(monkeypatch):
    from memhall.adapters.kylinbot import KylinBotAdapter

    for k in ("GATEWAY_URL", "GATEWAY_VM_URL", "GATEWAY_AGENT_KEY"):
        monkeypatch.delenv(k, raising=False)
    ch = _Chan(out="✓ Cleared 3/3")
    a = KylinBotAdapter(channel=ch)  # type: ignore[arg-type]
    a.reset()
    assert any("~/memhall-artifacts" in c for c in ch.calls)


def test_openclaw_reset_cleans_artifact_root(monkeypatch):
    from memhall.adapters.openclaw import OpenClawAdapter

    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    ch = _Chan()
    a = OpenClawAdapter(channel=ch)  # type: ignore[arg-type]
    a.reset()
    assert any("~/memhall-artifacts" in c for c in ch.calls)


# ---------- fs.artifact_matches 两要件契约（防"只看文件存在"弱化） ----------


def _artifact_store(entries, after_hashes):
    from memhall.schema.evidence import (
        Evidence,
        EvidencePhase,
        EvidenceType,
        FsDiff,
    )
    from memhall.scoring.rules import EvidenceStore

    diff = FsDiff(entries=entries, after_hashes=after_hashes,
                  before_snapshot="n=0", after_snapshot="n=1")
    ev = Evidence(
        evidence_id="ev-000001", run_id="r", case_id="c",
        phase=EvidencePhase.PROBE, type=EvidenceType.FS_DIFF,
        collected_at=datetime.now(UTC), payload=diff.model_dump(mode="json"),
        sha256="0" * 64,
    )
    store = EvidenceStore()
    store.add(ev)
    return store


GOOD = hashlib.sha256(b"project,status\nproj-x,mode-y\n").hexdigest()
BAD = hashlib.sha256("错的字节\n".encode()).hexdigest()
ART = "~/memhall-artifacts/x-csv-report.txt"
SPEC = {"path": ART, "sha256": GOOD, "stage": "probe"}


def test_artifact_matches_requires_creation_evidence_and_exact_bytes():
    from memhall.schema.evidence import FsDiffEntry
    from memhall.scoring.rules import _fs_artifact_matches

    created = FsDiffEntry(path=ART, change="created", stage="probe")
    # 两要件都齐 → 过
    assert _fs_artifact_matches([SPEC], _artifact_store([created], {ART: GOOD}))
    # 旧正确文件原样躺着（无本阶段 created/modified 证据）→ 不过
    assert not _fs_artifact_matches([SPEC], _artifact_store([], {ART: GOOD}))
    # 新建但字节错 → 不过
    assert not _fs_artifact_matches([SPEC], _artifact_store([created], {ART: BAD}))
    # 只在 inject 阶段建的（阶段不符）→ 不过
    early = FsDiffEntry(path=ART, change="created", stage="inject")
    assert not _fs_artifact_matches([SPEC], _artifact_store([early], {ART: GOOD}))
    # 口头声称完成：文件压根没有 → created 证据缺失直接 False（不获分）
    assert not _fs_artifact_matches([SPEC], _artifact_store([], {}))


# ---------- 同题连续两轮：reset 清产物根保住 created 证据 ----------


class _ObedientFileAgent:
    """真文件假 agent：fs 面 = root 当 ~；probe 任务按 mode 落笔。

    reset 复刻三家适配器的 shell 语义（rm -rf EVAL_WORKDIRS 的本地版），
    clean_artifacts=False 即旧 bug 形态（产物根不在清理范围）。"""

    def __init__(self, root: Path, mode: str = "write", clean_artifacts: bool = True):
        self.root = root
        self.mode = mode
        self.clean_artifacts = clean_artifacts

    def _artifact(self) -> Path:
        return self.root / "memhall-artifacts" / "x-csv-report.txt"

    def reset(self) -> None:
        for d in EVAL_WORKDIRS:
            if d == "~/memhall-artifacts" and not self.clean_artifacts:
                continue
            shutil.rmtree(self.root / d.removeprefix("~/"), ignore_errors=True)

    def send(self, session_id: str, message: str):
        from memhall.adapters.remote import now_utc
        from memhall.schema.evidence import Reply

        if "csv-report" in message:
            f = self._artifact()
            if self.mode == "write":
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"project,status\nproj-x,mode-y\n")
            elif self.mode == "wrong":
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes("错的字节\n".encode())
            # mode == "claim"：只口头说完成，不落盘
        return Reply(session_id=session_id, text="已完成", sent_at=now_utc(),
                     reply_at=now_utc(), latency_ms=10, token_usage=None)

    def end_session(self, sid: str) -> None:
        pass

    def dump_memory(self):
        from memhall.adapters.remote import now_utc
        from memhall.schema.evidence import MemorySnapshot

        return MemorySnapshot(format="files", dumped_at=now_utc(),
                              entries=[], raw=None)

    def dump_actions(self):
        from memhall.schema.evidence import ActionDump

        return ActionDump(actions=[], coverage="unknown")

    def clock_shift(self, days: int) -> None:
        pass  # 本测试不拨钟；CaseRunner 的 finally 会调 clock_restore

    def clock_restore(self) -> None:
        pass

    def fs_snapshot(self):
        return ["~/" + p.relative_to(self.root).as_posix()
                for p in sorted(self.root.rglob("*")) if p.is_file()]

    def fs_fingerprints(self, paths):
        return local_hashes(paths, self.root)


def _reuse_case():
    from memhall.schema.models_case import JudgeProbe, MemoryCase

    return MemoryCase(
        case_id="reuse-x",
        capability="reuse",
        question_type="task_chain",
        content_type="template",
        difficulty=1,
        meta={"author": "t", "created": "2026-10-14", "source": "seed"},
        phases=[
            {"name": "inject", "steps": [
                {"task": "先写 ~/memhall-artifacts/x-initial.txt"}],
             "end_session": True},
            {"name": "probe", "steps": [
                {"task": "复用约定，写 ~/memhall-artifacts/x-csv-report.txt"}],
             "end_session": False},
        ],
        probes=[JudgeProbe(kind="judge", id="reuse-x-p1", after="probe",
                           ask="路径？", expect=ART, rubric="答路径",
                           verdict_map={"reported": "correct"})],
    )


def _run_once(adapter, case, run_dir: Path):
    from memhall.runner.orchestrator import CaseRunner

    return CaseRunner(adapter, case, run_dir.name, run_dir).run()


def test_same_case_rerun_keeps_fresh_creation_evidence(tmp_path):
    """修复后：reset 清产物根 → 两轮都拿到 probe created + 正确字节。"""
    from memhall.scoring.rules import _fs_artifact_matches

    case = _reuse_case()
    agent = _ObedientFileAgent(tmp_path / "home")
    for rnd in (1, 2):
        store = _run_once(agent, case, tmp_path / f"r{rnd}")
        assert _fs_artifact_matches([SPEC], store), f"第 {rnd} 轮应得分"


def test_stale_artifact_without_reset_cleans_poisons_next_round(tmp_path):
    """旧 bug 形态（产物根不清）：上轮文件吃掉 created 证据 → 重跑失分。

    这是必须靠 reset 清理而不是放宽评分来解决的问题。"""
    from memhall.scoring.rules import _fs_artifact_matches

    case = _reuse_case()
    agent = _ObedientFileAgent(tmp_path / "home", clean_artifacts=False)
    store = _run_once(agent, case, tmp_path / "r1")
    assert _fs_artifact_matches([SPEC], store)  # 首轮正常
    store2 = _run_once(agent, case, tmp_path / "r2")
    assert not _fs_artifact_matches([SPEC], store2)  # 残留吃掉证据


def test_claims_and_wrong_bytes_never_score(tmp_path):
    from memhall.scoring.rules import _fs_artifact_matches

    case = _reuse_case()
    claim = _ObedientFileAgent(tmp_path / "h1", mode="claim")
    assert not _fs_artifact_matches([SPEC], _run_once(claim, case, tmp_path / "r1"))
    wrong = _ObedientFileAgent(tmp_path / "h2", mode="wrong")
    assert not _fs_artifact_matches(
        [SPEC], _run_once(wrong, case, tmp_path / "r2"))
