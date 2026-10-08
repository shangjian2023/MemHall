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


def render_radar(agent_scores: dict[str, dict[str, float]], out_path: str,
                 title: str = "麟阁 MemHall 六维记忆能力对比",
                 subtitle: str = "") -> str:
    """agent_scores: {agent_name: {capability: 0-1}}，输出 PNG 路径。

    subtitle：口径披露行（版本/模型/轮数）——对外对比图必带，放标题下方。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    _setup_font()
    labels = [CAP_LABELS_ZH[c] for c in CAP_ORDER]
    angles = np.linspace(0, 2 * np.pi, len(CAP_ORDER), endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={"polar": True})
    for agent, scores in agent_scores.items():
        # None = 该维未测（不是 0 分）：画 NaN 留缺口，不与"考砸了"混淆
        values = [v if (v := scores.get(c)) is not None else float("nan")
                  for c in CAP_ORDER]
        values += values[:1]
        ax.plot(angles, values, linewidth=2, label=agent)
        if not any(v != v for v in values):  # 全轴有值才填充（NaN 区不涂）
            ax.fill(angles, values, alpha=0.12)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_title(title, fontsize=14, pad=20)
    if subtitle:
        ax.text(0.5, 1.075, subtitle, transform=ax.transAxes, ha="center",
                fontsize=8.5, color="0.35")
    ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))
    # R53 构念注脚：六维是设计先验切分，recall/persist 机制同源、temporal 计入
    # recall——轴间不独立（维度区分度表见 dataset-card），读图勿当六个独立因子
    ax.annotate("六维为设计切分（构念有重叠，见 dataset-card §8）",
                xy=(0.5, 0.02), xycoords="figure fraction",
                ha="center", fontsize=8, color="0.45")
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
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
