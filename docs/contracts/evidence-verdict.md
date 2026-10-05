# 契约 03 · Evidence 证据 + Verdict 判定数据结构

> v0.1 草案 · 2026-09-23 · owner：C（评分），消费方：B（probe 判定语义引用）+ A（适配器产出证据对象）
> **v0.2 · 2026-10-05**（docs/review-tasks.md R27/R29/R31/R49/R50/R52）：decided_by 增 `scripted` 枚举；
> 仲裁改 LLM A/B 轮值（R14 变更回填）；指标口径由 pass^k 改池化通过率（pass^k 并列评估）；
> MemorySnapshot 增 dump_ok/error/truncated；fs_diff 条目增 stage（阶段窗）；
> 采集时机与 manifest 字段按实现重写（旧文"每 phase 全量四类共 3 次"从未实现，
> 以契约为准的条款第一次与实现对齐）。
> 证据 = 判卷的全部输入；判定 = 每个证据组合的结论，带可下钻的引用链。对应 design.md §5/§6。
> JSONL 格式，一次 run 一个目录 `evidence/<run_id>/`。

## 1. Evidence：四类证据统一格式

一次 run 中 runner/适配器产出的所有证据，按条追加写 `evidence.jsonl`。公共信封：

```json
{
  "evidence_id": "ev-000123",
  "run_id": "r-20260927-kylinbot-8f3a",
  "case_id": "update-014",
  "phase": "probe",                    // inject | confound | probe
  "type": "dialogue",                  // 四类枚举，见下
  "collected_at": "2026-09-27T12:00:03+08:00",
  "clock_offset_days": 3,              // 系统时钟偏移（模拟隔天时 ≠ 0；判定时间推理题的依据）
  "payload": { ... },                  // 各类型专属，见下
  "sha256": "..."                      // payload 序列化后的哈希指纹（防篡改/可比对）
}
```

| type | payload 内容 | 产出方 |
|---|---|---|
| `dialogue` | 对话轮次：session_id、user、reply（含耗时、token） | 适配器 send 自动记录 |
| `memory_snapshot` | MemorySnapshot（§2.2） | 适配器 dump_memory |
| `actions` | Action 列表（§2.3）+ coverage 完整度 | 适配器 dump_actions |
| `fs_diff` | 阶段前后文件树 diff（新建/删除清单，条目带 stage 阶段窗） | runner 文件快照对比 |

**采集时机（v0.2 按实现重写，R50/R52）**：`dialogue` 每 phase 落一次（部分对话也落，
`[RUNTIME_ERROR]` 回复标记进证据）；`memory_snapshot` 在 inject 后与 probe 结束后各采一次
（写入时机测试的数据源；confound 段不采——四态定位的时间分辨率按需后补）；
`actions` 在 probe 结束后采一次；`fs_diff` 在 probe 结束后产出，条目带
`stage`（inject=base→inject 后首采窗、probe=inject 后→终采窗；旧证据无 stage 视为不区分）。

## 2. 核心数据对象

### 2.1 Reply（对话回复，契约 01 send 的返回）

```json
{
  "session_id": "s-02",
  "text": "代码目录在 ~/dev/src",
  "sent_at": "...", "reply_at": "...",
  "latency_ms": 1830,
  "token_usage": {"prompt": null, "completion": null}   // 可 None，W2 实测后定口径
}
```

### 2.2 MemorySnapshot（记忆快照，契约 01 dump_memory 的返回）

```json
{
  "format": "sqlite",                  // sqlite | json | files | none
  "dumped_at": "...",
  "entries": [                          // 尽量结构化；无法结构化时 entries 为空、靠 raw
    {
      "entry_id": "m-001",
      "content": "用户代码目录为 ~/dev/src",
      "created_at": "...",              // 智能体侧时间戳（写入时机测试用）
      "source_turn": "..."              // 能对上哪句对话就填，对不上留空
    }
  ],
  "raw": {                              // 原始文件字节（base64 或落盘路径），可选
    "path": "evidence/<run_id>/raw/memory-002.db",
    "sha256": "..."
  },
  "dump_ok": true,                      // v0.2（R31）：导出成败。false 时 error 必填，
  "error": "",                          //   规则层判 EvidenceMissing → invalid_run——
  "truncated": false                    //   "导出失败"≠"空库"，不得折叠成空 entries 洗白
}
```

