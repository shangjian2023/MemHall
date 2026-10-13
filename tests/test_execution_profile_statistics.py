"""C 轮口径修复回归：兼容身份（沙箱/真实版本/模型）+ pass^k 分母账本
+ 延迟覆盖 + 实验索引统计。

对应评审复现（compatibility-review-findings.json）：
- sandbox 开/关两份同题快照曾被接受且 verified=true → 现在必须拒绝；
- 同智能体聚合曾查 runner 从不落账的 adapter_version → 现在查 agent_version。
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

from memhall.datasets import case_fingerprint
from memhall.report.aggregate import aggregate_runs, format_table, pass_k_detail
from memhall.report.cluster import load_clusters, status_pool
from memhall.report.compatibility import (
    check_compatibility,
    runtime_signal,
    sandbox_state,
    version_signal,
)
from memhall.report.metrics import compute_metrics, latency_coverage, latency_summary
from memhall.schema.models_case import JudgeProbe, MemoryCase

RUNS_ROOT_MARKER = "runs-index"


def _mini_case(cid: str = "persist-s", n_score: int = 5, with_diagnostic: bool = True) -> MemoryCase:
    probes = [
        JudgeProbe(
            kind="judge",
            id=f"{cid}-p{i}",
            after="probe",
            ask="风格名？",
            expect=f"mode-{i}",
            rubric="答对即 correct",
            verdict_map={"reported": "correct", "forgot": "omission"},
        )
        for i in range(1, n_score + 1)
    ]
    if with_diagnostic:
        probes.append(
            JudgeProbe(
                kind="judge",
                id=f"{cid}-d1",
                after="probe",
                role="diagnostic",
                ask="库里存了什么？",
                expect="mode-1",
                rubric="存储态断言",
                verdict_map={"reported": "correct", "forgot": "omission"},
            )
        )
    return MemoryCase(
        case_id=cid,
        capability="persist",
        question_type="cross_session_recall",
        content_type="fact",
        difficulty=1,
        meta={"author": "t", "created": "2026-10-14", "source": "seed"},
        phases=[{"name": "probe", "steps": [{"user": "风格名？"}]}],
        probes=probes,
    )


# (probe_id, 轮1判定, 轮2判定)；None=该轮 verdicts.jsonl 缺这条（跑丢）
LEDGER = [
    ("persist-s-p1", "correct", "correct"),      # 全轮有效全对
    ("persist-s-p2", "correct", None),           # 缺一轮
    ("persist-s-p3", "invalid_run", "correct"),  # 一轮无效
    ("persist-s-p4", "correct", "human_review"),  # 一轮未决
    ("persist-s-p5", "degraded", "correct"),      # 一轮降级猜测
]
LEDGER_DIAGNOSTIC = ("persist-s-d1", "correct", "correct")


def _verdict_rows(run_id: str, ledger: list, round_index: int) -> list[dict]:
    rows = []
    for i, (pid, *sides) in enumerate(ledger):
        raw = sides[round_index]
        if raw is None:
            continue
        row = {
            "verdict_id": f"v-{run_id}-{i:03d}",
            "probe_id": pid,
            "case_id": "persist-s",
            "run_id": run_id,
            "verdict": "correct" if raw == "degraded" else raw,
            "decided_by": "rule",
            "confidence": 1.0,
            "explanation": "",
        }
        if raw == "degraded":
            row["degraded"] = True
        rows.append(row)
    return rows


def _write_run(
    tmp: Path,
    run_id: str,
    *,
    case: MemoryCase | None = None,
    ledger: list | None = None,
    round_index: int = 0,
    adapter: str = "hermes",
    agent_version: str | None = "Hermes Agent v0.21.3",
    adapter_version: str | None = None,
    sandbox: str | None = "on",
    models: tuple[str, ...] = ("qwen3.7-plus",),
    runtime_models: tuple[str, ...] | None = None,
    reasoning: tuple[str, ...] = ("未设置（各智能体默认）",),
    suite_id: str = "memhall-full-v1",
    exploratory: bool = False,
    judge_mode: str = "dual",
    with_snapshot: bool = True,
) -> Path:
    case = case or _mini_case()
    ledger = ledger if ledger is not None else LEDGER + [LEDGER_DIAGNOSTIC]
    d = tmp / run_id
    cdir = d / "cases" / case.case_id
    cdir.mkdir(parents=True, exist_ok=True)
    if with_snapshot:
        (cdir / "case.yaml").write_text(
            yaml.safe_dump(case.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
        )
    rows = _verdict_rows(run_id, ledger, round_index)
    (d / "verdicts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    n_valid = sum(
        1
        for _pid, *sides in ledger
        if sides[round_index] not in (None, "invalid_run", "human_review", "degraded")
    )
    n_all = sum(1 for _pid, *sides in ledger if sides[round_index] is not None)
    score = round(sum(
        1
        for _pid, *sides in ledger
        if sides[round_index] == "correct"
    ) / n_valid, 4) if n_valid else None
    metrics = {
        "overall_score": score,
        "capability_scores": {"persist": score},
        "n_valid": n_valid,
        "n_invalid_run": sum(1 for _p, *s in ledger if s[round_index] == "invalid_run"),
        "n_human_review": sum(1 for _p, *s in ledger if s[round_index] == "human_review"),
        "n_probes_total": n_all,
    }
    (d / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "adapter": adapter,
        "cases": [case.case_id],
        "model_backend": {"mode": "gateway", "url": "http://127.0.0.1:8311/v1", "model": models[0]},
        "llm_runtime": {
            "models": list(runtime_models or models),
            "reasoning": list(reasoning),
            "requests": 42,
            "forced_model": True,
        },
        "judge": {
            "mode": judge_mode,
            "prompt_version": "2026-10-04",
            "model_a": "qwen3.7-plus",
            "model_b": None,
        },
        "scoring_protocol": {
            "id": "memhall.behavior-v2",
            "metric_version": "2",
            "judge": {"mode": judge_mode, "prompt_version": "2026-10-04"},
            "invalid_policy": "excluded-and-reported",
            "diagnostic_policy": "excluded",
            "degraded_policy": "excluded",
        },
        "benchmark": {
            "identity_version": "1.0",
            "suite": {
                "suite_id": suite_id,
                "suite_version": "1.0.0",
                "suite_fingerprint": "seed-fingerprint",
                "scoring_protocol": "memhall.behavior-v2",
            },
            "selection_fingerprint": case_fingerprint([case]) if with_snapshot else None,
            "paper_id": None,
            "exploratory": exploratory,
        },
        "token_usage": {"requests": 42, "total_tokens": 987654},
    }
    if sandbox is not None:
        manifest["environment"] = {"sandbox": sandbox}
    if agent_version is not None:
        manifest["agent_version"] = agent_version
    if adapter_version is not None:
        manifest["adapter_version"] = adapter_version
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return d


def _pair(tmp: Path, **overrides) -> tuple[Path, Path]:
    a = _write_run(tmp, "20261015-010101-hermes", round_index=0)
    b = _write_run(tmp, "20261015-020202-hermes", round_index=1, **overrides)
    return a, b


# ---------- 兼容身份：沙箱 / 真实版本 / 模型 ----------


def test_same_profile_aggregates_and_verifies(tmp_path):
    a, b = _pair(tmp_path)
    result = check_compatibility([a, b], aggregate=True)
    assert result["verified"] is True
    assert result["warnings"] == []
    identity = result["aggregate_identity"]
    assert identity["agent_version"] == "Hermes Agent v0.21.3"
    assert identity["version_field"] == "agent_version"
    assert result["identity"]["sandbox"] == "on"
    agg = aggregate_runs([a, b], tmp_path / "agg")
    assert agg["execution_profile"]["sandbox"] == "on"
    assert agg["execution_profile"]["agent_version"] == "Hermes Agent v0.21.3"


def test_sandbox_mismatch_rejected(tmp_path):
    # 评审复现 bug #1：sandbox 开/关两份同题快照曾被接受且 verified=true
    a, b = _pair(tmp_path, sandbox="off")
    with pytest.raises(ValueError, match="沙箱口径不一致"):
        check_compatibility([a, b])
    with pytest.raises(ValueError, match="沙箱口径不一致"):
        check_compatibility([a, b], aggregate=True)


def test_agent_version_mismatch_rejected(tmp_path):
    # 评审复现 bug #2：换真实 agent_version 的两轮曾被放行（查的是不存在的
    # adapter_version 字段）
    a, b = _pair(tmp_path, agent_version="Hermes Agent v0.22.0")
    with pytest.raises(ValueError, match="真实版本不一致"):
        check_compatibility([a, b], aggregate=True)
    # 跨智能体比较不查版本：不同家版本不同是常态，不构成拒绝理由
    cross = check_compatibility([a, b])
    assert cross["verified"] is True


def test_missing_fields_downgrade_not_fake_verified(tmp_path):
    a = _write_run(tmp_path, "20261015-010101-hermes", round_index=0,
                   sandbox=None, agent_version=None)
    b = _write_run(tmp_path, "20261015-020202-hermes", round_index=1,
                   sandbox=None, agent_version=None)
    result = check_compatibility([a, b], aggregate=True)
    assert result["verified"] is False  # 缺字段只降级，不冒充核验通过
    joined = "\n".join(result["warnings"])
    assert "沙箱口径未完全封存" in joined and "不默认当作关闭" in joined
    assert "真实版本未知" in joined and "未按相同处理" in joined
    # 降级 ≠ 拒绝：同题同协议的旧归档仍可聚合，只是带警告
    agg = aggregate_runs([a, b], tmp_path / "agg")
    assert agg["execution_profile"]["sandbox"] is None
    assert "比较限制" in format_table(agg)


def test_cross_agent_compare_allowed_with_different_versions(tmp_path):
    a = _write_run(tmp_path, "20261015-010101-hermes", round_index=0)
    b = _write_run(
        tmp_path,
        "20261015-020202-kylin-agent",
        round_index=1,
        adapter="kylin-agent",
        agent_version="Kylin Agent v1.0.0",
    )
    result = check_compatibility([a, b])  # 跨智能体：不查 adapter/版本
    assert result["verified"] is True
    assert result["aggregate_identity"] is None


def test_legacy_adapter_version_precedence_and_conflict(tmp_path):
    # 两份旧归档只有 adapter_version：同值聚合（带旧字段警告），异值拒绝
    a = _write_run(tmp_path, "20261015-010101-hermes", round_index=0,
                   agent_version=None, adapter_version="legacy-1")
    b = _write_run(tmp_path, "20261015-020202-hermes", round_index=1,
                   agent_version=None, adapter_version="legacy-1")
    r = check_compatibility([a, b], aggregate=True)
    assert r["verified"] is False
    assert any("adapter_version" in w for w in r["warnings"])
    assert r["aggregate_identity"]["version_field"] == "adapter_version"

    a2 = _write_run(tmp_path, "20261015-030303-hermes", round_index=0,
                    agent_version=None, adapter_version="legacy-1")
    b2 = _write_run(tmp_path, "20261015-040404-hermes", round_index=1,
                    agent_version=None, adapter_version="legacy-2")
    with pytest.raises(ValueError, match="真实版本不一致"):
        check_compatibility([a2, b2], aggregate=True)

    # 字段混用：一轮 agent_version、一轮 adapter_version，语义不同不可互证
    a3 = _write_run(tmp_path, "20261015-050505-hermes", round_index=0,
                    agent_version="same-string")
    b3 = _write_run(tmp_path, "20261015-060606-hermes", round_index=1,
                    agent_version=None, adapter_version="same-string")
    r3 = check_compatibility([a3, b3], aggregate=True)
    assert r3["verified"] is False
    assert any("版本字段混用" in w for w in r3["warnings"])
    assert r3["aggregate_identity"]["version_field"] == "mixed"


def test_signal_helpers():
    assert sandbox_state({"environment": {"sandbox": "bwrap"}}) == "on"
    assert sandbox_state({"environment": {"sandbox": "0"}}) == "off"
    assert sandbox_state({"environment": {"sandbox": "???"}}) is None  # 不可识别不猜
    assert sandbox_state({}) is None
    assert version_signal({"agent_version": "v2", "adapter_version": "v1"}) == ("v2", "agent_version")
    assert version_signal({"adapter_version": "v1"}) == ("v1", "adapter_version")
    assert version_signal({}) == (None, None)
    assert runtime_signal({"llm_runtime": {"models": ["b", "a"], "reasoning": []}})["models"] == ["a", "b"]
    assert runtime_signal({})["models"] == []


def test_runtime_model_or_reasoning_mismatch_rejected(tmp_path):
    # model_backend 与 llm_runtime 分别校验：网关改写模型相同、实际请求
    # 模型不同（账单归并抓到）也要拒绝
    a, b = _pair(tmp_path, runtime_models=("qwen3.5-turbo",))
    with pytest.raises(ValueError, match="实际请求模型/思考强度不一致"):
        check_compatibility([a, b], aggregate=True)
    a2, b2 = _pair(tmp_path, reasoning=("high",))
    with pytest.raises(ValueError, match="实际请求模型/思考强度不一致"):
        check_compatibility([a2, b2], aggregate=True)
    a3, b3 = _pair(tmp_path, judge_mode="scripted")
    with pytest.raises(ValueError, match="评分协议"):
        check_compatibility([a3, b3], aggregate=True)


# ---------- pass^k 分母账本 ----------


def test_pass_k_detail_ledger(tmp_path):
    a = _write_run(tmp_path, "20261015-010101-hermes", round_index=0)
    b = _write_run(tmp_path, "20261015-020202-hermes", round_index=1)
    statuses = status_pool([a, b])
    pool = load_clusters([a, b])
    # diagnostic 探测不进池（d1 正确两轮也不算"应考"）
    ids = {pid for fam in statuses["__overall__"].values() for pid in fam}
    assert "persist-s-d1" not in ids and ids == {f"persist-s-p{i}" for i in range(1, 6)}
    detail = pass_k_detail(statuses, "__overall__", 2)
    assert detail["eligible"] == 5
    assert detail["n_full"] == 1  # 只有 p1 两轮都有效
    assert detail["n_pass_all"] == 1
    assert detail["coverage"] == pytest.approx(0.2)
    assert detail["rate_conditional"] == 1.0  # 条件口径：考全的那 1 个全过
    assert detail["floor_conservative"] == pytest.approx(0.2)  # 未完成按未全过
    assert detail["incomplete"] == {
        "missing": 1, "invalid_run": 1, "human_review": 1, "degraded": 1,
    }
    # 条件通过率分母（旧行为）只含全轮有效：1/1
    from memhall.report.aggregate import _pass_k

    assert _pass_k(pool, "__overall__", 2) == {"k": 2, "rate": 1.0, "n_probes": 1}


def test_pass_k_ledger_visible_in_aggregate(tmp_path):
    a = _write_run(tmp_path, "20261015-010101-hermes", round_index=0)
    b = _write_run(tmp_path, "20261015-020202-hermes", round_index=1)
    r = aggregate_runs([a, b], tmp_path / "agg")
    d = r["pass_k_detail"]
    assert d["coverage"] == pytest.approx(0.2)
    table = format_table(r)
    assert "覆盖率 20.0%" in table
    assert "保守下界 20.0%" in table
    assert "缺失 1·无效 1·未决 1·降级 1" in table
    assert r["capabilities"]["persist"]["pass_k"]["n_probes"] == 1


# ---------- 延迟覆盖账本 ----------


def test_latency_summary_and_coverage(tmp_path):
    cdir = tmp_path / "cases" / "c1"
    cdir.mkdir(parents=True)
    dialogue = {
        "type": "dialogue",
        "phase": "probe",
        "payload": {
            "replies": [
                {"text": "a", "latency_ms": 100},
                {"text": "b", "latency_ms": 300},
                {"text": "[RUNTIME_ERROR] down", "latency_ms": 0},   # 占位错误
                {"text": "instant"},                                  # 缺字段
                {"text": "c", "latency_ms": 9000},
            ]
        },
    }
    (cdir / "evidence.jsonl").write_text(json.dumps(dialogue) + "\n", encoding="utf-8")
    lat = latency_summary(tmp_path)
    # 分布只收计时样本（文档=实现）；旧 5 键形状不变（下游兼容）
    assert lat == {"n": 3, "p50_ms": 300, "p95_ms": 8130, "max_ms": 9000, "mean_ms": 3133}
    cov = latency_coverage(tmp_path)
    assert cov == {
        "n_replies": 5,
        "n_timed": 3,
        "n_zero_ms": 1,
        "n_zero_ms_placeholder": 1,
        "n_missing_latency_ms": 1,
        "coverage": 0.6,
    }
    assert latency_coverage(tmp_path / "absent") is None


def test_render_report_shows_sandbox_and_latency_coverage(tmp_path):
    from memhall.report.report import render_report

    run_dir = _write_run(tmp_path, "20261015-010101-hermes", round_index=0)
    cdir = run_dir / "cases" / "persist-s"
    (cdir / "evidence.jsonl").write_text(
        json.dumps({
            "type": "dialogue", "phase": "probe",
            "payload": {"replies": [
                {"text": "a", "latency_ms": 4200},
                {"text": "[RUNTIME_ERROR] x", "latency_ms": 0},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    case = _mini_case(with_diagnostic=False, n_score=5)
    verdicts = []
    from memhall.schema.evidence import Verdict

    for i in range(1, 6):
        verdicts.append(Verdict.model_validate({
            "verdict_id": f"v{i}", "probe_id": f"persist-s-p{i}", "case_id": "persist-s",
            "run_id": manifest["run_id"], "verdict": "correct" if i <= 4 else "omission",
            "decided_by": "rule", "confidence": 1.0, "explanation": "",
        }))
    metrics = compute_metrics(verdicts, {"persist-s": case})
    metrics["latency"] = latency_summary(run_dir)
    report = render_report(run_dir, manifest["run_id"], manifest, verdicts,
                           {"persist-s": case}, metrics)
    assert "沙箱口径：**开启**" in report
    assert "采样覆盖率 50%" in report and "占位错误 1" in report

    manifest.pop("environment")
    manifest.pop("token_usage")  # 有模型流量但没账单：未记账≠0 元
    report2 = render_report(run_dir, manifest["run_id"], manifest, verdicts,
                            {"persist-s": case}, metrics)
    assert "沙箱口径：**未封存**" in report2
    assert "Token 消耗：**未记账**" in report2


# ---------- 实验索引：分组 / 不串轮 / 新统计 ----------


def _write_index(tmp: Path, name: str, index: dict) -> Path:
    path = tmp / name
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return path


def _load_stats_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "stats_uncertainty", Path(__file__).parent.parent / "scripts" / "stats_uncertainty.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _matrix(tmp: Path, suite_id: str = "memhall-challenge-v2-test", rounds: int = 2) -> Path:
    root = tmp / RUNS_ROOT_MARKER
    for agent in ("hermes", "kylin-agent"):
        for r in range(rounds):
            _write_run(
                root,
                f"20261015-0{r}{r}{r}0{r}{r}-{agent}",
                round_index=r % 2,
                adapter=agent,
                agent_version=f"{agent} v1.0",
                suite_id=suite_id,
            )
    return root


def _index_entries(root: Path) -> list[dict]:
    entries = []
    for d in sorted(root.iterdir()):
        if d.is_dir():
            manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            entries.append({"run_id": d.name, "agent": manifest["adapter"],
                            "round": int(d.name[9])})
    return entries


def test_index_stats_end_to_end(tmp_path, capsys, monkeypatch):
    root = _matrix(tmp_path)
    index = _write_index(tmp_path, "final.json", {
        "schema": "memhall.experiment-index/1",
        "experiment_id": "final-matrix",
        "purpose": "formal",
        "suite": "challenge-v2",
        "runs": _index_entries(root),
    })
    module = _load_stats_module()
    monkeypatch.setattr(sys, "argv", ["stats_uncertainty.py", str(root), "--index", str(index)])
    rc = module.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "⓪ 兼容性与分组" in out
    assert "sandbox=on" in out
    assert "hermes: ✓ 已验证" in out and "kylin-agent: ✓ 已验证" in out
    assert "⑥ 正式统计" in out
    assert "hermes: 总分" in out and "kylin-agent: 总分" in out
    assert "pass^2 条件" in out and "覆盖率" in out and "保守下界" in out
    assert "有效率（各轮）" in out
    assert "不自动等于显著排名" in out


def test_index_rejects_historical_and_exploratory_runs(tmp_path, capsys, monkeypatch):
    module = _load_stats_module()
    root = tmp_path / RUNS_ROOT_MARKER
    # 历史 run_id 冒充新矩阵轮次
    _write_run(root, "20261010-073953-hermes", round_index=0, suite_id="memhall-full-v1")
    index = _write_index(tmp_path, "bad-history.json", {
        "schema": "memhall.experiment-index/1", "purpose": "formal", "suite": "full",
        "runs": [{"run_id": "20261010-073953-hermes", "agent": "hermes", "round": 1}],
    })
    monkeypatch.setattr(sys, "argv", ["s", str(root), "--index", str(index)])
    assert module.main() == 2
    assert "历史实验轮次" in capsys.readouterr().out

    # exploratory 轮混进 formal 索引
    _write_run(root, "20261015-010101-hermes", round_index=0,
               suite_id="memhall-full-v1", exploratory=True)
    index2 = _write_index(tmp_path, "bad-exploratory.json", {
        "schema": "memhall.experiment-index/1", "purpose": "formal", "suite": "full",
        "runs": [{"run_id": "20261015-010101-hermes", "agent": "hermes", "round": 1}],
    })
    monkeypatch.setattr(sys, "argv", ["s", str(root), "--index", str(index2)])
    assert module.main() == 2
    assert "exploratory" in capsys.readouterr().out


def test_index_rejects_dev_suite_and_mixed_arms(tmp_path):
    module = _load_stats_module()
    # 开发题包不能进 formal
    with pytest.raises(ValueError, match="不能进正式口径"):
        module.load_experiment_index(_write_index(tmp_path, "dev.json", {
            "schema": "memhall.experiment-index/1", "purpose": "formal", "suite": "gen",
            "runs": [{"run_id": "x", "agent": "hermes", "round": 1}],
        }))
    # 一个索引混两个沙箱臂 → 拒绝（sandbox=0 对照必须另建索引）
    root = tmp_path / RUNS_ROOT_MARKER
    _write_run(root, "20261015-010101-hermes", round_index=0, suite_id="memhall-full-v1")
    _write_run(root, "20261015-020202-hermes", round_index=1, suite_id="memhall-full-v1",
               sandbox="off")
    index = _write_index(tmp_path, "mixed-arm.json", {
        "schema": "memhall.experiment-index/1", "purpose": "formal", "suite": "full",
        "runs": [
            {"run_id": "20261015-010101-hermes", "agent": "hermes", "round": 1},
            {"run_id": "20261015-020202-hermes", "agent": "hermes", "round": 2},
        ],
    })
    data = module.load_experiment_index(index)
    with pytest.raises(ValueError, match="混臂"):
        module.resolve_index(data, root)
    # 声明题包与归档 suite_id 对不上 → 拒绝
    index2 = _write_index(tmp_path, "wrong-suite.json", {
        "schema": "memhall.experiment-index/1", "purpose": "formal", "suite": "challenge-v2",
        "runs": [
            {"run_id": "20261015-010101-hermes", "agent": "hermes", "round": 1},
            {"run_id": "20261015-030303-hermes", "agent": "hermes", "round": 2},
        ],
    })
    _write_run(root, "20261015-030303-hermes", round_index=0, suite_id="memhall-full-v1")
    data2 = module.load_experiment_index(index2)
    with pytest.raises(ValueError, match="suite_id"):
        module.resolve_index(data2, root)


def test_historical_mode_output_unchanged(tmp_path, capsys, monkeypatch):
    """历史 full/chains 调用仍可复算：无索引时不跑 ⓪/⑥。"""
    module = _load_stats_module()
    monkeypatch.setattr(sys, "argv", ["stats_uncertainty.py", str(tmp_path / "runs")])
    assert module.main() == 2  # 缺 run 即退出（与旧行为一致：先报缺后退出）
    assert "缺 run" in capsys.readouterr().out
