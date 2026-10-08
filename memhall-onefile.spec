# -*- mode: python ; coding: utf-8 -*-
"""麟阁 MemHall 单文件版（onefile）：一个 exe 直发即用，用例打进包内。

启动比 onedir 慢几秒（每次解包到临时目录），分发场景（微信群/网盘）专用；
runs/.env 落在 exe 所在目录，重跑不丢。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()

a = Analysis(
    ["scripts/exe_entry.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src" / "memhall" / "ui" / "static"), "memhall/ui/static"),
        (str(ROOT / "cases"), "cases"),
    ],
    hiddenimports=[
        # 适配器注册表 / vm、systest 子命令都是运行时 import_module（lazy），
        # 静态分析看不见——1.3.1 用户实测：UI 选 hermes（本机直连）发车即
        # ModuleNotFoundError: memhall.adapters.hermes_local。全量收齐。
        # remote.py 顺带把 paramiko 拉进包（exe 也能跑 SSH 真机车道）。
        *collect_submodules("memhall.adapters"),
        "memhall.vm",
        "memhall.systests",
        # uvicorn 运行时按需导入的子模块，静态分析看不见
        "uvicorn.logging", "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MemHall",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "src" / "memhall" / "ui" / "static" / "icon.ico"),
)
