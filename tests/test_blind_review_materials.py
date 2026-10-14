"""在库盲评材料的完整性回归：指纹对账、双人副本一致、终裁模板对齐。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from memhall.datasets import fingerprint

ROOT = Path(__file__).resolve().parents[1]
BLIND = ROOT / "docs/audit/blind-review-20261014"
BASE = BLIND / "base"
FILLABLE = ("human_verdict", "notes")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_base_manifest_fingerprints_match_committed_sheets():
    manifest = json.loads((BASE / "calibration-manifest.json").read_text(encoding="utf-8"))
    rows = _read_csv(BASE / "worksheet.csv")
    keys = _read_csv(BASE / "answer-key.csv")
    assert manifest["status"] == "awaiting_human_labels"
    assert manifest["n_samples"] == len(rows) == len(keys)
    assert len({r["sample_id"] for r in rows}) == len(rows)
    assert all(not r["human_verdict"] for r in rows)  # 交付时必须尚未填写
    untouched = [{k: r[k] for k in r if k not in FILLABLE} for r in rows]
    assert fingerprint(untouched) == manifest["sheet_fingerprint"]
    assert fingerprint(keys) == manifest["key_fingerprint"]


def test_worksheet_hides_identity_and_machine_labels():
    rows = _read_csv(BASE / "worksheet.csv")
    hidden = {"run_id", "probe_id", "case_id", "family_id", "judge_verdict", "evidence_fingerprint"}
    assert not hidden & set(rows[0])
    assert {r["capability"] for r in rows} == {
        "boundary",
        "discriminate",
        "dynamic_update",
        "persist",
        "recall",
        "reuse",
    }


def test_reviewer_copies_match_base_worksheet():
    base_ws = (BASE / "worksheet.csv").read_bytes()
    base_key = (BASE / "answer-key.csv").read_bytes()
    for n in (1, 2):
        reviewer = BLIND / "reviewers" / f"reviewer-{n}"
        assert (reviewer / "worksheet.csv").read_bytes() == base_ws
        assert (reviewer / "answer-key.csv").read_bytes() == base_key


def test_adjudication_template_ids_follow_worksheet_order():
    rows = _read_csv(BASE / "worksheet.csv")
    adj = _read_csv(BLIND / "adjudication.csv")
    assert [r["sample_id"] for r in adj] == [r["sample_id"] for r in rows]
    assert all(r["final_verdict"] == "" for r in adj)
