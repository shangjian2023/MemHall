"""History-independent stratified sampling and self-contained, sealed assessment papers."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memhall.datasets import (
    CasePack,
    case_fingerprint,
    family_of,
    fingerprint,
    validate_cases,
)
from memhall.schema.models_case import MemoryCase


class FrozenPaper(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paper_schema: Literal["1.0"] = "1.0"
    paper_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    suite: dict
    sampling: dict
    cases: list[MemoryCase] = Field(min_length=1)

    def content(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True, exclude={"paper_id"})

    def verify(self) -> None:
        validate_cases(self.cases)
        if fingerprint(self.content()) != self.paper_id:
            raise ValueError("题单封印不匹配：内容、题目或评分配置被修改")
        if self.sampling.get("selected_count") != len(self.cases):
            raise ValueError("题单计数不匹配")
        required = {"suite_id", "suite_version", "suite_fingerprint", "scoring_protocol"}
        if not required <= self.suite.keys():
            raise ValueError("题单缺少题包身份或评分协议")
        if self.sampling.get("selection_fingerprint") != case_fingerprint(self.cases):
            raise ValueError("题单内容指纹不匹配")
        if self.sampling.get("n_families") != len({family_of(c) for c in self.cases}):
            raise ValueError("题单题族计数不匹配")
        if self.sampling.get("capability_counts") != dict(Counter(c.capability.value for c in self.cases)):
            raise ValueError("题单能力分布不匹配")
        if self.suite["scoring_protocol"] != "memhall.behavior-v2":
            raise ValueError("当前运行器不支持该评分协议")

    def save(self, path: Path) -> None:
        self.verify()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(self.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2)
                + "\n"
            )


def load_paper(path: Path) -> FrozenPaper:
    paper = FrozenPaper.model_validate_json(path.read_text(encoding="utf-8"))
    paper.verify()
    return paper


def _rank(seed: int, key: str) -> str:
    return fingerprint([seed, key])


def make_paper(
    pack: CasePack,
    *,
    count: int | None = None,
    seed: int = 0,
    split: str | None = None,
    allow_partial: bool = False,
    max_per_family: int | None = None,
) -> FrozenPaper:
    validate_cases(pack.cases)
    pool = [c for c in pack.cases if split is None or (c.split or pack.spec.split) == split]
    if not pool:
        raise ValueError("题池为空或指定 split 没有题目")
    if max_per_family is not None and max_per_family < 1:
        raise ValueError("max_per_family 必须为正数")
    n = len(pool) if count is None else count
    if n < 1 or n > len(pool):
        raise ValueError(f"抽题数量须在 1 到 {len(pool)} 之间")
    buckets: dict[str, list[MemoryCase]] = {}
    for c in pool:
        buckets.setdefault(c.capability.value, []).append(c)
    if not allow_partial and n < len(buckets):
        raise ValueError("抽题数小于能力维数；明确 allow_partial 才能生成不完整覆盖题单")
    selected: list[MemoryCase] = []
    if count is None and max_per_family is None:
        selected = sorted(pool, key=lambda c: c.case_id)
    else:
        capability_counts: Counter[str] = Counter()
        family_counts: Counter[str] = Counter()
        content_counts: Counter[tuple[str, str]] = Counter()
        difficulty_counts: Counter[tuple[str, int]] = Counter()
        remaining = list(pool)
        while len(selected) < n:
            candidates = [
                c for c in remaining if max_per_family is None or family_counts[family_of(c)] < max_per_family
            ]
            if not candidates:
                raise ValueError("题族数量不足：无法满足 max_per_family 与抽题数量")
            c = min(
                candidates,
                key=lambda c: (
                    capability_counts[c.capability.value],
                    family_counts[family_of(c)],
                    content_counts[(c.capability.value, c.content_type.value)],
                    difficulty_counts[(c.capability.value, c.difficulty)],
                    _rank(seed, c.case_id),
                    c.case_id,
                ),
            )
            selected.append(c)
            remaining.remove(c)
            capability_counts[c.capability.value] += 1
            family_counts[family_of(c)] += 1
            content_counts[(c.capability.value, c.content_type.value)] += 1
            difficulty_counts[(c.capability.value, c.difficulty)] += 1
    sampling = {
        "algorithm": "full-v1" if count is None else "stratified-family-v1",
        "seed": seed,
        "pool_count": len(pool),
        "selected_count": len(selected),
        "split": split,
        "allow_partial": allow_partial,
        "max_per_family": max_per_family,
        "capability_counts": dict(sorted(Counter(c.capability.value for c in selected).items())),
        "content_counts": dict(sorted(Counter(c.content_type.value for c in selected).items())),
        "difficulty_counts": {str(k): v for k, v in sorted(Counter(c.difficulty for c in selected).items())},
        "n_families": len({family_of(c) for c in selected}),
        "selection_fingerprint": case_fingerprint(selected),
        "history_independent": True,
    }
    raw = {
        "paper_schema": "1.0",
        "suite": pack.identity(),
        "sampling": sampling,
        "cases": [c.model_dump(mode="json", exclude_none=True) for c in selected],
    }
    paper = FrozenPaper.model_validate({"paper_id": fingerprint(raw), **raw})
    paper.verify()
    return paper


def run_identity(
    cases: list[MemoryCase],
    *,
    paper: FrozenPaper | None = None,
    suite: dict | None = None,
    exploratory: bool = False,
) -> dict:
    validate_cases(cases)
    if paper is not None:
        paper.verify()
        if [c.model_dump(mode="json", exclude_none=True) for c in cases] != [
            c.model_dump(mode="json", exclude_none=True) for c in paper.cases
        ]:
            raise ValueError("运行题目与封存题单不一致")
        suite = paper.suite
    return {
        "identity_version": "1.0",
        "suite": suite
        or {
            "suite_id": "ad-hoc",
            "suite_version": "unversioned",
            "suite_fingerprint": case_fingerprint(cases),
            "scoring_protocol": "memhall.behavior-v2",
        },
        "selection_fingerprint": case_fingerprint(cases),
        "paper_id": paper.paper_id if paper else None,
        "n_cases": len(cases),
        "n_families": len({family_of(c) for c in cases}),
        "family_fallback": "case_id",
        "exploratory": exploratory,
    }
