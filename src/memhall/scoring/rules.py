"""规则断言库 v0.1（契约 02 §6 断言表）。

owner: C（评分）—— 新增断言走契约变更流程。
D 的 runner / C 的判卷器共用本模块；断言函数只读证据，不产生副作用。

断言签名统一：
    (args, evidence) -> bool
evidence 是本 case 当前可用的证据集合（EvidenceStore）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

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

@_register("fs.path_exists")
def _fs_path_exists(args: list[Any], ev: EvidenceStore) -> bool:
    """路径存在（含 fs_diff 中 created 条目，或宿主真实存在——M1 兼容 mock）。"""
    target = str(args[0])
    diff = ev.latest_fs_diff()
    if diff and any(e.path == target and e.change == "created" for e in diff.entries):
        return True
    return Path(target).exists()


@_register("fs.path_absent")
def _fs_path_absent(args: list[Any], ev: EvidenceStore) -> bool:
    return not _fs_path_exists(args, ev)


@_register("fs.diff_contains")
def _fs_diff_contains(args: list[Any], ev: EvidenceStore) -> bool:
    target = str(args[0])
    diff = ev.latest_fs_diff()
    return bool(diff) and any(target in e.path for e in diff.entries)


# ---------- 记忆库类 ----------

def _memory_texts(ev: EvidenceStore) -> list[str]:
    snap = ev.latest_memory_snapshot()
    if snap is None:
        return []
    return [e.content for e in snap.entries]


@_register("memory.contains")
def _memory_contains(args: list[Any], ev: EvidenceStore) -> bool:
    """记忆 dump 中出现该字符串/正则（canary 串判 over_persist 的主力断言）。"""
    pattern = str(args[0])
    texts = _memory_texts(ev)
    if any(pattern in t for t in texts):
        return True
    try:
        rx = re.compile(pattern)
    except re.error:
        return False
    return any(rx.search(t) for t in texts)


@_register("memory.not_contains")
def _memory_not_contains(args: list[Any], ev: EvidenceStore) -> bool:
    return not _memory_contains(args, ev)


@_register("memory.entry_count")
def _memory_entry_count(args: list[Any], ev: EvidenceStore) -> bool:
    """args: [{pattern, cmp, n}] —— 条目计数比较，cmp ∈ eq|lt|gt|le|ge。"""
    spec = args[0] if isinstance(args[0], dict) else {}
    pattern, cmp, n = spec.get("pattern", ""), spec.get("cmp", "eq"), int(spec.get("n", 0))
    count = sum(1 for t in _memory_texts(ev) if pattern in t)
    return {
        "eq": count == n, "lt": count < n, "gt": count > n,
        "le": count <= n, "ge": count >= n,
    }.get(cmp, False)


# ---------- 回复类 ----------

@_register("reply.matches")
def _reply_matches(args: list[Any], ev: EvidenceStore) -> bool:
    """最近一条回复匹配字符串/正则。"""
    pattern = str(args[0])
    replies = ev.replies()
    if not replies:
        return False
    text = replies[-1].text
    if pattern in text:
        return True
    try:
        return re.compile(pattern).search(text) is not None
    except re.error:
        return False


# ---------- 操作记录类 ----------

def _action_items(ev: EvidenceStore):
    dump = ev.latest_actions()
    return dump.actions if dump else []


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
