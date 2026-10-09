"""报告进阶图（单 run 版，最终展示物多样化；风格同 make_readme_figures）。

每份报告自动嵌入的辅助图（全部离线、零 LLM；数据不满足"值得画"的条件
就跳过该图——UI 端按 404 自动隐藏，不硬凑空图）：
- report-verdict-mix.png：分能力的判定构成堆积条——错误集中在哪一维、
  什么形态（遗漏/混淆/编造/错误持久化/错误复用/无效），一眼可读
- report-caliber.png：双判 vs 脚本判卷哑铃（per 能力）——重判把哪些维
  拉低了多少（幸存者偏差摘除的可视化）；无 verdicts.scripted.jsonl
  存档时跳过（scripted run 不画）
- report-latency.png：教/扰/考三阶段回复延迟分布（对数轴带状散点）——
  隔段后变慢/超时尾；mock 级瞬时回复（全 <0.2s）没有可比性，不画
- report-memory.png：记忆库存量变化（教后 vs 考后哑铃，逐用例）+ 教后
  条目留存率——存了多少、删没删、忘没忘；两侧快照齐全的用例 <2 不画
- report-durations.png：逐用例回合耗时堆积条（教/扰/考分色，拨钟标记）
  ——马拉松时间花在哪；有耗时的用例 <3 不画
- report-probe-history.png：同智能体历轮探测点通过点阵——稳定失分点
  vs 偶发翻转；同适配器 <2 轮不画，全轮全对的探测点无信息量不列
- report-case-scores.png：逐用例计分通过率排序（棒棒糖，点色=能力维）
  ——"哪几道题它最不会"；计分用例 <3 不画

数据全部来自本 run 产物（verdicts/cases 快照/evidence.jsonl）及同根
runs 目录的历史轮（仅 probe-history 一张），零 LLM、离线可复现。
"""

from __future__ import annotations

import json
import re
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


# ---------- 第二梯队（2026-10-09）：数据全部来自 evidence.jsonl ----------
# 共用口径：教/扰/考三阶段色 + 六能力点色（Okabe-Ito，对齐 skills 风格库）
PHASE_ZH = {"inject": "教", "confound": "扰", "probe": "考"}
PHASE_COLOR = {"inject": "#0072B2", "confound": "#56B4E9", "probe": "#009E73"}
CAP_COLOR = {
    "persist": "#0072B2", "recall": "#E69F00", "dynamic_update": "#009E73",
    "discriminate": "#CC79A7", "boundary": "#D55E00", "reuse": "#56B4E9",
}
CAP_ORDER_KEYS = ("persist", "recall", "dynamic_update",
                  "discriminate", "boundary", "reuse")


def _iter_evidence(run_dir: Path):
    """逐 case 懒读 evidence.jsonl（报告图的数据源，坏行跳过不炸图）。"""
    cases_dir = run_dir / "cases"
    if not cases_dir.is_dir():
        return
    for ev_file in sorted(cases_dir.glob("*/evidence.jsonl")):
        case_id = ev_file.parent.name
        for line in ev_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield case_id, json.loads(line)
            except ValueError:
                continue


def _deco(ax) -> None:
    """横向条图族通用装饰（去三脊、淡网格、无左刻度线——同前两张图）。"""
    ax.xaxis.grid(True, color="0.88", linewidth=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)


def _fig_h(n_rows: int, base: float = 1.4, per: float = 0.24,
           top: float = 2.6) -> float:
    """行数驱动的图高（45 例全量也不挤成一条线）。"""
    return max(top, per * n_rows + base)


def _fmt_s(sec: float) -> str:
    sec = float(sec)
    if sec < 10:
        return f"{sec:.1f}s"
    if sec < 60:
        return f"{sec:.0f}s"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m{s:02d}s"


def _save(run_dir: Path, fig, name: str) -> str:
    fig.savefig(run_dir / name, dpi=200, bbox_inches="tight", pad_inches=0.1)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return name


