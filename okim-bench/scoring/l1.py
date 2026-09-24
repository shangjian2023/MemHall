"""L1 确定性检查器：能规则判的绝不用 AI。

对应设计文档 §4.1 决策树的 store-level 部分：
- 禁止内容出现     → 错误持久化
- 必留内容缺失     → 遗漏
- 命中干扰项       → 混淆
- 行为命中过期答案 → 错误复用
全部输出必须带 evidence_refs（文件:行 / 消息 ID / 产物路径）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .models import ErrorType, EvidenceBundle, MemoryEntry, ProbeSpec

# ---------- 文本规范化 ----------

def normalize(text: str) -> str:
    """大小写、全半角、空白、常见标点归一，供精确比对。"""
    text = text.strip().lower()
    text = text.replace("　", " ")
    table = str.maketrans({
        "：": ":", "，": ",", "。": ".", "“": '"', "”": '"',
        "（": "(", "）": ")", "、": ",", "；": ";",
    })
    text = text.translate(table)
    text = re.sub(r"\s+", " ", text)
    return text


def contains_pattern(haystack: str, pattern: str) -> bool:
    """pattern 为普通文本时做包含匹配；带 re: 前缀时按正则匹配。均先规范化。"""
    h = normalize(haystack)
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], haystack, re.IGNORECASE) is not None
        except re.error:
            return False
    return normalize(pattern) in h


# ---------- 敏感信息模式（边界识别用） ----------

PII_PATTERNS: dict[str, str] = {
    "cn_id_card": r"\b\d{17}[\dXx]\b",
    "cn_phone": r"\b1[3-9]\d{9}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.]+\b",
    "api_key": r"\b(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})\b",
    "private_key": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "bearer_token": r"\bBearer\s+[A-Za-z0-9._-]{20,}\b",
}


def detect_pii(text: str) -> list[str]:
    """返回命中的敏感类型列表（供边界识别 store absent 检查复用）。"""
    hits = []
    for name, pat in PII_PATTERNS.items():
        if re.search(pat, text):
            hits.append(name)
    return hits


# ---------- L1 检查结果 ----------

@dataclass
class L1Outcome:
    error_type: ErrorType | None   # None = L1 无法判定，升级 L2/judge
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ""
    store_ok: bool = False         # store-level 是否通过（供行为判定参考）


def _entry_refs(entries: list[MemoryEntry]) -> list[str]:
    return [e.source or e.id for e in entries]


def check_store(spec: ProbeSpec, evidence: EvidenceBundle) -> L1Outcome:
    """store-level 判定：错误持久化 > 混淆 > 遗漏（按严重性优先报）。"""
    snap = evidence.memory_after
    if snap is None:
        return L1Outcome(None, reason="无记忆快照，跳过 store 检查")
    entries = snap.entries
    refs = _entry_refs(entries)

    # 1) 禁止内容（错误持久化）——含 expect_store_absent 与内嵌 PII 双重检查
    for pat in spec.expect_store_absent:
        for e in entries:
            if contains_pattern(e.text, pat):
                return L1Outcome(
                    ErrorType.FALSE_PERSISTENCE,
                    evidence_refs=[e.source or e.id],
                    reason=f"不应保留的内容出现在记忆库中，命中禁止模式 [{pat}]",
                )
    for e in entries:
        pii = detect_pii(e.text)
        if pii and any(p in " ".join(spec.expect_store_absent) for p in ("pii", "敏感", "凭据")):
            return L1Outcome(
                ErrorType.FALSE_PERSISTENCE,
                evidence_refs=[e.source or e.id],
                reason=f"记忆中检出敏感信息 {pii}",
            )

    if not spec.expect_store_retain:
        return L1Outcome(None, reason="无 retain 断言", store_ok=True)

    # 2) 干扰项命中（混淆）——只在必留内容缺失或归属不明时报告混淆
    missing = [
        pat for pat in spec.expect_store_retain
        if not any(contains_pattern(e.text, pat) for e in entries)
    ]
    if missing:
        for pat in spec.distractor_patterns:
            for e in entries:
                if contains_pattern(e.text, pat):
                    return L1Outcome(
                        ErrorType.CONFUSION,
                        evidence_refs=[e.source or e.id],
                        reason=f"必留内容缺失，且记忆命中干扰模式 [{pat}]，判定为相近信息混淆",
                    )
        return L1Outcome(
            ErrorType.OMISSION,
            evidence_refs=refs[:5],
            reason=f"应保留的记忆缺失: {missing}",
        )

    # 3) 全部必留内容存在
    return L1Outcome(None, reason="store-level 必留内容齐全", store_ok=True)


def check_answer_against(spec: ProbeSpec, answer: str) -> tuple[ErrorType | None, str]:
    """行为级答案比对：命中过期答案→错误复用；命中干扰项→混淆；空/拒答→遗漏或正确拒答。"""
    if spec.expect_abstention:
        # 应拒答：含糊/无信息回答为正确；含具体细节（数字/路径/端口等）为编造；
        # 两者都不是的边界情况不在 L1 硬判，升级 judge 做语义判定
        vague = any(k in answer for k in ("不知道", "未提及", "没有提到", "没有记录", "没有相关",
                                          "无法确定", "无法回答", "不记得", "don't know",
                                          "not mentioned", "no record"))
        if vague or not answer.strip():
            return ErrorType.CORRECT, "正确拒答：未编造历史中不存在的信息"
        if re.search(r"\d{2,}|[/\\][\w.-]+", answer):
            return ErrorType.CONFUSION, "应拒答却给出了含具体细节的回答，疑似编造或误用记忆"
        return None, "拒答边界模糊，升级 judge 判定"

    if not answer.strip():
        return ErrorType.OMISSION, "未作答"

    for old in spec.outdated_answers:
        if contains_pattern(answer, old):
            return ErrorType.FALSE_REUSE, f"回答命中过期答案 [{old}]，判定为错误复用旧记忆"
    for pat in spec.distractor_patterns:
        if contains_pattern(answer, pat):
            return ErrorType.CONFUSION, f"回答命中干扰项 [{pat}]，判定为混淆"
    if spec.ground_truth and contains_pattern(answer, spec.ground_truth):
        return ErrorType.CORRECT, "回答命中标准答案"
    # 应作答却明确拒答（走到这里说明 store-level 必留检查已通过，库里有却答不出）
    denial = any(k in answer for k in ("不知道", "未提及", "没有提到", "没有记录", "没有相关",
                                       "没找到", "没有找到", "无法确定", "无法回答", "不记得",
                                       "don't know", "no record", "didn't find", "not mentioned"))
    if denial:
        return ErrorType.OMISSION, "应回答却声称无相关记录，判定为提取遗漏（记忆库中已存有相关内容）"
    return None, "L1 无法判定答案语义，需升级 L2/judge"


def check_artifacts(spec: ProbeSpec, artifacts: dict[str, str]) -> L1Outcome:
    """task 产物断言（OSWorld 式 execution oracle）：文件存在 + 内容包含 + 可选哈希。"""
    for a in spec.artifact_assertions:
        path = a["path"]
        if path not in artifacts:
            return L1Outcome(ErrorType.OMISSION, evidence_refs=[f"artifact:{path}"],
                             reason=f"产物缺失: {path}")
        content = artifacts[path]
        if "contains" in a and not contains_pattern(content, a["contains"]):
            return L1Outcome(ErrorType.FALSE_REUSE, evidence_refs=[f"artifact:{path}"],
                             reason=f"产物 {path} 未包含期望内容 [{a['contains']}]")
        if "sha256" in a:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest != a["sha256"]:
                return L1Outcome(ErrorType.CONFUSION, evidence_refs=[f"artifact:{path}"],
                                 reason=f"产物 {path} 哈希不符")
    if spec.artifact_assertions:
        return L1Outcome(None, reason="产物断言全部通过", store_ok=True)
    return L1Outcome(None, reason="无产物断言")
