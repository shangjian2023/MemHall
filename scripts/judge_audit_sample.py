"""人工判卷抽检工作表生成器（R03 评委校准的人工半边，零 LLM 调用）。

审计边界（2026-10-13 全面审计 R03）：本批判卷是规则 + 单 LLM 评委 A，
且与被测智能体同模型标识。离线 judge selftest 的 0 错判只证明管线
正确，不证明真实 LLM 评委判得准——评委校准只能靠人工抽检对账。

用法：
  生成盲评工作表 + 答案键：
    uv run python scripts/judge_audit_sample.py --out docs/judge-audit
  人工填 worksheet.csv 的 human_verdict 列（六态枚举值）后对分：
    uv run python scripts/judge_audit_sample.py --score docs/judge-audit

抽样框：各智能体各轮 verdicts.jsonl 中 LLM 评委判过的 judge 探测点
（decided_by 非 rule），按能力分层等比抽样；Q/A 提取复用判卷同款
_answer_for——人工看到的就是评委看到的。工作表不含评委结论（盲评），
答案键单独存放，填完再对。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

import yaml

from memhall.schema.evidence import Evidence
from memhall.schema.models_case import JudgeProbe, MemoryCase
from memhall.scoring.engine import _answer_for
from memhall.scoring.rules import EvidenceStore

SIX_STATES = ("correct", "omission", "confusion", "fabrication",
              "over_persist", "wrong_reuse")
WORKSHEET = "worksheet.csv"
ANSWER_KEY = "answer-key.csv"


def _stats_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "stats_uncertainty", Path(__file__).parent / "stats_uncertainty.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _collect_samples(stats, root: Path, mode: str) -> list[dict]:
    """收集抽样框：agent × capability 的全部 LLM 判定 judge 探测点。"""
    table = stats.CHAINS_AGENTS if mode == "chains" else stats.FULL_AGENTS
    rows: list[dict] = []
    for agent, run_names in table.items():
        for run_name in run_names:
            run_dir = root / run_name
            verdicts_path = run_dir / "verdicts.jsonl"
            if not verdicts_path.exists():
                print(f"跳过（无 verdicts）: {run_name}")
                continue
            cases = {
                case.case_id: case
                for p in (run_dir / "cases").glob("*/case.yaml")
                if (case := MemoryCase.model_validate(
                    yaml.safe_load(p.read_text(encoding="utf-8"))))
            }
            for line in verdicts_path.read_text(encoding="utf-8").splitlines():
                v = json.loads(line)
                if v.get("decided_by") == "rule":
                    continue  # 规则判定不在校准范围（要校准的是 LLM 评委）
                case = cases.get(v["case_id"])
                if case is None:
                    continue
                probe = next((p for p in case.probes
                              if p.id == v["probe_id"] and isinstance(p, JudgeProbe)),
                             None)
                if probe is None:
                    continue  # 规则探测的判定不进工作表
                store = EvidenceStore(_load_evidence(run_dir / "cases" / case.case_id))
                rows.append({
                    "agent": agent,
                    "run_id": run_name,
                    "case_id": case.case_id,
                    "probe_id": probe.id,
                    "capability": case.capability.value,
                    "ask": probe.ask,
                    "expect": probe.expect,
                    "rubric": probe.rubric,
                    "agent_reply": _answer_for(store, probe.ask),
                    "decided_by": v.get("decided_by", ""),
                    "human_verdict": "",
                    "_judge_verdict": v["verdict"],
                    "_explanation": v.get("explanation", ""),
                })
    return rows


def _load_evidence(case_dir: Path) -> list:
    path = case_dir / "evidence.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Evidence.model_validate(json.loads(line)))
    return out


def _stratified_sample(rows: list[dict], per_agent: int, seed: int) -> list[dict]:
    """按 agent × capability 分层等比抽样（每层取 floor/per-agent 比例）。"""
    rng = random.Random(seed)
    by_agent: dict[str, list[dict]] = {}
    for row in rows:
        by_agent.setdefault(row["agent"], []).append(row)
    picked: list[dict] = []
    for _agent, agent_rows in sorted(by_agent.items()):
        layers: dict[str, list[dict]] = {}
        for row in agent_rows:
            layers.setdefault(row["capability"], []).append(row)
        quota = per_agent / len(layers)
        for _cap, cap_rows in sorted(layers.items()):
            k = max(1, round(quota))
            k = min(k, len(cap_rows))
            picked.extend(rng.sample(cap_rows, k))
    return picked


def generate(out_dir: Path, mode: str, per_agent: int, seed: int) -> int:
    stats = _stats_module()
    rows = _collect_samples(stats, Path("runs"), mode)
    if not rows:
        print("抽样框为空：没有找到 LLM 判定的 judge 探测点", file=sys.stderr)
        return 2
    picked = _stratified_sample(rows, per_agent, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = ["sample_id", "agent", "run_id", "case_id", "probe_id",
              "capability", "ask", "expect", "rubric", "agent_reply",
              "decided_by", "human_verdict"]
    with (out_dir / WORKSHEET).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for i, row in enumerate(picked, start=1):
            row["sample_id"] = f"S{i:03d}"
            writer.writerow(row)
    with (out_dir / ANSWER_KEY).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["sample_id", "judge_verdict",
                                               "explanation"])
        writer.writeheader()
        for i, row in enumerate(picked, start=1):
            writer.writerow({"sample_id": f"S{i:03d}",
                             "judge_verdict": row["_judge_verdict"],
                             "explanation": row["_explanation"]})
    caps = sorted({row["capability"] for row in picked})
    print(f"抽样 {len(picked)} 条（{sorted(by_agent_count(picked))} × "
          f"能力层 {caps}）→ {out_dir / WORKSHEET}")
    print(f"答案键（勿与工作表同屏）: {out_dir / ANSWER_KEY}")
    print("人工判定用六态值: " + "/".join(SIX_STATES))
    return 0


def by_agent_count(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row["agent"]] = out.get(row["agent"], 0) + 1
    return out


def score(worksheet_path: Path) -> int:
    key_path = worksheet_path.parent / ANSWER_KEY
    with worksheet_path.open(encoding="utf-8-sig") as f:
        filled = list(csv.DictReader(f))
    with key_path.open(encoding="utf-8-sig") as f:
        key = {row["sample_id"]: row for row in csv.DictReader(f)}
    judged = [row for row in filled if row.get("human_verdict", "").strip()]
    if not judged:
        print("工作表还没有人工判定（human_verdict 全空）", file=sys.stderr)
        return 2
    bad = [row["sample_id"] for row in judged
           if row["human_verdict"].strip() not in SIX_STATES]
    if bad:
        print(f"非法判定值（只能填六态）: {bad}", file=sys.stderr)
        return 2
    exact = binary = 0
    misses: list[str] = []
    per_cap: dict[str, list[bool]] = {}
    for row in judged:
        judge_v = key[row["sample_id"]]["judge_verdict"]
        human_v = row["human_verdict"].strip()
        exact += human_v == judge_v
        binary += (human_v == "correct") == (judge_v == "correct")
        per_cap.setdefault(row["capability"], []).append(human_v == judge_v)
        if human_v != judge_v:
            misses.append(f"  {row['sample_id']} {row['probe_id']}: "
                          f"人工={human_v} 评委={judge_v}")
    n = len(judged)
    print(f"人工抽检对分：{n} 条")
    print(f"  六态一致率 {exact}/{n} = {exact / n:.0%}")
    print(f"  对错二值一致率 {binary}/{n} = {binary / n:.0%}")
    for cap, hits in sorted(per_cap.items()):
        print(f"  {cap}: {sum(hits)}/{len(hits)}")
    if misses:
        print("分歧明细：")
        print("\n".join(misses[:20]))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("。")[0])
    parser.add_argument("--out", default="docs/judge-audit",
                        help="工作表输出目录（默认 docs/judge-audit）")
    parser.add_argument("--mode", choices=("full", "chains"), default="full")
    parser.add_argument("--per-agent", type=int, default=12,
                        help="每家智能体抽检条数（按能力分层，默认 12）")
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--score", metavar="WORKSHEET",
                        help="填完的 worksheet.csv 路径——对分模式")
    args = parser.parse_args()
    if args.score:
        return score(Path(args.score))
    return generate(Path(args.out), args.mode, args.per_agent, args.seed)


if __name__ == "__main__":
    sys.exit(main())
