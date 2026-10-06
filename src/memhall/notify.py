"""评测结果触达 UKUI 桌面（design.md §10）。

跑完一轮评测：notify-send 弹通知（含分数与报告路径）+ xdg-open 直接弹出
雷达图——"挂机评测，回来看图"。仅在桌面环境可用时生效（notify-send 存在
且非 SSH 裸会话）；Windows/无头环境静默跳过，评测流程不因此分叉。
"""

from __future__ import annotations

import os
import shutil
import subprocess

from memhall.adapters.base import NO_WINDOW


def desktop_notify(title: str, body: str, open_path: str | None = None) -> None:
    """UKUI/桌面通知 + 可选弹图。任何失败都静默——通知是锦上添花。"""
    exe = shutil.which("notify-send")
    if not exe or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    try:
        subprocess.run([exe, "-a", "麟阁MemHall", "-t", "8000", title, body],
                       capture_output=True, timeout=5, creationflags=NO_WINDOW)
        if open_path and os.path.exists(open_path):
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, open_path],
                                 creationflags=NO_WINDOW)
    except Exception:
        pass


def notify_run_done(adapter: str, score: float | None, n_valid: int, n_total: int,
                    run_dir: str, radar: str | None = None) -> None:
    """评测完成的标配通知。score 0-1；None=未测（R57：不再 TypeError 崩通知）。"""
    score_text = "未测" if score is None else f"{score:.1%}"
    desktop_notify(
        "麟阁评测完成",
        f"{adapter} 总体 {score_text}（有效 {n_valid}/{n_total}）\n报告: {run_dir}",
        open_path=radar,
    )
