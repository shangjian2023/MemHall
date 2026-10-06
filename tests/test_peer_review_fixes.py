"""队友复核轮（C 角色交付 2026-10-05）三项落地回归。

- P0-2（方案 b，与 R27 兼容）：judge 网关故障降级时，脚本兜底的 key 猜测
  保留判定值与溯源（verdict.degraded），但不再计入分数——metrics 剔除、
  保守下界按错计，脚本猜测静默变正式分数的口子关上
- P2-6：metrics 顶层 judge_cohens_kappa（双评委一致性，剔除随机一致）
- P1-4：拒答正则命中需"expect/锚例的值 token 全缺"才定案——"改述正确 +
  顺带一句拒答话术"的混合答案转 LLM/人工，不再误判 omission
"""

from __future__ import annotations

from datetime import UTC, datetime

from memhall.report.metrics import _cohens_kappa, compute_metrics
from memhall.schema.evidence import (
    DecidedBy,
    Evidence,
    EvidencePhase,
    EvidenceType,
    JudgeMeta,
    JudgeMetaItem,
    Verdict,
    VerdictValue,
)
from memhall.schema.models_case import Anchor, JudgeProbe, MemoryCase
from memhall.scoring.engine import evaluate_case
from memhall.scoring.judge import ScriptedJudge
from memhall.scoring.rules import EvidenceStore


def _utc() -> datetime:
    return datetime.now(UTC)


def _dialogue(pairs: list[tuple[str, str]], phase: str = "probe") -> Evidence:
    return Evidence(
        evidence_id="ev-000001", run_id="r", case_id="c-001",
        phase=EvidencePhase(phase), type=EvidenceType.DIALOGUE,
        collected_at=_utc(),
        payload={"messages": [m for m, _ in pairs],
                 "replies": [{"session_id": "s-01", "text": t,
                              "sent_at": _utc().isoformat(),
                              "reply_at": _utc().isoformat(), "latency_ms": 1}
                             for _, t in pairs]},
        sha256="x" * 64)


def _probe(pid: str = "persist-x-p1", **kw) -> JudgeProbe:
    return JudgeProbe(kind="judge", id=pid, after="probe",
                      ask=kw.get("ask", "我的收件地址是什么？"),
                      expect=kw.get("expect", "杭州市滨江区 addr-887"),
                      rubric=kw.get("rubric", "答出地址为正确"),
                      verdict_map=kw.get("verdict_map", {"right": "correct",
                                                         "forgot": "omission"}),
                      anchors=kw.get("anchors", []))


def _case(probe: JudgeProbe, capability: str = "persist") -> MemoryCase:
    return MemoryCase(
        case_id=probe.id.rsplit("-", 1)[0], schema_version="0.1",
        capability=capability, question_type="cross_session_recall",
        content_type="fact", difficulty=1,
        meta={"author": "t", "created": "2026-10-06", "source": "seed"},
        phases=[], probes=[probe])


class _DeadJudge:
    """complete 必抛 RuntimeError 的假评委（模拟网关故障窗口）。"""

    def __init__(self, name: str = "dead"):
        self.name = name

    def complete(self, prompt: str) -> str:
        raise RuntimeError(f"judge {self.name} 网关不可用（测试注入）")


# ---------- P0-2：降级脚本猜测保留但不计分 ----------

def test_degraded_guess_kept_but_not_scored():
    """降级路径：脚本的 correct 猜测保留在 verdict（人工复核可提速），
    degraded 置位、confidence 0、判定值不进分数。"""
    probe = _probe()
    store = EvidenceStore([_dialogue([(probe.ask, probe.expect)])])
    vs = evaluate_case(_case(probe), store, "r", judges=(_DeadJudge(),))
    assert len(vs) == 1
    v = vs[0]
    assert v.verdict == VerdictValue.CORRECT          # 猜测值保留
    assert v.decided_by == DecidedBy.SCRIPTED          # R27：按实际打标
    assert v.degraded is True                          # P0-2：机器可读降级标记
    assert v.confidence == 0.0
    assert "[judge 降级]" in v.explanation


def test_degraded_undecided_goes_human_review_not_counted_twice():
    """降级且脚本判不了：转 human_review（原有语义），不重复计入 n_degraded。"""
    probe = _probe()
    store = EvidenceStore([_dialogue([(probe.ask, "可能是那边吧")])])
    vs = evaluate_case(_case(probe), store, "r", judges=(_DeadJudge(),))
    v = vs[0]
    assert v.verdict == VerdictValue.HUMAN_REVIEW
    assert v.decided_by == DecidedBy.HUMAN_REVIEW
    assert v.degraded is True
    m = compute_metrics(vs, {"persist-x": _case(probe)})
    assert m["n_human_review"] == 1
    assert m["n_degraded"] == 0                        # 未决已在 n_human_review 里


def _verdict(pid: str, value: VerdictValue, *, degraded: bool = False) -> Verdict:
    return Verdict(verdict_id=f"v-{pid}", probe_id=pid,
                   case_id=pid.rsplit("-", 1)[0], run_id="r",
                   verdict=value, confidence=0.0 if degraded else 1.0,
                   decided_by=DecidedBy.SCRIPTED, degraded=degraded)


