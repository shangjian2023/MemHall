"""判卷器：脚本判卷（离线确定性）+ 双 LLM judge（在线，跨家族交叉仲裁）。

双 judge 框架移植自 okim-bench/scoring/judges.py（C 角色 W1 交付），
适配主线契约：judge 输出 verdict_map 的 key（如 new_path/old_path），
引擎再把 key 映射回主线五态判定值。
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import string
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from memhall.schema.models_case import JudgeProbe

log = logging.getLogger(__name__)

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
    """判卷结果：key 为 verdict_map 的 key；None 表示无法判定。

    decided_by：dual 判卷时由 dual_judge 显式标注（judge_a/judge_b/
    arbitration）；None = 沿用引擎旧推导（单判/脚本）。
    """

    key: str | None
    confidence: float
    evidence_refs: list[str]
    reason: str
    judge_a: str | None = None
    judge_b: str | None = None
    judge_a_raw: str | None = None
    judge_b_raw: str | None = None
    arbitrated: bool = False
    decided_by: str | None = None


# ---------- 归一化 ----------

_STRIP = set(string.punctuation + "。，、；：？！“”‘’（） \t\n\r")


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch not in _STRIP)


_ABSTAIN = re.compile(r"不记得|不知道|没提过|没有记录|没听说过|无法确认|不清楚")


def _vals(s: str) -> set[str]:
    """文本里的 ASCII 区分值（路径、版本号、编号，≥3 字符）——值级比对用。"""
    return set(re.findall(r"[A-Za-z0-9_~/.\-]{3,}", re.sub(r"\s+", "", s)))


def _has_superstring_conflict(ans_vals: set[str], exp_vals: set[str]) -> bool:
    """R26：回答值集中存在与某期望值互含/前缀、但自身不是任何期望值的异值。
    discriminate-001 实测形态：问 ~/proj/api 答 ~/proj/api-v3 / ~/proj/api/backup
    ——规范化（去标点）后期望值是其子串，旧 expect 命中判 correct 属假阳性。"""
    for v in ans_vals:
        if v in exp_vals:
            continue
        for e in exp_vals:
            if len(e) >= 3 and (v.startswith(e) or e.startswith(v)
                                or e in v or v in e):
                return True
    return False

# 拒答类键名约定：出题人在 verdict_map 里显式声明拒答如何判
_REFUSAL_KEYS = {"abstain", "refused", "forgot"}


class ScriptedJudge:
    """离线脚本判卷：expect 匹配 + 锚例匹配 + 拒答关键词，全程确定性。

    无 API key / CI / 冒烟的默认判卷器；判不了显式返回 None（不瞎猜）。
    """

    name = "scripted"

    def judge(self, probe: JudgeProbe, answer: str) -> JudgeOutcome:
        ans_n, exp_n = _norm(answer), _norm(probe.expect)
        correct_keys = [k for k, v in probe.verdict_map.items() if v == "correct"]
        refs = ["transcript:answer"]

        ans_raw = re.sub(r"\s+", "", answer)
        ans_vals = _vals(answer)
        ans_bg = _bigrams(ans_raw)
        exp_vals = _vals(probe.expect)
        # 锚例值并集：P1-4 拒答值缺失门用——锚例的区分值出现在回答里，
        # 说明回答在谈题目内容（哪怕改述），不是纯拒答
        anchor_vals = {v for a in probe.anchors for v in _vals(a.reply)}

        def _hit_anchor(expect_verdict: str, reason: str) -> JudgeOutcome:
            """锚例命中收口：非 correct 类锚例（旧值/孪生值/编造值）命中且期望值
            也在回答中共现 = "新旧并列"——rubric 多判混淆，脚本层无法决断转
            LLM/人工（人工审计实录的错判形态：单侧子串命中即给分）。
            correct 类锚例命中 + 期望值共现是"答对"，不拦。"""
            if expect_verdict not in correct_keys and exp_vals and exp_vals <= ans_vals:
                return JudgeOutcome(
                    None, 0.0, refs,
                    "回答并列期望值与候选值，脚本无法决断，转 LLM/人工")
            return JudgeOutcome(expect_verdict, 0.9, refs, reason)

        # 锚例匹配三层：①尾段命中——锚例值通常在"是/用/在/要/→"之后的尾段，
        # 尾段的值与区分词整体出现才算（半对/改尾诱饵不放过）。在原文上取尾段
        # （_norm 会把路径分隔符压掉，".md/.zd"这类区别就没了）②全串关键值全中
        # ③重叠率 ≥0.8 兜底
        for anchor in probe.anchors:
            a_n = _norm(anchor.reply)
            if not a_n:
                continue
            a_raw = re.sub(r"\s+", "", anchor.reply)
            vals = _vals(anchor.reply)
            m = list(re.finditer(r"[是在用要放→]", a_raw))
            tail = a_raw[m[-1].end():] if m else ""
            if tail:
                t_bg = _bigrams(tail) - _bigrams(re.sub(r"\s+", "", probe.ask))
                t_vals = set(re.findall(r"[A-Za-z0-9_~/.\-]{3,}", tail))
                if ((t_bg or t_vals)
                        and (not t_bg or t_bg <= ans_bg)
                        and (not t_vals or t_vals <= ans_vals)):
                    return _hit_anchor(anchor.expect_verdict,
                                       f"锚例尾段命中（{tail[:20]}）")
            if vals and vals <= ans_vals:
                return _hit_anchor(anchor.expect_verdict,
                                   f"锚例值命中（{', '.join(sorted(vals))}）")
            toks = _tokens(a_n)
            overlap = sum(1 for tok in toks if tok in _tokens(ans_n))
            if toks and overlap / len(toks) >= 0.8:
                # 值一致性：锚例带 ASCII 区分值（路径/版本号）而该值不在回答里
                # ——同模板异值（"目录是 X" vs "目录是 Y"重叠 0.89），不按锚例判
                if vals and vals - ans_vals:
                    continue
                return _hit_anchor(anchor.expect_verdict,
                                   f"锚例命中（重叠率 {overlap}/{len(toks)}）")
        if exp_n and exp_n in ans_n:
            # R26 值级复查：expect 子串命中可能是"规范化超串"假阳性——回答里
            # 另有与期望值互为前缀/包含的异值（~/proj/api vs ~/proj/api-v3、
            # ~/proj/api/backup）时脚本不定案，转 LLM/人工
            if _has_superstring_conflict(ans_vals, exp_vals):
                return JudgeOutcome(None, 0.0, refs,
                                    "回答含期望值的近形异值（超串/前缀关系），"
                                    "子串命中可能是假阳性，转 LLM/人工")
            return JudgeOutcome(correct_keys[0] if correct_keys else "correct",
                                0.95, refs, f"回答包含期望答案 {probe.expect}")
        if _ABSTAIN.search(answer) and not (exp_vals | anchor_vals) & ans_vals:
            # P1-4（C 角色队友复核 10-05）：拒答正则不再单独定案。改述正确 +
            # 顺带一句"旧记录没找到"的混合话术（KylinBot 首跑实测分布）会被
            # 全文匹配误判 omission——回答里还有 expect/锚例的值 token 时，
            # 拒答只是半句话不是对问题的回答，脚本不定案转 LLM/人工；
            # 值 token 全缺（或本题无值）才按拒答判
            abstain_keys = [k for k, v in probe.verdict_map.items()
                            if v == "omission"]
            if not abstain_keys and not probe.expect:
                # 拒答题（expect 为空）：拒答本身就是正确行为
                abstain_keys = [k for k, v in probe.verdict_map.items()
                                if v == "correct"]
            if not abstain_keys:
                # verdict_map 显式声明了拒答类键：按作者口径判（expect 填了注释文案的新题走这）
                abstain_keys = [k for k in probe.verdict_map if k in _REFUSAL_KEYS]
            if abstain_keys:
                return JudgeOutcome(abstain_keys[0], 0.8, refs, "回答为拒答话术")
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


def _bigrams(s: str) -> set[str]:
    """CJK 相邻二元组——锚例区分词定位用。"""
    cs = re.findall(r"[一-鿿]", s)
    return {a + b for a, b in zip(cs, cs[1:], strict=False)}


# ---------- 双 LLM judge（移植自 okim-bench，适配 verdict_map）----------

# 判卷提示词版本——进 manifest，排除"判卷口径漂了"的质疑（design.md §10）
# 2026-10-04：锚例进提示词（契约 02 few-shot 防漂移首次落地）+ 仲裁轮值
JUDGE_PROMPT_VERSION = "2026-10-04"

JUDGE_PROMPT = """你是记忆评测评委。根据探测项契约与被测智能体的回答，从给定类别中选出判定。

