"""统一模型网关（design.md 统一模型对照⚠️ 的落地）：

- model 强制改写 + 凭据单点（真 key 只在转发头出现，dummy Bearer 兼作身份标记）
- 记账 JSONL（含流式 SSE 中继的 usage 抓取）
- 适配器统一模式接入（hermes-local/opencode/qwen-local/claude-local 拒绝）
- kylinbot VM config.toml 改指网关
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from memhall.adapters.base import AgentUnavailable
from memhall.gateway import (
    _backoff_delay,
    aggregate_usage,
    create_gateway_app,
    gateway_settings,
    model_backend,
)

UPSTREAM = "https://upstream.example/v1"
REAL_KEY = "sk-real-secret"


def _mk_app(tmp_path: Path, handler, min_interval: float = 0.0,
            backoff_base: float | None = None) -> httpx.AsyncClient:
    """网关 app + 直挂的测试客户端（upstream 用 MockTransport 替身）。

    min_interval=0 关掉安全节奏——默认 8s 会让多请求测试白等；
    backoff_base 缺省=env 优先（monkeypatch 的用例照常生效），
    env 也没有时用 2ms 快档让重试曲线秒级跑完。"""
    import os as _os
    base = (float(backoff_base) if backoff_base is not None
            else float(_os.environ.get("GATEWAY_BACKOFF_BASE", "") or 0.002))
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=UPSTREAM,
    )
    app = create_gateway_app(UPSTREAM, REAL_KEY, "unified-m",
                             tmp_path / "usage.jsonl", client=upstream_client,
                             min_interval=min_interval,
                             backoff_base=base)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://gw")


def test_gateway_rewrites_model_and_auth(tmp_path):
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "id": "x", "model": "unified-m",
            "choices": [{"message": {"content": "好的"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    async def go():
        async with _mk_app(tmp_path, upstream) as c:
            r = await c.post("/v1/chat/completions",
                             json={"model": "agent-picked-model", "messages": []},
                             headers={"Authorization": "Bearer memhall-hermes-local"})
            return r.status_code, r.json()

    status, body = asyncio.run(go())
    assert status == 200 and body["choices"][0]["message"]["content"] == "好的"
    up = json.loads(seen[0].read())
    assert up["model"] == "unified-m"          # 核心保证：模型网关说了算
    assert seen[0].headers["Authorization"] == f"Bearer {REAL_KEY}"
    assert "memhall-hermes-local" not in str(seen[0].headers)  # dummy 不外泄
    lines = (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    assert rec["agent"] == "memhall-hermes-local" and rec["total_tokens"] == 15
    assert rec["asked_model"] == "agent-picked-model" and rec["model"] == "unified-m"


def test_gateway_stream_relay_and_usage(tmp_path):
    seen: list[httpx.Request] = []
    sse = ('data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
           'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
           'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3,'
           '"total_tokens":10}}\n\n'
           "data: [DONE]\n\n")

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)

        async def gen():
            yield sse.encode("utf-8")

        # content 用异步生成器：保持响应为流态（bytes 直构会标记已消费，aiter 即炸）
        return httpx.Response(200, content=gen(),
                              headers={"Content-Type": "text/event-stream"})

    async def go():
        async with _mk_app(tmp_path, upstream) as c, c.stream("POST", "/chat/completions",
                            json={"model": "m", "stream": True, "messages": []},
                            headers={"Authorization": "Bearer memhall-kylinbot"}) as r:
            chunks = [chunk async for chunk in r.aiter_bytes()]
            return r.status_code, r.headers.get("content-type"), b"".join(chunks)

    status, ctype, body = asyncio.run(go())
    assert status == 200 and "text/event-stream" in ctype
    assert body.decode("utf-8") == sse  # 字节级透传
    up = json.loads(seen[0].read())
    assert up["stream_options"]["include_usage"] is True  # 注入抓 usage
    rec = json.loads((tmp_path / "usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[0])
    assert rec["agent"] == "memhall-kylinbot" and rec["total_tokens"] == 10


def test_gateway_agent_tag_privacy_and_models(tmp_path):
    """非 dummy Bearer（误配真 key）→ R34 起 401 拒转（不再照转+记 unknown）；
    真凭据既不转发也不落日志；/models 列统一模型。"""
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "usage": None})

    async def go():
        async with _mk_app(tmp_path, upstream) as c:
            r1 = await c.post("/v1/chat/completions", json={"model": "m"},
                              headers={"Authorization": "Bearer sk-real-looking-key"})
            r2 = await c.get("/v1/models")
            return r1.status_code, r2.json()

    s1, models = asyncio.run(go())
    assert s1 == 401          # R34：入站鉴权 fail closed
    assert [m["id"] for m in models["data"]] == ["unified-m"]
    log_text = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "sk-real-looking-key" not in log_text


def test_gateway_upstream_failure_recorded(tmp_path, monkeypatch):
    """重试耗尽：仍是最后一棒 502 透传（错误码在消息里），记账一行含 retries。"""
    monkeypatch.setenv("GATEWAY_BACKOFF_BASE", "0.01")  # 默认 2s 会让测试白等
    n = {"n": 0}

    def upstream(request: httpx.Request) -> httpx.Response:
        n["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    async def go():
        async with _mk_app(tmp_path, upstream) as c:
            r = await c.post("/v1/chat/completions", json={"model": "m"},
                             headers={"Authorization": "Bearer memhall-opencode"})
            return r.status_code, r.json()

    status, body = asyncio.run(go())
    assert status == 502 and "429" in body["error"]["message"]
    assert n["n"] == 7  # 1 次原始 + 6 次退避重试（_UPSTREAM_ATTEMPTS=7）
    rec = json.loads((tmp_path / "usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[0])
    assert rec["status"] == 429 and rec["agent"] == "memhall-opencode"
    assert rec["retries"] == 6
    agg = aggregate_usage(tmp_path / "usage.jsonl")
    assert agg["agents"]["memhall-opencode"]["errors"] == 1


def test_gateway_settings_lanes(monkeypatch):
    assert gateway_settings("hermes") is None  # 未配置 = 直连模式
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1/")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    g = gateway_settings("hermes-local")
    assert g == {"base_url": "http://127.0.0.1:8311/v1",
                 "key": "memhall-hermes-local", "model": "qwen3.7-plus"}
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    vm = gateway_settings("hermes", vm_lane=True)
    assert vm["base_url"] == "http://192.168.61.1:8311/v1"
    assert gateway_settings("opencode")["base_url"] == "http://127.0.0.1:8311/v1"


def test_model_backend_modes(monkeypatch):
    for k in ("GATEWAY_URL", "GATEWAY_MODEL", "AGENT_LLM_BASE_URL",
              "AGENT_LLM_MODEL", "CLAUDE_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert model_backend() == {"mode": "unknown"}
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "qwen3.7-plus")
    assert model_backend() == {"mode": "direct", "lanes": {
        "openai": {"url": "https://api.example/v1", "model": "qwen3.7-plus"}}}
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    assert model_backend() == {"mode": "gateway",
                               "url": "http://127.0.0.1:8311/v1",
                               "model": "qwen3.7-plus"}
    # 逐 run 改写口径（二元组对照）：单列 model_override，compare 侧可对账
    assert model_backend("deepseek-v4-pro") == {
        "mode": "gateway", "url": "http://127.0.0.1:8311/v1",
        "model": "qwen3.7-plus", "model_override": "deepseek-v4-pro"}


def test_hermes_local_gateway_mode(tmp_path, monkeypatch):
    from memhall.adapters.hermes_local import LocalHermesAdapter
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    a = LocalHermesAdapter(root=tmp_path)
    env = a._sandbox_env()
    assert env["DEEPSEEK_BASE_URL"] == "http://127.0.0.1:8311/v1"
    assert env["DEEPSEEK_API_KEY"] == "memhall-hermes-local"
    assert "AGENT_LLM_KEY" not in env["DEEPSEEK_API_KEY"]  # 真网关 key 不进沙箱


def test_opencode_gateway_mode(tmp_path, monkeypatch):
    from memhall.adapters.opencode import OpenCodeAdapter
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    a = OpenCodeAdapter(root=tmp_path)
    a.reset()
    cfg = json.loads((a.cfg_dir / "opencode.json").read_text(encoding="utf-8"))
    prov = cfg["provider"]["memhall-gw"]
    assert prov["options"]["baseURL"] == "http://127.0.0.1:8311/v1"
    assert prov["options"]["apiKey"] == "memhall-opencode"
    assert a.model == "qwen3.7-plus"


def test_qwen_local_gateway_mode(tmp_path, monkeypatch):
    from memhall.adapters.qwen_local import LocalQwenAdapter
    fake_home = tmp_path / "qwen-src"
    (fake_home / ".qwen").mkdir(parents=True)
    (fake_home / ".qwen" / "settings.json").write_text(json.dumps({
        "modelProviders": {"openai": [{"id": "qwen3.6-plus",
                                       "baseUrl": "https://api.mazhuoran.cloud/v1",
                                       "envKey": "NEWAPI_KEY"}]},
        "env": {"NEWAPI_KEY": "sk-user-real"},
        "model": {"name": "qwen3.6-plus"},
    }), encoding="utf-8")
    monkeypatch.setattr("memhall.adapters.qwen_local.Path.home",
                        lambda: fake_home)
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    a = LocalQwenAdapter(root=tmp_path / "sb")
    a.reset()
    settings = json.loads((a.qwen_home / "settings.json").read_text(encoding="utf-8"))
    prov = settings["modelProviders"]["openai"][0]
    assert prov["baseUrl"] == "http://127.0.0.1:8311/v1"
    assert settings["model"]["name"] == "qwen3.7-plus"
    assert a._sandbox_env()["NEWAPI_KEY"] == "memhall-qwen-local"


def test_claude_local_rejects_gateway(tmp_path, monkeypatch):
    from memhall.adapters.claude_local import LocalClaudeAdapter
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    a = LocalClaudeAdapter(root=tmp_path)
    with pytest.raises(AgentUnavailable, match="anthropic"):
        a._sandbox_env()


def test_compare_model_parity(tmp_path):
    """统一模型对账：两次运行 model_backend 不一致 → compare 报告顶部告警。"""
    from memhall.report.compare import compare_runs

    def mk(d, adapter, model):
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "adapter": adapter, "run_id": "20261002-000000-" + adapter,
            "model_backend": {"mode": "gateway", "url": "http://gw/v1",
                              "model": model}}), encoding="utf-8")
        (d / "metrics.json").write_text(json.dumps({
            "overall_score": 1.0, "capability_scores": {"persist": 1.0}}),
            encoding="utf-8")
        (d / "verdicts.jsonl").write_text("", encoding="utf-8")

    a = tmp_path / "a"
    mk(a, "hermes", "qwen3.7-plus")
    b = tmp_path / "b"
    mk(b, "kylinbot", "glm-5.3")
    out = compare_runs(a, b, tmp_path / "cmp")
    assert out["model_parity"]["same"] is False
    md = (tmp_path / "cmp" / "compare.md").read_text(encoding="utf-8")
    assert "模型口径不一致" in md and "qwen3.7-plus" in md and "glm-5.3" in md

    c = tmp_path / "c"
    mk(c, "hermes2", "qwen3.7-plus")
    out2 = compare_runs(a, c, tmp_path / "cmp2")
    assert out2["model_parity"]["same"] is True
    md2 = (tmp_path / "cmp2" / "compare.md").read_text(encoding="utf-8")
    assert "模型口径不一致" not in md2


def test_gateway_tls_error_rebuilds_client(tmp_path, monkeypatch):
    """上游 TLS 断流 → aclose 旧客户端后必须重建（复用已关客户端=整站 500，
    全量跑实逮：一次抖动砖死网关，kylinbot 两轮全灭）。"""
    monkeypatch.setenv("GATEWAY_BACKOFF_BASE", "0.01")  # 断流重试同走退避曲线
    calls = {"n": 0}

    def upstream(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("TLS bad record mac")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    mock = httpx.MockTransport(upstream)

    app = create_gateway_app(
        UPSTREAM, REAL_KEY, "unified-m", tmp_path / "u.jsonl",
        client_factory=lambda: httpx.AsyncClient(base_url=UPSTREAM, transport=mock))

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gw") as c:
            r = await c.post("/v1/chat/completions", json={"model": "x"},
                             headers={"Authorization": "Bearer memhall-hermes"})
            return r.status_code, r.json()

    status, body = asyncio.run(go())
    assert status == 200 and body["choices"][0]["message"]["content"] == "ok"
    assert calls["n"] == 2  # 第一次断流，重试（新客户端）成功


class _BrokenAdapter:
    """send 全挂的假适配器（AgentUnavailable）。"""

    name = "broken"

    def reset(self):
        pass

    def send(self, session_id, message):
        from memhall.adapters.base import AgentUnavailable
        raise AgentUnavailable("后端不可用")

    def end_session(self, sid):
        pass

    def dump_memory(self):
        from datetime import UTC, datetime

        from memhall.schema.evidence import MemorySnapshot
        return MemorySnapshot(format="files", dumped_at=datetime.now(UTC),
                              entries=[], raw=None)

    def dump_actions(self):
        from memhall.schema.evidence import ActionDump
        return ActionDump(actions=[], coverage="unknown")

    def verify_reset(self):
        pass

    def fs_snapshot(self):
        return []

    def clock_shift(self, days):
        pass

    def clock_restore(self):
        pass


def test_adapter_failure_marks_invalid_run(tmp_path):
    """适配器中途挂 → 部分对话落盘且判定 INVALID_RUN，不静默降级成 omission。"""
    from memhall.runner.orchestrator import run_suite
    from memhall.schema.models_case import MemoryCase, Phase, Step
    case = MemoryCase(
        case_id="broken-001", schema_version="0.1",
        capability="persist", question_type="session_recall",
        content_type="path", difficulty=1,
        meta={"author": "test", "created": "2026-10-02", "source": "seed"},
        phases=[Phase(name="inject", steps=[Step(user="我的笔记在 ~/notes/x")])],
        probes=[{"id": "broken-001-p1", "kind": "judge", "after": "probe",
                 "ask": "你记的我的笔记在哪？", "expect": "~/notes/x",
                 "rubric": "答出 = 记住了",
                 "verdict_map": {"reported": "correct", "forgot": "omission"},
                 "anchors": []}])
    from memhall.scoring.engine import evaluate_case
    run_id, stores = run_suite(_BrokenAdapter(), [case], tmp_path, "broken")
    verdicts = evaluate_case(case, stores[0], run_id)
    assert verdicts[0].verdict.value == "invalid_run"
    ev = (tmp_path / run_id / "cases" / "broken-001" / "evidence.jsonl") \
        .read_text(encoding="utf-8")
    assert "[RUNTIME_ERROR]" in ev  # 标记进证据，判定可下钻


class _FakeChannel:
    """kylinbot 测试通道：按序返回脚本化 (rc, out, err)。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []

    def run(self, cmd, timeout=300, stdin_data=None):
        self.calls.append(cmd)
        return self.script.pop(0)

    def sudo(self, cmd, timeout=120):
        self.calls.append(cmd)
        return self.script.pop(0)


