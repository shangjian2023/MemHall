"""六维雷达图渲染（交付 e 演示视频同款）。

matplotlib + CJK 字体回退链；openKylin 上依赖 fonts-noto-cjk（environment.md §1）。
多智能体叠加对比，输出 PNG。
"""
from __future__ import annotations

import json

CAP_LABELS_ZH = {
    "retention": "长期保持",
    "recall": "记忆调用",
    "update": "动态更新",
    "discrimination": "相近区分",
    "boundary": "边界识别",
    "reuse": "任务复用",
}
CAP_ORDER = ["retention", "recall", "update", "discrimination", "boundary", "reuse"]

FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans"]


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
                 title: str = "okim-bench 六维记忆能力对比") -> str:
    """agent_scores: {agent_name: {capability: 0-1 score}}，输出 PNG 路径。"""
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
        values = [scores.get(c, 0.0) for c in CAP_ORDER]
        values += values[:1]
        ax.plot(angles, values, linewidth=2, label=agent)
        ax.fill(angles, values, alpha=0.12)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_title(title, fontsize=14, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    plt.close(fig)
    return out_path


def render_radar_from_metrics(metrics_by_agent: dict[str, str], out_path: str,
                              title: str = "okim-bench 六维记忆能力对比") -> str:
    """从 metrics.json 文件路径字典渲染：{agent: metrics.json path}。"""
    agent_scores = {}
    for agent, path in metrics_by_agent.items():
        with open(path, encoding="utf-8") as f:
            agent_scores[agent] = json.load(f)["capability_scores"]
    return render_radar(agent_scores, out_path, title)
