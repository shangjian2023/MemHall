"""UI 与 CLI 入口冒烟（审计：UI 22 条路由 0 测试、CLI 仅 1 个真入口测试）。

不测业务逻辑（各模块有自己的库级测试），只保证：
① create_app() 可起、核心只读端点 200；
② 每个子命令的 argparse 树可构建（--help 退出 0）——防打包后入口坏。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import memhall.ui.app as uiapp
from memhall.ui.app import create_app

REPO = Path(__file__).parent.parent

SUBCOMMANDS = ["run", "report", "compare", "aggregate", "verify", "stability",
               "vm", "systest", "doctor", "gateway", "ui"]


def test_ui_app_core_endpoints(tmp_path, monkeypatch):
    app = create_app()
    monkeypatch.setattr(uiapp, "_runs_root", lambda: tmp_path)
    c = TestClient(app)
    r = c.get("/api/meta")
    assert r.status_code == 200 and "version" in r.json()
    r2 = c.get("/api/runs")
    assert r2.status_code == 200 and r2.json()["runs"] == []
    r3 = c.get("/")
    assert r3.status_code == 200 and "麟阁" in r3.text


def test_ui_start_blocked_when_gateway_down(tmp_path, monkeypatch):
    """UI 发车必须过上游预检（10-09 实锤：预检只接在 CLI，VM 上从 UI 发车
    直接冲进死网关/毒解析，2 用例纯超时假忙才被发现）。"""
    app = create_app()
    monkeypatch.setattr(uiapp, "_runs_root", lambda: tmp_path)
    monkeypatch.setattr("memhall.cli._upstream_preflight",
                        lambda a: "网关不可达 192.168.61.1:8311 —— 测试桩")
    c = TestClient(app)
    r = c.post("/api/start", json={"adapter": "hermes"})
    assert r.status_code == 503 and "网关不可达" in r.json()["detail"]
    assert list(tmp_path.glob("**/*")) == []  # 未起跑、不留残骸


def test_upstream_preflight_lanes(monkeypatch):
    """上游预检分车道探端点：网关模式探 GATEWAY_*，直连模式探
    AGENT_LLM_BASE_URL，claude 探 CLAUDE_LLM_*（anthropic 面），
    mock 免检——10-09 VM hosts 毒解析 2 例烧废的直接防复发件。"""
    import contextlib
    import socket

    from memhall.cli import _upstream_preflight
    probed: list[tuple[str, int]] = []

    def fake_connect(addr, timeout=None):
        host, port = addr[:2]
        probed.append((host, port))
        if port == 1 or host.endswith(".invalid"):   # 测试桩：.invalid 必拒
            raise OSError(111, "refused（测试桩）")
        return contextlib.nullcontext()

    monkeypatch.setattr(socket, "create_connection", fake_connect)
    for k in ("GATEWAY_URL", "GATEWAY_VM_URL", "AGENT_LLM_BASE_URL",
              "CLAUDE_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert _upstream_preflight("mock") is None
    assert probed == []                                 # mock 免检
    monkeypatch.setenv("GATEWAY_URL", "http://gw.invalid:8311/v1")
    msg = _upstream_preflight("hermes")
    assert msg and "统一网关不可达" in msg and "GATEWAY_URL" in msg
    monkeypatch.setenv("GATEWAY_URL", "http://gw.live:8311/v1")
    assert _upstream_preflight("hermes") is None
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://direct.invalid/v1")
    assert _upstream_preflight("hermes") is None       # 网关模式下不探直连
    monkeypatch.setenv("GATEWAY_VM_URL", "http://gw.live:8311/v1")
    probed.clear()
    _upstream_preflight("hermes")
    assert probed == [("gw.live", 8311)]               # 同址去重只探一次
    monkeypatch.delenv("GATEWAY_URL")
    monkeypatch.delenv("GATEWAY_VM_URL")
    msg2 = _upstream_preflight("hermes")
    assert msg2 and "直连上游不可达" in msg2
    monkeypatch.setenv("CLAUDE_LLM_BASE_URL", "http://direct.invalid/v1")
    msg3 = _upstream_preflight("claude-local")
    assert msg3 and "claude 直连上游不可达" in msg3


def test_ui_run_image_endpoint(tmp_path, monkeypatch):
    """进阶图进 UI（10-09 用户点名：panels 产物 10-08 就随 _finish_run 生成，
    但 UI 只引用过 radar）。/api/runs/{id}/img/{name} 只放行 run 目录内
    无路径分隔的 .png 文件名，非 png / 不存在一律 404。"""
    app = create_app()
    monkeypatch.setattr(uiapp, "_runs_root", lambda: tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "report-verdict-mix.png").write_bytes(b"\x89PNG-fake")
    (rd / "manifest.json").write_text("{}", encoding="utf-8")
    c = TestClient(app)
    r = c.get("/api/runs/r1/img/report-verdict-mix.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert c.get("/api/runs/r1/img/manifest.json").status_code == 404   # 非 png
    assert c.get("/api/runs/r1/img/report-caliber.png").status_code == 404  # 不存在


def test_cli_subcommand_help_smoke():
    """每个子命令 --help 必须退出 0（argparse 树完整、入口未坏）。"""
    for cmd in SUBCOMMANDS:
        cp = subprocess.run(
            [sys.executable, "-c",
             "import sys; from memhall.cli import main; "
             f"sys.argv=['memhall', '{cmd}', '--help']; main()"],
            capture_output=True, timeout=60,
            encoding="utf-8", errors="replace",  # 中文 help 文本：显式 UTF-8 解码
            env={**__import__("os").environ,
                 "PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8"})
        assert cp.returncode == 0, f"{cmd} --help 退出 {cp.returncode}: {(cp.stderr or '')[:200]}"
        assert "usage" in (cp.stdout or "").lower(), cmd


def test_pyinstaller_specs_collect_lazy_adapters():
    """打包回归网（1.3.1 实测事故）：适配器注册表/子命令全是运行时
    import_module，PyInstaller 静态分析看不见——spec 必须 collect 全量，
    否则 exe 里选 hermes（本机直连）发车即 ModuleNotFoundError。"""
    for spec in ("memhall.spec", "memhall-onefile.spec"):
        text = (REPO / spec).read_text(encoding="utf-8")
        assert 'collect_submodules("memhall.adapters")' in text, spec
        assert '"memhall.vm"' in text, spec
