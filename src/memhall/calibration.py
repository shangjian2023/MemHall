"""Blind human calibration sheets. Labels must be supplied by a human reviewer."""

from __future__ import annotations

import csv
import json
import random
from collections import Counter
from pathlib import Path

from memhall.datasets import family_of, fingerprint
from memhall.report.compatibility import snapshot_cases
from memhall.schema.evidence import Evidence, Verdict
from memhall.schema.models_case import JudgeProbe
from memhall.scoring.engine import _answer_for
from memhall.scoring.rules import EvidenceStore

LABELS = ("correct", "omission", "confusion", "fabrication", "over_persist", "wrong_reuse")


def export_sheets(run_dirs: list[Path], out_dir: Path, *, per_capability: int = 12, seed: int = 0) -> dict:
    if per_capability < 1:
        raise ValueError("每能力抽样数量必须为正数")
    buckets: dict[str, list[dict]] = {}
    sources: set[tuple[str, str]] = set()
    for directory in run_dirs:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        cases = {c.case_id: c for c in snapshot_cases(directory) or []}
        for line in (directory / "verdicts.jsonl").read_text(encoding="utf-8").splitlines():
            v = Verdict.model_validate_json(line)
            key = (manifest["run_id"], v.probe_id)
            if key in sources:
                raise ValueError(f"重复样本来源: {key}")
            sources.add(key)
            case = cases.get(v.case_id)
            probe = (
                next((p for p in case.probes if p.id == v.probe_id and isinstance(p, JudgeProbe)), None)
                if case
                else None
            )
            if probe is None or case is None or v.decided_by.value in ("rule", "human") or v.degraded:
                continue
            evidence = directory / "cases" / case.case_id / "evidence.jsonl"
            items = [
                Evidence.model_validate_json(s) for s in evidence.read_text(encoding="utf-8").splitlines()
            ]
            store = EvidenceStore(items)
            sid = fingerprint([manifest["run_id"], probe.id])[:16]
            row = {
                "sample_id": sid,
                "capability": case.capability.value,
                "ask": probe.ask,
                "expect": probe.expect,
                "rubric": probe.rubric,
                "answer": _answer_for(store, probe.ask),
                "human_verdict": "",
                "notes": "",
                "judge_verdict": v.verdict.value,
                "family_id": family_of(case),
                "run_id": manifest["run_id"],
                "probe_id": probe.id,
                "case_id": case.case_id,
                "evidence_fingerprint": fingerprint([e.model_dump(mode="json") for e in items]),
            }
            buckets.setdefault(case.capability.value, []).append(row)
    rng = random.Random(seed)
    rows = []
    for cap in sorted(buckets):
        candidates = sorted(buckets[cap], key=lambda r: r["sample_id"])
        rows.extend(rng.sample(candidates, min(per_capability, len(candidates))))
    rng.shuffle(rows)
    if not rows:
        raise ValueError("没有可抽检的 judge 判定；需要包含证据的非降级判卷归档")
    out_dir.mkdir(parents=True, exist_ok=False)
    sheet_fields = ["sample_id", "capability", "ask", "expect", "rubric", "answer", "human_verdict", "notes"]
    key_fields = [
        "sample_id",
        "judge_verdict",
        "family_id",
        "case_id",
        "run_id",
        "probe_id",
        "evidence_fingerprint",
    ]
    for name, fields in [("worksheet.csv", sheet_fields), ("answer-key.csv", key_fields)]:
        with (out_dir / name).open("x", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    info = {
        "status": "awaiting_human_labels",
        "seed": seed,
        "n_samples": len(rows),
        "per_capability": dict(Counter(r["capability"] for r in rows)),
        "sheet_fingerprint": fingerprint(
            [{k: r[k] for k in sheet_fields if k not in ("human_verdict", "notes")} for r in rows]
        ),
        "key_fingerprint": fingerprint([{k: r[k] for k in key_fields} for r in rows]),
    }
    (out_dir / "calibration-manifest.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def analyze_sheets(directory: Path) -> dict:
    def read(name):
        with (directory / name).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if len({r["sample_id"] for r in rows}) != len(rows):
            raise ValueError(f"重复 sample_id: {name}")
        return rows

    rows, keys = read("worksheet.csv"), read("answer-key.csv")
    keyed = {r["sample_id"]: r for r in keys}
    if set(keyed) != {r["sample_id"] for r in rows}:
        raise ValueError("工作表与答案键的样本集合不一致")
    manifest = json.loads((directory / "calibration-manifest.json").read_text(encoding="utf-8"))
    untouched = [{k: r[k] for k in r if k not in ("human_verdict", "notes")} for r in rows]
    if (
        fingerprint(untouched) != manifest["sheet_fingerprint"]
        or fingerprint(keys) != manifest["key_fingerprint"]
    ):
        raise ValueError("盲评题目或答案键被修改，请重新导出")
    confusion: dict[str, dict[str, int]] = {k: {v: 0 for v in LABELS} for k in LABELS}
    n = exact = binary = false_positive = 0
    per_cap: dict[str, list[bool]] = {}
    for row in rows:
        human = row["human_verdict"].strip()
        if not human:
            continue
        if human not in LABELS:
            raise ValueError(f"非法人工标签: {row['sample_id']}={human}")
        judge = keyed[row["sample_id"]]["judge_verdict"]
        if judge not in LABELS:
            continue
        n += 1
        exact += human == judge
        binary += (human == "correct") == (judge == "correct")
        false_positive += judge == "correct" and human != "correct"
        confusion[human][judge] += 1
        per_cap.setdefault(row["capability"], []).append(human == judge)
    result = {
        "status": "completed" if n == len(rows) else "partial" if n else "awaiting_human_labels",
        "n_samples": len(rows),
        "n_labeled": n,
        "n_unlabeled": len(rows) - n,
        "exact_agreement": exact / n if n else None,
        "binary_agreement": binary / n if n else None,
        "false_positive_count": false_positive,
        "confusion_human_rows_judge_columns": confusion,
        "per_capability": {
            c: {"n": len(hits), "agreement": sum(hits) / len(hits)} for c, hits in per_cap.items()
        },
    }
    if n:
        chance = sum(sum(confusion[k].values()) * sum(confusion[h][k] for h in LABELS) for k in LABELS) / n**2
        result["cohen_kappa"] = (exact / n - chance) / (1 - chance) if chance < 1 else None
    (directory / "calibration.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