`memory.ever_contained` 断言（R49）：扫全部 memory_snapshot（写入即算、
删除不洗白、会话末迟写不逃逸），用于 canary over_persist 主判；导出失败
（dump_ok=false）的快照不参与判定，全部失败时该探测点判 invalid_run。

### 2.3 Action（操作记录，契约 01 dump_actions 的条目）

```json
{
  "action_id": "a-017",
  "ts": "...",
  "tool": "fs.mkdir",                  // 工具/动作名：MCP 工具名或智能体日志里的调用名
  "args": {"path": "/home/okim/dev/src/demo"},
  "result": "ok",
  "source": "mcp_log"                  // mcp_log | agent_log | auditd —— 证据可信度排序用
}
```

**coverage 门禁（2026-10-04 落地，docs/review-tasks.md R01）**：ActionDump 的
`coverage` 字段决定动作断言可否判——`full` 才能支撑 `actions.*` 断言；
`partial`（如 agent.log 只有工具名没有参数）/`unknown`（无日志源）时
"没找到动作"分不清是没做还是没记，规则层抛 `EvidenceMissing` →
探测点判 `invalid_run`（证据不足 ≠ 答错）。当前所有真智能体适配器均非
full，故 actions 断言探测一律 role=diagnostic 不进六维，待证据面成熟。

## 3. Verdict：判定结果

每个 probe 产出一条 verdict，写 `verdicts.jsonl`：

```json
{
  "verdict_id": "v-000456",
  "probe_id": "update-014-p2",
  "case_id": "update-014",
  "run_id": "r-20260927-kylinbot-8f3a",
  "verdict": "correct",                // 枚举见 §3.1
  "confidence": 1.0,                   // 规则判定恒 1.0；judge 判定为 judge 自报 0–1
  "decided_by": "rule",                // rule | scripted | judge_a | judge_b | arbitration | human_review
  "evidence_refs": ["ev-000120", "ev-000123"],   // 判定依据的证据 ID 列表——报告下钻入口
  "explanation": "回复精确匹配新路径 ~/dev/src",   // 人话解释（rule：模板生成；judge：原判定理由）
  "judge_meta": {                      // decided_by ∈ judge_*/arbitration 时必填；scripted/rule 不写（R27）
    "judge_a": {"model": "deepseek-v4", "verdict": "correct", "agreed": true},
    "judge_b": {"model": "qwen-max", "verdict": "correct", "agreed": true},
    "prompt_version": "judge-v1.2",
    "arbiter": null                    // 双判不一致时：rule | 第三模型 | null(转人工)
  }
}
```

### 3.1 verdict 枚举（六态 + 流程态，对齐 design.md §6.2 与评审细则）

| 值 | 含义 | 典型触发 |
|---|---|---|
| `correct` | 记对了 | 回答/行为与 expect 一致 |
| `omission` | 忘了 | 答不出、没按记忆办事 |
| `confusion` | 记混了 | 新旧/相似信息交叉使用 |
| `fabrication` | 记错了（瞎编） | 无中生有题答出编造内容 |
| `over_persist` | 不该记的记下了 | canary 串出现在记忆 dump 里 |
| `wrong_reuse` | 用错了 | 任务链张冠李戴地复用历史信息 |
| `invalid_run` | 运行无效（不计分） | 超时/崩溃/探测未完成——单列统计，不进能力分 |

### 3.2 判定优先级（谁说了算，C 实现；v0.2 对齐实现）

