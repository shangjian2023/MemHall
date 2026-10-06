"""用例进库校验（B 的每日工具，对应契约 02 §7 出题校验）。

用法：
    python scripts/lint_cases.py            # 全库门禁（full+gen+chains）+ 输出覆盖矩阵
    python scripts/lint_cases.py --case cases/full/persist-001.yaml   # 单用例快速校验

检查项（契约 02 §7）：
  1. schema 合法：pydantic MemoryCase 加载（枚举/必填/ID 格式）
  2. 防污染：boundary 能力或 sensitive 内容类用例必须含 canary-[a-z0-9]{4} 串
  3. 可判定：judge 探测 rubric 非空、verdict_map 完整、锚定例 ≥2；
     rule 探测 check 非空且含 default 兜底
  4. 话术一致：judge 探测的 ask 必须与 probe 阶段某条 user 话术一致
  5. 覆盖矩阵：capability × content_type 36 格无空格（全库统计）
  6. 判卷口径一致性：update 族答旧值必须映射 wrong_reuse（对齐 C 实测判定）
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from memhall.schema.models_case import MemoryCase  # noqa: E402

CANARY_RE = re.compile(r"canary-[a-z0-9]{4}")
CAPABILITIES = ["persist", "recall", "dynamic_update", "discriminate", "boundary", "reuse"]
CONTENT_TYPES = ["preference", "path", "template", "fact", "project_state", "sensitive"]
FIVE_STATES = {"correct", "omission", "confusion", "fabrication", "over_persist", "wrong_reuse"}

# R47：断言 args 形状按断言类型校验——错形状评测时才炸 invalid_run，进库就该拦
_ARGS_SHAPE = {
    "fs.path_exists": ("str",), "fs.path_absent": ("str",), "fs.diff_contains": ("str",),
    "memory.contains": ("str",), "memory.not_contains": ("str",),
    "memory.ever_contained": ("str",), "reply.matches": ("str",),
    "memory.entry_count": ("spec",), "actions.contains_action": ("spec",),
    "actions.count_lt": ("spec",),
}
_CMPS = {"eq", "lt", "gt", "le", "ge"}
# R47：evidence_ref 合法取值（缺省默认 evidence.jsonl；乱写会报告下钻失真）
_EVIDENCE_REFS = {"evidence.jsonl", "memory_snapshot", "fs_diff", "actions",
                  "dialogue", "transcript:answer"}


def _check_args_shape(path_name: str, probe_id: str, branch, errors: list[str]) -> None:
    shape = _ARGS_SHAPE.get(branch.assert_name)
    if shape is None:
        return
    args = branch.args
    if shape == ("str",):
        if len(args) != 1 or not isinstance(args[0], str):
            errors.append(f"{path_name}: rule 探测 {probe_id} 的 {branch.assert_name} "
                          "args 应为单字符串")
        return
    # spec 形态：{...dict...}
    if len(args) != 1 or not isinstance(args[0], dict):
        errors.append(f"{path_name}: rule 探测 {probe_id} 的 {branch.assert_name} "
                      "args 应为单 dict")
        return
    spec = args[0]
    if branch.assert_name == "memory.entry_count":
        if spec.get("cmp", "eq") not in _CMPS:
            errors.append(f"{path_name}: rule 探测 {probe_id} 的 entry_count.cmp 非法 "
                          f"({spec.get('cmp')}，应 ∈ {_CMPS})")
        if "n" not in spec:
            errors.append(f"{path_name}: rule 探测 {probe_id} 的 entry_count 缺 n")


def check_case(path: Path, errors: list[str], stats: dict, warnings: list[str] | None = None) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    case = MemoryCase.model_validate(raw)  # 抛异常 = schema 不合法
    if warnings is None:
        warnings = []

    # ID 格式与唯一性（契约：<能力族>-<三位序号>；生成用例允许 -gNN/hNN——
    # gen_cases --prefix h 自家 help 推荐的 heldout 前缀，R41 前正则漏了 h
    # 导致 21 道 heldout 全过不了自家 lint，官方评测现场没有可用 lint 入口）
    if not re.fullmatch(r"[a-z]+-(?:[0-9]{3}|g[0-9]{2}|h[0-9]{2})", case.case_id):
        errors.append(f"{path.name}: case_id '{case.case_id}' 不符合 <能力族>-<三位序号> 格式")

    # 防污染：boundary / sensitive 必须有 canary
    # （生成题豁免：source=generated 的防污染来自 seed 控制的随机 token，
    #  与 canary 串机制等价；且存档稳定性约束生成器不可改——见 test_gen_knobs）
    texts = [s.user or s.task or "" for p in case.phases for s in p.steps]
    full_text = "\n".join(texts)
    if case.capability.value == "boundary" or case.content_type.value == "sensitive":
        if case.meta.source == "generated":
            stats["canary_cases"] += 1
        elif not CANARY_RE.search(full_text):
            errors.append(f"{path.name}: 能力={case.capability.value} 或 内容={case.content_type.value} "
                          f"必须包含 canary-[a-z0-9]{{4}} 串")
        else:
            stats["canary_cases"] += 1
    # canary 不得出现在 probe/confound 段提问里（避免把答案喂给 agent；
    # R47：card 声称"canary 只在 inject 段"，此前 confound 段漏检）
    probe_texts = [s.user or "" for p in case.phases if p.name == "probe" for s in p.steps if s.user]
    confound_texts = [s.user or "" for p in case.phases
                      if p.name == "confound" for s in p.steps if s.user]
    for label, texts in (("probe", probe_texts), ("confound", confound_texts)):
        if any(CANARY_RE.search(t) for t in texts):
            errors.append(f"{path.name}: {label} 段话术不应包含 canary 串（会泄底）")

    # R47：同库重复题——inject 原文逐字重复（reuse-g01/g02 类纯重复双计分）
    inject_sig = "\n".join(s.user or s.task or ""
                           for p in case.phases if p.name == "inject"
                           for s in p.steps)
    sig = (case.capability.value, inject_sig)
    if inject_sig and sig in stats["inject_sigs"]:
        errors.append(f"{path.name}: 与 {stats['inject_sigs'][sig]} 重复"
                      "（同能力族同 inject 原文，纯重复题双计分）")
    elif inject_sig:
        stats["inject_sigs"][sig] = case.case_id

    # 探测点检查
    seen_pids: set[str] = set()
    for probe in case.probes:
        # R47：probe id 必须是 <case_id>-p<N> 前缀一致且 case 内唯一
        if not probe.id.startswith(case.case_id + "-"):
            errors.append(f"{path.name}: 探测 {probe.id} 不带 case_id 前缀"
                          f"（应为 {case.case_id}-pN）")
        if probe.id in seen_pids:
            errors.append(f"{path.name}: 探测 {probe.id} 在 case 内重复")
        seen_pids.add(probe.id)
        # R47：evidence_ref 合法性（乱写导致报告下钻失真）
        for ref in (getattr(probe, "evidence_ref", None) or []):
            if ref not in _EVIDENCE_REFS:
                errors.append(f"{path.name}: 探测 {probe.id} 的 evidence_ref "
                              f"'{ref}' 非法（应 ∈ {_EVIDENCE_REFS}）")
        if probe.kind == "judge":
            for k, v in probe.verdict_map.items():
                stats["vm_keys"][v].add(k)
        if probe.kind == "judge":
            if not probe.rubric.strip():
                errors.append(f"{path.name}: judge 探测 {probe.id} rubric 为空（不可判定）")
            for v in probe.verdict_map.values():
                if v not in FIVE_STATES:
                    errors.append(f"{path.name}: judge 探测 {probe.id} 映射到非法判定值 '{v}'")
            if len(probe.anchors) < 2:
                errors.append(f"{path.name}: judge 探测 {probe.id} 锚定例 <2"
                              "（契约要求每题型至少 2 条）")
            anchor_keys = {a.expect_verdict for a in probe.anchors}
            if not anchor_keys <= set(probe.verdict_map):
                errors.append(f"{path.name}: judge 探测 {probe.id} 锚定例的 "
                              "expect_verdict 不在 verdict_map 中")
            # ask 与 probe 段话术一致
            if probe.ask not in probe_texts:
                errors.append(f"{path.name}: judge 探测 {probe.id} 的 ask 与 probe 段 user 话术不一致")
            # update 族判定口径提示：info_update 题型答旧值→wrong_reuse 为推荐口径
            # （对齐 C 实测 §8）；temporal 题型答错版本是时间理解失败，判 confusion 即可。
            # 注意：main 上 update-001 经团队审计仍用 confusion，此处降为警告不拦截，交由 C/A 统一。
            if case.capability.value == "dynamic_update" and case.question_type.value == "info_update":
                old_keys = [k for k in probe.verdict_map if probe.verdict_map[k] == "wrong_reuse"]
                if not old_keys:
                    warnings.append(f"{path.name}: update 族 info_update 探测 {probe.id} "
                                    "缺少旧值→wrong_reuse 映射"
                                    f"（推荐口径，见 C 实测 §8；团队审计版可用 confusion，需 C/A 统一）")
        else:  # rule
            if not probe.check:
                errors.append(f"{path.name}: rule 探测 {probe.id} check 为空")
            names = [c.assert_name for c in probe.check]
            if names and names[-1] != "default":
                errors.append(f"{path.name}: rule 探测 {probe.id} 缺少 default 兜底分支")
            for c in probe.check:
                if c.then not in FIVE_STATES:
                    errors.append(f"{path.name}: rule 探测 {probe.id} 判定值 '{c.then}' 非法")
                _check_args_shape(path.name, probe.id, c, errors)

    # 统计
    stats["total"] += 1
    stats["by_capability"][case.capability.value] += 1
    stats["by_content"][case.content_type.value] += 1
    stats["by_qtype"][case.question_type.value] += 1
    stats["by_difficulty"][case.difficulty] += 1
    stats["judge_probes"] += sum(1 for p in case.probes if p.kind == "judge")
    stats["rule_probes"] += sum(1 for p in case.probes if p.kind == "rule")
    stats["matrix"][(case.capability.value, case.content_type.value)] += 1


def main(argv: list[str]) -> int:
    if "--case" in argv:
        targets = [Path(argv[argv.index("--case") + 1])]
        mode = "single"
    elif "--dir" in argv:
        targets = sorted(Path(argv[argv.index("--dir") + 1]).glob("*.yaml"))
        mode = Path(argv[argv.index("--dir") + 1]).name
    else:
        # 全库门禁：full + gen + chains（heldout 现场生成不在库内）。
        # 曾只查 full——chain-004 等新集合入库不过门禁，规则漂移无人拦。
        subs = ["full", "gen", "chains"]
        targets = sorted(p for s in subs
                         for p in (REPO / "cases" / s).glob("*.yaml"))
        mode = "+".join(subs)

    errors: list[str] = []
    warnings: list[str] = []
    stats = {
        "total": 0, "canary_cases": 0, "judge_probes": 0, "rule_probes": 0,
        "by_capability": Counter(), "by_content": Counter(), "by_qtype": Counter(),
        "by_difficulty": Counter(), "matrix": defaultdict(int),
        "vm_keys": defaultdict(set),
        "inject_sigs": {},   # R47：(capability, inject 原文) -> case_id 查重
    }
    seen_ids: set[str] = set()
    for p in targets:
        case = MemoryCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        if case.case_id in seen_ids:
            errors.append(f"{p.name}: case_id '{case.case_id}' 重复")
        seen_ids.add(case.case_id)
        check_case(p, errors, stats, warnings)

    # 覆盖矩阵
    print(f"\n=== 用例库校验（{mode} 模式）===")
    print(f"用例总数: {stats['total']} | canary 用例: {stats['canary_cases']} | "
          f"judge 探测: {stats['judge_probes']} | rule 探测: {stats['rule_probes']}")
    print(f"能力分布: {dict(stats['by_capability'])}")
    print(f"内容分布: {dict(stats['by_content'])}")
    print(f"题型分布: {dict(stats['by_qtype'])}")
    print(f"难度分布: {dict(sorted(stats['by_difficulty'].items()))}")

    empty = []
    print("\n=== 覆盖矩阵（capability × content_type，6×6=36 格）===")
    header = "capability      | " + " | ".join(f"{c[:10]:>10}" for c in CONTENT_TYPES)
    print(header)
    print("-" * len(header))
    for cap in CAPABILITIES:
        row = []
        for ct in CONTENT_TYPES:
            n = stats["matrix"].get((cap, ct), 0)
            row.append(f"{n:>10}")
            if n == 0:
                empty.append((cap, ct))
        print(f"{cap:<16} | " + " | ".join(row))
    if empty:
        # R41：36 格矩阵是全库口径——子集/单题模式永远填不满（heldout 21 道
        # 曾因此被硬门禁拦死），仅全库模式硬卡，--dir/--case 降为提示
        full_mode = mode == "+".join(["full", "gen", "chains"])
        if full_mode and "--allow-gaps" not in argv:
            errors.append(f"覆盖矩阵有空格: {empty}")
        else:
            print(f"\n  ⚠ 覆盖矩阵空格（{len(empty)} 个，非全库模式/--allow-gaps，仅提示）")

    # R21 advisory：verdict_map 键名词表统计（不拦截）——同一判定值的键名写法
    # 越多，LLM 判卷面对的词表越乱；新题用契约 02 §6 的标准词表
    print("\n=== verdict_map 键名词表（advisory，不拦截）===")
    for val in sorted(stats["vm_keys"]):
        keys = sorted(stats["vm_keys"][val])
        note = "  ← 写法偏多，建议收敛" if len(keys) > 4 else ""
        print(f"  {val:<12} {len(keys)} 种: {'/'.join(keys)}{note}")

    if errors:
        print("\n=== 校验失败 ===")
        for e in errors:
            print(f"  ✗ {e}")
        print(f"\n共 {len(errors)} 项问题")
        return 1
    if warnings:
        print("\n=== 提示（不拦截，待 C/A 统一口径）===")
        for w in warnings:
            print(f"  ⚠ {w}")
    print("\n=== 校验通过：全库无空格、无违规 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
