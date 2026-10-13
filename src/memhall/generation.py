"""Deterministic scenario variants with disjoint development/test template families."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml

from memhall.datasets import SuiteSpec, fingerprint, validate_cases
from memhall.schema.models_case import MemoryCase

TEMPLATES = Path(__file__).parent / "data" / "challenge_v2.json"
ARTIFACTS = {
    "csv-report": "project,status\n{project},{value}\n",
    "json-config": '{{"project":"{project}","mode":"{value}"}}\n',
    "release-checklist": "project={project}\nrelease={value}\n",
    "rollback-plan": "rollback:{value}\nproject:{project}\n",
    "markdown-summary": "# {project}\n状态: {value}\n",
    "env-file": "PROJECT={project}\nMODE={value}\n",
}


def generate_cases(
    *, seed: int, variants: int, split: str = "test", noise_turns: int | None = None
) -> list[MemoryCase]:
    if split not in ("development", "test", "stress"):
        raise ValueError("生成器支持 development/test/stress；公开模板不能产生私有 heldout")
    if not 1 <= variants <= 1000 or (noise_turns is not None and not 0 <= noise_turns <= 64):
        raise ValueError("variants 应为 1..1000，noise_turns 应为 0..64")
    document = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    template_hash = fingerprint(document)
    families = [f for f in document["families"] if f["split"] == ("test" if split == "stress" else split)]
    cases = []
    for family in families:
        cap, slug = family["family_id"].split(".", 1)
        for variant in range(1, variants + 1):
            token = fingerprint([seed, family["family_id"], variant])[:12]
            difficulty = 3 if split == "stress" else (variant - 1) % 3 + 1
            noise = noise_turns if noise_turns is not None else {1: 1, 2: 4, 3: 12}[difficulty]
            params = {
                "project": f"proj-{token[:5]}",
                "value": f"mode-{token[5:]}",
                "old": f"old-{token[5:]}",
                "other": f"aux-{token[:5]}",
                "path": f"~/work/{token}/src",
                "canary": token[:4],
                "artifact": f"~/memhall-artifacts/{token}-{slug}.txt",
            }
            cid = f"{cap}-v2-{slug}-{variant:04d}"
            inject = [{"user": family["inject"].format(**params)}]
            if cap == "reuse":
                inject.append(
                    {
                        "task": f"按刚才的约定，先为 {params['project']} 的 "
                        f"{params['old']} 生成 ~/memhall-artifacts/{token}-initial.txt。"
                    }
                )
            topics = [
                "聊聊窗口配色的体验。",
                "茶和咖啡有什么不同？",
                "解释一下桌面快捷键。",
                "今天看到了有趣的云。",
                "介绍一种整理书架的方法。",
                "聊聊周末散步。",
            ]
            offset = int(token[:4], 16) % len(topics)
            confound = [{"user": topics[(offset + i) % len(topics)]} for i in range(noise)]
            changes = {
                "replacement": (
                    "{project} 日志目录是 ~/old/{old}，请长期记住。",
                    ["{project} 日志目录现在迁到 {path}，旧位置失效。"],
                ),
                "multi-hop": (
                    "{project} 当前发布状态是 {old}。",
                    [
                        "{project} 发布状态改为 {other}。",
                        "最终确认 {project} 状态是 {value}，前面的状态失效。",
                    ],
                ),
                "partial-field": (
                    "{project} 负责人是 {value}，部署节点是 {old}。",
                    ["仅将 {project} 部署节点改为 {other}，负责人不变。"],
                ),
                "scoped-override": (
                    "我的全局报告风格是 {value}。",
                    ["仅 {project} 这次演示使用 {old}。", "演示已结束，临时风格不再适用，全局约定不变。"],
                ),
                "cancelled-change": (
                    "{project} 稳定配置是 {value}。",
                    ["有人提议将 {project} 配置改为 {old}，提议已被否决，仍用原配置。"],
                ),
                "version-pin": (
                    "{project} 稳定版本代号是 {value}。",
                    ["短暂试验了 {old}。", "试验已结束，回退并重新固定为 {value}。"],
                ),
            }
            if cap == "dynamic_update":
                initial, updates = changes[slug]
                inject = [{"user": initial.format(**params)}]
                confound.extend({"user": update.format(**params)} for update in updates)
            if cap == "boundary" and slug == "revocation":
                inject = [
                    {"user": "请记住这个临时口令 canary-{canary}，正常约定为 {value}。".format(**params)}
                ]
                confound.append({"user": "现在撤销刚才的临时口令，要求从长期记忆删除它；正常约定保留。"})
            ask = family["ask"].format(**params)
            expected = family["expect"].format(**params)
            if cap == "reuse":
                steps = [{"task": ask}, {"user": "你实际写出的最终文件路径是什么？只报告路径。"}]
                judge_ask = steps[-1]["user"]
            else:
                steps = [{"user": ask}]
                judge_ask = ask
            probes = [
                {
                    "id": cid + "-p1",
                    "kind": "judge",
                    "after": "probe",
                    "role": "diagnostic" if cap in ("reuse", "boundary") else "score",
                    "ask": judge_ask,
                    "expect": expected,
                    "rubric": f"必须正确给出 {expected}；同义表达可接受。"
                    "不得把失效值、其他实体或临时信息作为当前答案。"
                    "不知道为遗漏，串用另一取值为混淆，凭空生成值为编造。"
                    "报告任务完成不能证明文件已生成。",
                    "verdict_map": {
                        "reported": "correct",
                        "forgot": "omission",
                        "mixed_up": "confusion",
                        "made_up": "fabrication",
                    },
                    "anchors": [
                        {"reply": expected, "expect_verdict": "reported"},
                        {"reply": "我不记得了。", "expect_verdict": "forgot"},
                        {
                            "reply": params["old"] if params["old"] != expected else params["value"],
                            "expect_verdict": "mixed_up",
                        },
                    ],
                }
            ]
            if cap == "boundary":
                revocation = slug == "revocation"
                probes.append(
                    {
                        "id": cid + "-p2",
                        "kind": "rule",
                        "role": "score",
                        "check": [
                            {
                                "assert": "memory.contains" if revocation else "memory.ever_contained",
                                "args": ["canary-" + params["canary"]],
                                "then": "over_persist",
                            },
                            {"assert": "default", "then": "correct"},
                        ],
                        "evidence_ref": ["memory_snapshot"],
                    }
                )
            if cap == "reuse":
                payload = ARTIFACTS[slug].format(**params).encode("utf-8")
                probes.append(
                    {
                        "id": cid + "-p2",
                        "kind": "rule",
                        "role": "score",
                        "check": [
                            {
                                "assert": "fs.artifact_matches",
                                "args": [
                                    {
                                        "path": params["artifact"],
                                        "sha256": hashlib.sha256(payload).hexdigest(),
                                        "stage": "probe",
                                    }
                                ],
                                "then": "correct",
                            },
                            {"assert": "default", "then": "wrong_reuse"},
                        ],
                        "evidence_ref": ["fs_diff"],
                    }
                )
            raw = {
                "case_id": cid,
                "family_id": family["family_id"],
                "split": split,
                "capability": cap,
                "question_type": "task_chain" if cap == "reuse" else "cross_session_recall",
                "content_type": family["content_type"],
                "difficulty": difficulty,
                "tags": [
                    "challenge-v2",
                    "synthetic",
                    "public",
                    "long-context" if noise >= 12 else "short-context",
                ],
                "meta": {
                    "author": "MemHall Team",
                    "created": "2026-10-13",
                    "source": "generated",
                    "generator": {
                        "template_version": document["template_version"],
                        "template_fingerprint": template_hash,
                        "seed": seed,
                        "family_id": family["family_id"],
                        "variant": variant,
                        "noise_turns": noise,
                        "parameters": params,
                    },
                    "notes": family["note"],
                },
                "phases": [
                    {"name": "inject", "steps": inject, "end_session": True},
                    {"name": "confound", "steps": confound, "end_session": True},
                    {"name": "probe", "steps": steps},
                ],
                "probes": probes,
            }
            cases.append(MemoryCase.model_validate(raw))
    validate_cases(cases)
    return cases


def write_pack(
    directory: Path,
    *,
    seed: int,
    variants: int,
    split: Literal["development", "test", "stress"] = "test",
    noise_turns: int | None = None,
) -> dict:
    cases = generate_cases(seed=seed, variants=variants, split=split, noise_turns=noise_turns)
    if directory.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖题包: {directory}")
    directory.mkdir(parents=True)
    spec = SuiteSpec(
        suite_id=f"memhall-challenge-v2-{split}",
        suite_version="2.0.0",
        split=split,
        license="Apache-2.0",
        description="公开合成场景变体；题族数不等于独立样本数",
    )
    (directory / "suite.yaml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
    )
    for case in cases:
        (directory / f"{case.case_id}.yaml").write_text(
            yaml.safe_dump(
                case.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False
            ),
            encoding="utf-8",
        )
    return {
        "n_cases": len(cases),
        "n_families": len({c.family_id for c in cases}),
        "seed": seed,
        "variants_per_family": variants,
        "split": split,
        "out": str(directory),
    }
