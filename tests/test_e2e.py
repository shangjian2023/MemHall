"""MVP 端到端：CLI 全链路（run → verdicts/metrics/radar/report）在 tmp 目录出全产物。"""

from __future__ import annotations

import json
from pathlib import Path

from memhall.adapters.mock import MockAdapter
from memhall.cli import load_cases
from memhall.report import compute_metrics, render_radar, render_report
from memhall.runner.orchestrator import run_suite
from memhall.schema.evidence import VerdictValue
from memhall.scoring.engine import evaluate_case

REPO = Path(__file__).parent.parent


def test_case_library_covers_six_capabilities():
    cases = load_cases(REPO / "cases" / "full")
    caps = {c.capability.value for c in cases}
    assert caps == {"persist", "recall", "dynamic_update",
                    "discriminate", "boundary", "reuse"}
    per_cap: dict[str, int] = {}
    for c in cases:
        per_cap[c.capability.value] = per_cap.get(c.capability.value, 0) + 1
    assert all(n >= 2 for n in per_cap.values()), per_cap


def test_mock_question_never_writes():
    a = MockAdapter()
    a.send("s", "我的主力编辑器用 vim")
    a.send("s", "我的备份编辑器是什么？")
    snap = a.dump_memory()
    joined = " ".join(e.content for e in snap.entries)
    assert "备份编辑器" not in joined


def test_end_to_end_pipeline(tmp_path: Path):
    cases = load_cases(REPO / "cases" / "full")
    run_id, stores = run_suite(MockAdapter(), cases, tmp_path, "mock")
    run_dir = tmp_path / run_id
    assert (run_dir / "manifest.json").exists()
    case_dirs = list((run_dir / "cases").iterdir())
    assert len(case_dirs) == len(cases)

    verdicts = []
    for case, store in zip(cases, stores, strict=True):
        verdicts.extend(evaluate_case(case, store, run_id))
    assert verdicts, "判定为空"
    invalid = [v for v in verdicts if v.verdict == VerdictValue.INVALID_RUN]
    assert not invalid, [f"{v.probe_id}: {v.explanation}" for v in invalid]
    # 判卷未决（脚本判不了的语义边界）转人工不计分——离线跑允许少量，
    # 设计目标：人工复核率 <5%（design.md §6.1）；此处只验离线 mock 链路。
    review = [v for v in verdicts if v.verdict == VerdictValue.HUMAN_REVIEW]
    assert len(review) <= 8, [v.probe_id for v in review]

    case_map = {c.case_id: c for c in cases}
    metrics = compute_metrics(verdicts, case_map)
    assert set(metrics["capability_scores"]) == {
        "persist", "recall", "dynamic_update",
        "discriminate", "boundary", "reuse"}
    # mock v2（缺陷注入基线）设计画像：persist/recall 回显≈满分（措辞已解耦）；
    # boundary=照单全收（over_persist）；update=答旧记混；reuse=只说不做。
    # persist/recall 里个别失分是案例内嵌 canary 子探测逮到照单全收，属设计内。
    assert metrics["capability_detail"]["boundary"]["error_breakdown"].get("over_persist")
    assert metrics["capability_scores"]["persist"] >= 0.85
    assert metrics["capability_scores"]["recall"] >= 0.85
    assert metrics["overall_score"] > 0.5

    render_radar({"mock": metrics["capability_scores"]}, str(run_dir / "radar.png"))
    assert (run_dir / "radar.png").stat().st_size > 10_000
    report = render_report(run_dir, run_id,
                           json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")),
                           verdicts, case_map, metrics)
    assert "六维能力" in report and "判定明细" in report
