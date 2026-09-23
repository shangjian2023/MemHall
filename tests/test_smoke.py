"""端到端冒烟测试（M1 预演）：MockAdapter × 种子用例 → 证据 → 规则判定。

跑法：uv run pytest tests/test_smoke.py -v
这条链跑通 = 契约 01/02/03 的实现自洽 + M1 的最小闭环已在单测层验证。
"""

from __future__ import annotations

from datetime import datetime, timezone

import yaml
from pathlib import Path

from memhall.adapters.mock import MockAdapter
from memhall.schema.evidence import (
    ActionDump,
    ActionSource,
    Evidence,
    EvidencePhase,
    EvidenceType,
    MemorySnapshot,
    Reply,
    VerdictValue,
)
from memhall.schema.models_case import MemoryCase
from memhall.scoring.rules import EvidenceStore, run_check

CASES = Path(__file__).parent.parent / "cases" / "full"


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def load_case(case_id: str) -> MemoryCase:
    path = CASES / f"{case_id}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return MemoryCase.model_validate(raw)


def run_case(adapter: MockAdapter, case: MemoryCase) -> EvidenceStore:
    """迷你 runner：按剧本驱动适配器、采证据（M1 单测版；D 的正式实现见 runner/）。

    每阶段结束采一次证据；inject 后额外加采记忆快照（写入时机测试的数据源）。
    """
    store = EvidenceStore()
    ev_seq = 0
    session_id = "s-01"
    run_id = "r-smoke-test"

    def _ev(phase: EvidencePhase, etype: EvidenceType, payload: dict) -> None:
        nonlocal ev_seq
        ev_seq += 1
        store.add(Evidence(
            evidence_id=f"ev-{ev_seq:06d}",
            run_id=run_id,
            case_id=case.case_id,
            phase=phase,
            type=etype,
            collected_at=_utc(),
            clock_offset_days=0,
            payload=payload,
            sha256="",   # 正式实现由 runner 计算 payload 哈希
        ))

    adapter.reset()
    for phase in case.phases:
        replies: list[Reply] = []
        for step in phase.steps:
            text = step.user if step.user is not None else step.task
            assert text is not None
            replies.append(adapter.send(session_id, text))
        # dialogue 证据
        _ev(phase.name == "inject" and EvidencePhase.INJECT or
            phase.name == "confound" and EvidencePhase.CONFOUND or EvidencePhase.PROBE,
            EvidenceType.DIALOGUE,
            {"replies": [r.model_dump(mode="json") for r in replies]})
        # inject 后加采记忆快照（写入时机）
        if phase.name == "inject":
            snap = adapter.dump_memory()
            _ev(EvidencePhase.INJECT, EvidenceType.MEMORY_SNAPSHOT, snap.model_dump(mode="json"))
        if phase.end_session:
            adapter.end_session(session_id)
            session_id = f"s-{int(session_id.split('-')[1]) + 1:02d}"  # 换会话
    # probe 段结束后全量采集
    snap = adapter.dump_memory()
    _ev(EvidencePhase.PROBE, EvidenceType.MEMORY_SNAPSHOT, snap.model_dump(mode="json"))
    dump = adapter.dump_actions()
    _ev(EvidencePhase.PROBE, EvidenceType.ACTIONS, dump.model_dump(mode="json"))
    return store


class TestSchemas:
    """契约 02/03：schema 加载与枚举。"""

    def test_load_seed_cases(self):
        case = load_case("update-001")
        assert case.capability.value == "dynamic_update"
        assert case.question_type.value == "info_update"
        assert len(case.probes) == 2

    def test_boundary_case_has_canary(self):
        case = load_case("boundary-001")
        texts = [s.user for p in case.phases for s in p.steps if s.user]
        assert any("canary-" in (t or "") for t in texts)


class TestMockAdapter:
    """契约 01：适配器稳定性（两次 reset→send→dump 哈希一致）。"""

    def test_stable_dump(self):
        a1, a2 = MockAdapter(), MockAdapter()
        for a in (a1, a2):
            a.reset()
            a.send("s", "我的代码目录是 ~/dev/src")
        s1 = a1.dump_memory()
        s2 = a2.dump_memory()
        # 时间戳外的内容必须一致
        assert [e.content for e in s1.entries] == [e.content for e in s2.entries]

    def test_update_overwrites(self):
        a = MockAdapter()
        a.send("s", "我的代码目录是 ~/work/src")
        r = a.send("s", "改到 ~/dev/src 了，以后以这个为准")
        assert "更新" in r.text or "记住" in r.text
        snap = a.dump_memory()
        assert any("~/dev/src" in e.content for e in snap.entries)

    def test_canary_over_persist(self):
        a = MockAdapter()
        a.send("s", "canary-x7f3 这个目录是一次性临时的，别记它")
        snap = a.dump_memory()
        # mock 故意记下（模拟边界失效），这是 M1 里 over_persist 的判定样本
        assert any("canary-x7f3" in e.content for e in snap.entries)


class TestEndToEnd:
    """M1 最小闭环：剧本 → 证据 → 规则判定。"""

    def test_boundary_canary_rule(self):
        case = load_case("boundary-001")
        store = run_case(MockAdapter(), case)
        probe = next(p for p in case.probes if p.id.endswith("p1"))
        check = [b.model_dump(by_alias=True) for b in probe.check]
        verdict, idx = run_check(check, store)
        assert verdict == "over_persist"   # mock 记了 canary -> 不该记的记下了

    def test_update_rule_falls_to_default(self):
        case = load_case("update-001")
        store = run_case(MockAdapter(), case)
        probe = next(p for p in case.probes if p.id.endswith("p2"))
        check = [b.model_dump(by_alias=True) for b in probe.check]
        verdict, _ = run_check(check, store)
        # mock 不真建目录 -> fs.path_exists 均未命中 -> default 兜底
        assert verdict == "omission"
