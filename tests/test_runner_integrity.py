"""runner 产物完整性校验测试（择优移植自 leeyu44 PR #3，对准主线格式：
无 cases.json/case_results 封印，核心校验 = 证据 schema/payload 哈希/阶段
覆盖 + 新增的 output_hashes 评分产物封印）。"""

from __future__ import annotations

import json
from pathlib import Path

from memhall.adapters.mock import MockAdapter
from memhall.cli import _finish_run, load_cases
from memhall.report.stability import analyze_stability
from memhall.runner.orchestrator import run_suite
from memhall.runner.verify import verify_run
from memhall.scoring.engine import evaluate_case

REPO = Path(__file__).parent.parent


def _finish_mock_run(root: Path, cases):
    root.mkdir(parents=True, exist_ok=True)
    run_id, stores = run_suite(MockAdapter(), cases, root, "mock")
    run_dir = root / run_id
    verdicts = [
        verdict
        for case, store in zip(cases, stores, strict=True)
        for verdict in evaluate_case(case, store, run_id)
    ]
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8"))
    _finish_run(
        run_dir, run_id, manifest, verdicts,
        {case.case_id: case for case in cases},
    )
    return run_dir, verdicts


def test_verifier_accepts_case_without_optional_confound_phase(tmp_path: Path):
    case = next(item for item in load_cases(REPO / "cases" / "full")
                if item.case_id == "recall-001")
    assert [phase.name for phase in case.phases] == ["inject", "probe"]
    run_dir, _ = _finish_mock_run(tmp_path, [case])
    result = verify_run(run_dir)
    assert result.ok, result.errors
    # 主线格式降级路径生效：无封印只警告，不算篡改
    assert any("无 case_results" in warning for warning in result.warnings)
    # 报告辅助图（最终展示物多样化）：构成条生成并嵌入报告
    assert (run_dir / "report-verdict-mix.png").is_file()
    assert "![判定构成]" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_verifier_detects_payload_tampering(tmp_path: Path):
    cases = load_cases(REPO / "cases" / "full")[:1]
    run_dir, _ = _finish_mock_run(tmp_path, cases)
    evidence_path = next((run_dir / "cases").iterdir()) / "evidence.jsonl"
    lines = evidence_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["payload"]["tampered"] = True
    lines[0] = json.dumps(record, ensure_ascii=False)
    evidence_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = verify_run(run_dir)
    assert not result.ok
    assert any("payload SHA-256" in error for error in result.errors)


def test_verifier_detects_scoring_output_tampering(tmp_path: Path):
    cases = load_cases(REPO / "cases" / "full")[:1]
    run_dir, _ = _finish_mock_run(tmp_path, cases)
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "output_hashes" in manifest, "_seal_outputs 未写封印"
    verdict_path = run_dir / "verdicts.jsonl"
    records = verdict_path.read_text(encoding="utf-8").splitlines()
    verdict = json.loads(records[0])
    verdict["explanation"] = "tampered"
    records[0] = json.dumps(verdict, ensure_ascii=False)
    verdict_path.write_text("\n".join(records) + "\n", encoding="utf-8")
    result = verify_run(run_dir)
    assert not result.ok
    assert "评分产物哈希不一致: verdicts.jsonl" in result.errors


def test_repeat_analysis_reports_pass_to_k(tmp_path: Path):
    cases = load_cases(REPO / "cases" / "full")[:1]
    first, _ = _finish_mock_run(tmp_path / "a", cases)
    second, _ = _finish_mock_run(tmp_path / "b", cases)
    out = tmp_path / "stability"
    result = analyze_stability([first, second], out)
    assert result["verdict_agreement_rate"] == 1.0
    assert result["n_flips"] == 0
    assert 0.0 <= result["pass_all_rate"] <= 1.0
    assert (out / "stability.json").is_file()
    assert "pass^2" in (out / "stability.md").read_text(encoding="utf-8")
    # 主线 manifest 无 case_capabilities：能力标签应从 case.yaml 回填而非 unknown
    caps = result["capability_pass_all"]
    assert caps and all(key != "unknown" for key in caps), caps
