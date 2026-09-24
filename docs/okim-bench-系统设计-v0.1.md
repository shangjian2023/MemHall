# okim-bench：智能体长期记忆自动化评测系统设计

> 版本 v0.2（2026-09-23）· 依据赛题原文、environment.md 环境基线与《01-智能体记忆机制与评测调研》
> 定位：可扩展数据集 + 证据驱动自动评分 + 一键 CLI + .deb 分发 + 雷达图对比报告

## 0. 设计立场：复用已实现的 benchmark，在其缺口上做提升

本方案**不另起炉灶**，明确站在四个已实现开源 benchmark 的基线上（资产盘点详见调研报告 §2.5）：

| 基线 | 我们直接复用 | 我们在此之上的提升 |
|---|---|---|
| **LongMemEval**（MIT，代码+数据开源） | 属性本体数据生成管线、haystack 编译器、filler 会话机制、证据会话标注格式（`answer_session_ids` + turn 级 `has_answer`）、500 题规模基线 | ① 证据标注从"会话级"细化到"记忆条目级"（支撑 store diff 判定）；② 生成目标从"聊天助手"改为"桌面智能体的可执行任务剧本" |
| **MemoryAgentBench**（ICLR 2026，开源） | 增量多轮流式喂入协议（chunk 喂入）、FactConsolidation 反事实更新对构造法 | ① 更新对从"事实覆盖"扩展到"偏好/路径/模板的版本化覆盖"；② 加入 filler 会话时间间隔，测长期保持而非即时覆盖 |
| **MPBench**（仅论文，方法论复现） | 两阶段评测法（写入检查 + 延迟复用检查）、写入通道分类学 | 把对抗载荷替换为 benign 边界样本（临时信息/PII/凭据/风险指令），形成**边界识别**维度——这是所有现有 benchmark 都没有的 |
| **MemBench**（开源） | factual/reflective 双层内容、多维指标（accuracy/recall/capacity/temporal efficiency） | 效率/容量指标落到真实 OS 环境度量（文件产物、token 成本、快照体积） |

