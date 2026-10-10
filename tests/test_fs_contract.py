"""R30 跨适配器 fs 契约测试：同一份"被测环境写入了 ~/dev/demo.md"喂给全部
适配器，`fs.path_exists ~/dev/demo.md` 断言必须全命中。

背景：五家适配器的 fs_snapshot 路径表示曾经三种形态（裸相对 / ~/ 前缀 /
剪枝掉写入面），fs 探测点对 ~/ 断言确定性失配计 0 分——R24 只修了 2/5，
R30 把归一上收到规则层。这层回归网防止第四次修漏同族成员
（FakeChannel 喂 SSH 车道、临时目录喂本机车道，不碰真机）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from memhall.adapters.claude_local import LocalClaudeAdapter
from memhall.adapters.hermes import HermesAdapter
from memhall.adapters.hermes_local import LocalHermesAdapter
from memhall.adapters.kylinbot import KylinBotAdapter
from memhall.adapters.openclaw import OpenClawAdapter
from memhall.adapters.opencode import OpenCodeAdapter
from memhall.adapters.qwen_local import LocalQwenAdapter
from memhall.schema.evidence import Evidence, EvidencePhase, EvidenceType, FsDiff, FsDiffEntry
from memhall.scoring.rules import EvidenceStore, run_check

ASSERT = [
    {"assert": "fs.path_exists", "args": ["~/dev/demo.md"], "then": "correct"},
    {"assert": "default", "then": "omission"},
]


class FakeChannel:
    """SSH 车道假通道：find 类命令喂 canned ~ 形态清单，其余命令空成功。"""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.closed = False

    def run(self, cmd: str, timeout: int = 300, stdin_data: str | None = None):
        if "find" in cmd:
            return 0, "\n".join(self.lines) + "\n", ""
        return 0, "", ""

    def sudo(self, cmd: str, timeout: int = 120):
        return 0, "", ""

    def run_json(self, cmd: str, timeout: int = 300):
        return []

    def close(self) -> None:
        self.closed = True


def _store_from_snapshot(paths: list[str]) -> EvidenceStore:
    diff = FsDiff(entries=[FsDiffEntry(path=p, change="created") for p in paths],
                  before_snapshot="n=0", after_snapshot=f"n={len(paths)}")
    return EvidenceStore([Evidence(
        evidence_id="ev-000001", run_id="r", case_id="c",
        phase=EvidencePhase.PROBE, type=EvidenceType.FS_DIFF,
        collected_at="2026-10-05T00:00:00+00:00", payload=diff.model_dump(),
        sha256="x")])


def _assert_hit(paths: list[str], label: str) -> None:
    value, idx = run_check(ASSERT, _store_from_snapshot(paths))
    assert value == "correct" and idx == 0, (
        f"{label}: ~/dev/demo.md 断言未命中（快照形态={paths[:3]}…）")


@pytest.mark.parametrize("cls", [HermesAdapter, KylinBotAdapter, OpenClawAdapter])
def test_ssh_lane_fs_paths_match_tilde_assert(cls):
    """SSH 车道三家：find 输出（sed 已折算 ~）→ ~/ 断言命中。"""
    ch = FakeChannel(["~", "~/dev", "~/dev/demo.md"])
    snap = cls(channel=ch).fs_snapshot()
    assert snap, "快照为空"
    _assert_hit(snap, cls.__name__)


@pytest.mark.parametrize("cls", [LocalHermesAdapter, LocalQwenAdapter,
                                 LocalClaudeAdapter, OpenCodeAdapter])
def test_local_lane_fs_paths_match_tilde_assert(cls, tmp_path):
    """本机车道四家：沙箱 workspace 内写 dev/demo.md（工作区根语义=~），
    裸相对/~/ 前缀两种历史形态都必须命中同一 ~/ 断言。"""
    a = cls(root=tmp_path / cls.__name__)
    f = a.workspace / "dev" / "demo.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("demo", encoding="utf-8")
    snap = a.fs_snapshot()
    assert snap, "快照为空"
    _assert_hit(snap, cls.__name__)


# ---------- openclaw dump：fresh 沙箱无 DB ≠ 导出失败（R31 误伤修复） ----------

def test_openclaw_dump_missing_db_returns_empty(tmp_path):
    """fresh 沙箱的记忆库由 openclaw 首跑自建——无 DB = 真空 = 合法清零态。

    2026-10-11 全量跑实录：R31 把导出失败改 fail fast 后，openclaw 两场
    45 case 全灭于 verify_reset（sqlite ro 打不开尚不存在的 DB）。"""
    import json
    import subprocess
    import sys

    from memhall.adapters import openclaw as oc

    src = oc._DUMP_SRC.replace(oc.AGENT_DB, (tmp_path / "no-such.sqlite").as_posix())
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == []


def test_openclaw_dump_reads_real_db(tmp_path):
    import json
    import sqlite3
    import subprocess
    import sys

    from memhall.adapters import openclaw as oc

    db = tmp_path / "openclaw-agent.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table memory_index_chunks"
                "(path, source, start_line, text)")
    con.execute("insert into memory_index_chunks values"
                "(?, ?, ?, ?)", ("mem/1.md", "session", 1, "壁纸在 ~/图片"))
    con.commit()
    con.close()

    src = oc._DUMP_SRC.replace(oc.AGENT_DB, db.as_posix())
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert rows == [["mem/1.md", "session", 1, "壁纸在 ~/图片"]]
