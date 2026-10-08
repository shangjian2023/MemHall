"""README 专业图表生成（三张，数据零 LLM 可复现）。

风格来源（skills）：
- scientific-visualization：Okabe-Ito 色盲安全配色、去顶右脊线、mean±误差棒、
  显著性星标、300dpi、PNG（README 场景不用 JPEG）。
- paper-figures 图库 seg042（Nature 柱+误差棒+散点）气质 → 图 1；
  seg033（哑铃/状态点）气质 → 图 2。
- architecture-diagram 设计系统（slate-950 底/语义色/圆角 6/1.5px 描边/
  箭头 marker/网格纹理）→ 图 3 评测管线 SVG。

用法：uv run python scripts/make_readme_figures.py
数据源：runs/_agg-{hermes,kylinbot,openclaw}-dual/aggregate.json（双判聚合）+
stats_uncertainty.py 同源数字（同探测点两轮一致率 / 配对符号检验）。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER

AGENTS = ["hermes", "kylinbot", "openclaw"]
AGENT_LABEL = {"hermes": "Hermes", "kylinbot": "KylinBot", "openclaw": "OpenClaw"}
# Okabe-Ito 色盲安全三色
COLOR = {"hermes": "#0072B2", "kylinbot": "#E69F00", "openclaw": "#009E73"}
NS_GRAY = "#7f7f7f"
OUT = Path("images")
RUNS = Path("runs")

# stats_uncertainty.py 2026-10-08 输出（同源可复算）
AGREE = {"hermes": 47.2, "kylinbot": 73.0, "openclaw": 85.4}   # 六态一致率 %
FLIP = {"hermes": 44, "kylinbot": 23, "openclaw": 13}          # 对错翻转 %
PAIRS = [  # (对比, p, 显著?)
    ("Hermes  vs  KylinBot", 0.002, True),
    ("Hermes  vs  OpenClaw", 0.027, True),
    ("KylinBot  vs  OpenClaw", 1.000, False),
]


def _setup_font() -> None:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                 "WenQuanYi Zen Hei", "DejaVu Sans"]:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def _publication(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=3, width=0.8)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)


def _stars(p: float) -> str:
    return "**" if p <= 0.01 else ("*" if p <= 0.05 else "ns")


def load_caps() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for a in AGENTS:
        d = json.loads((RUNS / f"_agg-{a}-dual" / "aggregate.json")
                       .read_text(encoding="utf-8"))
        out[a] = {c: d["capabilities"][c] for c in CAP_ORDER if c in d["capabilities"]}
    return out


def fig1_six_dim_bars(caps: dict[str, dict[str, dict]]) -> None:
    """六维 mean±std 分组柱 + 各轮散点（n=2，散点=两轮实际值）。"""
    _setup_font()
    fig, ax = plt.subplots(figsize=(9.6, 4.0))
    dims = CAP_ORDER
    n_a, n_d = len(AGENTS), len(dims)
    width = 0.8 / n_a
    x = np.arange(n_d)
    for i, a in enumerate(AGENTS):
        means = [caps[a][d]["mean"] * 100 for d in dims]
        stds = [caps[a][d]["std"] * 100 for d in dims]
        los = [caps[a][d]["min"] * 100 for d in dims]
        his = [caps[a][d]["max"] * 100 for d in dims]
        pos = x - 0.4 + width / 2 + i * width
        ax.bar(pos, means, width * 0.92, color=COLOR[a], alpha=0.88,
               label=AGENT_LABEL[a], zorder=2)
        ax.errorbar(pos, means, yerr=stds, fmt="none",
                    ecolor="#333333", elinewidth=1.1, capsize=2.5, zorder=4)
        # 各轮实际值散点（n=2：min 与 max 即两轮）
        ax.scatter(pos, los, s=11, color="white", edgecolor=COLOR[a],
                   linewidth=0.9, zorder=5)
        ax.scatter(pos, his, s=11, color="white", edgecolor=COLOR[a],
                   linewidth=0.9, zorder=5)
    ax.set_xticks(x)
    ax.set_xticklabels([CAP_LABELS_ZH[d] for d in dims], fontsize=11)
    ax.set_ylim(0, 100)
    ax.set_ylabel("正确率（%）")
    ax.yaxis.grid(True, color="0.88", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=3, loc="upper right", fontsize=10,
              columnspacing=1.4, handlelength=1.2)
    _publication(ax)
    fig.tight_layout()
    fig.savefig(OUT / "fig-six-dim.png", dpi=300)
    plt.close(fig)


def fig2_uncertainty() -> None:
    """测量可信度面板：(a) 同探测点两轮一致率 (b) 配对符号检验显著性。"""
    _setup_font()
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(9.6, 3.1), gridspec_kw={"width_ratios": [1, 1.15]})

    # (a) 一致率横向条 + 翻转率注记
    ys = np.arange(len(AGENTS))[::-1]
    vals = [AGREE[a] for a in AGENTS]
    ax1.barh(ys, vals, height=0.58, color=[COLOR[a] for a in AGENTS],
             alpha=0.88, zorder=2)
    for y, a, v in zip(ys, AGENTS, vals, strict=True):
        ax1.text(v + 1.5, y, f"{v:.0f}%", va="center", fontsize=10,
                 fontweight="bold", color="#222222")
        ax1.text(2, y, f"对错翻转 {FLIP[a]}%", va="center", ha="left",
                 fontsize=8.5, color="white", zorder=6)
    ax1.set_yticks(ys)
    ax1.set_yticklabels([AGENT_LABEL[a] for a in AGENTS], fontsize=10)
    ax1.set_xlim(0, 100)
    ax1.set_xlabel("同探测点两轮六态一致率（%）")
    ax1.xaxis.grid(True, color="0.88", linewidth=0.7, zorder=0)
    ax1.set_axisbelow(True)
    ax1.set_title("(a) 系统内稳定性（n=89 探测点 × 2 轮）",
                  fontsize=10.5, loc="left", pad=8)
    _publication(ax1)
    ax1.spines["left"].set_visible(False)
    ax1.tick_params(left=False)

    # (b) 配对符号检验：三对比较，p 值点在 log 轴上，注释列放右侧固定位置
    ax2.set_title("(b) 排名主张的显著性（配对符号检验）",
                  fontsize=10.5, loc="left", pad=8)
    for i, (_label, p, sig) in enumerate(PAIRS):
        y = len(PAIRS) - 1 - i
        color = "#0072B2" if sig else NS_GRAY
        marker = "*" if sig else "o"
        size = 150 if sig else 46
        ax2.scatter(p, y, s=size, color=color, zorder=3, marker=marker,
                    linewidth=0.6)
        star = _stars(p)
        p_txt = f"p = {p:.3f}" if p < 1 else "p = 1.000"
        note = f"{p_txt}  {star if star != 'ns' else 'ns · 并列'}"
        ax2.text(2.6, y, note, fontsize=9.5, ha="left", va="center",
                 color=color, clip_on=False)
    ax2.set_xscale("log")
    ax2.set_xlim(0.0006, 1.18)
    ax2.set_xticks([0.001, 0.01, 0.05, 0.5, 1.0])
    ax2.set_xticklabels(["0.001", "0.01", "0.05", "0.5", "1.0"], fontsize=8.5)
    ax2.set_yticks(range(len(PAIRS)))
    ax2.set_yticklabels([p[0] for p in reversed(PAIRS)], fontsize=9.5)
    ax2.axvline(0.05, color="#D55E00", linewidth=0.9, linestyle=(0, (4, 3)),
                alpha=0.7, zorder=2)
    ax2.text(0.05, len(PAIRS) - 0.42, "α=0.05", fontsize=8, color="#D55E00",
             ha="center", va="bottom")
    ax2.set_ylim(-0.55, len(PAIRS) - 0.05)
    _publication(ax2)
    ax2.spines["left"].set_visible(False)
    ax2.tick_params(left=False)
    fig.tight_layout()
    fig.savefig(OUT / "fig-uncertainty.png", dpi=300,
                bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)


# ---------- 图 3：评测管线（architecture-diagram 设计系统 → SVG）----------

C = {  # 语义色（fill / stroke）
    "eval":   ("rgba(6, 78, 59, 0.40)", "#34d399"),   # backend emerald：评测器
    "vm":     ("rgba(8, 51, 68, 0.40)",  "#22d3ee"),   # frontend cyan：被测环境
    "evid":   ("rgba(76, 29, 149, 0.40)", "#a78bfa"),  # database violet：证据
    "gw":     ("rgba(120, 53, 15, 0.30)", "#fbbf24"),  # amber：网关
    "judge":  ("rgba(136, 19, 55, 0.40)", "#fb7185"),  # rose：判卷
}
FONT = "JetBrains Mono, Consolas, 'Microsoft YaHei', monospace"


def _box(x: float, y: float, w: float, h: float, fill: str, stroke: str,
         title: str, sub: str = "", tsize: float = 12) -> str:
    lines = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
             f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
             f'<text x="{x + w / 2}" y="{y + h / 2 - (4 if sub else -4)}" '
             f'font-family="{FONT}" font-size="{tsize}" fill="#e2e8f0" '
             f'font-weight="600" text-anchor="middle">{title}</text>']
    if sub:
        lines.append(
            f'<text x="{x + w / 2}" y="{y + h / 2 + 13}" font-family="{FONT}" '
            f'font-size="8.5" fill="#94a3b8" text-anchor="middle">{sub}</text>')
    return "\n".join(lines)


def _arrow(x1: float, y1: float, x2: float, y2: float, label: str = "",
           lx: float = 0, ly: float = 0, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="4,4"' if dashed else ""
    out = [f'<path d="M {x1} {y1} L {x2} {y2}" stroke="#64748b" '
           f'stroke-width="1.5" fill="none" marker-end="url(#arrowhead)"'
           f'{dash}/>'
           f'<path d="M {x1} {y1} L {x2} {y2}" stroke="#020617" '
           f'stroke-width="5" fill="none"{dash} opacity="0"/>']
    if label:
        out.append(
            f'<text x="{lx or (x1 + x2) / 2}" y="{ly or (y1 + y2) / 2 - 5}" '
            f'font-family="{FONT}" font-size="8" fill="#94a3b8" '
            f'text-anchor="middle">{label}</text>')
    return "\n".join(out)


def fig3_pipeline_svg() -> None:
    w, h = 1180, 560
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
        '<defs>',
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" '
        'stroke-width="0.5"/></pattern>',
        '<marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" '
        'refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" '
        'fill="#64748b"/></marker>',
        '</defs>',
        f'<rect width="{w}" height="{h}" fill="#020617"/>',
        f'<rect width="{w}" height="{h}" fill="url(#grid)"/>',
    ]
    # 分区标题
    parts.append('<text x="24" y="34" font-family="' + FONT + '" '
                 'font-size="13" fill="#e2e8f0" font-weight="700">'
                 '麟阁 MemHall 评测管线</text>')
    parts.append('<text x="24" y="52" font-family="' + FONT + '" '
                 'font-size="9" fill="#64748b">bench · judge · report — '
                 '真实智能体运行证据上的自动评测</text>')

    # 左区：评测器（宿主）
    parts.append('<rect x="24" y="76" width="330" height="330" rx="12" '
                 'fill="none" stroke="#34d399" stroke-width="1" '
                 'stroke-dasharray="8,4" opacity="0.55"/>')
    parts.append('<text x="40" y="100" font-family="' + FONT + '" '
                 'font-size="9.5" fill="#34d399">评测器 · 宿主</text>')
    parts.append(_box(44, 114, 290, 64, *C["eval"], "剧本引擎",
                      "教 teach → 隔 confound → 考 probe（47 题三阶段剧本）"))
    parts.append(_box(44, 196, 290, 56, *C["eval"], "运行编排",
                      "SSH 车道 · 断线重连 · run.lock 互斥 · 成本预估"))
    parts.append(_box(44, 270, 138, 56, *C["eval"], "verify",
                      "证据完整性校验"))
    parts.append(_box(196, 270, 138, 56, *C["eval"], "stability",
                      "重跑稳定性 pass^k"))
    parts.append(_box(44, 344, 290, 48, *C["eval"], "aggregate / compare",
                      "N 轮 mean±std · bootstrap CI · 符号检验"))

    # 中区：openKylin VM（被测环境）
    parts.append('<rect x="392" y="76" width="330" height="330" rx="12" '
                 'fill="none" stroke="#22d3ee" stroke-width="1" '
                 'stroke-dasharray="8,4" opacity="0.55"/>')
    parts.append('<text x="408" y="100" font-family="' + FONT + '" '
                 'font-size="9.5" fill="#22d3ee">openKylin 3.0 · 虚拟机（被测）</text>')
    parts.append(_box(412, 114, 290, 64, *C["vm"], "被测智能体",
                      "hermes / kylinbot / openclaw（+ mock 基线）"))
    parts.append(_box(412, 196, 290, 56, *C["vm"], "沙箱隔离",
                      "~/.memhall-* 工作区 · reset 残留防线"))
    parts.append(_box(412, 270, 290, 56, *C["vm"], "系统级测试",
                      "重启存活 · 拨钟隔天 · 多用户 · 断网"))
    parts.append(_box(412, 344, 290, 48, *C["vm"], "UKUI 桌面集成",
                      "deb 原生包 · 菜单入口 · Web UI"))

    # 右上：证据
    parts.append('<rect x="760" y="76" width="396" height="158" rx="12" '
                 'fill="none" stroke="#a78bfa" stroke-width="1" '
                 'stroke-dasharray="8,4" opacity="0.55"/>')
    parts.append('<text x="776" y="100" font-family="' + FONT + '" '
                 'font-size="9.5" fill="#a78bfa">证据（逐条 SHA-256 封印）</text>')
    parts.append(_box(780, 114, 176, 52, *C["evid"], "对话 / 记忆快照",
                      "dialogue · memory_snapshot"))
    parts.append(_box(972, 114, 168, 52, *C["evid"], "行为 / 文件",
                      "actions · fs_diff"))
    parts.append(_box(780, 178, 358, 44, *C["evid"], "verdicts.jsonl",
                      "五态判定 · decided_by 溯源 · output_hashes 对账"))

    # 右下：判卷
    parts.append('<rect x="760" y="254" width="396" height="152" rx="12" '
                 'fill="none" stroke="#fb7185" stroke-width="1" '
                 'stroke-dasharray="8,4" opacity="0.55"/>')
    parts.append('<text x="776" y="278" font-family="' + FONT + '" '
                 'font-size="9.5" fill="#fb7185">判卷（契约 03 v0.2.1）</text>')
    parts.append(_box(780, 292, 176, 52, *C["judge"], "双 LLM 判卷",
                      "A/B 轮值仲裁 · kappa 复核"))
    parts.append(_box(972, 292, 168, 52, *C["judge"], "锚例 + 诱饵",
                      "188 诱饵 100% 拒收"))
    parts.append(_box(780, 356, 358, 38, *C["judge"], "六维报告 · 雷达 · 测量边界",
                      "", tsize=11))

    # 底部车道：统一模型网关
    parts.append('<rect x="24" y="434" width="1132" height="104" rx="12" '
                 'fill="none" stroke="#fbbf24" stroke-width="1" '
                 'stroke-dasharray="8,4" opacity="0.55"/>')
    parts.append('<text x="40" y="458" font-family="' + FONT + '" '
                 'font-size="9.5" fill="#fbbf24">统一模型网关（被测流量必经）</text>')
    parts.append(_box(44, 472, 258, 52, *C["gw"], "model 强制改写",
                      "配置漂移无效 · 统一 qwen3.7-plus"))
    parts.append(_box(322, 472, 258, 52, *C["gw"], "限速 + 指数退避",
                      "8s 节流排队 · 5xx/429 重试"))
    parts.append(_box(600, 472, 258, 52, *C["gw"], "token 记账",
                      "逐请求账单 · 归因到智能体"))
    parts.append(_box(878, 472, 258, 52, *C["gw"], "凭据单点",
                      "真 key 只在网关进程"))

    # 箭头（先画在盒后仍被盒盖住？——SVG 文档序绘制，箭头置于最后置于上层，
    # 用短引线只连区域边缘，避免穿盒）
    parts.append(_arrow(354, 160, 392, 160, "SSH 车道", 373, 150))
    parts.append(_arrow(722, 160, 760, 160, "采集", 741, 150))
    parts.append(_arrow(957, 232, 957, 254))            # 证据 → 判卷
    parts.append(_arrow(1136, 330, 1160, 330, "", 0, 0))  # 判卷 → 报告（右出界内收）
    parts.append(_arrow(189, 406, 189, 434, "", 0, 0))    # 评测器 ↔ 网关
    parts.append(_arrow(557, 406, 557, 434, "", 0, 0))    # VM → 网关
    parts.append('<text x="600" y="530" font-family="' + FONT + '" '
                 'font-size="8" fill="#94a3b8" text-anchor="middle">'
                 'agents → 网关（memhall-* dummy key）→ 上游 · 判卷流量同经网关'
                 '</text>')
    parts.append('</svg>')
    (OUT / "pipeline-dark.svg").write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    caps = load_caps()
    fig1_six_dim_bars(caps)
    fig2_uncertainty()
    fig3_pipeline_svg()
    for name in ["fig-six-dim.png", "fig-uncertainty.png", "pipeline-dark.svg"]:
        p = OUT / name
        print(f"{name}: {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