def _latency(run_dir: Path) -> str | None:
    """三阶段单次回复耗时分布（对数轴带状散点 + 中位菱标）。

    价值闸门：有效样本 ≥8 且最慢 ≥0.2s——mock 级瞬时回复（恒 ~1ms）
    画出来只有一团噪点，不画。
    """
    lat: dict[str, list[float]] = {}
    for _cid, ev in _iter_evidence(run_dir):
        if ev.get("type") != "dialogue":
            continue
        for r in (ev.get("payload") or {}).get("replies") or []:
            ms = r.get("latency_ms") or 0
            if ms > 0:  # RUNTIME_ERROR 占位回复 latency=0，不是真实耗时
                lat.setdefault(ev.get("phase", "?"), []).append(ms / 1000)
    phases = [p for p in ("inject", "confound", "probe") if lat.get(p)]
    vals = [v for p in phases for v in lat[p]]
    if len(vals) < 8 or max(vals, default=0.0) < 0.2:
        return None
    import matplotlib.pyplot as plt
    import numpy as np

    _setup()
    rng = np.random.default_rng(7)  # 固定种子：同数据同图，离线可复现
    fig, ax = plt.subplots(figsize=(8.4, 2.9))
    ys = np.arange(len(phases))[::-1]
    for y, p in zip(ys, phases, strict=True):
        v = np.asarray(lat[p])
        ax.scatter(v, y + (rng.random(len(v)) - 0.5) * 0.3, s=15,
                   color=PHASE_COLOR[p], alpha=0.4, edgecolors="none",
                   zorder=2)
        med = float(np.median(v))
        ax.scatter([med], [y], s=52, marker="D", facecolor="white",
                   edgecolor="#333333", linewidth=1.1, zorder=4)
        ax.text(med, y + 0.32, _fmt_s(med), fontsize=8, ha="center",
                color="#333333", zorder=5)
        ax.text(1.012, y, f"n={len(v)}", fontsize=8, color="#888888",
                transform=ax.get_yaxis_transform(), ha="left", va="center",
                clip_on=False)
    ax.set_yticks(ys)
    ax.set_yticklabels([PHASE_ZH[p] for p in phases], fontsize=11)
    ax.set_xscale("log")
    ax.set_xlim(max(0.03, min(vals) * 0.5), max(vals) * 2.4)
    ax.set_ylim(-0.55, len(phases) - 0.35)
    ax.set_xlabel("单次回复耗时（秒，对数轴）——◇=中位 · 占位回复（latency=0）不计")
    _deco(ax)
    fig.tight_layout()
    return _save(run_dir, fig, "report-latency.png")


def _memory_delta(run_dir: Path) -> str | None:
    """记忆库存量变化：教后 vs 考后条目数哑铃（逐用例）+ 原文留存率。

    价值闸门：两侧快照齐全且至少一侧有条目的用例 ≥2；导出失败
    （dump_ok=False）的用例剔除并计数披露——"导出失败"不冒充"没存"。
    """
    snaps: dict[str, dict[str, list[str]]] = {}
    n_dump_fail = 0
    for cid, ev in _iter_evidence(run_dir):
        if ev.get("type") != "memory_snapshot":
            continue
        pay = ev.get("payload") or {}
        if not pay.get("dump_ok", True):
            n_dump_fail += 1
            continue
        entries = [re.sub(r"\s+", "", str(e.get("content", "")))
                   for e in pay.get("entries") or []]
        snaps.setdefault(cid, {})[ev.get("phase", "?")] = entries
    rows = []
    for cid, by in snaps.items():
        a, b = by.get("inject"), by.get("probe")
        if a is None or b is None or not (a or b):
            continue
        kept = sum(1 for x in a if x in set(b))
        rows.append((cid, len(a), len(b), kept))
    if len(rows) < 2:
        return None
    rets = [k / n for _cid, n, _b, k in rows if n]
    mean_ret = sum(rets) / len(rets) if rets else None
    rows.sort(key=lambda r: (-r[1], r[0]))
    import matplotlib.pyplot as plt
    import numpy as np

    _setup()
    fig, ax = plt.subplots(figsize=(8.4, _fig_h(len(rows))))
    ys = np.arange(len(rows))[::-1]
    xmax = max(max(a, b) for _c, a, b, _k in rows)
    pad = xmax * 0.02 + 0.2
    for y, (_cid, n_a, n_b, kept) in zip(ys, rows, strict=True):
        ax.plot([n_a, n_b], [y, y], color="0.78", linewidth=1.5, zorder=2)
        ax.scatter(n_a, y, s=36, color="#0072B2", zorder=3)
        ax.scatter(n_b, y, s=36, color="#009E73", zorder=4)
        note = f"{n_a}→{n_b}" + (f"（留 {kept}/{n_a}）" if n_a else "（教后空库）")
        ax.text(max(n_a, n_b) + pad, y, note, fontsize=7.5, va="center",
                color="#555555")
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=7.5)
    ax.set_xlim(0, xmax + pad * 14)
    ax.set_xlabel("记忆库条目数（●蓝=教后 · ●绿=考后）")
    _deco(ax)
    sub = "教后 → 考后存量与留存"
    if mean_ret is not None:
        sub += f"　教后条目原文全匹配留存均值 {mean_ret:.0%}"
    if n_dump_fail:
        sub += f"　{n_dump_fail} 次导出失败剔除"
    ax.set_title(sub, loc="left", fontsize=8.5, color="0.4")
    fig.tight_layout()
    return _save(run_dir, fig, "report-memory.png")


