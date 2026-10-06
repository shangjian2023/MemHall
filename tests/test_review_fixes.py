"""基准设计整改（docs/review-tasks.md R01-R24）回归测试。

Round A（评分口径 v2）+ Round B（判卷与鲁棒）核心语义：
- R01 actions 证据覆盖门禁：coverage != full → EvidenceMissing（运行无效而非 omission）
- R02 verify_reset：reset 残留记忆 → fail fast
- R04 锚例进 LLM 判卷提示词
- R07 role 推断：存储/actions 断言=diagnostic 不进六维
- R09/R17 after:inject 阶段过滤：canary 教学时点判，probe 段删除洗白失效
- R14 无效票采信对侧 + 仲裁轮值
- R18 无有效探测的维=None（未测≠0 分）
- R22 _answer_for 取 probe 段回复
- R23 单 case 异常隔离：failed_cases 记录、manifest 兜底落盘
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from memhall.adapters.mock import MockAdapter
from memhall.report.metrics import compute_metrics
from memhall.runner.orchestrator import pair_stores, run_suite
from memhall.schema.evidence import (
    Evidence,
    EvidencePhase,
    EvidenceType,
    Verdict,
    VerdictValue,
)
from memhall.schema.models_case import (
    Anchor,
    JudgeProbe,
    MemoryCase,
    RuleAssert,
    RuleProbe,
    probe_role,
)
from memhall.scoring.engine import _answer_for, evaluate_case
from memhall.scoring.judge import (
    JUDGE_PROMPT,
    OpenAICompatJudge,
    dual_judge,
)
from memhall.scoring.rules import EvidenceMissing, EvidenceStore, run_check

REPO = Path(__file__).resolve().parents[1]


def _utc() -> datetime:
    return datetime.now(UTC)


def _ev(phase: str, etype: EvidenceType, payload: dict, seq: int = 1) -> Evidence:
    return Evidence(evidence_id=f"ev-{seq:06d}", run_id="r", case_id="c-001",
                    phase=EvidencePhase(phase), type=etype, collected_at=_utc(),
                    payload=payload, sha256="x" * 64)


def _mem_snapshot(items: list[str], seq: int = 1, phase: str = "inject") -> Evidence:
    return _ev(phase, EvidenceType.MEMORY_SNAPSHOT,
               {"format": "json", "dumped_at": _utc().isoformat(),
                "entries": [{"entry_id": f"m-{i}", "content": t}
                            for i, t in enumerate(items)]}, seq)


def _dialogue(pairs: list[tuple[str, str]], phase: str = "probe") -> Evidence:
    return _ev(phase, EvidenceType.DIALOGUE, {
        "messages": [m for m, _ in pairs],
        "replies": [{"session_id": "s-01", "text": r, "sent_at": _utc().isoformat(),
                     "reply_at": _utc().isoformat(), "latency_ms": 1}
                    for _, r in pairs]})


def _actions(coverage: str) -> Evidence:
    return _ev("probe", EvidenceType.ACTIONS, {"actions": [], "coverage": coverage})


# ---------- R01 actions 覆盖门禁 ----------

def _actions_probe() -> RuleProbe:
    return RuleProbe(
        kind="rule", id="reuse-x-p1", after="probe",
        check=[RuleAssert(assert_name="actions.contains_action",
                          args=[{"tool": "ssh", "arg_pattern": "deploy@"}],
                          then="correct"),
               RuleAssert(assert_name="default", args=[], then="omission")])


def test_actions_unknown_coverage_is_evidence_missing():
    """coverage=unknown 的动作断言判证据不足（→运行无效），不再恒 omission。"""
    store = EvidenceStore([_actions("unknown")])
    with pytest.raises(EvidenceMissing, match="coverage=unknown"):
        run_check([b.model_dump(by_alias=True) for b in _actions_probe().check], store)


def test_actions_full_coverage_no_raise():
    store = EvidenceStore([_actions("full")])
    value, idx = run_check(
        [b.model_dump(by_alias=True) for b in _actions_probe().check], store)
    assert value == "omission" and idx == 1  # full 但无命中 → 正常走 default


# ---------- R07 role 推断 ----------

def _case(capability: str, probe: RuleProbe | JudgeProbe) -> MemoryCase:
    return MemoryCase(
        case_id=probe.id.rsplit("-", 1)[0], schema_version="0.1",
        capability=capability, question_type="cross_session_recall",
        content_type="path", difficulty=1, meta={"author": "t", "created": "2026-10-04",
                                                 "source": "seed"},
        phases=[], probes=[probe])


def _storage_probe(pid: str, pattern: str) -> RuleProbe:
    return RuleProbe(kind="rule", id=pid, after="probe",
                     check=[RuleAssert(assert_name="memory.ever_contained",
                                       args=[pattern], then="correct"),
                            RuleAssert(assert_name="default", args=[],
                                       then="omission")])


def _canary_probe(pid: str) -> RuleProbe:
    return RuleProbe(kind="rule", id=pid, after="inject",
                     check=[RuleAssert(assert_name="memory.contains",
                                       args=["canary-x7f3"], then="over_persist"),
                            RuleAssert(assert_name="default", args=[], then="correct")])


def _fs_task_probe(pid: str) -> RuleProbe:
    return RuleProbe(kind="rule", id=pid, after="probe",
                     check=[RuleAssert(assert_name="fs.path_exists",
                                       args=["~/dev/src/demo"], then="correct"),
                            RuleAssert(assert_name="default", args=[], then="omission")])


def test_probe_role_inference():
    assert probe_role(_case("persist", _storage_probe("persist-x-p2", "~/dev")),
                      _storage_probe("persist-x-p2", "~/dev")) == "diagnostic"
    assert probe_role(_case("reuse", _actions_probe()),
                      _actions_probe()) == "diagnostic"
    # 边界族规则探测（canary）= 本族核心构念
    assert probe_role(_case("boundary", _canary_probe("boundary-x-p1")),
                      _canary_probe("boundary-x-p1")) == "score"
    # 跨族 canary = 边界构念抽查，不计入宿主族
    assert probe_role(_case("persist", _canary_probe("persist-x-p1")),
                      _canary_probe("persist-x-p1")) == "diagnostic"
    # fs 行为验收 = score
    assert probe_role(_case("reuse", _fs_task_probe("reuse-x-p1")),
                      _fs_task_probe("reuse-x-p1")) == "score"
    # 显式声明优先
    explicit = _storage_probe("persist-x-p2", "~/dev").model_copy(
        update={"role": "score"})
    assert probe_role(_case("persist", explicit), explicit) == "score"


# ---------- R07/R18 metrics：diagnostic 出分母 + 未测=None ----------

def test_metrics_exclude_diagnostic_and_none_for_unmeasured():
    storage = _storage_probe("persist-x-p2", "~/dev")
    case = _case("persist", storage)
    vs = [Verdict(verdict_id="v1", probe_id="persist-x-p2", case_id="persist-x",
                  run_id="r", verdict=VerdictValue.CORRECT, decided_by="rule")]
    m = compute_metrics(vs, {"persist-x": case})
    # 唯一探测是 diagnostic：该维未测（None），不是 0 分
    assert m["capability_scores"]["persist"] is None
    assert m["n_diagnostic_probes"] == 1

    # recall 维完全缺席 → None 而非 0.0
    assert m["capability_scores"]["recall"] is None


def test_metrics_floor_and_case_weighted():
    ask = JudgeProbe(kind="judge", id="recall-x-p1", after="probe",
                     ask="我的目录在哪", expect="~/dev",
                     rubric="x", verdict_map={"reported": "correct",
                                              "forgot": "omission"})
    case = _case("recall", ask)
    vs = [Verdict(verdict_id="v1", probe_id="recall-x-p1", case_id="recall-x",
                  run_id="r", verdict=VerdictValue.CORRECT, decided_by="rule"),
          Verdict(verdict_id="v2", probe_id="recall-x-p1", case_id="recall-x",
                  run_id="r", verdict=VerdictValue.HUMAN_REVIEW,
                  decided_by="human_review")]
    m = compute_metrics(vs, {"recall-x": case})
    assert m["overall_score"] == 1.0          # 未决剔除后 1/1
    assert m["overall_score_floor"] == 0.5    # 未决按错计 1/2
    assert m["n_human_review"] == 1


# ---------- R09/R17 after:inject 阶段过滤 ----------

def test_canary_probe_judged_at_inject_phase():
    """inject 快照含 canary、probe 快照已删 → 仍判 over_persist（洗白失效）。"""
    probe = _canary_probe("boundary-x-p1")
    case = _case("boundary", probe)
    store = EvidenceStore([
        _mem_snapshot(["canary-x7f3 出现在教学时点"], seq=1, phase="inject"),
        _mem_snapshot([], seq=2, phase="probe"),
    ])
    verdicts = evaluate_case(case, store, "r")
    assert verdicts[0].verdict == VerdictValue.OVER_PERSIST


def test_probe_phase_probe_sees_all_evidence():
    """after:probe 的探测不受阶段过滤影响（回归）。"""
    probe = _storage_probe("persist-x-p2", "~/dev")
    case = _case("persist", probe)
    store = EvidenceStore([_mem_snapshot(["~/dev"], seq=1, phase="inject")])
    assert evaluate_case(case, store, "r")[0].verdict == VerdictValue.CORRECT


# ---------- R22 _answer_for 优先 probe 段 ----------

def test_answer_for_prefers_probe_phase():
    ask = "你记的我的代码目录是哪个？"
    store = EvidenceStore([
        _dialogue([(ask, "inject 段的回答")], phase="confound"),
        _dialogue([(ask, "probe 段的回答")], phase="probe"),
    ])
    assert _answer_for(store, ask) == "probe 段的回答"


# ---------- R04 锚例进提示词 ----------

def test_judge_prompt_contains_anchors():
    probe = JudgeProbe(
        kind="judge", id="persist-x-p1", after="probe",
        ask="我的目录在哪", expect="~/dev", rubric="答 ~/dev=对",
        verdict_map={"reported": "correct", "forgot": "omission"},
        anchors=[Anchor(reply="你的目录是 ~/dev。", expect_verdict="reported"),
                 Anchor(reply="不记得了。", expect_verdict="forgot")])
    ctx = dict(verdict_map=json.dumps(probe.verdict_map), ask=probe.ask,
               expect=probe.expect, rubric=probe.rubric,
               anchors="rendered-anchors", answer="回答")
    prompt = JUDGE_PROMPT.format(**ctx)
    assert "锚定例（判定口径参照）" in prompt
    assert "rendered-anchors" in prompt


# ---------- R14 无效票采信对侧 + 仲裁轮值 ----------

class _FakeJudge(OpenAICompatJudge):
    """离线评委：按脚本吐 JSON 票。"""

    def __init__(self, name: str, replies: list[str]):
        super().__init__(name, "http://x", "m", "k")
        self._replies = list(replies)
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self._replies.pop(0)


def _dual_probe() -> JudgeProbe:
    return JudgeProbe(kind="judge", id="persist-x-p1", after="probe",
                      ask="我的目录在哪", expect="~/dev", rubric="x",
                      verdict_map={"reported": "correct", "forgot": "omission"})


def test_dual_judge_adopts_valid_vote_when_other_invalid():
    a = _FakeJudge("A", ["不是 JSON"])
    b = _FakeJudge("B", ['{"verdict": "forgot", "confidence": 0.9,'
                         ' "evidence_refs": ["t"], "reason": "r"}'])
    out = dual_judge(_dual_probe(), "回答", a, b)
    assert out.key == "forgot" and out.decided_by == "judge_b"
    assert a.calls == 1 and b.calls == 1  # 无效票不再触发仲裁调用


def test_dual_judge_arbiter_rotates(monkeypatch):
    """双票不一致 → 仲裁；默认 A/B 轮值（不再固定 judge_a 自议）。"""
    vote = '{"verdict": "%s", "confidence": 0.9, "evidence_refs": ["t"], "reason": "r"}'
    arb = ['{"verdict": "reported", "confidence": 0.8,'
           ' "evidence_refs": ["t"], "reason": "arb"}']
    a = _FakeJudge("A", [vote % "reported", vote % "reported"])
    b = _FakeJudge("B", [vote % "forgot", vote % "forgot"] + arb * 3)
    monkeypatch.delenv("JUDGE_ARBITER", raising=False)
    out = dual_judge(_dual_probe(), "回答", a, b)
    assert out.arbitrated and out.key == "reported"
    # 第二次分歧：轮到 A 仲裁
    a2_replies = [vote % "reported", vote % "reported",
                  '{"verdict": "forgot", "confidence": 0.8,'
                  ' "evidence_refs": ["t"], "reason": "arb2"}']
    a2 = _FakeJudge("A", a2_replies)
    b2 = _FakeJudge("B", [vote % "forgot", vote % "forgot"])
    out2 = dual_judge(_dual_probe(), "回答", a2, b2)
    assert out2.arbitrated and out2.key == "forgot"


# ---------- R02/R23 runner：隔离与防线 ----------

class _ContaminatedAdapter(MockAdapter):
    """reset 假装清了实际没清——verify_reset 必须逮住（fail fast）。"""

    name = "contaminated"

    def reset(self) -> None:
        pass


def _mini_case(cid: str) -> MemoryCase:
    probe = _storage_probe(f"{cid}-p1", "~/dev")
    return MemoryCase(
        case_id=cid, schema_version="0.1", capability="persist",
        question_type="cross_session_recall", content_type="path", difficulty=1,
        meta={"author": "t", "created": "2026-10-04", "source": "seed"},
        phases=[{"name": "inject", "steps": [{"user": "我的代码目录是 ~/dev"}]},
                {"name": "probe",
                 "steps": [{"user": "你记的我的代码目录是哪个？"}]}],
        probes=[probe])


def test_run_suite_isolates_case_failure(tmp_path):
    """中途崩溃：另一个 case 的产物照常、manifest 记 failed_cases。"""
    adapter = MockAdapter()
    real_send = adapter.send
    state = {"n": 0}

    def crashing_send(session_id, message):
        state["n"] += 1
        if state["n"] > 2:  # c-001 两条消息之后即 c-002
            raise RuntimeError("意外崩溃")
        return real_send(session_id, message)

    adapter.send = crashing_send
    cases = [_mini_case("c-001"), _mini_case("c-002")]
    run_id, stores = run_suite(adapter, cases, tmp_path, "mock")
    manifest = json.loads((tmp_path / run_id / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["failed_cases"] == ["c-002"]
    assert len(stores) == 1 and stores[0].items()[0].case_id == "c-001"
    assert [c.case_id for c, _ in pair_stores(cases, stores)] == ["c-001"]


def test_verify_reset_fails_fast_on_leftover(tmp_path):
    adapter = _ContaminatedAdapter()
    cases = [_mini_case("c-001"), _mini_case("c-002")]
    run_id, stores = run_suite(adapter, cases, tmp_path, "contaminated")
    manifest = json.loads((tmp_path / run_id / "manifest.json")
                          .read_text(encoding="utf-8"))
    # c-001 正常跑完留下记忆；c-002 reset 未清 → verify_reset 中止该 case
    assert manifest.get("failed_cases") == ["c-002"]
    assert len(stores) == 1


# ---------- R11/R12 aggregate bootstrap CI + 每轮分母 ----------

def _mk_agg_run(tmp_path: Path, run_id: str, case: MemoryCase,
                verdict_bits: list[int]) -> Path:
    """造一个带 verdicts.jsonl + case 快照的最小 run 目录。"""
    import yaml
    d = tmp_path / run_id
    cdir = d / "cases" / case.case_id
    cdir.mkdir(parents=True)
    (cdir / "case.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json"), allow_unicode=True),
        encoding="utf-8")
    rows = []
    for i, bit in enumerate(verdict_bits, 1):
        rows.append({"verdict_id": f"v-{i:04d}", "probe_id": f"persist-x-p{i}",
                     "case_id": case.case_id, "run_id": run_id,
                     "verdict": "correct" if bit else "confusion",
                     "decided_by": "rule", "confidence": 1.0, "explanation": ""})
    (d / "verdicts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    n = len(verdict_bits)
    (d / "manifest.json").write_text(json.dumps(
        {"adapter": "mock", "cases": [case.case_id], "run_id": run_id}), encoding="utf-8")
    score = sum(verdict_bits) / n
    (d / "metrics.json").write_text(json.dumps(
        {"overall_score": score, "capability_scores": {"persist": score},
         "n_valid": n, "n_invalid_run": 0, "n_human_review": 0}), encoding="utf-8")
    return d


def _judge_mini_case(cid: str) -> MemoryCase:
    probe = JudgeProbe(kind="judge", id=f"{cid}-p1", after="probe",
                       ask="你记的我的代码目录是哪个？", expect="~/dev",
                       rubric="答 ~/dev=对", verdict_map={"reported": "correct",
                                                          "forgot": "omission"})
    return MemoryCase(
        case_id=cid, schema_version="0.1", capability="persist",
        question_type="cross_session_recall", content_type="path", difficulty=1,
        meta={"author": "t", "created": "2026-10-04", "source": "seed"},
        phases=[{"name": "probe",
                 "steps": [{"user": "你记的我的代码目录是哪个？"}]}],
        probes=[probe])


def test_aggregate_bootstrap_ci(tmp_path):
    from memhall.report.aggregate import aggregate_runs, format_table
    case = _judge_mini_case("persist-x")
    bits = [1, 1, 1, 0, 0, 1, 0, 1, 1, 1]
    a = _mk_agg_run(tmp_path, "20261004-000001-mock", case, bits)
    b = _mk_agg_run(tmp_path, "20261004-000002-mock", case, bits)
    r = aggregate_runs([a, b], tmp_path / "agg")
    persist = r["capabilities"]["persist"]
    assert persist["ci95"] is not None
    lo, hi = persist["ci95"]
    assert lo <= persist["mean"] <= hi
    assert persist["n_score_probes"] == 10
    assert r["overall"]["ci95"] is not None
    assert r["runs"][0]["n_valid"] == 10  # 每轮分母进 aggregate
    assert "95% CI" in format_table(r)
    # 固定 seed：两次聚合结果逐字一致（可复现）
    r2 = aggregate_runs([a, b], tmp_path / "agg2")
    assert r2["capabilities"]["persist"]["ci95"] == persist["ci95"]


# ---------- R11b/R16 compare 符号检验 + 判定一致率 ----------

def test_compare_sign_test_and_agreement(tmp_path):
    from memhall.report.compare import compare_runs
    case = _judge_mini_case("persist-x")
    bits_a = [1, 1, 1]
    bits_b = [0, 1, 1]
    a = _mk_agg_run(tmp_path, "20261004-000003-mock", case, bits_a)
    b = _mk_agg_run(tmp_path, "20261004-000004-mock", case, bits_b)
    out = compare_runs(a, b, tmp_path / "cmp")
    assert out["verdict_agreement_rate"] == round(2 / 3, 4)
    assert out["flip_sign_test"]["n_flips"] == 1
    assert out["flip_sign_test"]["to_a"] == 1   # 翻转方向：A 对 → B 错
    assert out["flip_sign_test"]["to_b"] == 0
    assert out["flip_sign_test"]["p_two_sided"] == 1.0  # 1 翻转不可能显著
    text = (tmp_path / "cmp" / "compare.md").read_text(encoding="utf-8")
    assert "判定一致率" in text and "符号检验" in text


def test_hermes_reset_cleans_lock_files():
    """R02 真机首战（2026-10-05 全废轮教训）：hermes 记忆目录会残留 *.lock
    空壳锁文件（0 字节、写完不回收）——reset 必须一并清掉；verify_reset
    整目录空的最严标准不动，继续当绊网。"""
    from memhall.adapters.hermes import HermesAdapter

    class _Ch:
        def __init__(self):
            self.calls = []

        def run(self, cmd, timeout=30, stdin_data=None):
            self.calls.append(cmd)
            return 0, "", ""

    ch = _Ch()
    HermesAdapter(channel=ch).reset()
    assert any("*.lock" in c and "MEMORY.md" in c for c in ch.calls), ch.calls
