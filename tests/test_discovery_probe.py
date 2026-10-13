"""discovery 探测升级（借 stablyai/orca，2026-10-14）：登录 shell PATH 水合、
版本探测多候选走查、注册表对齐 orca 实测安装名。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import memhall.discovery as disc

# ---- 注册表对齐（orca src/shared/tui-agent-config.ts 实测口径）----

def test_registry_orca_alignment():
    by = {a[0]: a[1] for a in disc.LOCAL_AGENTS}
    # 安装器真实落名与直觉不同的纠偏
    assert by["kiro"] == ["kiro-cli", "kiro"]        # orca：installer 装 kiro-cli
    assert by["continue"] == ["cn", "continue"]      # orca：CLI 二进制是 cn
    assert by["trae"][0] == "traecli"                # orca：TRA CN 别名只有 traecli
    assert by["qoder"][0] == "qodercli"              # orca：qoder 是旧 dispatcher
    assert "kimi-code" in by["kimi"]                 # orca：launcher 以 kimi-code 落名
    assert "dsh-tui" in by["dsh (DeepSeek Harness)"]
    # 裸 cursor 是 Cursor IDE 启动器不是 agent CLI（PATH 侧误报，与 ~/.cursor
    # 目录误报同源），只认 cursor-agent
    assert by["cursor-agent"] == ["cursor-agent"]
    # orca 名单扩容：主流可装、此前没有的行
    for name in ("amp", "auggie", "mistral-vibe", "droid", "antigravity",
                 "muse", "devin", "rovo-dev", "command-code", "jcode",
                 "opencode2"):
        assert name in by, f"缺 orca 对齐行: {name}"


def test_registry_expansion_rows_have_no_adapter():
    """扩容行不得私接适配器：探测到该 CLI ≠ 我们的适配器能驱动它。"""
    adapters = {a[0]: a[3] for a in disc.LOCAL_AGENTS}
    for name in ("opencode2", "muse", "devin", "amp", "auggie", "rovo-dev",
                 "command-code", "jcode", "droid", "antigravity"):
        assert adapters[name] == "", name


# ---- _which_all 多目录候选（死 shim 走查的数据基础）----

def test_which_all_returns_full_paths_in_path_order(monkeypatch, tmp_path):
    """PATH 同名多份：_which 返回裸名（首目录最优扩展），_which_all 按
    目录序给出全路径候选，供版本探测逐个试（orca #22975：钉住能跑的那份）。"""
    d1, d2 = tmp_path / "shim", tmp_path / "real"
    for d in (d1, d2):
        d.mkdir()
        f = d / "agent"
        f.write_text("")
        os.chmod(f, 0o755)
    monkeypatch.setenv("PATH", f"{d1}{os.pathsep}{d2}")
    monkeypatch.setattr(disc, "_path_idx", None)
    assert disc._which("agent") == "agent"
    assert disc._which_all("agent") == [str(d1 / "agent"), str(d2 / "agent")]
    assert disc._which_all("/nonexistent/abs") == []


