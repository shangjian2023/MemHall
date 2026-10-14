# 历史批 LLM 判卷人工盲评 · 工作表与协议（2026-10-14）

**状态：awaiting_human_labels —— 72 条样本已导出，0 条已标注。**

本目录是对历史 18 轮（full/chains，规则 + 单评委 A）LLM 判卷质量的分层盲评材料。目标是让两名真人评审独立给出人工判定，再用 `memhall calibrate --analyze` 对比机器判定与人工判定的一致性。在真人标签填写完成之前，任何一致率、Cohen κ、F1 都不存在，也不允许由 LLM 代填"人工"标签。

## 导出命令与工具版本

工具：`src/memhall/calibration.py`（`python -m memhall calibrate`），代码基线 BASE_COMMIT `4185672`（分支 `codex/fix-submission-evidence`），共享 venv，PYTHONPATH 指向本 worktree 的 `src`。

```bash
python -m memhall calibrate \
  runs/20261010-135112-hermes runs/20261011-103806-hermes \
  runs/20261010-181612-kylinbot runs/20261011-131723-kylinbot \
  runs/20261010-200117-openclaw runs/20261010-215351-openclaw \
  runs/20261011-164757-openclaw runs/20261011-183642-openclaw \
  runs/20261011-102221-hermes runs/20261011-130417-hermes \
  runs/20261011-144945-kylinbot runs/20261011-164015-kylinbot \
  runs/20261011-182539-openclaw runs/20261011-201110-openclaw \
  --per-capability 12 --seed 20261014 -o <导出目录>
```

结果：`n_samples=72`，六维各 12；`sheet_fingerprint=c73abd9f…a83f5c5`，`key_fingerprint=8e7aba62…1f6c7`（见 `base/calibration-manifest.json`）。

## 抽样范围（进框的 14 轮）

只有通过 2026-10-14 完整性核验（`verify.ok=true`）的历史轮进入抽样框：

- full（8 轮）：hermes r2/r4、kylinbot r2/r3、openclaw r1–r4。
- chains（6 轮）：hermes、kylinbot、openclaw 各 2 轮。

被排除的 4 轮及原因（见 `docs/audit/review-20261013/verification.json`、`build/judge-review-20261014/experiment-audit.json`）：

| 轮 | 排除原因 |
|---|---|
| 20261010-073953-hermes | update-005/006/007 缺 phase 对话与 inject 记忆快照（不能补写） |
| 20261011-073455-hermes | 仅缺 radar.png；判定与证据齐全。重渲染件见 `docs/audit/review-20261013/reconstructed-radar.png`，原始封印未动 |
| 20261010-163950-kylinbot | boundary-005 probe 缺证据对话 |
| 20261011-145803-kylinbot | boundary-005 probe 缺证据对话 |

抽样框内的判定还必须满足：探测点为语义探测（JudgeProbe）、`decided_by` 为 `judge_a` 或 `human_review`、非降级判定。**规则断言判定（decided_by=rule）、人工已决判定与降级判定不在框内**——本表校准的是 LLM 评委，不是整套评分系统的总体准确率。

框内候选共 472 条，按能力 × 机器判定态构成：

| 能力 | correct | omission | confusion | fabrication | over_persist | wrong_reuse | human_review |
|---|---:|---:|---:|---:|---:|---:|---:|
| boundary | 44 | 2 | 1 | 9 | 6 | 0 | 2 |
| discriminate | 53 | 4 | 0 | 7 | 0 | 0 | 0 |
| dynamic_update | 31 | 9 | 0 | 5 | 0 | 17 | 2 |
| persist | 98 | 13 | 3 | 2 | 0 | 0 | 0 |
| recall | 75 | 14 | 10 | 4 | 0 | 0 | 1 |
| reuse | 48 | 7 | 3 | 0 | 0 | 2 | 0 |
| 合计 | 349 | 49 | 17 | 27 | 6 | 19 | 5 |

抽样方式：每能力内按 `sample_id` 排序后用 `random.Random(20261014)` 均匀抽取 12 条，再全表乱序。**能力内不按判定态、智能体或题族分层**。

## 实际分层核对（72 条）

