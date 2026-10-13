"""A4 隔离防线直接测试（roadmap 2026-10-14：机制在、直接测试无）。

- _GlobalRunLock 跨进程互斥（R57）
- VmAdapterMixin 拨钟/恢复行为（R33：失败必须 AdapterError）
- A1 错钟残留检测：_clock_drift_seconds + run_suite 拒跑闸门
- A2 网关对账告警：有回复但零记账 → 绕行怀疑进 manifest
- A6 延迟一等指标：latency_summary + aggregate pass^k/延迟/token 列
- 本机四家 reset"清旧"断言（上一 case 的残留是跨轮污染源）
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from memhall.adapters.base import AdapterError
from memhall.adapters.vm_common import VmAdapterMixin
from memhall.runner.orchestrator import (
    _clock_drift_seconds,
    _gateway_bypass_alarm,
    _GlobalRunLock,
    run_suite,
)
from memhall.schema.evidence import Evidence, EvidencePhase, EvidenceType
from memhall.scoring.rules import EvidenceStore

# ---------- R57 全局锁互斥 ----------


def test_global_run_lock_mutex_and_release():
    with _GlobalRunLock(), pytest.raises(RuntimeError, match="已有评测进程在跑"), _GlobalRunLock():
        pass
    # 退出即释放（进程退出自动释放靠 OS，这里验证正常路径可重入）
    with _GlobalRunLock():
        pass


# ---------- R33 VM 拨钟/恢复 ----------


class _ClockChannel:
    """假 VM 时钟通道：wall 可被 date -s 拨动，monotonic 独立推进。

    mono 由测试显式拨（ch.mono += 600 模拟用例真实耗时），不挂真实时间，
    恢复目标可断言到秒。"""

    def __init__(self, epoch: int, mono: float = 1000.0, sudo_rc: int = 0):
        self.wall = float(epoch)
        self.mono = mono
        self.sudo_rc = sudo_rc
        self.sudos: list[str] = []

    def run(self, cmd, timeout=300, stdin_data=None):
        if "date +%s" in cmd and "/proc/uptime" in cmd:
            return 0, f"{int(self.wall)} {self.mono:.2f}\n", ""
        return 0, "", ""

    def _apply(self, cmd):
        m = re.match(r"date -s @(\d+)", cmd)
        if m:
            self.wall = int(m.group(1))
        else:
            m = re.match(r"date -s '\+(\d+) days'", cmd)
            self.wall += int(m.group(1)) * 86400

    def sudo(self, cmd, timeout=120):
        self.sudos.append(cmd)
        if self.sudo_rc:
            return self.sudo_rc, "", "boom"
        self._apply(cmd)
        return 0, "ok", ""


class _VmLane(VmAdapterMixin):
    name = "fake-vm"

    def __init__(self, ch):
        self.ch = ch


def test_clock_shift_zero_days_is_noop():
    ch = _ClockChannel(1700000000)
    _VmLane(ch).clock_shift(0)
    assert ch.sudos == []


def test_clock_shift_and_restore_roundtrip():
    """恢复必须包含真实耗时：起点 1700000000、用例走 600s → 恢复到
    1700000600（评审复现：旧实现回拨 1700000000，每 case 白吃一段耗时，
    攒过 ±300s 漂移闸门后下一场被拒跑）。"""
    ch = _ClockChannel(1700000000)
    a = _VmLane(ch)
    a.clock_shift(30)
    assert "date -s '+30 days'" in ch.sudos[0]
    assert ch.wall == 1700000000 + 30 * 86400  # 拨钟在 VM 侧生效
    ch.mono += 600  # 用例真实耗时（单调钟，date -s 拨不动）
    a.clock_restore()
    assert "date -s @1700000600" in ch.sudos[1]  # 原 epoch + 耗时，非原 epoch
    assert ch.wall == 1700000600
    assert a._clock_state is None
    a.clock_restore()  # 无挂起基准：幂等无操作
    assert len(ch.sudos) == 2


def test_clock_shift_and_restore_failures_raise_adapter_error():
    a = _VmLane(_ClockChannel(1700000000, sudo_rc=1))
    with pytest.raises(AdapterError, match="拨钟失败"):
        a.clock_shift(7)
    a = _VmLane(_ClockChannel(1700000000))
    a.clock_shift(7)
    a.ch.sudo_rc = 1
    with pytest.raises(AdapterError, match="时钟恢复失败"):
        a.clock_restore()
    assert a._clock_state is not None  # 失败不清状态：下一 case finally 重试


# ---------- A1 错钟残留检测 ----------


def test_clock_drift_seconds_none_for_local_and_unreadable(monkeypatch):
    import memhall.adapters.remote as remote

    # 本机车道（无 ch / 非 SSH 通道）：同一块钟，无漂移可言
    assert _clock_drift_seconds(SimpleNamespace()) is None
    assert _clock_drift_seconds(SimpleNamespace(ch=object())) is None

    class _DeadSsh:
        def run(self, cmd, timeout=300, stdin_data=None):
            return 255, "", "unreachable"

    monkeypatch.setattr(remote, "SshChannel", _DeadSsh)
    assert _clock_drift_seconds(SimpleNamespace(ch=_DeadSsh())) is None


def test_clock_drift_seconds_measures_gap(monkeypatch):
    import memhall.adapters.remote as remote

    class _FakeSsh:
        def __init__(self, epoch):
            self.epoch = epoch

        def run(self, cmd, timeout=300, stdin_data=None):
            return 0, f"{self.epoch}\n", ""

    monkeypatch.setattr(remote, "SshChannel", _FakeSsh)
    drift = _clock_drift_seconds(SimpleNamespace(ch=_FakeSsh(int(time.time()) - 5)))
    assert drift is not None and -6.5 < drift < -4.5


def test_run_suite_refuses_on_excess_clock_drift(tmp_path, monkeypatch):
    from memhall.adapters.mock import MockAdapter
    from memhall.datasets import load_pack
    from memhall.runner import orchestrator

    monkeypatch.setattr(orchestrator, "_clock_drift_seconds", lambda a: 432000.0)
    case = load_pack("quick").cases[0]
    with pytest.raises(RuntimeError, match="系统钟相差"):
        run_suite(MockAdapter(), [case], tmp_path / "runs", "mock")
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())  # 拒跑不留半截 run 目录


# ---------- A2 网关对账告警 ----------


def _store_with_dialogue(case_id: str, t0: datetime, n_real: int = 2, shifted: bool = False) -> EvidenceStore:
    replies = [
        {
            "session_id": "s-01",
            "text": f"答{i}",
            "sent_at": t0.isoformat(),
            "reply_at": t0.isoformat(),
            "latency_ms": 1000,
        }
        for i in range(n_real)
    ]
    replies.append(
        {
            "session_id": "s-01",
            "text": "[RUNTIME_ERROR] x",
            "sent_at": t0.isoformat(),
            "reply_at": t0.isoformat(),
            "latency_ms": 0,
        }
    )
    ev = Evidence(
        evidence_id="ev-000001",
        run_id="r",
        case_id=case_id,
        phase=EvidencePhase.PROBE,
        type=EvidenceType.DIALOGUE,
        collected_at=t0,
        clock_offset_days=3 if shifted else 0,
        payload={"messages": ["q"], "replies": replies},
        sha256="0" * 64,
    )
    store = EvidenceStore()
    store.add(ev)
    return store


def _write_gateway_log(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def test_gateway_alarm_zero_records_suspects_case(tmp_path, monkeypatch):
    import memhall.gateway as gw

    log = tmp_path / "gateway-usage.jsonl"
    _write_gateway_log(log, [{"ts": "2020-01-01T00:00:00+00:00", "agent": "memhall-other", "status": 200}])
    monkeypatch.setattr(gw, "default_log_path", lambda: log)
    now = datetime.now(UTC)
    stores = [_store_with_dialogue("c1", now)]
    alarm = _gateway_bypass_alarm(SimpleNamespace(ch=None), stores, "hermes", "gateway", [])
    assert alarm is not None
    assert alarm["n_replies"] == 2
    assert alarm["n_tag_records"] == 0
    assert alarm["suspect_cases"] == ["c1"]
    assert "绕行" in alarm["note"]


def test_gateway_alarm_quiet_when_records_cover_windows(tmp_path, monkeypatch):
    import memhall.gateway as gw

    log = tmp_path / "gateway-usage.jsonl"
    now = datetime.now(UTC)
    _write_gateway_log(
        log,
        [
            {"ts": now.isoformat(), "agent": "memhall-hermes", "status": 200},
            {
                "ts": (now - timedelta(days=400)).isoformat(),
                "agent": "memhall-hermes",
                "status": 200,
            },  # 上一场：不影响本轮
        ],
    )
    monkeypatch.setattr(gw, "default_log_path", lambda: log)
    stores = [_store_with_dialogue("c1", now)]
    assert _gateway_bypass_alarm(SimpleNamespace(ch=None), stores, "hermes", "gateway", []) is None


def test_gateway_alarm_skips_shifted_cases_and_non_gateway_modes(tmp_path, monkeypatch):
    import memhall.gateway as gw

    log = tmp_path / "gateway-usage.jsonl"
    _write_gateway_log(log, [])
    monkeypatch.setattr(gw, "default_log_path", lambda: log)
    now = datetime.now(UTC)
    shifted = [_store_with_dialogue("c1", now, shifted=True)]
    # 拨过钟的 case 不做逐 case 归因；但整本账单零记录仍触发整体告警
    alarm = _gateway_bypass_alarm(SimpleNamespace(ch=None), shifted, "hermes", "gateway", [])
    assert alarm is not None and "suspect_cases" not in alarm
    # 直连/未配置模式：无从对账，静默
    assert _gateway_bypass_alarm(SimpleNamespace(ch=None), shifted, "hermes", "direct", []) is None
    # 无真实回复（全是 RUNTIME_ERROR 占位）：没有"该来没来"
    empty = [_store_with_dialogue("c1", now, n_real=0)]
    assert _gateway_bypass_alarm(SimpleNamespace(ch=None), empty, "hermes", "gateway", []) is None


def test_gateway_alarm_unreadable_log_is_silent(tmp_path, monkeypatch):
    import memhall.adapters.remote as remote
    import memhall.gateway as gw

    class _NoLogSsh:
        def run(self, cmd, timeout=300, stdin_data=None):
            return 1, "", "no such file"

    monkeypatch.setattr(gw, "default_log_path", lambda: tmp_path / "nope.jsonl")
    monkeypatch.setattr(remote, "SshChannel", _NoLogSsh)
    now = datetime.now(UTC)
    stores = [_store_with_dialogue("c1", now)]
    assert _gateway_bypass_alarm(SimpleNamespace(ch=_NoLogSsh()), stores, "hermes", "gateway", []) is None


# ---------- 本机四家 reset"清旧"（上一 case 残留 = 跨轮污染源） ----------


def _seed(path: Path, content: str = "stale") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_claude_local_reset_cleans_old(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("CLAUDE_LLM_KEY", "dummy")
    from memhall.adapters.claude_local import LocalClaudeAdapter

    a = LocalClaudeAdapter(root=tmp_path)
    _seed(a.config_dir / "projects" / "p" / "memory" / "MEMORY.md")
    _seed(a.workspace / "junk.txt")
    a.reset()
    assert not (a.config_dir / "projects").exists()
    assert not (a.workspace / "junk.txt").exists()
    assert a.config_dir.is_dir() and a.workspace.is_dir()


def test_hermes_local_reset_cleans_old(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_KEY", "dummy")
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    monkeypatch.delenv("GATEWAY_VM_URL", raising=False)
    from memhall.adapters.hermes_local import LocalHermesAdapter

    a = LocalHermesAdapter(root=tmp_path)
    _seed(a.mem_dir / "MEMORY.md")
    _seed(a.workspace / "junk.txt")
    a.reset()
    assert not (a.mem_dir / "MEMORY.md").exists()
    assert not (a.workspace / "junk.txt").exists()
    assert a.mem_dir.is_dir() and a.workspace.is_dir()


def test_qwen_local_reset_cleans_old_and_strips_hooks(tmp_path, monkeypatch):
    settings = tmp_path / "qwen-settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {"clawd": ["run-me"]},
                "modelProviders": {"p": {"baseUrl": "https://x", "envKey": "K"}},
            }
        ),
        encoding="utf-8",
    )
    from memhall.adapters.qwen_local import LocalQwenAdapter

    a = LocalQwenAdapter(root=tmp_path / "sandbox")
    monkeypatch.setattr(a, "_settings_src", lambda: settings)
    _seed(a.qwen_home / "QWEN.md")
    _seed(a.workspace / "junk.txt")
    a.reset()
    assert not (a.qwen_home / "QWEN.md").exists()
    assert not (a.workspace / "junk.txt").exists()
    # 用户配置原样拷进沙箱但剥掉 hooks（clawd 钩子不进评测）
    data = json.loads((a.qwen_home / "settings.json").read_text(encoding="utf-8"))
    assert "hooks" not in data and "modelProviders" in data


def test_opencode_reset_cleans_old(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("AGENT_LLM_KEY", "dummy")
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    monkeypatch.delenv("GATEWAY_VM_URL", raising=False)
    from memhall.adapters.opencode import OpenCodeAdapter

    a = OpenCodeAdapter(root=tmp_path)
    _seed(a.cfg_dir / "opencode.json")
    _seed(a.data_dir / "db.sqlite")
    _seed(a.workspace / "junk.txt")
    a.reset()
    assert not (a.data_dir / "db.sqlite").exists()
    assert not (a.workspace / "junk.txt").exists()
    # 配置重建：R36 权限面（bash/webfetch deny）不被上一轮残留顶掉
    cfg = json.loads((a.cfg_dir / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["permission"] == {"edit": "allow", "bash": "deny", "webfetch": "deny"}


# ---------- A6 延迟一等指标 ----------


def test_latency_summary_skips_placeholders(tmp_path):
    from memhall.report.metrics import latency_summary

    cdir = tmp_path / "cases" / "c1"
    cdir.mkdir(parents=True)
    dialogue = {
        "type": "dialogue",
        "phase": "probe",
        "payload": {
            "replies": [
                {"text": "a", "latency_ms": 100},
                {"text": "b", "latency_ms": 300},
                {"text": "c", "latency_ms": 9000},
                {"text": "[RUNTIME_ERROR]", "latency_ms": 0},  # 占位回复不计
            ]
        },
    }
    (cdir / "evidence.jsonl").write_text(
        json.dumps(dialogue)
        + "\n"
        + json.dumps({"type": "memory_snapshot", "payload": {}})
        + "\n"
        + "{broken json\n",
        encoding="utf-8",
    )
    lat = latency_summary(tmp_path)
    # p95 线性插值：k=(3-1)*0.95=1.9 → 300+(9000-300)*0.9=8130
    assert lat == {"n": 3, "p50_ms": 300, "p95_ms": 8130, "max_ms": 9000, "mean_ms": 3133}
    assert latency_summary(tmp_path / "absent") is None


def test_pass_k_requires_all_rounds_scored():
    from memhall.report.aggregate import _pass_k

    pool = {"__overall__": {"f": {"p1": [1.0, 1.0], "p2": [1.0, 0.0], "p3": [None, 1.0]}}}
    pk = _pass_k(pool, "__overall__", 2)
    assert pk == {"k": 2, "rate": 0.5, "n_probes": 2}  # p3 缺一轮不进分母
    assert _pass_k(pool, "__overall__", 1) is None
    assert _pass_k({}, "__overall__", 2) is None


def _mk_agg_run(tmp_path: Path, run_id: str, case, verdict_bits, p95_ms=None, tokens=None) -> Path:
    import yaml

    d = tmp_path / run_id
    cdir = d / "cases" / case.case_id
    cdir.mkdir(parents=True)
    case = case.model_copy(
        update={
            "probes": [
                case.probes[0].model_copy(update={"id": f"{case.case_id}-p{i}"})
                for i in range(1, len(verdict_bits) + 1)
            ]
        }
    )
    (cdir / "case.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
    )
    rows = [
        {
            "verdict_id": f"v-{i:04d}",
            "probe_id": f"{case.case_id}-p{i}",
            "case_id": case.case_id,
            "run_id": run_id,
            "verdict": "correct" if bit else "confusion",
            "decided_by": "rule",
            "confidence": 1.0,
            "explanation": "",
        }
        for i, bit in enumerate(verdict_bits, 1)
    ]
    (d / "verdicts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    n = len(verdict_bits)
    score = sum(verdict_bits) / n
    metrics = {
        "overall_score": score,
        "capability_scores": {"persist": score},
        "n_valid": n,
        "n_invalid_run": 0,
        "n_human_review": 0,
    }
    if p95_ms is not None:
        metrics["latency"] = {
            "n": n,
            "p50_ms": p95_ms // 2,
            "p95_ms": p95_ms,
            "max_ms": p95_ms,
            "mean_ms": p95_ms // 2,
        }
    manifest = {"adapter": "mock", "cases": [case.case_id], "run_id": run_id}
    if tokens is not None:
        manifest["token_usage"] = {"requests": 9, "total_tokens": tokens}
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
    return d


def test_aggregate_reports_pass_k_latency_and_tokens(tmp_path):
    from memhall.report.aggregate import aggregate_runs, format_table
    from memhall.schema.models_case import JudgeProbe, MemoryCase

    case = MemoryCase(
        case_id="persist-y",
        capability="persist",
        question_type="cross_session_recall",
        content_type="fact",
        difficulty=1,
        meta={"author": "t", "created": "2026-10-14", "source": "seed"},
        phases=[{"name": "probe", "steps": [{"user": "风格名？"}]}],
        probes=[
            JudgeProbe(
                kind="judge",
                id="persist-y-p1",
                after="probe",
                ask="风格名？",
                expect="mode-x",
                rubric="答 mode-x=对",
                verdict_map={"reported": "correct", "forgot": "omission"},
            )
        ],
    )
    a = _mk_agg_run(tmp_path, "20261014-000001-mock", case, [1, 1], p95_ms=41000, tokens=123456)
    b = _mk_agg_run(tmp_path, "20261014-000002-mock", case, [1, 0], p95_ms=52000, tokens=234567)
    r = aggregate_runs([a, b], tmp_path / "agg")
    assert r["pass_k"] == {"k": 2, "rate": 0.5, "n_probes": 2}
    assert r["capabilities"]["persist"]["pass_k"]["rate"] == 0.5
    assert r["runs"][0]["p95_latency_ms"] == 41000
    assert r["runs"][1]["total_tokens"] == 234567
    table = format_table(r)
    assert "pass^2" in table and "50.0%" in table
    assert "回复延迟 p95（各轮）：41s / 52s" in table
    assert "Token 消耗（各轮，网关记账）：123,456 / 234,567" in table
    assert "合计 358,023" in table
