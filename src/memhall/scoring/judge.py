"""判卷器：脚本判卷（离线确定性）+ 双 LLM judge（在线，跨家族交叉仲裁）。

双 judge 框架移植自 okim-bench/scoring/judges.py（C 角色 W1 交付），
适配主线契约：judge 输出 verdict_map 的 key（如 new_path/old_path），
引擎再把 key 映射回主线五态判定值。
"""

from __future__ import annotations

import json
import os
import re
import string
import threading
import time
from dataclasses import dataclass
from typing import Optional

from memhall.schema.models_case import JudgeProbe

# 网关 RPM 限额保护：同一把 key 共享的调用节流（超限会被掐 TLS 而非 429）
_MIN_INTERVAL = float(os.environ.get("JUDGE_MIN_INTERVAL", "12"))
_rate_lock = threading.Lock()
_last_call = 0.0


def _rate_limit() -> None:
    global _last_call
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


@dataclass
class JudgeOutcome:
    """判卷结果：key 为 verdict_map 的 key；None 表示无法判定。"""

    key: Optional[str]
    confidence: float
    evidence_refs: list[str]
    reason: str
    judge_a: Optional[str] = None
    judge_b: Optional[str] = None
    judge_a_raw: Optional[str] = None
    judge_b_raw: Optional[str] = None
    arbitrated: bool = False


# ---------- 归一化 ----------

_STRIP = set(string.punctuation + "。，、；：？！“”‘’（） \t\n\r")


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch not in _STRIP)


_ABSTAIN = re.compile(
    r"不记得|不知道|没提过|没说过|没告诉过|没有记录|没听说过|无法确认|不清楚|"
    r"不保留|清除|清掉|删除|没有保存|未保存|不该记|不应该记"
)
_NEGATION_PREFIX = r"不是|并非|不对|没有|没|未|不存在|错误地?"


def _distinctive_tokens(text: str) -> set[str]:
    """提取路径和编号，避免单个常见汉字造成锚例误命中。"""

    return set(re.findall(
        r"~?/[A-Za-z0-9_.\-一-鿿]+(?:/[A-Za-z0-9_.\-一-鿿]+)*|"
        r"[A-Za-z0-9][A-Za-z0-9_.-]{2,}", text,
    ))


def _anchor_score(anchor: "Anchor", answer_norm: str, answer: str) -> float:
    anchor_norm = _norm(anchor.reply)
    if anchor_norm and anchor_norm in answer_norm:
        return 1.0
    expected = _distinctive_tokens(anchor.reply)
    if not expected:
        return 0.0
    present = sum(1 for token in expected if token.lower() in answer.lower())
    return present / len(expected)


class ScriptedJudge:
    """离线脚本判卷：expect 匹配 + 锚例匹配 + 拒答关键词，全程确定性。

    无 API key / CI / 冒烟的默认判卷器；判不了显式返回 None（不瞎猜）。
    """

    name = "scripted"

    def judge(self, probe: JudgeProbe, answer: str) -> JudgeOutcome:
        ans_n, exp_n = _norm(answer), _norm(probe.expect)
        correct_keys = [k for k, v in probe.verdict_map.items() if v == "correct"]
        refs = ["transcript:answer"]

        # 先识别非正确锚例。这样“新值 + 旧值”或泄露口令不会因包含新值
        # 而直接被 expect 子串检查判成正确。
        anchor_hits: list[tuple[float, Anchor]] = []
        for anchor in probe.anchors:
            score = _anchor_score(anchor, ans_n, answer)
            if score >= 0.8:
                anchor_hits.append((score, anchor))
        for score, anchor in sorted(anchor_hits, key=lambda item: item[0], reverse=True):
            mapped = probe.verdict_map.get(anchor.expect_verdict)
            if mapped != "correct":
                return JudgeOutcome(anchor.expect_verdict, 0.9, refs,
                                    f"非正确锚例命中（特征覆盖率 {score:.0%}）")

        # 拒答优先于期望值匹配；“没有记录 ~/new”不能算召回成功。
        if _ABSTAIN.search(answer):
            abstain_keys = [k for k, v in probe.verdict_map.items()
                            if v == "omission"]
            if not abstain_keys:
                abstain_keys = [k for k, v in probe.verdict_map.items()
                                if v == "correct" and k.lower() in {
                                    "abstain", "refused", "forgot", "dont_know", "dontknow",
                                }]
            if abstain_keys:
                return JudgeOutcome(abstain_keys[0], 0.8, refs, "回答为拒答话术")

        # 明确否定期望值时不做自动正确判定，交给 LLM/人工依据上下文判断。
        if probe.expect and re.search(
            rf"(?:{_NEGATION_PREFIX})[^\n。！？]{{0,20}}{re.escape(probe.expect)}",
            answer,
            re.IGNORECASE,
        ):
            return JudgeOutcome(None, 0.0, refs, "期望值出现在否定语境，脚本判卷不确定")

        if exp_n and exp_n in ans_n:
            return JudgeOutcome(correct_keys[0] if correct_keys else "correct",
                                0.95, refs, f"回答包含期望答案 {probe.expect}")
        return JudgeOutcome(None, 0.0, refs, "脚本判卷无法判定，需 LLM judge 或人工复核")


