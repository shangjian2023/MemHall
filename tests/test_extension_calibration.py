"""Explicit plugin registration and blind calibration integrity."""
import csv

import pytest
from test_benchmark_papers import finished_run

from memhall.adapters import REGISTERED, create_adapter, register_adapter
from memhall.adapters.mock import MockAdapter
from memhall.calibration import analyze_sheets, export_sheets
from memhall.datasets import load_pack
from memhall.sampling import make_paper
from memhall.scoring.rules import ASSERTS, EvidenceStore, register_assertion, run_check


def test_adapter_plugin_and_collision(monkeypatch):
    monkeypatch.setitem(REGISTERED, "sentinel", MockAdapter)
    with pytest.raises(ValueError, match="冲突"):
        register_adapter("mock", MockAdapter)
    with pytest.raises(TypeError):
        register_adapter("bad", dict)
    assert isinstance(create_adapter("sentinel"), MockAdapter)


def test_assertion_plugin_and_collision(monkeypatch):
    monkeypatch.setitem(ASSERTS, "example.always", lambda args, ev: True)
    with pytest.raises(ValueError, match="冲突"):
        register_assertion("default", lambda args, ev: True)
    assert run_check([{"assert": "example.always", "then": "correct"}], EvidenceStore([])) == ("correct", 0)


def test_calibration_pending_labels_and_integrity(tmp_path):
    directory = finished_run(tmp_path / "runs", make_paper(load_pack("quick")))
    out = tmp_path / "calibration"
    info = export_sheets([directory], out, per_capability=2, seed=123)
    assert info["status"] == "awaiting_human_labels"
    assert analyze_sheets(out)["exact_agreement"] is None
    sheet = out / "worksheet.csv"
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert all(not r["human_verdict"] for r in rows)
    assert all("judge_verdict" not in r and "run_id" not in r for r in rows)
    rows[0]["human_verdict"] = "correct"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert analyze_sheets(out)["n_labeled"] >= 1
    rows[0]["expect"] = "changed"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="被修改"):
        analyze_sheets(out)


def test_installed_entry_points_load_lazily(monkeypatch):
    import sys
    import types
    from importlib.metadata import EntryPoint

    import memhall.adapters as adapters
    import memhall.scoring.rules as rules
    module = types.ModuleType("example_memhall_plugin")
    module.Adapter = MockAdapter
    module.assertion = lambda args, ev: bool(args[0])
    monkeypatch.setitem(sys.modules, module.__name__, module)
    ep_adapter = EntryPoint(name="example-installed", value="example_memhall_plugin:Adapter",
                            group="memhall.adapters")
    ep_assert = EntryPoint(name="example.installed", value="example_memhall_plugin:assertion",
                           group="memhall.assertions")
    monkeypatch.setattr(adapters, "REGISTERED", {})
    monkeypatch.setattr(adapters, "entry_points", lambda **kw: [ep_adapter])
    assert isinstance(adapters.create_adapter("example-installed"), MockAdapter)
    monkeypatch.setattr(rules, "ASSERTS", dict(ASSERTS))
    monkeypatch.setattr(rules, "entry_points", lambda **kw: [ep_assert])
    assert rules.run_check([{"assert": "example.installed", "args": [True], "then": "correct"}],
                           EvidenceStore([])) == ("correct", 0)
