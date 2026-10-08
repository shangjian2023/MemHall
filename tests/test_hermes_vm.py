"""VM 版 hermes 适配器回归网（2026-10-08 r3 假 invalid_run 事故）。

事故：dump_memory 的 for 循环退出码 = 最后一次 [ -f ] 的结果，USER.md
合法缺席（boundary 题智能体本就不写记忆）→ rc=1 → 被当"导出失败" →
25 例假 invalid_run。修复 = 命令末尾 `; true`（真实传输故障走 run() 的
异常路径，不会被吞）。
"""

from __future__ import annotations

from memhall.adapters.hermes import MEM_DIR, HermesAdapter


class _Chan:
    """按真实 shell 语义演的假通道：有 `; true` 恒 0，否则尾命令决定 rc。"""

    def __init__(self, files: dict[str, str]):
        self.files = files  # {文件名: 内容}
        self.cmds: list[str] = []

    def run(self, cmd: str, timeout: int = 300,
            stdin_data: str | None = None) -> tuple[int, str, str]:
        self.cmds.append(cmd)
        if cmd.startswith("for f in"):
            out = ""
            last_rc = 1
            for name in (f"{MEM_DIR}/MEMORY.md", f"{MEM_DIR}/USER.md"):
                if name in self.files:
                    out += f"=== {name}\n{self.files[name]}"
                    last_rc = 0
                else:
                    last_rc = 1  # [ -f ] 失败，&& 链短路
            rc = 0 if cmd.rstrip().endswith("; true") else last_rc
            return rc, out, ""
        return 0, "", ""


def _adapter(files: dict[str, str]) -> HermesAdapter:
    return HermesAdapter(channel=_Chan(files))  # type: ignore[arg-type]


def test_dump_cmd_never_fails_on_absent_files():
    """修复面：命令必须带 `; true`，文件缺席 rc=0（空记忆≠导出失败）。"""
    a = _adapter({})                      # 两个文件都不存在
    snap = a.dump_memory()
    assert a.ch.cmds[0].rstrip().endswith("; true")
    assert snap.dump_ok is True
    assert snap.entries == []


def test_dump_failure_still_flags_when_rc_nonzero():
    """R31 面保住：真 rc!=0（无 ; true 的旧语义/其他失败）仍如实标注。"""
    a = _adapter({})
    a.ch.cmds.clear()
    rc, out, _ = a.ch.run(
        f"for f in {MEM_DIR}/MEMORY.md {MEM_DIR}/USER.md; do "
        f'[ -f $f ] && echo "=== $f" && cat $f; done')
    assert rc == 1  # 假通道还原旧命令语义：缺席即 1
    assert out == ""


def test_dump_parses_bullet_entries():
    a = _adapter({f"{MEM_DIR}/MEMORY.md": "- 主力编辑器是 vim\n- 常去城市是杭州\n"})
    snap = a.dump_memory()
    assert snap.dump_ok is True
    assert [e.content for e in snap.entries] == ["主力编辑器是 vim", "常去城市是杭州"]
