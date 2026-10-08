"""doctor 一键发现：本机扫描信号矩阵 + 环境体检降级路径。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


from memhall.discovery import (
    DoctorReport,
    Finding,
    check_env,
    render_doctor,
    scan_local,
)


def test_scan_local_signal_matrix(monkeypatch, tmp_path):
    import memhall.discovery as disc
    monkeypatch.setattr(disc, "_which", lambda b: f"/fake/bin/{b}" if b == "claude" else "")
    monkeypatch.setattr(disc, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(
        "memhall.discovery.Path.exists", lambda self: "codex" in str(self))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type(
        "R", (), {"stdout": "1.0.0-cli\n", "stderr": ""})())
    out = scan_local()
    by_name = {f.name: f for f in out}
    assert by_name["claude-code"].found and by_name["claude-code"].version == "1.0.0-cli"
    assert by_name["claude-code"].evidence == "cli"
    assert by_name["codex"].found and by_name["codex"].detail  # 仅配置目录命中也算发现
    assert by_name["codex"].evidence == "cfg"  # 但只算疑似，不算在册
    assert not by_name["aider"].found


def test_check_env_degrades(monkeypatch):
    for k in ("AGENT_LLM_KEY", "AGENT_LLM_BASE_URL", "AGENT_LLM_MODEL", "VM_PASS"):
        monkeypatch.delenv(k, raising=False)
    checks = {c.name: c for c in check_env()}
    assert checks["LLM 网关配置"].ok is False
    assert "缺" in checks["LLM 网关配置"].detail


def test_which_prefers_pathext_over_shim(monkeypatch, tmp_path):
    """Windows 下无扩展 bash shim 不得蹭掉真身 .cmd/.exe（clawd 探测方案对齐）。"""
    import os

    import memhall.discovery as disc
    for n in ("claude", "claude.cmd"):
        f = tmp_path / n
        f.write_text("")
        os.chmod(f, 0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(disc, "_path_idx", None)
    assert disc._which("claude") == "claude.cmd"


def test_dsh_sentinel_and_extras_category(monkeypatch, tmp_path):
    """目录存在≠装过：dsh 需哨兵内容；运行时/工具不冒充智能体。"""
    import memhall.discovery as disc
    monkeypatch.setattr(disc, "_which", lambda b: "")
    monkeypatch.setattr(disc, "CACHE_PATH", tmp_path / "cache.json")

    def fake_exists(self):
        s = str(self)
        if ".dsh" in s:
            return "profiles" in s or s.rstrip("\\/").endswith(".dsh")
        return ".ollama" in s

    monkeypatch.setattr("memhall.discovery.Path.exists", fake_exists)
    out = scan_local()
    by = {f.name: f for f in out}
    assert by["dsh (DeepSeek Harness)"].found            # 哨兵 profiles 佐证
    assert by["dsh (DeepSeek Harness)"].evidence == "cfg"
    assert by["ollama"].found and by["ollama"].category == "runtime"
    agents = [f for f in out if f.found and f.category in ("cli", "ide")]
    assert all(f.name != "ollama" for f in agents)


def test_cfg_only_not_counted_as_confirmed(monkeypatch, tmp_path):
    """疑似档（仅目录）不进在册/可用适配器：~/.cursor 是 Cursor IDE 的数据
    目录，不能报"cursor-agent 在册·N 天前活跃"（1.3.1 用户实测误报）。"""
    import memhall.discovery as disc
    monkeypatch.setattr(disc, "_which", lambda b: "")
    monkeypatch.setattr(disc, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(
        "memhall.discovery.Path.exists", lambda self: ".cursor" in str(self))
    rep = DoctorReport(local=scan_local(), vm=[], env=[])
    cur = next(f for f in rep.local if f.name == "cursor-agent")
    assert cur.found and cur.evidence == "cfg"
    assert rep.usable_adapters() == ["mock"]
    assert "疑似" in render_doctor(rep)
    cli_agents = [f for f in rep.local
                  if f.found and f.category in ("cli", "ide") and f.evidence == "cli"]
    assert cli_agents == []  # 全目录命中时在册数应为 0


def test_activity_days(tmp_path):
    import memhall.discovery as disc
    (tmp_path / "x").mkdir()
    assert disc._activity_days([str(tmp_path / "x")]) == 0
    assert disc._activity_days([str(tmp_path / "nope")]) is None


def test_scan_vm_same_machine(monkeypatch):
    """openKylin 原生模式：VM_HOST 指向本机时不做远端探测，直接并段。"""
    import memhall.discovery as disc
    monkeypatch.setenv("VM_HOST", "127.0.0.1")
    monkeypatch.setenv("VM_PASS", "whatever")
    vm, err = disc.scan_vm()
    assert err == "SAME-MACHINE" and vm == []


def test_usable_adapters_always_has_mock():
    rep = DoctorReport(local=[], vm=[], env=[])
    assert "mock" in rep.usable_adapters()


def test_render_contains_sections():
    rep = DoctorReport(local=[Finding("claude-code", "local", True, "1.0", "x")],
                       vm=[], env=[])
    text = render_doctor(rep)
    for section in ("本机智能体", "评测机智能体", "评测环境就绪度", "可跑"):
        assert section in text
