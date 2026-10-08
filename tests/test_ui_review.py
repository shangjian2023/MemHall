"""人工复核入口（UI API）测试：未决队列 → 裁决 → 指标重算全链路。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

import memhall.ui.app as uiapp
from memhall.ui.app import create_app

REPO = Path(__file__).parent.parent
RID = "20990101-000000-mock"


def _make_run(root: Path) -> Path:
    d = root / RID
    case_dir = d / "cases" / "persist-002"
    case_dir.mkdir(parents=True)
    shutil.copy2(REPO / "cases" / "full" / "persist-002.yaml",
                 case_dir / "case.yaml")
    reply = {"text": "我记得你的常用编辑器是 vim。", "session_id": "s-01"}
    evidence = {
        "evidence_id": "ev-000001", "run_id": RID, "case_id": "persist-002",
        "phase": "probe", "type": "dialogue",
        "payload": {"messages": ["我常用什么编辑器？"], "replies": [reply],
                    "skipped": False},
    }
    (case_dir / "evidence.jsonl").write_text(
        json.dumps(evidence, ensure_ascii=False) + "\n", encoding="utf-8")
    verdicts = [
        {"verdict_id": "v1", "probe_id": "persist-002-p2",
         "case_id": "persist-002", "run_id": RID, "verdict": "correct",
         "confidence": 1.0, "decided_by": "rule", "evidence_refs": [],
         "explanation": ""},
        # human_review 只会出现在 judge 探测点（rule 探测无未决态）
        {"verdict_id": "v2", "probe_id": "persist-002-p1",
         "case_id": "persist-002", "run_id": RID, "verdict": "human_review",
         "confidence": 1.0, "decided_by": "scripted", "evidence_refs": [],
         "explanation": "脚本判卷无法裁决"},
    ]
    (d / "verdicts.jsonl").write_text(
        "\n".join(json.dumps(v, ensure_ascii=False) for v in verdicts) + "\n",
        encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({
        "run_id": RID, "adapter": "mock", "cases": ["persist-002"],
        "started_at": "20990101-000000", "judge": {"mode": "scripted"},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "metrics.json").write_text("{}", encoding="utf-8")
    return d


def _client(root: Path, monkeypatch) -> TestClient:
    app = create_app()
    monkeypatch.setattr(uiapp, "_runs_root", lambda: root)
    return TestClient(app)


def test_review_queue_lists_pending_with_context(tmp_path, monkeypatch):
    _make_run(tmp_path)
    c = _client(tmp_path, monkeypatch)
    r = c.get(f"/api/runs/{RID}/review")
    assert r.status_code == 200
    data = r.json()
    assert data["n_pending"] == 1
    item = data["items"][0]
    assert item["probe_id"] == "persist-002-p1"
    assert item["ask"]  # 题面在
    assert "vim" in item["agent_reply"]  # 智能体回答在


def test_review_decide_recomputes_metrics_and_seals(tmp_path, monkeypatch):
    run_dir = _make_run(tmp_path)
    c = _client(tmp_path, monkeypatch)
    r = c.post(f"/api/runs/{RID}/review/persist-002-p1",
               json={"verdict": "omission", "note": "回答含糊，判遗漏"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["verdict"] == "omission"

    verdicts = [json.loads(line) for line
                in (run_dir / "verdicts.jsonl").read_text(
                    encoding="utf-8").splitlines()]
    hit = next(v for v in verdicts if v["probe_id"] == "persist-002-p1")
    assert hit["verdict"] == "omission"
    assert hit["decided_by"] == "human"
    assert "人工复核→omission" in hit["explanation"]
    assert "回答含糊" in hit["explanation"]
    # 留痕备份 + 指标重算 + 封印刷新
    assert (run_dir / "verdicts.pre-review.jsonl").is_file()
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["n_probes_total"] >= 2
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("human_review_applied") == 1
    assert "output_hashes" in manifest
    # 已裁决的再裁决 → 409
    r2 = c.post(f"/api/runs/{RID}/review/persist-002-p1",
                json={"verdict": "correct"})
    assert r2.status_code == 409
    # 非法判定值 → 400
    r3 = c.post(f"/api/runs/{RID}/review/persist-002-p2",
                json={"verdict": "banana"})
    assert r3.status_code == 400