def _tokens(s: str) -> set[str]:
    """锚例重叠度用的粗粒度 token 集：连续字母数字串 + 单个 CJK 字符。"""
    out: set[str] = set()
    buf = ""
    for ch in s:
        if ch.isascii() and ch.isalnum():
            buf += ch
        else:
            if buf:
                out.add(buf)
                buf = ""
            if "一" <= ch <= "鿿":
                out.add(ch)
    if buf:
        out.add(buf)
    return out


# ---------- 双 LLM judge（移植自 okim-bench，适配 verdict_map）----------

JUDGE_PROMPT = """你是记忆评测评委。根据探测项契约与被测智能体的回答，从给定类别中选出判定。

规则：
1. 只能依据回答原文，禁止臆测；评分必须引用具体证据（evidence_refs），无证据不得分。
2. 回答与期望答案语义等价即选 correct 对应类别；命中 rubric 描述的其他情形选对应类别。
3. "语义等价"指关键识别信息一致（实体、路径、取值），不要求逐字重复。
4. 输出严格 JSON：{{"verdict": "<类别key>", "confidence": 0-1, "evidence_refs": ["..."], "reason": "..."}}

类别（verdict_map，key=judge输出，value=五态判定）：
{verdict_map}

探测项契约：
问题：{ask}
期望答案：{expect}
判卷标准：
{rubric}

被测智能体的回答：
{answer}

请输出 JSON。"""

ARBITER_PROMPT = """你是仲裁评委。两位评委对同一回答给出不同结论，请复议并给出最终结论。

类别（verdict_map）：
{verdict_map}

问题：{ask}
期望答案：{expect}
判卷标准：
{rubric}

被测智能体的回答：
{answer}

评委A结论：{verdict_a}
评委B结论：{verdict_b}

规则同上：只依据回答原文、必须给出 evidence_refs、verdict 必须是类别 key 之一。
输出严格 JSON：{{"verdict": "...", "confidence": 0-1, "evidence_refs": ["..."], "reason": "..."}}"""


