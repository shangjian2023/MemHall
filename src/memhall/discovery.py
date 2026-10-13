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

2026-10-14 借鉴 stablyai/orca 探测体系（src/main/preflight/agent-detection.ts
+ src/main/startup/hydrate-shell-path.ts + src/shared/tui-agent-config.ts）：
- 登录 shell PATH 水合：桌面/SSH 起的进程拿不到 rc 追加的用户安装位，
  与其逐工具硬编码绝对路径，不如问一次 $SHELL（见 hydrate_shell_path）
- 版本探测多候选走查：PATH 同名多份（版本管理器 shim 遮真身）时逐个
  --version，钉住真正能跑的那份（orca #22975"pin to the copy that passed
  --version"）；--version 非零退出不再被误当版本号
- 注册表纠偏与扩容：对齐 orca 实测的各 CLI 真实安装名（kiro-cli/cn/
  traecli/qodercli/kimi-code/dsh-tui 等）并补齐其名单
"""

from __future__ import annotations

import json
import os
import re
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
    # 检出证据分级（1.3.1 用户实测教训：仅目录存在≠装过——~/.cursor 是 Cursor
    # IDE 的数据目录，天天被编辑器写，报"cursor-agent 在册·1 天前活跃"是误报）：
    #   "cli" = PATH 上检出 CLI 命令（确证，计入"在册"）
    #   "cfg" = 仅配置目录存在（疑似，不计入在册，UI 单列并说明）
    evidence: str = "cli"


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
    # 登录 shell 水合补进 PATH 的段（orca addedPathSegments 同款透明度：
    # 让用户知道"为什么新冒出一个智能体"是 PATH 补段的功劳）
    path_added: list[str] = field(default_factory=list)

    def usable_adapters(self) -> list[str]:
        names = {f.adapter for f in self.local + self.vm
                 if f.found and f.adapter and f.evidence != "cfg"}
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


def _js_pm_bin_dirs() -> list[str]:
    """JS 包管理器/版本管理器的全局 bin 布局（Linux/Win 环境特性差异，
    2026-10-09 用户点名适配化）：同一个 npm 系 CLI（claude/qwen/openclaw）
    在不同管理器下落点完全不同——npm 默认 prefix（win=%APPDATA%\\npm、
    linux 常见 ~/.local/bin 或 /usr/local/bin）、pnpm（PNPM_HOME，缺省
    ~/.local/share/pnpm、win %LOCALAPPDATA%\\pnpm）、volta（~/.volta/bin）。
    nvm/fnm 不列：版本目录带版本号没法静态枚举，且它们的激活模式就是把
    bin 塞进 shell PATH（激活了就一定能 which 到）。探测是毫秒级 stat，
    宁可多列不可漏。"""
    pnpm = os.environ.get("PNPM_HOME", "")
    dirs = [pnpm] if pnpm else []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        lad = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            dirs.append(f"{appdata}/npm")          # npm 默认 prefix（claude.cmd 在这）
        if lad:
            dirs += [f"{lad}/pnpm", f"{lad}/Volta/bin"]
    else:
        dirs += ["~/.local/share/pnpm", "~/.volta/bin"]
    return dirs


# kylin-agent 系统智能体后端运行时（Hermes fork）：env 覆写 > PATH 名 >
# apt 落点 > pip --user > Gitee README 部署路径（clone agent-runtime + uv venv）。
# 只认 kylin-agent-runtime——GUI 前端叫 kylin-agent（apt 同名包），装了它
# 不等于装了被测后端，绝不进候选（F 专项：discovery 与适配器口径一致）。
def _kylin_agent_candidates() -> list[str]:
    env = os.environ.get("MEMHALL_KYLIN_AGENT_BIN", "").strip()
    cands = ["kylin-agent-runtime", "/usr/bin/kylin-agent-runtime",
             "/usr/local/bin/kylin-agent-runtime",
             "~/.local/bin/kylin-agent-runtime",
             "~/agent-runtime/.venv/bin/kylin-agent-runtime"]
    return ([env, *cands] if env else cands)


ADAPTER_CLI: dict[str, list[str]] = {
    "hermes": _hermes_candidates(),
    "claude": ["claude", "~/.local/bin/claude",
               *[f"{d}/claude" for d in _js_pm_bin_dirs()]],
    "qwen": ["qwen", "~/.local/bin/qwen",
             *[f"{d}/qwen" for d in _js_pm_bin_dirs()]],
    # openKylin 侧智能体：绝对路径兜底（SSH/桌面起的进程 PATH 不含用户安装位）
    "kylinbot": ["kylin-bot", "/usr/bin/kylin-bot", "/usr/local/bin/kylin-bot"],
    "kylin-agent": _kylin_agent_candidates(),
    "openclaw": ["openclaw", "~/.local/bin/openclaw",
                 *[f"{d}/openclaw" for d in _js_pm_bin_dirs()]],
    "opencode": ["opencode"],   # R57：UI 下拉曾漏 opencode（适配器早已注册）
    # node 不是适配器，是 npm 系 CLI 的运行时依赖——适配器 PATH 加固
    # （claude_local._sandbox_env）借这份清单找 node 所在目录
    "node": ["node", *[f"{d}/node" for d in _js_pm_bin_dirs()]],
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
# ④可执行名对齐 stablyai/orca tui-agent-config.ts 实测口径（2026-10-14）：
#   安装器真实落名与直觉不同的都纠偏——kiro 装 kiro-cli、continue 装 cn、
#   TRA CN 装 traecli（trae-cli 是字节开源 trae-agent，另一家）、qoder 装
#   qodercli、kimi 别名 kimi-code、dsh 别名 dsh-tui/dst、aug 装 auggie、
#   mistral-vibe 装 vibe、rovo-dev 无独立二进制（acli 子命令）、antigravity 装 agy。
#   裸 cursor 是 Cursor IDE 启动器不是 agent CLI，从 cursor-agent 候选剔除
#   （PATH 侧同类误报，与 1.3.1 的 ~/.cursor 目录误报同源教训）
# (名字, 可执行候选, 配置目录候选, 适配器名, 类别)
LOCAL_AGENTS: list[tuple[str, list[str], list[str], str, str]] = [
    # --- CLI 编程智能体 ---
    ("claude-code", ADAPTER_CLI["claude"], ["~/.claude"], "claude-local", "cli"),
    ("codex", ["codex"], ["~/.codex"], "", "cli"),
    ("dsh (DeepSeek Harness)", ["dsh", "dsh-tui", "dst"], ["~/.dsh"], "", "cli"),
    ("gemini-cli", ["gemini"], ["~/.gemini"], "", "cli"),
    ("opencode", ADAPTER_CLI["opencode"], ["~/.config/opencode"], "opencode", "cli"),
    ("opencode2", ["opencode2"], [], "", "cli"),  # orca：v2 装独立二进制（适配器驱动的是 v1 CLI，不接线）
    ("mimocode", ["mimocode"], ["~/.config/mimocode"], "", "cli"),
    ("qwen-code", ADAPTER_CLI["qwen"], ["~/.qwen"], "", "cli"),
    ("qwenpaw", ["qwenpaw"], ["~/.qwenpaw"], "", "cli"),
    ("zcode", ["zcode"], ["~/.zcode"], "", "cli"),
    ("pi", ["pi"], ["~/.pi/agent"], "", "cli"),
    ("grok-build", ["grok", "grokbuild"], ["~/.grok"], "", "cli"),
    ("workbuddy", ["workbuddy"], ["~/.workbuddy-ai", "~/.workbuddy"], "", "cli"),
    ("kimi", ["kimi", "kimi-code"], ["~/.kimi-code", "~/.kimi"], "", "cli"),
    ("kiro", ["kiro-cli", "kiro"], ["~/.kiro"], "", "cli"),
    ("cursor-agent", ["cursor-agent"], ["~/.cursor"], "", "cli"),
    ("copilot-cli", ["copilot"], ["~/.copilot"], "", "cli"),
    ("codebuddy", ["codebuddy"], ["~/.codebuddy"], "", "cli"),
    ("openclaw", ADAPTER_CLI["openclaw"], ["~/.openclaw"], "openclaw", "cli"),
    ("qoder", ["qodercli", "qoder"], ["~/.qoder"], "", "cli"),
    ("qoderwork", ["qoderwork"], ["~/.qoderwork"], "", "cli"),
    ("qwenwork", ["qwenwork"], ["~/.QwenWorkCN"], "", "cli"),
    ("aider", ["aider"], ["~/.aider.conf.yml"], "", "cli"),
    ("goose", ["goose"], ["~/.config/goose"], "", "cli"),
    ("crush", ["crush"], ["~/.config/crush"], "", "cli"),
    ("hermes", ADAPTER_CLI["hermes"], ["~/.hermes"], "hermes-local", "cli"),
    ("kylin-bot", ADAPTER_CLI["kylinbot"], ["~/.kylinbot"], "kylinbot", "cli"),
    ("kylin-agent", ADAPTER_CLI["kylin-agent"], ["~/.kylin-agent-runtime"],
     "kylin-agent", "cli"),
    # --- 以下为 orca 名单对齐新增（2026-10-14；主流可装、openKylin 生态外）---
    ("amp", ["amp"], [], "", "cli"),
    ("auggie", ["auggie"], [], "", "cli"),
    ("mistral-vibe", ["vibe", "mistral-vibe"], [], "", "cli"),
    ("droid", ["droid"], [], "", "cli"),
    ("antigravity", ["agy"], [], "", "cli"),
    ("muse", ["muse"], [], "", "cli"),
    ("devin", ["devin"], [], "", "cli"),
    ("rovo-dev", ["acli"], [], "", "cli"),
    ("command-code", ["command-code"], [], "", "cli"),
    ("jcode", ["jcode"], [], "", "cli"),
    # --- IDE / 编辑器内智能体 ---
    ("cline", ["cline"], ["~/.cline"], "", "ide"),
    ("continue", ["cn", "continue"], ["~/.continue"], "", "ide"),
    ("lingma (通义灵码)", ["lingma"], ["~/.lingma"], "", "ide"),
    ("trae", ["traecli", "trae", "trea"], ["~/.trae-cn", "~/.trae-aicc"], "", "ide"),
    ("marscode", ["marscode"], ["~/.marscode"], "", "ide"),
    ("kilocode", ["kilo", "kilocode"], ["~/.kilocode"], "", "ide"),
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
    # node 是 npm 系智能体（claude/qwen/openclaw）的硬依赖，缺了就是
    # "检测到 CLI 但发车即 env node 扑空"（2026-10-08 VM openclaw 实录）
    ("node", ADAPTER_CLI["node"], [], "runtime", "JS 运行时（npm 系智能体依赖）"),
    ("cc-switch", ["ccswitch", "cc-switch"], ["~/.cc-switch"], "tool", "智能体配置切换器"),
    ("cherry-studio", ["cherry"], ["~/.cherrystudio"], "tool", "聊天客户端"),
]

VM_AGENTS: list[tuple[str, list[str], str]] = [
    ("hermes", ["~/.hermes/bin/hermes", "/usr/local/bin/hermes"], "hermes"),
    ("kylin-bot", ["/usr/bin/kylin-bot", "/usr/local/bin/kylin-bot"], "kylinbot"),
    ("kylin-agent-runtime",
     ["/usr/bin/kylin-agent-runtime", "/usr/local/bin/kylin-agent-runtime",
      "~/.local/bin/kylin-agent-runtime",
      "~/agent-runtime/.venv/bin/kylin-agent-runtime"], "kylin-agent"),
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


# PATH 名→[(目录, 最优扩展)]（PATH 目录序，进程内一次构建）
_path_idx: dict[str, list[tuple[str, str]]] | None = None
_EXT_RANK = {".exe": 4, ".com": 3, ".cmd": 2, ".bat": 1, "": 0}


def _is_pathlike(name: str) -> bool:
    """候选是否 ~/、绝对路径形态（直接验文件，不走 PATH 索引）。"""
    return name.startswith(("~", "/")) or (os.name == "nt" and name[1:2] == ":")


def _build_path_index() -> dict[str, list[tuple[str, str]]]:
    """一次 scandir 建 名→[(目录, 扩展)] 索引（目录序，同目录内同名只留
    PATHEXT 优先级最高的扩展）。与 shutil.which 对齐的三点：①只索引文件；
    ②POSIX 校验可执行位；③Windows 同名多扩展取优先级最高者——
    fnm/git 的无扩展 bash shim 蹭不掉真身 claude.exe/claude.cmd
    （否则版本探测时 CreateProcess 找不到可执行文件直接失败）。"""
    idx: dict[str, list[tuple[str, str]]] = {}
    dirs = dict.fromkeys(os.environ.get("PATH", "").split(os.pathsep))
    for d in dirs:
        best: dict[str, str] = {}   # 本目录内 名→最优扩展
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
                    old = best.get(root.lower())
                    if old is None or _EXT_RANK.get(ext, 0) > _EXT_RANK.get(old, 0):
                        best[root.lower()] = ext
        except OSError:
            continue
        for root, ext in best.items():
            idx.setdefault(root, []).append((d, ext))
    return idx


def _which(name: str) -> str:
    """按序探测 CLI：PATH 名或 ~/、绝对路径均可，返回首个命中（""=未找到）。
    PATH 名命中返回裸名（子进程借 PATH 自行解析，与 shutil.which 展示口径
    一致；全路径候选见 _which_all）。"""
    # ~/ 或绝对路径：直接验文件（clawd 同款思路：候选清单里混排 PATH 名
    # 与安装位全路径）。win 下 npm 布局同名多扩展（无扩展 sh 脚本与 .cmd
    # 并存），取 PATHEXT 优先级最高者——裸 sh 脚本喂给 subprocess 会
    # ENOEXEC（2026-10-09 包管理器布局适配补齐，与 PATH 索引分支同一条规矩）
    if _is_pathlike(name):
        p = Path(name).expanduser()
        if os.name == "nt":
            for ext in (".cmd", ".exe", ".bat", ""):
                c = Path(str(p) + ext)
                if c.is_file() and os.access(c, os.X_OK):
                    return str(c)
            return ""
        return str(p) if p.is_file() and os.access(p, os.X_OK) else ""
    global _path_idx
    if _path_idx is None:
        _path_idx = _build_path_index()
    hits = _path_idx.get(name.lower()) or []
    return name + hits[0][1] if hits else ""


def _which_all(name: str, limit: int = 3) -> list[str]:
    """同名多份的全路径候选，按 PATH 目录序返回。版本探测逐个试它（学 orca
    findRunnableLocalCommand）：PATH 首命中的可能是版本管理器死 shim，
    #22975 教训——用全路径钉住具体那份能跑的，绕开 PATH 重新解析又落回 shim。"""
    if _is_pathlike(name):
        w = _which(name)
        return [w] if w else []
    global _path_idx
    if _path_idx is None:
        _path_idx = _build_path_index()
    hits = _path_idx.get(name.lower()) or []
    return [os.path.join(d, name + ext) for d, ext in hits[:limit]]


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


