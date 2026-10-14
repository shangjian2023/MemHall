"""判卷质检：诱饵测试（design §6.1 三件套之二）。

把机械生成的故意错误答案喂给判卷器，统计拒绝率（目标 ≥95%）。
默认离线 ScriptedJudge（零成本）；--judge llm 走 LLM 单判（消耗网关额度，慎用）。

用法：
    uv run python scripts/judge_decoy.py                 # 离线脚本判卷质检
    uv run python scripts/judge_decoy.py --judge llm    # LLM 判卷质检（JUDGE_A_* 环境变量）
"""

from __future__ import annotations

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memhall.cli import load_cases  # noqa: E402
from memhall.schema.models_case import JudgeProbe  # noqa: E402
from memhall.scoring.decoy import run_decoy_test  # noqa: E402


class _LLMJudgeAdapter:
    """LLM 判卷适配：离线诱饵接口 → llm_judge 单判。"""

    name = "llm"

    def judge(self, probe, answer):
        from memhall.scoring.judge import OpenAICompatJudge, llm_judge
        judge = OpenAICompatJudge.judge_from_env()
        if judge is None:
            raise SystemExit("缺 JUDGE_A_* 环境变量（--judge llm 需要）")
        return llm_judge(probe, answer, judge)


def main() -> int:
    case_dir = sys.argv[sys.argv.index("-c") + 1] if "-c" in sys.argv else "cases/full"
    judge = (_LLMJudgeAdapter()
             if "--judge" in sys.argv and ("llm" in sys.argv or "dual" in sys.argv)
             else None)
    cases = load_cases(__import__("pathlib").Path(case_dir))
    probes = [p for c in cases for p in c.probes if isinstance(p, JudgeProbe)]
    rep = run_decoy_test(probes, judge)  # type: ignore[arg-type]
    print(f"诱饵件：{rep['n_decoys']} 个（来自 {case_dir}，判卷器 "
          f"{'LLM 单判' if judge else 'scripted 离线'}）")
    for r in rep["results"]:
        if r.accepted:
            print(f"  ✗ 未拒绝 {r.probe_id} [{r.kind}] 答='{r.answer[:40]}' "
                  f"判={r.verdict} ({r.note})")
    rate = rep["rejection_rate"]
    print(f"拒绝率: {rate:.1%}（目标 ≥95%）{'✓ 达标' if rep['pass'] else '✗ 不达标'}")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