class OpenAICompatJudge:
    """OpenAI 兼容端点评委，key 从环境变量读取（JUDGE_A_/JUDGE_B_ 前缀）。"""

    def __init__(self, name: str, base_url: str, model: str, api_key: str,
                 temperature: float | None = None):
        self.name, self.base_url, self.model, self.api_key = name, base_url, model, api_key
        self.temperature = temperature

    @classmethod
    def from_env(cls, which: str) -> "OpenAICompatJudge":
        prefix = f"JUDGE_{which}"
        return cls(
            name=os.environ.get(f"{prefix}_MODEL", "unknown"),
            base_url=os.environ[f"{prefix}_BASE_URL"],
            model=os.environ.get(f"{prefix}_MODEL", "unknown"),
            api_key=os.environ[f"{prefix}_KEY"],
            temperature=float(os.environ[f"{prefix}_TEMP"])
            if os.environ.get(f"{prefix}_TEMP") else None,
        )

    @classmethod
    def pair_from_env(cls) -> Optional[tuple["OpenAICompatJudge", ...]]:
        """按环境变量组装判卷组：JUDGE_A 必需，JUDGE_B 可选（留空=单判）。

        单判省一半 RPM（网关限额实测个位数/分钟）；双判跨家族仲裁是
        design.md 的强化项，限额放开后配 JUDGE_B 即恢复。
        """
        try:
            a = cls.from_env("A")
        except KeyError:
            return None
        try:
            b = cls.from_env("B")
        except KeyError:
            return (a,)
        return (a, b)

    def _post(self, payload: dict) -> str:
        """带持久连接与长退避的 POST：网关坏窗口实测分钟级，短退避无效。

        连接复用是关键（hermes 的 httpx keep-alive 稳、urllib 每请求新建
        TLS 握手恰是坏 record mac 放大器），失败即弃连重连。
        """
        import http.client
        import threading
        import time as _t
        import urllib.parse
        _rate_limit()
        u = urllib.parse.urlparse(self.base_url)
        host, port = u.hostname, u.port or 443
        path = (u.path.rstrip("/") or "") + "/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        last_err: Exception | None = None
        for attempt in range(8):
            try:
                conn = http.client.HTTPSConnection(host, port, timeout=120)
                conn.request("POST", path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
                if resp.will_close:
                    conn.close()
                if resp.status != 200:
                    conn.close()
                    raise RuntimeError(f"HTTP {resp.status}: {data[:200]!r}")
                return json.loads(data.decode("utf-8"))["choices"][0]["message"]["content"]
            except Exception as e:   # noqa: BLE001 TLS 断流/429/5xx 一律退避重试
                last_err = e
                _t.sleep(min(2 ** attempt, 90))
        raise RuntimeError(f"judge {self.name} 重试 8 次仍失败: {last_err}")

    def complete(self, prompt: str) -> str:
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return self._post(payload)


@dataclass
class _RawVerdict:
    key: str
    confidence: float
    evidence_refs: list[str]
    reason: str


def _parse(raw: str, valid_keys: set[str]) -> Optional[_RawVerdict]:
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        key = str(data["verdict"]).strip()
        if key not in valid_keys:
            return None
        refs = [str(r) for r in data.get("evidence_refs", [])]
        if not refs:   # 无证据不得分
            return None
        return _RawVerdict(key, float(data.get("confidence", 0.5)), refs,
                           str(data.get("reason", "")))
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def dual_judge(probe: JudgeProbe, answer: str,
               judge_a: OpenAICompatJudge,
               judge_b: OpenAICompatJudge | None = None) -> JudgeOutcome:
    """单判（judge_b 缺省）：一票定案，无效票转人工。
    双判：独立判 → 一致即出；不一致（或票无效）→ 仲裁；仲裁无效 → key=None。"""
    valid = set(probe.verdict_map.keys())
    ctx = dict(verdict_map=json.dumps(probe.verdict_map, ensure_ascii=False),
               ask=probe.ask, expect=probe.expect, rubric=probe.rubric, answer=answer)
    va = _parse(judge_a.complete(JUDGE_PROMPT.format(**ctx)), valid)

    if judge_b is None:
        if va:
            return JudgeOutcome(va.key, va.confidence, va.evidence_refs,
                                f"单判: {va.reason}",
                                judge_a=judge_a.name, judge_a_raw=va.key)
        return JudgeOutcome(None, 0.0, [], "单判输出无效，转人工复核",
                            judge_a=judge_a.name)

    vb = _parse(judge_b.complete(JUDGE_PROMPT.format(**ctx)), valid)

    if va and vb and va.key == vb.key:
        return JudgeOutcome(va.key, (va.confidence + vb.confidence) / 2,
                            sorted(set(va.evidence_refs + vb.evidence_refs)),
                            f"双评委一致: {va.reason} | {vb.reason}",
                            judge_a=judge_a.name, judge_b=judge_b.name,
                            judge_a_raw=va.key, judge_b_raw=vb.key)
    raw = judge_a.complete(ARBITER_PROMPT.format(
        **ctx,
        verdict_a=va.key if va else "无效（未按格式输出或无证据引用）",
        verdict_b=vb.key if vb else "无效（未按格式输出或无证据引用）"))
    arb = _parse(raw, valid)
    if arb:
        return JudgeOutcome(arb.key, arb.confidence, arb.evidence_refs,
                            f"仲裁结论: {arb.reason}",
                            judge_a=judge_a.name, judge_b=judge_b.name,
                            judge_a_raw=va.key if va else None,
                            judge_b_raw=vb.key if vb else None, arbitrated=True)
    return JudgeOutcome(None, 0.0, [], "仲裁输出无效，转人工复核",
                        judge_a=judge_a.name, judge_b=judge_b.name,
                        judge_a_raw=va.key if va else None,
                        judge_b_raw=vb.key if vb else None, arbitrated=True)
