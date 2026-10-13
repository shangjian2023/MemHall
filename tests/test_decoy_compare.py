"""P1 对账项测试：诱饵件拒绝率 + compare CLI 产物。"""

from pathlib import Path

from memhall.cli import load_case_set, load_cases
from memhall.report.compare import compare_runs
from memhall.schema.models_case import JudgeProbe
from memhall.scoring.decoy import run_decoy_test

REPO = Path(__file__).resolve().parents[1]


def test_decoy_rejection_rate():
    """全库诱饵拒绝率 ≥95%（design.md §6.1）。未达标=判卷器宽松，是真 bug。"""
    cases = load_cases(REPO / "cases/full")
    probes = [p for c in cases for p in c.probes if isinstance(p, JudgeProbe)]
    rep = run_decoy_test(probes)
    assert rep["n_decoys"] > 30
    for r in rep["results"]:
        assert not r.accepted, f"诱饵被误接受: {r.probe_id} {r.kind} {r.answer!r}"
    assert rep["rejection_rate"] == 1.0


def test_decoy_mutate_breaks_substring():
    from memhall.scoring.decoy import _mutate
    e = "~/work/src"
    m = _mutate(e)
    assert m != e and e not in m


def test_compare_runs(tmp_path: Path):
    """mock 跑两轮同套用例 → compare 出雷达+md，零翻转；改一条 verdict 后翻转=1。"""
    import json

    from memhall.adapters.mock import MockAdapter
    from memhall.cli import _finish_run
    from memhall.runner.orchestrator import run_suite
    from memhall.scoring.engine import evaluate_case

    cases = load_case_set("cases/quick")
    case_map = {c.case_id: c for c in cases}
    made = []
    for i in range(2):  # 各自独立目录：快机上同秒 run_id 相同会互相覆盖
        out = tmp_path / f"runs{i}"
        adapter = MockAdapter()
        run_id, stores = run_suite(adapter, cases, out, "mock")
        manifest = json.loads((out / run_id / "manifest.json").read_text(encoding="utf-8"))
        verdicts = [v for c, s in zip(cases, stores, strict=True)
                    for v in evaluate_case(c, s, run_id, None)]
        _finish_run(out / run_id, run_id, manifest, verdicts, case_map)
        made.append(out / run_id)

    res = compare_runs(made[0], made[1], tmp_path / "_compare")
    assert res["n_common"] > 0 and res["n_flips"] == 0
    assert Path(res["radar"]).exists() and Path(res["report"]).exists()

    # 人为翻转一条判定，diff 应抓到
    vp = made[1] / "verdicts.jsonl"
    lines = [json.loads(ln) for ln in vp.read_text(encoding="utf-8").splitlines()]
    lines[0]["verdict"] = "fabrication"
    vp.write_text("\n".join(json.dumps(ln, ensure_ascii=False) for ln in lines),
                  encoding="utf-8")
    from memhall.schema.evidence import Verdict
    manifest = json.loads((made[1] / "manifest.json").read_text(encoding="utf-8"))
    _finish_run(made[1], manifest["run_id"], manifest,
                [Verdict.model_validate(v) for v in lines], case_map)
    res2 = compare_runs(made[0], made[1], tmp_path / "_compare2")
    assert res2["n_flips"] == 1