def test_kylinbot_config_toml_rewrite(tmp_path, monkeypatch):
    from memhall.adapters.kylinbot import KylinBotAdapter
    monkeypatch.setenv("GATEWAY_VM_URL", "http://192.168.61.1:8311/v1")
    monkeypatch.setenv("GATEWAY_MODEL", "qwen3.7-plus")
    ch = _FakeChannel([
        (0, "custom:https://api.mazhuoran.cloud/v1\n", ""),   # grep 当前 provider
        (0, "", ""),                                          # 备份 cp
        (0, "", ""),                                          # sed 改指
        (0, "Cleared 5/5\n", ""),                             # memory clear
    ])
    KylinBotAdapter(channel=ch).reset()
    sed = [c for c in ch.calls if "sed -i" in c]
    assert sed, ch.calls
    assert 'custom:http://192.168.61.1:8311/v1' in sed[0]
    assert "memhall-kylinbot" in sed[0]
    assert "api.mazhuoran.cloud" not in sed[0].split("&&")[1]  # 第二段只剩网关 URL
    # 直连模式 + 残留网关配置 → 自动还原备份
    monkeypatch.delenv("GATEWAY_VM_URL")
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    ch2 = _FakeChannel([
        (0, "custom:http://192.168.61.1:8311/v1\n", ""),      # grep：发现残留
        (0, "", ""),                                          # cp 还原
        (0, "Cleared 5/5\n", ""),
    ])
    KylinBotAdapter(channel=ch2).reset()
    assert any("config.toml.bak-memhall" in c and "cp" in c for c in ch2.calls)