| 分层目标 | 实际情况 |
|---|---|
| 六维 | 各 12 条，达标 |
| 四家智能体 | 只覆盖三家：openclaw 36、kylinbot 18、hermes 18。kylin-agent 无历史归档，待 D 的正式验收归档到齐后另行导出补充表，不能复制或推断 Hermes 分数 |
| 题族 | 35 个不同 family_id（旧题未声明 family_id 时按 case_id 聚类） |
| correct | 52 条 |
| 错误态 | 17 条：omission 6、fabrication 5、confusion 4、wrong_reuse 2 |
| 需要复核 | 3 条（机器态 human_review） |
| over_persist | **0 条**：框内仅 6 条——openclaw 的行为级 over_persist 计分探测大多由规则断言判定，按框定义被排除。本次盲评无法校准 over_persist 判卷，如实标注 |

## 未采样偏差（不得外推的边界）

1. **不能把本表一致率解释为整套系统的总体判卷准确率**：规则判定（框内占比最大的确定性部分）未被抽检；抽样框仅限 LLM 评委判定。
2. **72 条中 52 条为 correct**：一致率将主要由 correct-correct 主导；各错误态只有 2–6 条，分态误差只能逐条描述，不能当成分态错误率。
3. **三家覆盖不均**（36/18/18），来自框内 openclaw 通过核验的轮次更多；分家误差估计的样本量不同，解读时注明。
4. **4 轮未过核验的归档不在框内**，其判卷质量未被本次盲评覆盖。
5. **human_review 3 条已入表**，但当前 `--analyze` 只统计机器判定属六态的行，这 3 条不计入一致率分母；其人工标注仍要求填写，供逐条比对。
6. 回复文本的风格与内容可能透露被测智能体特征（内容级去标识化的固有极限）；评审只对判定负责，不猜测、不利用身份线索。
7. 工作表已隐藏 adapter、模型与机器判定；映射关系在 `answer-key.csv`（含 run_id/probe_id/case_id 与证据指纹），**评审人不得打开**。

## 双人评审与分歧复核流程

1. 两位评审各自独立填写 `reviewers/reviewer-1/worksheet.csv`、`reviewers/reviewer-2/worksheet.csv` 的 `human_verdict`（六态之一：correct / omission / confusion / fabrication / over_persist / wrong_reuse）和 `notes`。除此之外的任何列不得改动——分析时会校验题目指纹，改过即拒绝。
2. 两份都交回后，协调者分别运行：

   ```bash
   python -m memhall calibrate --analyze docs/audit/blind-review-20261014/reviewers/reviewer-1
   python -m memhall calibrate --analyze docs/audit/blind-review-20261014/reviewers/reviewer-2
   ```

   输出写入各目录 `calibration.json`（六态混淆矩阵、对错一致率、误判为正确的数量、分能力一致率、Cohen κ）。
3. 协调者把两份判定填入 `adjudication.csv`（`reviewer_1_verdict`、`reviewer_2_verdict`、`agree`），分歧行与终裁填 `final_verdict` 与 `adjudicator_notes`。
4. 终裁后：复制 `base/` 三件套为 `adjudicated/`，把终裁标签填入其 `worksheet.csv` 的 `human_verdict` 列，再 `--analyze docs/audit/blind-review-20261014/adjudicated`，作为对外报告的正式人工金标准口径。
5. 全程禁止用另一个 LLM 代替真人填写；标签到齐前材料中不得出现任何一致率数字。

## 已知工具局限（向总控报备）

- `--analyze` 不支持两位评审的组内一致性（reviewer-1 vs reviewer-2 的 κ），当前只能分别对机器判定算；需要时提独立需求扩展。
- 无按判定态分层抽样的选项（如 `--min-per-state`），导致 over_persist 等稀错误态可能整表缺失；本次以文档披露代替。
- `human_review` 态入表但不进统计分母（见上文偏差 5）。

## 文件清单

| 文件 | 说明 |
|---|---|
| `base/` | 工具原样导出：worksheet.csv（待填）、answer-key.csv（受限映射）、calibration-manifest.json（种子与指纹） |
| `reviewers/reviewer-1/`、`reviewers/reviewer-2/` | 与 base 相同的三件套副本，各自独立填写 |
| `adjudication.csv` | 分歧复核与终裁模板，sample_id 已按工作表顺序预填 |