# ---- 登录 shell PATH 水合（借 orca hydrate-shell-path，2026-10-14）----
# 痛点：GUI/桌面/SSH 起的进程拿不到 rc 追加的用户安装位（~/.local/bin、
# pnpm home、cargo bin……），此前只能逐工具硬编码绝对路径兜底（如 kylinbot）。
# orca 的通用解法：与其穷举每个工具的安装位，不如 spawn 一次用户的登录
# shell 问它"你眼里的 PATH 是什么"。rc 横幅/彩色提示符用首尾分隔符隔出
# PATH 本体；慢 rc 有超时兜底，失败静默降级回进程 PATH。

_PATH_PROBE_DELIM = "__MEMHALL_PATH__"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_hydrated: list[str] | None = None   # None=未水合；[]=跑过但没补进新段


def _parse_probe_path(out: str) -> list[str]:
    """分隔符之间取 PATH 段：剥 ANSI（rc 横幅/彩色提示符泄进 stdout 的转义），
    保序去重（PATH 首匹配优先，顺序就是语义——orca 同款）。"""
    cleaned = _ANSI_RE.sub("", out)
    first = cleaned.find(_PATH_PROBE_DELIM)
    if first < 0:
        return []
    start = first + len(_PATH_PROBE_DELIM)
    second = cleaned.find(_PATH_PROBE_DELIM, start)
    if second < 0:
        return []
    segs = [s for s in (t.strip() for t in
                        cleaned[start:second].split(os.pathsep)) if s]
    return list(dict.fromkeys(segs))