def test_gateway_paces_upstream_calls(tmp_path):
    """安全节奏：间隔内的第二个请求在网关排队等待（压上游 RPM，不回 429）。"""

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "x", "model": "unified-m",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 1}})

    gw = _mk_app(tmp_path, upstream, min_interval=0.25)

    async def go():
        t0 = asyncio.get_running_loop().time()
        async with gw as c:
            r1 = await c.post("/v1/chat/completions", json={"model": "m"},
                              headers={"Authorization": "Bearer memhall-pace"})
            r2 = await c.post("/v1/chat/completions", json={"model": "m"},
                              headers={"Authorization": "Bearer memhall-pace"})
        return r1.status_code, r2.status_code, asyncio.get_running_loop().time() - t0

    s1, s2, dt = asyncio.run(go())
    assert s1 == 200 and s2 == 200
    assert dt >= 0.2, f"第二个请求应被节奏压住，实际只隔了 {dt:.3f}s"


def test_gateway_pace_off_with_zero_interval(tmp_path):
    """间隔 0 = 关闭节奏（测试替身/本地快速上游用）。"""

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "x", "model": "unified-m",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 1}})

    gw = _mk_app(tmp_path, upstream, min_interval=0.0)

    async def go():
        codes = []
        async with gw as c:
            for _ in range(3):
                r = await c.post("/v1/chat/completions", json={"model": "m"},
                                 headers={"Authorization": "Bearer memhall-pace"})
                codes.append(r.status_code)
        return codes

    assert asyncio.run(go()) == [200, 200, 200]