**一句话差异化**：已有 benchmark 评的是"模型在聊天 harness 里记不记得住"，我们评的是"**真实智能体在 openKylin 桌面上记没记对、用没用对、不该记的是不是真没记**"——新增 store-level 证据判定、边界识别维度、任务复用产物断言、双评委一致性闸门与快照级可复现。

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│ CLI: okim-bench run / score / report / compare             │
├──────────────┬─────────────────┬───────────────────────────┤
│ 数据集层      │ 执行层           │ 评分层                     │
│ cases/*.jsonl│ SessionDriver   │ L1 确定性检查器             │
│  (可扩展)     │  ├ AgentAdapter │ L2 语义规则(embedding)     │
│              │  │  ├ kylinbot  │ L3 双 LLM judge(跨家族)    │
│              │  │  ├ openclaw  │   + 一致性闸门(Kappa)      │
│              │  │  └ hermes    │                            │
├──────────────┴─────────────────┴───────────────────────────┤
│ 证据层 Evidence Bundle（每次 run 一份，全量哈希入 manifest） │
│  transcripts/ memory_snapshots/ traces/ artifacts/ logs/    │
├────────────────────────────────────────────────────────────┤
│ 报告层 report/：metrics.json 六维雷达图 + 五类错误矩阵        │
│              + HTML 报告 + 评分理由(证据引用) + manifest     │
└────────────────────────────────────────────────────────────┘
```

设计原则：**评分对象从"答案"前移为"证据"**——每个 case 跑完后先落一份完整证据包，评分器只读证据包、不接触 agent，评分可离线重跑、可审计、可复现。

## 2. 数据集设计（数据设计质量 25%）

### 2.1 Case schema（JSONL，一行一个用例，字段可扩展）

```json
{
  "case_id": "KU-0042",
  "capability": "knowledge_update",        // 六能力之一，见 §2.2
  "subtype": "direct_override",            // 能力内细分
  "difficulty": "easy|medium|hard",
  "sessions": [                            // 剧本化多轮会话（驱动写入）
    {"turns": [{"role": "user", "text": "...", "expect": "optional"}],
     "filler_sessions": 2}                 // 干扰：插入 N 个无关会话后再探测
  ],
  "probes": [                              // 探测项（驱动读取/行为）
    {"probe_id": "KU-0042-p1",
     "kind": "memory_store_check|qa|task",
     "question": "...",                    // qa 用
     "ground_truth": "...",                // 客观答案或结构化断言
     "expect_store": {"retain": ["事实X=新值"], "absent": ["事实X=旧值"]},
     "task_spec": {"goal": "...", "verify": "artifact_assertions"}   // task 用
    }
  ],
  "scoring": {"rubric_id": "ku_v1"},
  "tags": ["偏好", "文件路径", "模板"]
}
```

扩展性：新增能力 = 新增 `capability` 取值 + 对应 checker 插件；新增智能体 = 新增 Adapter，用例零改动。

### 2.2 六能力 → 题型映射（区分度优先）

| 能力 | 题型设计 | 核心干扰手段 | 主要证据 |
|---|---|---|---|
| 长期保持 retention | 单会话埋入事实 → 隔 2~5 个 filler 会话后 QA 探测 | 时间距离、话题漂移 | store diff + QA 对 |
| 记忆调用 recall | 多跳问答（需组合 2+ 会话信息）、needle 检索 | 相似干扰项、同义改写提问 | QA 对 + 检索日志 |
| 动态更新 update | 反事实更新对：先教旧值，后教新值（MemoryAgentBench 式） | 新旧值语义相近（地址/版本号） | store diff（旧值消失、新值存在）+ QA 用新值 |
| 相近区分 discrimination | 成对相似实体（两台服务器路径、两个项目模板），问其中之一的属性 | 表面高度相似的干扰项 | QA 对 + store 条目归属 |
| 边界识别 boundary | 三类：①临时信息（"这次先用端口8080"）②敏感信息（PII/凭据样例）③风险指令（要求记住并执行危险操作） | 用日常口吻包装，无显式"记住"指令 | store absent 检查（L1）+ 延迟行为探测（MPBench 式第二会话） |
| 任务复用 reuse | 先教流程/偏好（"报表都用这个模板生成"），后给真实文件任务 | 任务表面与教学时不同但需同一记忆 | 产物断言（文件内容/格式）+ 行为轨迹 |

### 2.3 数据生成方式

- 本体驱动（LongMemEval 法）：定义用户属性本体（偏好/路径/模板/项目/人物/事件 6 类，每类 10~20 属性），LLM 生成属性级背景段落，人工抽检 10%。
- 反事实对：同一事实生成旧/新两版本，保证新旧可判定区分。
- 干扰样本库：为每个真值配 2~3 个表面相似假值（项目名 ±1 字符、路径前缀相同等），供相近区分与混淆判定复用。
- 规模基线：首版 ≥ 300 case（每能力 ≥ 40），样例集（交付 b）从中抽 30 case 附完整证据与评分。

## 3. 执行层

### 3.1 AgentAdapter 接口

```python
class AgentAdapter(Protocol):
    def reset(self) -> None: ...                    # 回滚快照/清空记忆，保证用例间隔离
    def run_session(self, turns) -> SessionTrace: ...  # 喂入剧本，采集对话+工具调用
    def snapshot_memory(self) -> MemorySnapshot: ...   # 直读/导出记忆存储，统一中间格式
    def run_task(self, spec) -> TaskTrace: ...         # 执行真实任务，采集产物
```

- KylinBot adapter：会话驱动走 UI/接口；记忆取证走设置-记忆导出 + 日志（W1 摸清存储后补直读路径）。
- OpenClaw / Hermes adapter：直读 `MEMORY.md` / `memory/*.md` / `~/.hermes/memories/*.md`，sqlite 索引进快照。
- 隔离：每个 case 前回滚 VM `clean-baseline` 快照或 adapter 级记忆清空，两者取成本较低者（开发期 adapter 清空，验收/录屏期快照回滚）。

### 3.2 统一记忆中间格式（MemorySnapshot）

```json
{"agent": "openclaw", "captured_at": "...", "store_hash": "sha256:...",
 "entries": [{"id": "...", "text": "...", "source": "MEMORY.md:L12", "created_hint": "..."}]}
```

所有 agent 的记忆证据归一到 entries 列表，使 **L1/L2 检查器与 agent 完全解耦**——这是"通用性 15%"的核心得分点。

## 4. 评分层（自动评分能力 25%）

### 4.1 五类错误判定决策树（防语义偏移的关键）

对每个 probe，按序做确定性判定，judge 只做最后的语义归因：

```
记忆状态判定（store-level）
├─ expect_store.absent 中出现禁止内容？ → 错误持久化（boundary 失败）
├─ expect_store.retain 缺失？           → 遗漏
├─ 保留但归属/取值张冠李戴（相似干扰项命中）？ → 混淆
└─ 全部命中                              → 正确记忆

行为判定（behavior-level）
├─ 使用了已过期/被覆盖的旧值行动？       → 错误复用
├─ 回答使用了正确记忆？                  → 正确复用
└─ 该用没用/答非所问                     → 调用失败
```

L1 判定用：精确/规范化字符串匹配、PII-正则、文件断言脚本（OSWorld 式 execution oracle）。
L2 判定用：embedding 相似度 ≥ τ 判"语义等价存在"，τ 在校准集上标定并写入 manifest。

### 4.2 LLM judge 使用规范

- 仅用于：开放问答正确性、行为归因说明、"是否合理复用"的边界 case。
- 双评委跨家族（`JUDGE_A` / `JUDGE_B` 不同厂商模型），结构化输出：
  `{verdict, confidence, evidence_refs[], reason}`，**无 evidence_refs 的评分无效**。
- 顺序交换双跑 + 一致性 Cohen's Kappa < 0.6 → 该 probe 标记 `needs_review`（低人工介入闸门，而非全量人工）。
- 每次 run 的 judge token 成本按端点分列写入 manifest（environment.md §4 要求）。

### 4.3 指标输出（指标完整性 10%）

- 六维能力分（每能力 = probe 加权正确率，错误类型分列）。
- 五类错误率矩阵：正确记忆/遗漏/混淆/错误持久化/错误复用 × 六能力。
- 过程指标：记忆容量占用、token 成本、平均会话时延（借鉴 MemBench 效率维度）。
- 稳定性指标：同 case 3 次重复运行方差；judge 间 Kappa。
- 雷达图：matplotlib + fonts-noto-cjk（environment.md §6），六轴 = 六能力，多 agent 叠加。

## 5. 可复现性（15%）

- 运行契约（measurement contract）：模型端点+日期、prompt 模板版本、case ID 清单、scorer 版本、镜像批次、随机种子、temperature、快照 ID，全量写入 `manifest.json`。
- 证据包全量 sha256 哈希链；`okim-bench score --bundle <path>` 可离线重评分。
- 温度固定（agent 侧允许其默认，评分侧 judge temperature=0）；重复运行默认 ×3 取均值±方差。
- 验收：W1 末按 environment.md §5 清单过一遍；每次评测前回滚 `clean-baseline`。

## 6. 工具化与分发（交付 d）

- CLI（Python，uv 管理）：`okim-bench run --suite full --agents kylinbot,openclaw` → `okim-bench report --radar`。
- 打包 `.deb`：WSL2 内 `dpkg-deb --build` 练手 → VM 内干净安装验证（environment.md §3 既定路线）。
- 配置：每 agent 一个 YAML（路径、启动方式、模型端点、预算上限），`--agents` 批量对比。
- GUI 为非必须项，CLI 优先；富余工期再考虑 UKUI 集成面板（创新加分项）。

## 7. 与评审维度对照自查

| 评审维度（权重） | 本设计对应点 |
|---|---|
| 任务定义与通用性 15% | 统一 MemorySnapshot 解耦 agent；adapter 插件化；OS 无关 harness |
| 数据设计质量 25% | 本体驱动生成、反事实更新对、相似干扰库、六能力×多题型 ≥300 case |
| 自动评分能力 25% | L1/L2/L3 三层、五类错误决策树、证据引用强制、双评委+Kappa 闸门 |
| 稳定性与可复现 15% | 快照回滚、种子固定、manifest 测量契约、离线重评分、重复运行方差 |
| 指标完整性 10% | 六维雷达 + 错误矩阵 + 过程/稳定性指标 |
| 创新与落地 10% | store+behavior 双层评测、证据包哈希链、.deb 入软件源、token 成本记账 |

## 8. 风险与开放问题

1. **KylinBot 记忆取证路径未明**（W1 阻塞项）：若无导出接口，需直读运行时存储或截屏 OCR 兜底——取证方式决定 adapter 工作量，W1 必须关闭。
2. **judge 成本**：300 case × 3 重复 × 2 评委，预算需 E 按 manifest 口径核算，必要时对 L1 已判定的 probe 跳过 judge。
3. **agent 间模型后端不一致**：被测 agent 各自用不同 LLM 后端时，分数混入了模型差异——报告须强制声明后端模型，条件允许时统一后端复测（通用性维度的评审关注点）。
4. **边界识别的"度"**：风险指令用例须止于评测沙箱，不产出真实破坏行为（快照回滚兜底）。
