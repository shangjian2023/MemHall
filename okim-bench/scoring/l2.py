"""L2 语义规则：embedding 相似度判"语义等价"。

embedding 模型可插拔：
- 默认离线回退：字符 bigram Jaccard 相似度（无模型也能跑通全链路，阈值语义不同需分别标定）
- 生产环境：注入真实 embedding 函数（如 openKylin 文本向量化 SDK / bge-m3），
  通过 set_embedder() 注册，阈值在校准集上标定后写入 manifest。
"""
from __future__ import annotations

from typing import Callable, Optional

from .models import ErrorType, ProbeSpec
from .l1 import normalize, contains_pattern

Embedder = Callable[[str], list[float]]

_embedder: Optional[Embedder] = None
_SEMANTIC_TAU = 0.85  # 默认阈值（离线 Jaccard 模式下建议 0.6，见 manifest）


def set_embedder(fn: Optional[Embedder], tau: Optional[float] = None) -> None:
    global _embedder, _SEMANTIC_TAU
    _embedder = fn
    if tau is not None:
        _SEMANTIC_TAU = tau


def _jaccard(a: str, b: str) -> float:
    def grams(s: str) -> set[str]:
        s = normalize(s)
        return {s[i:i + 2] for i in range(len(s) - 1)} or {s}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / max(1, len(ga | gb))


def similarity(a: str, b: str) -> float:
    if _embedder is not None:
        va, vb = _embedder(a), _embedder(b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = sum(x * x for x in va) ** 0.5
        nb = sum(x * x for x in vb) ** 0.5
        return dot / max(1e-9, na * nb)
    return _jaccard(a, b)


def semantic_match(answer: str, reference: str, tau: Optional[float] = None) -> tuple[bool, float]:
    tau = _SEMANTIC_TAU if tau is None else tau
    score = similarity(answer, reference)
    return score >= tau, score


def l2_answer_check(spec: ProbeSpec, answer: str) -> tuple[ErrorType | None, str, float]:
    """L1 未判定时用语义匹配兜底：对 ground_truth 与过期答案分别算相似度。"""
    if not spec.ground_truth:
        return None, "无标准答案可比", 0.0
    ok_gt, s_gt = semantic_match(answer, spec.ground_truth)
    if ok_gt:
        return ErrorType.CORRECT, f"L2 语义命中标准答案 (sim={s_gt:.2f})", s_gt
    best_old, s_old = 0.0, ""
    for old in spec.outdated_answers:
        ok, s = semantic_match(answer, old)
        if s > best_old:
            best_old, s_old = s, old
    if best_old >= _SEMANTIC_TAU:
        return ErrorType.FALSE_REUSE, f"L2 语义命中过期答案 [{s_old}] (sim={best_old:.2f})", best_old
    return None, f"L2 语义相似度不足 (gt={s_gt:.2f})，升级 judge", s_gt
