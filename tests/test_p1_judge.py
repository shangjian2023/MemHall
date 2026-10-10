"""P1 判卷层整改（docs/engineering-tasks.md T07/T09）回归测试。"""

from __future__ import annotations

import time

import httpx
import pytest

import memhall.scoring.judge as jm
from memhall.schema.evidence import Evidence, EvidencePhase, EvidenceType
from memhall.schema.models_case import Anchor, JudgeProbe
from memhall.scoring.engine import _answer_for
from memhall.scoring.judge import ScriptedJudge
from memhall.scoring.rules import EvidenceStore


def _probe(anchors=None) -> JudgeProbe:
    return JudgeProbe(
        kind="judge", id="update-999-p1", after="probe",
        ask="我的代码目录在哪？", expect="~/dev/src",
        rubric="答 ~/dev/src=对；~/work/src=记混",
        verdict_map={"new_path": "correct", "old_path": "confusion",
                     "dont_know": "omission"},
        anchors=anchors or [Anchor(reply="你的代码目录是 ~/work/src。",
                                   expect_verdict="old_path")])


# ---------- T09 httpx 客户端 ----------

class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {"choices": [{"message": {"content": "ok"}}]}
        self.text = str(self._body)

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, behavior):
        self._behavior = behavior
        self.n_posts = 0

    def post(self, *a, **kw):
        self.n_posts += 1
        r = self._behavior(self.n_posts)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        pass


def test_judge_post_returns_content(monkeypatch):
    j = jm.OpenAICompatJudge("t", "https://gw.example/v1", "m", "k")
    monkeypatch.setattr(jm, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: _FakeClient(lambda n: _Resp()))
    assert j._post({"x": 1}) == "ok"


def test_judge_post_gives_up_within_budget(monkeypatch):
    """网关挂死时单题在总预算处放弃（不再 12 分钟级阻塞），报因预算耗尽。"""
    j = jm.OpenAICompatJudge("t", "https://gw.example/v1", "m", "k")
    monkeypatch.setattr(jm, "_MIN_INTERVAL", 0.0)
    monkeypatch.setenv("JUDGE_TOTAL_BUDGET", "0.5")
    fake = _FakeClient(lambda n: httpx.ConnectError("gateway dead"))
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="预算"):
        j._post({"x": 1})
    assert time.monotonic() - t0 < 5
    assert fake.n_posts < 6  # 预算耗尽提前放弃，非固定 6 轮


def test_judge_post_retries_then_succeeds(monkeypatch):
    j = jm.OpenAICompatJudge("t", "https://gw.example/v1", "m", "k")
    monkeypatch.setattr(jm, "_MIN_INTERVAL", 0.0)
    monkeypatch.setenv("JUDGE_TOTAL_BUDGET", "10")
    fake = _FakeClient(lambda n: _Resp(502) if n < 3 else _Resp())
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    assert j._post({}) == "ok"
    assert fake.n_posts == 3


def test_judge_post_rebuilds_client_after_502(monkeypatch):
    """非 200 弃连后必须重建 client——close 不置 None 会让后续判卷全灭于
    "Cannot send a request, as the client has been closed"（2026-10-10
    VM 全量跑实测：一次 429 之后 87 探测点连环 6 连败）。"""
    j = jm.OpenAICompatJudge("t", "https://gw.example/v1", "m", "k")
    monkeypatch.setattr(jm, "_MIN_INTERVAL", 0.0)
    monkeypatch.setenv("JUDGE_TOTAL_BUDGET", "10")

    built: list[_FakeClient] = []

    class _CloseAware(_FakeClient):
        closed = False

        def close(self):
            self.closed = True

    def factory(**kw):
        c = _CloseAware(lambda n: _Resp())
        built.append(c)
        return c

    monkeypatch.setattr(httpx, "Client", factory)

    first = _CloseAware(lambda n: _Resp(502))  # 预填只会 502 的旧连接
    built.append(first)
    j._client = first
    assert j._post({}) == "ok"                 # 弃连→重建→重试一把过

    assert first.closed                         # 旧 client 确被弃
    assert len(built) == 2                      # 重建了一个新 client
    assert j._client is built[1] and built[1].n_posts == 1


# ---------- T08 顺带修复的并列护栏（ScriptedJudge） ----------

def test_scripted_judge_defers_on_juxtaposition():
    """expect 命中但锚例区分值（旧路径）共现 → 新旧并列，转人工不硬判。"""
    sj = ScriptedJudge()
    o = sj.judge(_probe(), "你的目录有两个候选：~/dev/src 和 ~/work/src。")
    assert o.key is None and "并列" in o.reason


def test_scripted_judge_clean_expect_still_scores():
    sj = ScriptedJudge()
    o = sj.judge(_probe(), "你的代码目录是 ~/dev/src。")
    assert o.key == "new_path"


# ---------- T07 _answer_for 精确匹配优先 ----------

def _store_with_dialogue(messages, replies) -> EvidenceStore:
    store = EvidenceStore()
    store.add(Evidence(
        evidence_id="ev-1", run_id="r", case_id="c",
        phase=EvidencePhase.PROBE, type=EvidenceType.DIALOGUE,
        collected_at="2026-10-02T00:00:00+00:00", clock_offset_days=0,
        payload={"messages": messages,
                 "replies": [{"session_id": "s", "text": t,
                              "sent_at": "", "reply_at": "",
                              "latency_ms": 1} for t in replies]},
        sha256=""))
    return store


def test_answer_for_prefers_exact_match():
    """相近话术两条都能被子串命中时，精确那条的回复胜出（旧逻辑取最后命中
    会张冠李戴）。"""
    store = _store_with_dialogue(
        ["我的口令是什么？", "顺便问下我的口令是什么？"],
        ["口令是 canary-9x8q", "这个我不记得了"])
    assert _answer_for(store, "我的口令是什么？") == "口令是 canary-9x8q"


def test_answer_for_takes_last_when_repeated():
    """同一问题跨会话问两次，取最后一次的回答（时序更新语义）。"""
    store = _store_with_dialogue(
        ["我的口令是什么？", "我的口令是什么？"],
        ["口令是 a-111", "口令是 b-222"])
    assert _answer_for(store, "我的口令是什么？") == "口令是 b-222"
