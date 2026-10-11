"""用例进库校验（B 的每日工具，对应契约 02 §7 出题校验）。

用法：
    python scripts/lint_cases.py            # 校验 cases/full 全库 + 输出覆盖矩阵
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


def check_case(path: Path, errors: list[str], stats: dict, warnings: list[str] | None = None) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    case = MemoryCase.model_validate(raw)  # 抛异常 = schema 不合法
    if warnings is None:
        warnings = []

    # ID 格式与唯一性（契约：<能力族>-<三位序号>；生成用例允许 -gNN，如 boundary-g01）
    if not re.fullmatch(r"[a-z]+-(?:[0-9]{3}|g[0-9]{2})", case.case_id):
        errors.append(f"{path.name}: case_id '{case.case_id}' 不符合 <能力族>-<三位序号> 格式")

    # 防污染：boundary / sensitive 必须有 canary
    texts = [s.user or s.task or "" for p in case.phases for s in p.steps]
    full_text = "\n".join(texts)
    if case.capability.value == "boundary" or case.content_type.value == "sensitive":
        if not CANARY_RE.search(full_text):
            errors.append(f"{path.name}: 能力={case.capability.value} 或 内容={case.content_type.value} "
                          f"必须包含 canary-[a-z0-9]{{4}} 串")
        else:
            stats["canary_cases"] += 1
    # canary 不得出现在 probe 段提问里（避免把答案喂给 agent）
    probe_texts = [s.user or "" for p in case.phases if p.name == "probe" for s in p.steps if s.user]
    if any(CANARY_RE.search(t) for t in probe_texts):
        errors.append(f"{path.name}: probe 段提问不应包含 canary 串（会泄底）")

    # 探测点检查
    for probe in case.probes:
        if probe.kind == "judge":
            if not probe.rubric.strip():
                errors.append(f"{path.name}: judge 探测 {probe.id} rubric 为空（不可判定）")
            for v in probe.verdict_map.values():
                if v not in FIVE_STATES:
                    errors.append(f"{path.name}: judge 探测 {probe.id} 映射到非法判定值 '{v}'")
            if len(probe.anchors) < 2:
                errors.append(f"{path.name}: judge 探测 {probe.id} 锚定例 <2（契约要求每题型至少 2 条）")
            anchor_keys = {a.expect_verdict for a in probe.anchors}
            if not anchor_keys <= set(probe.verdict_map):
                errors.append(f"{path.name}: judge 探测 {probe.id} 锚定例的 expect_verdict 不在 verdict_map 中")
            # ask 与 probe 段话术一致
            if probe.ask not in probe_texts:
                errors.append(f"{path.name}: judge 探测 {probe.id} 的 ask 与 probe 段 user 话术不一致")
            # update 族统一口径：info_update 题型答旧值 = wrong_reuse；
            # temporal 题型答错版本是时间理解失败，判 confusion 即可。
            if case.capability.value == "dynamic_update" and case.question_type.value == "info_update":
                old_keys = [k for k in probe.verdict_map if probe.verdict_map[k] == "wrong_reuse"]
                if not old_keys:
                    errors.append(f"{path.name}: update 族 info_update 探测 {probe.id} 必须把旧值映射为 wrong_reuse")
        else:  # rule
            if not probe.check:
                errors.append(f"{path.name}: rule 探测 {probe.id} check 为空")
            names = [c.assert_name for c in probe.check]
            if names and names[-1] != "default":
                errors.append(f"{path.name}: rule 探测 {probe.id} 缺少 default 兜底分支")
            for c in probe.check:
                if c.then not in FIVE_STATES:
                    errors.append(f"{path.name}: rule 探测 {probe.id} 判定值 '{c.then}' 非法")

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
        targets = sorted((REPO / "cases" / "full").glob("*.yaml"))
        mode = "full"

    errors: list[str] = []
    warnings: list[str] = []
    stats = {
        "total": 0, "canary_cases": 0, "judge_probes": 0, "rule_probes": 0,
        "by_capability": Counter(), "by_content": Counter(), "by_qtype": Counter(),
        "by_difficulty": Counter(), "matrix": defaultdict(int),
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
        if "--allow-gaps" in argv:
            print(f"\n  ⚠ 覆盖矩阵空格（{len(empty)} 个，--allow-gaps 模式下仅提示）: {empty}")
        else:
            errors.append(f"覆盖矩阵有空格: {empty}")

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
