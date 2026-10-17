"""Build the README's editable SVG illustrations and copy reviewed report assets.

No experiment is run and no score or application screenshot is synthesized.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "images/readme"
REPORT = ROOT / "docs/contest/submission/assets/refined"
INK = "#23304a"
MUTED = "#58677b"
PALETTES = [
    ("#edf2ff", "#5264ce"),
    ("#e9f7f2", "#22836f"),
    ("#fff2e8", "#bc693b"),
    ("#f3edfb", "#8660af"),
    ("#eaf6fc", "#3282a5"),
    ("#fff7df", "#a17822"),
]


def text(
    x: int, y: int, value: str, size: int = 24, color: str = INK, weight: int = 400, anchor: str = "start"
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>'
    )


def box(x: int, y: int, w: int, h: int, fill: str, stroke: str = "none", radius: int = 22) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'


def arrow(x1: int, y1: int, x2: int, y2: int, color: str = "#8592a7") -> str:
    return (
        (
            f'<path d="M{x1} {y1}H{x2}" fill="none" stroke="{color}" '
            'stroke-width="3" marker-end="url(#arrow)"/>'
        )
        if y1 == y2
        else (
            f'<path d="M{x1} {y1}V{y2}" fill="none" stroke="{color}" '
            'stroke-width="3" marker-end="url(#arrow)"/>'
        )
    )


def icon(kind: str, x: int, y: int, color: str) -> str:
    paths = {
        "clock": '<circle cx="24" cy="24" r="18"/><path d="M24 12v13l9 5"/>',
        "search": '<circle cx="21" cy="21" r="13"/><path d="m31 31 11 11M15 21h12"/>',
        "refresh": (
            '<path d="M39 18A16 16 0 0 0 10 14L6 20m0-10v10h10M9 30a16 16 0 0 0 29 4l4-6m0 10V28H32"/>'
        ),
        "split": (
            '<rect x="5" y="9" width="14" height="29" rx="4"/>'
            '<rect x="29" y="9" width="14" height="29" rx="4"/>'
            '<path d="M24 6v36" stroke-dasharray="3 5"/>'
        ),
        "shield": '<path d="m24 5 16 6v13c0 10-16 19-16 19S8 34 8 24V11Z"/><path d="m16 24 6 6 11-13"/>',
        "folder": '<path d="M5 14h15l5 6h18v21H5ZM5 14V8h15l5 6h18v6"/><path d="m18 30 5 5 10-11"/>',
        "monitor": (
            '<rect x="5" y="7" width="38" height="27" rx="4"/>'
            '<path d="M24 34v9M14 43h20M12 15h15M12 22h24"/>'
        ),
        "terminal": '<rect x="4" y="7" width="40" height="34" rx="5"/><path d="m12 17 7 7-7 7M26 31h10"/>',
        "bell": '<path d="M10 33h28l-4-6V19a10 10 0 0 0-20 0v8ZM20 40h8M24 5v4"/>',
        "box": '<path d="m5 14 19-9 19 9v23l-19 9-19-9Zm0 0 19 10 19-10M24 24v22M15 9l19 10"/>',
        "network": (
            '<rect x="16" y="3" width="16" height="12" rx="3"/>'
            '<rect x="2" y="33" width="16" height="12" rx="3"/>'
            '<rect x="30" y="33" width="16" height="12" rx="3"/>'
            '<path d="M24 15v10H10v8M24 25h14v8"/>'
        ),
        "file": '<path d="M11 4h18l10 10v30H11ZM29 4v12h10M18 25h14M18 33h14"/>',
    }
    return (
        f'<g transform="translate({x} {y})" fill="none" stroke="{color}" '
        f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">{paths[kind]}</g>'
    )


def save(name: str, title: str, height: int, body: str, bg: str = "#ffffff") -> None:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" '
        f'viewBox="0 0 1200 {height}" role="img" aria-labelledby="title">'
        f'<title id="title">{escape(title)}</title>'
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0 10 5 0 10Z" fill="#8592a7"/></marker></defs>'
        f'<rect width="1200" height="{height}" rx="24" fill="{bg}"/>'
        '<g font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif">'
        f"{body}</g></svg>"
    )
    (OUT / name).write_text(svg + "\n", encoding="utf-8")


def heading(title: str, subtitle: str) -> str:
    return text(42, 58, title, 34, weight=700) + text(42, 100, subtitle, 23, MUTED)


def cards(name: str, title: str, subtitle: str, items: list[tuple], columns: int = 3) -> None:
    rows = (len(items) + columns - 1) // columns
    w = (1116 - (columns - 1) * 24) // columns
    body = heading(title, subtitle)
    for i, (label, lines, symbol) in enumerate(items):
        x, y = 42 + (i % columns) * (w + 24), 137 + (i // columns) * 238
        fill, accent = PALETTES[i % len(PALETTES)]
        body += box(x, y, w, 214, fill)
        body += icon(symbol, x + 22, y + 22, accent)
        body += text(x + 84, y + 54, label, 28, weight=700)
        for j, line in enumerate(lines):
            body += text(x + 24, y + 113 + j * 35, line, 23, MUTED)
    save(name, title, 137 + rows * 238 + 4, body)


def build() -> None:
    OUT.mkdir(exist_ok=True)
    cards(
        "capabilities.svg",
        "六项能力，看清记忆的不同侧面",
        "从记住信息，到更新约定，再到真正办对事情。",
        [
            ("长期保持", ["经历后续交互", "原有信息还能用吗？"], "clock"),
            ("记忆调用", ["任务需要时", "能找到相关约定吗？"], "search"),
            ("动态更新", ["用户修改约定", "旧信息会随之更新吗？"], "refresh"),
            ("相近区分", ["相似项目与人物", "会被混在一起吗？"], "split"),
            ("边界识别", ["明确不应保留的信息", "会被继续使用吗？"], "shield"),
            ("任务复用", ["记住的信息", "会落实到文件与行动吗？"], "folder"),
        ],
    )
    cards(
        "scenarios.svg",
        "把评测放进真实使用场景",
        "从个人助理选型，到团队定位问题与版本回归。",
        [
            ("选择合适的助理", ["围绕同一份固定试卷", "观察各项能力与证据"], "search"),
            ("定位记忆问题", ["追查旧约定、串台和遗漏", "对照回答与实际产物"], "file"),
            ("检查版本变化", ["升级前后复用试卷", "复查修复与历史问题"], "refresh"),
        ],
    )
    cards(
        "kylin.svg",
        "为 openKylin 的日常使用设计",
        "发现环境、发起评测、收到通知、打开结果。",
        [
            ("本机智能体探测", ["检查命令与配置线索", "匹配可用评测适配器"], "search"),
            ("UKUI 桌面触达", ["从应用入口启动", "完成提醒与结果打开"], "bell"),
            ("原生 .deb 交付", ["封装程序、题库与依赖", "按版本部署与复查"], "box"),
            ("本机与 SSH", ["直接在麒麟上测试", "也可从另一台电脑管理"], "network"),
            ("系统事件检查", ["重启、权限与网络变化", "在目标环境单独验证"], "monitor"),
            ("固定试卷回归", ["保留题目与内容指纹", "升级后重复检查"], "refresh"),
        ],
    )
    cards(
        "quickstart.svg",
        "三步，先体验完整流程",
        "使用 mock 模拟适配器，无需调用真实模型。",
        [
            ("01  准备环境", ["安装固定依赖", "doctor 检查运行条件"], "terminal"),
            ("02  跑通模拟", ["运行 quick 题包", "体验采集、判卷与归档"], "refresh"),
            ("03  打开报告", ["verify 核查归档", "在界面查看逐题结果"], "monitor"),
        ],
    )
    cards(
        "delivery.svg",
        "选择适合你的使用入口",
        "安装包与源码入口共用同一套题库、评分和报告。",
        [
            ("openKylin", ["原生 .deb 软件包", "麒麟桌面与本机评测"], "box"),
            ("Windows", ["单文件 .exe 入口", "图形界面与远程管理"], "monitor"),
            ("源码与扩展", ["Python · uv · CLI", "新增题目与智能体适配"], "terminal"),
        ],
    )
    body = heading("同一条评测链路，连接不同智能体", "已接入的四款智能体，共用试卷、评分与证据格式。")
    for i, label in enumerate(["Hermes", "kylin-agent", "KylinBot", "OpenClaw"]):
        x = 42 + 288 * i
        fill, accent = PALETTES[i]
        body += box(x, 140, 252, 82, fill)
        body += text(x + 126, 190, label, 28, accent, 700, "middle")
        body += arrow(x + 126, 223, x + 126, 263)
    body += box(42, 277, 1116, 92, "#25334e")
    body += text(600, 332, "MemHall   /   统一适配 · 三阶段执行 · 六维报告", 30, "#ffffff", 700, "middle")
    body += box(42, 397, 728, 106, "#f2f5fa")
    body += text(66, 437, "更多内置适配入口", 25, weight=700)
    body += text(66, 477, "hermes-local · claude-local · qwen-local · opencode", 23, MUTED)
    body += box(794, 397, 364, 106, "#fff7df")
    body += text(818, 437, "mock：工具流程演练", 25, weight=700)
    body += text(818, 477, "独立于真实模型能力评测", 23, MUTED)
    save("ecosystem.svg", "智能体接入生态", 535, body)

    body = heading(
        "从题目到报告，一条链路可以追溯", "统一入口负责执行，判卷按检查类型选择，原始证据随结果保留。"
    )
    for i, (label, desc, symbol) in enumerate(
        [
            ("题库与试卷", "固定任务 · 场景扩展", "file"),
            ("智能体执行", "教 → 隔 → 考", "terminal"),
            ("证据采集", "对话 · 行动 · 文件", "folder"),
        ]
    ):
        x = 42 + i * 382
        fill, accent = PALETTES[i]
        body += box(x, 142, 350, 150, fill)
        body += icon(symbol, x + 23, 165, accent)
        body += text(x + 88, 192, label, 29, weight=700)
        body += text(x + 24, 255, desc, 24, MUTED)
        if i < 2:
            body += arrow(x + 353, 217, x + 377, 217)
    body += arrow(981, 293, 981, 328)
    body += box(42, 342, 1116, 90, "#f0edf8")
    body += text(77, 398, "判卷与解释", 29, "#715898", 700)
    body += text(323, 398, "规则检查文件等确定条件   /   LLM 评估语义回答", 26)
    body += arrow(600, 433, 600, 469)
    body += box(42, 482, 1116, 90, "#eaf5f2")
    body += text(77, 538, "报告与复查", 29, "#247c68", 700)
    body += text(323, 538, "六维画像   ·   逐题判定   ·   原始证据   ·   归档核验", 26)
    save("architecture.svg", "评测架构", 606, body)

    body = heading(
        "真实案例：改过目录，后续是否真的用新目录？", "kylin-agent · update-001 · 20261015-115547 归档"
    )
    body += box(42, 143, 1116, 94, "#edf2ff")
    body += text(68, 184, "教学要求", 25, "#5264ce", 700)
    body += text(252, 184, "旧约定  ~/work/src    →    新约定  ~/dev/src", 29, weight=700)
    body += text(252, 218, "加入干扰、切换会话后，再提问并要求创建 demo 目录。", 23, MUTED)
    for x, label, lines, symbol in [
        (42, "回答检查", ["后续仍回答旧路径", "~/work/src"], "file"),
        (612, "行动检查", ["demo 实际出现在旧目录", "/home/okim/work/src/demo"], "folder"),
    ]:
        body += box(x, 268, 546, 185, "#fff2e8")
        body += icon(symbol, x + 24, 290, "#b97146")
        body += text(x + 91, 326, label, 29, weight=700)
        for j, line in enumerate(lines):
            body += text(x + 26, 380 + j * 37, line, 25, MUTED)
    body += box(42, 483, 1116, 72, "#25334e")
    body += text(600, 528, "回答与行动分开检查，才能看清问题发生在哪里。", 28, "#ffffff", 700, "middle")
    save("paired-check.svg", "目录更新真实案例：回答与行动双侧检查", 585, body)

    body = heading(
        "从一个问题，到一次可追溯的改进", "GitHub 主仓库 · Gitee 镜像 · 公开 Issue / PR / 版本记录"
    )
    labels = [
        ("提交问题", "场景与复现", "file"),
        ("贡献修改", "代码与文档", "terminal"),
        ("检查与审阅", "CI 与维护者", "shield"),
        ("发布与复查", "版本与验收", "box"),
    ]
    for i, (label, desc, symbol) in enumerate(labels):
        x = 42 + i * 288
        fill, accent = PALETTES[i]
        body += box(x, 145, 252, 198, fill)
        body += icon(symbol, x + 24, 165, accent)
        body += text(x + 24, 253, label, 29, weight=700)
        body += text(x + 24, 301, desc, 24, MUTED)
        if i < 3:
            body += arrow(x + 255, 243, x + 282, 243)
    body += box(42, 376, 1116, 67, "#f2f5fa")
    body += text(
        600, 419, "治理规则  ·  贡献指南  ·  依赖许可  ·  维护手册  ·  长期路线", 26, MUTED, anchor="middle"
    )
    save("collaboration.svg", "开源协作与维护流程", 475, body)

    copied = {
        "architecture-detail.png": "original-arch.png",
        "agent-discovery.png": "agent-discovery.png",
        "gateway.png": "gateway.png",
        "sandbox.png": "sandbox.png",
        "verdicts.png": "verdict-flow.png",
        "archive.png": "evidence.png",
        "case-update.png": "contribution-behavior.png",
        "case-boundary.png": "contribution-boundary.png",
    }
    manifest = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    for target, source in copied.items():
        if (REPORT / source).is_file():
            shutil.copyfile(REPORT / source, OUT / target)
        else:
            expected = manifest["files"][target]["sha256"]
            actual = hashlib.sha256((OUT / target).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"Published asset checksum mismatch: {target}")
        manifest["files"][target] = {
            "source": (REPORT / source).relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256((OUT / target).read_bytes()).hexdigest(),
        }
    manifest["generated"] = {
        path.name: {
            "source": "scripts/build_readme_visuals.py",
            "kind": "explanatory SVG, not an application capture",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in OUT.glob("*.svg")
        if path.name != "hero.svg"
    }
    manifest["case_run"] = "20261015-115547-kylin-agent"
    manifest["case_source"] = "docs/contest/submission/current-experiment-20261015/selected-evidence/"
    (OUT / "provenance.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Built {len(manifest['generated'])} SVG diagrams; copied {len(copied)} reviewed assets.")


if __name__ == "__main__":
    build()
