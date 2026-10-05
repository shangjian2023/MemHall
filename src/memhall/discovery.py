"""一键发现本机/评测机智能体（doctor 档接入，仿 brew doctor / ccswitch 探测方案）。

三路探测：
1. 本机：PATH 可执行 + 知名配置目录 + --version（并行、结果落盘缓存）
2. 评测机（可选）：单次 SSH 复合命令探测远端二进制与记忆库状态
3. 评测环境就绪度：.env 密钥、SSH 可达、LLM 网关可达

缓存（学 ccswitch / clawd on desk 的成熟做法）：
- 慢操作只有版本探测（个别 CLI --version 实测 11s），结果按可执行名缓存
  到 ~/.memhall/doctor-cache.json，TTL 12h；fresh=True 强制破缓存
- which/目录/socket 探测本来就毫秒级，永远实时

设计要点（沿用成熟方案惯例）：
- 信号矩阵：可执行 + 配置目录任一命中即"发现"
- 优雅降级：发现但无适配器的智能体照常列出，给接入指引而不是报错
- 退出码：0 = 至少一个适配器可用；1 = 什么都不能跑
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from memhall.adapters.base import NO_WINDOW

CACHE_PATH = Path.home() / ".memhall" / "doctor-cache.json"
VERSION_TTL = 12 * 3600


@dataclass
class Finding:
    name: str
    where: str                 # local / vm
    found: bool
    version: str = ""
    detail: str = ""
    adapter: str = ""          # 对应 memhall run -a <name>；空 = 无适配器
    hint: str = ""
    category: str = "cli"      # cli / ide = 智能体；runtime / tool = 周边信号，非智能体
    activity_days: int | None = None  # 最近活动（天前）；None = 无目录证据可考


@dataclass
class EnvCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DoctorReport:
    local: list[Finding] = field(default_factory=list)
    vm: list[Finding] = field(default_factory=list)
    env: list[EnvCheck] = field(default_factory=list)
    vm_error: str = ""

    def usable_adapters(self) -> list[str]:
        names = {f.adapter for f in self.local + self.vm
                 if f.found and f.adapter}
        names.add("mock")
        return sorted(names)


# 适配器 CLI 候选（单源）：PATH 名在前，其后是已知安装位。hermes 候选链对齐
# clawd hooks/hermes-install.js hermesCommandCandidates（HERMES_HOME 推导 +
# .local/bin + win32 venv 变体），外加我们自装的 ~/.hermes/bin 布局；claude/qwen
# 是 PATH 型 CLI，补 npm 用户前缀位。扫描（LOCAL_AGENTS）、适配器 _resolve_exe、
# UI /api/adapter-status 三处共用这份清单，探测口径永远一致。
def _hermes_candidates() -> list[str]:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    cands = [f"{home}/bin/hermes", f"{home}/hermes-agent/venv/bin/hermes"]
    if os.name == "nt":
        cands.append(f"{home}/hermes-agent/venv/Scripts/hermes.exe")
        lad = os.environ.get("LOCALAPPDATA", "")
        if lad:
            cands.append(f"{lad}/hermes/hermes-agent/venv/Scripts/hermes.exe")
    else:
        cands.append(str(Path.home() / ".local/bin/hermes"))
    return ["hermes", *cands]


ADAPTER_CLI: dict[str, list[str]] = {
    "hermes": _hermes_candidates(),
    "claude": ["claude", "~/.local/bin/claude"],
    "qwen": ["qwen", "~/.local/bin/qwen"],
    # openKylin 侧智能体：绝对路径兜底（SSH/桌面起的进程 PATH 不含用户安装位）
    "kylinbot": ["kylin-bot", "/usr/bin/kylin-bot", "/usr/local/bin/kylin-bot"],
    "openclaw": ["openclaw", "~/.local/bin/openclaw"],
    "opencode": ["opencode"],   # R57：UI 下拉曾漏 opencode（适配器早已注册）
}


def find_cli(*candidates: str) -> str:
    """按序探测 CLI：PATH 名或 ~/、绝对路径均可，返回首个命中（""=未找到）。"""
    return next((w for c in candidates if (w := _which(c))), "")


# 智能体注册表 —— 方案对齐 clawd-on-desk 的 agent-installation-detector.js：
# ①只收真智能体（CLI 编程智能体 + IDE 内嵌），模型运行时/聊天客户端/管理工具
#   不进名单（ollama 等见 EXTRA_TOOLS，检出后单独一排说明）
# ②目录常量逐一对齐 clawd hooks/*-install.js：kimi 双代 ~/.kimi-code + ~/.kimi、
#   workbuddy 双代、pi=~/.pi/agent、qwenwork=~/.QwenWorkCN、mimocode=~/.config/mimocode
# ③目录存在≠装过：dsh/遗留 workbuddy 目录要求哨兵内容佐证（CFG_SENTINELS）
# (名字, 可执行候选, 配置目录候选, 适配器名, 类别)
LOCAL_AGENTS: list[tuple[str, list[str], list[str], str, str]] = [
    # --- CLI 编程智能体 ---
    ("claude-code", ADAPTER_CLI["claude"], ["~/.claude"], "", "cli"),
    ("codex", ["codex"], ["~/.codex"], "", "cli"),
    ("dsh (DeepSeek Harness)", ["dsh"], ["~/.dsh"], "", "cli"),
    ("gemini-cli", ["gemini"], ["~/.gemini"], "", "cli"),
    ("opencode", ADAPTER_CLI["opencode"], ["~/.config/opencode"], "opencode", "cli"),
    ("mimocode", ["mimocode"], ["~/.config/mimocode"], "", "cli"),
    ("qwen-code", ADAPTER_CLI["qwen"], ["~/.qwen"], "", "cli"),
    ("qwenpaw", ["qwenpaw"], ["~/.qwenpaw"], "", "cli"),
    ("zcode", ["zcode"], ["~/.zcode"], "", "cli"),
    ("pi", ["pi"], ["~/.pi/agent"], "", "cli"),
    ("grok-build", ["grok", "grokbuild"], ["~/.grok"], "", "cli"),
    ("workbuddy", ["workbuddy"], ["~/.workbuddy-ai", "~/.workbuddy"], "", "cli"),
    ("kimi", ["kimi"], ["~/.kimi-code", "~/.kimi"], "", "cli"),
    ("kiro", ["kiro"], ["~/.kiro"], "", "cli"),
    ("cursor-agent", ["cursor-agent", "cursor"], ["~/.cursor"], "", "cli"),
    ("copilot-cli", ["copilot"], ["~/.copilot"], "", "cli"),
    ("codebuddy", ["codebuddy"], ["~/.codebuddy"], "", "cli"),
    ("openclaw", ADAPTER_CLI["openclaw"], ["~/.openclaw"], "openclaw", "cli"),
    ("qoder", ["qoder"], ["~/.qoder"], "", "cli"),
    ("qoderwork", ["qoderwork"], ["~/.qoderwork"], "", "cli"),
    ("qwenwork", ["qwenwork"], ["~/.QwenWorkCN"], "", "cli"),
    ("aider", ["aider"], ["~/.aider.conf.yml"], "", "cli"),
    ("goose", ["goose"], ["~/.config/goose"], "", "cli"),
    ("crush", ["crush"], ["~/.config/crush"], "", "cli"),
    ("hermes", ADAPTER_CLI["hermes"], ["~/.hermes"], "hermes-local", "cli"),
    ("kylin-bot", ADAPTER_CLI["kylinbot"], ["~/.kylinbot"], "kylinbot", "cli"),
    # --- IDE / 编辑器内智能体 ---
    ("cline", ["cline"], ["~/.cline"], "", "ide"),
    ("continue", ["continue"], ["~/.continue"], "", "ide"),
    ("lingma (通义灵码)", ["lingma"], ["~/.lingma"], "", "ide"),
    ("trae", ["trae", "trea"], ["~/.trae-cn", "~/.trae-aicc"], "", "ide"),
    ("marscode", ["marscode"], ["~/.marscode"], "", "ide"),
    ("kilocode", ["kilocode"], ["~/.kilocode"], "", "ide"),
]

# 目录存在还不够的（clawd 教训：目录可能被别的工具或用户随手创建，
# 如 ~/kimi-chat 里只躺一个 index.html）：要求哨兵子项任一存在才算命中。
# agent 名 -> {配置目录 -> 哨兵列表}
CFG_SENTINELS: dict[str, dict[str, list[str]]] = {
    "dsh (DeepSeek Harness)": {"~/.dsh": ["profiles", "sessions", "storages"]},
    "workbuddy": {"~/.workbuddy": ["settings.json"]},
}

# 非智能体的周边信号：检出后单独说明，不进"发现 N 个智能体"计数
# (名字, 可执行候选, 目录, 类别, 说明)
EXTRA_TOOLS: list[tuple[str, list[str], list[str], str, str]] = [
    ("ollama", ["ollama"], ["~/.ollama"], "runtime", "本地推理运行时"),
    ("lm-studio", ["lms", "lmstudio"], ["~/.lmstudio"], "runtime", "本地推理运行时"),
    ("cc-switch", ["ccswitch", "cc-switch"], ["~/.cc-switch"], "tool", "智能体配置切换器"),
    ("cherry-studio", ["cherry"], ["~/.cherrystudio"], "tool", "聊天客户端"),
]

VM_AGENTS: list[tuple[str, list[str], str]] = [
    ("hermes", ["~/.hermes/bin/hermes", "/usr/local/bin/hermes"], "hermes"),
    ("kylin-bot", ["/usr/bin/kylin-bot", "/usr/local/bin/kylin-bot"], "kylinbot"),
    ("openclaw", ["~/.local/bin/openclaw"], "openclaw"),
]


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_path_idx: dict[str, str] | None = None   # PATH 文件名索引（进程内一次构建）
_EXT_RANK = {".exe": 4, ".com": 3, ".cmd": 2, ".bat": 1, "": 0}


def _which(name: str) -> str:
    """which 的索引版：长 PATH 下 shutil.which 逐目录 stat 太慢（实测 2 万+次），
    一次 scandir 建名→扩展索引后全部内存查询。与 shutil.which 对齐的三点：
    ①只索引文件；②POSIX 校验可执行位；③Windows 同名多扩展取 PATHEXT 优先级
    最高者——fnm/git 的无扩展 bash shim 蹭不掉真身 claude.exe/claude.cmd
    （否则版本探测时 CreateProcess 找不到可执行文件直接失败）。"""
    # ~/ 或绝对路径：直接验文件，不走 PATH 索引（clawd 同款思路：
    # 候选清单里混排 PATH 名与安装位全路径）
    if name.startswith(("~", "/")) or (os.name == "nt" and name[1:2] == ":"):
        p = Path(name).expanduser()
        return str(p) if p.is_file() and os.access(p, os.X_OK) else ""
    global _path_idx
    if _path_idx is None:
        idx: dict[str, str] = {}
        dirs = dict.fromkeys(os.environ.get("PATH", "").split(os.pathsep))
        for d in dirs:
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if not entry.is_file():
                                continue
                            if os.name == "posix" and not os.access(entry.path, os.X_OK):
                                continue
                        except OSError:
                            continue
                        root, ext = os.path.splitext(entry.name)
                        ext = ext.lower()
                        if ext and ext not in (".exe", ".com", ".cmd", ".bat"):
                            continue
                        old = idx.get(root.lower())
                        if old is None or _EXT_RANK.get(ext, 0) > _EXT_RANK.get(old, 0):
                            idx[root.lower()] = ext
            except OSError:
                continue
        _path_idx = idx
    hit = _path_idx.get(name.lower())
    if hit is None:
        return ""
    return name + hit


def _activity_days(cfgs: list[str]) -> int | None:
    """最近活动（天前）：各配置目录两层内最新 mtime。

    只看两层——徽标只需要"在用/闲置"级别的粒度，不能为它把
    8 万个文件的 ~/.dsh 深扫一遍（活动日志/会话通常落在浅层）。"""
    newest = 0.0
    for cfg in cfgs:
        root = Path(cfg).expanduser()
        try:
            newest = max(newest, root.stat().st_mtime)
            for entry in os.scandir(root):
                try:
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
        except OSError:
            continue
    return int((time.time() - newest) / 86400) if newest else None


def _cfg_hit(agent: str, cfg: str) -> bool:
    """配置目录命中判定；带哨兵要求的目录（CFG_SENTINELS）须内容佐证。"""
    p = Path(cfg).expanduser()
    if not p.exists():
        return False
    sentinels = CFG_SENTINELS.get(agent, {}).get(cfg)
    if sentinels:
        return any((p / s).exists() for s in sentinels)
    return True


def scan_local(timeout_s: int = 4, fresh: bool = False) -> list[Finding]:
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    cache = {} if fresh else _load_cache()
    versions_cache: dict = cache.get("versions", {})

    out: list[Finding] = []
    hits: list[tuple[Finding, str]] = []   # (finding, exe)
    for name, bins, cfgs, adapter, category in LOCAL_AGENTS:
        exe = next((w for b in bins if (w := _which(b))), "")
        hit_cfg = next((c for c in cfgs if _cfg_hit(name, c)), "")
        if exe or hit_cfg:
            hits.append((Finding(name, "local", True, category=category,
                                 detail=exe or hit_cfg, adapter=adapter,
                                 activity_days=_activity_days(cfgs)), exe))
        else:
            out.append(Finding(name, "local", False, category=category))
    # 周边工具：检出才列（未检出不占"未检出"名单——本来就不是智能体）
    for name, bins, cfgs, category, note in EXTRA_TOOLS:
        exe = next((w for b in bins if (w := _which(b))), "")
        hit_cfg = next((c for c in cfgs
                        if Path(c).expanduser().exists()), "")
        if exe or hit_cfg:
            hits.append((Finding(name, "local", True, category=category, hint=note,
                                 detail=exe or hit_cfg), exe))

    # 版本是锦上添花：并行探测 + 短超时 + 12h 落盘缓存（慢 CLI 如本地 hermes 实测 11s）
    def _ver(exe: str) -> str:
        if not exe:
            return ""
        hit = versions_cache.get(exe)
        if hit and time.time() - hit.get("ts", 0) < VERSION_TTL:
            return hit.get("version", "")
        try:
            r = subprocess.run([exe, "--version"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=timeout_s,
                               creationflags=NO_WINDOW)
            ver = ((r.stdout or r.stderr).strip().splitlines()[0][:40]
                   if (r.stdout or r.stderr).strip() else "")
        except Exception:  # 超时/编码崩溃/找不到一律降级为"无版本"，不让体检崩
            ver = ""
        versions_cache[exe] = {"version": ver, "ts": time.time()}
        return ver

    if hits:
        with ThreadPoolExecutor(max_workers=8) as ex:
            versions = list(ex.map(_ver, [h[1] for h in hits]))
        for (f, _), ver in zip(hits, versions, strict=True):
            f.version = ver
            out.append(f)
        cache["versions"] = versions_cache
        cache["updated_at"] = time.time()
        _save_cache(cache)
    out.sort(key=lambda f: (not f.found,
                            f.activity_days if f.activity_days is not None else 99999))
    return out


# 一次 SSH 打包全部探测（省往返），输出 name|version|path 供解析
_VM_PROBE = (
    "for spec in "
    "'hermes|~/.hermes/bin/hermes' "
    "'hermes|/usr/local/bin/hermes' "
    "'kylin-bot|/usr/bin/kylin-bot' "
    "'kylin-bot|/usr/local/bin/kylin-bot' "
    "'openclaw|~/.local/bin/openclaw'; do "
    "n=${spec%%|*}; p=${spec##*|}; "
    "[ -x $(eval echo $p) ] && "
    "echo \"$n|$($(eval echo $p) --version 2>/dev/null | head -1 | cut -c1-40)|$p\"; "
    "done; "
    "ls ~/.kylinbot/workspace/memory/brain.db "
    "~/.hermes/memories/MEMORY.md 2>/dev/null | head -2"   # R59：~/.hermes 笔误
)


def vm_is_self() -> bool:
    """VM_HOST 指向本机（openKylin 原生模式）：本机扫描已覆盖全部智能体，
    "评测机"段是 Windows 宿主 + 远端 VM 架构才需要的区分。
    UI（deploy-mode/adapter-status）也按它切换页面口径。"""
    host = os.environ.get("VM_HOST", "").strip()
    if not host:
        return False
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return True
    try:
        import socket
        if host == socket.gethostname():
            return True
        own = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None)}
        return host in own
    except OSError:
        return False


def scan_vm() -> tuple[list[Finding], str]:
    if vm_is_self():
        return [], "SAME-MACHINE"
    if not os.environ.get("VM_PASS"):
        return [], "缺 VM_PASS（.env 未加载），跳过评测机扫描"
    try:
        from memhall.adapters.remote import SshChannel
        ch = SshChannel()
        rc, out, _ = ch.run(_VM_PROBE, timeout=30)
    except Exception as e:  # SSH 不通是"警告"不是崩溃
        return [], f"评测机不可达: {str(e)[:120]}"
    if rc != 0:
        # R59：rc 非 0 不再静默当"未发现"——探测脚本自身失败（shell/权限）
        # 与"真没装"是两回事，报出来别让用户误诊
        return [], f"评测机探测命令失败 rc={rc}（shell/权限问题？）"

    findings: dict[str, Finding] = {}
    for line in out.splitlines():
        line = line.strip()
        if "|" in line and not line.startswith(("/", "~")):
            name, version, path = (line.split("|", 2) + ["", ""])[:3]
            adapter = next((a for n, _, a in VM_AGENTS if n == name), "")
            findings[name] = Finding(name, "vm", True, version.strip(),
                                     detail=path.strip(), adapter=adapter)
    extra = [ln for ln in out.splitlines()
             if ln.strip().startswith(("/", "~"))]
    if "brain.db" in "\n".join(extra):
        f = findings.setdefault("kylin-bot", Finding("kylin-bot", "vm", True,
                                                     adapter="kylinbot"))
        f.hint = "brain.db 记忆库在位"
    return list(findings.values()), ""


def adapter_availability(vm_probe=None) -> dict:
    """跑页下拉框的真实可跑性（UI /api/adapter-status 数据源，不装不骗人）。

    部署形态决定口径与可见车道（UI 描述随形态切换，一套形态一套列表）：
    - native：VM_HOST 指向本机 = openKylin 原生模式，本机即评测机。只列评测
      车道：hermes/kylinbot/openclaw 走回环 SSH 驱动本机智能体，label 按
      "本机"讲；可跑 = 本机检出二进制 + 回环通道（VM_HOST/VM_PASS）配好。
      本机直连车道不进列表（宿主机冒烟备胎，原生形态下列出只会重复扰视）。
    - remote：Windows 宿主 + 评测 VM。列本机直连车道（hermes-local/claude/
      qwen）+ VM 车道，后者可跑由 VM 内 SSH 实测决定（vm_probe 返回
      list[Finding]，可注入；缺省走 scan_vm）。
    mock 恒可用；未检出即 ok=False，由前端从下拉里剔除（完整名单与原因
    在体检页）。
    """
    native = vm_is_self()
    channel = bool(os.environ.get("VM_HOST") and os.environ.get("VM_PASS"))
    local = {k: bool(find_cli(*cands)) for k, cands in ADAPTER_CLI.items()}
    if native:
        # 原生模式只列评测车道（mock + 回环 SSH 三家）：本机直连车道
        # （hermes-local/claude/qwen）是宿主机无 SSH 时的冒烟备胎，在 VM 里
        # 与评测车道重复列出只会让人困惑"两个 hermes 有什么区别"（10-05 反馈）。
        # 原生侧只有一套车道，label 不带后缀（全是本机，后缀是噪音）
        return {"mode": "native", "adapters": {
            "mock": {"label": "mock（离线演示）", "ok": True},
            "hermes": {"label": "hermes", "ok": channel and local["hermes"]},
            "kylinbot": {"label": "kylinbot", "ok": channel and local["kylinbot"]},
            "openclaw": {"label": "openclaw", "ok": channel and local["openclaw"]},
        }}
    adapters = {
        "mock": {"label": "mock（离线演示）", "ok": True},
        "hermes-local": {"label": "hermes（本机直连）", "ok": local["hermes"]},
        "claude-local": {"label": "claude code（本机）", "ok": local["claude"]},
        "qwen-local": {"label": "qwen code（本机）", "ok": local["qwen"]},
        "opencode": {"label": "opencode（本机）", "ok": local["opencode"]},
    }
    vm = vm_probe() if vm_probe else scan_vm()[0]
    found = {f.adapter for f in vm if f.found and f.adapter}
    adapters.update({
        "hermes": {"label": "hermes（VM 连接）", "ok": "hermes" in found},
        "kylinbot": {"label": "kylinbot（VM 连接）", "ok": "kylinbot" in found},
        "openclaw": {"label": "openclaw（VM 连接）", "ok": "openclaw" in found},
    })
    return {"mode": "remote", "adapters": adapters}


def check_env() -> list[EnvCheck]:
    checks: list[EnvCheck] = []
    # R59：统一网关模式（GATEWAY_URL/GATEWAY_VM_URL）下 AGENT_LLM_* 允许留空——
    # 适配器凭据由网关派生，此前把该模式误报成"缺配置"
    gw_url = (os.environ.get("GATEWAY_URL") or os.environ.get("GATEWAY_VM_URL")
              or "").strip()
    need = ["AGENT_LLM_KEY", "AGENT_LLM_BASE_URL", "AGENT_LLM_MODEL"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing and gw_url:
        checks.append(EnvCheck("LLM 网关配置", True,
                               f"统一网关模式（{gw_url}），AGENT_LLM_* 留空正常"))
    elif missing:
        checks.append(EnvCheck("LLM 网关配置", False,
                               f"缺 {', '.join(missing)}（source .env）"))
    else:
        checks.append(EnvCheck("LLM 网关配置", True, "齐全"))
    probe_url = (os.environ.get("AGENT_LLM_BASE_URL") or gw_url).strip()
    if probe_url:
        u = urlparse(probe_url)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=4):
                checks.append(EnvCheck("LLM 网关可达", True, f"{host}:{port}"))
        except OSError as e:
            checks.append(EnvCheck("LLM 网关可达", False,
                                   f"{host}:{port} {type(e).__name__}"))
    host = os.environ.get("VM_HOST")
    if host and os.environ.get("VM_PASS"):
        try:
            with socket.create_connection((host, 22), timeout=4):
                checks.append(EnvCheck("评测机 SSH", True, f"{host}:22"))
        except OSError as e:
            checks.append(EnvCheck("评测机 SSH", False,
                                   f"{host}:22 {type(e).__name__}"))
    else:
        checks.append(EnvCheck("评测机 SSH", False,
                               "缺 VM_HOST/VM_PASS，跳过（本机适配器评测不需要）"))
    return checks


def run_doctor(scan_remote: bool = True, fresh: bool = False) -> DoctorReport:
    """三路并行体检（local/vm/env 互不阻塞，墙钟≈最慢一路）。"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_local = ex.submit(scan_local, 4, fresh)
        fut_vm = ex.submit(scan_vm) if scan_remote else None
        fut_env = ex.submit(check_env)
        rep = DoctorReport(local=fut_local.result())
        if fut_vm is not None:
            rep.vm, rep.vm_error = fut_vm.result()
        rep.env = fut_env.result()
    return rep


