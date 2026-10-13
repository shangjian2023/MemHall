"""三阶段剧本编排器（D 主线，正式 runner 实现）。

职责：按 MemoryCase 剧本驱动适配器、按契约 03 采集证据、落盘 JSONL + manifest。
不评判——判定全部在 scoring/engine.py。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

import yaml

from memhall import __version__
from memhall.adapters.base import NO_WINDOW, AdapterError, AgentAdapter
from memhall.cost import summarize, usage_delta, usage_snapshot
from memhall.sampling import FrozenPaper, run_identity
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

    def __init__(
        self, adapter: AgentAdapter, case: MemoryCase, run_id: str, evidence_dir: Path, on_event=None
    ):
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
            yaml.safe_dump(self.case.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.adapter.reset()
        # R02/R23：reset 彻底性防线——残留记忆会把上一个 case 的答案带进来
        # （同问异答题库里是定向毒药），宁可本 case 中止也不静默污染。
        # getattr 防御：测试/第三方 duck-typed 适配器可能未继承基类
        verify = getattr(self.adapter, "verify_reset", None)
        if verify is not None:
            verify()
        base_fs = self.adapter.fs_snapshot()
        hash_fn = getattr(self.adapter, "fs_fingerprints", lambda paths: None)
        base_hashes = hash_fn(base_fs) if base_fs is not None else None
        inject_hashes = None
        session_id = "s-01"
        try:
            for phase in self.case.phases:
                se = phase.system_events
                # R60：死旋钮 fail fast——reboot/network_off/rollback 当前编排器
                # 未实现，填 true 静默忽略会让用例拿到"没发生事件的正常分"
                if se is not None and any(
                    getattr(se, k, False) for k in ("reboot", "network_off", "rollback")
                ):
                    raise AdapterError(
                        f"system_events.reboot/network_off/rollback=true 未实现"
                        f"（case {self.case.case_id}），fail fast 防静默忽略"
                    )
                if se is not None and se.clock_shift_days:
                    try:
                        self.adapter.clock_shift(se.clock_shift_days)
                    except (AdapterError, RuntimeError) as e:
                        # 拨钟不支持/失败（如 Windows 本机适配器）→ 本 case 运行
                        # 无效，不能让一个 case 的环境限制打崩整套。R33：错误写进
                        # 证据（[RUNTIME_ERROR] 回复通道）——判卷层据此标
                        # INVALID_RUN，此前无对话证据会静默降级成 omission
                        log.warning("%s 拨钟失败（case 运行无效）: %s", self.case.case_id, e)
                        self._runtime_error = str(e)
                        self._collect(
                            phase.name,
                            EvidenceType.DIALOGUE,
                            {
                                "messages": [],
                                "replies": [
                                    Reply(
                                        session_id=session_id,
                                        text=f"[RUNTIME_ERROR] {e}",
                                        sent_at=_utc(),
                                        reply_at=_utc(),
                                        latency_ms=0,
                                    ).model_dump(mode="json")
                                ],
                            },
                        )
                        break
                    log.info(
                        "%s 拨钟 %+d 天（累计 %+d）",
                        self.case.case_id,
                        se.clock_shift_days,
                        self.clock_offset + se.clock_shift_days,
                    )
                    self.clock_offset += se.clock_shift_days
                    _safe_emit(
                        self.on_event,
                        {
                            "type": "clock",
                            "case": self.case.case_id,
                            "days": se.clock_shift_days,
                            "total": self.clock_offset,
                        },
                    )
                messages: list[str] = []
                replies: list[Reply] = []
                for step in phase.steps:
                    text = step.user if step.user is not None else step.task
                    if text is None:
                        raise ValueError(
                            f"用例 {self.case.case_id} 阶段 {phase.name} 存在既无 user 也无 task 的步骤"
                        )
                    log.debug("%s 阶段 %s 问: %s", self.case.case_id, phase.name, text[:60])
                    messages.append(text)
                    _safe_emit(
                        self.on_event,
                        {"type": "ask", "case": self.case.case_id, "phase": phase.name, "q": text},
                    )
                    try:
                        reply = self.adapter.send(session_id, text)
                        replies.append(reply)
                        _safe_emit(
                            self.on_event,
                            {
                                "type": "reply",
                                "case": self.case.case_id,
                                "phase": phase.name,
                                "a": reply.text,
                                "ms": reply.latency_ms,
                            },
                        )
                    except AdapterError as e:
                        # 契约 01：适配器不可用 -> 后续步骤无意义，case 标运行无效
                        log.error("%s 阶段 %s 适配器错误: %s", self.case.case_id, phase.name, e)
                        replies.append(
                            Reply(
                                session_id=session_id,
                                text=f"[RUNTIME_ERROR] {e}",
                                sent_at=_utc(),
                                reply_at=_utc(),
                                latency_ms=0,
                            )
                        )
                        self._runtime_error = str(e)
                        _safe_emit(self.on_event, {"type": "err", "case": self.case.case_id, "msg": str(e)})
                        break
                # 部分对话也落盘：[RUNTIME_ERROR] 回复标记进证据，判卷层据此
                # 标 INVALID_RUN——适配器中途挂掉不能静默降级成 omission
                self._collect(
                    phase.name,
                    EvidenceType.DIALOGUE,
                    {"messages": messages, "replies": [r.model_dump(mode="json") for r in replies]},
                )
                if getattr(self, "_runtime_error", None):
                    break
                # inject 后加采记忆快照（写入时机测试的数据源）
                if phase.name == "inject":
                    snap = self.adapter.dump_memory()
                    self._collect("inject", EvidenceType.MEMORY_SNAPSHOT, snap.model_dump(mode="json"))
                    _safe_emit(
                        self.on_event, {"type": "memory", "case": self.case.case_id, "n": len(snap.entries)}
                    )
                    # R50：inject 后文件面也采——fs 证据按阶段切分，
                    # 链题"第 1 会话产物"与"复用行为产物"不再混在一锅
                    inject_fs = self.adapter.fs_snapshot()
                    inject_hashes = hash_fn(inject_fs) if inject_fs is not None else None
                if phase.end_session:
                    self.adapter.end_session(session_id)
                    n = int(session_id.split("-")[1]) + 1
                    session_id = f"s-{n:02d}"
                    # 会话翻卷是跨会话保持的考察机制本身（10-09 用户点名要
                    # 在 UI 直播流里可见），静默滚号会让"考"看起来还在原会话
                    _safe_emit(
                        self.on_event,
                        {"type": "session", "case": self.case.case_id, "id": session_id, "after": phase.name},
                    )
            # probe 结束后全量采集
            snap = self.adapter.dump_memory()
            self._collect("probe", EvidenceType.MEMORY_SNAPSHOT, snap.model_dump(mode="json"))
            _safe_emit(
                self.on_event,
                {"type": "memory", "case": self.case.case_id, "n": len(snap.entries), "final": True},
            )
            dump = self.adapter.dump_actions()
            self._collect("probe", EvidenceType.ACTIONS, dump.model_dump(mode="json"))
        finally:
            # R33：时钟恢复失败必须暴露——残留 +N 天会污染后续所有 case 的
            # 时间语义且无人知晓；记入 manifest clock_restore_failed
            try:
                self.adapter.clock_restore()
            except (AdapterError, RuntimeError) as e:
                self._clock_restore_error = str(e)
                log.error("%s clock_restore 失败（记 manifest）: %s", self.case.case_id, e)
        # 文件系统 diff（适配器支持时）：before 快照 vs after 快照
        after_fs = self.adapter.fs_snapshot()
        after_hashes = hash_fn(after_fs) if after_fs is not None else None
        if base_fs is not None and after_fs is not None:
            # Keep injection artifacts and later task reuse in separate windows.
            inject_fs = locals().get("inject_fs")
            mid = inject_fs if inject_fs is not None else base_fs
            entries: list[FsDiffEntry] = []
            entries += [
                FsDiffEntry(path=p, change="created", stage="inject") for p in sorted(set(mid) - set(base_fs))
            ]
            entries += [
                FsDiffEntry(path=p, change="deleted", stage="inject") for p in sorted(set(base_fs) - set(mid))
            ]
            entries += [
                FsDiffEntry(path=p, change="created", stage="probe") for p in sorted(set(after_fs) - set(mid))
            ]
            entries += [
                FsDiffEntry(path=p, change="deleted", stage="probe") for p in sorted(set(mid) - set(after_fs))
            ]
            middle_hashes = inject_hashes if inject_hashes is not None else base_hashes
            for stage, before, after in [
                ("inject", base_hashes, middle_hashes),
                ("probe", middle_hashes, after_hashes),
            ]:
                if before is not None and after is not None:
                    for path in sorted(before.keys() & after.keys()):
                        if before[path] != after[path]:
                            entries.append(
                                FsDiffEntry(
                                    path=path,
                                    change="modified",
                                    stage=stage,
                                    before_sha256=before[path],
                                    after_sha256=after[path],
                                )
                            )
            for entry in entries:
                before = base_hashes if entry.stage == "inject" else middle_hashes
                after = middle_hashes if entry.stage == "inject" else after_hashes
                entry.before_sha256 = (before or {}).get(entry.path)
                entry.after_sha256 = (after or {}).get(entry.path)
            fs_diff = FsDiff(
                entries=entries,
                before_hashes=base_hashes,
                after_hashes=after_hashes,
                before_snapshot=f"n={len(base_fs)}",
                after_snapshot=f"n={len(after_fs)}",
            )
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
            capture_output=True,
            text=True,
            check=True,
            creationflags=NO_WINDOW,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _source_fingerprint() -> str:
    """源码树指纹：git 检出与 tgz 铺树都能算，同代码同指纹。
    VM 部署形态无 .git（git_hash 只能记 unknown），审计要能把
    "这轮跑的代码"钉到具体字节——对 memhall 包全部 .py 做内容哈希。"""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


def _safe_emit(on_event, payload: dict) -> None:
    """事件流给 UI 看过程用——它坏掉不能打崩评测。"""
    if on_event is None:
        return
    with contextlib.suppress(Exception):
        on_event(payload)


def _clock_drift_seconds(adapter: AgentAdapter) -> float | None:
    """宿主 vs 评测机系统钟差（秒，评测机-宿主）。

    A1 错钟残留检测的探针：本机/原生车道（评测器与被测者同一块钟）
    和读不到时间的场合返回 None（探测失败≠有漂移，不据此拦跑）。
    """
    from memhall.adapters.remote import SshChannel

    ch = getattr(adapter, "ch", None)
    if not isinstance(ch, SshChannel):
        return None
    rc, out, _ = ch.run("date +%s", timeout=15)
    if rc != 0 or not out.strip().isdigit():
        log.warning("错钟检测读不到评测机时间（rc=%s），跳过校验", rc)
        return None
    return int(out.strip()) - time.time()


def _clock_drift_max_s() -> float:
    raw = os.environ.get("MEMHALL_CLOCK_DRIFT_MAX", "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def _parse_iso(ts: str) -> datetime | None:
    """ISO 时间戳 → aware datetime（归一到可比时刻）。

    尾缀 Z/z 视作 UTC；无 offset 的按 UTC（全链路账单/证据都是 UTC 写入）；
    解析不了返回 None——调用方按明确失败处理，不猜。
    """
    s = ts.strip()
    if not s:
        return None
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


_COMPACT_STAMP = re.compile(r"^\d{8}-\d{6}")


def _parse_started_at(value: str) -> datetime | None:
    """run 起点（manifest.started_at / run_id）→ aware UTC datetime。

    两种合法形态：
    - aware ISO（2026-10 起新归档的 started_at）：任意 offset 归一到 UTC
      ——同一时刻的 +08:00 与 Z 写法必须解析相等，不做字符串前缀比较；
    - 紧凑戳 YYYYMMDD-HHMMSS（历史归档 started_at 与 run_id 前 15 位）：
      按 UTC 解释。run_id 自 MVP（1ac6397）起就是 _utc() 生成的 UTC 墙钟，
      从未用过本地钟——这是既有事实口径，不是本轮新解释；带 adapter /
      冲突序号后缀的完整 run_id 同样只取前 15 位。
    其余输入（空、过短、非数字、越界日期）→ None：明确失败，不猜。
    """
    s = value.strip()
    if not s:
        return None
    if "T" in s:
        return _parse_iso(s)
    if len(s) < 15 or not _COMPACT_STAMP.match(s):
        return None
    try:
        return datetime.strptime(s[:15], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _run_start_iso(started_at: str) -> str:
    """started_at → UTC ISO（网关账单 ts 同口径可比窗）；解析失败返回 ""。"""
    dt = _parse_started_at(started_at)
    return dt.astimezone(UTC).isoformat() if dt is not None else ""


def _agent_gateway_key(adapter_name: str) -> str:
    """本轮评测在网关账单里的 tag——与 gateway_settings 的 key 同源：
    自定义 GATEWAY_AGENT_KEY 优先，否则 memhall-<agent>。"""
    return os.environ.get("GATEWAY_AGENT_KEY", f"memhall-{adapter_name}")


def _reply_span(stores: list) -> dict[str, dict]:
    """逐 case 的真实回复统计：[RUNTIME_ERROR] 占位回复不算 agent 产出
    （占位意味着 send 没发出去，不该有对应上游调用）。

    {case_id: {"n": 真实回复数, "sent0": 首条真实发送时刻, "reply1": 末条
    回复时刻, "shifted": 本 case 是否拨过钟}}——A2 网关对账的本地侧账。
    时刻取对话证据里的 sent_at/reply_at（真正发问/收答的时刻），不再用
    证据落盘时刻近似——落盘晚于整段回复，是旧对账窗口虚宽的根源。
    """
    stats: dict[str, dict] = {}
    for store in stores:
        items = store.items()
        if not items:
            continue
        entry = stats.setdefault(
            items[0].case_id, {"n": 0, "sent0": None, "reply1": None, "shifted": False}
        )
        for ev in items:
            entry["shifted"] = entry["shifted"] or bool(ev.clock_offset_days)
            if ev.type.value != "dialogue":
                continue
            for r in (ev.payload or {}).get("replies") or []:
                if str(r.get("text", "")).startswith("[RUNTIME_ERROR]"):
                    continue
                entry["n"] += 1
                sent = _parse_iso(str(r.get("sent_at", "")))
                reply = _parse_iso(str(r.get("reply_at", "")))
                if sent is not None and (entry["sent0"] is None or sent < entry["sent0"]):
                    entry["sent0"] = sent
                if reply is not None and (entry["reply1"] is None or reply > entry["reply1"]):
                    entry["reply1"] = reply
    return stats


_ALARM_CLOCK_SLACK_S = 300.0  # 对账窗口两侧钟差余量，与发车错钟闸门阈值同源


def _gateway_records(adapter: AgentAdapter) -> list[dict] | None:
    """网关账单全部记录（宿主文件优先，评测机侧账单兜底）；读不到 → None。"""
    from memhall.gateway import default_log_path

    lines: list[str] | None = None
    host_log = default_log_path()
    if host_log.is_file():
        try:
            lines = host_log.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = None
    if lines is None:
        # 宿主无账单 → 试评测机侧网关的账单（VM 上跑网关的形态）
        ch = getattr(adapter, "ch", None)
        run = getattr(ch, "run", None)
        if callable(run):
            rc, out, _ = run("cat ~/.memhall/gateway-usage.jsonl 2>/dev/null", timeout=30)
            if rc == 0 and out.strip():
                lines = out.splitlines()
    if lines is None:
        return None
    records: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _gateway_bypass_alarm(
    adapter: AgentAdapter, stores: list, adapter_name: str, mode: str, clock_restore_failed: list[str]
) -> dict | None:
    """A2 网关对账（无账本游标的直调形态）：统一网关车道里 agent 正常作答、
    该 tag 却零记账 → 绕行告警。

    "必经网关"此前纯靠配置注入，没有"该来没来"的对账——agent 自带真 key
    直连上游时分数照常出、口径记录却在说谎。账单读不到（网关在别机/没起
    记账）= 无法对账，返回 None 不告警；触发才写 manifest。
    归因窗口用对话证据里真实的 sent_at/reply_at，两侧各放
    _ALARM_CLOCK_SLACK_S 收敛钟差（与发车错钟闸门同阈值），不再是从证据
    落盘时刻向前放宽 30 分钟的宽窗；时刻解析成 aware datetime 比较，同一
    时刻的不同 offset 写法（+08:00 vs Z）能正确相等。拨过钟的 case 时间
    语义不可信，跳过逐 case 归因（run_suite 生产路径已改账单追加序号对账，
    不受此限，见 _GatewayLedger）。
    """
    if mode != "gateway":
        return None
    stats = _reply_span(stores)
    n_replies = sum(e["n"] for e in stats.values())
    if n_replies == 0:
        return None
    records = _gateway_records(adapter)
    if records is None:
        return None
    tag = _agent_gateway_key(adapter_name)
    tag_ts = [
        ts
        for ts in (_parse_iso(str(rec.get("ts", ""))) for rec in records if rec.get("agent") == tag)
        if ts is not None
    ]
    suspects: list[str] = []
    if not clock_restore_failed:
        for cid, entry in stats.items():
            if entry["n"] == 0 or entry["shifted"] or entry["sent0"] is None:
                continue
            lo = entry["sent0"] - timedelta(seconds=_ALARM_CLOCK_SLACK_S)
            hi = (entry["reply1"] or entry["sent0"]) + timedelta(seconds=_ALARM_CLOCK_SLACK_S)
            if not any(lo <= ts <= hi for ts in tag_ts):
                suspects.append(cid)
    alarm = {"n_replies": n_replies, "n_tag_records": len(tag_ts)}
    if suspects:
        alarm["suspect_cases"] = suspects
        alarm["note"] = (
            "这些 case 有真实回复但网关零记账——疑似绕行网关/零上游调用，口径记录不可信，需人工核查"
        )
    elif not tag_ts:
        alarm["note"] = (
            "本轮有真实回复但整个账单无该智能体 tag 记录——疑似绕行网关，口径记录不可信，需人工核查"
        )
    else:
        return None
    if clock_restore_failed and not suspects:
        alarm["per_case"] = "skipped（本轮有拨钟恢复失败，逐 case 归因不可靠）"
    return alarm


_REMOTE_BILL = "~/.memhall/gateway-usage.jsonl"


class _GatewayLedger:
    """网关账单游标（A2 对账的因果锚点）。

    账单是逐请求追加的 JSONL，行序即因果序——与系统钟完全无关：拨钟、
    钟差、时区都不改变追加顺序。发车时记基线行数，逐 case 开跑前再记
    边界，case i 的上游流量 = 账单 [mark[i], mark[i+1]) 行里本 tag 的
    记录——上一题的调用永远落在下一题区间之外，新题零请求不可能被
    旧题记录掩盖。宿主文件优先、评测机侧账单兜底（网关跑在 VM 的形态）；
    来源在 open 时钉死，中途换源会让行号失去可比性。读不到账单 →
    open 返回 None，整轮对账记 unknown，绝不当作已验证。
    """

    def __init__(self, source: str, host_path: Path | None, channel) -> None:
        self.source = source  # "host" | "remote"（写进对账块，可审计）
        self.host_path = host_path
        self.channel = channel

    @classmethod
    def open(cls, adapter: AgentAdapter) -> _GatewayLedger | None:
        from memhall.gateway import default_log_path

        host_log = default_log_path()
        if host_log.is_file():
            return cls("host", host_log, None)
        run = getattr(getattr(adapter, "ch", None), "run", None)
        if callable(run):
            rc, out, _ = run(f"test -f {_REMOTE_BILL} && echo ok", timeout=15)
            if rc == 0 and out.strip():
                ch = getattr(adapter, "ch", None)
                return cls("remote", None, ch)
        return None

    def count(self) -> int | None:
        """账单当前总行数（含末尾未换行的半行，与 awk NR 同口径）；读不到 → None。"""
        if self.source == "host":
            path = self.host_path
            assert path is not None
            n, tail = 0, b""
            try:
                with path.open("rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        n += chunk.count(b"\n")
                        if chunk:
                            tail = chunk[-1:]
            except OSError:
                return None
            return n + (1 if tail and tail != b"\n" else 0)
        rc, out, _ = self.channel.run(
            f"awk 'END{{print NR}}' {_REMOTE_BILL} 2>/dev/null", timeout=15
        )
        if rc != 0 or not out.strip().isdigit():
            return None
        return int(out.strip())

    def records(self, start: int) -> list[dict] | None:
        """第 start 行（0 基）起的记录，按行序返回；读不到 → None。

        解析失败的行给 {} 占住行号（与 count 保持对齐），tag 过滤自然排除。
        """
        if self.source == "host":
            path = self.host_path
            assert path is not None
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return None
            lines = lines[start:]
        else:
            rc, out, _ = self.channel.run(
                f"tail -n +{start + 1} {_REMOTE_BILL} 2>/dev/null", timeout=30
            )
            if rc != 0:
                return None
            lines = out.splitlines()
        out_recs: list[dict] = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                rec = {}
            out_recs.append(rec if isinstance(rec, dict) else {})
        return out_recs


def _gateway_reconciliation(
    adapter_name: str,
    case_ids: list[str],
    stores: list,
    mode: str,
    ledger: _GatewayLedger | None,
    baseline: int | None,
    case_marks: list[int | None],
    final_count: int | None,
    clock_restore_failed: list[str],
) -> dict | None:
    """A2 网关对账（run_suite 生产路径）：账单追加序号把"本轮本题"与
    "实际发出的上游请求"连起来，三态结论，账单读不到绝不冒充已验证。

    - verified：每个有真实回复的 case，其账单区间内都有本 tag 追加记录；
    - suspect：有 case 零记录（疑似绕行网关/零上游调用，需人工核查）；
    - unknown：账单不可读，或行号失序（轮转/清空/中途失联）——无法对账。
    归因只看追加顺序不看 ts，拨钟 case（账单 ts 会跟着拨钟走）同样可
    归因；per_case 里仍标 shifted、clock_restore_failed 单列，限制显式
    可见。请求条数可以多于回复数（一次回复背后多次上游调用是常态），
    对账只要求"有回复 ⇒ 区间内有记录"，不做一回复一请求的等同。
    """
    if mode != "gateway":
        return None
    stats = _reply_span(stores)
    n_replies = sum(e["n"] for e in stats.values())
    if n_replies == 0:
        return None
    out: dict = {"method": "bill-append-sequence", "n_replies": n_replies}
    if ledger is None or baseline is None:
        out["status"] = "unknown"
        out["reason"] = "网关账单不可读（未记账/在别机且 SSH 读不到），无法对账——不视为已验证"
        return out
    marks = [m for m in case_marks if m is not None]  # 有 None 时下面整体转 unknown
    if final_count is None or len(marks) != len(case_marks) or final_count < max([baseline, *marks]):
        out["status"] = "unknown"
        out["reason"] = "账单行数中途不可读或回退（疑似轮转/清空），行号失序，无法对账"
        return out
    records = ledger.records(baseline)
    if records is None:
        out["status"] = "unknown"
        out["reason"] = "收尾读账单失败（SSH 断/文件消失），无法对账"
        return out
    tag = _agent_gateway_key(adapter_name)
    per_case: dict[str, dict] = {}
    suspects: list[str] = []
    n_tag = 0
    for i in range(len(marks)):
        cid = case_ids[i]
        lo = marks[i]
        hi = marks[i + 1] if i + 1 < len(marks) else final_count
        k = sum(
            1
            for pos, rec in enumerate(records)
            if lo <= baseline + pos < hi and rec.get("agent") == tag
        )
        n_tag += k
        entry = stats.get(cid)
        if entry is None:
            continue  # 异常中止无证据的 case 不入 per_case（对账对象是"有回复"的 case）
        per_case[cid] = {"records": k, "replies": entry["n"], "shifted": entry["shifted"]}
        if entry["n"] > 0 and k == 0:
            suspects.append(cid)
    out.update(
        tag=tag,
        ledger_source=ledger.source,
        baseline_records=baseline,
        end_records=final_count,
        records_this_run=n_tag,
        per_case=per_case,
    )
    if clock_restore_failed:
        out["clock_restore_failed"] = list(clock_restore_failed)
    if suspects:
        out["status"] = "suspect"
        out["suspect_cases"] = suspects
        out["note"] = (
            "这些 case 有真实回复但其账单区间内零记账——疑似绕行网关/零上游调用，"
            "口径记录不可信，需人工核查"
        )
    elif n_tag == 0:
        out["status"] = "suspect"
        out["note"] = (
            "本轮有真实回复但账单无该智能体 tag 的追加记录——疑似绕行网关，"
            "口径记录不可信，需人工核查"
        )
    else:
        out["status"] = "verified"
        out["note"] = (
            "有回复 case 的账单区间内均有本 tag 追加记录；归因按账单追加序号，"
            "与系统钟无关（拨钟 case 亦适用，见 per_case.shifted）"
        )
    return out


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

            f.seek(0)
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
                "已有评测进程在跑（~/.memhall/run.lock 被占用）——CLI 与 UI 不能同时开跑，等它结束或停掉它"
            ) from e
        return self

    def __exit__(self, *exc) -> None:
        if self._f is not None:
            with contextlib.suppress(OSError):
                self._release()
            self._f.close()
            self._f = None


def run_suite(
    adapter: AgentAdapter,
    cases: list[MemoryCase],
    out_dir: Path,
    adapter_name: str,
    case_source: str = "",
    on_case_done=None,
    on_event=None,
    model_override: str | None = None,
    rotate_count: int | None = None,
    rotation_seed: int | None = None,
    paper: FrozenPaper | None = None,
    suite_identity: dict | None = None,
) -> tuple[str, list[EvidenceStore]]:
    """跑整套用例，落盘 manifest，返回 (run_id, 每 case 的证据视图)。

    on_case_done(case_id, i, n)：每用例跑完后回调（UI 进度流用）；
        回调抛异常即中止（配合 UI 的停止按钮，已完成的用例证据已落盘）。
    on_event(ev)：逐条过程事件（ask/reply/memory/err/case_start），
        供 UI 直播问答过程；回调异常被吞，不影响评测。
    model_override：网关车道逐 run 模型改写（(agent, model) 二元组对照）。
        发车前写进网关改写表（按 memhall-<agent> tag 键控），收尾必清；
        直连/mock 车道忽略（不经网关无从改写），manifest 记 model_override
        供 compare 侧与官方口径区分。
    """
    if rotate_count is not None:
        if rotation_seed is None:
            raise ValueError("题目轮换必须提供 rotation_seed")
        if rotate_count <= 0 or rotate_count > len(cases):
            raise ValueError(f"rotate_count 必须在 1 到 {len(cases)} 之间")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("轮换题库包含重复 case_id")

    stores: list[EvidenceStore] = []
    if paper is not None and rotate_count is not None:
        raise ValueError("封存题单不能进行历史轮换")
    identity = run_identity(cases, paper=paper, suite=suite_identity, exploratory=rotate_count is not None)
    failed: list[str] = []
    clock_restore_failed: list[str] = []
    rotation_meta: dict | None = None
    usage_before = usage_snapshot()
    from memhall.gateway import model_backend

    backend = model_backend(model_override)
    with _GlobalRunLock():  # R57：CLI/UI 跨进程互斥
        # A1 错钟残留检测：上一轮拨钟恢复失败会让评测机时间整体偏移 N 天，
        # temporal 类用例的时间语义全错还无人知晓（R33 只记账不拦跑）。
        # 发车前宿主-评测机对时，超阈值拒跑。
        clock_drift = _clock_drift_seconds(adapter)
        if clock_drift is not None and abs(clock_drift) > _clock_drift_max_s():
            raise RuntimeError(
                f"宿主与评测机系统钟相差 {clock_drift:+.0f}s（阈值 "
                f"±{_clock_drift_max_s():.0f}s，可用 MEMHALL_CLOCK_DRIFT_MAX 调）"
                "——疑似上一轮拨钟恢复失败残留，先校时/重启评测机再发车"
            )
        # A2 账本游标：发车前钉账单基线行数——追加序号是与系统钟无关的
        # 因果锚点，本轮/本题的流量从此与旧记录可区分（网关模式才对账）
        ledger = _GatewayLedger.open(adapter) if backend.get("mode") == "gateway" else None
        baseline = ledger.count() if ledger is not None else None
        # 网关车道逐 run 模型改写：改写表是共享家目录里的全局状态，必须在
        # 拿到互斥锁、通过预检之后才写——锁未取得或预检拒绝时碰都不要碰
        # （更不能残留）；写入后用罩住全生命周期的 finally 恢复此前值
        # （此前的值可能是操作员手工配的，直接清掉等于毁配置）。tag 推导
        # 与 gateway_settings 同源（GATEWAY_AGENT_KEY 可自定义）。
        gw_tag = ""
        prev_override: str | None = None
        if model_override:
            from memhall.gateway import gateway_settings, model_overrides, set_model_override

            if gateway_settings(adapter_name):
                gw_tag = _agent_gateway_key(adapter_name)
                prev_override = model_overrides().get(gw_tag)
                set_model_override(gw_tag, model_override)
                log.info("模型改写: %s → %s（网关）", gw_tag, model_override)
        # run_id 在锁内分配（同秒两场不撞目录）；起点保存真实 UTC datetime，
        # 不再从 run_id 回推（紧凑戳曾被误当宿主本地钟，+8 时区下差 8 小时）
        run_started: datetime | None = None
        run_dir: Path | None = None
        run_id = ""
        case_marks: list[int | None] = []  # 逐 case 开跑前的账单行号边界
        try:
            run_started = _utc()
            run_id = run_started.strftime("%Y%m%d-%H%M%S") + f"-{adapter_name}"
            base_dir = out_dir / run_id
            run_dir, k = base_dir, 2
            while run_dir.exists():
                run_dir = out_dir / f"{run_id}-{k}"
                k += 1
            run_id = run_dir.name
            if rotate_count is not None:
                from memhall.runner.case_rotation import rotate_cases

                assert rotation_seed is not None
                rotation = rotate_cases(cases, count=rotate_count, seed=rotation_seed, out_dir=out_dir)
                cases = rotation.cases
                rotation_meta = rotation.manifest()
                identity = run_identity(cases, suite=suite_identity, exploratory=True)
            if paper is not None:
                paper.save(run_dir / "paper.json")
            log.info(
                "评测开始: %s × %d 用例 × %d 探测点 → %s",
                adapter_name,
                len(cases),
                sum(len(c.probes) for c in cases),
                run_dir,
            )
            # R01 封印：题库级快照先行落盘（轮换后的实际题目集）——归档自包含，
            # verify 据此校验阶段覆盖与题目是否被换（有则强校验）
            snapshot_items = [c.model_dump(mode="json") for c in cases]
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "cases.json").write_text(
                json.dumps({"cases": snapshot_items}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            for i, case in enumerate(cases):
                # 逐 case 开跑前记账单边界——case i 的上游流量随后落在
                # [mark[i], mark[i+1]) 行区间里，与时刻无关
                case_marks.append(ledger.count() if ledger is not None else None)
                log.info("[%d/%d] %s 开跑", i + 1, len(cases), case.case_id)
                t0 = time.monotonic()
                _safe_emit(
                    on_event, {"type": "case_start", "case": case.case_id, "i": i + 1, "n": len(cases)}
                )
                runner = CaseRunner(
                    adapter, case, run_id, run_dir / "cases" / case.case_id, on_event=on_event
                )
                # R23：单 case 未预期异常不再引爆整套马拉松——记录失败续跑，
                # 已完成用例的证据/manifest 照常落盘（40 题挂 1 题不报废整轮）
                try:
                    stores.append(runner.run())
                except Exception:  # noqa: BLE001 隔离层必须兜住一切
                    log.exception(
                        "[%d/%d] %s 异常中止（记入 failed_cases，续跑）", i + 1, len(cases), case.case_id
                    )
                    failed.append(case.case_id)
                else:
                    log.info(
                        "[%d/%d] %s 完成（%.1fs）", i + 1, len(cases), case.case_id, time.monotonic() - t0
                    )
                    if on_case_done is not None:
                        on_case_done(case.case_id, i + 1, len(cases))
                # R33：clock_restore 失败的 case 单列进 manifest（时钟残留警报）
                if getattr(runner, "_clock_restore_error", None):
                    clock_restore_failed.append(case.case_id)
        finally:
            # A2 网关对账：三态（verified/suspect/unknown）——账单读不到是
            # unknown，不等于"没问题"；对账自身异常也不能打崩 manifest 落盘
            gw_recon: dict | None = None
            try:
                final_count = ledger.count() if ledger is not None else None
                gw_recon = _gateway_reconciliation(
                    adapter_name,
                    [c.case_id for c in cases],
                    stores,
                    backend.get("mode", ""),
                    ledger,
                    baseline,
                    case_marks,
                    final_count,
                    clock_restore_failed,
                )
            except Exception:  # noqa: BLE001 对账是加强层，不是判分层
                log.exception("网关对账异常（跳过）")
            try:
                if run_dir is not None:
                    _write_manifest(
                        run_dir,
                        run_id,
                        adapter_name,
                        case_source,
                        cases,
                        failed,
                        usage_before,
                        adapter,
                        clock_restore_failed=clock_restore_failed,
                        model_override=model_override,
                        case_rotation=rotation_meta,
                        benchmark=identity,
                        clock_drift=clock_drift,
                        gateway_reconciliation=gw_recon,
                        run_started=run_started,
                        ledger=ledger,
                        baseline=baseline,
                    )
            finally:
                if gw_tag:  # 改写表必恢复到发车前的值——残留会把下一场 run 偷偷挂到旧模型上
                    from memhall.gateway import set_model_override

                    set_model_override(gw_tag, prev_override)
            # R57：SshChannel 等底层资源统一收口——此前全链路无人调 close，靠 GC
            # getattr 防御 duck-typed 适配器（契约建议继承基类，不强求）
            close = getattr(adapter, "close", None)
            if callable(close):
                close()
    log.info("评测完成: run_id=%s", run_id)
    return run_id, stores


def pair_stores(
    cases: list[MemoryCase], stores: list[EvidenceStore]
) -> list[tuple[MemoryCase, EvidenceStore]]:
    """stores 与 cases 按证据内 case_id 配对（failed_cases 无 store，跳过）。"""
    by_id: dict[str, EvidenceStore] = {}
    for s in stores:
        items = s.items()
        if items:
            by_id.setdefault(items[0].case_id, s)
    return [(c, st) for c in cases if (st := by_id.get(c.case_id)) is not None]


def _seal_case_archive(run_dir: Path, manifest: dict, cases: list[MemoryCase]) -> None:
    """R01 封印：题库指纹 + 逐题证据哈希 + 整包哈希，与 verify_run 的
    复算口径逐字段对齐（cases.json 有则强校验）。失败的 case 记 failed
    条目——缺证据的轮必须在 verify 面前亮红灯，而不是混进通过名单。"""
    snapshot_path = run_dir / "cases.json"
    if not snapshot_path.is_file():
        return
    manifest["cases_snapshot_sha256"] = _file_sha256(snapshot_path)
    snapshot_items = json.loads(snapshot_path.read_text(encoding="utf-8")).get("cases", [])
    case_hashes = {
        str(item.get("case_id")): _sha256(item) for item in snapshot_items if isinstance(item, dict)
    }
    manifest["case_hashes"] = case_hashes
    manifest["cases_version"] = f"sha256:{_sha256(case_hashes)}"

    file_hashes: dict[str, str | None] = {}
    case_results: list[dict] = []
    for case in cases:
        cid = case.case_id
        evidence_path = run_dir / "cases" / cid / "evidence.jsonl"
        if evidence_path.is_file():
            entry: dict = {
                "case_id": cid,
                "evidence_sha256": _file_sha256(evidence_path),
                "evidence_count": sum(
                    1 for line in evidence_path.read_text(encoding="utf-8").splitlines() if line.strip()
                ),
            }
            file_hashes[cid] = entry["evidence_sha256"]
            (run_dir / "cases" / cid / "case.json").write_text(
                json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            entry = {"case_id": cid, "failed": True}
            file_hashes[cid] = None
            (run_dir / "cases" / cid / "case.json").write_text(
                json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        case_results.append(entry)
    manifest["case_results"] = case_results
    manifest["evidence_bundle_sha256"] = _sha256(file_hashes)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(
    run_dir: Path,
    run_id: str,
    adapter_name: str,
    case_source: str,
    cases: list[MemoryCase],
    failed: list[str],
    usage_before,
    adapter: AgentAdapter,
    clock_restore_failed: list[str] | None = None,
    model_override: str | None = None,
    case_rotation: dict | None = None,
    benchmark: dict | None = None,
    clock_drift: float | None = None,
    gateway_reconciliation: dict | None = None,
    run_started: datetime | None = None,
    ledger: _GatewayLedger | None = None,
    baseline: int | None = None,
) -> None:
    manifest = {
        "run_id": run_id,
        "tool": "memhall",
        "tool_version": __version__,
        "python": platform.python_version(),
        "schema_version": "0.1",
        "adapter": adapter_name,
        "case_source": case_source,
        "git_hash": _git_hash(),
        "source_fingerprint": _source_fingerprint(),
        # 发车时刻保存为 aware UTC ISO（自带时区，读者无需猜口径）；
        # 历史归档的紧凑戳仍是 UTC 墙钟，解析见 _parse_started_at
        "started_at": (run_started or _utc()).isoformat(),
        "finished_at": _utc().isoformat(),
        "cases": [c.case_id for c in cases],
        "n_probes_total": sum(len(c.probes) for c in cases),
    }
    manifest["benchmark"] = benchmark or run_identity(cases)
    paper_path = run_dir / "paper.json"
    if paper_path.is_file():
        manifest["paper_sha256"] = hashlib.sha256(paper_path.read_bytes()).hexdigest()
    try:
        _seal_case_archive(run_dir, manifest, cases)
    except OSError as e:
        # 封印失败不能连累 manifest 落盘——没有 manifest 的 run 比没封印更糟
        log.error("题库封印失败（run 无封印，verify 将降级警告）: %s", e)
    if failed:
        manifest["failed_cases"] = failed
    if case_rotation is not None:
        manifest["case_rotation"] = case_rotation
    # 复现元数据：本轮实际挂在哪个模型（网关/直连两模式 + 逐 run 改写口径）。
    # 此前只在 CLI cmd_run 补写——UI 发车的 run 一直缺这格（compare 对账盲区）
    from memhall.gateway import model_backend

    manifest["model_backend"] = model_backend(model_override)
    if clock_restore_failed:
        # R33：时钟残留警报——这些 case 之后系统时间可能仍 +N 天，
        # 后续 run 的时间语义（temporal 族）需对照本字段核查
        manifest["clock_restore_failed"] = clock_restore_failed
    if clock_drift is not None:
        # A1：发车时宿主-评测机对时差（秒，评测机-宿主）——口径可追溯；
        # 发车闸门只拦超阈值的（拒跑），落账值本身不判好坏
        manifest["clock_drift_s"] = round(clock_drift, 1)
    if gateway_reconciliation:
        # A2：三态对账结论（verified/suspect/unknown）——账单读不到是
        # unknown，不是"没告警=没问题"；方法与逐 case 区间行号可审计
        manifest["gateway_reconciliation"] = gateway_reconciliation
        if gateway_reconciliation.get("status") == "suspect":
            # 旧键别名（10-14 及更早归档与既有工具还认它；新读者请用
            # gateway_reconciliation.status）——只在怀疑时出现，语义不变
            alarm = {
                "n_replies": gateway_reconciliation.get("n_replies", 0),
                "n_tag_records": gateway_reconciliation.get("records_this_run", 0),
                "note": gateway_reconciliation.get("note", ""),
            }
            if gateway_reconciliation.get("suspect_cases"):
                alarm["suspect_cases"] = gateway_reconciliation["suspect_cases"]
            manifest["gateway_reconciliation_alarm"] = alarm
    # 网关记账差值（直连模式/无记账文件时为 None，不落键）
    token_usage = summarize(usage_delta(usage_before, usage_snapshot()))
    if token_usage:
        manifest["token_usage"] = token_usage
        log.info("本轮网关记账: %d 请求 / %d tokens", token_usage["requests"], token_usage["total_tokens"])
    # 被测智能体版本（报告可复现性元数据；探测失败静默跳过）
    try:
        version = adapter.version_info()
    except Exception:  # noqa: BLE001 元数据探测不阻塞评测
        version = None
    if version:
        manifest["agent_version"] = version
    # LLM 运行时口径自动识别（用户要求：结果必须带模型 id/思考强度）——
    # 从网关账单归并被测 tag 的实际模型（网关改写后生效值）与 reasoning
    # 参数，配置漂移单列 asked_models。取数按账本游标从基线行切出本轮
    # 追加段：前一场同名 tag 的请求天然在段外（时间窗归并已退场——
    # run_id 紧凑戳曾被误当宿主本地钟，窗口整体偏一个时区混入前场流量）。
    # 直连模式/账单不可读 → None 不落键，展示层写"未记账"
    try:
        from memhall.gateway import summarize_reasoning_records

        if ledger is not None and baseline is not None:
            records = ledger.records(baseline)
            if records is not None:
                llm_rt = summarize_reasoning_records(records, _agent_gateway_key(adapter_name))
                if llm_rt:
                    llm_rt["forced_model"] = True  # 网关车道：model 网关说了算
                    manifest["llm_runtime"] = llm_rt
    except Exception:  # noqa: BLE001 口径元数据不阻塞评测
        pass
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