def test_metrics_exclude_degraded_and_floor_counts_as_wrong():
    """1 个正常 correct + 1 个降级 correct 猜测：总分只算前者（1.0），
    n_degraded 单列，保守下界把降级猜测按错计（0.5）。"""
    p1, p2 = _probe("persist-x-p1"), _probe("persist-x-p2")
    case = _case(p1)
    case.probes.append(p2)
    vs = [_verdict(p1.id, VerdictValue.CORRECT),
          _verdict(p2.id, VerdictValue.CORRECT, degraded=True)]
    m = compute_metrics(vs, {"persist-x": case})
    assert m["n_score_probes"] == 2
    assert m["n_valid"] == 1
    assert m["n_degraded"] == 1
    assert m["overall_score"] == 1.0
    assert m["overall_score_floor"] == 0.5
    assert m["capability_scores"]["persist"] == 1.0
    assert m["capability_detail"]["persist"]["n_valid"] == 1
    # 降级未参与任何 run（旧数据兼容）：无 degraded 字段语义不受影响
    assert m["judge_cohens_kappa"] is None             # 无双判，kappa 缺席


# ---------- P2-6：Cohen's Kappa ----------

def _dual_meta(a: VerdictValue, b: VerdictValue) -> JudgeMeta:
    return JudgeMeta(
        judge_a=JudgeMetaItem(model="a", verdict=a, agreed=a == b),
        judge_b=JudgeMetaItem(model="b", verdict=b, agreed=a == b),
        prompt_version="test")


def _kappa_v(a: VerdictValue, b: VerdictValue) -> Verdict:
    return _verdict("persist-x-p1", VerdictValue.CORRECT).model_copy(
        update={"judge_meta": _dual_meta(a, b)})


def test_kappa_perfect_agreement():
    vs = [_kappa_v(VerdictValue.CORRECT, VerdictValue.CORRECT) for _ in range(4)]
    assert _cohens_kappa(vs) == 1.0


def test_kappa_partial_agreement():
    # (CC)(CC)(OO)(CO)：一致率 0.75，剔除随机一致后 kappa=0.5——
    # 类别集中时 kappa 显著低于一致率，这正是要它的原因
    vs = [_kappa_v(VerdictValue.CORRECT, VerdictValue.CORRECT),
          _kappa_v(VerdictValue.CORRECT, VerdictValue.CORRECT),
          _kappa_v(VerdictValue.OMISSION, VerdictValue.OMISSION),
          _kappa_v(VerdictValue.CORRECT, VerdictValue.OMISSION)]
    assert _cohens_kappa(vs) == 0.5


def test_kappa_none_without_dual_votes():
    assert _cohens_kappa([]) is None
    # 单判（judge_b 缺席）与无效票（raw=None→invalid_run）不进配对——
    # 双票皆无效不构成"一致"（对齐 R56 的 agreed 语义）
    single = _verdict("persist-x-p1", VerdictValue.CORRECT).model_copy(
        update={"judge_meta": JudgeMeta(
            judge_a=JudgeMetaItem(model="a", verdict=VerdictValue.CORRECT,
                                  agreed=False))})
    assert _cohens_kappa([single]) is None
    both_invalid = _kappa_v(VerdictValue.INVALID_RUN, VerdictValue.INVALID_RUN)
    assert _cohens_kappa([both_invalid]) is None
    # 一票无效一票有效：只按有效配对算
    mixed = [_kappa_v(VerdictValue.INVALID_RUN, VerdictValue.CORRECT),
             _kappa_v(VerdictValue.CORRECT, VerdictValue.CORRECT)]
    assert _cohens_kappa(mixed) == 1.0


# ---------- P1-4：拒答需值缺失 ----------

def test_abstain_mixed_answer_not_refusal():
    """改述正确 + 拒答半句的混合话术：值 token 还在，不是拒答——
    脚本不定案转 LLM/人工（旧口径全文匹配误判 omission）。"""
    sj = ScriptedJudge()
    probe = _probe()
    oc = sj.judge(probe, "没听说过这个地址，不过门牌应该是 addr-887 那边")
    assert oc.key is None
    # 纯拒答（值全缺）：照旧按拒答判
    oc = sj.judge(probe, "没听说过这个地址")
    assert oc.key == "forgot" and oc.confidence == 0.8
    # 字面正确不受影响（expect 子串先行，走不到拒答分支）
    oc = sj.judge(probe, probe.expect)
    assert oc.key == "right"


def test_abstain_anchor_partial_value_blocks_refusal():
    """多值锚例部分命中：回答引了旧地址一半 + 拒答话术——不是纯拒答，
    锚例分支又不足以定案（值集不全），转 LLM/人工。"""
    sj = ScriptedJudge()
    probe = _probe(anchors=[Anchor(reply="旧地址在 ~/old/home 门牌 3-401",
                                   expect_verdict="forgot")])
    oc = sj.judge(probe, "不清楚，好像翻到过 ~/old/home，后面的门牌没看到")
    assert oc.key is None
