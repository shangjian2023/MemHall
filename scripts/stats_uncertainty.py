"""测量不确定度与功效统计（R55/R53 对账数据源，零 LLM 调用）。

回答一个问题：对外报的百分数是谁的属性——把误差来源拆开列账：
  ① 系统内随机性：同一探测点两轮判定一致率（"问两遍"的跨轮形态）
  ② 排名是否成立：智能体对合并两轮的配对符号检验
  ③ 维度切法携不携信息：每维组间/组内方差比（≈1 的维不区分系统）
  ④ 判卷口径差：dual vs scripted 同证据分差（仪器方差上界之一）
  ⑤ 功效声明：按观测方差，把两台智能体分开需要几轮

用法:
  uv run python scripts/stats_uncertainty.py [runs 根目录] [full|chains]
      历史口径复算：三家 × 4 轮（full）或 × 2 轮（chains）dual 直判 run
      （verdicts.jsonl）+ scripted 备份（verdicts.scripted.jsonl，缺了该表跳过）。
      输出与 2026-10 复算版逐字一致（可复算性优先）。

  uv run python scripts/stats_uncertainty.py runs --index <index.json>
      新实验索引模式：先过 check_compatibility（题目/试卷/协议/沙箱口径），
      按 suite/sandbox/model/judge 派生分组（一个索引文件 = 一个实验臂，
      sandbox=1 主矩阵与 sandbox=0 对照臂分开建索引，不得混合聚合），
      然后输出 ①-⑤ + ⑥ 正式统计（均值±样本标准差、bootstrap CI、六维、
      pass^k 条件通过率/覆盖率/保守下界、有效率）。智能体数量不设上限
      （四家矩阵：hermes/kylinbot/openclaw/kylin-agent 或任何新名字）。

实验索引格式（memhall.experiment-index/1，D/E 可直接生成）:
  {
    "schema": "memhall.experiment-index/1",
    "experiment_id": "final-v3-20261015",        // 任意标识，仅展示
    "purpose": "formal",                          // formal|development|exploratory
    "suite": "challenge-v2",                      // full|chains|challenge-v2|
                                                  // development-v2|gen
    "runs": [                                     // 本臂全部 run，顺序不限
      {"run_id": "20261015-090000-hermes", "agent": "hermes", "round": 1},
      ...
    ]
  }
  校验规则：purpose=formal 只收正式三题包，且拒绝 manifest 标记 exploratory
  的轮次、拒绝与历史 FULL_AGENTS/CHAINS_AGENTS 重叠的 run_id（新矩阵不串
  历史轮）；development 只收 development-v2/gen（结果不能并入正式分数，
  输出全程带标签）；一个索引内 suite/sandbox/model/judge 必须一致（混臂
  请拆多个索引文件）。全部校验只读原始归档，不写不删。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import yaml

from memhall.report.compatibility import check_compatibility, sandbox_state
from memhall.schema.models_case import MemoryCase, probe_role

# full-45 口径四轮（10-10 两轮 + 10-11/12 r3/r4 两轮）；chains 口径两轮（r3/r4）。
# AGENTS 别名保持指向 full——make_readme_figures 经 import 复用本表出图。
FULL_AGENTS = {
    "hermes": ["20261010-073953-hermes", "20261010-135112-hermes",
               "20261011-073455-hermes", "20261011-103806-hermes"],
    "kylinbot": ["20261010-163950-kylinbot", "20261010-181612-kylinbot",
                 "20261011-131723-kylinbot", "20261011-145803-kylinbot"],
    "openclaw": ["20261010-200117-openclaw", "20261010-215351-openclaw",
                 "20261011-164757-openclaw", "20261011-183642-openclaw"],
}
CHAINS_AGENTS = {
    "hermes": ["20261011-102221-hermes", "20261011-130417-hermes"],
    "kylinbot": ["20261011-144945-kylinbot", "20261011-164015-kylinbot"],
    "openclaw": ["20261011-182539-openclaw", "20261011-201110-openclaw"],
}
AGENTS = FULL_AGENTS
HISTORICAL_RUN_IDS = {
    rid for rounds in (*FULL_AGENTS.values(), *CHAINS_AGENTS.values()) for rid in rounds
}
# 题包目录名 → manifest.benchmark.suite.suite_id（索引声明 suite 用目录名，
# 与归档对账用 suite_id；两者映射在这里维护）
SUITE_IDS = {
    "full": "memhall-full-v1",
    "chains": "memhall-chains-v1",
    "challenge-v2": "memhall-challenge-v2-test",
    "development-v2": "memhall-challenge-v2-development",
    "gen": "memhall-generated-v1",
}
FORMAL_SUITES = ("full", "chains", "challenge-v2")
DEVELOPMENT_SUITES = ("development-v2", "gen")
# 维度族前缀（探测点 ID 的 family 段即 capability 族）
FAMILY_CAP = {
    "persist": "persist", "recall": "recall", "update": "dynamic_update",
    "discriminate": "discriminate", "boundary": "boundary", "reuse": "reuse",
    "chain": "reuse", "temporal": "recall",
}
CAP_ZH = {"persist": "长期保持", "recall": "记忆调用", "dynamic_update": "动态更新",
          "discriminate": "相近区分", "boundary": "边界识别", "reuse": "任务复用"}
_EXCLUDED = ("invalid_run", "human_review", "degraded")


def load_verdicts(path: Path) -> dict[str, str]:
    # 与正式报告对齐：存储态等 diagnostic 探测不进入成绩统计。
    # 从该次运行的题目快照判 role，避免当前题库变更污染历史口径。
    diagnostic = set()
    for case_path in (path.parent / "cases").glob("*/case.yaml"):
        case = MemoryCase.model_validate(yaml.safe_load(case_path.read_text(encoding="utf-8")))
        diagnostic.update(p.id for p in case.probes if probe_role(case, p) == "diagnostic")
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        v = json.loads(line)
        if v["probe_id"] not in diagnostic:
            out[v["probe_id"]] = "degraded" if v.get("degraded") else v["verdict"]
    return out


def load_probe_capabilities(run_dir: Path) -> dict[str, str]:
    """能力标签取题目快照；temporal 等题型也并入正式能力维。"""
    caps = {}
    for case_path in (run_dir / "cases").glob("*/case.yaml"):
        case = MemoryCase.model_validate(yaml.safe_load(case_path.read_text(encoding="utf-8")))
        caps.update((p.id, case.capability.value) for p in case.probes)
    return caps


def binary(v: str) -> bool | None:
    """correct→True；错误态→False；无效/未决/降级→None（不进配对）。"""
    if v == "correct":
        return True
    if v in _EXCLUDED:
        return None
    return False


def sign_p(n: int, k: int) -> float:
    if n <= 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def f_sf(x: float, d1: int = 2, d2: int = 3) -> float:
    """F(d1,d2) 上尾概率——区分度比值的显著性参考。d1=2 时正则化不完全
    beta 有闭式解 (d2/(d1·x+d2))^(d2/2)，对任意 d2 成立（对照 α=0.05 临界：
    d2=3 → 9.55，d2=9 → 4.26）。调用方按轮数算 d2=Σ(n_i−1)。"""
    if d1 != 2:
        raise NotImplementedError("闭式解仅对 d1=2 成立")
    return (d2 / (d1 * x + d2)) ** (d2 / 2)


def overall(vs: dict[str, str]) -> float | None:
    vals = [binary(v) for v in vs.values()]
    valid = [x for x in vals if x is not None]
    return sum(valid) / len(valid) if valid else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测量不确定度与功效统计（零 LLM 调用）")
    parser.add_argument("runs_root", nargs="?", default="runs", help="runs 根目录（默认 runs）")
    parser.add_argument("mode", nargs="?", default="full", choices=("full", "chains"),
                        help="历史口径：full=三家×4 轮，chains=三家×2 轮")
    parser.add_argument("--index", type=Path, default=None,
                        help="实验索引 JSON（memhall.experiment-index/1）；指定后 mode 无效")
    return parser.parse_args()


def _manifest_of(run_dir: Path) -> dict:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def _arm_key(manifest: dict) -> tuple[str, str, str, str]:
    """从 manifest 派生实验臂：suite/sandbox/model/judge（四元组一致才同臂）。"""
    benchmark = manifest.get("benchmark") or {}
    suite_id = ((benchmark.get("suite") or {}).get("suite_id")) or "unknown"
    sandbox = sandbox_state(manifest) or "unknown"
    backend = manifest.get("model_backend") or {}
    model = backend.get("model") or f"{backend.get('mode') or 'unknown'}-lane"
    judge = (manifest.get("scoring_protocol") or {}).get("judge") or manifest.get("judge") or {}
    judge_key = f"{judge.get('mode', '?')}:{judge.get('model_a') or '?'}:{judge.get('prompt_version', '?')}"
    return (suite_id, sandbox, model, judge_key)


def load_experiment_index(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "memhall.experiment-index/1":
        raise ValueError(f"schema 不识别: {data.get('schema')!r}（预期 memhall.experiment-index/1）")
    if data.get("purpose") not in ("formal", "development", "exploratory"):
        raise ValueError("purpose 必须为 formal/development/exploratory")
    if data.get("suite") not in SUITE_IDS:
        raise ValueError(f"suite 必须为 {sorted(SUITE_IDS)} 之一，收到 {data.get('suite')!r}")
    if data.get("purpose") == "formal" and data["suite"] not in FORMAL_SUITES:
        raise ValueError(f"formal 索引只收正式题包 {FORMAL_SUITES}，{data['suite']} 不能进正式口径")
    if data.get("purpose") == "development" and data["suite"] not in DEVELOPMENT_SUITES:
        raise ValueError(f"development 索引只收 {DEVELOPMENT_SUITES}（其结果不能并入正式分数）")
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("runs 不能为空")
    for entry in runs:
        if not isinstance(entry, dict) or not entry.get("run_id") or not entry.get("agent"):
            raise ValueError(f"runs[] 每项需要 run_id 与 agent: {entry!r}")
    ids = [entry["run_id"] for entry in runs]
    if len(set(ids)) != len(ids):
        raise ValueError("runs[].run_id 重复")
    return data


def resolve_index(index: dict, runs_root: Path) -> tuple[dict[str, list[Path]], dict]:
    """索引 → {agent: [run 目录…]}（同一索引 = 同一实验臂），只读 + 口径闸门。"""
    purpose, suite = index["purpose"], index["suite"]
    entries = sorted(index["runs"], key=lambda e: (e.get("round", 0), e["run_id"]))
    runs: dict[str, list[Path]] = {}
    manifests: dict[str, dict] = {}
    for entry in entries:
        run_dir = runs_root / entry["run_id"]
        if not (run_dir / "verdicts.jsonl").is_file():
            raise ValueError(f"缺 run 或缺 verdicts.jsonl: {run_dir}")
        manifest = _manifest_of(run_dir)
        run_id = entry["run_id"]
        if purpose == "formal":
            if run_id in HISTORICAL_RUN_IDS:
                raise ValueError(f"{run_id} 是历史实验轮次，不能进 formal 新矩阵索引")
            if (manifest.get("benchmark") or {}).get("exploratory"):
                raise ValueError(f"{run_id} 标记为探索轮换（exploratory），不能进 formal 索引")
            derived = ((manifest.get("benchmark") or {}).get("suite") or {}).get("suite_id")
            if derived != SUITE_IDS[suite]:
                raise ValueError(
                    f"{run_id} 的题包 suite_id={derived!r} 与索引声明 {suite}"
                    f"（{SUITE_IDS[suite]!r}）不一致"
                )
        runs.setdefault(entry["agent"], []).append(run_dir)
        manifests[run_id] = manifest
    round_counts = {a: len(v) for a, v in runs.items()}
    if len(set(round_counts.values())) != 1:
        raise ValueError(f"各智能体轮数不一致 {round_counts}，先补齐再建索引")
    # 一个索引一个臂：suite/sandbox/model/judge 四元组全索引一致，混臂拆多个索引
    keys = {run_id: _arm_key(m) for run_id, m in manifests.items()}
    distinct = sorted(set(keys.values()))
    if len(distinct) > 1:
        raise ValueError(
            f"索引内混臂（suite/sandbox/model/judge 有 {len(distinct)} 种），"
            f"请拆分索引文件: {distinct}"
        )
    # 同智能体多轮：真实版本/模型/思考条件 + 沙箱 + 题卷协议，不一致即拒绝
    compat = {}
    for agent, dirs in runs.items():
        try:
            compat[agent] = check_compatibility(dirs, aggregate=True)
        except ValueError as e:
            raise ValueError(f"{agent} 各轮口径不一致: {e}") from e
    # 跨智能体：不同家版本天然不同，但题卷/协议/沙箱臂必须一致才可比
    all_dirs = [d for dirs in runs.values() for d in dirs]
    cross = check_compatibility(all_dirs)
    arm = {
        "experiment_id": index.get("experiment_id", "?"),
        "purpose": purpose,
        "suite": suite,
        "key": {"suite_id": distinct[0][0], "sandbox": distinct[0][1],
                "model": distinct[0][2], "judge": distinct[0][3]},
        "per_agent": {a: c for a, c in compat.items()},
        "cross_agent": cross,
    }
    return runs, arm


def _print_compatibility(arm: dict) -> None:
    key = arm["key"]
    print(
        f"== ⓪ 兼容性与分组（实验臂 {arm['experiment_id']} · {arm['purpose']} · {arm['suite']}）=="
    )
    print(
        f"  臂条件：suite={key['suite_id']} · sandbox={key['sandbox']} · "
        f"model={key['model']} · judge={key['judge']}"
    )
    for agent, compat in arm["per_agent"].items():
        state = "✓ 已验证" if compat["verified"] else "⚠ 未验证"
        print(f"  {agent}: {state}")
        for warning in compat["warnings"]:
            print(f"    - {warning}")
    cross = arm["cross_agent"]
    if not cross["verified"]:
        print("  跨智能体共同口径：⚠ 未验证")
        for warning in cross["warnings"]:
            print(f"    - {warning}")
    if arm["purpose"] != "formal":
        print(f"  ⚠ purpose={arm['purpose']}：以下统计仅供该用途，不能并入正式分数")


def _formal_stats(runs: dict[str, list[Path]], vd: dict[str, list[dict[str, str]]]) -> None:
    """⑥ 正式统计：均值±样本标准差、bootstrap CI、六维、pass^k、有效率。"""
    from memhall.report.aggregate import pass_k_detail
    from memhall.report.cluster import bootstrap_ci, load_clusters, status_pool

    probe_caps = load_probe_capabilities(next(iter(runs.values()))[0])
    for agent, dirs in runs.items():
        rounds = len(dirs)
        clusters = load_clusters(dirs)
        statuses = status_pool(dirs)
        scores = [overall(r) for r in vd[agent]]
        mean = sum(scores) / rounds
        # 单轮无样本标准差（n-1=0），如实显示 —；多轮但该维度只测过一轮同理
        std_s = f"{_std(scores):.1%}" if rounds > 1 else "—"
        ci = bootstrap_ci(clusters, "__overall__", rounds=rounds).get("ci95")
        ci_s = f"（95% CI {ci[0]:.0%}~{ci[1]:.0%}）" if ci else ""
        detail = pass_k_detail(statuses, "__overall__", rounds)
        line = f"  {agent}: 总分 {mean:.1%} ± {std_s}{ci_s}（n={rounds} 轮）"
        if detail:
            line += (
                f"　pass^{detail['k']} 条件 {detail['rate_conditional']:.1%}"
                f"（完整 {detail['n_full']}/{detail['eligible']}，覆盖率 {detail['coverage']:.1%}，"
                f"保守下界 {detail['floor_conservative']:.1%}）"
            )
            inc = detail["incomplete"]
            if any(inc.values()):
                line += (
                    f"　未全过原因：缺失 {inc['missing']}·无效 {inc['invalid_run']}"
                    f"·未决 {inc['human_review']}·降级 {inc['degraded']}"
                )
        print(line)
        valid_rates = [sum(v not in _EXCLUDED for v in r.values()) / len(r) for r in vd[agent]]
        print(f"    有效率（各轮）：{' / '.join(f'{v:.1%}' for v in valid_rates)}")
        for cap in CAP_ZH:
            per_round = []
            for r in vd[agent]:
                ps = [p for p in r if probe_caps.get(p) == cap]
                vals = [binary(r[p]) for p in ps]
                valid = [x for x in vals if x is not None]
                per_round.append(sum(valid) / len(valid) if valid else None)
            if all(x is None for x in per_round):
                continue
            cells = " ".join(
                f"{v:.0%}" if v is not None else "未测" for v in per_round
            )
            known = [v for v in per_round if v is not None]
            m = sum(known) / len(known)
            s_s = f"{_std(known):.0%}" if len(known) > 1 else "—"
            pk = pass_k_detail(statuses, cap, rounds)
            pk_s = (
                f" · pass^{pk['k']} {pk['rate_conditional']:.0%}"
                f"（{pk['n_full']}/{pk['eligible']}）"
                if pk
                else ""
            )
            print(f"    {CAP_ZH[cap]:<5} 均值 {m:.0%} ± {s_s}　各轮 {cells}{pk_s}")


def main() -> int:
    # GBK 控制台跑批也别崩：不可编码字符降级为 ?（UTF-8 终端不受影响）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = _parse_args()
    root = Path(args.runs_root)
    arm = None
    if args.index:
        try:
            index = load_experiment_index(args.index)
            runs, arm = resolve_index(index, root)
        except (ValueError, OSError) as e:
            print(f"索引无效: {e}")
            return 2
        _print_compatibility(arm)
    else:
        table = CHAINS_AGENTS if args.mode == "chains" else FULL_AGENTS
        runs = {a: [root / r for r in rs] for a, rs in table.items()}
    missing = [str(p) for ps in runs.values() for p in ps if not (p / "verdicts.jsonl").exists()]
    if missing:
        print(f"缺 run: {missing}")
        return 2
    vd = {a: [load_verdicts(p / "verdicts.jsonl") for p in ps] for a, ps in runs.items()}

    # ① 系统内随机性：同一探测点轮间两两一致率（六态严格一致 + 对错翻转）
    # （自比无方向可言，不报 p；翻转率就是"问两遍答案变不变"的直接读数。
    #   n=2 一对即全量；n≥3 报两两组合的 min–max 与均值）
    n_r = len(next(iter(vd.values())))
    n_pair = n_r * (n_r - 1) // 2
    print(f"== ① 系统内稳定性（同探测点轮间两两，{n_r} 轮 {n_pair} 对）==")
    for a, rs in vd.items():
        common = set.intersection(*(set(r) for r in rs))
        agrees: list[float] = []
        flips: list[float] = []
        for i in range(n_r):
            for j in range(i + 1, n_r):
                r1, r2 = rs[i], rs[j]
                exact = sum(1 for p in common if r1[p] == r2[p])
                b = [xy for xy in ((binary(r1[p]), binary(r2[p])) for p in common)
                     if None not in xy]
                agrees.append(exact / len(common))
                flips.append(sum(1 for x, y in b if x != y) / len(b))
        # 单轮臂（gen/development-v2 的 1 轮口径）没有轮间对可比——如实跳过
        if not agrees:
            print(f"  {a:<9} 单轮，无轮间对可比")
            continue
        print(f"  {a:<9} 六态一致 {min(agrees):.1%}–{max(agrees):.1%}"
              f"（均值 {sum(agrees) / len(agrees):.1%}）　"
              f"对错翻转 {min(flips):.0%}–{max(flips):.0%}"
              f"（均值 {sum(flips) / len(flips):.0%}）")

    # ② 成对符号检验：各轮同位配对合并，方向票 = 一家对该题对、另一家错
    print("== ② 探索性配对票检验（各轮合并，未校正重复题相关性）==")
    names = list(vd)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            to_b = to_a = 0
            for ra, rb in zip(vd[a], vd[b], strict=True):   # 逐轮同位配对，家内轮数一致
                for p in set(ra) & set(rb):
                    x, y = binary(ra[p]), binary(rb[p])
                    if x is None or y is None or x == y:
                        continue
                    if y:
                        to_b += 1
                    else:
                        to_a += 1
            n = to_a + to_b
            p = sign_p(n, to_b)
            verdict = ("不显著" if p >= 0.05
                       else f"显著偏向 {b if to_b > to_a else a}")
            print(f"  {a} vs {b}: 方向票 {to_a}+{to_b}={n}，p={p:.3f}，{verdict}")

    # ②b case 聚类稳健检验：同一题跨轮重复出现在票层相关，把独立单元从
    # "探测票"换成"题"——每题净方向 = 该题全部轮/探测点的方向票代数和，
    # 只对净方向非零的题做符号检验。轮数少时这是审计要求的最低校正。
    print("== ②b case 聚类稳健检验（独立单元=题，净方向票符号检验）==")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            net: dict[str, int] = {}
            for ra, rb in zip(vd[a], vd[b], strict=True):
                for p in set(ra) & set(rb):
                    x, y = binary(ra[p]), binary(rb[p])
                    if x is None or y is None or x == y:
                        continue
                    case_key = p.rsplit("-p", 1)[0]
                    net[case_key] = net.get(case_key, 0) + (1 if y else -1)
            pro_b = sum(1 for v in net.values() if v > 0)
            pro_a = sum(1 for v in net.values() if v < 0)
            n = pro_a + pro_b
            p = sign_p(n, pro_b)
            verdict = ("不显著" if p >= 0.05
                       else f"显著偏向 {b if pro_b > pro_a else a}")
            print(f"  {a} vs {b}: 题 {pro_a}+{pro_b}={n}"
                  f"（{len(net)} 题有方向票）,p={p:.3f}，{verdict}")

    # ③ 维度区分度：组间方差（智能体均值间）/ 组内方差（同智能体轮间）
    print(f"== ③ 维度区分度（组间/组内方差比，描述性，n={n_r}/组）==")
    probe_caps = load_probe_capabilities(next(iter(runs.values()))[0])
    for cap in CAP_ZH:
        per_agent: dict[str, list[float]] = {}
        for a in vd:
            scores = []
            for r in vd[a]:
                ps = [p for p in r if probe_caps.get(p) == cap]
                vals = [binary(r[p]) for p in ps]
                valid = [x for x in vals if x is not None]
                scores.append(sum(valid) / len(valid) if valid else float("nan"))
            per_agent[a] = scores
        # 未测族如实标注：本轮 run 无该族探测点（如 full-45 轮的 chain），
        # 空分数全 nan 会算出 inf 比值——那是"没考"，不是"无穷区分度"
        if any(x != x for v in per_agent.values() for x in v):
            print(f"  {CAP_ZH[cap]:<5} 未测（本轮 run 无该能力计分探测点）")
            continue
        # 区分度是"跨智能体"的描述量：单智能体臂（逐家索引/冒烟口径）
        # 没有组间方差可言，不硬算 0 除——如实标注不适用
        if len(per_agent) < 2:
            print(f"  {CAP_ZH[cap]:<5} 不适用（单智能体臂，无组间方差）")
            continue
        # 单轮臂组内 df=0，轮间方差分母为零——如实标注不适用
        if n_r < 2:
            print(f"  {CAP_ZH[cap]:<5} 不适用（单轮臂，无组内方差）")
            continue
        means = [sum(v) / len(v) for v in per_agent.values()]
        between = sum((m - sum(means) / len(means)) ** 2 for m in means) / (len(means) - 1)
        within = sum(sum((x - sum(v) / len(v)) ** 2 for x in v) / (len(v) - 1)
                     for v in per_agent.values()) / len(per_agent)
        ratio = between / within if within > 0 else float("inf")
        d2 = sum(len(v) - 1 for v in per_agent.values())
        print(f"  {CAP_ZH[cap]:<5} 均值 "
              + " ".join(f"{a}:{m:.0%}" for a, m in zip(per_agent, means, strict=True))
              + f"　组间/组内 = {ratio:.1f}（描述性比值，组内 df={d2}；非 ANOVA F 值）"
              + ("　⚠ 不区分" if ratio < 1 else ""))

    # ④ 判卷口径差 + 环境噪声
    print("== ④ 判卷口径差（同证据 dual vs scripted）与环境噪声（invalid 率）==")
    for _a, ps in runs.items():
        for p in ps:
            cur = load_verdicts(p / "verdicts.jsonl")
            d_over = overall(cur)
            line = f"  {p.name:<28} dual {d_over:.1%}"
            s_path = p / "verdicts.scripted.jsonl"
            if s_path.exists():
                s_over = overall(load_verdicts(s_path))
                line += f"　scripted {s_over:.1%}（口径差 {(d_over - s_over) * 100:+.1f} 分）"
            n_inv = sum(1 for v in cur.values() if v == "invalid_run")
            line += f"　invalid {n_inv}/{len(cur)}"
            print(line)

    # ⑤ 功效声明：检测两台差距 Δ（α=0.05、power 80% → z≈2.8）所需轮数
    print("== ⑤ 功效声明（需要几轮才能把两家分开）==")
    overall_by_agent = {a: [overall(r) for r in rs] for a, rs in vd.items()}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if n_r < 2:
                print(f"  {a} vs {b}: 单轮臂 σ 不可估，跳过功效估计")
                continue
            sa = _std(overall_by_agent[a])
            sb = _std(overall_by_agent[b])
            delta, n = required_rounds(overall_by_agent[a], overall_by_agent[b])
            if delta == 0:
                print(f"  {a} vs {b}: 均值无差距")
                continue
            print(f"  {a} vs {b}: 差距 {delta:.1%}，σ {sa:.1%}/{sb:.1%} → 需 ≈{math.ceil(n)} 轮")
    print("（口径：score 探测、轮级总分、双侧 α=0.05、power 80%；"
          "轮数为基于小样本观测方差的近似估计；②未校正重复题相关性，"
          "以 ②b 按 case 聚类的净方向检验为稳健口径）")
    if arm is not None:
        print(f"== ⑥ 正式统计（{arm['purpose']} · {arm['suite']}，均值±样本标准差，题族 bootstrap CI）==")
        _formal_stats(runs, vd)
        print("（⑥ pass^k 条件通过率只统计 k 轮全部有效判定的探测点；覆盖率与保守下界"
              "必须一起读——n 轮是可复现性起点，不自动等于显著排名，排名证据以 ②b 聚类"
              "稳健检验与 ⑤ 功效声明为准；证据不足时如实报『证据不足』）")
    return 0


def required_rounds(a: list[float], b: list[float]) -> tuple[float, float]:
    """按实际轮数算均值差；零差距的需求轮数为无穷。"""
    delta = abs(sum(a) / len(a) - sum(b) / len(b))
    n = ((2.8 * math.sqrt(_std(a) ** 2 + _std(b) ** 2) / delta) ** 2
         if delta else float("inf"))
    return delta, n


def _std(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


if __name__ == "__main__":
    sys.exit(main())
