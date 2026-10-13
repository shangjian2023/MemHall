"""参赛材料审计发现的执行、配置持久化和统计口径回归。"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import memhall.adapters.remote as remote
import memhall.ui.app as uiapp
from memhall.env import _parse_line
from memhall.schema.models_case import MemoryCase, probe_role

ROOT = Path(__file__).resolve().parents[1]


def _stats_module():
    spec = importlib.util.spec_from_file_location("audit_stats", ROOT / "scripts/stats_uncertainty.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_channel_prefers_native_git_bash(tmp_path, monkeypatch):
    git = tmp_path / "Git" / "cmd" / "git.exe"
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.touch()
    monkeypatch.setattr(remote.sys, "platform", "win32")
    monkeypatch.setattr(remote.shutil, "which", lambda name: str(git) if name == "git" else "wsl-bash")
    assert remote.LocalChannel(password="dummy").bash == str(bash)


@pytest.mark.parametrize("value", ["safe\nVM_HOST=attacker", "safe\rVM_HOST=attacker", "x\0y", 123])
def test_config_rejects_line_injection_without_writing(tmp_path, monkeypatch, value):
    env = tmp_path / ".env"
    env.write_text('VM_HOST="127.0.0.1"\n', encoding="utf-8")
    monkeypatch.setattr(uiapp, "ENV_PATH", env)
    before = env.read_bytes()
    response = TestClient(uiapp.create_app()).post("/api/config", json={"updates": {"VM_PASS": value}})
    assert response.status_code == 400
    assert env.read_bytes() == before


def test_config_roundtrips_hash_and_quote_characters(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(uiapp, "ENV_PATH", env)
    monkeypatch.delenv("VM_PASS", raising=False)
    value = 'pa #ss"word'
    response = TestClient(uiapp.create_app()).post("/api/config", json={"updates": {"VM_PASS": value}})
    assert response.status_code == 200
    assert _parse_line(env.read_text(encoding="utf-8")) == ("VM_PASS", value)


def test_uncertainty_excludes_diagnostics_using_run_snapshot(tmp_path):
    case_dir = tmp_path / "cases" / "persist-001"
    case_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "cases/full/persist-001.yaml", case_dir / "case.yaml")
    case = MemoryCase.model_validate(yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8")))
    expected = {p.id: "correct" for p in case.probes if probe_role(case, p) == "score"}
    verdicts = tmp_path / "verdicts.jsonl"
    verdicts.write_text(
        "".join(json.dumps({"probe_id": p.id, "verdict": "correct"}) + "\n" for p in case.probes),
        encoding="utf-8",
    )
    assert _stats_module().load_verdicts(verdicts) == expected


def test_power_uses_actual_four_round_means():
    delta, rounds = _stats_module().required_rounds([0.7, 0.8, 0.7, 0.8], [0.6, 0.7, 0.6, 0.7])
    assert delta == pytest.approx(0.1)
    assert math.ceil(rounds) == 6


def test_ui_reports_scoring_denominator_and_snapshot_roles(tmp_path, monkeypatch):
    rid = "20990101-000000-mock"
    run = tmp_path / rid
    case_dir = run / "cases/persist-001"
    case_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "cases/full/persist-001.yaml", case_dir / "case.yaml")
    (run / "manifest.json").write_text(json.dumps({"adapter": "mock"}), encoding="utf-8")
    (run / "metrics.json").write_text(
        json.dumps(
            {
                "overall_score": 1.0,
                "n_valid": 1,
                "n_probes_total": 2,
                "n_score_probes": 1,
                "n_diagnostic_probes": 1,
            }
        ),
        encoding="utf-8",
    )
    (run / "verdicts.jsonl").write_text(
        "".join(
            json.dumps({"probe_id": pid, "verdict": "correct"}) + "\n"
            for pid in ["persist-001-p1", "persist-001-p2"]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(uiapp, "_runs_root", lambda: tmp_path)
    client = TestClient(uiapp.create_app())
    assert client.get("/api/runs").json()["runs"][0]["n_total"] == 1
    payload = client.get(f"/api/runs/{rid}/data").json()
    assert payload["metrics"]["adapter"] == "mock"
    assert [v["role"] for v in payload["verdicts"]] == ["score", "diagnostic"]


def test_unconfigured_judge_b_has_no_agreement_measurement():
    from memhall.report.metrics import _agreement_rate
    from memhall.schema.evidence import Verdict

    v = Verdict.model_validate(
        {
            "verdict_id": "v1",
            "probe_id": "p1",
            "case_id": "c1",
            "run_id": "r1",
            "verdict": "correct",
            "decided_by": "judge_a",
            "judge_meta": {
                "judge_a": {"model": "a", "verdict": "correct", "agreed": False},
                "judge_b": {"model": "", "verdict": "invalid_run", "agreed": False},
            },
        }
    )
    assert _agreement_rate([v]) is None


def test_uncertainty_excludes_degraded_fallback_from_score(tmp_path):
    verdicts = tmp_path / "verdicts.jsonl"
    verdicts.write_text(
        json.dumps({"probe_id": "p1", "verdict": "correct", "degraded": True}) + "\n"
        + json.dumps({"probe_id": "p2", "verdict": "omission", "degraded": False}) + "\n",
        encoding="utf-8",
    )
    stats = _stats_module()
    loaded = stats.load_verdicts(verdicts)
    assert stats.binary(loaded["p1"]) is None
    assert stats.overall(loaded) == 0.0
