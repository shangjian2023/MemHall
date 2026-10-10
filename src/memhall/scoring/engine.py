"""评分引擎：case 探测点 × 证据 → 契约 03 Verdict 列表。

RuleProbe -> 断言链（scoring/rules.py，确定性）
JudgeProbe -> 判卷器（scripted 离线 / 双 LLM judge 在线，scoring/judge.py）
"""

from __future__ import annotations

from typing import Optional

from memhall.schema.evidence import (
    DecidedBy,
    EvidenceType,
    JudgeMeta,
    JudgeMetaItem,
    Verdict,
    VerdictValue,
)
from memhall.schema.models_case import JudgeProbe, MemoryCase, RuleProbe
from memhall.scoring.judge import (
    JudgeOutcome,
    OpenAICompatJudge,
    ScriptedJudge,
    dual_judge,
)
from memhall.scoring.rules import EvidenceStore, run_check

_scripted = ScriptedJudge()


def _answer_for(store: EvidenceStore, ask: str) -> str:
    """取 probe 段中对该问题的回复（按 message 匹配，取最后一条）。"""
    best = ""
    for ev in store.by_type(EvidenceType.DIALOGUE):
        msgs = ev.payload.get("messages", [])
        replies = ev.payload.get("replies", [])
        for msg, rep in zip(msgs, replies):
            if _norm_pair(msg, ask):
                best = rep.get("text", "")
    return best


def _norm_pair(message: str, ask: str) -> bool:
    a, b = message.strip(), ask.strip()
    return a == b or b in a or a in b


def _raw_verdict(key: str | None, probe: JudgeProbe) -> VerdictValue:
    """judge 原始输出（verdict_map 的 key）→ 五态；非法/缺失 → invalid_run。"""
    if key is None:
        return VerdictValue.INVALID_RUN
    return VerdictValue(probe.verdict_map.get(key, "invalid_run"))


def _judge_verdict(probe: JudgeProbe, store: EvidenceStore, run_id: str, seq: int,
                   judges: Optional[tuple[OpenAICompatJudge, ...]]) -> Verdict:
    answer = _answer_for(store, probe.ask)
    if "[RUNTIME_ERROR]" in answer:
        return Verdict(
            verdict_id=f"v-{seq:04d}", probe_id=probe.id,
            case_id=probe.id.rsplit("-", 1)[0], run_id=run_id,
            verdict=VerdictValue.INVALID_RUN, confidence=0.0,
            decided_by=DecidedBy.RULE, evidence_refs=["transcript:answer"],
            explanation="运行无效：被测智能体后端不可用，不计入分母",
        )
    outcome: JudgeOutcome
    decided_by: DecidedBy

    if judges is not None:
        try:
            outcome = dual_judge(probe, answer, *judges)
            decided_by = DecidedBy.ARBITRATION if outcome.arbitrated else DecidedBy.JUDGE_A
            meta = _judge_meta(outcome, probe)
        except RuntimeError as e:
            # judge 端点彻底不可用：降级脚本判卷，评测不因 judge 挂而报废
            outcome = _scripted.judge(probe, answer)
            decided_by = DecidedBy.SCRIPTED
            meta = None
            reason = f"[judge 降级] {e}: {outcome.reason}"
            outcome = JudgeOutcome(outcome.key, 0.0, outcome.evidence_refs, reason)
    else:
        outcome = _scripted.judge(probe, answer)
        decided_by = DecidedBy.SCRIPTED
        meta = None

    if outcome.key is None:
        value, confidence = VerdictValue.NEEDS_REVIEW, 0.0
        decided_by = DecidedBy.HUMAN_REVIEW
    else:
        value = VerdictValue(probe.verdict_map.get(outcome.key, "invalid_run"))
        confidence = outcome.confidence

    return Verdict(
        verdict_id=f"v-{seq:04d}",
        probe_id=probe.id,
        case_id=probe.id.rsplit("-", 1)[0],
        run_id=run_id,
        verdict=value,
        confidence=confidence,
        decided_by=decided_by,
        evidence_refs=outcome.evidence_refs,
        explanation=outcome.reason,
        judge_meta=meta,
    )


def _judge_meta(outcome: JudgeOutcome, probe: JudgeProbe) -> JudgeMeta:
    """只写入实际存在的评委，避免单评委伪造一个空的 B 评委。"""

    agreed = outcome.judge_a_raw is not None and outcome.judge_a_raw == outcome.judge_b_raw
    item_a = None
    item_b = None
    if outcome.judge_a is not None:
        item_a = JudgeMetaItem(
            model=outcome.judge_a,
            verdict=_raw_verdict(outcome.judge_a_raw, probe),
            agreed=agreed,
        )
    if outcome.judge_b is not None:
        item_b = JudgeMetaItem(
            model=outcome.judge_b,
            verdict=_raw_verdict(outcome.judge_b_raw, probe),
            agreed=agreed,
        )
    return JudgeMeta(
        judge_a=item_a,
        judge_b=item_b,
        prompt_version="judge-prompt-v1",
        arbiter="judge_a-arbiter" if outcome.arbitrated else None,
    )


def _rule_verdict(probe: RuleProbe, store: EvidenceStore, run_id: str, seq: int) -> Verdict:
    check = [b.model_dump(by_alias=True) for b in probe.check]
    value, idx = run_check(check, store)
    branch = probe.check[idx]
    return Verdict(
        verdict_id=f"v-{seq:04d}",
        probe_id=probe.id,
        case_id=probe.id.rsplit("-", 1)[0],
        run_id=run_id,
        verdict=VerdictValue(value),
        confidence=1.0,
        decided_by=DecidedBy.RULE,
        evidence_refs=probe.evidence_ref or ["evidence.jsonl"],
        explanation=f"断言命中: {branch.assert_name}（第 {idx + 1} 分支）",
    )


def evaluate_case(case: MemoryCase, store: EvidenceStore, run_id: str,
                  judges: Optional[tuple[OpenAICompatJudge, ...]] = None
                  ) -> list[Verdict]:
    out: list[Verdict] = []
    for i, probe in enumerate(case.probes, start=1):
        if isinstance(probe, RuleProbe):
            out.append(_rule_verdict(probe, store, run_id, i))
        else:
            out.append(_judge_verdict(probe, store, run_id, i, judges))
    return out
