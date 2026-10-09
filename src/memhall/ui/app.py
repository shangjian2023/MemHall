"""麟阁 MemHall Web UI 后端（FastAPI，本地 127.0.0.1，无鉴权——本机工具）。

架构仿 DeepSeek Harness Desktop 的思路：核心是本地 Web 服务 + 单页界面；
后续可套 Electron/pywebview 薄壳做独立窗口，UI 层零改动。

路由一览：
- GET  /                     单页界面（static/index.html）
- GET  /api/doctor           三路体检聚合（脚本面用；页面走下面三个子端点）
- GET  /api/doctor/local|vm|env  体检三分段（页面并行拉取）
- GET  /api/deploy-mode      部署形态（宿主 remote / openKylin 原生）
- GET  /api/adapter-status   适配器可用态（下拉过滤）
- GET  /api/case-dirs        可选用例目录
- GET  /api/estimate         token 成本预估（跑前确认弹窗）
- GET  /api/agents           智能体注册表名单（体检扫描动画素材）
- GET  /api/meta             服务端版本信息（页面据此自检新旧）
- POST /api/start            开始一轮评测（后台线程跑，SSE 推进度）
- GET  /api/events           SSE 进度流（case/done/stopped/error）
- POST /api/stop             请求停止（当前用例跑完后生效）
- GET  /api/runs             历史运行列表
- GET  /api/runs/{id}/data   单次运行指标+判定
- GET  /api/runs/{id}/radar  雷达图 PNG
- GET  /api/runs/{id}/case/{cid}/evidence  证据下钻（对话/记忆/文件/操作）
- GET  /api/runs/{id}/review 人工复核队列（HUMAN_REVIEW 未决+裁决上下文）
- POST /api/runs/{id}/review/{probe_id}  裁决一条未决判定（指标自动重算）
- GET  /api/compare          双运行对比雷达 PNG
- GET/POST /api/config       .env 图形化（密钥脱敏回显，留空=不变）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from memhall.env import load_dotenv
from memhall.paths import QUICK_IDS, case_roots, repo_root

REPO_ROOT = repo_root()
ENV_PATH = REPO_ROOT / ".env"
if not os.access(REPO_ROOT, os.W_OK):  # deb 装机：系统目录不可写，配置落家目录
    ENV_PATH = Path.home() / "memhall.env"


def _runs_root() -> Path:
    """runs 落点：仓库/exe 同级；deb 装机系统目录不可写时退 ~/memhall-runs。"""
    r = REPO_ROOT / "runs"
    try:
        r.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        r = Path.home() / "memhall-runs"
        r.mkdir(parents=True, exist_ok=True)
    return r


def _case_roots() -> list[Path]:
    """用例目录候选根（源码/exe/onefile 解包/deb 安装），单源在 memhall.paths。"""
    return case_roots()

# 用例集中文说明（键=目录名）。顺序即 UI 下拉框排序，quick 在最前：装完先冒烟。
CASE_SET_DESC = {
    "quick": "冒烟自检 · {n} 题 · 分钟级离线，装完先跑这个",
    "full": "种子题库 · {n} 题 · 六能力×六内容全覆盖（主力评测集）",
    "gen": "生成器扩量 · {n} 题 · 参数化模板生成，防智能体背题",
    "chains": "任务链 · {n} 题 · 多步任务弧，考操作与文件证据",
}
STATIC_DIR = Path(__file__).resolve().parent / "static"

SECRET_KEYS = {"VM_PASS", "AGENT_LLM_KEY", "JUDGE_A_KEY", "JUDGE_B_KEY"}
KNOWN_KEYS = [
    "VM_HOST", "VM_USER", "VM_PASS",
    "AGENT_LLM_BASE_URL", "AGENT_LLM_KEY", "AGENT_LLM_MODEL",
    "JUDGE_A_BASE_URL", "JUDGE_A_MODEL", "JUDGE_A_KEY",
    "JUDGE_B_BASE_URL", "JUDGE_B_MODEL", "JUDGE_B_KEY",
    "JUDGE_MIN_INTERVAL", "KYLINBOT_SEND_INTERVAL",
]


class _RunAborted(Exception):
    pass


class RunSession:
    """单例运行会话：后台线程跑评测，SSE 每连接一份订阅队列（R57 广播）。"""

    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.subscribers: list[asyncio.Queue] = []
        self.active = False
        self.stop = False
        self.lock = threading.Lock()

    def publish(self, payload: dict) -> None:
        """事件广播：兼容旧 q（无订阅者时的落点），并 fan-out 到全部订阅者。"""
        with contextlib.suppress(asyncio.QueueFull):
            self.q.put_nowait(payload)
        for sub in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                sub.put_nowait(payload)

    def reset(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()
        self.stop = False


session = RunSession()

# scan_vm 结果短缓存：页面加载会同时触发体检与下拉两条 VM 探测，SSH 不通时
# 连接超时最长 15s，别让下拉跟着干等第二遍。
_vm_probe_cache: tuple[float, list] = (0.0, [])


def _cached_vm_findings(ttl_s: float = 60.0) -> list:
    global _vm_probe_cache
    now = time.monotonic()
    if now - _vm_probe_cache[0] < ttl_s:
        return _vm_probe_cache[1]
    from memhall.discovery import scan_vm
    vm, _err = scan_vm()
    _vm_probe_cache = (now, vm)
    return vm


def _safe_run_id(run_id: str) -> Path:
    # R57：旧正则 \w.- 放行 ".."/"." 等纯点路径，可越出 runs 根
    if ".." in run_id or not re.fullmatch(r"[\w.-]+", run_id):
        raise HTTPException(400, "非法 run_id")
    return _runs_root() / run_id


# SPA 单页且资源名不带版本号：响应不给 Cache-Control 时浏览器按启发式缓存，
# deb 升级换脑后仍可能不回源、拿旧页面打新接口（10-05 "下拉只剩未检出"事故，
# 旧 loadAdapters 遍历新版 {mode,adapters} 响应渲染出伪条目）。no-cache 强制
# 回源验证，ETag 命中走 304，不增加流量。
NO_CACHE = {"Cache-Control": "no-cache"}


def create_app() -> FastAPI:
    app = FastAPI(title="麟阁 MemHall", docs_url=None, redoc_url=None)

    # ---------- 页面 ----------
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    @app.get("/static/{name:path}")
    def static_file(name: str) -> FileResponse:
        # R57：startswith 前缀匹配可被同前缀目录逃逸（/static../ 等），
        # 改 relative_to 严格边界校验
        root = STATIC_DIR.resolve()
        p = (root / name).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            raise HTTPException(404, "not found") from None
        if not p.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(p, headers=NO_CACHE)

    # ---------- 体检 ----------
    @app.get("/api/doctor")
    async def doctor() -> dict:
        from memhall.discovery import run_doctor
        rep = await asyncio.to_thread(run_doctor, True)
        return {
            "local": [asdict(f) for f in rep.local],
            "vm": [asdict(f) for f in rep.vm],
            "env": [asdict(c) for c in rep.env],
            "vm_error": rep.vm_error,
            "usable": rep.usable_adapters(),
        }

    # 分段端点：前端并行拉取，逐段点亮（体检总时长≈最慢一段而非三段之和）。
    # 异常也回结构化 JSON（而非裸 500），让前端能展示具体原因。
    @app.get("/api/doctor/local")
    async def doctor_local(fresh: bool = False) -> dict:
        from memhall.discovery import scan_local
        try:
            rep = await asyncio.to_thread(scan_local, 4, fresh)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"[:300]}
        return {"local": [asdict(f) for f in rep]}

    @app.get("/api/doctor/vm")
    async def doctor_vm() -> dict:
        from memhall.discovery import scan_vm
        try:
            vm, err = await asyncio.to_thread(scan_vm)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"[:300]}
        same = err == "SAME-MACHINE"
        return {"vm": [asdict(f) for f in vm],
                "vm_error": "" if same else err, "same": same}

    @app.get("/api/doctor/env")
    async def doctor_env() -> dict:
        from memhall.discovery import check_env
        try:
            env = await asyncio.to_thread(check_env)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"[:300]}
        return {"env": [asdict(c) for c in env]}

    @app.get("/api/agents")
    def agent_catalog() -> dict:
        from memhall.discovery import LOCAL_AGENTS
        return {"names": [a[0] for a in LOCAL_AGENTS]}

    @app.get("/api/deploy-mode")
    def deploy_mode() -> dict:
        """部署形态（毫秒级、无 SSH）：native = 本机即评测机（openKylin 原生，
        VM_HOST 指向本机），remote = 宿主机 + 评测 VM。配置页文案据此切换口径。"""
        from memhall.discovery import vm_is_self
        return {"mode": "native" if vm_is_self() else "remote",
                "host": os.environ.get("VM_HOST", "")}

    @app.get("/api/adapter-status")
    def adapter_status() -> dict:
        """跑页下拉框数据源：按部署形态切换 label 口径与探测方式
        （native 探本机、remote SSH 探 VM），逻辑单源在 discovery.adapter_availability。"""
        from memhall.discovery import adapter_availability
        return adapter_availability(vm_probe=_cached_vm_findings)

    @app.get("/api/meta")
    def meta() -> dict:
        from importlib.metadata import PackageNotFoundError, version
        try:
            v = version("memhall")
        except PackageNotFoundError:
            v = "dev"
        return {"version": v, "python": sys.version.split()[0]}

    # ---------- 用例目录 ----------
    @app.get("/api/case-dirs")
    def case_dirs() -> dict:
        found: dict[str, Path] = {}
        for root in _case_roots():
            base = root / "cases"
            if base.is_dir():
                for d in base.iterdir():
                    # quick 是虚拟集（full 子集按 ID 引用），物理残留目录跳过
                    if d.is_dir() and d.name != "quick":
                        found.setdefault(d.name, d)
        order = {k: i for i, k in enumerate(CASE_SET_DESC)}
        sets = [{"id": "cases/quick",
                 "label": CASE_SET_DESC["quick"].format(n=len(QUICK_IDS))}]
        sets += [
            {"id": f"cases/{name}",
             "label": CASE_SET_DESC.get(name, "{name} · {n} 题")
                      .format(n=len(list(d.glob("*.y*ml"))), name=name)}
            for name, d in sorted(found.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))
        ]
        return {"sets": sets if found else [{"id": "cases/full", "label": "种子题库（默认）"}]}

    # ---------- 运行会话 ----------
    @app.get("/api/estimate")
    def estimate(adapter: str, cases: str) -> dict:
        """跑前 token 预估：按该智能体历史 run 的网关记账均摊（弹窗数据源）。"""
        from memhall.cli import load_case_set
        from memhall.cost import estimate as estimate_fn
        try:
            n = len(load_case_set(cases))
        except Exception:
            return {"available": False, "note": "用例集加载失败"}
        if not n:
            return {"available": False, "note": "用例集为空"}
        est = estimate_fn(adapter, n, _runs_root())
        if not est:
            return {"available": False, "n_cases": n,
                    "note": "该智能体暂无网关记账历史，首跑后自动校准"
                            "（直连/mock 模式无记账）"}
        return {"available": True, **est}

    @app.get("/api/models")
    def models() -> dict:
        """跑页「模型（网关改写）」下拉数据源：网关模式拉网关 /models（网关
        转发上游菜单），默认模型来自 GATEWAY_MODEL；直连/mock 模式
        available=False，前端隐藏下拉（无从改写就不给选）。"""
        import httpx

        from memhall.gateway import gateway_settings
        gw = gateway_settings("ui-menu")
        if not gw:
            return {"available": False, "reason": "direct"}
        try:
            r = httpx.get(f"{gw['base_url']}/models", timeout=5,
                          headers={"Authorization": f"Bearer {gw['key']}"})
            ids = ([str(m.get("id")) for m in r.json().get("data", [])
                    if isinstance(m, dict) and m.get("id")]
                   if r.status_code == 200 else [])
        except Exception:
            return {"available": False, "reason": "网关不可达"}
        if not ids:
            ids = [gw["model"]]
        return {"available": True, "default": gw["model"], "models": ids}

    @app.post("/api/start")
    async def start(body: dict) -> dict:
        if session.active:
            raise HTTPException(409, "已有评测在跑")
        adapter_name = body.get("adapter", "mock")
        case_dir = body.get("cases", "cases/full")
        judge_mode = body.get("judge", "scripted")
        model_override = (body.get("model") or "").strip() or None
        if model_override:
            from memhall.gateway import gateway_settings
            if adapter_name in ("mock", "claude-local") or \
                    not gateway_settings(adapter_name):
                raise HTTPException(400, "该车道不经统一网关，无从改写模型"
                                          "（mock 无上游；claude 走豁免直连）")
        out_root = _runs_root()

        # 上游预检（与 CLI cmd_run 同一道闸，2026-10-09 实锤缺口：预检只接在
        # CLI，VM 上从 UI 发车直接冲进死网关/毒解析，2 用例纯超时假忙）。
        # TCP 探测放线程池，不挡事件循环（SSE 心跳照常）
        from memhall.cli import _upstream_preflight
        up_err = await asyncio.to_thread(_upstream_preflight, adapter_name)
        if up_err is not None:
            raise HTTPException(503, up_err)

        loop = asyncio.get_running_loop()

        def emit(payload: dict) -> None:
            loop.call_soon_threadsafe(session.publish, payload)

        def worker() -> None:
            from memhall.adapters import create_adapter
            from memhall.cli import _finish_run, load_case_set
            from memhall.runner.orchestrator import run_suite
            from memhall.scoring.engine import evaluate_case
            from memhall.scoring.judge import OpenAICompatJudge
            try:
                cases = load_case_set(case_dir)
                if not cases:
                    emit({"type": "error", "msg": f"未找到用例: {case_dir}"})
                    return
                try:
                    adapter = create_adapter(adapter_name)
                except ValueError as e:
                    emit({"type": "error", "msg": str(e)})
                    return
                emit({"type": "start", "n_cases": len(cases),
                      "adapter": adapter_name, "cases": case_dir,
                      "judge": judge_mode, "model": model_override})

                def on_case_done(cid: str, i: int, n: int) -> None:
                    if session.stop:
                        raise _RunAborted()
                    emit({"type": "case", "case": cid, "i": i, "n": n})

                judges = (OpenAICompatJudge.pair_from_env()
                          if judge_mode == "dual" else None)
                run_id, stores = run_suite(adapter, cases, out_root,
                                           adapter_name, case_source=case_dir,
                                           on_case_done=on_case_done,
                                           on_event=emit,
                                           model_override=model_override)
                emit({"type": "phase", "msg": "评测完成，开始判卷…"})
                verdicts = []
                from memhall.runner.orchestrator import pair_stores
                for case, store in pair_stores(cases, stores):
                    verdicts.extend(evaluate_case(case, store, run_id, judges))
                    if session.stop:
                        break
                run_dir = out_root / run_id
                manifest = json.loads(
                    (run_dir / "manifest.json").read_text(encoding="utf-8"))
                metrics = _finish_run(run_dir, run_id, manifest, verdicts,
                                      {c.case_id: c for c in cases},
                                      judge_mode=judge_mode)
                emit({"type": "done", "run_id": run_id,
                      "score": metrics["overall_score"],
                      "n_valid": metrics["n_valid"],
                      "n_total": metrics["n_probes_total"],
                      "caps": metrics["capability_scores"]})
                from memhall.notify import notify_run_done
                notify_run_done(adapter_name, metrics["overall_score"],
                                metrics["n_valid"], metrics["n_probes_total"],
                                str(run_dir), radar=str(run_dir / "radar.png"))
            except _RunAborted:
                emit({"type": "stopped",
                      "msg": "已中止（当前用例完成处停下，证据已落盘）"})
            except Exception as e:  # 后台线程兜底：报给前端而不是无声死掉
                emit({"type": "error", "msg": f"{type(e).__name__}: {e}"[:400]})
            finally:
                session.active = False

        session.reset()
        session.active = True
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    @app.post("/api/stop")
    def stop() -> dict:
        session.stop = True
        return {"ok": True, "note": "当前用例完成后停止"}

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        """SSE 进度流（R57：广播 + 心跳）。

        此前单 asyncio.Queue 被多个 EventSource 连接互抢事件（开两个页面
        一边有进度一边死寂）；改为每连接一份订阅队列，发布方 fan-out；
        15s 心跳注释帧防代理掐空闲连接，也让断连可被发现。
        """
        sub: asyncio.Queue = asyncio.Queue(maxsize=512)
        session.subscribers.append(sub)

        async def gen():
            try:
                yield "data: " + json.dumps(
                    {"type": "connected"}, ensure_ascii=False) + "\n\n"
                while True:
                    try:
                        item = await asyncio.wait_for(sub.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": ping\n\n"   # 心跳注释帧
                        continue
                    yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
                    if item.get("type") in ("done", "stopped", "error"):
                        break
            finally:
                with contextlib.suppress(ValueError):
                    session.subscribers.remove(sub)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    # ---------- 历史/报告 ----------
    @app.get("/api/runs")
    def list_runs() -> dict:
        entries = []
        root = _runs_root()
        if root.is_dir():
            for d in root.iterdir():
                if not (d / "manifest.json").is_file() or not d.name[0].isdigit():
                    continue
                entry = {"run_id": d.name,
                         "adapter": json.loads(
                             (d / "manifest.json").read_text(encoding="utf-8")
                         ).get("adapter", "?")}
                mfile = d / "metrics.json"
                if mfile.is_file():
                    m = json.loads(mfile.read_text(encoding="utf-8"))
                    entry["score"] = m.get("overall_score")
                    entry["n_valid"] = m.get("n_valid")
                    entry["n_total"] = m.get("n_probes_total")
                entries.append(entry)
        entries.sort(key=lambda e: e["run_id"], reverse=True)
        return {"runs": entries}

    @app.get("/api/runs/{run_id}/data")
    def run_data(run_id: str) -> dict:
        d = _safe_run_id(run_id)
        mfile = d / "metrics.json"
        if not mfile.is_file():
            raise HTTPException(404, "无指标（运行未完成或已中止）")
        metrics = json.loads(mfile.read_text(encoding="utf-8"))
        verdicts = []
        vfile = d / "verdicts.jsonl"
        if vfile.is_file():
            verdicts = [json.loads(line)
                        for line in vfile.read_text(encoding="utf-8").splitlines()]
        return {"metrics": metrics, "verdicts": verdicts}

    @app.get("/api/runs/{run_id}/radar")
    def run_radar(run_id: str) -> FileResponse:
        p = _safe_run_id(run_id) / "radar.png"
        if not p.is_file():
            raise HTTPException(404, "无雷达图")
        return FileResponse(p, media_type="image/png")

    @app.get("/api/runs/{run_id}/img/{name}")
    def run_image(run_id: str, name: str) -> FileResponse:
        """run 目录内的产物图（进阶图 report-*.png 等）。名字只放行
        无路径分隔的 .png 文件名——报告进阶图 10-08 就随 _finish_run 生成，
        但 UI 只引用过 radar，产物一直在盘上没人看（10-09 用户点名）。"""
        d = _safe_run_id(run_id)
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.png", name):
            raise HTTPException(404, "无此图")
        f = d / name
        if not f.is_file():
            raise HTTPException(404, "无此图（旧 run 或未生成）")
        return FileResponse(f, media_type="image/png")

    @app.get("/api/runs/{run_id}/case/{case_id}/evidence")
    def case_evidence(run_id: str, case_id: str) -> dict:
        if not re.fullmatch(r"[\w.-]+", case_id):
            raise HTTPException(400, "非法 case_id")
        ev_file = _safe_run_id(run_id) / "cases" / case_id / "evidence.jsonl"
        if not ev_file.is_file():
            raise HTTPException(404, "无证据文件")
        phases: dict[str, dict] = {}
        memories: list[dict] = []
        fs_created: list[str] = []
        actions: list[dict] = []
        for line in ev_file.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            pay = ev.get("payload") or {}
            ph = ev.get("phase", "")
            if ev.get("type") == "dialogue":
                p = phases.setdefault(ph, {"name": ph, "clock_days": 0, "turns": []})
                msgs, reps = pay.get("messages", []), pay.get("replies", [])
                for i, msg in enumerate(msgs):
                    r = reps[i] if i < len(reps) else {}
                    p["turns"].append({
                        "user": msg,
                        "reply": r.get("text", ""),
                        "latency_ms": r.get("latency_ms"),
                        "session": r.get("session_id", ""),
                    })
            elif ev.get("type") == "memory_snapshot":
                memories.append({"phase": ph,
                                 "entries": [e.get("content", "")
                                             for e in pay.get("entries", [])]})
            elif ev.get("type") == "fs_diff":
                fs_created = [e["path"] for e in pay.get("entries", [])
                              if e.get("change") == "created"]
            elif ev.get("type") == "actions":
                actions = [{"tool": a.get("tool", ""),
                            "result": a.get("result", "")}
                           for a in pay.get("actions", [])]
            if ev.get("clock_offset_days"):
                p = phases.setdefault(ph, {"name": ph, "clock_days": 0, "turns": []})
                p["clock_days"] = ev["clock_offset_days"]
        order = {n: i for i, n in enumerate(["inject", "confound", "probe"])}
        return {"case_id": case_id,
                "phases": sorted(phases.values(),
                                 key=lambda p: order.get(p["name"], 9)),
                "memories": memories, "fs_created": fs_created,
                "actions": actions}

    # ---------- 人工复核入口（HUMAN_REVIEW 未决判定的裁决闭环） ----------

    @app.get("/api/runs/{run_id}/review")
    def review_list(run_id: str) -> dict:
        """待复核队列：每条未决判定带完整裁决上下文（题面/期望/判定选项/
        智能体回答/判卷说明）——评审看得到证据再拍板，不是盲选。"""
        from memhall.cli import load_cases_for_run

        d = _safe_run_id(run_id)
        vfile = d / "verdicts.jsonl"
        if not vfile.is_file():
            raise HTTPException(404, "无判定文件")
        verdicts = [json.loads(line)
                    for line in vfile.read_text(encoding="utf-8").splitlines()]
        cases = load_cases_for_run(d)
        items = []
        for v in verdicts:
            if v.get("verdict") != "human_review":
                continue
            case = cases.get(v.get("case_id", ""))
            probe = None
            if case is not None:
                probe = next((p for p in case.probes
                              if getattr(p, "id", "") == v.get("probe_id")), None)
            items.append({
                "probe_id": v.get("probe_id"),
                "case_id": v.get("case_id"),
                "capability": case.capability.value if case else "?",
                "ask": getattr(probe, "ask", "") if probe else "",
                "expect": getattr(probe, "expect", "") if probe else "",
                "verdict_map": getattr(probe, "verdict_map", {}) if probe else {},
                "explanation": v.get("explanation", ""),
                "agent_reply": _probe_reply_text(d, v.get("case_id", "")),
            })
        return {"run_id": d.name, "n_pending": len(items), "items": items}

    @app.post("/api/runs/{run_id}/review/{probe_id}")
    def review_decide(run_id: str, probe_id: str, payload: dict) -> dict:
        """裁决一条未决判定：verdict 置为人的决定（decided_by=human），
        原 verdicts 先备份留痕，指标/报告/封印全链路自动重算。"""
        import shutil as _shutil

        from memhall.cli import _finish_run, load_cases_for_run
        from memhall.schema.evidence import Verdict

        allowed = {"correct", "omission", "confusion", "fabrication",
                   "over_persist", "wrong_reuse", "invalid_run"}
        decision = payload.get("verdict")
        if decision not in allowed:
            raise HTTPException(400, f"非法判定值: {decision}")
        note = str(payload.get("note", "")).strip()[:500]
        d = _safe_run_id(run_id)
        vfile = d / "verdicts.jsonl"
        if not vfile.is_file():
            raise HTTPException(404, "无判定文件")
        verdicts = [json.loads(line)
                    for line in vfile.read_text(encoding="utf-8").splitlines()]
        hit = next((v for v in verdicts if v.get("probe_id") == probe_id), None)
        if hit is None:
            raise HTTPException(404, f"无此探测点: {probe_id}")
        if hit.get("verdict") != "human_review":
            raise HTTPException(409, "该判定不在待复核队列（可能已裁决）")
        # 留痕：首次裁决前整份备份原始判定（含未决态）
        bak = d / "verdicts.pre-review.jsonl"
        if not bak.is_file():
            _shutil.copy2(vfile, bak)
        hit["verdict"] = decision
        hit["decided_by"] = "human"
        suffix = f"｜人工复核→{decision}" + (f"：{note}" if note else "")
        hit["explanation"] = (hit.get("explanation", "") + suffix)[:1000]
        vfile.write_text(
            "\n".join(json.dumps(v, ensure_ascii=False) for v in verdicts) + "\n",
            encoding="utf-8")
        # 指标/雷达/报告/封印一键重算（_finish_run 内 output_hashes 自动刷新）
        manifest = json.loads(
            (d / "manifest.json").read_text(encoding="utf-8"))
        manifest["human_review_applied"] = manifest.get("human_review_applied", 0) + 1
        cases = load_cases_for_run(d)
        mode = manifest.get("judge", {}).get("mode", "scripted")
        metrics = _finish_run(d, manifest.get("run_id", d.name), manifest,
                              [Verdict.model_validate(v) for v in verdicts],
                              cases, judge_mode=mode)
        return {"ok": True, "probe_id": probe_id, "verdict": decision,
                "metrics": {k: metrics[k] for k in
                            ("overall_score", "n_valid", "n_probes_total")
                            if k in metrics}}

    def _probe_reply_text(run_dir: Path, case_id: str) -> str:
        """probe 阶段最后一条对话的智能体回答（复核卡片的"它当时怎么说"）。"""
        ev = run_dir / "cases" / case_id / "evidence.jsonl"
        if not ev.is_file():
            return ""
        last = ""
        for line in ev.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "dialogue" and e.get("phase") == "probe":
                reps = (e.get("payload") or {}).get("replies") or []
                if reps and reps[-1].get("text"):
                    last = str(reps[-1]["text"])
        return last[:800]

    @app.get("/api/compare")
    def compare(runs: str) -> FileResponse:
        ids = [r for r in runs.split(",") if r.strip()][:2]
        if len(ids) != 2:
            raise HTTPException(400, "需要恰好两个 run_id")
        from memhall.report.radar import render_radar
        scores = {}
        names = []
        for rid in ids:
            d = _safe_run_id(rid)
            mfile = d / "metrics.json"
            if not mfile.is_file():
                raise HTTPException(404, f"{rid} 无指标")
            m = json.loads(mfile.read_text(encoding="utf-8"))
            label = f"{m.get('adapter', rid)} ({m['overall_score']:.1%})"
            scores[label] = m["capability_scores"]
            names.append(label)
        out_dir = _runs_root() / "_compare"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{ids[0]}__{ids[1]}.png"
        render_radar(scores, str(out))
        return FileResponse(out, media_type="image/png")

    # ---------- 配置 ----------
    @app.get("/api/config")
    def get_config() -> dict:
        load_dotenv()
        items = []
        for k in KNOWN_KEYS:
            v = os.environ.get(k, "")
            if k in SECRET_KEYS:
                items.append({"key": k, "set": bool(v), "value": ""})
            else:
                items.append({"key": k, "set": bool(v), "value": v})
        return {"items": items, "env_path": str(ENV_PATH)}

    @app.post("/api/config")
    def save_config(body: dict) -> dict:
        updates = {k: (v or "").strip() for k, v in (body.get("updates") or {}).items()
                   if k in KNOWN_KEYS and (v or "").strip()}
        if not updates:
            return {"ok": True, "saved": 0}
        lines = (ENV_PATH.read_text(encoding="utf-8").splitlines()
                 if ENV_PATH.exists() else [])
        seen = set()
        out = []
        for line in lines:
            m = re.match(r"^([A-Z_0-9]+)\s*=", line)
            if m and m.group(1) in updates:
                out.append(f"{m.group(1)}={updates[m.group(1)]}")
                seen.add(m.group(1))
            else:
                out.append(line)
        for k, v in updates.items():
            if k not in seen:
                out.append(f"{k}={v}")
        ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
        for k, v in updates.items():
            os.environ[k] = v
        return {"ok": True, "saved": len(updates)}

    return app
