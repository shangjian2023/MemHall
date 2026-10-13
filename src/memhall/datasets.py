"""Versioned data packs. Loading a pack never imports or executes its code."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from memhall.paths import QUICK_IDS, resolve_case_dir
from memhall.schema.models_case import MemoryCase, probe_role

SCORING_PROTOCOL: Literal["memhall.behavior-v2"] = "memhall.behavior-v2"


def fingerprint(payload: object) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def case_payload(case: MemoryCase) -> dict:
    return case.model_dump(mode="json", exclude_none=True)


def case_fingerprint(cases: list[MemoryCase]) -> str:
    return fingerprint([case_payload(c) for c in sorted(cases, key=lambda c: c.case_id)])


def family_of(case: MemoryCase) -> str:
    return case.family_id or case.case_id


class SuiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    suite_version: str = Field(min_length=1, max_length=64)
    manifest_version: Literal["1.0"] = "1.0"
    description: str = ""
    license: str = ""
    split: Literal["development", "test", "heldout", "stress"] = "test"
    scoring_protocol: Literal["memhall.behavior-v2"] = SCORING_PROTOCOL
    case_globs: list[str] = Field(default_factory=lambda: ["**/*.yaml"], min_length=1)


@dataclass(frozen=True)
class CasePack:
    spec: SuiteSpec
    cases: list[MemoryCase]
    source: str

    def identity(self) -> dict:
        return {
            "suite_id": self.spec.suite_id,
            "suite_version": self.spec.suite_version,
            "suite_fingerprint": fingerprint(
                {"spec": self.spec.model_dump(mode="json"), "cases": case_fingerprint(self.cases)}
            ),
            "scoring_protocol": self.spec.scoring_protocol,
        }

    def inventory(self) -> dict:
        cs = self.cases
        return {
            **self.identity(),
            "n_cases": len(cs),
            "n_probes": sum(len(c.probes) for c in cs),
            "n_score_probes": sum(probe_role(c, p) == "score" for c in cs for p in c.probes),
            "n_families": len({family_of(c) for c in cs}),
            "n_cases_with_declared_family": sum(c.family_id is not None for c in cs),
            "family_fallback": "case_id when family_id is absent; independence is not proven",
            "capabilities": dict(sorted(Counter(c.capability.value for c in cs).items())),
            "contents": dict(sorted(Counter(c.content_type.value for c in cs).items())),
            "difficulties": dict(sorted(Counter(c.difficulty for c in cs).items())),
            "splits": dict(sorted(Counter(c.split or self.spec.split for c in cs).items())),
        }


def validate_cases(cases: list[MemoryCase]) -> None:
    seen: set[str] = set()
    probes: set[str] = set()
    for c in cases:
        if c.case_id in seen:
            raise ValueError(f"重复 case_id: {c.case_id}")
        seen.add(c.case_id)
        if not c.phases or not c.probes:
            raise ValueError(f"用例缺少 phases/probes: {c.case_id}")
        for p in c.probes:
            if p.id in probes:
                raise ValueError(f"重复 probe_id: {p.id}")
            probes.add(p.id)


def load_directory(directory: Path) -> CasePack:
    directory = directory.resolve()
    manifest = directory / "suite.yaml"
    if manifest.is_file():
        spec = SuiteSpec.model_validate(yaml.safe_load(manifest.read_text(encoding="utf-8")))
    else:
        spec = SuiteSpec(suite_id="custom", suite_version="unversioned")
    paths: set[Path] = set()
    for pattern in spec.case_globs:
        if ".." in Path(pattern).parts or Path(pattern).is_absolute() or ":" in pattern:
            raise ValueError(f"case_globs 必须在题包内: {pattern}")
        for p in directory.glob(pattern):
            if not p.is_file() or p.name == "suite.yaml" or p.suffix != ".yaml":
                continue
            resolved = p.resolve()
            if not resolved.is_relative_to(directory):
                raise ValueError(f"题包引用了目录外文件: {p}")
            paths.add(resolved)
    cases = [MemoryCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))) for p in sorted(paths)]
    validate_cases(cases)
    return CasePack(spec, cases, str(directory))


def load_pack(spec: str) -> CasePack:
    if spec.rstrip("/") in ("quick", "cases/quick"):
        directory = resolve_case_dir("cases/full")
        if directory is None:
            raise ValueError("未找到 full 题库")
        full = load_directory(directory)
        by_id = {c.case_id: c for c in full.cases}
        missing = set(QUICK_IDS) - set(by_id)
        if missing:
            raise ValueError(f"quick 引用缺失: {sorted(missing)}")
        quick = full.spec.model_copy(update={"suite_id": "memhall-smoke-v1"})
        return CasePack(quick, [by_id[i] for i in QUICK_IDS], "cases/quick")
    directory = resolve_case_dir(spec)
    if directory is None:
        raise ValueError(f"未找到题库: {spec}")
    return load_directory(directory)