def _durations(run_dir: Path) -> str | None:
    """逐用例回合耗时堆积条（教/扰/考分色，按时长降序）+ 拨钟标记。

    价值闸门：合计 ≥0.2s 的用例 ≥3（mock 瞬时回合不画）。
    """
    secs: dict[str, dict[str, float]] = {}
    clocks: dict[str, int] = {}
    for cid, ev in _iter_evidence(run_dir):
        off = ev.get("clock_offset_days") or 0
        if off > 0:
            clocks[cid] = max(clocks.get(cid, 0), off)
        if ev.get("type") != "dialogue":
            continue
        for r in (ev.get("payload") or {}).get("replies") or []:
            ms = r.get("latency_ms") or 0
            if ms > 0:
                d = secs.setdefault(cid, {})
                ph = ev.get("phase", "?")
                d[ph] = d.get(ph, 0.0) + ms / 1000
    rows = [(cid, d) for cid, d in secs.items() if sum(d.values()) >= 0.2]
    if len(rows) < 3:
        return None
    rows.sort(key=lambda kv: sum(kv[1].values()), reverse=True)
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import FuncFormatter

    _setup()
    fig, ax = plt.subplots(figsize=(8.4, _fig_h(len(rows))))
    ys = np.arange(len(rows))[::-1]
    for ph in ("inject", "confound", "probe"):
        vals = np.array([d.get(ph, 0.0) for _c, d in rows])
        if not vals.any():
            continue
        ax.barh(ys, vals, height=0.62, color=PHASE_COLOR[ph],
                label=PHASE_ZH[ph], edgecolor="white", linewidth=0.4,
                alpha=0.9, zorder=2)
    top = max(sum(d.values()) for _c, d in rows)
    for y, (cid, d) in zip(ys, rows, strict=True):
        tot = sum(d.values())
        badge = f"　拨钟+{clocks[cid]}天" if cid in clocks else ""
        ax.text(tot + top * 0.015, y, _fmt_s(tot) + badge, fontsize=7.5,
                va="center", color="#D55E00" if badge else "#555555")
    ax.set_yticks(ys)
    ax.set_yticklabels([cid for cid, _d in rows], fontsize=7.5)
    ax.set_xlim(0, top * 1.32)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: _fmt_s(v)))
    ax.set_xlabel("回合耗时合计 = 教+扰+考全部回复延迟（判卷/证据采集开销另计）")
    _deco(ax)
    ax.legend(frameon=False, ncol=3, loc="lower right", fontsize=8.5)
    fig.tight_layout()
    return _save(run_dir, fig, "report-durations.png")