def _merge_path_front(segs: list[str]) -> list[str]:
    """shell 段整体提到 PATH 最前（保序、去重），返回新增的段。
    前置而非追加：用户 shell 的解析顺序优先于启动时继承的 PATH
    （orca：shell-provided entries must win over packaged fallbacks）。"""
    cur = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    curset = set(cur)
    shellset = set(segs)
    os.environ["PATH"] = os.pathsep.join(
        segs + [p for p in cur if p not in shellset])
    return [s for s in segs if s not in curset]


def hydrate_shell_path(fresh: bool = False, timeout_s: int = 6) -> list[str]:
    """问登录 shell 要 PATH，把 shell 段提到进程 PATH 最前（进程内缓存一次；
    fresh=True 重问——"刚装了新 CLI 但探测不到"的刷新语义，orca
    refreshShellPathAndDetectAgents 同款）。仅 posix：win 下 memhall 从终端
    起、PATH 完整，npm/pnpm 布局另有 _js_pm_bin_dirs 候选。成功后 PATH 索引
    作废重建，本进程后续 find_cli/_which 全部受益。返回本次新增的段。"""
    global _hydrated, _path_idx
    import subprocess
    if _hydrated is None or fresh:
        added: list[str] = []
        if os.name == "posix":
            # -i 走交互 rc（.bashrc/.zshrc 才是装 CLI 改 PATH 的地方）；
            # 无 tty 下 bash 的 job control 警告只进 stderr，不脏 stdout。
            # fish 的 "$PATH" 是空格分隔 list，会解析失败→静默降级（可接受）。
            shell = os.environ.get("SHELL") or "/bin/bash"
            probe = (f"printf '%s' '{_PATH_PROBE_DELIM}'; printf '%s' \"$PATH\"; "
                     f"printf '%s' '{_PATH_PROBE_DELIM}'")
            try:
                r = subprocess.run([shell, "-ilc", probe], capture_output=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=timeout_s, stdin=subprocess.DEVNULL,
                                   creationflags=NO_WINDOW)
                segs = _parse_probe_path(r.stdout or "")
            except Exception:   # 超时/无 shell/编码：降级回进程 PATH，水合不该拖垮体检
                segs = []
            if segs:
                added = _merge_path_front(segs)
                if added:
                    _path_idx = None   # 索引按旧 PATH 建的，作废重建
        _hydrated = added
    return _hydrated


