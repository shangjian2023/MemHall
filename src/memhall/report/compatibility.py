"""Check question content and scoring conditions before combining runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from memhall.datasets import case_fingerprint, validate_cases
from memhall.sampling import load_paper
from memhall.schema.models_case import MemoryCase


def snapshot_cases(run_dir: Path) -> list[MemoryCase] | None:
    full = run_dir / "cases.json"
    if full.is_file():
        raw = json.loads(full.read_text(encoding="utf-8"))
        cases = [MemoryCase.model_validate(c) for c in raw["cases"]]
    else:
        paths = sorted((run_dir / "cases").glob("*/case.yaml"))
        if not paths:
            return None
        cases = [MemoryCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))) for p in paths]
    validate_cases(cases)
    if full.is_file():
        for case in cases:
            path = run_dir / "cases" / case.case_id / "case.yaml"
            if path.is_file():
                archived = MemoryCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
                if case_fingerprint([archived]) != case_fingerprint([case]):
                    raise ValueError(f"逐题快照与 cases.json 不一致: {case.case_id}")
    return cases


def _identity(run_dir: Path, manifest: dict) -> tuple[dict, list[str]]:
    cases = snapshot_cases(run_dir)
    for name, expected in manifest.get("output_hashes", {}).items():
        if name not in ("verdicts.jsonl", "metrics.json", "report.md", "radar.png"):
            raise ValueError(f"不支持的产物封印名: {name}")
        path = run_dir / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"评分产物哈希不匹配: {run_dir.name}/{name}")
    benchmark = manifest.get("benchmark", {})
    warnings: list[str] = []
    selection = case_fingerprint(cases) if cases is not None else None
    recorded = benchmark.get("selection_fingerprint")
    if recorded and recorded != selection:
        raise ValueError(f"题目快照与 manifest 不一致: {run_dir.name}")
    if cases is None:
        warnings.append("旧归档缺少题目快照，只能核对题目清单")
    elif not recorded:
        warnings.append("旧归档按题目快照核对，未提供题包封印")
    paper_file = run_dir / "paper.json"
    paper_id = benchmark.get("paper_id")
    if paper_id:
        if not paper_file.is_file():
            raise ValueError(f"封存试卷缺失: {run_dir.name}")
        paper = load_paper(paper_file)
        if paper.paper_id != paper_id or case_fingerprint(paper.cases) != selection:
            raise ValueError(f"封存试卷与运行题目不一致: {run_dir.name}")
        if paper.suite != benchmark.get("suite"):
            raise ValueError(f"封存试卷与题包身份不一致: {run_dir.name}")
        expected = manifest.get("paper_sha256")
        if expected and hashlib.sha256(paper_file.read_bytes()).hexdigest() != expected:
            raise ValueError(f"封存试卷文件哈希不匹配: {run_dir.name}")
    protocol = manifest.get("scoring_protocol")
    if protocol is None:
        protocol = {"legacy_judge": manifest.get("judge"), "metric_version": "legacy-unsealed"}
        warnings.append("旧归档未封存评分协议；比较不能证明全部评分条件一致")
    return {
        "selection": selection,
        "cases": sorted(manifest.get("cases", [])),
        "suite": benchmark.get("suite"),
        "paper_id": paper_id,
        "protocol": protocol,
        "exploratory": benchmark.get("exploratory", False),
    }, warnings


def check_compatibility(run_dirs: list[Path], *, aggregate: bool = False) -> dict:
    if not run_dirs:
        raise ValueError("至少提供一个运行目录")
    identities: list[dict] = []
    manifests: list[dict] = []
    warnings: list[str] = []
    for directory in run_dirs:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        identity, notes = _identity(directory, manifest)
        identities.append(identity)
        manifests.append(manifest)
        warnings.extend(notes)
    first = identities[0]
    for identity in identities[1:]:
        for key, label in [
            ("selection", "题目内容"),
            ("cases", "题目清单"),
            ("suite", "题包版本"),
            ("paper_id", "固定试卷"),
            ("protocol", "评分协议与判卷器"),
            ("exploratory", "抽题模式"),
        ]:
            if identity[key] != first[key]:
                raise ValueError(f"口径混杂，拒绝比较或聚合：{label}不一致")
    if aggregate:
        for manifest in manifests[1:]:
            for key in ("adapter", "adapter_version", "model_backend", "model_override"):
                if manifest.get(key) != manifests[0].get(key):
                    raise ValueError(f"口径混杂，拒绝聚合：{key}不一致")
    if first["exploratory"]:
        warnings.append("历史轮换为探索评测，不应与固定试卷正式排名混用")
    return {
        "verified": not warnings,
        "warnings": sorted(set(warnings)),
        "selection_fingerprint": first["selection"],
        "paper_id": first["paper_id"],
    }