def render_doctor(rep: DoctorReport) -> str:
    lines = ["麟阁 MemHall · 环境体检", ""]

    lines.append("── 本机智能体 ──")
    agents = [f for f in rep.local if f.category in ("", "cli", "ide")]
    for f in agents:
        mark = "✓" if f.found else "·"
        ver = f"  {f.version}" if f.version else ""
        where = f"  ({f.detail})" if f.detail else ""
        act = (f"  · {f.activity_days}天前" if f.activity_days is not None else "")
        ad = f"  [适配器: -a {f.adapter}]" if f.adapter else \
            "  [无适配器，可按契约 01 定制]"
        if f.found:
            lines.append(f"  {mark} {f.name:<12}{ver}{where}{act}{ad}")
        else:
            lines.append(f"  {mark} {f.name:<12}（未检出）")
    extras = [f for f in rep.local if f.found and f.category in ("runtime", "tool")]
    if extras:
        lines.append("  · 另检出（非智能体）: "
                     + "、".join(f"{f.name}（{f.hint}）" for f in extras))
    lines.append("")

    lines.append("── 评测机智能体（openKylin VM）──")
    if rep.vm_error == "SAME-MACHINE":
        lines.append("  （openKylin 原生模式：本机即评测机，已并入上方本机扫描）")
    elif rep.vm_error:
        lines.append(f"  ⚠ {rep.vm_error}")
    elif rep.vm:
        for f in rep.vm:
            ver = f"  {f.version}" if f.version else ""
            hint = f"  · {f.hint}" if f.hint else ""
            lines.append(f"  ✓ {f.name:<12}{ver}  ({f.detail})"
                         f"  [适配器: -a {f.adapter}]{hint}")
    else:
        lines.append("  （未发现评测机智能体）")
    lines.append("")

    lines.append("── 评测环境就绪度 ──")
    for c in rep.env:
        lines.append(f"  {'✓' if c.ok else '✗'} {c.name:<12} {c.detail}")
    lines.append("")

    usable = rep.usable_adapters()
    if usable:
        lines.append(f"可跑: memhall run -a {usable[0]} -c cases/full -o runs"
                     f"（可用适配器: {', '.join(usable)}）")
    else:
        lines.append("无可用适配器；mock 始终可用: memhall run -a mock ...")
    return "\n".join(lines)
