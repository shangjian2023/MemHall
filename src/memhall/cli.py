"""CLI 入口：memhall run / report。

run:    载入用例 → Runner 编排 → 评分引擎 → 指标/雷达图/报告，一次出齐
report: 对已有 run 目录重渲染报告（不重跑智能体）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml

from memhall import __version__
from memhall.adapters import create_adapter
from memhall.paths import QUICK_IDS, resolve_case_dir
from memhall.report import compute_metrics, render_radar, render_report
from memhall.runner.orchestrator import pair_stores, run_suite
from memhall.schema.evidence import Verdict
from memhall.schema.models_case import MemoryCase
from memhall.scoring.engine import evaluate_case
from memhall.scoring.judge import OpenAICompatJudge

log = logging.getLogger(__name__)


def load_cases(case_dir: Path) -> list[MemoryCase]:
    cases = []
    for path in sorted(case_dir.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases.append(MemoryCase.model_validate(raw))
    return cases


def load_case_set(spec: str) -> list[MemoryCase]:
    """按 'cases/full' / 'cases/quick' 规格载入用例集。

    quick 是虚拟集：full 中六能力各 1 题，按 QUICK_IDS 引用过滤——
    不再以独立目录复制 full 文件（副本曾漂移），找不到返回空列表。
    """
    if spec.rstrip("/") in ("quick", "cases/quick"):
        full = resolve_case_dir("cases/full")
        if full is None:
            return []
        by_id = {c.case_id: c for c in load_cases(full)}
        return [by_id[i] for i in QUICK_IDS if i in by_id]
    d = resolve_case_dir(spec)
    return load_cases(d) if d is not None else []


def load_cases_for_run(run_dir: Path) -> dict[str, MemoryCase]:
    """run 目录自包含优先：cases/<id>/case.yaml 快照（heldout run、换机重渲染
    都成立）；旧 run 无快照时退回当前用例库。"""
    cases: dict[str, MemoryCase] = {}
    for p in sorted((run_dir / "cases").rglob("case.yaml")):
        c = MemoryCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        cases[c.case_id] = c
    if cases:
        return cases
    d = resolve_case_dir("cases")
    return {c.case_id: c for c in load_cases(d)} if d is not None else {}


def _finish_run(run_dir: Path, run_id: str, manifest: dict,
                verdicts: list[Verdict], cases: dict[str, MemoryCase],
                judge_mode: str = "scripted") -> dict:
    from memhall.scoring.judge import JUDGE_PROMPT_VERSION
    # R27：scripted run 不写 model_a/model_b——写了第三方审计会把脚本判卷
    # 误读为 LLM 判卷（判卷方式与模型口径是两回事，decided_by 同步区分）
    judge_info: dict = {"mode": judge_mode, "prompt_version": JUDGE_PROMPT_VERSION}
    if judge_mode == "dual":
        # model_b 为 null = JUDGE_B 未配（单判降级，A/B 轮值仲裁不触发）；
        # 网关限额放开后配 JUDGE_B_* 即恢复双判——null 别写 ""，审计要能区分
        judge_info.update({
            "model_a": os.environ.get("JUDGE_A_MODEL", ""),
            "model_b": os.environ.get("JUDGE_B_MODEL"),
        })
    manifest["judge"] = judge_info  # 依赖锁定：判卷口径可追溯（design.md §10）
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = compute_metrics(verdicts, cases)
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    with (run_dir / "verdicts.jsonl").open("w", encoding="utf-8") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")
    render_radar({manifest.get("adapter", "agent"): metrics["capability_scores"]},
                 str(run_dir / "radar.png"))
    report = render_report(run_dir, run_id, manifest, verdicts, cases, metrics)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    return metrics


def cmd_run(args: argparse.Namespace) -> int:
    cases = load_case_set(args.cases)
    if not cases:
        print(f"未找到用例: {args.cases}", file=sys.stderr)
        return 1

    try:
        adapter = create_adapter(args.adapter)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    judges = OpenAICompatJudge.pair_from_env() if args.judge == "dual" else None
    if args.judge == "dual" and judges is None:
        print("缺少 JUDGE_A_ 环境变量，回退脚本判卷", file=sys.stderr)

    from memhall.cost import estimate, fmt_tokens
    est = estimate(args.adapter, len(cases), Path(args.out))
    if est:
        req = f"、约 {est['requests']} 次请求" if est.get("requests") else ""
        print(f"⏳ 预计消耗 ≈ {fmt_tokens(est['total_tokens'])} tokens{req}"
              f"（按 {est['basis_runs']} 轮历史均摊，判卷流量另计）", flush=True)

    run_id, stores = run_suite(adapter, cases, Path(args.out), args.adapter,
                               case_source=args.cases)
    run_dir = Path(args.out) / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    from memhall.gateway import model_backend
    manifest["model_backend"] = model_backend()

    verdicts = []
    for case, store in pair_stores(cases, stores):
        verdicts.extend(evaluate_case(case, store, run_id, judges))
    metrics = _finish_run(run_dir, run_id, manifest, verdicts,
                          {c.case_id: c for c in cases}, judge_mode=args.judge)

    print(f"run_id: {run_id}")
    if metrics["overall_score"] is None:
        print("总体正确率: 未测（无有效计分探测点）")
    else:
        print(f"总体正确率: {metrics['overall_score']:.1%}"
              f"（有效计分 {metrics['n_valid']}/{metrics.get('n_score_probes', '?')}"
              f"，诊断探测 {metrics.get('n_diagnostic_probes', '?')} 不进分，"
              f"规则判卷率 {metrics['rule_scoring_rate']:.0%}）")
    for cap, score in metrics["capability_scores"].items():
        print(f"  {cap:<14} {'未测' if score is None else f'{score:.0%}'}")
    print(f"产物: {run_dir}")
    from memhall.notify import notify_run_done
    notify_run_done(args.adapter, metrics["overall_score"],
                    metrics["n_valid"], metrics["n_probes_total"],
                    str(run_dir), radar=str(run_dir / "radar.png"))
    # R57：全废轮（无一 case 出证据）返回非零——马拉松脚本此前无法感知废轮
    if not stores and cases:
        print("全部用例无证据产出（全废轮），退出码 3", file=sys.stderr)
        return 3
    return 0


def _load_verdicts(run_dir: Path, manifest: dict,
                   cases: dict[str, MemoryCase], judges,
                   rejudge: bool = False) -> tuple[list[Verdict], list[str]]:
    """优先读已落盘 verdicts；缺则（或 --re-judge 强制时）从证据 JSONL 重放评分。

    R35：failed_cases 与证据缺失的 case 跳过（对齐 pair_stores 语义），
    返回 (verdicts, skipped)——R23 后"部分 case 无证据"是合法产物，报告
    不再因此崩溃，恰是最需要重放评分的 run 也能出报告。"""
    vpath = run_dir / "verdicts.jsonl"
    if vpath.exists() and not rejudge:
        return [Verdict.model_validate(json.loads(line))
                for line in vpath.read_text(encoding="utf-8").splitlines()], []
    from memhall.schema.evidence import Evidence
    from memhall.scoring.engine import evaluate_case
    from memhall.scoring.rules import EvidenceStore
    verdicts: list[Verdict] = []
    skipped: list[str] = []
    failed = set(manifest.get("failed_cases", []))
    for cid in manifest.get("cases", []):
        if cid in failed:
            skipped.append(cid)
            continue
        case = cases.get(cid)
        ev_path = run_dir / "cases" / cid / "evidence.jsonl"
        if case is None or not ev_path.exists():
            skipped.append(cid)
            continue
        store = EvidenceStore([Evidence.model_validate(json.loads(line))
                               for line in ev_path.read_text(encoding="utf-8").splitlines()])
        verdicts.extend(evaluate_case(case, store, manifest["run_id"], judges))
    return verdicts, skipped


def _resolve_run(p: str) -> Path:
    d = Path(p)
    if d.exists():
        return d
    alt = Path("runs") / p
    if alt.exists():
        return alt
    raise SystemExit(f"找不到运行目录: {p}（可用: runs/<run_id>，或完整路径）")


def cmd_compare(args: argparse.Namespace) -> int:
    from memhall.report import compare_runs
    out = compare_runs(_resolve_run(args.run_a), _resolve_run(args.run_b),
                       Path(args.out))
    print(f"{out['label_a']} vs {out['label_b']}")
    print(f"总体: {out['overall_a']:.1%} → {out['overall_b']:.1%}"
          f"　共同探测点 {out['n_common']}　判定翻转 {out['n_flips']}")
    print(f"产物: {out['radar']}  {out['report']}")
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    from memhall.report.aggregate import aggregate_runs, format_table
    result = aggregate_runs([_resolve_run(p) for p in args.runs], Path(args.out))
    print(format_table(result))
    print(f"产物: {Path(args.out) / 'aggregate.json'}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = _resolve_run(args.run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = load_cases_for_run(run_dir)
    judges = OpenAICompatJudge.pair_from_env() if args.judge == "dual" else None
    if args.judge == "dual" and judges is None:
        print("缺少 JUDGE_A_ 环境变量，无法 dual 判卷", file=sys.stderr)
        return 1
    vpath = run_dir / "verdicts.jsonl"
    if args.rejudge and vpath.exists():
        # 备份旧口径 verdicts（R25 离线重判：scripted → dual 可对账可回退）
        old_mode = manifest.get("judge", {}).get("mode", "scripted")
        backup = run_dir / f"verdicts.{old_mode}.jsonl"
        vpath.replace(backup)
        print(f"旧 verdicts 备份: {backup.name}")
    verdicts, skipped = _load_verdicts(run_dir, manifest, cases, judges,
                                       rejudge=args.rejudge)
    if skipped:
        # R35：无证据 case 单列，不悄悄缩水（报告头/控制台都可见）
        manifest["n_cases_no_evidence"] = len(skipped)
        print(f"{len(skipped)} 个 case 无证据未计分: {', '.join(skipped[:8])}"
              f"{'…' if len(skipped) > 8 else ''}")
    # 纯重渲染（verdicts 已在、非 --rejudge）不得篡改判卷口径标签：
    # 口径跟着已有 verdicts/manifest 走，--judge 只在真判卷时生效
    effective_mode = (args.judge if (judges is not None or args.rejudge)
                      else manifest.get("judge", {}).get("mode", args.judge))
    metrics = _finish_run(run_dir, manifest["run_id"], manifest, verdicts, cases,
                          judge_mode=effective_mode)
    score = metrics["overall_score"]
    print(f"报告已出: {run_dir / 'report.md'}"
          f"（总体 {'未测' if score is None else f'{score:.1%}'}）")
    return 0


def _ensure_streams() -> None:
    """窗口模式 exe（console=False）双击启动时无控制台，sys.stdout/stderr
    为 None——print/logging 一碰就崩。重定向到 exe 同级 memhall.log，
    写不进（只读目录等）则退临时目录。"""
    if not (getattr(sys, "frozen", False)
            and (sys.stdout is None or sys.stderr is None)):
        return
    import tempfile
    from pathlib import Path
    for base in (Path(sys.executable).resolve().parent,
                 Path(tempfile.gettempdir())):
        try:
            log = (base / "memhall.log").open("a", encoding="utf-8")
        except OSError:
            continue
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log
        break


def _utf8_console() -> None:
    """Windows 控制台默认 GBK 代码页，中文输出乱码——统一改 UTF-8。"""
    import io
    for stream in (sys.stdout, sys.stderr):
        # 非 TextIOWrapper（重定向到文件等）时不动
        if (isinstance(stream, io.TextIOWrapper)
                and stream.encoding.lower() not in ("utf-8", "utf8")):
            stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_doctor(args: argparse.Namespace) -> int:
    from memhall.discovery import render_doctor, run_doctor
    rep = run_doctor(scan_remote=not args.no_vm)
    print(render_doctor(rep))
    return 0 if rep.usable_adapters() else 1


def cmd_systest(args: argparse.Namespace) -> int:
    try:
        from memhall.systests import run_systest
    except ImportError:
        print("系统级测试需要 paramiko（uv run / pip 安装），exe 单文件版不含", file=sys.stderr)
        return 2
    print("系统级测试将重启虚拟机并短暂断网（自动恢复），开始…")
    rep = run_systest(args.adapter, Path(args.out))
    for x in rep["results"]:
        print(f"  {'✅' if x.passed else '❌'} {x.zh}: {x.detail}")
    print(f"产物: {rep['run_dir']}")
    from memhall.notify import notify_run_done
    notify_run_done(f"systest-{args.adapter}",
                    rep["n_pass"] / rep["n_total"], rep["n_pass"], rep["n_total"],
                    rep["run_dir"], radar=f"{rep['run_dir']}/systest.png")
    return 0 if rep["n_pass"] == rep["n_total"] else 1


def cmd_gateway(args: argparse.Namespace) -> int:
    from pathlib import Path as _P

    from memhall.gateway import DEFAULT_PORT, aggregate_usage, create_gateway_app, default_log_path
    log_path = _P(args.log) if args.log else default_log_path()
    if args.report:
        agg = aggregate_usage(log_path)
        print(f"记账文件: {log_path}")
        if not agg["agents"]:
            print("（暂无记录）")
            return 0
        print(f"{'智能体':<18} {'请求':>6} {'错误':>4} {'入tokens':>9} {'出tokens':>9} {'合计':>9}")
        for tag, a in sorted(agg["agents"].items()):
            print(f"{tag:<18} {a['n']:>6} {a['errors']:>4} "
                  f"{a['prompt_tokens']:>9} {a['completion_tokens']:>9} {a['total_tokens']:>9}")
        return 0
    upstream = args.upstream or os.environ.get("GATEWAY_UPSTREAM_URL", "")
    key = args.key or os.environ.get("GATEWAY_UPSTREAM_KEY", "")
    model = args.model or os.environ.get("GATEWAY_MODEL", "")
    if not (upstream and key and model):
        print("缺网关配置：--upstream/--model/--key 或环境变量 "
              "GATEWAY_UPSTREAM_URL/GATEWAY_MODEL/GATEWAY_UPSTREAM_KEY", file=sys.stderr)
        return 1
    port = args.port or DEFAULT_PORT
    app = create_gateway_app(upstream, key, model, log_path)
    print(f"统一模型网关: http://{args.host}:{port}  model={model}  "
          f"upstream={upstream}  记账={log_path}")
    print("被测智能体侧只需 GATEWAY_URL + GATEWAY_MODEL 两个环境变量（dummy key 自动派生）")
    import uvicorn
    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    import socket
    url = f"http://127.0.0.1:{args.port}/"
    # 单实例：菜单重复点击时第二份进程绑不上端口会无声退出，浏览器却连回旧实例，
    # 造成"重启了但没生效"的错觉——这里识别到已有实例就直接开浏览器走人。
    probe = socket.socket()
    probe.settimeout(0.5)
    try:
        probe.connect(("127.0.0.1", args.port))
        alive = True
    except OSError:
        alive = False
    finally:
        probe.close()
    if alive:
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(f"{url}api/meta", timeout=2) as r:
                ours = "version" in json.load(r)
        except Exception:
            ours = False
        if ours:
            if not args.no_open:
                import webbrowser
                webbrowser.open(url)
            print(f"已有麟阁实例在 {url}，直接打开（不再重复启动）")
            return 0
        print(f"端口 {args.port} 被其他程序占用", file=sys.stderr)
        return 1
    from memhall.ui.app import create_app
    app = create_app()
    if args.window:
        return _run_window(app)
    import threading
    import webbrowser
    threading.Timer(1.2, lambda: webbrowser.open(url)).start() if not args.no_open else None
    print(f"麟阁 Web UI: {url}（Ctrl+C 退出）")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _run_window(app) -> int:
    """原生窗口壳：优先 pywebview（真原生窗口+任务栏图标）；打包环境缺
    pythonnet/WebView2 时退 Edge 应用模式窗口（无地址栏，观感接近原生）。"""
    import socket
    import threading
    import time
    import urllib.request

    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}/"
    for _ in range(50):  # 等服务就绪再开窗，避免白屏
        try:
            urllib.request.urlopen(f"{url}api/meta", timeout=1).read()
            break
        except OSError:
            time.sleep(0.2)
    try:
        import webview
        webview.create_window("麟阁 MemHall · 智能体记忆评测", url,
                              width=1280, height=880, min_size=(980, 640))
        webview.start()
        return 0
    except Exception:
        pass
    _open_app_window(url)
    t.join()
    return 0


def _open_app_window(url: str) -> None:
    """Edge/Chrome 的 --app 窗口（无地址栏）；都没有则普通浏览器。"""
    import os
    import shutil
    import webbrowser

    cands = [shutil.which("msedge"), shutil.which("chrome"),
             os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
             os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")]
    for path in cands:
        if path and os.path.isfile(path):
            import subprocess
            subprocess.Popen([path, f"--app={url}"])
            return
    webbrowser.open(url)


def main() -> None:
    _ensure_streams()
    _utf8_console()
    from memhall.env import load_dotenv
    from memhall.logs import setup_logging
    if len(sys.argv) == 1 and getattr(sys, "frozen", False):
        sys.argv = ["memhall", "ui", "--window"]  # 双击 exe = 直接开窗口
    parser = argparse.ArgumentParser(prog="memhall",
                                     description="麟阁：智能体记忆能力评测基准")
    parser.add_argument("--version", action="version",
                        version=f"memhall {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        help="调试日志（逐 case/步骤/SSH 命令）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="跑一轮评测并出报告", parents=[common])
    p_run.add_argument("-a", "--adapter", default="mock", help="适配器名（默认 mock）")
    p_run.add_argument("-c", "--cases", default="cases/full", help="用例目录")
    p_run.add_argument("-o", "--out", default="runs", help="输出根目录")
    p_run.add_argument("--judge", choices=["scripted", "dual"], default="scripted",
                       help="判卷方式（dual=LLM 判卷[单判或双判，按 JUDGE_B 是否配置]）")
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report", help="出报告（缺 verdicts 时从证据重放评分）",
                           parents=[common])
    p_rep.add_argument("run_dir", help="runs/ 下的 run 目录")
    p_rep.add_argument("--judge", choices=["scripted", "dual"], default="scripted",
                       help="重放评分时的判卷方式")
    p_rep.add_argument("--rejudge", action="store_true",
                       help="忽略已落盘 verdicts，从证据重放评分（旧 verdicts 自动备份）")
    p_rep.set_defaults(func=cmd_report)

    p_cmp = sub.add_parser("compare", help="对比两次运行：对比雷达 + 判定翻转明细",
                           parents=[common])
    p_cmp.add_argument("run_a", help="运行 A（runs/<run_id> 或完整路径）")
    p_cmp.add_argument("run_b", help="运行 B")
    p_cmp.add_argument("-o", "--out", default="runs/_compare", help="输出目录")
    p_cmp.set_defaults(func=cmd_compare)

    p_agg = sub.add_parser("aggregate", help="N 轮重跑聚合成 mean±std（方差口径）",
                           parents=[common])
    p_agg.add_argument("runs", nargs="+", help="N 个运行（runs/<run_id> 或完整路径）")
    p_agg.add_argument("-o", "--out", default="runs/_aggregate", help="输出目录")
    p_agg.set_defaults(func=cmd_aggregate)

    p_sys = sub.add_parser("systest", help="系统级测试：重启/拨钟/多用户/断网（真机真做）",
                           parents=[common])
    p_sys.add_argument("-a", "--adapter", default="hermes", help="VM 内适配器")
    p_sys.add_argument("-o", "--out", default="runs", help="输出根目录")
    p_sys.set_defaults(func=cmd_systest)

    p_doc = sub.add_parser("doctor", help="一键发现本机/评测机智能体，体检评测环境",
                           parents=[common])
    p_doc.add_argument("--no-vm", action="store_true", help="跳过评测机 SSH 扫描")
    p_doc.set_defaults(func=cmd_doctor)

    p_gw = sub.add_parser("gateway", help="统一模型网关（被测智能体流量必经代理）",
                          parents=[common])
    p_gw.add_argument("--host", default="127.0.0.1",
                      help="监听地址（VM 智能体要用时指 0.0.0.0）")
    p_gw.add_argument("--port", type=int, default=0, help="端口（默认 8311）")
    p_gw.add_argument("--upstream", default="", help="上游 base_url（默认 GATEWAY_UPSTREAM_URL）")
    p_gw.add_argument("--model", default="", help="统一模型名（默认 GATEWAY_MODEL）")
    p_gw.add_argument("--key", default="",
                      help="上游 API key（默认 GATEWAY_UPSTREAM_KEY；命令行会进 ps，优先用环境变量）")
    p_gw.add_argument("--log", default="", help="记账 JSONL 路径（默认 ~/.memhall/gateway-usage.jsonl）")
    p_gw.add_argument("--report", action="store_true", help="不启服务，打印现有记账聚合")
    p_gw.set_defaults(func=cmd_gateway)

    p_ui = sub.add_parser("ui", help="启动 Web UI（本地服务 + 自动开浏览器）",
                          parents=[common])
    p_ui.add_argument("--port", type=int, default=8300, help="端口（默认 8300）")
    p_ui.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p_ui.add_argument("--window", action="store_true",
                      help="原生窗口模式（pywebview，exe 双击默认）")
    p_ui.set_defaults(func=cmd_ui)

    args = parser.parse_args()
    setup_logging(verbose=getattr(args, "verbose", False))
    for env_file in load_dotenv():
        log.info("已加载配置: %s", env_file)
    raise SystemExit(args.func(args))
