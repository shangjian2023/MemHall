"""Sampling, protocol isolation and archived-paper integrity regressions."""
import json
from pathlib import Path

import pytest

from memhall.adapters.mock import MockAdapter
from memhall.cli import _finish_run
from memhall.datasets import load_directory, load_pack
from memhall.report.aggregate import aggregate_runs
from memhall.report.compare import compare_runs
from memhall.runner.orchestrator import pair_stores, run_suite
from memhall.runner.verify import verify_run
from memhall.sampling import load_paper, make_paper
from memhall.scoring.engine import evaluate_case


def finished_run(root, paper, adapter="mock"):
    rid, stores = run_suite(MockAdapter(), paper.cases, root, adapter, paper=paper)
    directory = root / rid
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    verdicts = [v for c, st in pair_stores(paper.cases, stores) for v in evaluate_case(c, st, rid)]
    _finish_run(directory, rid, manifest, verdicts, {c.case_id: c for c in paper.cases})
    return directory


def test_sampling_determinism_balance_and_seed(tmp_path):
    pack = load_pack("cases/challenge-v2")
    a = make_paper(pack, count=24, seed=7, max_per_family=1)
    b = make_paper(pack, count=24, seed=7, max_per_family=1)
    c = make_paper(pack, count=24, seed=8, max_per_family=1)
    assert a.paper_id == b.paper_id and a.paper_id != c.paper_id
    assert set(a.sampling["capability_counts"].values()) == {4}
    assert a.sampling["n_families"] == 24
    path = tmp_path / "paper.json"
    a.save(path)
    assert load_paper(path).paper_id == a.paper_id
    with pytest.raises(FileExistsError):
        a.save(path)
    with pytest.raises(ValueError, match="能力维数"):
        make_paper(pack, count=2)
    with pytest.raises(ValueError, match="题族数量不足"):
        make_paper(pack, count=25, max_per_family=1)


def test_embedded_paper_tampering_rejected(tmp_path):
    paper = make_paper(load_pack("quick"))
    path = tmp_path / "paper.json"
    paper.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["cases"][0]["phases"][0]["steps"][0]["user"] = "changed"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="封印"):
        load_paper(path)


def test_same_paper_across_history_and_agents(tmp_path):
    paper = make_paper(load_pack("quick"))
    a = finished_run(tmp_path, paper)
    b = finished_run(tmp_path, paper, "mock-peer")
    for directory in (a, b):
        assert verify_run(directory).ok
        assert load_paper(directory / "paper.json").paper_id == paper.paper_id
    report = compare_runs(a, b, tmp_path / "compare")
    assert report["compatibility"]["verified"]
    assert report["clustered_sign_test"]["unit"] == "family_or_case"
    assert report["n_flips"] == 0


def test_protocol_mismatch_rejected_before_outputs(tmp_path):
    paper = make_paper(load_pack("quick"))
    a = finished_run(tmp_path / "a", paper)
    b = finished_run(tmp_path / "b", paper)
    path = b / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m["scoring_protocol"]["judge"]["mode"] = "dual"
    path.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="评分协议"):
        compare_runs(a, b, tmp_path / "cmp")
    with pytest.raises(ValueError, match="评分协议"):
        aggregate_runs([a, b], tmp_path / "agg")
    assert not (tmp_path / "cmp").exists()
    assert not (tmp_path / "agg").exists()


def test_verify_archive_missing_paper(tmp_path):
    paper = make_paper(load_pack("quick"))
    directory = finished_run(tmp_path, paper)
    (directory / "paper.json").unlink()
    result = verify_run(directory)
    assert not result.ok
    assert any("试卷缺失" in e for e in result.errors)


def test_duplicate_case_rejected(tmp_path):
    source = Path("cases/full/persist-001.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(source, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="重复 case_id"):
        load_directory(tmp_path)


def test_sealed_output_tampering_rejected(tmp_path):
    paper = make_paper(load_pack("quick"))
    a = finished_run(tmp_path / "a", paper)
    b = finished_run(tmp_path / "b", paper)
    path = b / "metrics.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["overall_score"] = 1.0
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="评分产物哈希"):
        compare_runs(a, b, tmp_path / "cmp")


def test_batch_reuses_paper_for_all_rounds(tmp_path):
    import argparse

    from memhall.cli import cmd_batch
    paper = make_paper(load_pack("quick"))
    path = tmp_path / "paper.json"
    paper.save(path)
    args = argparse.Namespace(paper=str(path), adapters=["mock"], repeats=2,
                              model=None, judge="scripted", out=str(tmp_path / "runs"))
    assert cmd_batch(args) == 0
    plan = json.loads(next((tmp_path / "runs").glob("batch-*.json")).read_text(encoding="utf-8"))
    assert plan["status"] == "completed"
    assert len(plan["runs"]) == 2
    for run in plan["runs"]:
        assert load_paper(Path(run["run_dir"]) / "paper.json").paper_id == paper.paper_id