规则：
1. 只能依据回答原文，禁止臆测；评分必须引用具体证据（evidence_refs），无证据不得分。
2. 回答与期望答案语义等价即选 correct 对应类别；命中 rubric 描述的其他情形选对应类别。
3. "语义等价"指关键识别信息一致（实体、路径、取值），不要求逐字重复。
4. 锚定例只作判定口径参照，不是标准答案本身；回答不必与锚例措辞一致。
5. 输出严格 JSON：{{"verdict": "<类别key>", "confidence": 0-1, "evidence_refs": ["..."], "reason": "..."}}

类别（verdict_map，key=judge输出，value=五态判定）：
{verdict_map}

探测项契约：
问题：{ask}
期望答案：{expect}
判卷标准：
{rubric}

锚定例（判定口径参照）：
{anchors}

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

锚定例（判定口径参照）：
{anchors}

被测智能体的回答：
{answer}

评委A结论：{verdict_a}
评委B结论：{verdict_b}

规则同上：只依据回答原文、必须给出 evidence_refs、verdict 必须是类别 key 之一。
输出严格 JSON：{{"verdict": "...", "confidence": 0-1, "evidence_refs": ["..."], "reason": "..."}}"""


def _anchors_block(probe: JudgeProbe) -> str:
    """锚例渲染成 few-shot 参照块（契约 02 §6；无锚例时明示）。"""
    if not probe.anchors:
        return "（本题无锚例）"
    return "\n".join(f"- 回复「{a.reply}」→ {a.expect_verdict}"
                     for a in probe.anchors)


class OpenAICompatJudge:
    """OpenAI 兼容端点评委，key 从环境变量读取（JUDGE_A_/JUDGE_B_ 前缀）。"""

    def __init__(self, name: str, base_url: str, model: str, api_key: str,
                 temperature: float | None = None):
        self.name, self.base_url, self.model, self.api_key = name, base_url, model, api_key
        self.temperature = temperature
        self._client: httpx.Client | None = None  # 首个请求时建

    @classmethod
    def from_env(cls, which: str) -> OpenAICompatJudge:
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
    def pair_from_env(cls) -> tuple[OpenAICompatJudge, ...] | None:
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
        """httpx 连接复用 + 预算内退避重试。

        旧手搓 http.client 版最坏情况单题阻塞约 12 分钟（8 次退避、单次
        上限 90s），网关坏窗口实测分钟级——现改为：连接复用（keep-alive
        稳，避开每请求 TLS 握手的坏 record mac 放大器）+ 单题总预算
        JUDGE_TOTAL_BUDGET（默认 300s）内重试，预算耗尽即弃、走降级路径。
        """
        _rate_limit()
        budget = float(os.environ.get("JUDGE_TOTAL_BUDGET", "300"))
        t0 = time.monotonic()
        last_err: Exception | None = None
        for attempt in range(6):
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.base_url.rstrip("/"),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=httpx.Timeout(connect=15, read=120, write=15, pool=15),
                )
            try:
                r = self._client.post("/chat/completions", json=payload)
                if r.status_code != 200:
                    # 429/5xx 退避重试；弃连保下轮干净握手。
                    # close 后必须置 None 且懒建在循环内：httpx client
                    # 关闭不自知，留着会让重试拿尸体（或 None）连环
                    # "Cannot send a request, as the client has been closed"
                    self._client.close()
                    self._client = None
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]!r}")
                return r.json()["choices"][0]["message"]["content"]
            except Exception as e:   # noqa: BLE001 TLS 断流/429/5xx 一律退避重试
                last_err = e
                remaining = budget - (time.monotonic() - t0)
                if remaining <= 0:
                    log.error("judge %s 预算耗尽（%.0fs），放弃本判: %s",
                              self.name, budget, e)
                    break
                log.warning("judge %s 第 %d 次失败（剩预算 %.0fs）: %s",
                            self.name, attempt + 1, remaining, e)
                time.sleep(min(2 ** attempt, 30.0, remaining))
        raise RuntimeError(
            f"judge {self.name} 预算 {budget:g}s 内重试仍失败: {last_err}")

    def complete(self, prompt: str) -> str:
        payload: dict[str, Any] = {"model": self.model,
                                   "messages": [{"role": "user", "content": prompt}]}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return self._post(payload)


@dataclass
class _RawVerdict:
    key: str
    confidence: float
    evidence_refs: list[str]
    reason: str


def _parse(raw: str, valid_keys: set[str]) -> _RawVerdict | None:
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
    双判：独立判 → 一致即出；一票无效 → 直接采信对侧有效票（不再多花一次
    仲裁调用去问一个已知投不出票的评委）；双票不一致 → 仲裁——仲裁评委
    A/B 轮值（JUDGE_ARBITER=a|b|rotate），固定由 A 复议自己的分歧会引入
    自偏好；仲裁无效 → key=None。"""
    valid = set(probe.verdict_map.keys())
    ctx = dict(verdict_map=json.dumps(probe.verdict_map, ensure_ascii=False),
               ask=probe.ask, expect=probe.expect, rubric=probe.rubric,
               anchors=_anchors_block(probe), answer=answer)
    va = _parse(judge_a.complete(JUDGE_PROMPT.format(**ctx)), valid)

    if judge_b is None:
        if va:
            return JudgeOutcome(va.key, va.confidence, va.evidence_refs,
                                f"单判: {va.reason}",
                                judge_a=judge_a.name, judge_a_raw=va.key,
                                decided_by="judge_a")
        return JudgeOutcome(None, 0.0, [], "单判输出无效，转人工复核",
                            judge_a=judge_a.name)

    vb = _parse(judge_b.complete(JUDGE_PROMPT.format(**ctx)), valid)

    if va and not vb:
        return JudgeOutcome(va.key, va.confidence, va.evidence_refs,
                            f"评委B票无效，采信A有效票: {va.reason}",
                            judge_a=judge_a.name, judge_b=judge_b.name,
                            judge_a_raw=va.key, decided_by="judge_a")
    if vb and not va:
        return JudgeOutcome(vb.key, vb.confidence, vb.evidence_refs,
                            f"评委A票无效，采信B有效票: {vb.reason}",
                            judge_a=judge_a.name, judge_b=judge_b.name,
                            judge_b_raw=vb.key, decided_by="judge_b")

    if va and vb and va.key == vb.key:
        return JudgeOutcome(va.key, (va.confidence + vb.confidence) / 2,
                            sorted(set(va.evidence_refs + vb.evidence_refs)),
                            f"双评委一致: {va.reason} | {vb.reason}",
                            judge_a=judge_a.name, judge_b=judge_b.name,
                            judge_a_raw=va.key, judge_b_raw=vb.key,
                            decided_by="judge_a")
    arbiter, arb_name = _pick_arbiter(judge_a, judge_b)
    raw = arbiter.complete(ARBITER_PROMPT.format(
        **ctx,
        verdict_a=va.key if va else "无效（未按格式输出或无证据引用）",
        verdict_b=vb.key if vb else "无效（未按格式输出或无证据引用）"))
    arb = _parse(raw, valid)
    if arb:
        return JudgeOutcome(arb.key, arb.confidence, arb.evidence_refs,
                            f"仲裁结论（{arb_name}）: {arb.reason}",
                            judge_a=judge_a.name, judge_b=judge_b.name,
                            judge_a_raw=va.key if va else None,
                            judge_b_raw=vb.key if vb else None, arbitrated=True,
                            decided_by="arbitration")
    return JudgeOutcome(None, 0.0, [], "仲裁输出无效，转人工复核",
                        judge_a=judge_a.name, judge_b=judge_b.name,
                        judge_a_raw=va.key if va else None,
                        judge_b_raw=vb.key if vb else None, arbitrated=True)


_arb_count = itertools.count()


def _pick_arbiter(judge_a: OpenAICompatJudge,
                  judge_b: OpenAICompatJudge) -> tuple[OpenAICompatJudge, str]:
    """仲裁评委选择：JUDGE_ARBITER 环境变量（a/b/rotate，默认 rotate）。"""
    mode = os.environ.get("JUDGE_ARBITER", "rotate").strip().lower()
    if mode == "a":
        pick = 0
    elif mode == "b":
        pick = 1
    else:
        pick = next(_arb_count) % 2
    return (judge_a, "评委A") if pick == 0 else (judge_b, "评委B")
