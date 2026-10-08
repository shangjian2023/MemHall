"""R54：heldout 产物脱敏脚本的行为测试（合成 run 目录，不依赖真跑）。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from redact_heldout import redact_run  # noqa: E402

_CASE = """schema_version: "0.1"
case_id: persist-h01
capability: persist
question_type: cross_session_recall
content_type: path
difficulty: 1
meta:
  author: t
  created: "2026-10-08"
  source: generated
phases:
- name: inject
  steps:
  - user: 我的构建目录是 ~/build-area-77
- name: probe
  steps:
  - user: 你记的我的构建目录是哪个？
probes:
- kind: judge
  id: persist-h01-p1
  after: probe
  role: score
  ask: 用户记的构建目录是哪个
  expect: ~/build-area-77
  rubric: 精确路径才算对
  verdict_map:
    ~/build-area-77: correct
    忘了: omission
  anchors:
  - reply: 在 ~/build-area-77
    expect_verdict: ~/build-area-77
"""


def _ev_dialogue() -> dict:
    return {"evidence_id": "ev-000001", "run_id": "r", "case_id": "persist-h01",
            "phase": "inject", "type": "dialogue",
            "collected_at": "2026-10-08T00:00:00+00:00", "clock_offset_days": 0,
            "payload": {"messages": ["我的构建目录是 ~/build-area-77"],
                        "replies": [{"session_id": "s-01",
                                     "text": "已记住 ~/build-area-77",
                                     "sent_at": "x", "reply_at": "x",
                                     "latency_ms": 1}]},
            "sha256": "abc"}


def _ev_memory() -> dict:
    return {"evidence_id": "ev-000002", "run_id": "r", "case_id": "persist-h01",
            "phase": "probe", "type": "memory_snapshot",
            "collected_at": "2026-10-08T00:01:00+00:00", "clock_offset_days": 0,
            "payload": {"format": "files", "dumped_at": "x",
                        "entries": [{"entry_id": "m-0001",
                                     "content": "构建目录 ~/build-area-77",
                                     "created_at": None,
                                     "source_turn": "unknown"}],
                        "raw": None},
            "sha256": "def"}


@pytest.fixture()
def fake_run(tmp_path: Path) -> Path:
    run = tmp_path / "20261008-fake"
    h = run / "cases" / "persist-h01"
    f = run / "cases" / "persist-001"
    h.mkdir(parents=True)
    f.mkdir(parents=True)
    (h / "case.yaml").write_text(_CASE, encoding="utf-8")
    (h / "evidence.jsonl").write_text(
        json.dumps(_ev_dialogue(), ensure_ascii=False) + "\n"
        + json.dumps(_ev_memory(), ensure_ascii=False) + "\n", encoding="utf-8")
    (f / "case.yaml").write_text(_CASE.replace("persist-h01", "persist-001"),
                                 encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return run


def _all_text(p: Path, exclude_bak: bool = False) -> str:
    return "".join(f.read_text(encoding="utf-8") for f in p.rglob("*")
                   if f.is_file()
                   and not (exclude_bak and f.name.endswith(".bak-redact")))


def _leaks(p: Path, exclude_bak: bool = False) -> bool:
    return "build-area-77" in _all_text(p, exclude_bak=exclude_bak)


def test_redact_copy_mode(fake_run: Path):
    out = redact_run(fake_run, in_place=False)
    assert out.name == "20261008-fake-redacted"
    # 脱敏副本：密值全消失；非 heldout 原样；原件未动
    assert not _leaks(out / "cases" / "persist-h01")
    assert "build-area-77" in (out / "cases" / "persist-001" / "case.yaml"
                               ).read_text(encoding="utf-8")
    assert _leaks(fake_run / "cases" / "persist-h01")


def test_redact_in_place_makes_backup(fake_run: Path):
    redact_run(fake_run, in_place=True)
    # .bak-redact 备份留底（含密值，打包分享前须删）——审计口径排除备份
    assert not _leaks(fake_run / "cases" / "persist-h01", exclude_bak=True)
    bak = fake_run / "cases" / "persist-h01" / "case.yaml.bak-redact"
    assert "build-area-77" in bak.read_text(encoding="utf-8")
    # 脱敏后仍是合法 jsonl（审计可对账）
    for line in (fake_run / "cases" / "persist-h01" / "evidence.jsonl"
                 ).read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_redact_hash_is_verifiable(fake_run: Path):
    out = redact_run(fake_run, in_place=False)
    text = (out / "cases" / "persist-h01" / "case.yaml").read_text(encoding="utf-8")
    expect_hash = hashlib.sha1(b"~/build-area-77").hexdigest()[:12]
    assert f"[REDACTED:{expect_hash}]" in text   # 哈希可复算对账