def test_backoff_delay_schedule():
    """指数退避曲线：base·2^n 封顶 cap，等抖动取 [d/2, d]，Retry-After 优先且封顶。"""
    for _ in range(30):  # 抖动是随机的，多次采样验证边界
        assert 1.0 <= _backoff_delay(0, None, 2.0, 30.0) <= 2.0      # 2^0·2=2
        assert 8.0 <= _backoff_delay(3, None, 2.0, 30.0) <= 16.0     # 2^3·2=16
        assert 15.0 <= _backoff_delay(9, None, 2.0, 30.0) <= 30.0    # 512 封顶 30
    assert _backoff_delay(0, "120", 2.0, 30.0) == 30.0    # Retry-After 大值 → 封顶
    assert _backoff_delay(3, "0.001", 2.0, 30.0) >= 8.0   # Retry-After 小值 → 公式优先
    assert _backoff_delay(0, "garbage", 2.0, 30.0) <= 2.0  # 头解析不了当没有
    assert 3.0 <= _backoff_delay(0, "6", 2.0, 30.0) <= 6.0  # 秒数指示被尊重


def test_gateway_retries_transient_503(tmp_path, monkeypatch):
    """瞬态 503 → 网关内退避重试吸收，被测侧无感拿 200；记账一行含 retries。"""
    import time as _time
    monkeypatch.setenv("GATEWAY_BACKOFF_BASE", "0.05")
    calls = {"n": 0}

    def upstream(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, json={"error": "upstream busy"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 2}})

    async def go():
        async with _mk_app(tmp_path, upstream) as c:
            r = await c.post("/v1/chat/completions", json={"model": "m"},
                             headers={"Authorization": "Bearer memhall-hermes"})
            return r.status_code

    t0 = _time.monotonic()
    status = asyncio.run(go())
    dt = _time.monotonic() - t0
    assert status == 200 and calls["n"] == 3
    assert dt >= 0.05  # 两次退避至少各等 base/2
    rec = json.loads((tmp_path / "usage.jsonl")
                     .read_text(encoding="utf-8").splitlines()[0])
    assert rec["retries"] == 2 and rec["status"] == 200


