"""A 档修复回归：runner/网关实验可信度三缺陷（2026-10-14 评审确认）。

1. run_id 紧凑戳是 UTC 却被当宿主本地钟解析（+8 时区差 8 小时）——
   started_at 改存发车时保存的 aware UTC datetime，不再从 run_id 回推；
2. 网关模型改写在拿到互斥锁/通过预检之前就写、清理 finally 罩不住——
   锁失败与预检拒绝都会残留覆盖；改为锁内写、全生命周期 finally 恢复
   此前值；
3. 网关对账用 tag + 1800s 宽窗字符串比时刻——上一题的调用会掩盖新题
   零调用；改为账单追加序号（与系统钟无关的因果锚点），三态结论
   （verified/suspect/unknown），账单读不到不是已验证。

全部用 fake channel / monkeypatch / 临时目录复现，不碰真实 VM、网关
状态与真实模型。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from memhall.adapters.base import AdapterError
from memhall.runner.orchestrator import (
    _gateway_bypass_alarm,
    _parse_started_at,
    _run_start_iso,
    run_suite,
)
from memhall.schema.evidence import ActionDump, Evidence, EvidencePhase, EvidenceType, MemorySnapshot, Reply
from memhall.schema.models_case import MemoryCase
from memhall.scoring.rules import EvidenceStore


def _write_bill(path: Path, rows: list) -> None:
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _append_bill(path: Path, rows: list) -> None:
    """模拟网关记账：逐请求追加一行（账本是 append-only 的，整写会破坏行序）。"""
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n")


class _BillAdapter:
    """假适配器：send 时向账单追加记录行（模拟网关记账），回一条真回复。

    record_sends 按全局 send 序号控制哪些请求真走"上游"（其余零请求，
    复现"旧题调用掩盖新题零调用"）；records_per_send 让一次回复背后
    可以有多条上游记录（请求多于回复是常态）。归因走账单行序，ts 只
    是账单格式的一部分，不参与比较。
    """

    name = "hermes"

    def __init__(self, bill: Path | None, record_sends: set[int] | None = None,
                 records_per_send: int = 1, tag: str = "memhall-hermes", model: str = "m-run"):
        self.bill = bill
        self.record_sends = record_sends or set()
        self.records_per_send = records_per_send
        self.tag = tag
        self.model = model
        self.n_send = 0

    def reset(self):
        pass

    def verify_reset(self):
        pass

    def fs_snapshot(self):
        return None

    def dump_memory(self):
        return MemorySnapshot(format="none", dumped_at=datetime.now(UTC))

    def dump_actions(self):
        return ActionDump(actions=[], coverage="unknown")

    def clock_shift(self, days):
        pass

    def clock_restore(self):
        pass

    def end_session(self, session_id):
        pass

    def close(self):
        pass

    def version_info(self):
        return "bill-fake-1"

    def send(self, session_id, text):
        self.n_send += 1
        if self.bill is not None and self.n_send in self.record_sends:
            now = datetime.now(UTC).isoformat()
            _append_bill(self.bill, [{
                "ts": now, "agent": self.tag, "model": self.model,
                "asked_model": "drift-m", "status": 200, "total_tokens": 2,
            }] * self.records_per_send)
        t = datetime.now(UTC)
        return Reply(session_id=session_id, text="好的，记住了",
                     sent_at=t, reply_at=t, latency_ms=10)


def _mk_case(cid: str, clock_shift_days: int = 0) -> MemoryCase:
    if clock_shift_days:
        phase = {"name": "inject", "steps": [{"user": f"记住 {cid}"}],
                 "system_events": {"clock_shift_days": clock_shift_days}}
    else:
        phase = {"name": "probe", "steps": [{"user": f"问题 {cid}"}]}
    return MemoryCase(
        case_id=cid, schema_version="0.1", capability="persist",
        question_type="session_recall", content_type="path", difficulty=1,
        meta={"author": "t", "created": "2026-10-14", "source": "seed"},
        phases=[phase],
        probes=[{"kind": "rule", "id": f"{cid}-p1",
                 "check": [{"assert": "default", "then": "correct"}]}],
    )


@pytest.fixture
def gw_env(tmp_path, monkeypatch):
    """网关模式环境：GATEWAY_URL 指向假地址（不发真请求），账单路径钉到
    临时文件；HOME 再钉到本测试独享目录（改写表/run.lock 不与同会话其他
    测试互串——conftest 的 session home 是共享的）。"""
    import memhall.gateway as gw

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "unified-m")
    for k in ("GATEWAY_VM_URL", "GATEWAY_AGENT_KEY"):
        monkeypatch.delenv(k, raising=False)
    bill = tmp_path / "gateway-usage.jsonl"
    monkeypatch.setattr(gw, "default_log_path", lambda: bill)
    return SimpleNamespace(bill=bill, runs=tmp_path / "runs", home=home)


def _manifest(runs: Path, rid: str) -> dict:
    return json.loads((runs / rid / "manifest.json").read_text(encoding="utf-8"))


# ---------- 缺陷 1：UTC 起点 ----------


def test_parse_started_at_offsets_suffixes_and_garbage():
    """紧凑戳按 UTC 解释（run_id 自 MVP 起就是 _utc() 生成）；同一时刻的
    不同 offset 写法解析相等；带 adapter/冲突后缀的 run_id 只取前 15 位；
    非法输入明确返回 None。"""
    base = datetime(2026, 10, 14, 2, 0, 0, tzinfo=UTC)
    assert _parse_started_at("20261014-020000") == base
    assert _parse_started_at("20261014-020000-hermes") == base      # adapter 后缀
    assert _parse_started_at("20261014-020000-hermes-2") == base    # 同秒冲突后缀
    assert _parse_started_at("2026-10-14T02:00:00+00:00") == base
    assert _parse_started_at("2026-10-14T02:00:00Z") == base
    assert _parse_started_at("2026-10-14T10:00:00+08:00") == base   # 同刻异 offset
    for bad in ["", "garbage", "20261014", "20261014-0200",
                "20261314-010203", "2026-10-14T99:00:00"]:
        assert _parse_started_at(bad) is None, bad
    assert _run_start_iso("garbage") == ""
    assert _run_start_iso("20261014-020000") == "2026-10-14T02:00:00+00:00"


def test_run_suite_started_at_saved_as_utc_iso(gw_env, monkeypatch):
    """发车时刻保存为 aware UTC ISO——不再从 run_id 回推当本地钟解析
    （本宿主是 +08:00，旧实现会把 20261014-020000 解析成前一天 18:00Z）。"""
    from memhall.runner import orchestrator

    fixed = datetime(2026, 10, 14, 2, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(orchestrator, "_utc", lambda: fixed)
    rid, _ = run_suite(_BillAdapter(gw_env.bill), [_mk_case("c1")], gw_env.runs, "hermes")
    m = _manifest(gw_env.runs, rid)
    assert m["started_at"] == "2026-10-14T02:00:00+00:00"
    assert orchestrator._parse_started_at(rid) == fixed   # run_id 与起点同刻


def test_same_second_conflict_suffix_keeps_started_at(gw_env, monkeypatch):
    """同秒冲突走 -2 后缀，started_at 仍是真实起点（不被后缀污染）。"""
    from memhall.runner import orchestrator

    fixed = datetime(2026, 10, 14, 2, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(orchestrator, "_utc", lambda: fixed)
    (gw_env.runs / "20261014-020000-hermes").mkdir(parents=True)
    rid, _ = run_suite(_BillAdapter(gw_env.bill), [_mk_case("c1")], gw_env.runs, "hermes")
    assert rid == "20261014-020000-hermes-2"
    assert _manifest(gw_env.runs, rid)["started_at"] == fixed.isoformat()


def test_summarize_reasoning_compares_offsets_as_instants(tmp_path):
    """窗口边界与账单 ts 都按解析后的时刻比较：同刻两种 offset 写法都
    命中、窗口外排除、坏 ts 行跳过——不再做字符串前缀比较。"""
    from memhall.gateway import summarize_reasoning

    t = datetime(2026, 10, 14, 2, 0, 3, tzinfo=UTC)
    bill = tmp_path / "usage.jsonl"
    _write_bill(bill, [
        {"ts": t.isoformat(), "agent": "memhall-hermes", "status": 200, "model": "m-a"},
        {"ts": t.astimezone(timezone(timedelta(hours=8))).isoformat(),
         "agent": "memhall-hermes", "status": 200, "model": "m-b"},
        {"ts": (t + timedelta(hours=1)).isoformat(), "agent": "memhall-hermes",
         "status": 200, "model": "m-out"},
        {"ts": "not a ts", "agent": "memhall-hermes", "status": 200, "model": "m-junk"},
    ])
    rt = summarize_reasoning("2026-10-14T02:00:00Z", "2026-10-14T02:00:05+00:00",
                             "memhall-hermes", log_file=bill)
    assert rt is not None
    assert sorted(rt["models"]) == ["m-a", "m-b"]
    assert rt["requests"] == 2


# ---------- 缺陷 2：模型改写生命周期 ----------


def test_lock_denial_does_not_touch_active_override(gw_env, monkeypatch):
    """锁被另一场 run 占着 → 拒跑且改写表零触碰（不写也不清别人的覆盖）。"""
    import memhall.gateway as gw
    from memhall.gateway import model_overrides, set_model_override
    from memhall.runner import orchestrator

    set_model_override("memhall-hermes", "other-run-model")   # 另一场活跃覆盖
    writes: list[tuple[str, str | None]] = []
    orig = gw.set_model_override
    monkeypatch.setattr(gw, "set_model_override",
                        lambda tag, model: (writes.append((tag, model)), orig(tag, model)))

    def _deny(self):
        raise OSError("locked by another process")

    monkeypatch.setattr(orchestrator._GlobalRunLock, "_acquire", _deny)
    with pytest.raises(RuntimeError, match="已有评测进程在跑"):
        run_suite(_BillAdapter(gw_env.bill), [_mk_case("c1")], gw_env.runs, "hermes",
                  model_override="m-new")
    assert writes == []                                     # 一次都没碰
    assert model_overrides() == {"memhall-hermes": "other-run-model"}


def test_preflight_rejection_leaves_no_override(gw_env, monkeypatch):
    """错钟预检拒绝发生在写改写之前——不残留覆盖（评审复现的第 2 项）。"""
    import memhall.gateway as gw
    from memhall.runner import orchestrator

    monkeypatch.setattr(orchestrator, "_clock_drift_seconds", lambda a: 432000.0)
    writes: list[tuple[str, str | None]] = []
    orig = gw.set_model_override
    monkeypatch.setattr(gw, "set_model_override",
                        lambda tag, model: (writes.append((tag, model)), orig(tag, model)))
    with pytest.raises(RuntimeError, match="系统钟相差"):
        run_suite(_BillAdapter(gw_env.bill), [_mk_case("c1")], gw_env.runs, "hermes",
                  model_override="m2")
    assert writes == []
    from memhall.gateway import model_overrides
    assert model_overrides() == {}


def test_override_restores_previous_value(gw_env):
    """运行中生效新值、收尾恢复发车前的值（旧值可能是操作员配的，不是垃圾）。"""
    from memhall.gateway import model_overrides, set_model_override

    set_model_override("memhall-hermes", "prev-m")
    seen: dict[str, dict] = {}

    def on_done(cid, i, n):
        seen[cid] = dict(model_overrides())

    rid, _ = run_suite(_BillAdapter(gw_env.bill, record_sends={1}),
                       [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes",
                       model_override="new-m", on_case_done=on_done)
    assert seen["c1"] == {"memhall-hermes": "new-m"}             # 运行中：新值
    assert model_overrides() == {"memhall-hermes": "prev-m"}     # 收尾：恢复旧值
    m = _manifest(gw_env.runs, rid)
    assert m["model_backend"]["model_override"] == "new-m"       # manifest 记本轮口径


def test_case_exception_records_failed_and_restores_override(gw_env):
    """case 中途异常（R23 续跑）不改生命周期：failed_cases 记账、覆盖恢复。"""
    from memhall.gateway import model_overrides, set_model_override

    set_model_override("memhall-hermes", "prev-m")
    adapter = _BillAdapter(gw_env.bill, record_sends={1})
    adapter._case_no = 0

    def counting_reset():
        adapter._case_no += 1

    def fs_snapshot():
        if adapter._case_no == 2:              # 第 2 个 case 起快照崩（R23 续跑）
            raise RuntimeError("fs boom")
        return

    adapter.reset = counting_reset             # type: ignore[method-assign]
    adapter.fs_snapshot = fs_snapshot          # type: ignore[method-assign]
    rid, _ = run_suite(adapter, [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes",
                       model_override="m")
    m = _manifest(gw_env.runs, rid)
    assert m["failed_cases"] == ["c2"]
    assert model_overrides() == {"memhall-hermes": "prev-m"}


def test_callback_abort_restores_override_and_writes_manifest(gw_env):
    """on_case_done 抛异常即中止（UI 停止按钮路径）：覆盖恢复、manifest 照落。"""
    from memhall.gateway import model_overrides, set_model_override

    set_model_override("memhall-hermes", "prev-m")

    def boom(cid, i, n):
        raise ValueError("stop pressed")

    with pytest.raises(ValueError, match="stop pressed"):
        run_suite(_BillAdapter(gw_env.bill), [_mk_case("c1")], gw_env.runs, "hermes",
                  model_override="m", on_case_done=boom)
    assert model_overrides() == {"memhall-hermes": "prev-m"}
    rid = next(p.name for p in gw_env.runs.iterdir())
    assert (gw_env.runs / rid / "manifest.json").is_file()


# ---------- 缺陷 3：账单追加序号对账 ----------


def test_prior_case_records_do_not_mask_fresh_case_zero(gw_env):
    """评审复现第 3 项：新题零请求时，旧题十分钟前的调用不再掩盖——
    追加序号下旧记录永远在新题区间之外。坏 JSON 行占行号不破坏对齐。"""
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    _write_bill(gw_env.bill, [
        {"ts": old, "agent": "memhall-other", "status": 200},
        "not-json",                                        # 坏行占一个行号
        {"ts": old, "agent": "memhall-judge", "status": 200},
    ])
    adapter = _BillAdapter(gw_env.bill, record_sends={1})  # 只有 c1 真发上游请求
    rid, _ = run_suite(adapter, [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes")
    recon = _manifest(gw_env.runs, rid)["gateway_reconciliation"]
    assert recon["status"] == "suspect"
    assert recon["suspect_cases"] == ["c2"]
    assert recon["per_case"]["c1"]["records"] == 1
    assert recon["per_case"]["c2"]["records"] == 0
    assert recon["baseline_records"] == 3
    assert gw_env.bill.read_text(encoding="utf-8").count("\n") == 4
    # 旧键别名（既有工具兼容）：只在怀疑时出现
    assert _manifest(gw_env.runs, rid)["gateway_reconciliation_alarm"]["suspect_cases"] == ["c2"]


def test_real_requests_attributed_verified(gw_env):
    """真请求落在各自区间 → verified；不要求一回复一请求。"""
    _write_bill(gw_env.bill, [])                              # 账单在（空）——账本可开
    adapter = _BillAdapter(gw_env.bill, record_sends={1, 2}, records_per_send=3)
    rid, _ = run_suite(adapter, [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes")
    m = _manifest(gw_env.runs, rid)
    recon = m["gateway_reconciliation"]
    assert recon["status"] == "verified"
    assert recon["per_case"]["c1"] == {"records": 3, "replies": 1, "shifted": False}
    assert recon["per_case"]["c2"]["records"] == 3           # 请求可多于回复
    assert "与系统钟无关" in recon["note"]
    assert "gateway_reconciliation_alarm" not in m           # 无怀疑不落旧键


def test_unreadable_bill_is_unknown_not_verified(gw_env):
    """账单缺失 → status unknown + 原因；读不到账单不能算已验证。"""
    # gw_env.bill 不创建：宿主无账单、适配器无 ch → 账本不可用
    rid, _ = run_suite(_BillAdapter(gw_env.bill), [_mk_case("c1")], gw_env.runs, "hermes")
    m = _manifest(gw_env.runs, rid)
    recon = m["gateway_reconciliation"]
    assert recon["status"] == "unknown"
    assert "不可读" in recon["reason"]
    assert "gateway_reconciliation_alarm" not in m
    assert "llm_runtime" not in m                            # 无账单 → 未记账


def test_custom_gateway_agent_key(gw_env, monkeypatch):
    """自定义 GATEWAY_AGENT_KEY：对账与 llm_runtime 都认自定义 tag，
    默认 tag 的记录不算本轮流量。"""
    monkeypatch.setenv("GATEWAY_AGENT_KEY", "memhall-custom")
    _write_bill(gw_env.bill, [])
    adapter = _BillAdapter(gw_env.bill, tag="memhall-custom", record_sends={1, 2})
    rid, _ = run_suite(adapter, [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes")
    recon = _manifest(gw_env.runs, rid)["gateway_reconciliation"]
    assert recon["tag"] == "memhall-custom"
    assert recon["status"] == "verified"
    assert recon["records_this_run"] == 2
    # 反向：只记默认 tag → 自定义 tag 零追加 → 全部怀疑
    _write_bill(gw_env.bill, [])
    adapter2 = _BillAdapter(gw_env.bill, tag="memhall-hermes", record_sends={1, 2})
    rid2, _ = run_suite(adapter2, [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes")
    recon2 = _manifest(gw_env.runs, rid2)["gateway_reconciliation"]
    assert recon2["status"] == "suspect"
    assert recon2["records_this_run"] == 0
    assert sorted(recon2["suspect_cases"]) == ["c1", "c2"]


def test_llm_runtime_excludes_previous_run_traffic(gw_env):
    """llm_runtime 按账本游标取本轮追加段：前一场同名 tag 的请求被排除
    （时间窗归并曾整体偏一个时区混入前场流量）。"""
    old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _write_bill(gw_env.bill, [
        {"ts": old, "agent": "memhall-hermes", "status": 200, "model": "m-old"},
        {"ts": old, "agent": "memhall-hermes", "status": 200, "model": "m-old2"},
    ])
    rid, _ = run_suite(_BillAdapter(gw_env.bill, record_sends={1}),
                       [_mk_case("c1")], gw_env.runs, "hermes")
    rt = _manifest(gw_env.runs, rid)["llm_runtime"]
    assert rt["agent_tag"] == "memhall-hermes"
    assert rt["models"] == ["m-run"]                          # 只本轮实际模型
    assert rt["requests"] == 1                                # 前场 2 条被排除
    assert rt["asked_models"] == ["drift-m"]                  # 配置漂移单列
    assert rt["forced_model"] is True


def test_clock_shifted_case_attributed_with_explicit_marking(gw_env):
    """拨钟 case：追加序号归因与系统钟无关，仍可精确对账；shifted 标记与
    恢复失败清单显式可见，不伪称也不隐瞒限制。"""
    _write_bill(gw_env.bill, [])
    rid, _ = run_suite(_BillAdapter(gw_env.bill, record_sends={1}),
                       [_mk_case("c-shift", clock_shift_days=3)], gw_env.runs, "hermes")
    recon = _manifest(gw_env.runs, rid)["gateway_reconciliation"]
    assert recon["status"] == "verified"
    assert recon["per_case"]["c-shift"]["shifted"] is True
    assert "与系统钟无关" in recon["note"]

    # clock_restore 失败的 case：逐 case 归因仍成立（不依赖 ts），限制单列
    _write_bill(gw_env.bill, [])

    class _BadRestore(_BillAdapter):
        def clock_restore(self):
            raise AdapterError("date -s 恢复失败")

    rid2, _ = run_suite(_BadRestore(gw_env.bill, record_sends={1}),
                        [_mk_case("c-x")], gw_env.runs, "hermes")
    m2 = _manifest(gw_env.runs, rid2)
    recon2 = m2["gateway_reconciliation"]
    assert recon2["status"] == "verified"
    assert recon2["clock_restore_failed"] == ["c-x"]          # 对账块内单列
    assert m2["clock_restore_failed"] == ["c-x"]              # manifest 原有警报保留


def test_bill_truncation_reports_unknown(gw_env):
    """账单中途变短（轮转/清空）→ 行号失序 → unknown，不硬算。"""
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _write_bill(gw_env.bill, [
        {"ts": old, "agent": "memhall-other", "status": 200},
        {"ts": old, "agent": "memhall-other", "status": 200},
        {"ts": old, "agent": "memhall-other", "status": 200},
    ])                                                        # baseline = 3

    def on_done(cid, i, n):
        if cid == "c1":                                       # c1 完成后账单被清短
            gw_env.bill.write_text(
                json.dumps({"ts": old, "agent": "memhall-other", "status": 200}) + "\n",
                encoding="utf-8")

    rid, _ = run_suite(_BillAdapter(gw_env.bill, record_sends={1}),
                       [_mk_case("c1"), _mk_case("c2")], gw_env.runs, "hermes",
                       on_case_done=on_done)
    recon = _manifest(gw_env.runs, rid)["gateway_reconciliation"]
    assert recon["status"] == "unknown"
    assert "回退" in recon["reason"]


# ---------- 旧直调形态（B 的隔离测试钉住签名，内部已修） ----------


def _store_with_dialogue(case_id: str, sent: datetime, replied: datetime) -> EvidenceStore:
    replies = [{
        "session_id": "s-01", "text": "答",
        "sent_at": sent.isoformat(), "reply_at": replied.isoformat(), "latency_ms": 1000,
    }]
    ev = Evidence(
        evidence_id="ev-000001", run_id="r", case_id=case_id,
        phase=EvidencePhase.PROBE, type=EvidenceType.DIALOGUE,
        collected_at=replied, clock_offset_days=0,
        payload={"messages": ["q"], "replies": replies}, sha256="0" * 64,
    )
    store = EvidenceStore()
    store.add(ev)
    return store


def test_legacy_bypass_alarm_true_send_reply_moments_and_offsets(gw_env):
    """无账本游标的直调形态：窗口锚到真实 sent_at/reply_at（±300s 钟差余量），
    十几分钟前的旧调用不再落窗掩盖零调用；+08:00 写法的同刻记录能命中；
    自定义 GATEWAY_AGENT_KEY 同样生效。"""
    now = datetime.now(UTC)
    sent = now - timedelta(minutes=10)
    replied = sent + timedelta(seconds=5)
    stores = [_store_with_dialogue("c1", sent, replied)]
    in_window = (sent + timedelta(seconds=1)).astimezone(timezone(timedelta(hours=8))).isoformat()
    _write_bill(gw_env.bill, [{"ts": in_window, "agent": "memhall-hermes", "status": 200}])
    assert _gateway_bypass_alarm(SimpleNamespace(ch=None), stores, "hermes", "gateway", []) is None

    # 25 分钟前的旧调用：在 sent0-300s 之外 → 不再掩盖（旧实现 1800s 宽窗会掩盖）
    _write_bill(gw_env.bill, [
        {"ts": (now - timedelta(minutes=25)).isoformat(), "agent": "memhall-hermes", "status": 200},
    ])
    alarm = _gateway_bypass_alarm(SimpleNamespace(ch=None), stores, "hermes", "gateway", [])
    assert alarm is not None and alarm["suspect_cases"] == ["c1"]


def test_legacy_bypass_alarm_honors_custom_agent_key(gw_env, monkeypatch):
    monkeypatch.setenv("GATEWAY_AGENT_KEY", "memhall-custom")
    now = datetime.now(UTC)
    stores = [_store_with_dialogue("c1", now, now)]
    _write_bill(gw_env.bill, [{"ts": now.isoformat(), "agent": "memhall-custom", "status": 200}])
    assert _gateway_bypass_alarm(SimpleNamespace(ch=None), stores, "hermes", "gateway", []) is None
    # 默认 tag 的记录不算 custom tag 的流量
    _write_bill(gw_env.bill, [{"ts": now.isoformat(), "agent": "memhall-hermes", "status": 200}])
    alarm = _gateway_bypass_alarm(SimpleNamespace(ch=None), stores, "hermes", "gateway", [])
    assert alarm is not None and alarm["n_tag_records"] == 0
