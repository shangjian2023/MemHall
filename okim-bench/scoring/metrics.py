"""指标聚合：六维能力分 + 五类错误矩阵 + 稳定性 + 评委一致性。

对应设计文档 §4.3 与赛题"指标完整性 10%"。
输入为 results.jsonl（每行一个 ScoreResult.to_dict()）。
"""
from __future__ import annotations

import json
from collections import defaultdict

from .models import Capability, ErrorType

ERROR_TYPES = [e.value for e in ErrorType]
CAPABILITIES = [c.value for c in Capability]


def aggregate(results: list[dict], kappa: float | None = None) -> dict:
    """results: ScoreResult.to_dict() 列表。kappa: 双评委一致性（若有 judge 评分）。"""
    by_cap: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cap[r.get("capability", "unknown")].append(r)

    capability_scores: dict[str, float] = {}
    error_matrix: dict[str, dict[str, int]] = {}
    for cap in CAPABILITIES:
        rows = by_cap.get(cap, [])
        if rows:
            capability_scores[cap] = round(
                sum(1 for r in rows if r["error_type"] == ErrorType.CORRECT.value) / len(rows), 4)
        else:
            capability_scores[cap] = 0.0
        error_matrix[cap] = {et: sum(1 for r in rows if r["error_type"] == et) for et in ERROR_TYPES}

    # 五类错误总体构成（占全部 probe 比例）
    total = len(results)
    error_composition = {
        et: round(sum(1 for r in results if r["error_type"] == et) / total, 4) if total else 0.0
        for et in ERROR_TYPES
    }

    scored_by = {k: sum(1 for r in results if r.get("scored_by") == k)
                 for k in ("L1", "L2", "JUDGE", "ARBITER")}
    needs_review = sum(1 for r in results if r.get("needs_review"))

    out = {
        "n_probes": total,
        "capability_scores": capability_scores,   # 雷达图六轴
        "overall": round(sum(1 for r in results if r["error_type"] == ErrorType.CORRECT.value) / total, 4) if total else 0.0,
        "error_matrix": error_matrix,             # 能力 × 错误类型
        "error_composition": error_composition,   # 五类错误构成
        "scored_by": scored_by,                   # 规则/AI 评分占比（低人工介入证据）
        "needs_review": needs_review,
        "judge_kappa": kappa,
    }
    return out


def aggregate_from_files(result_paths: list[str], kappa: float | None = None,
                         out_path: str | None = None) -> dict:
    results = []
    for p in result_paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
    metrics = aggregate(results, kappa)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
    return metrics
