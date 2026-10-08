"""run 目录离线完整性校验（择优移植自 leeyu44 PR #3，适配主线产物格式）。

主线 runner 的产物是 manifest + cases/<id>/{case.yaml, evidence.jsonl} +
verdicts.jsonl；fork 版 runner 额外写 cases.json / case_results / 各级哈希
封印（cases_snapshot_sha256、逐题 evidence_sha256、evidence_bundle_sha256、
output_hashes）。适配原则：主线有的字段全量校验，封印类字段**有则强校验、
无则降级为一条警告**——校验器对两种产物都能给出明确结论，不把"没写封印"
误报成"证据被篡改"。

核心校验（与封印无关，对所有 run 生效）：
  证据逐条 schema 校验 + payload SHA-256 复算、evidence_id 去重、
  run_id/case_id 一致性、阶段覆盖（case.yaml 定义哪些阶段）、
  verdicts.jsonl 格式 / 去重 / evidence_refs 引用有效性。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from memhall.runner.orchestrator import _sha256
from memhall.schema.evidence import (
    ActionDump,
    Evidence,
    EvidencePhase,
    EvidenceType,
    FsDiff,
    MemorySnapshot,
    Reply,
    Verdict,
)


@dataclass
class VerificationResult:
    run_id: str = ""
    ok: bool = False
    n_cases: int = 0
    n_evidence: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    computed_bundle_sha256: str = ""

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, result: VerificationResult,
               soft: bool = False) -> dict[str, Any] | None:
    """soft=True 时文件不存在返回 None 不记错误（可选产物用）。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not soft:
            result.errors.append(f"缺少文件: {path.name}")
        return None
    except (OSError, json.JSONDecodeError) as error:
        result.errors.append(f"无法读取 {path.name}: {type(error).__name__}: {error}")
        return None
    if not isinstance(value, dict):
        result.errors.append(f"{path.name} 顶层必须是对象")
        return None
    return value


def _validate_payload(evidence: Evidence) -> None:
    """按证据类型校验专属 payload（允许 recorder 附加元数据字段）。"""
    if evidence.type == EvidenceType.DIALOGUE:
        messages = evidence.payload.get("messages")
        replies = evidence.payload.get("replies")
        if not isinstance(messages, list) or not all(
                isinstance(item, str) for item in messages):
            raise ValueError("dialogue.messages 必须是字符串数组")
        if not isinstance(replies, list):
            raise ValueError("dialogue.replies 必须是数组")
        for reply in replies:
            Reply.model_validate(reply)
        if not evidence.payload.get("skipped") and len(messages) != len(replies):
            raise ValueError("dialogue.messages/replies 数量不一致")
    elif evidence.type == EvidenceType.MEMORY_SNAPSHOT:
        MemorySnapshot.model_validate(evidence.payload)
    elif evidence.type == EvidenceType.ACTIONS:
        ActionDump.model_validate(evidence.payload)
    elif evidence.type == EvidenceType.FS_DIFF:
        FsDiff.model_validate(evidence.payload)


def _expected_phases_from_yaml(case_dir: Path) -> set[EvidencePhase] | None:
    """主线格式：从 cases/<id>/case.yaml 读题目定义的阶段集合。"""
    path = case_dir / "case.yaml"
    try:
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        return {EvidencePhase(p["name"]) for p in case.get("phases", [])}
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
        return None


