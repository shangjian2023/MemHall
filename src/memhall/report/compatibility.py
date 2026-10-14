"""Check question content and scoring conditions before combining runs.

口径身份约定（哪些字段进比较/聚合的身份，哪些刻意不进）：

进身份（确实影响实验行为的执行口径）：
- 题目内容与清单：selection_fingerprint / cases（快照 vs manifest 对账）；
- 固定试卷与题包：paper_id（+paper_sha256）与 benchmark.suite；
- 评分协议与判卷器：scoring_protocol（含 judge 模式/模型/提示词版本；
  旧归档缺失时回退 manifest.judge 并降级警告）；
- 沙箱口径：environment.sandbox（归一化为 on/off；bwrap 真值集合与
  adapters.sandbox 对齐）。两边都有值且不同 → 拒绝混合；
- 同智能体多轮聚合另加（跨智能体比较不查这些——不同家版本/适配器
  天然不同，禁止比较反而错）：
  * 被测真实版本：agent_version（runner 实际落账字段）优先，历史
    adapter_version 仅作兜底；字段混用/缺失都只降级不冒充；
  * 模型口径：model_backend 的 mode/model/lanes/override（网关地址等
    部署细节不进身份）；实际请求模型与思考强度：llm_runtime 的
    models/reasoning（网关账单归并，未记账 = 未知）。

不进身份（与实验行为无关，进来了会导致所有轮次都无法相容）：
主机名、路径、启动/完成时间、token 用量、git_hash 等运行痕迹。

历史探索性读取路径：旧归档缺以上字段时，本模块一律显式记 unknown 并
加警告（verified=false）——可以读、可以比，但不能宣称验证一致；有
明确不一致（两边都有值且不同）的混合直接拒绝，没有静默放过的通道。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from memhall.datasets import case_fingerprint, validate_cases
from memhall.sampling import load_paper
from memhall.schema.models_case import MemoryCase

# 与 adapters.sandbox._TRUTHY 对齐（bwrap 车道）；report 侧不复用适配器
# 实现，避免统计代码反向依赖执行环境代码
_SANDBOX_ON = {"1", "true", "on", "bwrap"}
_SANDBOX_OFF = {"", "0", "off", "false"}


def sandbox_state(manifest: dict) -> str | None:
    """manifest.environment.sandbox → 'on'/'off'；未封存或不可识别 → None。"""
    env = manifest.get("environment")
    if not isinstance(env, dict) or "sandbox" not in env:
        return None
    raw = env.get("sandbox")
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in _SANDBOX_ON:
        return "on"
    if value in _SANDBOX_OFF:
        return "off"
    return None  # 无法识别的取值：按未知处理，不替 manifest 猜


def version_signal(manifest: dict) -> tuple[str | None, str | None]:
    """被测智能体真实版本：(版本值, 落账字段名)。

    agent_version 是 runner 实际写的字段（adapter.version_info()）；
    adapter_version 是历史字段名，仅在其缺席时兜底。两者皆缺 → (None, None)，
    调用方必须按未知降级，不能默认版本相同。"""
    for field in ("agent_version", "adapter_version"):
        value = manifest.get(field)
        if value:
            return str(value), field
    return None, None


def backend_signal(manifest: dict) -> dict:
    """模型口径的可比面：mode/model/lanes/override。url 等部署细节不进身份。"""
    mb = manifest.get("model_backend") or {}
    return {
        "mode": mb.get("mode"),
        "model": mb.get("model"),
        "lanes": mb.get("lanes"),
        "override": mb.get("override", mb.get("model_override")),
    }


def runtime_signal(manifest: dict) -> dict:
    """实际请求模型与思考强度（llm_runtime 网关账单归并）。

    只取可比面（排序后的 models/reasoning）；requests 计数逐轮天然不同，
    forced_model 是车道属性保留。未记账 → models 为空表，调用方按未知处理。"""
    rt = manifest.get("llm_runtime") or {}
    return {
        "models": sorted(rt.get("models") or []),
        "reasoning": sorted(rt.get("reasoning") or []),
        "forced_model": rt.get("forced_model"),
    }


def snapshot_cases(run_dir: Path) -> list[MemoryCase] | None:
    full = run_dir / "cases.json"
    if full.is_file():
        raw = json.loads(full.read_text(encoding="utf-8"))
        cases = [MemoryCase.model_validate(c) for c in raw["cases"]]
    else:
        paths = sorted((run_dir / "cases").glob("*/case.yaml"))
        if not paths:
            return None
        cases = [MemoryCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))) for p in paths]
    validate_cases(cases)
    if full.is_file():
        for case in cases:
            path = run_dir / "cases" / case.case_id / "case.yaml"
            if path.is_file():
                archived = MemoryCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
                if case_fingerprint([archived]) != case_fingerprint([case]):
                    raise ValueError(f"逐题快照与 cases.json 不一致: {case.case_id}")
    return cases


def _identity(run_dir: Path, manifest: dict) -> tuple[dict, list[str]]:
    cases = snapshot_cases(run_dir)
    for name, expected in manifest.get("output_hashes", {}).items():
        if name not in ("verdicts.jsonl", "metrics.json", "report.md", "radar.png"):
            raise ValueError(f"不支持的产物封印名: {name}")
        path = run_dir / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"评分产物哈希不匹配: {run_dir.name}/{name}")
    benchmark = manifest.get("benchmark", {})
    warnings: list[str] = []
    selection = case_fingerprint(cases) if cases is not None else None
    recorded = benchmark.get("selection_fingerprint")
    if recorded and recorded != selection:
        raise ValueError(f"题目快照与 manifest 不一致: {run_dir.name}")
    if cases is None:
        warnings.append("旧归档缺少题目快照，只能核对题目清单")
    elif not recorded:
        warnings.append("旧归档按题目快照核对，未提供题包封印")
    paper_file = run_dir / "paper.json"
    paper_id = benchmark.get("paper_id")
    if paper_id:
        if not paper_file.is_file():
            raise ValueError(f"封存试卷缺失: {run_dir.name}")
        paper = load_paper(paper_file)
        if paper.paper_id != paper_id or case_fingerprint(paper.cases) != selection:
            raise ValueError(f"封存试卷与运行题目不一致: {run_dir.name}")
        if paper.suite != benchmark.get("suite"):
            raise ValueError(f"封存试卷与题包身份不一致: {run_dir.name}")
        expected = manifest.get("paper_sha256")
        if expected and hashlib.sha256(paper_file.read_bytes()).hexdigest() != expected:
            raise ValueError(f"封存试卷文件哈希不匹配: {run_dir.name}")
    protocol = manifest.get("scoring_protocol")
    if protocol is None:
        protocol = {"legacy_judge": manifest.get("judge"), "metric_version": "legacy-unsealed"}
        warnings.append("旧归档未封存评分协议；比较不能证明全部评分条件一致")
    return {
        "selection": selection,
        "cases": sorted(manifest.get("cases", [])),
        "suite": benchmark.get("suite"),
        "paper_id": paper_id,
        "protocol": protocol,
        "sandbox": sandbox_state(manifest),
        "exploratory": benchmark.get("exploratory", False),
    }, warnings


def _aggregate_identity(manifests: list[dict], warnings: list[str]) -> dict:
    """同智能体多轮聚合的执行口径：真实版本 + 模型 + 思考条件。

    明确不一致（两边都有值且不同）→ 抛错拒绝聚合；缺字段/字段混用 →
    警告降级，verified=false，不冒充核验通过。"""
    adapters = {m.get("adapter") for m in manifests}
    if len(adapters) > 1:
        raise ValueError(f"口径混杂，拒绝聚合：adapter 不一致（{sorted(map(str, adapters))}）")

    signals = [version_signal(m) for m in manifests]
    known = [value for value, _ in signals if value is not None]
    fields = sorted({field for _, field in signals if field})
    if len(set(known)) > 1:
        shown = "、".join(f"{v}({f})" for v, f in signals if v is not None)[:160]
        raise ValueError(f"口径混杂，拒绝聚合：被测真实版本不一致（{shown}）")
    if len(known) < len(signals):
        warnings.append(
            f"{len(signals) - len(known)} 轮缺 agent_version/adapter_version，"
            "被测真实版本未知（未按相同处理）"
        )
    if len(fields) > 1:
        warnings.append(
            "版本字段混用（agent_version 与历史 adapter_version 语义不同），即使同值也不构成版本一致的证明"
        )
    elif fields == ["adapter_version"]:
        warnings.append("各轮仅历史 adapter_version 落账，按旧字段核对（agent_version 未封存）")

    backends = [backend_signal(m) for m in manifests]
    sealed = [b for b in backends if b["mode"] not in (None, "unknown")]
    if len({json.dumps(b, sort_keys=True, ensure_ascii=False) for b in sealed}) > 1:
        raise ValueError("口径混杂，拒绝聚合：model_backend 不一致（mode/model/lanes/override）")
    if len(sealed) < len(backends):
        warnings.append("模型口径未完全封存（model_backend 缺失或 unknown），模型条件未核验")

    runtimes = [runtime_signal(m) for m in manifests]
    billed = [r for r in runtimes if r["models"]]
    if len({json.dumps(r, sort_keys=True, ensure_ascii=False) for r in billed}) > 1:
        raise ValueError("口径混杂，拒绝聚合：实际请求模型/思考强度不一致（llm_runtime）")
    if len(billed) < len(runtimes):
        warnings.append("实际请求模型/思考强度未完全记账（llm_runtime 缺失），模型条件未核验")

    return {
        "adapter": manifests[0].get("adapter"),
        "agent_version": known[0] if known else None,
        "version_field": fields[0] if len(fields) == 1 else ("mixed" if fields else None),
        "model_backend": backends[0],
        "runtime": runtimes[0] if billed else None,
    }


def check_compatibility(run_dirs: list[Path], *, aggregate: bool = False) -> dict:
    if not run_dirs:
        raise ValueError("至少提供一个运行目录")
    identities: list[dict] = []
    manifests: list[dict] = []
    warnings: list[str] = []
    for directory in run_dirs:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        identity, notes = _identity(directory, manifest)
        identities.append(identity)
        manifests.append(manifest)
        warnings.extend(notes)
    first = identities[0]
    for identity in identities[1:]:
        for key, label in [
            ("selection", "题目内容"),
            ("cases", "题目清单"),
            ("suite", "题包版本"),
            ("paper_id", "固定试卷"),
            ("protocol", "评分协议与判卷器"),
            ("exploratory", "抽题模式"),
        ]:
            if identity[key] != first[key]:
                raise ValueError(f"口径混杂，拒绝比较或聚合：{label}不一致")
    # 沙箱口径单独比：明确不一致拒绝；unknown（未封存/不可识别）只降级警告，
    # 不默认当 sandbox=0 放行
    sandbox_values = [i["sandbox"] for i in identities]
    known_sandbox = sorted({v for v in sandbox_values if v is not None})
    if len(known_sandbox) > 1:
        raise ValueError(
            f"口径混杂，拒绝比较或聚合：沙箱口径不一致（{' vs '.join(known_sandbox)}）"
        )
    if any(v is None for v in sandbox_values):
        warnings.append(
            "沙箱口径未完全封存（environment.sandbox 缺失或不可识别），一致性未核验——不默认当作关闭"
        )
    aggregate_identity = _aggregate_identity(manifests, warnings) if aggregate else None
    if first["exploratory"]:
        warnings.append("历史轮换为探索评测，不应与固定试卷正式排名混用")
    return {
        "verified": not warnings,
        "warnings": sorted(set(warnings)),
        "selection_fingerprint": first["selection"],
        "paper_id": first["paper_id"],
        "identity": {
            "sandbox": first["sandbox"],
            "suite": first["suite"],
            "exploratory": first["exploratory"],
        },
        "aggregate_identity": aggregate_identity,
    }