def last_path_hydration() -> list[str]:
    """最近一次水合补进 PATH 的段（doctor 报告/UI 端点展示用）。"""
    return list(_hydrated or [])


def scan_local(timeout_s: int = 4, fresh: bool = False) -> list[Finding]:
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    hydrate_shell_path(fresh)   # 登录 shell 视角的 PATH（进程内一次；fresh 破缓存）
    cache = {} if fresh else _load_cache()
    versions_cache: dict = cache.get("versions", {})

    out: list[Finding] = []
    hits: list[tuple[Finding, list[str]]] = []   # (finding, 版本探测候选表)
    for name, bins, cfgs, adapter, category in LOCAL_AGENTS:
        hit = next(((w, b) for b in bins if (w := _which(b))), ("", ""))
        exe, bin_name = hit
        hit_cfg = next((c for c in cfgs if _cfg_hit(name, c)), "")
        if exe or hit_cfg:
            hits.append((Finding(name, "local", True, category=category,
                                 detail=exe or hit_cfg, adapter=adapter,
                                 activity_days=_activity_days(cfgs),
                                 evidence="cli" if exe else "cfg"),
                         _which_all(bin_name) or ([exe] if exe else [])))
        else:
            out.append(Finding(name, "local", False, category=category))
    # 周边工具：检出才列（未检出不占"未检出"名单——本来就不是智能体）
    for name, bins, cfgs, category, note in EXTRA_TOOLS:
        hit = next(((w, b) for b in bins if (w := _which(b))), ("", ""))
        exe, bin_name = hit
        hit_cfg = next((c for c in cfgs
                        if Path(c).expanduser().exists()), "")
        if exe or hit_cfg:
            hits.append((Finding(name, "local", True, category=category, hint=note,
                                 detail=exe or hit_cfg,
                                 evidence="cli" if exe else "cfg"),
                         _which_all(bin_name) or ([exe] if exe else [])))

    # 版本是锦上添花：并行探测 + 短超时 + 12h 落盘缓存（慢 CLI 如本地 hermes 实测 11s）。
    # 多候选逐个试（orca findRunnableLocalCommand）：PATH 首命中可能是版本
    # 管理器死 shim，钉住真正能跑 --version 的那份；--version 非零退出不再
    # 把报错首行误当版本号（此前旧实现直接取 stdout/stderr 第一行）。
    def _ver(cands: list[str]) -> tuple[str, str]:
        """返回 (版本, 答话的那份可执行)；版本探测失败降级 ("", "")。"""
        if not cands:
            return "", ""
        key = cands[0]   # 缓存键用首候选：PATH 布局变了键自然变
        hit = versions_cache.get(key)
        if hit and time.time() - hit.get("ts", 0) < VERSION_TTL:
            return hit.get("version", ""), key
        ver, ran = "", ""
        for exe in cands:
            try:
                r = subprocess.run([exe, "--version"], capture_output=True,
                                   encoding="utf-8", errors="replace", timeout=timeout_s,
                                   creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired:
                break   # 超时≠坏（慢 CLI 实测 11s），别把预算烧在重试上
            except Exception:   # ENOEXEC/找不到：试下一份候选
                continue
            out_put = (r.stdout or r.stderr).strip()
            if getattr(r, "returncode", 0) == 0 and out_put:
                ver = out_put.splitlines()[0][:40]
                ran = exe
                break
        versions_cache[key] = {"version": ver, "ts": time.time()}
        return ver, ran

    if hits:
        with ThreadPoolExecutor(max_workers=8) as ex:
            versions = list(ex.map(_ver, [h[1] for h in hits]))
        for (f, _), (ver, ran) in zip(hits, versions, strict=True):
            f.version = ver
            if ver and ran:
                f.detail = ran   # 钉到答话的那份（orca #22975）
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
    "'kylin-agent-runtime|/usr/bin/kylin-agent-runtime' "
    "'kylin-agent-runtime|/usr/local/bin/kylin-agent-runtime' "
    "'kylin-agent-runtime|~/.local/bin/kylin-agent-runtime' "
    "'kylin-agent-runtime|~/agent-runtime/.venv/bin/kylin-agent-runtime' "
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
    - native：VM_HOST 指向本机 = openKylin 原生模式，本机即评测机。列
      mock + 三家评测车道（hermes/kylinbot/openclaw 本机直跑——LocalChannel
      不绕 SSH，2026-10 架构收尾；拨钟等 sudo 操作仍需 VM_PASS）+
      claude-local（例外：本机安装且无重复车道，2026-10-09 补），label 按
      "本机"讲；可跑 = 本机检出二进制 + VM_HOST/VM_PASS 配好。其余本机
      直连车道不进列表（宿主机冒烟备胎，原生形态下列出只会重复扰视）。
    - remote：Windows 宿主 + 评测 VM。列本机直连车道（hermes-local/claude/
      qwen）+ VM 车道，后者可跑由 VM 内 SSH 实测决定（vm_probe 返回
      list[Finding]，可注入；缺省走 scan_vm）。
    mock 恒可用；未检出即 ok=False，由前端从下拉里剔除（完整名单与原因
    在体检页）。
    """
    native = vm_is_self()
    channel = bool(os.environ.get("VM_HOST") and os.environ.get("VM_PASS"))
    local = {k: bool(find_cli(*cands)) for k, cands in ADAPTER_CLI.items()}
    # claude 车道额外门槛：统一网关模式下 claude-local 起跑即拒（anthropic
    # 协议进不了 openai 网关面，适配器里显式报错）——ok=False 让下拉直接
    # 不列，好过发车才撞墙。GATEWAY_EXEMPT=claude-local 为明示豁免
    # （混合形态：其余车道走网关、claude 直连 anthropic）
    from memhall.gateway import gateway_settings
    exempt = "claude-local" in {
        s.strip() for s in os.environ.get("GATEWAY_EXEMPT", "").split(",")
        if s.strip()}
    claude_ok = local["claude"] and (exempt or not gateway_settings("claude-local"))
    if native:
        # 原生模式列 mock + 回环 SSH 三家；claude-local 例外（2026-10-09 补）：
        # openKylin 上 claude code 是本机安装、无同名评测车道（hermes 车道驱动
        # 的是 hermes），不列反而丢一个真实被测对象。其余本机直连车道
        # （hermes-local/qwen）仍不进列表——与评测车道重复扰视（10-05 反馈）。
        # 原生侧只有一套车道，label 不带后缀（全是本机，后缀是噪音）
        return {"mode": "native", "adapters": {
            "mock": {"label": "mock（离线演示）", "ok": True},
            "claude-local": {"label": "claude code", "ok": claude_ok},
            "hermes": {"label": "hermes", "ok": channel and local["hermes"]},
            "kylinbot": {"label": "kylinbot", "ok": channel and local["kylinbot"]},
            "kylin-agent": {"label": "kylin-agent", "ok": channel and local["kylin-agent"]},
            "openclaw": {"label": "openclaw", "ok": channel and local["openclaw"]},
        }}
    adapters = {
        "mock": {"label": "mock（离线演示）", "ok": True},
        "hermes-local": {"label": "hermes（本机直连）", "ok": local["hermes"]},
        "claude-local": {"label": "claude code（本机）", "ok": claude_ok},
        "qwen-local": {"label": "qwen code（本机）", "ok": local["qwen"]},
        "opencode": {"label": "opencode（本机）", "ok": local["opencode"]},
    }
    vm = vm_probe() if vm_probe else scan_vm()[0]
    found = {f.adapter for f in vm if f.found and f.adapter}
    adapters.update({
        "hermes": {"label": "hermes（VM 连接）", "ok": "hermes" in found},
        "kylinbot": {"label": "kylinbot（VM 连接）", "ok": "kylinbot" in found},
        "kylin-agent": {"label": "kylin-agent（VM 连接）",
                        "ok": "kylin-agent" in found},
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
        rep.path_added = last_path_hydration()
    return rep


def render_doctor(rep: DoctorReport) -> str:
    lines = ["麟阁 MemHall · 环境体检", ""]

    lines.append("── 本机智能体 ──")
    if rep.path_added:
        lines.append(f"  · PATH 水合：登录 shell 补 {len(rep.path_added)} 段"
                     f"（{rep.path_added[0]}{' 等' if len(rep.path_added) > 1 else ''}）")
    agents = [f for f in rep.local if f.category in ("", "cli", "ide")]
    for f in agents:
        if not f.found:
            lines.append(f"  · {f.name:<12}（未检出）")
            continue
        if f.evidence == "cfg":
            # 疑似：目录可能是别的工具建的（~/.cursor 属于 Cursor IDE），
            # 不能报"在册"（1.3.1 用户实测误报教训）
            lines.append(f"  ? {f.name:<12}  疑似：仅配置目录（{f.detail}），"
                         "PATH 未检出 CLI，不计入在册")
            continue
        ver = f"  {f.version}" if f.version else ""
        where = f"  ({f.detail})" if f.detail else ""
        act = (f"  · {f.activity_days}天前" if f.activity_days is not None else "")
        ad = f"  [适配器: -a {f.adapter}]" if f.adapter else "  [暂无适配器]"
        lines.append(f"  ✓ {f.name:<12}{ver}{where}{act}{ad}")
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
