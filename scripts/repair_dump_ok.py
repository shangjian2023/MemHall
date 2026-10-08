"""R3 修复重放：把 dump rc=1 陷阱造成的假 dump_ok=False 修回真实态。

事故（2026-10-08 r3，runs/20261008-044029-hermes）：hermes dump_memory
的 for 循环退出码 = 最后一次 [ -f ] 的结果——MEMORY.md/USER.md 合法缺席
（boundary 题被测智能体本就不写记忆）被记成"导出失败"（dump_ok=False，
error="记忆导出失败: SSH cat 失败 rc=1"）→ 引擎按 R31 判 25 例假
invalid_run。适配器已修（循环末尾 `; true`，commit 0284b1f），本脚本把
证据修回真实语义：文件缺席 = 空记忆（dump_ok=True, entries 保持 []）。

只动一类行：memory_snapshot 且 error 命中 rc=1 模式。其余一概不碰
（真传输故障/其他 error 不在修复面）。payload 变更后按 orchestrator
._sha256 同款序列化（ensure_ascii=False, sort_keys=True, default=str）
重算证据哈希；原文件备份 *.bak-repair；manifest 写 repair 留痕。
verify_run 修复后应全绿（哈希链一致）。

用法：uv run python scripts/repair_dump_ok.py runs/<run_id>
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

_PATTERN = "SSH cat 失败 rc=1"


def _sha256(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(blob.encode("utf-8")).hexdigest()


def repair_run(run_dir: Path) -> int:
    cases_dir = run_dir / "cases"
    if not cases_dir.is_dir():
        sys.exit(f"不是 run 目录（缺 cases/）: {run_dir}")
    n_lines = 0
    n_cases = set()
    for cdir in sorted(cases_dir.iterdir()):
        ev = cdir / "evidence.jsonl"
        if not ev.is_file():
            continue
        out, touched = [], False
        for line in ev.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                out.append(line)
                continue
            e = json.loads(line)
            pay = e.get("payload") or {}
            if (e.get("type") == "memory_snapshot"
                    and pay.get("dump_ok") is False
                    and _PATTERN in str(pay.get("error", ""))):
                pay["dump_ok"] = True
                pay.pop("error", None)
                e["payload"] = pay
                e["sha256"] = _sha256(pay)
                touched = True
                n_lines += 1
                n_cases.add(cdir.name)
            out.append(json.dumps(e, ensure_ascii=False))
        if touched:
            shutil.copy2(ev, ev.with_suffix(".jsonl.bak-repair"))
            ev.write_text("\n".join(out) + "\n", encoding="utf-8")
    if n_lines:
        mfile = run_dir / "manifest.json"
        manifest = json.loads(mfile.read_text(encoding="utf-8"))
        manifest["repair"] = {
            "at": datetime.now(UTC).isoformat(),
            "what": "dump rc=1 陷阱假 dump_ok=False 修正为空记忆真实态",
            "fix_commit": "0284b1f（adapters/hermes.py dump 命令 `; true`）",
            "evidence_lines": n_lines,
            "cases": sorted(n_cases),
            "backup_suffix": ".jsonl.bak-repair",
        }
        mfile.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"修复 {n_lines} 条 memory_snapshot 证据（{len(n_cases)} 个用例）→ {run_dir}")
    return n_lines


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    repair_run(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