def test_version_probe_walks_candidates_and_pins_runnable(
        monkeypatch, tmp_path):
    """PATH 首候选是死 shim（--version 非零退出）时，走查下一份；
    版本与 detail 钉在真正答话的那份上；报错首行不再被误当版本号。"""
    monkeypatch.setattr(disc, "_hydrated", [])   # 跳过水合
    monkeypatch.setattr(disc, "CACHE_PATH", tmp_path / "cache.json")
    dead = str(tmp_path / "shim" / "kiro-cli")
    live = str(tmp_path / "real" / "kiro-cli")
    monkeypatch.setattr(disc, "_which",
                        lambda b: dead if b == "kiro-cli" else "")
    monkeypatch.setattr(disc, "_which_all",
                        lambda b, limit=3: [dead, live] if b == "kiro-cli" else [])

    def fake_run(argv, **kw):
        if argv[0] == dead:
            # 死 shim：退出非零 + 报错文本（旧实现会把 stderr 首行当版本号）
            return type("R", (), {"stdout": "", "stderr": "node: bad install\n",
                                  "returncode": 127})()
        return type("R", (), {"stdout": "Kiro CLI 2.1.0\n", "stderr": "",
                              "returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = disc.scan_local()
    f = next(x for x in out if x.name == "kiro")
    assert f.found and f.evidence == "cli"
    assert f.version == "Kiro CLI 2.1.0"
    assert f.detail == live   # 钉在答话的那份（orca #22975），不是被遮的首候选
    assert "bad install" not in f.version


def test_version_probe_timeout_stops_walk(monkeypatch, tmp_path):
    """超时≠坏（慢 CLI 实测 11s）：立即停走查不烧预算，版本降级为空。"""
    monkeypatch.setattr(disc, "_hydrated", [])
    monkeypatch.setattr(disc, "CACHE_PATH", tmp_path / "cache.json")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv[0])
        raise subprocess.TimeoutExpired(cmd=argv[0], timeout=4)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(disc, "_which",
                        lambda b: "/x/kiro-cli" if b == "kiro-cli" else "")
    monkeypatch.setattr(disc, "_which_all",
                        lambda b, limit=3: ["/x/a", "/x/b"] if b == "kiro-cli" else [])
    out = disc.scan_local()
    f = next(x for x in out if x.name == "kiro")
    assert f.found and f.version == ""
    assert calls == ["/x/a"]   # 只烧第一份的预算，不连环重试


# ---- 登录 shell PATH 水合（orca hydrate-shell-path）----

def test_parse_probe_path():
    D = disc._PATH_PROBE_DELIM
    sep = os.pathsep
    # rc 横幅 + ANSI 转义混入 stdout：分隔符之间取 PATH 本体，保序去重
    out = (f"\x1b[32mwelcome banner\x1b[0m\n{D}"
           f"/a/bin{sep}/b/bin{sep}/a/bin{sep}{D}\n")
    assert disc._parse_probe_path(out) == ["/a/bin", "/b/bin"]
    assert disc._parse_probe_path("no delimiters") == []
    assert disc._parse_probe_path(f"{D}{D}") == []


def test_hydrate_merges_shell_path_front(monkeypatch):
    """水合成功：shell 段整体前置、新增段返回、PATH 索引作废；进程内缓存
    二次调用不再 spawn（win 靠 os.name monkeypatch 走 posix 分支）。"""
    monkeypatch.setattr(disc, "_hydrated", None)
    monkeypatch.setattr(disc.os, "name", "posix")
    monkeypatch.setenv("PATH", "/base/bin")
    monkeypatch.setattr(disc, "_path_idx", "SENTINEL")
    D, sep = disc._PATH_PROBE_DELIM, os.pathsep
    spawns = []

    def fake_run(argv, **kw):
        spawns.append(argv)
        return type("R", (), {"stdout": (
            f"\x1b[32mrc banner\x1b[0m\n{D}"
            f"/base/bin{sep}/home/u/.local/bin{sep}/snap/bin{D}\n"),
            "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)
    added = disc.hydrate_shell_path()
    assert added == ["/home/u/.local/bin", "/snap/bin"]
    assert os.environ["PATH"] == (
        f"/base/bin{sep}/home/u/.local/bin{sep}/snap/bin")   # shell 段前置
    assert disc._path_idx is None          # 索引按旧 PATH 建的，已作废
    assert disc.last_path_hydration() == added
    # 缓存：二次调用不重新 spawn；fresh=True 重问
    disc.hydrate_shell_path()
    assert len(spawns) == 1
    disc.hydrate_shell_path(fresh=True)
    assert len(spawns) == 2


def test_hydrate_timeout_degrades_silently(monkeypatch):
    """慢 rc / shell 卡死：超时降级回进程 PATH，环境原样、不报错。"""
    monkeypatch.setattr(disc, "_hydrated", None)
    monkeypatch.setattr(disc.os, "name", "posix")
    monkeypatch.setenv("PATH", "/base/bin")
    monkeypatch.setattr(disc, "_path_idx", "SENTINEL")

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="bash", timeout=6)

    monkeypatch.setattr("subprocess.run", boom)
    assert disc.hydrate_shell_path() == []
    assert os.environ["PATH"] == "/base/bin"
    assert disc._path_idx == "SENTINEL"    # 没动 PATH 就不作废索引


def test_render_shows_path_hydration():
    rep = disc.DoctorReport(path_added=["/home/u/.local/bin"])
    text = disc.render_doctor(rep)
    assert "PATH 水合" in text and "/home/u/.local/bin" in text
    assert "PATH 水合" not in disc.render_doctor(disc.DoctorReport())