def verify_run(run_dir: Path) -> VerificationResult:
    """校验 schema、payload 哈希、阶段覆盖与（可选的）产物封印。"""
    run_dir = run_dir.resolve()
    result = VerificationResult()
    manifest = _read_json(run_dir / "manifest.json", result)
    if manifest is None:
        return result

    result.run_id = str(manifest.get("run_id", ""))
    if run_dir.name != result.run_id:
        result.warnings.append(
            f"目录名 {run_dir.name} 与 run_id {result.run_id} 不一致")
    if "status" in manifest:
        if manifest.get("status") != "completed":
            result.errors.append(f"运行状态不是 completed: {manifest.get('status')}")
    elif not manifest.get("finished_at"):
        # 主线 manifest 无 status 字段：用 finished_at 侧面确认收尾
        result.warnings.append("manifest 无 status/finished_at，运行是否收尾未知")

    # 阶段定义来源二选一：fork 的 cases.json 快照，或主线的逐题 case.yaml
    expected_phases: dict[str, set[EvidencePhase]] = {}
    cases_snapshot = _read_json(run_dir / "cases.json", result, soft=True)
    if cases_snapshot is not None:
        expected = manifest.get("cases_snapshot_sha256")
        actual = _file_sha256(run_dir / "cases.json")
        if expected and expected != actual:
            result.errors.append("cases.json 哈希与 manifest 不一致")
        snapshot_cases = cases_snapshot.get("cases", [])
        if not isinstance(snapshot_cases, list):
            result.errors.append("cases.json.cases 必须是数组")
            snapshot_cases = []
        snapshot_ids = []
        snapshot_hashes: dict[str, str] = {}
        for item in snapshot_cases:
            if not isinstance(item, dict) or not item.get("case_id"):
                result.errors.append("cases.json 含无效用例对象")
                continue
            case_id = str(item["case_id"])
            snapshot_ids.append(case_id)
            snapshot_hashes[case_id] = _sha256(item)
            phases: set[EvidencePhase] = set()
            for phase in item.get("phases", []):
                try:
                    phases.add(EvidencePhase(phase["name"]))
                except (KeyError, TypeError, ValueError):
                    result.errors.append(f"{case_id}: cases.json 含无效阶段")
            expected_phases[case_id] = phases
        if snapshot_ids != manifest.get("cases", []):
            result.errors.append("cases.json 的用例顺序与 manifest 不一致")
        if manifest.get("case_hashes") and snapshot_hashes != manifest.get("case_hashes"):
            result.errors.append("cases.json 的逐题哈希与 manifest 不一致")
        if manifest.get("cases_version") and (
                f"sha256:{_sha256(snapshot_hashes)}" != manifest.get("cases_version")):
            result.errors.append("cases.json 的题库指纹与 manifest 不一致")
    else:
        result.warnings.append(
            "无 cases.json 快照（主线 runner 逐题写 case.yaml）——阶段覆盖"
            " 按各 case.yaml 校验，题库级封印跳过")

    seen_ids: dict[str, str] = {}
    case_file_hashes: dict[str, str | None] = {}
    raw_case_results = [
        item for item in manifest.get("case_results", [])
        if isinstance(item, dict) and item.get("case_id")
    ] if isinstance(manifest.get("case_results"), list) else []
    has_case_results = bool(raw_case_results)
    if not has_case_results:
        result.warnings.append(
            "manifest 无 case_results / 逐题 evidence_sha256 封印（主线口径："
            "判定与对账走 verdicts.jsonl）——证据完整性仍逐条强校验")
    case_results = {
        item.get("case_id"): item
        for item in raw_case_results
    }
    if len(case_results) != len(raw_case_results):
        result.errors.append("manifest.case_results 含重复 case_id")
    expected_cases = manifest.get("cases", [])
    if not isinstance(expected_cases, list):
        result.errors.append("manifest.cases 必须是数组")
        expected_cases = []

    for case_id in expected_cases:
        case_dir = run_dir / "cases" / str(case_id)
        evidence_path = case_dir / "evidence.jsonl"
        case_status = _read_json(case_dir / "case.json", result, soft=True)
        recorded = case_results.get(str(case_id))
        if has_case_results and recorded is None:
            result.errors.append(f"{case_id}: manifest 缺少 case_result")
            recorded = case_status or {}
        elif case_status is not None and recorded is not None \
                and _sha256(recorded) != _sha256(case_status):
            result.errors.append(f"{case_id}: case.json 与 manifest.case_results 不一致")
        if not evidence_path.is_file():
            result.errors.append(f"{case_id}: 缺少 evidence.jsonl")
            case_file_hashes[str(case_id)] = None
            continue

        file_hash = _file_sha256(evidence_path)
        case_file_hashes[str(case_id)] = file_hash
        if recorded is not None and "evidence_sha256" in recorded \
                and recorded.get("evidence_sha256") != file_hash:
            result.errors.append(f"{case_id}: evidence.jsonl 文件哈希不一致")

        phase_types: dict[EvidencePhase, set[EvidenceType]] = {
            phase: set() for phase in EvidencePhase
        }
        memory_counts: dict[EvidencePhase, int] = {
            phase: 0 for phase in EvidencePhase
        }
        count = 0
        case_evidence_ids: set[str] = set()  # 主线 evidence_id 按用例重排，去重按用例域
        try:
            lines = evidence_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            result.errors.append(f"{case_id}: 读取证据失败: {error}")
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                result.warnings.append(f"{case_id}:{line_number}: 空行")
                continue
            try:
                evidence = Evidence.model_validate(json.loads(line))
                _validate_payload(evidence)
            except Exception as error:
                result.errors.append(
                    f"{case_id}:{line_number}: 证据格式错误: "
                    f"{type(error).__name__}: {error}")
                continue
            count += 1
            result.n_evidence += 1
            if evidence.evidence_id in case_evidence_ids:
                result.errors.append(
                    f"{case_id}:{line_number}: 重复 evidence_id "
                    f"{evidence.evidence_id}")
            case_evidence_ids.add(evidence.evidence_id)
            seen_ids[evidence.evidence_id] = str(case_id)
            if evidence.run_id != result.run_id:
                result.errors.append(
                    f"{case_id}:{line_number}: run_id 不一致")
            if evidence.case_id != case_id:
                result.errors.append(
                    f"{case_id}:{line_number}: case_id 不一致")
            if _sha256(evidence.payload) != evidence.sha256:
                result.errors.append(
                    f"{case_id}:{line_number}: payload SHA-256 不一致")
            phase_types[evidence.phase].add(evidence.type)
            if evidence.type == EvidenceType.MEMORY_SNAPSHOT:
                memory_counts[evidence.phase] += 1

        if case_id not in expected_phases:
            from_yaml = _expected_phases_from_yaml(case_dir)
            if from_yaml is not None:
                expected_phases[str(case_id)] = from_yaml
        required_phases = expected_phases.get(str(case_id), set(EvidencePhase))
        observed_phases = {
            phase for phase, types in phase_types.items() if types
        }
        for phase in sorted(observed_phases - required_phases, key=lambda x: x.value):
            result.errors.append(f"{case_id}:{phase.value}: 出现题目未定义的阶段证据")
        # 主线口径：inject/confound 至少有对话证据，probe 四类齐全
        # （actions/fs_diff 只在终采阶段记录；fork 产物四阶段全写，本规则同样放行）
        for phase in sorted(required_phases, key=lambda x: x.value):
            need = (set(EvidenceType) if phase == EvidencePhase.PROBE
                    else {EvidenceType.DIALOGUE})
            missing = need - phase_types[phase]
            if missing:
                result.errors.append(
                    f"{case_id}:{phase.value}: 缺少证据 "
                    + ", ".join(sorted(item.value for item in missing)))
        if memory_counts[EvidencePhase.INJECT] == 0:
            result.errors.append(f"{case_id}: inject 阶段无任何记忆快照")
        elif memory_counts[EvidencePhase.INJECT] < 2:
            # fork runner 注入后即时采两条；主线 run 缺第二条记警告不定罪
            result.warnings.append(
                f"{case_id}: inject 仅 {memory_counts[EvidencePhase.INJECT]} 条记忆快照")
        if case_status is not None and case_status.get("evidence_count") != count:
            result.errors.append(
                f"{case_id}: case.json evidence_count 与实际不一致")
        result.n_cases += 1

    result.computed_bundle_sha256 = _sha256(case_file_hashes)
    if manifest.get("evidence_bundle_sha256") is not None \
            and manifest.get("evidence_bundle_sha256") != result.computed_bundle_sha256:
        result.errors.append("整包证据哈希与 manifest 不一致")
    if manifest.get("n_cases_completed") is not None \
            and manifest.get("n_cases_completed") != len(case_results):
        result.errors.append("manifest.n_cases_completed 与 case_results 不一致")
    if has_case_results \
            and set(case_results) != {str(item) for item in expected_cases}:
        result.errors.append("manifest.case_results 与用例清单不一致")

    verdict_path = run_dir / "verdicts.jsonl"
    seen_verdict_ids: set[str] = set()
    if verdict_path.is_file():
        for line_number, line in enumerate(
                verdict_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                verdict = Verdict.model_validate(json.loads(line))
            except Exception as error:
                result.errors.append(
                    f"verdicts.jsonl:{line_number}: 判定格式错误: {error}")
                continue
            if verdict.run_id != result.run_id:
                result.errors.append(
                    f"verdicts.jsonl:{line_number}: run_id 不一致")
            if verdict.verdict_id in seen_verdict_ids:
                result.errors.append(
                    f"verdicts.jsonl:{line_number}: 重复 verdict_id "
                    f"{verdict.verdict_id}")
            seen_verdict_ids.add(verdict.verdict_id)
            for reference in verdict.evidence_refs:
                if isinstance(reference, str) and reference.startswith("ev-"):
                    if reference not in seen_ids:
                        result.errors.append(
                            f"verdicts.jsonl:{line_number}: "
                            f"引用不存在的证据 {reference}")
                    elif seen_ids[reference] != verdict.case_id:
                        result.errors.append(
                            f"verdicts.jsonl:{line_number}: 引用了其他用例的证据 "
                            f"{reference}")
    else:
        result.warnings.append("缺 verdicts.jsonl（未判卷或判卷产物被移走）")

    output_hashes = manifest.get("output_hashes")
    if output_hashes is not None:
        if not isinstance(output_hashes, dict):
            result.errors.append("manifest.output_hashes 必须是对象")
        else:
            allowed = {"verdicts.jsonl", "metrics.json", "report.md", "radar.png"}
            if set(output_hashes) != allowed:
                result.errors.append("manifest.output_hashes 文件集合不完整")
            for name in sorted(allowed):
                path = run_dir / name
                if not path.is_file():
                    result.errors.append(f"缺少评分产物: {name}")
                elif output_hashes.get(name) != _file_sha256(path):
                    result.errors.append(f"评分产物哈希不一致: {name}")

    result.ok = not result.errors
    return result