def _probe_history(run_dir: Path, cases: dict) -> str | None:
    """同智能体历轮探测点通过点阵：稳定失分点 vs 偶发翻转。

    价值闸门：同适配器 ≥2 轮（单轮无"跨轮"可言）；全轮全对的探测点
    无信息量不列；超过 40 行截断（更全的明细 verdicts.jsonl 可下钻）。
    """
    try:
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    adapter = manifest.get("adapter", "")
    if not adapter:
        return None
    runs: list[tuple[str, dict[str, tuple[str, bool]]]] = []
    for d in sorted(run_dir.parent.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        mfile, vfile = d / "manifest.json", d / "verdicts.jsonl"
        if not (mfile.is_file() and vfile.is_file()):
            continue
        try:
            if json.loads(mfile.read_text(encoding="utf-8")
                          ).get("adapter") != adapter:
                continue
            per: dict[str, tuple[str, bool]] = {}
            for line in vfile.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    per[rec.get("probe_id", "")] = (
                        rec.get("verdict", ""), bool(rec.get("degraded")))
        except (OSError, ValueError):
            continue
        runs.append((d.name, per))
    if len(runs) < 2:
        return None
    from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER
    order = {c.value: i for i, c in enumerate(CAP_ORDER)}
    cap_of = {p.id: case.capability.value
              for case in cases.values() for p in case.probes}
    rows, n_all_pass = [], 0
    for pid in sorted(cap_of):
        outs: list[str] = []
        present = correct = countable = 0
        for _rid, per in runs:
            hit = per.get(pid)
            if hit is None:
                outs.append("absent")
                continue
            v, degraded = hit
            present += 1
            if v == "correct" and not degraded:
                correct += 1
                countable += 1
                outs.append("correct")
            elif v in ("invalid_run", "human_review") or degraded:
                outs.append("invalid")
            else:
                countable += 1
                outs.append("wrong")
        if present < 2:
            continue
        if countable and correct == countable:
            n_all_pass += 1
            continue
        rows.append((cap_of[pid], pid, outs, correct, countable))
    if not rows:
        return None
    rows.sort(key=lambda r: (order.get(r[0], 99),
                             r[3] / r[4] if r[4] else 0.0, r[1]))
    shown = rows[:40]
    import matplotlib.pyplot as plt
    import matplotlib.transforms as mtransforms
    import numpy as np
    from matplotlib.lines import Line2D

    _setup()
    fig, ax = plt.subplots(figsize=(8.6, _fig_h(len(shown), top=2.8)))
    ys = np.arange(len(shown))[::-1]
    cell = {"correct": ("#009E73", 40, "#009E73"),
            "wrong": ("#D55E00", 40, "#D55E00"),
            "invalid": ("#CCCCCC", 26, "#CCCCCC"),
            "absent": ("none", 14, "#BBBBBB")}
    for y, (_cap, _pid, outs, n_ok, n_cnt) in zip(ys, shown, strict=True):
        for x, o in enumerate(outs):
            face, size, edge = cell[o]
            ax.scatter(x, y, s=size, marker="o", facecolor=face,
                       edgecolor="none" if o != "absent" else edge,
                       linewidth=0.8, zorder=3)
        ax.text(len(runs) - 0.42, y, f"{n_ok}/{n_cnt}" if n_cnt else "全无效",
                fontsize=7, va="center", color="#555555", clip_on=False)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[1] for r in shown], fontsize=7)
    ax.set_xticks(range(len(runs)))
    ax.set_xticklabels([f"{'◆' if rid == run_dir.name else ''}{rid[4:8]}·{rid[9:15]}"
                        for rid, _p in runs], fontsize=7.5)
    ax.set_xlim(-0.6, len(runs) + 0.9)
    ax.set_ylim(-0.7, len(shown) - 0.3)
    _deco(ax)
    trans = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
    prev = None
    for y, (cap, *_r) in zip(ys, shown, strict=True):
        if cap != prev:
            if prev is not None:
                ax.axhline(y + 0.5, color="0.9", linewidth=0.8, zorder=1)
            ax.text(-0.015, y, CAP_LABELS_ZH.get(cap, cap), transform=trans,
                    ha="right", va="center", fontsize=7.5, color="#777777")
            prev = cap
    handles = [Line2D([], [], marker="o", ls="", color=c, markersize=6,
                      label=lab)
               for c, lab in (("#009E73", "正确"), ("#D55E00", "失分"),
                              ("#CCCCCC", "无效/未决/降级"))]
    handles.append(Line2D([], [], marker="o", ls="", markerfacecolor="none",
                          markeredgecolor="#BBBBBB", markersize=4.5,
                          label="该轮未考"))
    ax.legend(handles=handles, frameon=False, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.08), fontsize=8)
    title = (f"{adapter} · {len(runs)} 轮重跑点阵（◆=本轮）"
             f"　全轮全对 {n_all_pass} 个未列")
    if len(rows) > len(shown):
        title += f"　另 {len(rows) - len(shown)} 个失分点截断"
    ax.set_title(title, loc="left", fontsize=8.5, color="0.4")
    fig.tight_layout()
    return _save(run_dir, fig, "report-probe-history.png")


