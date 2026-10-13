"""token 成本记账与预估（跑前弹窗/CLI 提示行数据源）+ report 渲染。"""

from __future__ import annotations

import json
from pathlib import Path

from memhall.cost import estimate, summarize, usage_delta, usage_snapshot


def _write_log(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _row(agent: str, total: int, status: int = 200) -> dict:
    return {"agent": agent, "status": status, "n": 1, "errors": 0,
            "total_tokens": total, "prompt_tokens": total - 5,
            "completion_tokens": 5}


def test_usage_snapshot_delta_summarize(tmp_path):
    log = tmp_path / "gw.jsonl"
    _write_log(log, [_row("memhall-hermes", 100)])
    before = usage_snapshot(log)
    assert before["memhall-hermes"]["total_tokens"] == 100
    _write_log(log, [_row("memhall-hermes", 60), _row("unknown", 40)])
    after = usage_snapshot(log)
    d = usage_delta(before, after)
    assert d["memhall-hermes"]["total_tokens"] == 60
    s = summarize(d)
    # unknown（误配真凭据等）不进 run 汇总
    assert s["total_tokens"] == 60 and s["requests"] == 1
    assert set(s["tags"]) == {"memhall-hermes"}
    # 无 memhall-* 流量（直连模式）→ None，manifest 不落键
    assert summarize(usage_delta(before, {"unknown": after["unknown"]})) is None


def test_estimate_from_history_median(tmp_path):
    def mk_run(d: Path, adapter: str, n: int, total: int, req: int = 10) -> None:
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "adapter": adapter, "cases": ["x"] * n,
            "token_usage": {"total_tokens": total, "requests": req}}),
            encoding="utf-8")

    mk_run(tmp_path / "20261001-1-agent", "hermes", 47, 7_400_000, req=512)
    mk_run(tmp_path / "20261002-2-agent", "hermes", 47, 7_600_000, req=520)
    mk_run(tmp_path / "20261002-3-agent", "kylinbot", 47, 14_000_000)  # 别的智能体
    mk_run(tmp_path / "20261002-4-agent", "hermes", 10, 0)             # 无记账
    mk_run(tmp_path / "_void-legacy", "hermes", 47, 999_999)           # 非数字目录跳过
    est = estimate("hermes", 47, tmp_path)
    assert est is not None
    assert est["total_tokens"] == 7_500_000       # 中位数（7.4/7.6 两轮取中）
    assert est["tokens_per_case"] == 7_500_000 // 47
    assert est["requests"] == 516
    assert est["basis_runs"] == 2
    # 首跑无历史 / 直连无记账 → None（调用方提示后照常开跑）
    assert estimate("openclaw", 47, tmp_path) is None
    assert estimate("hermes", 47, tmp_path / "nope") is None


def test_run_suite_records_token_usage(tmp_path, monkeypatch):
    """run_suite 前后网关快照差值落 manifest（monkeypatch 掉真实记账文件）。"""
    import memhall.runner.orchestrator as orch
    from memhall.runner.orchestrator import run_suite
    from memhall.schema.models_case import MemoryCase

    calls = {"i": 0}

    def fake_snapshot():
        i = calls["i"]
        calls["i"] += 1
        return {} if i == 0 else {"memhall-broken": {
            "n": 3, "errors": 0, "prompt_tokens": 90,
            "completion_tokens": 30, "total_tokens": 120}}

    monkeypatch.setattr(orch, "usage_snapshot", fake_snapshot)
    case = MemoryCase(
        case_id="t-001", schema_version="0.1", capability="persist",
        question_type="session_recall", content_type="path", difficulty=1,
        meta={"author": "t", "created": "2026-10-03", "source": "seed"},
        phases=[{"name": "probe", "steps": []}],
        probes=[{"kind": "rule", "id": "t-001-p1", "check": [
            {"assert": "default", "then": "correct"}]}])
    run_id, _ = run_suite(_NoopAdapter(), [case], tmp_path, "broken")
    m = json.loads((tmp_path / run_id / "manifest.json").read_text(encoding="utf-8"))
    assert m["token_usage"]["total_tokens"] == 120
    assert m["token_usage"]["requests"] == 3


class _NoopAdapter:
    name = "broken"

    def reset(self):
        pass

    def send(self, session_id, message):
        raise NotImplementedError

    def end_session(self, sid):
        pass

    def dump_memory(self):
        from datetime import UTC, datetime

        from memhall.schema.evidence import MemorySnapshot
        return MemorySnapshot(format="files", dumped_at=datetime.now(UTC),
                              entries=[], raw=None)

    def dump_actions(self):
        from memhall.schema.evidence import ActionDump
        return ActionDump(actions=[], coverage="unknown")

    def fs_snapshot(self):
        return []

    def clock_shift(self, days):
        pass

    def clock_restore(self):
        pass


def test_report_renders_version_model_and_tokens(tmp_path):
    """report.md 头部：agent 版本 + 模型口径 + token 消耗三行元数据。"""
    from memhall.report.report import render_report

    manifest = {
        "adapter": "openclaw", "cases": ["a"], "git_hash": "abc1234",
        "agent_version": "OpenClaw 2026.9.8 (fc23bc8)",
        "model_backend": {"mode": "gateway", "url": "http://127.0.0.1:8311/v1",
                          "model": "qwen3.7-plus"},
        "token_usage": {"total_tokens": 71863, "requests": 8, "errors": 0},
    }
    metrics = {"n_probes_total": 2, "n_valid": 2, "overall_score": 1.0,
               "rule_scoring_rate": 0.5, "capability_scores": {"persist": 1.0},
               "capability_detail": {cap: {"score": 1.0, "n_correct": 2,
                                           "n_valid": 2, "error_breakdown": {}}
                                     for cap in ("persist", "recall",
                                                 "dynamic_update", "discriminate",
                                                 "boundary", "reuse")}}
    md = render_report(tmp_path, "20261003-000000-openclaw", manifest,
                       [], {"a": None}, metrics)
    assert "OpenClaw 2026.9.8" in md
    assert "统一网关" in md and "qwen3.7-plus" in md
    assert "71,863" in md and "8 次请求" in md