def test_gateway_429_honors_retry_after(tmp_path, monkeypatch):
    """上游 Retry-After 指示优先于公式退避（仍封顶 GATEWAY_BACKOFF_MAX）。"""
    import time as _time
    monkeypatch.setenv("GATEWAY_BACKOFF_BASE", "0.01")
    monkeypatch.setenv("GATEWAY_BACKOFF_MAX", "5")
    calls = {"n": 0}

    def upstream(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"},
                                  headers={"Retry-After": "0.5"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 1}})

    async def go():
        async with _mk_app(tmp_path, upstream) as c:
            r = await c.post("/v1/chat/completions", json={"model": "m"},
                             headers={"Authorization": "Bearer memhall-qwen-local"})
            return r.status_code

    t0 = _time.monotonic()
    status = asyncio.run(go())
    dt = _time.monotonic() - t0
    assert status == 200 and calls["n"] == 2
    assert dt >= 0.45  # 公式值 ~0.005s，等待来自 Retry-After


# ---------- 发车前上游预检（2026-10-08 网关死掉空转 + 10-09 hosts 毒解析的回归网） ----------

def test_preflight_mock_needs_no_gateway(monkeypatch):
    from memhall.cli import _upstream_preflight
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:1/v1")
    assert _upstream_preflight("mock") is None


