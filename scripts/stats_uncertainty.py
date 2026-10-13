"""测量不确定度与功效统计（R55/R53 对账数据源，零 LLM 调用）。

回答一个问题：对外报的百分数是谁的属性——把误差来源拆开列账：
  ① 系统内随机性：同一探测点两轮判定一致率（"问两遍"的跨轮形态）
  ② 排名是否成立：三对智能体合并两轮的配对符号检验
  ③ 维度切法携不携信息：每维组间/组内方差比（≈1 的维不区分系统）
  ④ 判卷口径差：dual vs scripted 同证据分差（仪器方差上界之一）
  ⑤ 功效声明：按观测方差，把两台智能体分开需要几轮

用法: uv run python scripts/stats_uncertainty.py [runs 根目录] [full|chains]
输入: 三家 × 4 轮（full 口径）或 × 2 轮（chains 口径）dual 直判 run
      （verdicts.jsonl）+ scripted 备份（verdicts.scripted.jsonl，缺了该表跳过）
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import yaml

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


def main() -> int:
    # GBK 控制台跑批也别崩：不可编码字符降级为 ?（UTF-8 终端不受影响）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "runs")
    mode = sys.argv[2] if len(sys.argv) > 2 else "full"
    table = CHAINS_AGENTS if mode == "chains" else FULL_AGENTS
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
