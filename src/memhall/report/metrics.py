"""指标计算：Verdict 列表 → 六维能力分 + 错误分解 + 故障定位 + 判卷方式统计。

口径 v2（2026-10-04，docs/review-tasks.md R03/R07/R08/R10/R12/R18）：
- 探测点分 role：score 进六维，diagnostic 只进故障定位/质检层——
  "没写库"一个故障不在 persist/update/reuse 三处重复扣分；
- 无有效探测的能力维输出 None（未测≠0 分），雷达/报告跳轴；
- invalid_run 与 human_review 分开计数；overall 附保守下界
  （未决按错计）与按用例等权两种口径；
- 故障定位四态（design §6.3）与过期信息调用率（§8）在此聚合。
"""

from __future__ import annotations

from collections import Counter, defaultdict

from memhall.schema.evidence import DecidedBy, Verdict, VerdictValue
from memhall.schema.models_case import (
    Capability,
    MemoryCase,
    Probe,
    probe_role,
)

CAP_ORDER = [
    Capability.PERSIST, Capability.RECALL, Capability.DYNAMIC_UPDATE,
    Capability.DISCRIMINATE, Capability.BOUNDARY, Capability.REUSE,
]
CAP_LABELS_ZH: dict[str, str] = {
    Capability.PERSIST: "长期保持",
    Capability.RECALL: "记忆调用",
    Capability.DYNAMIC_UPDATE: "动态更新",
    Capability.DISCRIMINATE: "相近区分",
    Capability.BOUNDARY: "边界识别",
    Capability.REUSE: "任务复用",
}

_EXCLUDED = (VerdictValue.INVALID_RUN, VerdictValue.HUMAN_REVIEW)


def _countable(v: Verdict) -> bool:
    """计分有效：非 invalid_run/human_review，且非降级脚本猜测（P0-2，
    队友复核 10-05 方案 b）——judge 故障窗口里脚本的判定值保留在 verdict
    供人工复核，但不进分子分母，避免"分母随网关故障漂移"（R03 复发）。"""
    return v.verdict not in _EXCLUDED and not v.degraded
_CONTENT_ERR = (VerdictValue.CONFUSION, VerdictValue.FABRICATION,
                VerdictValue.WRONG_REUSE)

# 故障定位四态（design §6.3）+ 两个辅助态
FAULT_STATES = {
    "not_stored": "没存（写入故障）",
    "stored_unused": "存了没用上（调用故障）",
    "stored_wrong": "存了但内容错（写入质量/检索串台）",
    "over_persisted": "该删没删（边界失效）",
    "ok": "记住了（存取一致）",
    "mixed": "混合（多探测点结论不一致）",
}


def _probe_index(cases: dict[str, MemoryCase]) -> dict[str, tuple[MemoryCase, Probe]]:
    """probe_id -> (case, probe)；快照旧数据缺 role 字段时同样可推断。"""
    idx: dict[str, tuple[MemoryCase, Probe]] = {}
    for case in cases.values():
        for p in case.probes:
            idx[p.id] = (case, p)
    return idx


def _is_storage_probe(case: MemoryCase, probe) -> bool:
    """存储态断言（memory.*）：故障定位里"存没存"的证据源。"""
    if probe.kind != "rule":
        return False
    return probe_role(case, probe) == "diagnostic" and any(
        b.assert_name.startswith("memory.") for b in probe.check)


