"""报告进阶图第二梯队测试：延迟/记忆存量/耗时/跨轮矩阵/用例排序。

核心断言不是"图存在"而是价值闸门双向成立：
- 有数据的图画出来（mock：记忆存量、用例排序、第二轮的跨轮矩阵）；
- 无价值的不画（mock 瞬时回复 → 延迟/耗时跳过；单轮 → 无跨轮矩阵）。
"""

from __future__ import annotations

import json
from pathlib import Path

from memhall.adapters.mock import MockAdapter
from memhall.cli import _finish_run, load_cases
from memhall.report.panels import render_panels
from memhall.runner.orchestrator import run_suite
from memhall.scoring.engine import evaluate_case

REPO = Path(__file__).parent.parent


def _quick_cases() -> list:
    from memhall.paths import QUICK_IDS

    by_id = {c.case_id: c for c in load_cases(REPO / "cases" / "full")}
    return [by_id[i] for i in QUICK_IDS if i in by_id]


def _mock_run(root: Path, cases) -> Path:
    run_id, stores = run_suite(MockAdapter(), cases, root, "mock")
    run_dir = root / run_id
    verdicts = [v for case, store in zip(cases, stores, strict=True)
                for v in evaluate_case(case, store, run_id)]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    _finish_run(run_dir, run_id, manifest, verdicts,
                {c.case_id: c for c in cases})
    return run_dir


def test_panels_value_gates_single_run(tmp_path: Path):
    run_dir = _mock_run(tmp_path, _quick_cases())
    # 有数据：构成条（既有）+ 记忆存量（mock 教完即存）+ 用例排序（6 例计分）
    assert (run_dir / "report-verdict-mix.png").is_file()
    assert (run_dir / "report-memory.png").is_file()
    assert (run_dir / "report-case-scores.png").is_file()
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "![记忆库存量变化](report-memory.png)" in report
    assert "![用例正确率排序](report-case-scores.png)" in report
    # 价值闸门：mock 回复恒 ~1ms → 延迟/耗时不画；单轮 → 无跨轮矩阵
    assert not (run_dir / "report-latency.png").exists()
    assert not (run_dir / "report-durations.png").exists()
    assert not (run_dir / "report-probe-history.png").exists()


def test_probe_history_appears_from_second_run(tmp_path: Path):
    cases = _quick_cases()
    first = _mock_run(tmp_path, cases)
    assert not (first / "report-probe-history.png").exists()
    second = _mock_run(tmp_path, cases)
    # 同适配器第二轮：跨轮点阵生成（mock 设计缺陷保证存在非全对探测点）
    assert (second / "report-probe-history.png").is_file()
    assert "![探测点跨轮稳定性](report-probe-history.png)" in (
        second / "report.md").read_text(encoding="utf-8")
    # 旧 run 不回头补图（重渲染才补）——按需 memhall report
    assert not (first / "report-probe-history.png").exists()


def test_render_panels_survives_empty_run_dir(tmp_path: Path):
    assert render_panels(tmp_path, [], {}) == []
