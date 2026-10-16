"""runner 产物完整性校验测试（对准主线格式：runner 落盘全套装印——
cases.json 题库快照、case_hashes/cases_version 题库指纹、case_results
逐题证据哈希、evidence_bundle_sha256 整包哈希、output_hashes 评分产物
封印；verify 对封印有则强校验）。"""

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


def _finish_mock_run(root: Path, cases, judge_mode="scripted"):
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
        judge_mode=judge_mode,
    )
    return run_dir, verdicts


def test_single_judge_manifest_ignores_legacy_b_environment(tmp_path, monkeypatch):
    """旧配置存在时，新归档不能误报双评委并改变报告与聚合口径。"""
    monkeypatch.setenv("JUDGE_A_MODEL", "judge-a")
    monkeypatch.setenv("JUDGE_B_MODEL", "obsolete-judge-b")
    cases = load_cases(REPO / "cases" / "full")[:1]
    run_dir, _ = _finish_mock_run(tmp_path, cases, judge_mode="dual")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["judge"]["model_a"] == "judge-a"
    assert manifest["judge"]["model_b"] is None
    assert manifest["scoring_protocol"]["judge"]["model_b"] is None
    assert "双 LLM 评委" not in (run_dir / "report.md").read_text(encoding="utf-8")


def test_verifier_accepts_case_without_optional_confound_phase(tmp_path: Path):
    case = next(item for item in load_cases(REPO / "cases" / "full")
                if item.case_id == "recall-001")
    assert [phase.name for phase in case.phases] == ["inject", "probe"]
    run_dir, _ = _finish_mock_run(tmp_path, [case])
    result = verify_run(run_dir)
    assert result.ok, result.errors
    # R01 封印齐全：不再有"无封印降级"警告，题库/逐题/整包封印全部生效
    assert not any("封印" in warning for warning in result.warnings)
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8"))
    for key in ("cases_snapshot_sha256", "case_hashes", "cases_version",
                "case_results", "evidence_bundle_sha256",
                "source_fingerprint"):
        assert key in manifest, f"manifest 缺封印字段 {key}"
    assert len(manifest["case_results"]) == 1
    entry = manifest["case_results"][0]
    assert entry["evidence_sha256"] and entry["evidence_count"] > 0
    assert (run_dir / "cases" / "recall-001" / "case.json").is_file()
    # 报告辅助图（最终展示物多样化）：构成条生成并嵌入报告
    assert (run_dir / "report-verdict-mix.png").is_file()
    assert "![判定构成]" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_verifier_detects_evidence_file_swap(tmp_path: Path):
    """绕过 payload 哈希的文件级替换（如重排/重序列化条目）也必须被
    case_results 逐题封印抓到——这是主线此前唯一缺的防篡改层。"""
    cases = load_cases(REPO / "cases" / "full")[:1]
    run_dir, _ = _finish_mock_run(tmp_path, cases)
    evidence_path = next((run_dir / "cases").iterdir()) / "evidence.jsonl"
    lines = evidence_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    lines[0] = json.dumps(dict(reversed(list(record.items()))),
                          ensure_ascii=False)
    evidence_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = verify_run(run_dir)
    assert not result.ok
    assert any("evidence.jsonl 文件哈希不一致" in error
               for error in result.errors)


def test_verifier_detects_cases_json_swap(tmp_path: Path):
    """cases.json 题库快照被改（换题/改步骤文本）→ 题库指纹失配。"""
    cases = load_cases(REPO / "cases" / "full")[:1]
    run_dir, _ = _finish_mock_run(tmp_path, cases)
    snapshot_path = run_dir / "cases.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["cases"][0]["phases"][0]["steps"][0]["user"] = "换过的题面"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8")
    result = verify_run(run_dir)
    assert not result.ok
    assert any("题库指纹" in error or "case_hashes" in error or
               "cases.json" in error for error in result.errors)


def test_seal_marks_failed_case_and_verify_fails(tmp_path: Path):
    """单 case 崩掉（R23 续跑语义）：case_results 记 failed、整包哈希含
    None，verify 硬失败——残缺轮不能混进通过名单。"""
    from memhall.adapters.base import AdapterError

    class BoomAdapter(MockAdapter):
        # dump_memory 在 inject 后即采——未被 RUNTIME_ERROR 通道兜住的
        # case 级异常，走 R23 failed_cases 路径（无 evidence.jsonl 落盘）
        def dump_memory(self):
            raise AdapterError("boom")

    cases = load_cases(REPO / "cases" / "full")[:1]
    root = tmp_path
    run_id, stores = run_suite(BoomAdapter(), cases, root, "mock")
    run_dir = root / run_id
    assert stores == []
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["failed_cases"] == [cases[0].case_id]
    entry = manifest["case_results"][0]
    assert entry["failed"] is True
    assert manifest["evidence_bundle_sha256"]
    result = verify_run(run_dir)
    assert not result.ok
    assert any("缺少 evidence.jsonl" in error for error in result.errors)


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
