"""报告进阶图（单 run 版，最终展示物多样化；风格同 make_readme_figures）。

每份报告自动带两张辅助图：
- report-verdict-mix.png：分能力的判定构成堆积条——错误集中在哪一维、
  什么形态（遗漏/混淆/编造/错误持久化/错误复用/无效），一眼可读
- report-caliber.png：双判 vs 脚本判卷哑铃（per 能力）——重判把哪些维
  拉低了多少（幸存者偏差摘除的可视化）；无 verdicts.scripted.jsonl
  存档时跳过（scripted run 不画）

数据全部来自本 run 产物（verdicts/cases 快照），零 LLM、离线可复现。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

VERDICT_ZH_P = {
    "correct": "正确", "omission": "遗漏", "fabrication": "混淆/编造",
    "over_persist": "错误持久化", "wrong_reuse": "错误复用",
    "human_review": "人工复核", "invalid_run": "运行无效",
}
VERDICT_COLOR_P = {
    "correct": "#009E73", "omission": "#0072B2", "fabrication": "#E69F00",
    "over_persist": "#D55E00", "wrong_reuse": "#CC79A7",
    "human_review": "#999999", "invalid_run": "#CCCCCC",
}
_EXCLUDED = ("invalid_run", "human_review")


def _setup() -> None:
    import matplotlib

    from memhall.report.radar import _setup_font
    matplotlib.use("Agg")
    _setup_font()


def _verdict_mix(run_dir: Path, verdicts, cases: dict) -> str | None:
    """分能力判定构成堆积条。"""
    if not verdicts:
        return None
    import matplotlib.pyplot as plt
    import numpy as np

    from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER
    _setup()
    per_cap: dict[str, Counter] = {}
    for v in verdicts:
        cap = cases[v.case_id].capability.value if v.case_id in cases else "?"
        per_cap.setdefault(cap, Counter())[v.verdict.value] += 1
    caps = [c for c in CAP_ORDER if c in per_cap] or sorted(per_cap)  # type: ignore[arg-type]
    states = [k for k in VERDICT_ZH_P
              if any(per_cap[c].get(k) for c in caps)]

    fig, ax = plt.subplots(figsize=(8.4, 3.0))
    ys = np.arange(len(caps))[::-1]
    totals = np.array([sum(per_cap[c].values()) for c in caps], dtype=float)
    left = np.zeros(len(caps))
    for s in states:
        vals = np.array([per_cap[c].get(s, 0) for c in caps], dtype=float)
        shares = vals / totals * 100
        ax.barh(ys, shares, left=left, height=0.6, color=VERDICT_COLOR_P[s],
                label=VERDICT_ZH_P[s], edgecolor="white", linewidth=0.5,
                alpha=0.92 if s != "invalid_run" else 0.7)
        for y, x0, sh in zip(ys, left, shares, strict=True):
            if sh >= 8:
                ax.text(x0 + sh / 2, y, f"{sh:.0f}", ha="center", va="center",
                        fontsize=8, color="white")
        left += shares
    ax.set_yticks(ys)
    ax.set_yticklabels([CAP_LABELS_ZH.get(c, c) for c in caps], fontsize=10)
    ax.set_xlim(0, 100)
    ax.set_xlabel("判定占比（%）")
    ax.xaxis.grid(True, color="0.88", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)
    ax.legend(frameon=False, ncol=len(states), loc="upper center",
              bbox_to_anchor=(0.5, -0.24), fontsize=8,
              columnspacing=1.0, handlelength=1.0)
    fig.tight_layout()
    out = run_dir / "report-verdict-mix.png"
    fig.savefig(out, dpi=200, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    return out.name


def _caliber(run_dir: Path, verdicts, cases: dict) -> str | None:
    """双判 vs 脚本判卷哑铃（per 能力，同证据两口径）。"""
    s_file = run_dir / "verdicts.scripted.jsonl"
    if not s_file.is_file():
        return None
    import matplotlib.pyplot as plt
    import numpy as np

    from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER
    _setup()

    def _load(path: Path) -> dict[str, str]:
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            v = json.loads(line)
            out[v["probe_id"]] = v["verdict"]
        return out

    def _rate(vs: dict[str, str], ids: list[str]) -> float | None:
        vals = [vs[p] for p in ids if p in vs]
        valid = [v for v in vals if v not in _EXCLUDED]
        if not valid:
            return None
        return sum(1 for v in valid if v == "correct") / len(valid)

    dual_raw = _load(run_dir / "verdicts.jsonl")
    scr_raw = _load(s_file)
    per_cap_ids: dict[str, list[str]] = {}
    for v in verdicts:
        cap = cases[v.case_id].capability.value if v.case_id in cases else "?"
        per_cap_ids.setdefault(cap, []).append(v.probe_id)
    caps = [c for c in CAP_ORDER if c in per_cap_ids] or sorted(per_cap_ids)  # type: ignore[arg-type]
    rows = []
    for c in caps:
        s, d = _rate(scr_raw, per_cap_ids[c]), _rate(dual_raw, per_cap_ids[c])
        if s is not None and d is not None:
            rows.append((CAP_LABELS_ZH.get(c, c), s * 100, d * 100))
    if not rows:
        return None

    fig, ax = plt.subplots(figsize=(6.8, 2.8))
    ys = np.arange(len(rows))[::-1]
    for y, (_label, s, d) in zip(ys, rows, strict=True):
        ax.plot([d, s], [y, y], color="0.75", linewidth=1.6)
        ax.scatter(s, y, s=40, facecolor="white", edgecolor="#666666",
                   linewidth=1.1, zorder=3)
        ax.scatter(d, y, s=44, color="#0072B2", zorder=4)
        ax.text(s + 1.2, y, f"{s:.0f}", fontsize=7.5, color="#666666")
        ax.text(d - 1.2, y, f"{d:.0f}", fontsize=7.5, ha="right",
                color="#222222")
        ax.text(103, y, f"Δ{d - s:+.0f}", fontsize=8.5, va="center",
                color="#D55E00" if d < s else "#009E73", clip_on=False)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=9.5)
    ax.set_xlim(0, 100)
    ax.set_xlabel("正确率（%）　空心=脚本判卷 · 实心=双 LLM 判卷")
    ax.xaxis.grid(True, color="0.88", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)
    fig.tight_layout()
    out = run_dir / "report-caliber.png"
    fig.savefig(out, dpi=200, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    return out.name


def render_panels(run_dir: Path, verdicts, cases: dict) -> list[str]:
    """生成报告辅助图，返回可嵌入 report.md 的 markdown 行列表。"""
    lines: list[str] = []
    mix = _verdict_mix(run_dir, verdicts, cases)
    if mix:
        lines.append(f"![判定构成]({mix})")
        lines.append("")
    cal = _caliber(run_dir, verdicts, cases)
    if cal:
        lines.append(f"![判卷口径对照]({cal})")
        lines.append("")
    return lines
