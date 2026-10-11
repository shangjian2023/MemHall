"""六维雷达图渲染（移植自 okim-bench/scoring/radar.py，轴名对齐主线六能力）。

matplotlib + CJK 字体回退链；openKylin 上依赖 fonts-noto-cjk。
"""

from __future__ import annotations

import json

# R56：六维顺序/中文标签单源（metrics 定义），radar/compare 都从这里取——
# 此前双份手维护，新增/改名漏一边就雷达图轴错位
from memhall.report.metrics import CAP_LABELS_ZH, CAP_ORDER  # noqa: F401

FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                   "WenQuanYi Zen Hei", "DejaVu Sans"]


def _setup_font():
    import matplotlib
    from matplotlib import font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in FONT_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def _wrap_subtitle(subtitle: str, width: int = 46) -> str:
    """口径披露行断行：优先按「；」分段（口径串天然 ； 分隔），超宽再硬折。"""
    import textwrap
    parts = [p for seg in subtitle.split("；") for p in (seg.strip(),)]
    lines: list[str] = []
    cur = ""
    for p in parts:
        cand = f"{cur}；{p}" if cur else p
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = p if len(p) <= width else "\n".join(
                textwrap.wrap(p, width=width, break_long_words=True))
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def render_radar(agent_scores: dict[str, dict[str, float]], out_path: str,
                 title: str = "麟阁 MemHall 六维记忆能力对比",
                 subtitle: str = "") -> str:
    """agent_scores: {agent_name: {capability: 0-1}}，输出 PNG 路径。

    subtitle：口径披露行（版本/模型/轮数）——对外对比图必带，放标题下方。

    布局（2026-10-11 重排，修首图文字错位遮挡）：标题/副题/图例全走
    figure 级坐标带——axes 级 set_title(pad) 与 ax.text(1.075) 的坐标带
    重叠是老版遮挡根源，且长副题不换行会把 tight bbox 拉歪、锚在
    (1.25,1.1) 的图例越界进一步挤压正文区。现在：题带（0.98→0.90）/
    极区（subplots_adjust top=0.84）/ 底部图例带 + 构念注脚，互不侵占。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    _setup_font()
    labels = [CAP_LABELS_ZH[c] for c in CAP_ORDER]
    angles = np.linspace(0, 2 * np.pi, len(CAP_ORDER), endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7.4), subplot_kw={"polar": True})
    fig.subplots_adjust(top=0.84, bottom=0.13)
    for agent, scores in agent_scores.items():
        # None = 该维未测（不是 0 分）：画 NaN 留缺口，不与"考砸了"混淆
        values = [v if (v := scores.get(c)) is not None else float("nan")
                  for c in CAP_ORDER]
        values += values[:1]
        ax.plot(angles, values, linewidth=2, label=agent)
        if not any(v != v for v in values):  # 全轴有值才填充（NaN 区不涂）
            ax.fill(angles, values, alpha=0.12)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=11)
    ax.tick_params(axis="x", pad=14)   # 轴名离外圈留呼吸，防与刻度粘连
    ax.set_ylim(0, 1)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=8,
                       color="0.4")
    # 题带：title 在 fig 0.97（va=bottom），副题从 0.945 向下排——两带错开
    fig.suptitle(title, fontsize=13, y=0.97, va="bottom")
    if subtitle:
        fig.text(0.5, 0.945, _wrap_subtitle(subtitle), ha="center",
                 va="top", fontsize=8.5, color="0.35", linespacing=1.5)
    # 图例横向铺底（不再右上角悬空压轴名/数据）
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center",
               bbox_to_anchor=(0.5, 0.055), ncol=len(agent_scores),
               frameon=False, fontsize=10)
    # R53 构念注脚：六维是设计先验切分，recall/persist 机制同源、temporal 计入
    # recall——轴间不独立（维度区分度表见 dataset-card），读图勿当六个独立因子
    fig.text(0.5, 0.015, "六维为设计切分（构念有重叠，见 dataset-card §8）",
             ha="center", fontsize=7.5, color="0.45")
    fig.savefig(out_path, dpi=300)
    plt.close(fig)
    return out_path


def render_radar_from_metrics(metrics_by_agent: dict[str, str], out_path: str,
                              title: str = "麟阁 MemHall 六维记忆能力对比") -> str:
    """{agent: metrics.json 路径} → PNG。"""
    agent_scores = {}
    for agent, path in metrics_by_agent.items():
        with open(path, encoding="utf-8") as f:
            agent_scores[agent] = json.load(f)["capability_scores"]
    return render_radar(agent_scores, out_path, title)
