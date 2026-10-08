"""R54：heldout 产物脱敏——对外分享 run 目录前洗掉不可见池的题目文本。

泄漏面（review-tasks R54）：runner 把 case 全文快照进 runs/<id>/cases/<cid>/case.yaml，
对话证据 evidence.jsonl 里逐字含题目与被测者回答（含期望值）——公开 runs 即公开
不可见池。本脚本把 heldout 用例（ID 以 -hNN 结尾）的敏感字段哈希化：
题目文本、期望值、verdict_map 键（即标准答案话术）、对话双方文本。
结构 / ID / 判定 / 指标不动——审计可对账（哈希可复算），题目不外泄。

用法:
  uv run python scripts/redact_heldout.py runs/<run_id>              # 产出 runs/<run_id>-redacted/
  uv run python scripts/redact_heldout.py runs/<run_id> --in-place  # 原地洗（打包分享前）

非 heldout 用例原样保留；--in-place 前自动备份被改文件为 *.bak-redact。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

_HELDOUT = re.compile(r"-h\d{2}$")


def _redact(text: str) -> str:
    """文本 → [REDACTED:sha12]，哈希可对账、内容不外泄。"""
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"[REDACTED:{h}]"


def _redact_case_yaml(path: Path, in_place: bool) -> None:
    """case.yaml 是 yaml 文本——按行态替换值太脆，走 pydantic 重序列化。"""
    import yaml

    from memhall.schema.models_case import Anchor, JudgeProbe, MemoryCase, Step

    case = MemoryCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for phase in case.phases:
        for step in phase.steps:  # type: ignore[union-attr]
            if isinstance(step, Step):
                if step.user is not None:
                    step.user = _redact(step.user)
                if step.task is not None:
                    step.task = _redact(step.task)
    for probe in case.probes:
        if isinstance(probe, JudgeProbe):
            probe.ask = _redact(probe.ask)
            probe.expect = _redact(probe.expect)
            probe.rubric = _redact(probe.rubric)
            probe.verdict_map = {_redact(k): v for k, v in probe.verdict_map.items()}
            probe.anchors = [Anchor(reply=_redact(a.reply),
                                     expect_verdict=_redact(a.expect_verdict))
                             for a in probe.anchors]
    if in_place:
        _backup(path)
    path.write_text(yaml.safe_dump(case.model_dump(mode="json"),
                                   allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def _redact_evidence(path: Path, in_place: bool) -> None:
    """evidence.jsonl：heldout 用例的对话/记忆快照文本哈希化（时间戳/哈希链不动）。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        p = e.get("payload", {})
        if e.get("type") == "dialogue":
            p["messages"] = [_redact(m) for m in p.get("messages", [])]
            for r in p.get("replies", []):
                r["text"] = _redact(r.get("text", ""))
        elif e.get("type") == "memory_snapshot":
            for ent in p.get("entries", []):
                ent["content"] = _redact(ent.get("content", ""))
        out.append(json.dumps(e, ensure_ascii=False))
    if in_place:
        _backup(path)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".bak-redact")
    if not bak.exists():
        shutil.copy2(path, bak)


def redact_run(run_dir: Path, in_place: bool = False) -> Path:
    """洗一个 run 目录，返回产出目录（--in-place 时即原目录）。"""
    cases_dir = run_dir / "cases"
    if not cases_dir.is_dir():
        sys.exit(f"不是 run 目录（缺 cases/）: {run_dir}")
    out = run_dir if in_place else run_dir.with_name(run_dir.name + "-redacted")
    if not in_place:
        if out.exists():
            sys.exit(f"已存在 {out}，先删再跑")
        shutil.copytree(run_dir, out)
    n_h = 0
    for cdir in sorted(out.joinpath("cases").iterdir()):
        if not _HELDOUT.search(cdir.name):
            continue
        n_h += 1
        yaml_p = cdir / "case.yaml"
        if yaml_p.exists():
            _redact_case_yaml(yaml_p, in_place)
        ev_p = cdir / "evidence.jsonl"
        if ev_p.exists():
            _redact_evidence(ev_p, in_place)
    print(f"{'in-place' if in_place else 'copy'} 模式：heldout 用例 {n_h} 个已脱敏 → {out}")
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    in_place = "--in-place" in sys.argv[2:]
    redact_run(Path(sys.argv[1]), in_place=in_place)
    return 0


if __name__ == "__main__":
    sys.exit(main())
