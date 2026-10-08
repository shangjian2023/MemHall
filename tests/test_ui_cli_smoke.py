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

REPO = Path(__file__).parent.parent

import memhall.ui.app as uiapp
from memhall.ui.app import create_app

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
