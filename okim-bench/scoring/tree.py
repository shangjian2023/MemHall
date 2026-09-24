"""判定决策树：L1 → L2 → 双 judge 的完整评分流水线。

对应设计文档 §4.1：
store-level 判定优先（错误持久化 > 混淆 > 遗漏），再做 behavior-level 判定；
L1 能判绝不用 AI；L1 判不了升级 L2 语义；再判不了且契约允许才走双 judge；
judge 也不一致 → 仲裁；仲裁无效 → needs_review（人工复核闸门，非全量人工）。
"""
from __future__ import annotations

from typing import Optional

from .models import (ErrorType, EvidenceBundle, ProbeSpec, ScoreResult)
from .l1 import check_store, check_answer_against, check_artifacts
from .l2 import l2_answer_check
from .judges import Judge, dual_judge


def score_probe(spec: ProbeSpec, evidence: EvidenceBundle,
                judge_a: Optional[Judge] = None,
                judge_b: Optional[Judge] = None) -> ScoreResult:
    # ---- 第一级：store-level（所有 kind 都做，有快照才判） ----
    store = check_store(spec, evidence)
    if store.error_type is not None:
        return ScoreResult(spec.probe_id, store.error_type, 0.95,
                           store.evidence_refs, store.reason, scored_by="L1")

    # ---- 第二级：behavior-level ----
    if spec.kind == "qa":
        err, why = check_answer_against(spec, evidence.answer)
        if err is ErrorType.CORRECT and not store.store_ok and spec.expect_store_retain:
            # 答对但记忆库没存 → 不算长期记忆能力，判遗漏并注明
            return ScoreResult(spec.probe_id, ErrorType.OMISSION, 0.8,
                               store.evidence_refs, f"回答正确但记忆库无对应条目（{why}），不计入长期记忆",
                               scored_by="L1")
        if err is not None:
            return ScoreResult(spec.probe_id, err, 0.9,
                               store.evidence_refs or ["transcript:answer"],
                               why, scored_by="L1")
        # L1 判不了 → L2 语义
        err2, why2, _sim = l2_answer_check(spec, evidence.answer)
        if err2 is not None:
            return ScoreResult(spec.probe_id, err2, 0.75,
                               ["transcript:answer"], why2, scored_by="L2")
        why = why2

    elif spec.kind == "task":
        art = check_artifacts(spec, evidence.artifacts)
        if art.error_type is not None:
            return ScoreResult(spec.probe_id, art.error_type, 0.95,
                               art.evidence_refs, art.reason, scored_by="L1")
        if art.store_ok and store.store_ok:
            return ScoreResult(spec.probe_id, ErrorType.CORRECT, 0.9,
                               art.evidence_refs or store.evidence_refs,
                               "产物断言与记忆状态均通过", scored_by="L1")
        why = "产物通过但记忆状态待语义确认"
    else:  # memory_store_check
        if store.store_ok:
            return ScoreResult(spec.probe_id, ErrorType.CORRECT, 0.95,
                               store.evidence_refs, store.reason, scored_by="L1")
        why = store.reason

    # ---- 第三级：双 judge（仅当契约要求或无法判定时） ----
    if (judge_a and judge_b) and (spec.judge_required or spec.kind == "qa"):
        dj = dual_judge(spec, evidence, judge_a, judge_b)
        if dj.verdict:
            return ScoreResult(spec.probe_id, ErrorType(dj.verdict), dj.confidence,
                               dj.evidence_refs, dj.reason, scored_by="ARBITER" if dj.arbitrated else "JUDGE",
                               judge_a=dj.judge_a, judge_b=dj.judge_b,
                               judge_a_verdict=dj.va, judge_b_verdict=dj.vb)
        return ScoreResult(spec.probe_id, ErrorType.OMISSION, 0.3, [],
                           "仲裁无效，转人工复核", scored_by="JUDGE", needs_review=True)

    # 无 judge 可用且无法判定 → 标人工复核（闸门机制，非默认路径）
    return ScoreResult(spec.probe_id, ErrorType.OMISSION, 0.3, [],
                       f"规则无法判定（{why}），转人工复核", scored_by="L1", needs_review=True)