def compute_metrics(verdicts: list[Verdict], cases: dict[str, MemoryCase]) -> dict:
    """六维雷达 + 错误分解 + 故障定位。invalid_run/human_review 不计入分母（单列）。"""
    idx = _probe_index(cases)

    by_cap: dict[Capability, list[Verdict]] = defaultdict(list)
    by_case_score: dict[str, list[Verdict]] = defaultdict(list)
    by_case_storage: dict[str, list[Verdict]] = defaultdict(list)
    n_diag = 0
    for v in verdicts:
        entry = idx.get(v.probe_id)
        case = cases.get(v.case_id)
        if entry is not None:
            case, probe = entry
        elif case is not None:
            probe = None  # 历史数据 probe_id 对不上：按 score 处理（保守）
        else:
            continue
        role = probe_role(case, probe) if probe is not None else "score"
        if role == "diagnostic":
            n_diag += 1
            if _is_storage_probe(case, probe):
                by_case_storage[v.case_id].append(v)
            continue
        by_cap[case.capability].append(v)
        by_case_score[v.case_id].append(v)

    capability_scores: dict[str, float | None] = {}
    detail: dict[str, dict] = {}
    for cap in CAP_ORDER:
        vs = by_cap.get(cap, [])
        valid = [v for v in vs if _countable(v)]
        correct = sum(1 for v in valid if v.verdict == VerdictValue.CORRECT)
        # 无探测或全被剔除 = 该维未测（None），不是 0 分
        score = round(correct / len(valid), 4) if valid else None
        capability_scores[cap.value] = score
        errors = Counter(v.verdict.value for v in valid if v.verdict != VerdictValue.CORRECT)
        detail[cap.value] = {
            "label_zh": CAP_LABELS_ZH[cap],
            "n_probes": len(vs),
            "n_valid": len(valid),
            "n_correct": correct,
            "score": score,
            "error_breakdown": dict(errors),
            "n_invalid_run": sum(1 for v in vs if v.verdict == VerdictValue.INVALID_RUN),
            "n_human_review": sum(1 for v in vs if v.verdict == VerdictValue.HUMAN_REVIEW),
        }

    score_all = [v for vs in by_case_score.values() for v in vs]
    valid_all = [v for v in score_all if _countable(v)]
    correct_all = sum(1 for v in valid_all if v.verdict == VerdictValue.CORRECT)
    n_human = sum(1 for v in score_all if v.verdict == VerdictValue.HUMAN_REVIEW)
    # 降级猜测：verdict 值可计分形态但 degraded（脚本猜了个 key）；判 human_review
    # 的降级未决已在 n_human 里，不重复计
    n_degraded = sum(1 for v in score_all
                     if v.degraded and v.verdict not in _EXCLUDED)

    # 按用例等权（R10）：先每 case 聚合通过率，再对 case 平均——
    # 否则探测点多的族（reuse 23 个）在总分里权重是少族（discriminate 10）的 2.3 倍
    case_rates = []
    for vs in by_case_score.values():
        cv = [v for v in vs if _countable(v)]
        if cv:
            case_rates.append(sum(1 for v in cv if v.verdict == VerdictValue.CORRECT) / len(cv))

    decided = Counter(v.decided_by.value for v in verdicts)
    # 写入卫生（design.md §8 边界维专属）：不该存的内容进记忆库的比例
    boundary = detail.get(Capability.BOUNDARY.value, {})
    bn = boundary.get("n_valid", 0)
    write_hygiene = (round(boundary.get("error_breakdown", {})
                           .get("over_persist", 0) / bn, 4)) if bn else None
    # 过期信息调用率（design.md §8 更新维专属）：拿旧值答新题（confusion+wrong_reuse）
    upd = detail.get(Capability.DYNAMIC_UPDATE.value, {})
    un = upd.get("n_valid", 0)
    stale = (round(sum(upd.get("error_breakdown", {}).get(k, 0)
                       for k in ("confusion", "wrong_reuse")) / un, 4)
             if un else None)
    return {
        "n_probes_total": len(verdicts),
        "n_score_probes": len(score_all),
        "n_diagnostic_probes": n_diag,
        "n_valid": len(valid_all),
        "n_invalid_run": sum(1 for v in score_all if v.verdict == VerdictValue.INVALID_RUN),
        "n_human_review": n_human,
        "n_degraded": n_degraded,
        "overall_score": round(correct_all / len(valid_all), 4) if valid_all else None,
        # 保守下界（R03）：判卷未决与降级猜测按错计——未决/降级剔除率高的
        # run，分数区间必须可见
        "overall_score_floor": (round(correct_all / (len(valid_all) + n_human + n_degraded), 4)
                                if valid_all and (n_human or n_degraded) else None),
        "overall_score_case_weighted": (round(sum(case_rates) / len(case_rates), 4)
                                        if case_rates else None),
        "capability_scores": capability_scores,
        "capability_detail": detail,
        "decided_by": dict(decided),
        "rule_scoring_rate": round(
            decided.get("rule", 0) / len(verdicts), 4) if verdicts else 0.0,
        "write_hygiene": write_hygiene,
        "stale_info_rate": stale,
        "fault_localization": _fault_localization(by_case_score, by_case_storage),
        # 判卷质检（design.md §8 评测质量指标）：仅 dual 判卷时有值
        "judge_agreement_rate": _agreement_rate(verdicts),
        "judge_cohens_kappa": _cohens_kappa(verdicts),
        "human_review_rate": round(
            sum(1 for v in verdicts
                if v.decided_by == DecidedBy.HUMAN_REVIEW) / len(verdicts), 4
        ) if verdicts else 0.0,
    }


