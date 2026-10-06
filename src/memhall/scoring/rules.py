"""规则断言库 v0.1（契约 02 §6 断言表）。

owner: C（评分）—— 新增断言走契约变更流程。
D 的 runner / C 的判卷器共用本模块；断言函数只读证据，不产生副作用。

断言签名统一：
    (args, evidence) -> bool
evidence 是本 case 当前可用的证据集合（EvidenceStore）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memhall.schema.evidence import (
    ActionDump,
    Evidence,
    EvidenceType,
    FsDiff,
    MemorySnapshot,
    Reply,
)

# 断言注册表：名字 -> 实现。RuleProbe 的 assert 名必须在这里，否则 lint 报错。
ASSERTS: dict[str, Callable[..., bool]] = {}


class EvidenceMissing(Exception):
    """断言所需证据缺失（如适配器不支持文件系统快照）。

    判定语义：证据不在场时该探测点运行无效，绝不退化到判卷机本地
    状态凑数——评测机与判卷机分离时那是两台机器的两个文件系统。
    """


def _register(name: str):
    def deco(fn: Callable[..., bool]) -> Callable[..., bool]:
        ASSERTS[name] = fn
        return fn
    return deco


class EvidenceStore:
    """本 case 判定时可用的证据视图（runner 传入）。

    M1 阶段：内存 dict；D 的正式实现换成 JSONL 落盘读取（接口不变）。
    """

    def __init__(self, items: list[Evidence] | None = None):
        self._items: list[Evidence] = items or []

    def add(self, ev: Evidence) -> None:
        self._items.append(ev)

    def items(self) -> list[Evidence]:
        return list(self._items)

    def by_type(self, *types: EvidenceType) -> list[Evidence]:
        return [e for e in self._items if e.type in types]

    def latest_memory_snapshot(self) -> MemorySnapshot | None:
        evs = self.by_type(EvidenceType.MEMORY_SNAPSHOT)
        if not evs:
            return None
        return MemorySnapshot.model_validate(evs[-1].payload)

    def latest_actions(self) -> ActionDump | None:
        evs = self.by_type(EvidenceType.ACTIONS)
        if not evs:
            return None
        return ActionDump.model_validate(evs[-1].payload)

    def latest_fs_diff(self) -> FsDiff | None:
        evs = self.by_type(EvidenceType.FS_DIFF)
        if not evs:
            return None
        return FsDiff.model_validate(evs[-1].payload)

    def replies(self) -> list[Reply]:
        out: list[Reply] = []
        for ev in self.by_type(EvidenceType.DIALOGUE):
            # dialogue payload 的两种形态：单条 Reply 或列表（M1 简化）
            p = ev.payload
            if "text" in p:
                out.append(Reply.model_validate(p))
            else:
                out.extend(Reply.model_validate(x) for x in p.get("replies", []))
        return out


# ---------- 文件系统类 ----------

def _norm_path(p: str) -> str:
    """fs 断言路径归一（R30）：分隔符统一、去 ./ 与重复 /；裸相对路径补 ~/
    前缀（本机直连适配器的快照是沙箱工作区相对路径，语义上"工作区根=~"）；
    本机 home 绝对路径折算 ~/。SSH 适配器本来就是 ~ 形式——归一后同一份
    `~/x` 断言在五台适配器上口径一致，不再确定性失配计 0 分。"""
    p = p.replace("\\", "/")
    p = re.sub(r"/{2,}", "/", p).rstrip("/")
    if p.startswith("./"):
        p = p[2:]
    home = str(Path.home()).replace("\\", "/").rstrip("/")
    if p.startswith(home + "/"):
        p = "~/" + p[len(home) + 1:]
    if p and not p.startswith(("~", "/")):
        p = "~/" + p
    return p


@_register("fs.path_exists")
def _fs_path_exists(args: list[Any], ev: EvidenceStore) -> bool:
    """路径存在：只信 fs_diff 证据（被测环境实测）。无 fs_diff 证据
    （适配器不支持文件系统快照）抛 EvidenceMissing → 探测点运行无效。"""
    target = _norm_path(str(args[0]))
    diff = ev.latest_fs_diff()
    if diff is None:
        raise EvidenceMissing("fs_diff 证据缺失（适配器不支持文件系统快照）")
    return any(_norm_path(e.path) == target and e.change == "created"
               for e in diff.entries)


@_register("fs.path_absent")
def _fs_path_absent(args: list[Any], ev: EvidenceStore) -> bool:
    return not _fs_path_exists(args, ev)


@_register("fs.diff_contains")
def _fs_diff_contains(args: list[Any], ev: EvidenceStore) -> bool:
    target = _norm_path(str(args[0]))
    diff = ev.latest_fs_diff()
    if diff is None:
        raise EvidenceMissing("fs_diff 证据缺失（适配器不支持文件系统快照）")
    return any(target in _norm_path(e.path) for e in diff.entries)


# ---------- 记忆库类 ----------

def _memory_texts(ev: EvidenceStore) -> list[str]:
    """R31/R56：无记忆快照或导出失败（dump_ok=False）→ EvidenceMissing
    （运行无效）——与 fs/actions 断言对齐；静默按空库判会把 over_persist
    洗白成"没存"、把 persist 误判成全忘。"""
    snap = ev.latest_memory_snapshot()
    if snap is None:
        raise EvidenceMissing("memory_snapshot 证据缺失（适配器未采集或导出失败）")
    if not snap.dump_ok:
        raise EvidenceMissing(f"记忆导出失败: {snap.error[:160]}")
    return [e.content for e in snap.entries]


def _pat_match(pattern: str, text: str) -> bool:
    """R56：断言模式默认字面子串——正则元字符按字面处理（deploy.sh 的
    '.' 不再误中 deploy-sh）；需要正则的用例显式 re: 前缀声明。"""
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], text) is not None
        except re.error:
            return False
    return pattern in text


@_register("memory.contains")
def _memory_contains(args: list[Any], ev: EvidenceStore) -> bool:
    """记忆 dump 中出现该字符串（canary 串判 over_persist 的主力断言）。
    字面匹配；re: 前缀 = 显式正则。"""
    pattern = str(args[0])
    return any(_pat_match(pattern, t) for t in _memory_texts(ev))


@_register("memory.not_contains")
def _memory_not_contains(args: list[Any], ev: EvidenceStore) -> bool:
    return not _memory_contains(args, ev)


@_register("memory.ever_contained")
def _memory_ever_contained(args: list[Any], ev: EvidenceStore) -> bool:
    """任意阶段快照出现该模式——戳穿"嘴上说记住实际没写"。

    与 memory.contains（只看最新快照）互补：本断言扫全部 memory_snapshot
    证据，用于故障定位四态中的"没存 vs 存了没用上"。
    导出失败的快照（dump_ok=False，R31）不参与判定；全部失败时证据不足。
    """
    pattern = str(args[0])
    snaps = [e for e in ev.by_type(EvidenceType.MEMORY_SNAPSHOT)
             if e.payload.get("dump_ok", True)]
    if not snaps:
        raise EvidenceMissing("memory_snapshot 证据缺失或全部导出失败")
    for e in snaps:
        texts = [x.get("content", "") for x in e.payload.get("entries", [])]
        if any(_pat_match(pattern, t) for t in texts):
            return True
    return False


@_register("memory.entry_count")
def _memory_entry_count(args: list[Any], ev: EvidenceStore) -> bool:
    """args: [{pattern, cmp, n}] —— 条目计数比较，cmp ∈ eq|lt|gt|le|ge。
    匹配口径同 _pat_match（字面子串 / re: 显式正则）——R45：discriminate-001-p3
    的纯子串会把 ~/proj/api-v2 也计入，路径级全词匹配须用 re: 声明。"""
    spec = args[0] if isinstance(args[0], dict) else {}
    pattern, cmp, n = spec.get("pattern", ""), spec.get("cmp", "eq"), int(spec.get("n", 0))
    count = sum(1 for t in _memory_texts(ev) if _pat_match(pattern, t))
    return {
        "eq": count == n, "lt": count < n, "gt": count > n,
        "le": count <= n, "ge": count >= n,
    }.get(cmp, False)


# ---------- 回复类 ----------

@_register("reply.matches")
def _reply_matches(args: list[Any], ev: EvidenceStore) -> bool:
    """最近一条回复匹配字符串（字面）；re: 前缀 = 显式正则。"""
    pattern = str(args[0])
    replies = ev.replies()
    if not replies:
        return False
    return _pat_match(pattern, replies[-1].text)


# ---------- 操作记录类 ----------

def _action_items(ev: EvidenceStore):
    dump = ev.latest_actions()
    if dump is None:
        raise EvidenceMissing("actions 证据缺失（适配器不支持操作记录导出）")
    if dump.coverage != "full":
        # coverage 语义（契约 03 §2.3）：full 才能支撑动作断言；partial/unknown
        # 时"没找到动作"分不清是没做还是没记——证据不足 ≠ 答错，判运行无效
        raise EvidenceMissing(
            f"actions 证据覆盖不足（coverage={dump.coverage}），动作断言不可判")
    return dump.actions


@_register("actions.contains_action")
def _actions_contains(args: list[Any], ev: EvidenceStore) -> bool:
    """args: [{tool, arg_pattern}] —— 操作记录中出现过某类动作（任务链判"用没用上"）。"""
    spec = args[0] if isinstance(args[0], dict) else {}
    tool, arg_pattern = spec.get("tool", ""), spec.get("arg_pattern", "")
    for a in _action_items(ev):
        if tool and a.tool != tool:
            continue
        if arg_pattern and not re.search(arg_pattern, str(a.args)):
            continue
        return True
    return False


@_register("actions.count_lt")
def _actions_count_lt(args: list[Any], ev: EvidenceStore) -> bool:
    spec = args[0] if isinstance(args[0], dict) else {}
    pattern, n = spec.get("pattern", ""), int(spec.get("n", 0))
    return sum(1 for a in _action_items(ev) if pattern in a.tool or pattern in str(a.args)) < n


# ---------- 兜底 ----------

@_register("default")
def _default(args: list[Any], ev: EvidenceStore) -> bool:
    """兜底分支：恒 True。check 列表的最后一项必须是它（lint 检查）。"""
    return True


def run_check(check: list[dict], ev: EvidenceStore) -> tuple[str, int]:
    """按序求值断言链，返回 (命中的 then 判定值, 命中下标)。

    check 是 RuleProbe.check 的 dict 形式（yaml 解析后）：
      - assert: fs.path_exists / args: [...] / then: correct_reuse
    """
    for i, branch in enumerate(check):
        name = branch["assert"]
        if name not in ASSERTS:
            raise KeyError(f"未知断言: {name}（契约 02 §6 断言库之外）")
        if ASSERTS[name](branch.get("args", []), ev):
            return branch["then"], i
    raise ValueError("断言链未兜底：最后必须是 assert: default")
