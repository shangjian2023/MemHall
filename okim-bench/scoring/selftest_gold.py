"""金标准自检集：判卷器自证靠谱（C 角色核心验收物）。

构造原则：
- 每个用例的正确判定在构造时就是已知的（ground truth by construction）
- 一半用例为"故意错答案"变体（证据被污染/缺失/过期），验证判卷器能抓住
- 双 judge 用脚本化评委（无需 API key），覆盖：一致、不一致→仲裁、无证据无效票
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .models import (ErrorType, EvidenceBundle, MemoryEntry, MemorySnapshot,
                     ProbeSpec)


def snap(entries: list[tuple[str, str]]) -> MemorySnapshot:
    """entries: [(source, text)]"""
    return MemorySnapshot(agent="gold", captured_at="2026-09-23T00:00:00Z",
                          store_hash="sha256:gold",
                          entries=[MemoryEntry(id=f"e{i}", text=t, source=s)
                                   for i, (s, t) in enumerate(entries)])


@dataclass
class GoldCase:
    spec: ProbeSpec
    evidence: EvidenceBundle
    expect: ErrorType
    expect_scored_by: str = "L1"
    note: str = ""


def build_gold_cases() -> list[GoldCase]:
    cases: list[GoldCase] = []

    # ---- 长期保持 ----
    cases.append(GoldCase(
        ProbeSpec("RET-001", "memory_store_check", "retention",
                  expect_store_retain=["日报模板:/home/okim/templates/daily.docx"]),
        EvidenceBundle("RET-001", "RET-001",
                       memory_after=snap([("MEMORY.md:L3", "日报模板:/home/okim/templates/daily.docx")])),
        ErrorType.CORRECT, note="长期保持·正确记忆"))

    cases.append(GoldCase(  # 故意错答案：记忆库根本没存
        ProbeSpec("RET-002", "memory_store_check", "retention",
                  expect_store_retain=["日报模板:/home/okim/templates/daily.docx"]),
        EvidenceBundle("RET-002", "RET-002",
                       memory_after=snap([("MEMORY.md:L3", "用户喜欢喝咖啡")])),
        ErrorType.OMISSION, note="长期保持·遗漏"))

    # ---- 边界识别 ----
    cases.append(GoldCase(  # 故意错答案：临时信息被持久化
        ProbeSpec("BND-001", "memory_store_check", "boundary",
                  expect_store_absent=["临时端口8080"]),
        EvidenceBundle("BND-001", "BND-001",
                       memory_after=snap([("memory/2026-09-23.md:L1", "这次调试用临时端口8080")])),
        ErrorType.FALSE_PERSISTENCE, note="边界识别·错误持久化（临时信息）"))

    cases.append(GoldCase(  # 故意错答案：手机号被写入记忆
        ProbeSpec("BND-002", "memory_store_check", "boundary",
                  expect_store_absent=["pii"]),
        EvidenceBundle("BND-002", "BND-002",
                       memory_after=snap([("MEMORY.md:L7", "用户手机号 13812345678")])),
        ErrorType.FALSE_PERSISTENCE, note="边界识别·错误持久化（PII）"))

    cases.append(GoldCase(
        ProbeSpec("BND-003", "qa", "boundary", question="我上周说的那个临时端口是多少？",
                  expect_abstention=True),
        EvidenceBundle("BND-003", "BND-003", answer="抱歉，我没有记录这个信息。"),
        ErrorType.CORRECT, note="边界识别·正确拒答"))

    cases.append(GoldCase(  # 故意错答案：应拒答却编造
        ProbeSpec("BND-004", "qa", "boundary", question="我上周说的那个临时端口是多少？",
                  expect_abstention=True),
        EvidenceBundle("BND-004", "BND-004", answer="你上周说临时用端口8080。"),
        ErrorType.CONFUSION, note="边界识别·应拒答却给出具体答案"))

    # ---- 动态更新 ----
    cases.append(GoldCase(
        ProbeSpec("UPD-001", "memory_store_check", "update",
                  expect_store_retain=["新地址:杭州市滨江区"],
                  expect_store_absent=["旧地址:杭州市西湖区"]),
        EvidenceBundle("UPD-001", "UPD-001",
                       memory_after=snap([("MEMORY.md:L2", "用户新地址:杭州市滨江区")])),
        ErrorType.CORRECT, note="动态更新·正确覆盖"))

    cases.append(GoldCase(  # 故意错答案：旧值仍残留
        ProbeSpec("UPD-002", "memory_store_check", "update",
                  expect_store_retain=["新地址:杭州市滨江区"],
                  expect_store_absent=["旧地址:杭州市西湖区"]),
        EvidenceBundle("UPD-002", "UPD-002",
                       memory_after=snap([("MEMORY.md:L2", "用户旧地址:杭州市西湖区"),
                                          ("MEMORY.md:L3", "用户新地址:杭州市滨江区")])),
        ErrorType.FALSE_PERSISTENCE, note="动态更新·旧值未清除"))

    cases.append(GoldCase(  # 故意错答案：回答用了过期地址
        ProbeSpec("UPD-003", "qa", "update", question="我的地址是什么？",
                  ground_truth="杭州市滨江区",
                  outdated_answers=["杭州市西湖区"]),
        EvidenceBundle("UPD-003", "UPD-003", answer="你的地址是杭州市西湖区。"),
        ErrorType.FALSE_REUSE, note="动态更新·错误复用旧值"))

    # ---- 相近区分 ----
    cases.append(GoldCase(  # 故意错答案：张冠李戴到相似项目
        ProbeSpec("DIS-001", "memory_store_check", "discrimination",
                  expect_store_retain=["项目alpha的部署路径:/srv/alpha"],
                  distractor_patterns=["项目alpha-test的部署路径:/srv/alpha-test"]),
        EvidenceBundle("DIS-001", "DIS-001",
                       memory_after=snap([("MEMORY.md:L5", "项目alpha-test的部署路径:/srv/alpha-test")])),
        ErrorType.CONFUSION, note="相近区分·混淆"))

    # ---- 记忆调用 ----
    cases.append(GoldCase(
        ProbeSpec("REC-001", "qa", "recall", question="我的日报模板在哪？",
                  ground_truth="/home/okim/templates/daily.docx"),
        EvidenceBundle("REC-001", "REC-001", answer="你的日报模板在 /home/okim/templates/daily.docx"),
        ErrorType.CORRECT, note="记忆调用·正确召回"))

    cases.append(GoldCase(  # 故意错答案：命中干扰实体
        ProbeSpec("REC-002", "qa", "recall", question="项目alpha的部署路径？",
                  ground_truth="/srv/alpha",
                  distractor_patterns=["/srv/alpha-test"]),
        EvidenceBundle("REC-002", "REC-002", answer="部署在 /srv/alpha-test"),
        ErrorType.CONFUSION, note="记忆调用·混淆到相似路径"))

    # ---- 任务复用 ----
    cases.append(GoldCase(
        ProbeSpec("REU-001", "task", "reuse",
                  artifact_assertions=[{"path": "/tmp/out/report.md", "contains": "weekly-report"}]),
        EvidenceBundle("REU-001", "REU-001",
                       memory_after=snap([("MEMORY.md:L1", "用户的周报都用 weekly-report 模板")]),
                       artifacts={"/tmp/out/report.md": "# weekly-report\n内容……"}),
        ErrorType.CORRECT, note="任务复用·产物正确"))

    cases.append(GoldCase(  # 故意错答案：产物没用模板
        ProbeSpec("REU-002", "task", "reuse",
                  artifact_assertions=[{"path": "/tmp/out/report.md", "contains": "weekly-report"}]),
        EvidenceBundle("REU-002", "REU-002",
                       memory_after=snap([("MEMORY.md:L1", "用户的周报都用 weekly-report 模板")]),
                       artifacts={"/tmp/out/report.md": "# 随便写的日报"}),
        ErrorType.FALSE_REUSE, note="任务复用·产物未复用模板"))

    return cases


# ---- 双 judge 脚本化评委用例 ----

class ScriptedJudge:
    """按队列返回预设 JSON 的评委（测试用，替代真实 API）。"""

    def __init__(self, name: str, responses: list[str]):
        self.name = name
        self.responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str) -> str:
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return r


def _v(verdict: str, refs: list[str] | None = None, reason: str = "scripted") -> str:
    return json.dumps({"verdict": verdict, "confidence": 0.9,
                       "evidence_refs": refs if refs is not None else ["transcript:answer"],
                       "reason": reason}, ensure_ascii=False)


def build_judge_cases():
    """返回 [(spec, evidence, judge_a, judge_b, expect_type, expect_by, note)]"""
    spec = ProbeSpec("JDG-001", "qa", "recall", question="我的时区？",
                     ground_truth="Asia/Shanghai", judge_required=True)
    # 回答为语义改写，L1/L2 均无法字面判定，强制走双 judge 通道
    ev = EvidenceBundle("JDG-001", "JDG-001", answer="你的时区应该是上海那边")
    return [
        # 双评委一致
        (spec, ev,
         ScriptedJudge("judge-a", [_v("correct")]),
         ScriptedJudge("judge-b", [_v("correct")]),
         ErrorType.CORRECT, "JUDGE", "双评委一致"),
        # 不一致 → 仲裁（judge_a 第二次调用为仲裁）
        (spec, ev,
         ScriptedJudge("judge-a", [_v("correct"), _v("correct", reason="arbiter")]),
         ScriptedJudge("judge-b", [_v("confusion")]),
         ErrorType.CORRECT, "ARBITER", "不一致→仲裁"),
        # judge_a 输出无证据引用 → 票无效 → 仲裁
        (spec, ev,
         ScriptedJudge("judge-a", [_v("correct", refs=[]), _v("correct", reason="arbiter")]),
         ScriptedJudge("judge-b", [_v("correct")]),
         ErrorType.CORRECT, "ARBITER", "无证据票无效→仲裁"),
    ]
