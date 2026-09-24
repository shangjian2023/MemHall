"""L3 双 LLM judge 交叉评分 + 仲裁 + 一致性度量。

设计约束（调研报告 §3.2 实证结论）：
- 双评委必须跨家族（JUDGE_A / JUDGE_B 不同厂商模型）
- 结构化输出，无 evidence_refs 的评分无效
- 两位不一致 → 仲裁（judge_a 带双方理由复议一次）
- 评委间 Cohen's Kappa 写入报告，低于闸门阈值的结果标 needs_review
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional, Protocol

from .models import EvidenceBundle, JudgeVerdict, ProbeSpec

VALID_VERDICTS = {"correct", "omission", "confusion", "false_persistence", "false_reuse"}

JUDGE_PROMPT = """你是记忆评测评委。根据探测项契约与证据包，判定被测智能体的表现属于哪一类：
correct(正确记忆/复用) | omission(遗漏) | confusion(混淆) | false_persistence(错误持久化) | false_reuse(错误复用)。

规则：
1. 只能依据证据包中的内容，禁止臆测；评分必须引用具体证据（evidence_refs），无证据不得分。
2. 回答与标准答案语义等价即 correct；命中契约 outdated_answers 中列出的过期/被覆盖旧值
   （含其简称、局部写法，如"西湖区"之于"杭州市西湖区"）一律判 false_reuse，优先于 confusion；
   命中契约 distractor_patterns 中列出的相近干扰实体才判 confusion。
3. "语义等价"指关键识别信息一致（实体名、目录名、文件名、取值），不要求逐字包含完整字符串；
   但考点本身的区分信息缺失（如考点是位置却只给文件名）不算等价。
4. 输出严格 JSON：{{"verdict": "...", "confidence": 0-1, "evidence_refs": ["..."], "reason": "..."}}

探测项契约：
{spec}

证据包：
{evidence}

请输出 JSON。"""

ARBITER_PROMPT = """你是仲裁评委。两位评委对同一记忆评测探测项给出不同结论，请复议并给出最终结论。

探测项契约：
{spec}

证据包：
{evidence}

评委A结论：{verdict_a}
评委B结论：{verdict_b}

规则同上：只依据证据、必须给出 evidence_refs、输出严格 JSON：
{{"verdict": "...", "confidence": 0-1, "evidence_refs": ["..."], "reason": "..."}}"""


class Judge(Protocol):
    name: str
    def complete(self, prompt: str) -> str: ...


class OpenAICompatJudge:
    """OpenAI 兼容端点评委（DeepSeek / Qwen 等），key 从环境变量读取。"""

    def __init__(self, name: str, base_url: str, model: str, api_key: str,
                 temperature: float | None = None):
        self.name, self.base_url, self.model, self.api_key = name, base_url, model, api_key
        # temperature=None 时不传该字段（部分端点只允许特定值，如 Kimi 网关仅允许 1）
        self.temperature = temperature

    @classmethod
    def from_env(cls, which: str) -> "OpenAICompatJudge":
        prefix = f"JUDGE_{which}"
        return cls(
            name=os.environ.get(f"{prefix}_MODEL", "unknown"),
            base_url=os.environ[f"{prefix}_BASE_URL"],
            model=os.environ.get(f"{prefix}_MODEL", "unknown"),
            api_key=os.environ[f"{prefix}_KEY"],
            temperature=float(os.environ[f"{prefix}_TEMP"]) if os.environ.get(f"{prefix}_TEMP") else None,
        )

    def complete(self, prompt: str) -> str:
        import urllib.request
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]


def parse_verdict(raw: str) -> Optional[JudgeVerdict]:
    """解析 judge 输出；结构非法或无证据引用 → None（该票无效，按缺失处理）。"""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        verdict = str(data["verdict"]).strip().lower()
        if verdict not in VALID_VERDICTS:
            return None
        refs = [str(r) for r in data.get("evidence_refs", [])]
        if not refs:  # 无证据不得分
            return None
        return JudgeVerdict(verdict, float(data.get("confidence", 0.5)), refs, str(data.get("reason", "")))
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


@dataclass
class DualJudgeResult:
    verdict: Optional[str]
    judge_a: Optional[str]
    judge_b: Optional[str]
    confidence: float
    evidence_refs: list[str]
    reason: str
    arbitrated: bool
    va: Optional[str] = None   # 评委A原始 verdict（无效票为 None）
    vb: Optional[str] = None


def _render(spec: ProbeSpec, evidence: EvidenceBundle) -> str:
    spec_d = {
        "kind": spec.kind, "capability": spec.capability, "question": spec.question,
        "ground_truth": spec.ground_truth,
        "expect_store_retain": spec.expect_store_retain,
        "expect_store_absent": spec.expect_store_absent,
        "expect_abstention": spec.expect_abstention,
        "outdated_answers": spec.outdated_answers,
        "distractor_patterns": spec.distractor_patterns,
    }
    ev_d = {
        "answer": evidence.answer,
        "memory_entries": [
            {"source": e.source, "text": e.text} for e in (evidence.memory_after.entries if evidence.memory_after else [])
        ],
        "artifacts": list(evidence.artifacts.keys()),
    }
    return json.dumps(spec_d, ensure_ascii=False), json.dumps(ev_d, ensure_ascii=False)


def dual_judge(spec: ProbeSpec, evidence: EvidenceBundle,
               judge_a: Judge, judge_b: Judge) -> DualJudgeResult:
    spec_s, ev_s = _render(spec, evidence)
    va = parse_verdict(judge_a.complete(JUDGE_PROMPT.format(spec=spec_s, evidence=ev_s)))
    vb = parse_verdict(judge_b.complete(JUDGE_PROMPT.format(spec=spec_s, evidence=ev_s)))

    if va and vb and va.verdict == vb.verdict:
        return DualJudgeResult(va.verdict, judge_a.name, judge_b.name,
                               (va.confidence + vb.confidence) / 2,
                               sorted(set(va.evidence_refs + vb.evidence_refs)),
                               f"双评委一致: {va.reason} | {vb.reason}", False,
                               va=va.verdict, vb=vb.verdict)
    # 不一致（或某票无效）→ 仲裁
    raw = judge_a.complete(ARBITER_PROMPT.format(
        spec=spec_s, evidence=ev_s,
        verdict_a=va.verdict if va else "无效（未按格式输出或无证据引用）",
        verdict_b=vb.verdict if vb else "无效（未按格式输出或无证据引用）"))
    arb = parse_verdict(raw)
    if arb:
        return DualJudgeResult(arb.verdict, judge_a.name, judge_b.name,
                               arb.confidence, arb.evidence_refs,
                               f"仲裁结论: {arb.reason}", True,
                               va=va.verdict if va else None, vb=vb.verdict if vb else None)
    return DualJudgeResult(None, judge_a.name, judge_b.name, 0.0, [],
                           "仲裁输出无效", True,
                           va=va.verdict if va else None, vb=vb.verdict if vb else None)


def cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """评委间一致性（Cohen's Kappa），写入报告；< 0.6 触发 needs_review 闸门。"""
    n = len(labels_a)
    if n == 0:
        return 1.0
    labels = sorted(set(labels_a) | set(labels_b))
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    from collections import Counter
    ca, cb = Counter(labels_a), Counter(labels_b)
    pe = sum((ca[l] / n) * (cb[l] / n) for l in labels)
    return 1.0 if pe == 1.0 else (po - pe) / (1 - pe)
