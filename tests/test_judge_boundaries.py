from __future__ import annotations

from memhall.schema.models_case import Anchor, JudgeProbe
from memhall.scoring.judge import ScriptedJudge


def _probe() -> JudgeProbe:
    return JudgeProbe(
        kind="judge",
        id="judge-boundary",
        ask="我的代码目录在哪？",
        expect="~/dev/new",
        rubric="新值正确，旧值混淆，不知道是遗漏。",
        verdict_map={
            "right": "correct",
            "stale": "confusion",
            "dont_know": "omission",
        },
        anchors=[
            Anchor(reply="我的代码目录是 ~/dev/new", expect_verdict="right"),
            Anchor(reply="我的代码目录是 ~/work/old", expect_verdict="stale"),
            Anchor(reply="这个我不记得了", expect_verdict="dont_know"),
        ],
    )


def test_scripted_judge_rejects_conflicting_old_value():
    outcome = ScriptedJudge().judge(
        _probe(), "旧目录 ~/work/old，新目录 ~/dev/new，但我无法确定。"
    )
    assert outcome.key == "stale"


def test_scripted_judge_prefers_abstention_and_negation_is_not_correct():
    probe = _probe()
    assert ScriptedJudge().judge(probe, "没有记录 ~/dev/new").key == "dont_know"
    assert ScriptedJudge().judge(probe, "不是 ~/dev/new").key is None


def test_scripted_judge_supports_correct_abstention_label_with_explanatory_expect():
    probe = probe = JudgeProbe(
        kind="judge", id="boundary", ask="临时端口是多少？",
        expect="（临时信息不保留）", rubric="应拒答",
        verdict_map={"abstain": "correct", "gave_port": "over_persist"},
        anchors=[Anchor(reply="临时端口用后就清掉了，不保留。", expect_verdict="abstain")],
    )
    assert ScriptedJudge().judge(probe, "临时端口用后就清掉了，不保留。").key == "abstain"