def test_preflight_dead_gateway_blocks_run(monkeypatch):
    from memhall.cli import _upstream_preflight
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:1/v1")  # 端口 1 必不通
    err = _upstream_preflight("hermes")
    assert err is not None and "统一网关不可达" in err


def test_preflight_live_port_passes(monkeypatch):
    import socket as _socket

    from memhall.cli import _upstream_preflight
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        monkeypatch.setenv("GATEWAY_URL", f"http://127.0.0.1:{port}/v1")
        assert _upstream_preflight("hermes") is None
    finally:
        srv.close()


def test_preflight_direct_lane_poisoned_hosts(monkeypatch):
    """直连车道预检（2026-10-09 VM 事故）：没配网关时探 AGENT_LLM_BASE_URL，
    域名被 /etc/hosts 钉到不可达地址要拦下。"""
    from memhall.cli import _upstream_preflight
    for k in ("GATEWAY_URL", "GATEWAY_VM_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    err = _upstream_preflight("hermes")
    assert err is not None and "直连上游不可达" in err and "/etc/hosts" in err


def test_gateway_model_override(tmp_path, monkeypatch):
    """逐 run 模型改写（(agent, model) 二元组对照的网关侧落点）：按
    memhall-<agent> tag 键控——改写的 tag 走 override、别的 tag 照走默认、
    收尾清掉恢复默认；账单记实际生效模型（不是智能体侧漂移值）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    from memhall.gateway import model_overrides, set_model_override
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read())["model"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2}})

    async def go():
        set_model_override("memhall-hermes", "deepseek-v4-pro")
        async with _mk_app(tmp_path, upstream) as c:
            h = {"Authorization": "Bearer memhall-hermes"}
            r1 = await c.post("/v1/chat/completions",
                              json={"model": "drift-m", "messages": []},
                              headers={"Authorization": "Bearer memhall-hermes"})
            r2 = await c.post("/v1/chat/completions",
                              json={"model": "drift-m", "messages": []},
                              headers={"Authorization": "Bearer memhall-kylinbot"})
            set_model_override("memhall-hermes", None)   # 收尾清（runner 同款）
            r3 = await c.post("/v1/chat/completions",
                              json={"model": "drift-m", "messages": []}, headers=h)
            return r1.status_code, r2.status_code, r3.status_code

    assert asyncio.run(go()) == (200, 200, 200)
    assert seen == ["deepseek-v4-pro", "unified-m", "unified-m"]
    assert model_overrides() == {}
    recs = [json.loads(ln) for ln in
            (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["model"] for r in recs] == ["deepseek-v4-pro", "unified-m",
                                          "unified-m"]
    assert all(r["asked_model"] == "drift-m" for r in recs)  # 漂移单列可对账


def test_gateway_models_passthrough(tmp_path):
    """GET /v1/models 透传上游菜单（模型下拉数据源）；默认模型不在菜单时
    保证在列；上游腿失败回落静态单模型（菜单是锦上添花，不挡车道）。"""
    menu = {"object": "list", "data": [
        {"id": "qwen3.7-plus", "object": "model"},
        {"id": "kimi-k2.7-code", "object": "model"},
        {"id": "deepseek-v4-pro", "object": "model"}]}

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {REAL_KEY}"
        return httpx.Response(200, json=menu)

    def upstream_dead(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "x"})

    async def go():
        async with _mk_app(tmp_path, upstream) as c:
            ok = await c.get("/v1/models")
        async with _mk_app(tmp_path, upstream_dead) as c:
            fb = await c.get("/v1/models")
        return ok.json(), fb.json()

    ok, fb = asyncio.run(go())
    ids = [m["id"] for m in ok["data"]]
    assert ids[0] == "unified-m"        # 默认模型补进菜单头
    assert set(ids) == {"unified-m", "qwen3.7-plus", "kimi-k2.7-code",
                        "deepseek-v4-pro"}
    assert [m["id"] for m in fb["data"]] == ["unified-m"]   # 失败回落


def test_run_suite_model_override_lifecycle(tmp_path, monkeypatch):
    """run_suite(model_override=...)：网关模式发车写改写表（memhall-<agent>
    tag）、收尾必清（残留会把下一场偷偷挂旧模型）、manifest 记口径；
    直连模式不落表（写了也没人读，还污染共享家目录）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    from memhall.gateway import model_overrides
    from memhall.runner.orchestrator import run_suite
    from memhall.schema.models_case import MemoryCase

    case = MemoryCase(
        case_id="t-001", schema_version="0.1", capability="persist",
        question_type="session_recall", content_type="path", difficulty=1,
        meta={"author": "t", "created": "2026-10-03", "source": "seed"},
        phases=[], probes=[])

    class _Noop:
        name = "hermes"

    for k in ("GATEWAY_URL", "GATEWAY_VM_URL", "GATEWAY_AGENT_KEY"):
        monkeypatch.delenv(k, raising=False)
    rid, _ = run_suite(_Noop(), [case], tmp_path, "hermes",
                       model_override="m2")
    m = json.loads((tmp_path / rid / "manifest.json")
                   .read_text(encoding="utf-8"))
    assert model_overrides() == {}                    # 直连：全程不落表
    assert "model_override" not in m["model_backend"]
    assert m["model_backend"]["mode"] == "unknown"    # （无任何车道 env）

    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:8311/v1")
    calls: list[tuple[str, str | None]] = []
    import memhall.gateway as gw_mod
    orig = gw_mod.set_model_override

    def spy(tag: str, model: str | None) -> None:
        calls.append((tag, model))
        orig(tag, model)

    monkeypatch.setattr(gw_mod, "set_model_override", spy)
    rid2, _ = run_suite(_Noop(), [case], tmp_path, "hermes",
                        model_override="deepseek-v4-pro")
    assert calls == [("memhall-hermes", "deepseek-v4-pro"),
                     ("memhall-hermes", None)]        # 写 → 收尾清
    assert model_overrides() == {}
    m2 = json.loads((tmp_path / rid2 / "manifest.json")
                    .read_text(encoding="utf-8"))
    assert m2["model_backend"]["model_override"] == "deepseek-v4-pro"
