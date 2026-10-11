"""三阶段剧本编排器（D 主线，正式 runner 实现）。

职责：按 MemoryCase 剧本驱动适配器、按契约 03 采集证据、落盘 JSONL + manifest。
不评判——判定全部在 scoring/engine.py。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from memhall.adapters.base import AgentAdapter
from memhall.schema.evidence import (
    ActionDump,
    Evidence,
    EvidencePhase,
    EvidenceType,
    FsDiff,
    FsDiffEntry,
    MemorySnapshot,
    Reply,
)
from memhall.schema.models_case import MemoryCase
from memhall.scoring.rules import EvidenceStore


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _phase_enum(name: str) -> EvidencePhase:
    return EvidencePhase(name)


class CaseRunner:
    """单用例执行器：剧本 → 适配器调用 → 证据采集落盘。"""

    def __init__(self, adapter: AgentAdapter, case: MemoryCase, run_id: str,
                 evidence_dir: Path):
        self.adapter = adapter
        self.case = case
        self.run_id = run_id
        self.evidence_dir = evidence_dir
        self.store = EvidenceStore()
        self._seq = 0
        self.clock_offset = 0

    def _collect(self, phase: str, etype: EvidenceType, payload: dict) -> None:
        self._seq += 1
        ev = Evidence(
            evidence_id=f"ev-{self._seq:06d}",
            run_id=self.run_id,
            case_id=self.case.case_id,
            phase=_phase_enum(phase),
            type=etype,
            collected_at=_utc(),
            clock_offset_days=self.clock_offset,
            payload=payload,
            sha256=_sha256(payload),
        )
        self.store.add(ev)

    def run(self) -> EvidenceStore:
        self._runtime_error: str | None = None
        base_fs: list[str] | None = None
        session_id = "s-01"
        try:
            try:
                self.adapter.reset()
                base_fs = self.adapter.fs_snapshot()
            except Exception as e:  # adapter contract: any external failure invalidates the case
                self._runtime_error = str(e)
            for phase in self.case.phases:
                if self._runtime_error:
                    break
                se = phase.system_events
                try:
                    if se is not None and se.clock_shift_days:
                        self.adapter.clock_shift(se.clock_shift_days)
                        self.clock_offset += se.clock_shift_days
                except Exception as e:
                    self._runtime_error = str(e)
                    break
                messages: list[str] = []
                replies: list[Reply] = []
                for step in phase.steps:
                    text = step.user if step.user is not None else step.task
                    assert text is not None
                    messages.append(text)
                    try:
                        replies.append(self.adapter.send(session_id, text))
                    except Exception as e:
                        # 契约 01：适配器不可用 -> 后续步骤无意义，case 标运行无效
                        replies.append(Reply(
                            session_id=session_id,
                            text=f"[RUNTIME_ERROR] {e}",
                            sent_at=_utc(), reply_at=_utc(), latency_ms=0))
                        self._runtime_error = str(e)
                        break
                self._collect(phase.name, EvidenceType.DIALOGUE,
                              {"messages": messages,
                               "replies": [r.model_dump(mode="json") for r in replies]})
                if self._runtime_error:
                    break
                # inject 后加采记忆快照（写入时机测试的数据源）
                if phase.name == "inject":
                    try:
                        snap = self.adapter.dump_memory()
                        self._collect("inject", EvidenceType.MEMORY_SNAPSHOT,
                                      snap.model_dump(mode="json"))
                    except Exception as e:
                        self._runtime_error = str(e)
                        break
                if phase.end_session:
                    try:
                        self.adapter.end_session(session_id)
                    except Exception as e:
                        self._runtime_error = str(e)
                        break
                    n = int(session_id.split("-")[1]) + 1
                    session_id = f"s-{n:02d}"
            # probe 结束后全量采集
            if not self._runtime_error:
                try:
                    snap = self.adapter.dump_memory()
                    self._collect("probe", EvidenceType.MEMORY_SNAPSHOT,
                                  snap.model_dump(mode="json"))
                    dump = self.adapter.dump_actions()
                    self._collect("probe", EvidenceType.ACTIONS,
                                  dump.model_dump(mode="json"))
                except Exception as e:
                    self._runtime_error = str(e)
        finally:
            try:
                self.adapter.clock_restore()
            except Exception:
                self._runtime_error = self._runtime_error or "恢复系统时钟失败"
        if self._runtime_error and not self.store.runtime_error():
            self._collect("probe", EvidenceType.DIALOGUE, {
                "messages": [],
                "replies": [Reply(
                    session_id=session_id,
                    text=f"[RUNTIME_ERROR] {self._runtime_error}",
                    sent_at=_utc(), reply_at=_utc(), latency_ms=0,
                ).model_dump(mode="json")],
            })
        # 文件系统 diff（适配器支持时）：before 快照 vs after 快照
        try:
            after_fs = self.adapter.fs_snapshot()
        except Exception as e:
            after_fs = None
            self._runtime_error = self._runtime_error or str(e)
            if not self.store.runtime_error():
                self._collect("probe", EvidenceType.DIALOGUE, {
                    "messages": [],
                    "replies": [Reply(
                        session_id=session_id,
                        text=f"[RUNTIME_ERROR] {self._runtime_error}",
                        sent_at=_utc(), reply_at=_utc(), latency_ms=0,
                    ).model_dump(mode="json")],
                })
        if base_fs is not None and after_fs is not None:
            created = sorted(set(after_fs) - set(base_fs))
            deleted = sorted(set(base_fs) - set(after_fs))
            fs_diff = FsDiff(entries=[FsDiffEntry(path=p, change="created")
                                      for p in created]
                             + [FsDiffEntry(path=p, change="deleted") for p in deleted],
                             before_snapshot=f"n={len(base_fs)}",
                             after_snapshot=f"n={len(after_fs)}")
            self._collect("probe", EvidenceType.FS_DIFF, fs_diff.model_dump(mode="json"))
        self._flush()
        return self.store

    def _flush(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / "evidence.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for ev in self.store._items:
                f.write(ev.model_dump_json() + "\n")


def _git_hash() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def run_suite(adapter: AgentAdapter, cases: list[MemoryCase], out_dir: Path,
              adapter_name: str, selection_meta: dict | None = None
              ) -> tuple[str, list[EvidenceStore]]:
    """跑整套用例，落盘 manifest，返回 (run_id, 每 case 的证据视图)。"""
    started_at = _utc()
    run_id = started_at.strftime("%Y%m%d-%H%M%S-%f") + f"-{adapter_name}"
    run_dir = out_dir / run_id
    suffix = 2
    while run_dir.exists():
        run_id = _utc().strftime("%Y%m%d-%H%M%S-%f") + f"-{adapter_name}-{suffix}"
        run_dir = out_dir / run_id
        suffix += 1
    stores: list[EvidenceStore] = []
    try:
        for case in cases:
            runner = CaseRunner(adapter, case, run_id, run_dir / "cases" / case.case_id)
            stores.append(runner.run())
    finally:
        try:
            adapter.close()
        except Exception:
            # 评测结果已经落盘；资源释放失败不能覆盖 case 证据。
            pass
    manifest = {
        "run_id": run_id,
        "tool": "memhall",
        "schema_version": "0.1",
        "adapter": adapter_name,
        "git_hash": _git_hash(),
        "started_at": started_at.isoformat(),
        "finished_at": _utc().isoformat(),
        "cases": [c.case_id for c in cases],
        "n_probes_total": sum(len(c.probes) for c in cases),
    }
    if selection_meta is not None:
        manifest["case_selection"] = selection_meta
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_id, stores
