"""gen_cases 三难度旋钮（design §4.4）：可调 + 默认参数不漂移已发布存档。"""

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "gen_cases.py"


def _gen(tmp: Path, *extra: str) -> Path:
    out = tmp / "out"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), *extra],
        check=True, capture_output=True, cwd=REPO)
    return out


def _strip_created(p: Path) -> str:
    """归一化 meta.created 行——R60 起生成器记实际生成日期（诚实元数据），
    存档与重生成只允许这一行随日期漂移，其余仍须逐字一致。"""
    import re
    return re.sub(r"^(\s*created: )'.*'$", r"\1<DATE>",
                  p.read_text(encoding="utf-8"), flags=re.M)


def test_default_reproduces_committed_archive(tmp_path):
    """默认参数下与已发布 cases/gen 一致（除 meta.created 日期戳外逐字，
    存档不因生成器升级漂移）。"""
    out = _gen(tmp_path, "--seed", "20260928")
    for f in (REPO / "cases" / "gen").glob("*.yaml"):
        assert (_strip_created(f) == _strip_created(out / f.name)), \
            f"漂移: {f.name}"


def test_distract_knob(tmp_path):
    out = _gen(tmp_path, "--seed", "1", "--distract", "4",
               "--counts", "persist:1")
    c = yaml.safe_load((out / "persist-g01.yaml").read_text(encoding="utf-8"))
    cf = [p for p in c["phases"] if p["name"] == "confound"][0]
    assert len(cf["steps"]) == 4


def test_gap_days_knob(tmp_path):
    out = _gen(tmp_path, "--seed", "1", "--gap-days", "14",
               "--counts", "temporal:1")
    c = yaml.safe_load((out / "temporal-g01.yaml").read_text(encoding="utf-8"))
    cf = [p for p in c["phases"] if p["name"] == "confound"][0]
    assert cf["system_events"]["clock_shift_days"] == 14


def test_similar_knob_widens_decoy(tmp_path):
    def decoy(level: str) -> str:
        out = _gen(tmp_path / level, "--seed", "7", "--similar", level,
                   "--counts", "discriminate:1")
        c = yaml.safe_load(next(out.glob("*.yaml")).read_text(encoding="utf-8"))
        return c["phases"][0]["steps"][1]["user"].rsplit(" ", 1)[-1]

    high, low = decoy("high"), decoy("low")
    # high 档同父同 token：诱饵与真值同在 proj/work
    assert high.startswith("~/proj/") or high.startswith("~/work/")
    # low 档诱饵换到 opt/srv 父目录；真值父目录固定为 proj/work
    assert low.startswith("~/opt/") or low.startswith("~/srv/")
    # 真值父目录固定为 proj/work，low 档诱饵必不在其中
    assert not (low.startswith("~/proj/") or low.startswith("~/work/"))
