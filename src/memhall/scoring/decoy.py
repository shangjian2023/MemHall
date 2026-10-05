"""判卷质检·诱饵件（design.md §6.1 三件套之二）。

把故意做错的答案喂给判卷器：判成"记对了"=判卷器宽松（假阳性）。
拒绝率 ≥95% 才过关。业界教训：只测金标准一致发现不了宽松——
有知名记忆评测的判卷器对故意错误答案的接受率高达 63%。

诱饵全部机械生成、确定性可复现：
- swap      张冠李戴：拿另一题的期望答案回答本题
- fabricate 瞎编：把期望答案改一个字符（既破坏子串匹配，也测语义判卷）
- hedge     含糊其辞：自信口吻包装错误答案（"我记得应该是 X 吧"）
- superstring 超串近形：给期望值添后缀（~/proj/api → ~/proj/api-v3）——
  规范化后期望值仍是子串，专打 expect 子串回退的假阳性面（R26）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from memhall.schema.models_case import JudgeProbe
from memhall.scoring.judge import _REFUSAL_KEYS, ScriptedJudge

PASS_RATE = 0.95  # design.md §6.1：诱饵拒绝率 ≥95%


@dataclass
class DecoyResult:
    probe_id: str
    kind: str
    answer: str
    accepted: bool
    verdict: str
    note: str


def _mutate(expect: str) -> str:
    """把答案做错但保持"看起来可信"：
    有字母数字→改首个字母数字（破坏子串匹配）；纯中文→替换尾部多个汉字
    （只改一个字时锚例重叠判卷 0.8 阈值仍会误收——半对诱饵必须拉开重叠差距）。"""
    m = re.search(r"[A-Za-z0-9]", expect)
    if m:
        i = m.start()
        repl = "z" if expect[i] != "z" else "q"
        return expect[:i] + repl + expect[i + 1:]
    cjk = list(re.finditer(r"[一-鿿]", expect))
    if len(cjk) >= 2:
        k = max(2, len(cjk) * 3 // 10 + 1)   # 替换 ≥2 个且 ≥30% 汉字
        return expect[:cjk[-k].start()] + "另" * k + expect[cjk[-1].end():]
    return expect + "0"


def _superstring(expect: str) -> str:
    """超串近形：首个 ASCII 值 token 添 -v2 后缀。纯中文期望无此形态，返回空。"""
    m = re.search(r"[A-Za-z0-9_~/.\-]{3,}", expect)
    if not m:
        return ""
    tok = m.group(0)
    if tok.endswith(("-v2", "v2")):
        return ""
    return expect.replace(tok, tok + "-v2")


def _is_refusal_probe(probe: JudgeProbe) -> bool:
    """正确答案=拒答的题（"别记"类）：改拒答句还是拒答句，fabricate/hedge 无意义。"""
    correct_keys = [k for k, v in probe.verdict_map.items() if v == "correct"]
    return bool(correct_keys) and all(k in _REFUSAL_KEYS for k in correct_keys)


def build_decoys(probes: list[JudgeProbe]) -> list[tuple[JudgeProbe, str, str]]:
    """[(probe, kind, decoy_answer)]——拒答类题只用 swap（报出具体记忆才是真错）。"""
    out: list[tuple[JudgeProbe, str, str]] = []
    expects = [p.expect for p in probes if p.expect]
    for p in probes:
        if not p.expect or "correct" not in p.verdict_map.values():
            continue
        swap = next((e for e in expects if e != p.expect
                     and not any(k in e for k in ("不记得", "不知道", "没记", "忘了"))),
                    "")
        if swap:
            out.append((p, "swap(张冠李戴)", swap))
        if _is_refusal_probe(p):
            continue  # expect 本身是拒答话术：改一个字符仍是拒答，构不成诱饵
        fab = _mutate(p.expect)
        if fab != p.expect:
            out.append((p, "fabricate(瞎编)", fab))
            out.append((p, "hedge(含糊其辞)", f"我记得应该是 {fab} 吧"))
        sup = _superstring(p.expect)
        if sup and sup != p.expect:
            out.append((p, "superstring(超串)", sup))
    return out


def run_decoy_test(probes: list[JudgeProbe],
                   judge: ScriptedJudge | None = None) -> dict:
    """跑诱饵件。缺省离线 ScriptedJudge；可传任何有 .judge(probe, answer) 的判卷器。"""
    judge = judge or ScriptedJudge()
    results: list[DecoyResult] = []
    for probe, kind, answer in build_decoys(probes):
        oc = judge.judge(probe, answer)
        accepted = (oc.key is not None
                    and probe.verdict_map.get(oc.key) == "correct")
        results.append(DecoyResult(probe.id, kind, answer, accepted,
                                    oc.key or "undecided", oc.reason[:80]))
    n = len(results)
    acc = sum(1 for r in results if r.accepted)
    return {"n_decoys": n, "n_accepted": acc,
            "rejection_rate": round(1 - acc / n, 4) if n else None,
            "pass": n > 0 and (1 - acc / n) >= PASS_RATE,
            "results": results}