def _fault_localization(by_case_score: dict[str, list[Verdict]],
                        by_case_storage: dict[str, list[Verdict]]) -> dict:
    """行为×存储交叉 → 四态定位（design §6.3）。

    优先级：该删没删 > 存了但内容错 > 存了没用上 > 没存 > 记住了；
    只有行为无存储证据的 case 不进四态（定位不了，单列）。
    """
    summary: Counter[str] = Counter()
    per_case: dict[str, str] = {}
    for cid, behs in by_case_score.items():
        stores = by_case_storage.get(cid, [])
        any_over = any(v.verdict == VerdictValue.OVER_PERSIST for v in behs + stores)
        beh_valid = [v for v in behs if v.verdict not in _EXCLUDED]
        beh_bad = [v for v in beh_valid if v.verdict != VerdictValue.CORRECT]
        stored_ok = any(v.verdict == VerdictValue.CORRECT for v in stores) if stores else None

        if any_over:
            state = "over_persisted"
        elif not beh_valid:
            continue  # 整 case 无效：不定位
        elif not beh_bad:
            state = "ok"
        elif stored_ok is None:
            continue  # 无存储证据：进 no_storage 桶，不硬猜
        elif stored_ok and any(v.verdict in _CONTENT_ERR for v in beh_bad):
            state = "stored_wrong"
        elif stored_ok and any(v.verdict == VerdictValue.OMISSION for v in beh_bad):
            state = "stored_unused"
        elif stored_ok:
            state = "mixed"
        else:
            state = "not_stored"
        summary[state] += 1
        per_case[cid] = state

    n_score_cases = len(by_case_score)
    return {
        "summary": dict(summary),
        "summary_zh": {FAULT_STATES[k]: n for k, n in summary.items()},
        "per_case": per_case,
        "n_cases_without_storage_evidence": n_score_cases - len(per_case),
    }


def _agreement_rate(verdicts: list[Verdict]) -> float | None:
    """双判一致率：双评委原始票相同的比例（无 judge_meta/无双判时 None）。"""
    dual = [v.judge_meta for v in verdicts if v.judge_meta is not None]
    bs = [m.judge_b for m in dual if m.judge_b is not None]
    if not bs:
        return None
    agreed = sum(1 for b in bs if b.agreed)
    return round(agreed / len(bs), 4)


def _cohens_kappa(verdicts: list[Verdict]) -> float | None:
    """Cohen's Kappa（P2-6，C 角色队友复核 10-05）：剔除随机一致的评委一致性。

    一致率在类别集中时虚高（双评委都爱投 correct，随机一致就不低）；
    kappa=(po-pe)/(1-pe)，≥0.6 才算实质一致。无效票（raw=None→invalid_run）
    不进配对——双票皆无效不构成"一致"（对齐 R56 的 agreed 语义）。"""
    pairs = [(m.judge_a.verdict, m.judge_b.verdict)
             for v in verdicts if (m := v.judge_meta) is not None
             and m.judge_a is not None and m.judge_b is not None
             and m.judge_a.verdict != VerdictValue.INVALID_RUN
             and m.judge_b.verdict != VerdictValue.INVALID_RUN]
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    cats = {x for pair in pairs for x in pair}
    pe = sum((sum(1 for a, _ in pairs if a == c) / n)
             * (sum(1 for _, b in pairs if b == c) / n) for c in cats)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0
