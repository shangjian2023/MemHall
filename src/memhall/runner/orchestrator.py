"""三阶段剧本编排器（D 主线，正式 runner 实现）。

职责：按 MemoryCase 剧本驱动适配器、按契约 03 采集证据、落盘 JSONL + manifest。
不评判——判定全部在 scoring/engine.py。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import yaml

from memhall import __version__
from memhall.adapters.base import NO_WINDOW, AdapterError, AgentAdapter
from memhall.cost import summarize, usage_delta, usage_snapshot
from memhall.schema.evidence import (
    Evidence,
    EvidencePhase,
    EvidenceType,
    FsDiff,
    FsDiffEntry,
    Reply,
)
from memhall.schema.models_case import MemoryCase
from memhall.scoring.rules import EvidenceStore

log = logging.getLogger(__name__)


def _utc() -> datetime:
    return datetime.now(UTC)


def _sha256(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _phase_enum(name: str) -> EvidencePhase:
    return EvidencePhase(name)


class CaseRunner:
    """单用例执行器：剧本 → 适配器调用 → 证据采集落盘。"""

    def __init__(self, adapter: AgentAdapter, case: MemoryCase, run_id: str,
                 evidence_dir: Path, on_event=None):
        self.adapter = adapter
        self.case = case
        self.run_id = run_id
        self.evidence_dir = evidence_dir
        self.on_event = on_event
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
        # 用例快照先行：run 目录自包含，report/换机重渲染不依赖源码树用例库
        # （heldout 题目文本不入仓库，快照是它唯一的持久载体）
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        (self.evidence_dir / "case.yaml").write_text(
            yaml.safe_dump(self.case.model_dump(mode="json"),
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        self.adapter.reset()
        # R02/R23：reset 彻底性防线——残留记忆会把上一个 case 的答案带进来
        # （同问异答题库里是定向毒药），宁可本 case 中止也不静默污染。
        # getattr 防御：测试/第三方 duck-typed 适配器可能未继承基类
        verify = getattr(self.adapter, "verify_reset", None)
        if verify is not None:
            verify()
        base_fs = self.adapter.fs_snapshot()
        session_id = "s-01"
        try:
            for phase in self.case.phases:
                se = phase.system_events
                # R60：死旋钮 fail fast——reboot/network_off/rollback 当前编排器
                # 未实现，填 true 静默忽略会让用例拿到"没发生事件的正常分"
                if se is not None and any(
                        getattr(se, k, False) for k in
                        ("reboot", "network_off", "rollback")):
                    raise AdapterError(
                        f"system_events.reboot/network_off/rollback=true 未实现"
                        f"（case {self.case.case_id}），fail fast 防静默忽略")
                if se is not None and se.clock_shift_days:
                    try:
                        self.adapter.clock_shift(se.clock_shift_days)
                    except (AdapterError, RuntimeError) as e:
                        # 拨钟不支持/失败（如 Windows 本机适配器）→ 本 case 运行
                        # 无效，不能让一个 case 的环境限制打崩整套。R33：错误写进
                        # 证据（[RUNTIME_ERROR] 回复通道）——判卷层据此标
                        # INVALID_RUN，此前无对话证据会静默降级成 omission
                        log.warning("%s 拨钟失败（case 运行无效）: %s",
                                    self.case.case_id, e)
                        self._runtime_error = str(e)
                        self._collect(phase.name, EvidenceType.DIALOGUE, {
                            "messages": [],
                            "replies": [Reply(
                                session_id=session_id,
                                text=f"[RUNTIME_ERROR] {e}",
                                sent_at=_utc(), reply_at=_utc(),
                                latency_ms=0).model_dump(mode="json")]})
                        break
                    log.info("%s 拨钟 %+d 天（累计 %+d）", self.case.case_id,
                             se.clock_shift_days, self.clock_offset + se.clock_shift_days)
                    self.clock_offset += se.clock_shift_days
                messages: list[str] = []
                replies: list[Reply] = []
                for step in phase.steps:
                    text = step.user if step.user is not None else step.task
                    if text is None:
                        raise ValueError(f"用例 {self.case.case_id} 阶段 {phase.name} "
                                         "存在既无 user 也无 task 的步骤")
                    log.debug("%s 阶段 %s 问: %s", self.case.case_id, phase.name,
                              text[:60])
                    messages.append(text)
                    _safe_emit(self.on_event, {"type": "ask",
                                               "case": self.case.case_id,
                                               "phase": phase.name, "q": text})
                    try:
                        reply = self.adapter.send(session_id, text)
                        replies.append(reply)
                        _safe_emit(self.on_event, {"type": "reply",
                                                   "case": self.case.case_id,
                                                   "phase": phase.name,
                                                   "a": reply.text,
                                                   "ms": reply.latency_ms})
                    except AdapterError as e:
                        # 契约 01：适配器不可用 -> 后续步骤无意义，case 标运行无效
                        log.error("%s 阶段 %s 适配器错误: %s", self.case.case_id,
                                  phase.name, e)
                        replies.append(Reply(
                            session_id=session_id,
                            text=f"[RUNTIME_ERROR] {e}",
                            sent_at=_utc(), reply_at=_utc(), latency_ms=0))
                        self._runtime_error = str(e)
                        _safe_emit(self.on_event, {"type": "err",
                                                   "case": self.case.case_id,
                                                   "msg": str(e)})
                        break
                # 部分对话也落盘：[RUNTIME_ERROR] 回复标记进证据，判卷层据此
                # 标 INVALID_RUN——适配器中途挂掉不能静默降级成 omission
                self._collect(phase.name, EvidenceType.DIALOGUE,
                              {"messages": messages,
                               "replies": [r.model_dump(mode="json") for r in replies]})
                if getattr(self, "_runtime_error", None):
                    break
                # inject 后加采记忆快照（写入时机测试的数据源）
                if phase.name == "inject":
                    snap = self.adapter.dump_memory()
                    self._collect("inject", EvidenceType.MEMORY_SNAPSHOT,
                                  snap.model_dump(mode="json"))
                    _safe_emit(self.on_event, {"type": "memory",
                                               "case": self.case.case_id,
                                               "n": len(snap.entries)})
                    # R50：inject 后文件面也采——fs 证据按阶段切分，
                    # 链题"第 1 会话产物"与"复用行为产物"不再混在一锅
                    inject_fs = self.adapter.fs_snapshot()
                if phase.end_session:
                    self.adapter.end_session(session_id)
                    n = int(session_id.split("-")[1]) + 1
                    session_id = f"s-{n:02d}"
            # probe 结束后全量采集
            snap = self.adapter.dump_memory()
            self._collect("probe", EvidenceType.MEMORY_SNAPSHOT, snap.model_dump(mode="json"))
            _safe_emit(self.on_event, {"type": "memory",
                                       "case": self.case.case_id,
                                       "n": len(snap.entries),
                                       "final": True})
            dump = self.adapter.dump_actions()
            self._collect("probe", EvidenceType.ACTIONS, dump.model_dump(mode="json"))
        finally:
            # R33：时钟恢复失败必须暴露——残留 +N 天会污染后续所有 case 的
            # 时间语义且无人知晓；记入 manifest clock_restore_failed
            try:
                self.adapter.clock_restore()
            except (AdapterError, RuntimeError) as e:
                self._clock_restore_error = str(e)
                log.error("%s clock_restore 失败（记 manifest）: %s",
                          self.case.case_id, e)
        # 文件系统 diff（适配器支持时）：before 快照 vs after 快照
        after_fs = self.adapter.fs_snapshot()
        if base_fs is not None and after_fs is not None:
            # R50：fs 证据按阶段窗切分——base→inject 后（stage=inject）与
            # inject 后→终采（stage=probe）。链题"第 1 会话教出来的产物"与
            # "复用记忆时的行为产物"可分归因；断言层不筛 stage（旧证据 ""
            # 兼容）。注：适配器快照只有路径清单，modified/内容哈希需适配器
            # 提供内容指纹后才能落地（已知局限，见 review-tasks R50 状态）
            inject_fs = locals().get("inject_fs")
            mid = inject_fs if inject_fs is not None else base_fs
            entries: list[FsDiffEntry] = []
            entries += [FsDiffEntry(path=p, change="created", stage="inject")
                        for p in sorted(set(mid) - set(base_fs))]
            entries += [FsDiffEntry(path=p, change="deleted", stage="inject")
                        for p in sorted(set(base_fs) - set(mid))]
            entries += [FsDiffEntry(path=p, change="created", stage="probe")
                        for p in sorted(set(after_fs) - set(mid))]
            entries += [FsDiffEntry(path=p, change="deleted", stage="probe")
                        for p in sorted(set(mid) - set(after_fs))]
            fs_diff = FsDiff(entries=entries,
                             before_snapshot=f"n={len(base_fs)}",
                             after_snapshot=f"n={len(after_fs)}")
            self._collect("probe", EvidenceType.FS_DIFF, fs_diff.model_dump(mode="json"))
        self._flush()
        log.debug("%s 证据落盘 %d 条", self.case.case_id, len(self.store.items()))
        return self.store

    def _flush(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / "evidence.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for ev in self.store.items():
                f.write(ev.model_dump_json() + "\n")


def _git_hash() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
            creationflags=NO_WINDOW,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _safe_emit(on_event, payload: dict) -> None:
    """事件流给 UI 看过程用——它坏掉不能打崩评测。"""
    if on_event is None:
        return
    with contextlib.suppress(Exception):
        on_event(payload)


class _GlobalRunLock:
    """R57：跨进程评测互斥锁（CLI 与 UI 同跑时，一方 reset 的 rmtree 会
    拆掉另一方的沙箱）。OS 级文件锁（Windows msvcrt / POSIX fcntl），
    进程退出自动释放，无陈锁问题。"""

    def __init__(self) -> None:
        self._f: IO[str] | None = None

    def _acquire(self) -> None:
        f = self._f
        assert f is not None
        # sys.platform 比较让 mypy 静态收窄平台分支（win 上 fcntl 分支不可达）
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self) -> None:
        f = self._f
        assert f is not None
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> _GlobalRunLock:
        path = Path.home() / ".memhall" / "run.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a+")
        try:
            self._acquire()
        except OSError as e:
            self._f.close()
            self._f = None
            raise RuntimeError(
                "已有评测进程在跑（~/.memhall/run.lock 被占用）——"
                "CLI 与 UI 不能同时开跑，等它结束或停掉它") from e
        return self

    def __exit__(self, *exc) -> None:
        if self._f is not None:
            with contextlib.suppress(OSError):
                self._release()
            self._f.close()
            self._f = None


def run_suite(adapter: AgentAdapter, cases: list[MemoryCase], out_dir: Path,
              adapter_name: str, case_source: str = "",
              on_case_done=None, on_event=None) -> tuple[str, list[EvidenceStore]]:
    """跑整套用例，落盘 manifest，返回 (run_id, 每 case 的证据视图)。

    on_case_done(case_id, i, n)：每用例跑完后回调（UI 进度流用）；
        回调抛异常即中止（配合 UI 的停止按钮，已完成的用例证据已落盘）。
    on_event(ev)：逐条过程事件（ask/reply/memory/err/case_start），
        供 UI 直播问答过程；回调异常被吞，不影响评测。
    """
    run_id = _utc().strftime("%Y%m%d-%H%M%S") + f"-{adapter_name}"
    # 快机同秒跑两轮（mock 单轮 2 秒级）会互相覆盖，冲突时加序号后缀
    base_dir = out_dir / run_id
    run_dir, k = base_dir, 2
    while run_dir.exists():
        run_dir = out_dir / f"{run_id}-{k}"
        k += 1
    run_id = run_dir.name
    log.info("评测开始: %s × %d 用例 × %d 探测点 → %s", adapter_name, len(cases),
             sum(len(c.probes) for c in cases), run_dir)
    stores: list[EvidenceStore] = []
    failed: list[str] = []
    clock_restore_failed: list[str] = []
    usage_before = usage_snapshot()
    with _GlobalRunLock():   # R57：CLI/UI 跨进程互斥
        try:
            for i, case in enumerate(cases):
                log.info("[%d/%d] %s 开跑", i + 1, len(cases), case.case_id)
                t0 = time.monotonic()
                _safe_emit(on_event, {"type": "case_start", "case": case.case_id,
                                      "i": i + 1, "n": len(cases)})
                runner = CaseRunner(adapter, case, run_id,
                                    run_dir / "cases" / case.case_id,
                                    on_event=on_event)
                # R23：单 case 未预期异常不再引爆整套马拉松——记录失败续跑，
                # 已完成用例的证据/manifest 照常落盘（40 题挂 1 题不报废整轮）
                try:
                    stores.append(runner.run())
                except Exception:  # noqa: BLE001 隔离层必须兜住一切
                    log.exception("[%d/%d] %s 异常中止（记入 failed_cases，续跑）",
                                  i + 1, len(cases), case.case_id)
                    failed.append(case.case_id)
                else:
                    log.info("[%d/%d] %s 完成（%.1fs）", i + 1, len(cases),
                             case.case_id, time.monotonic() - t0)
                    if on_case_done is not None:
                        on_case_done(case.case_id, i + 1, len(cases))
                # R33：clock_restore 失败的 case 单列进 manifest（时钟残留警报）
                if getattr(runner, "_clock_restore_error", None):
                    clock_restore_failed.append(case.case_id)
        finally:
            _write_manifest(run_dir, run_id, adapter_name, case_source, cases,
                            failed, usage_before, adapter,
                            clock_restore_failed=clock_restore_failed)
            # R57：SshChannel 等底层资源统一收口——此前全链路无人调 close，靠 GC
            # getattr 防御 duck-typed 适配器（契约建议继承基类，不强求）
            close = getattr(adapter, "close", None)
            if callable(close):
                close()
    log.info("评测完成: run_id=%s", run_id)
    return run_id, stores


def pair_stores(cases: list[MemoryCase],
                stores: list[EvidenceStore]) -> list[tuple[MemoryCase, EvidenceStore]]:
    """stores 与 cases 按证据内 case_id 配对（failed_cases 无 store，跳过）。"""
    by_id: dict[str, EvidenceStore] = {}
    for s in stores:
        items = s.items()
        if items:
            by_id.setdefault(items[0].case_id, s)
    return [(c, st) for c in cases if (st := by_id.get(c.case_id)) is not None]


def _write_manifest(run_dir: Path, run_id: str, adapter_name: str,
                    case_source: str, cases: list[MemoryCase], failed: list[str],
                    usage_before, adapter: AgentAdapter,
                    clock_restore_failed: list[str] | None = None) -> None:
    manifest = {
        "run_id": run_id,
        "tool": "memhall",
        "tool_version": __version__,
        "python": platform.python_version(),
        "schema_version": "0.1",
        "adapter": adapter_name,
        "case_source": case_source,
        "git_hash": _git_hash(),
        "started_at": run_id[:15],
        "finished_at": _utc().isoformat(),
        "cases": [c.case_id for c in cases],
        "n_probes_total": sum(len(c.probes) for c in cases),
    }
    if failed:
        manifest["failed_cases"] = failed
    if clock_restore_failed:
        # R33：时钟残留警报——这些 case 之后系统时间可能仍 +N 天，
        # 后续 run 的时间语义（temporal 族）需对照本字段核查
        manifest["clock_restore_failed"] = clock_restore_failed
    # 网关记账差值（直连模式/无记账文件时为 None，不落键）
    token_usage = summarize(usage_delta(usage_before, usage_snapshot()))
    if token_usage:
        manifest["token_usage"] = token_usage
        log.info("本轮网关记账: %d 请求 / %d tokens",
                 token_usage["requests"], token_usage["total_tokens"])
    # 被测智能体版本（报告可复现性元数据；探测失败静默跳过）
    try:
        version = adapter.version_info()
    except Exception:  # noqa: BLE001 元数据探测不阻塞评测
        version = None
    if version:
        manifest["agent_version"] = version
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
