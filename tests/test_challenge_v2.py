"""Generated instances are reproducible; template families and splits are explicit."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from memhall.adapters.fs_hashes import local_hashes
from memhall.adapters.mock import MockAdapter
from memhall.datasets import case_fingerprint, load_pack
from memhall.generation import generate_cases, write_pack
from memhall.report.cluster import bootstrap_ci, load_clusters
from memhall.runner.orchestrator import run_suite
from memhall.schema.models_case import MemoryCase
from memhall.scoring.engine import evaluate_case


def test_published_packs_are_regenerable():
    for split, pack in [("test", "cases/challenge-v2"), ("development", "cases/development-v2")]:
        rebuilt = generate_cases(seed=20261013, variants=3, split=split)
        assert case_fingerprint(rebuilt) == case_fingerprint(load_pack(pack).cases)
    test = generate_cases(seed=1, variants=1)
    dev = generate_cases(seed=1, variants=1, split="development")
    assert {c.family_id for c in test}.isdisjoint({c.family_id for c in dev})


def test_thousand_instances_do_not_inflate_family_count(tmp_path):
    cases = generate_cases(seed=7, variants=42)
    assert len(cases) == 1008
    assert len({c.family_id for c in cases}) == 24
    assert len({c.case_id for c in cases}) == 1008
    assert case_fingerprint(cases) == case_fingerprint(generate_cases(seed=7, variants=42))
    with pytest.raises(ValueError, match="私有 heldout"):
        generate_cases(seed=1, variants=2, split="heldout")
    with pytest.raises(FileExistsError):
        write_pack(tmp_path, seed=1, variants=1)


def test_cluster_keeps_correlated_probes_together():
    # Two families; one has 50 identical correct probes, the other 50 incorrect.
    # Treating 100 probes as independent would give a falsely narrow interval.
    pool = {"persist": {"a": {f"a{i}": [1., 1.] for i in range(50)},
                         "b": {f"b{i}": [0., 0.] for i in range(50)}}}
    r = bootstrap_ci(pool, "persist", rounds=2)
    assert r["ci95"] == [0., 1.]
    assert r["n_clusters"] == 2


class FileAdapter(MockAdapter):
    def __init__(self, root, actually_write=True):
        super().__init__()
        self.root, self.actually_write = root, actually_write

    def send(self, session_id, message):
        if self.actually_write:
            if message == "create-start":
                (self.root / "result.txt").write_bytes(b"initial\n")
            if message == "change-final":
                (self.root / "result.txt").write_bytes(b"accepted\n")
        return super().send(session_id, message)

    def fs_snapshot(self):
        return [p.name for p in self.root.glob("*.txt")]

    def fs_fingerprints(self, paths):
        return local_hashes(paths, self.root)


def test_task_artifact_requires_bytes_and_probe_stage(tmp_path):
    raw = {"case_id": "reuse-content", "capability": "reuse", "question_type": "task_chain",
           "content_type": "template", "difficulty": 2,
           "meta": {"author": "test", "created": "2026-10-13", "source": "seed"},
           "phases": [{"name": "inject", "steps": [{"task": "create-start"}], "end_session": True},
                      {"name": "probe", "steps": [{"task": "change-final"}]}],
           "probes": [{"kind": "rule", "id": "reuse-content-p1", "role": "score", "check": [
               {"assert": "fs.artifact_matches", "args": [{"path": "~/result.txt",
                  "sha256": hashlib.sha256(b"accepted\n").hexdigest(), "stage": "probe"}], "then": "correct"},
               {"assert": "default", "then": "wrong_reuse"}]}]}
    case = MemoryCase.model_validate(raw)
    files = tmp_path / "files"
    files.mkdir()
    rid, stores = run_suite(FileAdapter(files), [case], tmp_path / "runs", "mock")
    assert evaluate_case(case, stores[0], rid)[0].verdict.value == "correct"
    diff = stores[0].latest_fs_diff()
    assert any(e.change == "modified" and e.stage == "probe" for e in diff.entries)
    raw["probes"][0]["check"][0]["args"][0]["sha256"] = hashlib.sha256(b"wrong").hexdigest()
    wrong = MemoryCase.model_validate(raw)
    assert evaluate_case(wrong, stores[0], rid)[0].verdict.value == "wrong_reuse"
    empty = tmp_path / "empty"
    empty.mkdir()
    rid, stores = run_suite(FileAdapter(empty, False), [case], tmp_path / "runs", "mock")
    assert evaluate_case(case, stores[0], rid)[0].verdict.value == "wrong_reuse"


def test_cluster_excludes_degraded_diagnostic_and_unknown(tmp_path):
    case = load_pack("quick").cases[0]
    case_dir = tmp_path / "cases" / case.case_id
    case_dir.mkdir(parents=True)
    import yaml
    (case_dir / "case.yaml").write_text(yaml.safe_dump(case.model_dump(mode="json")), encoding="utf-8")
    rows = [{"verdict_id": "v1", "case_id": case.case_id, "probe_id": "unknown",
             "run_id": "r", "decided_by": "rule", "verdict": "correct"}]
    for i, probe in enumerate(case.probes, 2):
        rows.append(dict(rows[0], verdict_id=f"v{i}", probe_id=probe.id, degraded=True))
    (tmp_path / "verdicts.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert bootstrap_ci(load_clusters([tmp_path]), "__overall__", rounds=1)["ci95"] is None


def test_ui_paper_import_roundtrip_and_invalid_run(tmp_path, monkeypatch):
    import memhall.ui.app as ui
    from memhall.sampling import make_paper
    monkeypatch.setattr(ui, "_runs_root", lambda: tmp_path)
    client = TestClient(ui.create_app())
    paper = make_paper(load_pack("quick"))
    r = client.post("/api/papers", json={"paper": paper.model_dump(mode="json")})
    assert r.status_code == 200
    pid = r.json()["paper_id"]
    assert client.get("/api/papers").json()["papers"][0]["paper_id"] == pid
    assert client.get(f"/api/papers/{pid}").json()["paper_id"] == pid
    assert client.get("/api/papers/..%2Fbad").status_code != 200
    bad = paper.model_dump(mode="json")
    bad["cases"][0]["difficulty"] = 3
    assert client.post("/api/papers", json={"paper": bad}).status_code == 400
    assert client.post("/api/start", json={"adapter": "mock", "paper": "0" * 64}).status_code == 400
