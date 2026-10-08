"""统一模型网关：所有被测智能体的 LLM 流量必经的本地代理（design.md 统一模型对照⚠️ 落地）。

调研定案（2026-10-02）：cc-switch 是桌面配置改写器、非可嵌入依赖（其社区分支的
"代理模式"验证了本架构）；LiteLLM 功能全但依赖重、离线 deb 打包负担大。自研薄层，
fastapi/httpx 已是项目依赖，零新增。

三条保证机制：
- **model 强制改写**：请求体 model 一律换成 GATEWAY_MODEL——智能体侧配置漂移无效；
- **凭据单点**：真上游 key 只在网关进程环境（GATEWAY_UPSTREAM_KEY），智能体只拿
  各自 dummy key（memhall-<agent>）——入站 Bearer 兼作智能体身份标记（记账归因用，
  不满足前缀的 Bearer 记 unknown，且绝不把入站凭据写进日志/转发给上游）；
- **记账**：每请求一行 JSONL（agent/model/tokens/耗时/状态码/重试数），GET /usage 出聚合；
- **安全节奏与退避**：转发最小间隔（GATEWAY_MIN_INTERVAL，默认 8s）+ 上游
  429/5xx 指数退避重试（等抖动、尊重 Retry-After），超限信号还会把节奏门整体冷却。

协议面：OpenAI Chat Completions（含流式 SSE 中继，注入 include_usage 抓用量）。
anthropic 面（claude-local）v1 不做——该适配器走 anthropic 端点直连（如 bigmodel），
不进统一对照车道，配置了 GATEWAY_URL 会显式报错而非静默绕过。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger(__name__)

DEFAULT_PORT = 8311


def default_log_path() -> Path:
    return Path.home() / ".memhall" / "gateway-usage.jsonl"


def _agent_tag(bearer: str) -> str:
    """入站 dummy Bearer → 智能体身份；其余一律 unknown（防真凭据落日志）。"""
    return bearer if bearer.startswith("memhall-") else "unknown"


def _inbound_ok(bearer: str) -> bool:
    """R34：入站鉴权——Bearer 必须是 memhall-* 派生 dummy 或 GATEWAY_INBOUND_TOKEN。
    此前任何 Bearer（含空）都照转：网关绑 0.0.0.0 时 LAN 任何主机可白嫖真 key
    刷量、伪造 memhall-* tag 污染记账。不匹配 401 不转发。"""
    if bearer.startswith("memhall-"):
        return True
    token = os.environ.get("GATEWAY_INBOUND_TOKEN", "")
    return bool(token) and bearer == token


# 指数退避（上游瞬态错误吸收）：限流/容量窗口直接 502 给被测智能体
# = 白白废一个 case（invalid_run），网关内退避重试把它吃掉
_UPSTREAM_ATTEMPTS = 4                       # 1 次原始 + 3 次退避重试
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


def _retry_after_seconds(retry_after: str | None) -> float | None:
    """Retry-After 头 → 秒。秒数直读；HTTP 日期算差值；解析不了当没有。"""
    if not retry_after:
        return None
    s = retry_after.strip()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None
    return (dt - datetime.now(UTC)).total_seconds()


def _backoff_delay(attempt: int, retry_after: str | None,                   base: float, cap: float) -> float:
    """指数退避 + 等抖动 + Retry-After。

    delay = min(base·2^attempt, cap) 再取 [d/2, d) 等抖动——多客户端同拍
    重试会互相撞限流，抖动把重试摊开；上游 Retry-After（秒/HTTP 日期）
    优先于公式值，但同样封顶 cap——上游异常大的指示不跟着陪葬。
    """
    d = min(base * (2 ** attempt), cap)
    d = random.uniform(d / 2, d)
    ra = _retry_after_seconds(retry_after)
    if ra is not None:
        d = max(d, min(max(ra, 0.0), cap))
    return d


def _reasoning_desc(payload: dict) -> str | None:
    """从请求体提取思考强度参数（口径自动识别，结果展示用）。

    各家叫法不一，按已见形态归一成紧凑描述：reasoning_effort（openai 系）、
    enable_thinking（qwen/dashscope）、thinking.type/budget_tokens
    （anthropic/qwen3 结构）、thinking_budget / max_reasoning_tokens。
    都没有 → None（=各智能体默认，展示层如实标注"未设置"）。
    """
    if payload.get("reasoning_effort") is not None:
        return f"reasoning_effort={payload['reasoning_effort']}"
    if payload.get("enable_thinking") is not None:
        return f"enable_thinking={payload['enable_thinking']}"
    th = payload.get("thinking")
    if isinstance(th, dict):
        parts = []
        if th.get("type"):
            parts.append(f"type={th['type']}")
        if th.get("budget_tokens"):
            parts.append(f"budget={th['budget_tokens']}")
        return "thinking(" + ",".join(parts) + ")" if parts else "thinking(present)"
    for key in ("thinking_budget", "max_reasoning_tokens"):
        if payload.get(key) is not None:
            return f"{key}={payload[key]}"
    return None


def summarize_reasoning(start_iso: str, end_iso: str, agent_tag: str,
                        log_file: Path | None = None) -> dict | None:
    """按时间窗归并某智能体在网关账单里的模型与思考参数（run 收尾进 manifest）。

    只认 tag == agent_tag 的行——判卷流量（memhall-judge*）与其他车道不掺。
    返回 None = 窗口内无该 tag 流量（直连模式/网关没跑），不落键。
    """
    path = log_file or default_log_path()
    if not path.is_file() or not start_iso or not end_iso:
        return None
    models: set[str] = set()
    reasoning: set[str] = set()
    n = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ts = str(rec.get("ts", ""))
        if not (start_iso[:19] <= ts[:19] <= end_iso[:19]):
            continue
        if rec.get("agent") != agent_tag or rec.get("status") != 200:
            continue
        n += 1
        if rec.get("asked_model"):
            models.add(str(rec["asked_model"]))
        if rec.get("reasoning"):
            reasoning.add(str(rec["reasoning"]))
    if not n:
        return None
    return {"agent_tag": agent_tag, "requests": n,
            "models": sorted(models),
            "reasoning": sorted(reasoning) or ["未设置（各智能体默认）"]}


def create_gateway_app(upstream: str, api_key: str, model: str,
                       log_path: Path | None = None,
                       client: httpx.AsyncClient | None = None,
                       client_factory=None,
                       min_interval: float | None = None) -> FastAPI:
    """网关 FastAPI 应用（cli `memhall gateway` 挂 uvicorn；测试注入替身）。

    client：完整客户端替身（MockTransport 直挂）；
    client_factory：重建路径的替身工厂（TLS 断流重建客户端的回归测试用）；
    min_interval：上游安全节奏秒数（None=读 GATEWAY_MIN_INTERVAL，默认 8），
    测试传 0 关闭——默认值会让每个多请求测试白等 8 秒。"""

    def _new_client() -> httpx.AsyncClient:
        if client_factory is not None:
            return client_factory()
        return httpx.AsyncClient(
            base_url=upstream.rstrip("/"),
            timeout=httpx.Timeout(connect=15, read=300, write=30, pool=15))

    log_path = log_path or default_log_path()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if own_client and client is not None:
            await client.aclose()

    app = FastAPI(title="MemHall Gateway", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    own_client = client is None
    if client is None:
        client = _new_client()
    state: dict = {"n_req": 0}

    # 上游安全节奏：网关是所有被测/判卷流量的唯一出口，在这统一压最稳——
    # 适配器自己的 send 间隔只管自己（hermes 甚至没有），CLI 判卷、自检、
    # 多客户端并发时全靠这兜底。默认 8s ≈ 7.5 RPM（上游实测个位数 RPM，
    # 超限会掐 TLS/503）。超出间隔的请求在网关内排队等待而非 429——被测
    # 智能体的 HTTP 客户端重试能力参差，掐 429 会把节流副作用漏进被测行为。
    if min_interval is None:
        raw = os.environ.get("GATEWAY_MIN_INTERVAL", "").strip()
        min_interval = float(raw) if raw else 8.0
    iv = float(min_interval)
    pace_lock = asyncio.Lock()
    pace_state = {"t": 0.0}
    backoff_base = float(os.environ.get("GATEWAY_BACKOFF_BASE", "") or 2.0)
    backoff_cap = float(os.environ.get("GATEWAY_BACKOFF_MAX", "") or 30.0)

    async def _pace() -> None:
        if iv <= 0:
            return
        async with pace_lock:
            wait = pace_state["t"] + iv - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            pace_state["t"] = time.monotonic()

    async def _pace_bump(delay: float) -> None:
        """限流信号 → 全局冷却：把节奏门整体后移 delay——并发客户端一起慢
        （429 是"我们整体太快"的信号，只退避当前请求治标）；节奏关着时不干预。"""
        if iv <= 0:
            return
        async with pace_lock:
            pace_state["t"] = max(pace_state["t"], time.monotonic() + delay)

    def _record(tag: str, asked: str, usage: dict | None, ms: float, status: int,
                retries: int = 0, reasoning: str | None = None) -> None:
        rec = {"ts": datetime.now(UTC).isoformat(),
               "agent": tag, "model": model, "asked_model": asked,
               "status": status, "ms": round(ms)}
        if retries:
            rec["retries"] = retries   # 上游瞬态被退避重试吸收的次数（可观测）
        if reasoning:
            rec["reasoning"] = reasoning  # 思考强度参数（口径自动识别）
        if usage:
            rec.update({k: usage.get(k) for k in
                        ("prompt_tokens", "completion_tokens", "total_tokens")})
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:  # 记账失败不打崩转发
            log.warning("网关记账写盘失败: %s", e)

    # 返回类型注解会让 FastAPI 尝试生成 response_model（Response 联合类型不支持）
    async def _proxy_inner(request: Request):
        state["n_req"] += 1
        raw = await request.body()
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        # R34：入站鉴权 fail closed——不匹配的 Bearer 不转发（防 LAN 白嫖真 key
        # 与伪造 tag 污染记账）；memhall-* dummy 或 GATEWAY_INBOUND_TOKEN 放行
        if not _inbound_ok(bearer):
            _record("rejected", "", None, 0.0, 401)
            return JSONResponse({"error": {"message": "入站凭据不合法：Bearer 应为 "
                                           "memhall-* 派生 dummy 或 GATEWAY_INBOUND_TOKEN",
                                           "type": "gateway_unauthorized"}},
                                status_code=401)
        tag = _agent_tag(bearer)
        if tag == "unknown":
            # 某些智能体（hermes 部分内部调用）用 x-api-key 带 key——同样识别
            tag = _agent_tag(request.headers.get("x-api-key", ""))
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:  # JSONDecodeError / UnicodeDecodeError（非 UTF-8 体）
            return JSONResponse({"error": {"message": "请求体不是合法 UTF-8 JSON",
                                           "type": "gateway_bad_request"}}, status_code=400)
        asked = str(payload.get("model", ""))
        reasoning = _reasoning_desc(payload)
        payload["model"] = model  # 核心保证：模型一律网关说了算
        stream = bool(payload.get("stream"))
        if stream:
            # 注入 include_usage，让上游在流尾补 usage 块（记账数据源）
            payload.setdefault("stream_options", {}).setdefault("include_usage", True)
        await _pace()  # 安全节奏压在转发前（拒绝/坏请求早退，不占节奏额度；
        # 放 t0 前面，等待时间不进记账耗时）
        t0 = time.monotonic()
        # 上游腿两路瞬态都在这吸收：①建连/发送异常（TLS 断流 bad record mac
        # 坏窗口；aclose 是永久关闭，必须弃旧建新客户端，复用已关客户端=整站
        # 500，全量跑实逮）②429/5xx（限流/容量窗口）→ 指数退避重试，耗尽才
        # 把最后一棒透传给调用方。重试只覆盖建连/发请求阶段，流中途断不重发。
        nonlocal client
        up = None
        last_err: Exception | None = None
        n_retry = 0
        for attempt in range(_UPSTREAM_ATTEMPTS):
            c = client
            assert c is not None  # init 已兜底
            try:
                up_req = c.build_request(
                    "POST", "/chat/completions", json=payload,
                    headers={"Authorization": f"Bearer {api_key}",  # 真凭据只在这出现
                             "Content-Type": "application/json"})
                resp = await c.send(up_req, stream=stream)
            except Exception as e:  # noqa: BLE001 TLS 断流常以裸 ssl.SSLError 冒出（实测）
                last_err = e
                log.warning("网关上游断流（第 %d 次）: %s", attempt + 1, e)
                if client is not None:
                    await client.aclose()
                if own_client:
                    client = _new_client()
                if attempt < _UPSTREAM_ATTEMPTS - 1:
                    n_retry += 1
                    await asyncio.sleep(
                        _backoff_delay(attempt, None, backoff_base, backoff_cap))
                continue
            if (resp.status_code in _RETRYABLE
                    and attempt < _UPSTREAM_ATTEMPTS - 1):
                body = (await resp.aread()).decode("utf-8", "replace")[:200]
                await resp.aclose()
                n_retry += 1
                delay = _backoff_delay(attempt, resp.headers.get("retry-after"),
                                       backoff_base, backoff_cap)
                log.warning("网关上游 %d，退避 %.1fs 后重试（第 %d 次）: %s",
                            resp.status_code, delay, attempt + 1, body)
                await _pace_bump(delay)  # 全局冷却：并发客户端同步慢下来
                await asyncio.sleep(delay)
                continue
            up = resp
            break
        if up is None:
            _record(tag, asked, None, (time.monotonic() - t0) * 1000, 599,
                    retries=n_retry, reasoning=reasoning)
            return JSONResponse({"error": {"message": f"上游不可达: {last_err}",
                                           "type": "gateway_upstream_unreachable"}},
                                status_code=502)
        if up.status_code >= 400:
            text = (await up.aread()).decode("utf-8", "replace")[:500]
            await up.aclose()
            _record(tag, asked, None, (time.monotonic() - t0) * 1000, up.status_code,
                    retries=n_retry, reasoning=reasoning)
            log.warning("网关转发失败 %d: %s", up.status_code, text[:200])
            return JSONResponse({"error": {
                "message": f"上游 {up.status_code}: {text}",
                "type": "gateway_upstream_error"}}, status_code=502)

        if not stream:
            data = up.json()
            await up.aclose()
            _record(tag, asked, data.get("usage"), (time.monotonic() - t0) * 1000, 200,
                    retries=n_retry, reasoning=reasoning)
            return JSONResponse(data)

        async def relay():
            buf = bytearray()
            try:
                async for chunk in up.aiter_raw():
                    buf.extend(chunk)
                    yield chunk
            finally:
                usage = _usage_from_sse(bytes(buf))
                _record(tag, asked, usage, (time.monotonic() - t0) * 1000, 200,
                        retries=n_retry, reasoning=reasoning)
                with contextlib.suppress(Exception):
                    await up.aclose()

        return StreamingResponse(relay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    async def _proxy(request: Request):
        try:
            return await _proxy_inner(request)
        except Exception as e:  # 兜底：意外异常不当 500 黑盒
            log.exception("网关内部错误")
            _record("unknown", "", None, 0.0, 500)  # 砖死也要在账上可见
            return JSONResponse({"error": {"message": f"gateway internal: {e}",
                                           "type": "gateway_internal_error"}},
                                status_code=500)

    for path in ("/v1/chat/completions", "/chat/completions"):
        app.post(path)(_proxy)

    async def _models() -> JSONResponse:
        return JSONResponse({"object": "list",
                             "data": [{"id": model, "object": "model",
                                       "owned_by": "memhall-gateway"}]})

    for path in ("/v1/models", "/models"):
        app.get(path)(_models)

    @app.get("/usage")
    async def _usage(request: Request) -> JSONResponse:
        # R34：记账数据同样不裸奔——回环免token，外部访问须带合法凭据
        if not _loopback(request) and not _req_inbound_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(aggregate_usage(log_path))

    @app.post("/v1/messages")
    async def _anthropic_unsupported() -> JSONResponse:
        return JSONResponse({"error": {
            "message": "anthropic 协议面 v1 未实现：claude-local 走 anthropic 端点"
                       "直连，不进统一对照车道（见 docs/design.md 统一模型对照）",
            "type": "gateway_protocol_unsupported"}}, status_code=501)

    @app.get("/health")
    async def _health(request: Request) -> JSONResponse:
        # R34：同 /usage——回环免 token，外部访问须带合法凭据
        if not _loopback(request) and not _req_inbound_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"ok": True, "model": model, "upstream": upstream,
                             "n_req": state["n_req"]})

    return app


def _usage_from_sse(buf: bytes) -> dict | None:
    """从 SSE 流原文里抓最后一个 usage 块（include_usage 注入的产出）。

    R59：按 \\n\\n 分帧、帧内逐行剥 data: 前缀——回复正文含 "data: " 字面量
    （如让模型输出原始 SSE 示例文本）时，旧的全文劈分法会把 usage 解析切碎，
    流式记账静默丢失。"""
    usage = None
    for frame in buf.split(b"\n\n"):
        for line in frame.split(b"\n"):
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            part = line[5:].strip()
            if not part or part == b"[DONE]":
                continue
            try:
                chunk = json.loads(part)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and chunk.get("usage"):
                usage = chunk["usage"]
    return usage


def _loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


def _req_inbound_ok(request: Request) -> bool:
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not bearer:
        bearer = request.headers.get("x-api-key", "").strip()
    return _inbound_ok(bearer)


def aggregate_usage(log_path: Path) -> dict:
    """usage.jsonl → 按智能体聚合（n/tokens/错误数），供 /usage 与 --report。"""
    agg: dict[str, dict] = {}
    if not log_path.is_file():
        return {"agents": agg, "log_path": str(log_path)}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        a = agg.setdefault(rec.get("agent", "unknown"),
                           {"n": 0, "errors": 0, "prompt_tokens": 0,
                            "completion_tokens": 0, "total_tokens": 0})
        a["n"] += 1
        if rec.get("status", 200) >= 400:
            a["errors"] += 1
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            a[k] += int(rec.get(k) or 0)
    return {"agents": agg, "log_path": str(log_path)}


# ---------- 客户端侧：适配器读统一配置 ----------

def gateway_settings(agent: str, vm_lane: bool = False) -> dict | None:
    """GATEWAY_URL 已设 → 统一模型模式：返回适配器应使用的 {base_url, key, model}。

    key 是 dummy（memhall-<agent>），网关按它识别智能体身份做记账归因；
    真实上游凭据只存在于网关进程环境，适配器进程里摸不到。
    vm_lane=True（VM 内适配器）：优先 GATEWAY_VM_URL（宿主网关的 VM 可达地址，
    如 http://192.168.61.1:8311/v1），回落 GATEWAY_URL。"""
    url = (os.environ.get("GATEWAY_VM_URL", "") if vm_lane else "").strip()
    if not url:
        url = os.environ.get("GATEWAY_URL", "").strip()
    if not url:
        return None
    return {
        "base_url": url.rstrip("/"),
        "key": os.environ.get("GATEWAY_AGENT_KEY", f"memhall-{agent}"),
        "model": os.environ.get("GATEWAY_MODEL", "unified-model"),
    }


def model_backend() -> dict:
    """manifest 复现元数据：本轮评测各适配器实际挂在哪个模型上。

    统一模式记网关（网关改写保证 manifest 与流量一致）；直连模式记录
    AGENT_LLM_*/CLAUDE_LLM_* 两条已知车道，供 compare 侧一致性对账。"""
    gw = os.environ.get("GATEWAY_URL", "").strip()
    if gw:
        return {"mode": "gateway", "url": gw,
                "model": os.environ.get("GATEWAY_MODEL", "")}
    lanes = {}
    for lane, prefix in (("openai", "AGENT_LLM"), ("anthropic", "CLAUDE_LLM")):
        url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
        if url:
            lanes[lane] = {"url": url,
                           "model": os.environ.get(f"{prefix}_MODEL", "")}
    return {"mode": "direct", "lanes": lanes} if lanes else {"mode": "unknown"}