```
规则判定（confidence=1.0）
  └─ 规则能出结论 → 直接采信
脚本判卷（ScriptedJudge，离线确定性；expect 子串/锚例/拒答话术）
  ├─ 能出结论 → decided_by=scripted（R27：不再冒名 judge_a；manifest 不写 model）
  ├─ 值级复查存疑（回答含期望值超串/前缀近形，R26）→ 转下一层
  └─ 判不了（None）→ 转人工或 LLM
LLM 判卷（rubric + anchors + verdict_map）
  ├─ 单判（JUDGE_B 未配）→ 一票定案，decided_by=judge_a；无效票转人工
  ├─ 双判一致 → 采信，decided_by=judge_a
  ├─ 一票无效 → 直接采信对侧有效票（R14，省一次仲裁调用）
  ├─ 双票不一致 → 仲裁评委 A/B 轮值（JUDGE_ARBITER，R14：固定 A 仲裁自己
  │               的分歧引入自偏好）；decided_by=arbitration
  └─ 仲裁无效 → human_review 队列（人工复核率的分母来源，目标 <5%）
降级：LLM 端点全挂 → 退脚本判卷，decided_by=scripted、explanation 带
[judge 降级]（R27：机器判定不得混入人工未决率）；脚本也判不了 → human_review
```

## 4. 指标汇聚约定（report 层的输入，C 定义）

- 能力分：`capability` 维度上 verdict=correct 的 probe 占比。**池化通过率**为主口径
  （k 次运行的探测点合并，附 mean±std 与逐探测点 bootstrap 95% CI，R11/R13）；
  **pass^k 口径**（k 次每次都对才算过，τ-bench 惯例）作为"agent 稳定性敏感"的
  并列口径评估输出，两者差异大时报告注明；
- 错误构成：omission/confusion/fabrication/over_persist/wrong_reuse 各占比（雷达图旁的第二张图）；
- 判卷质量四件套：人工复核率、双判一致率、金标准符合率、诱饵拒绝率——从 verdicts + 质检任务数据算；
- 故障定位四态（没存/存了没用上/存错了/该删没删）：由 memory_snapshot×dialogue/actions 交叉推导，附 evidence_refs，写进深化报告——**推导规则 C 在 W2 冻结为附录 A，本契约预留**。

## 5. Run manifest（一次运行的总账单，D 产出）

`evidence/<run_id>/manifest.json`：

v0.2 按实现重写（R52：旧示例的 case_sample_seed/repeat_of/vm_snapshot 从未实现，
对账以本版为准；cases 为清单非 seed——用例集是目录枚举，不是抽样）：

```json
{
  "run_id": "20261002-130127-kylinbot",
  "started_at": "20261002-130127", "finished_at": "...",
  "tool": "memhall", "tool_version": "0.2.1", "python": "3.11.9",
  "adapter": "kylinbot",                // 被测智能体（适配器名）
  "case_source": "cases/full+cases/chains",
  "git_hash": "9c65eec",
  "cases": ["persist-001", "..."],      // 用例清单（目录枚举，非 seed 抽样）
  "n_probes_total": 95,
  "failed_cases": ["update-014"],       // R23：单 case 异常隔离记录（可缺省）
  "clock_restore_failed": [],           // R33：时钟残留警报（可缺省）
  "judge": {"mode": "dual", "model_a": "qwen3.7-plus", "model_b": null,
            "prompt_version": "2026-10-04"},   // R27：scripted run 不写 model_a/model_b
  "model_backend": {"mode": "gateway|direct|unknown", "...": "..."},  // 统一模型对账
  "token_usage": {...},                 // 网关记账差值（无记账时不写键）
  "agent_version": "0.7.5"
}
```

## 6. 开放问题（冻结前必须定）

- [ ] `raw` 大文件（SQLite 全量）进 evidence 目录还是只留哈希+路径（gitignore 策略已排除，但网盘归档口径要定）——E+W2 定
- [ ] 故障定位四态的推导规则附录 A——C W2 冻结
- [ ] auditd 兜底采集的 Action 映射粒度（能否还原 tool 语义还是只有 syscall 级）——W2 实测
- [ ] 金标准/诱饵质检任务的存放格式（建议复用 probe+verdict 结构，加 `quality_check` 标记）——C W2 定