def _case_scores(run_dir: Path, verdicts, cases: dict) -> str | None:
    """逐用例计分通过率排序（棒棒糖，低分在上，点色=能力维）。

    价值闸门：有计分判定的用例 ≥3；全无效用例计数披露，不冒充 0 分。
    """
    from memhall.schema.models_case import probe_role
    idx = {p.id: (case, p) for case in cases.values() for p in case.probes}
    per: dict[str, list] = {}
    for v in verdicts:
        entry = idx.get(v.probe_id)
        if entry is not None and probe_role(*entry) != "score":
            continue
        per.setdefault(v.case_id, []).append(v)
    rows, n_void = [], 0
    for cid, vs in per.items():
        val = [v for v in vs
               if v.verdict.value not in ("invalid_run", "human_review")
               and not v.degraded]
        if not val:
            n_void += 1
            continue
        n_ok = sum(1 for v in val if v.verdict.value == "correct")
        cap = cases[cid].capability.value if cid in cases else "?"
        rows.append((cid, n_ok / len(val), n_ok, len(val), cap))
    if len(rows) < 3:
        return None
    rows.sort(key=lambda r: (r[1], r[0]))
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D

    from memhall.report.metrics import CAP_LABELS_ZH
    _setup()
    fig, ax = plt.subplots(figsize=(8.4, _fig_h(len(rows), top=2.7)))
    ys = np.arange(len(rows))[::-1]
    for y, (_cid, rate, n_ok, n, cap) in zip(ys, rows, strict=True):
        color = CAP_COLOR.get(cap, "#999999")
        ax.hlines(y, 0, rate * 100, color=color, alpha=0.45, linewidth=1.6,
                  zorder=2)
        ax.scatter(rate * 100, y, s=46, color=color, zorder=3)
        ax.text(rate * 100 + 1.6, y, f"{n_ok}/{n}", fontsize=7.5,
                va="center", color="#555555")
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=7.5)
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("用例通过率（%，计分探测点口径）")
    _deco(ax)
    caps_in = {r[4] for r in rows}
    handles = [Line2D([], [], marker="o", ls="",
                      color=CAP_COLOR.get(c, "#999999"), markersize=6,
                      label=CAP_LABELS_ZH.get(c, c))
               for c in CAP_ORDER_KEYS if c in caps_in]
    if handles:
        ax.legend(handles=handles, frameon=False, ncol=min(6, len(handles)),
                  loc="lower right", fontsize=8)
    note = "低分在上——哪几道题最不会（诊断探测/无效/未决剔除）"
    if n_void:
        note += f"；{n_void} 例无有效计分判定未列"
    ax.set_title(note, loc="left", fontsize=8.5, color="0.4")
    fig.tight_layout()
    return _save(run_dir, fig, "report-case-scores.png")


def render_panels(run_dir: Path, verdicts, cases: dict) -> list[str]:
    """生成报告辅助图，返回可嵌入 report.md 的 markdown 行列表。

    每张图自带价值闸门（数据不足/无信息量 → None 跳过）：不为凑数画空图。
    """
    lines: list[str] = []
    for name, path in (
            ("判定构成", _verdict_mix(run_dir, verdicts, cases)),
            ("判卷口径对照", _caliber(run_dir, verdicts, cases)),
            ("回答延迟分布", _latency(run_dir)),
            ("记忆库存量变化", _memory_delta(run_dir)),
            ("用例耗时构成", _durations(run_dir)),
            ("探测点跨轮稳定性", _probe_history(run_dir, cases)),
            ("用例正确率排序", _case_scores(run_dir, verdicts, cases))):
        if path:
            lines.append(f"![{name}]({path})")
            lines.append("")
    return lines
