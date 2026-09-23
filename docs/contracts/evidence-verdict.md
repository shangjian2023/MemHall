# 契约 03 · Evidence 证据 + Verdict 判定数据结构

> v0.1 草案 · 2026-09-23 · owner：C（评分），消费方：B（probe 判定语义引用）+ A（适配器产出证据对象）
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
| `fs_diff` | 阶段前后文件树 diff（新建/修改/删除清单） | runner 文件快照对比 |

**采集时机（固定，D 实现）**：每 phase 结束时采一次全量四类——共 3 次/case；`memory_snapshot` 额外在 inject 后立即加采一次（看「边聊边记还是聊完才记」，写入时机测试的数据源）。

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
  }
}
```

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
  "decided_by": "rule",                // rule | judge_a | judge_b | arbitration | human_review
  "evidence_refs": ["ev-000120", "ev-000123"],   // 判定依据的证据 ID 列表——报告下钻入口
  "explanation": "回复精确匹配新路径 ~/dev/src",   // 人话解释（rule：模板生成；judge：原判定理由）
  "judge_meta": {                      // decided_by ∈ judge/arbitration 时必填
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

### 3.2 判定优先级（谁说了算，C 实现）

```
规则判定（confidence=1.0）
  └─ 规则能出结论 → 直接采信
judge 判定（rubric + anchors + verdict_map，双判）
  ├─ A、B 一致 → 采信，decided_by=judge_a
  ├─ A、B 不一致 → 规则仲裁（能用规则裁的用规则，decided_by=arbitration）
  │                 └─ 裁不了 → human_review 队列（人工复核率指标的分母来源，目标 <5%）
```

## 4. 指标汇聚约定（report 层的输入，C 定义）

- 能力分：`capability` 维度上 verdict=correct 的 probe 占比，**pass^k 口径**（重复运行 k 次每次都对才算过，k=2，design.md §8）；
- 错误构成：omission/confusion/fabrication/over_persist/wrong_reuse 各占比（雷达图旁的第二张图）；
- 判卷质量四件套：人工复核率、双判一致率、金标准符合率、诱饵拒绝率——从 verdicts + 质检任务数据算；
- 故障定位四态（没存/存了没用上/存错了/该删没删）：由 memory_snapshot×dialogue/actions 交叉推导，附 evidence_refs，写进深化报告——**推导规则 C 在 W2 冻结为附录 A，本契约预留**。

## 5. Run manifest（一次运行的总账单，D 产出）

`evidence/<run_id>/manifest.json`：

```json
{
  "run_id": "r-20260927-kylinbot-8f3a",
  "started_at": "...", "finished_at": "...",
  "agent": "kylinbot",
  "cases_version": "git:abc1234",       // 题库版本
  "case_sample_seed": 42,               // 题目池抽样 seed（可复现同一份题）
  "code_version": "git:def5678",
  "judge": {"models": ["deepseek-v4", "qwen-max"], "prompt_version": "judge-v1.2"},
  "env": {"os": "openKylin 3.0 (20260905)", "vm_snapshot": "clean-baseline", "python": "3.11.9"},
  "cost": {"judge_tokens": {...}, "agent_tokens": {...}},   // 记账，W2 对接 Token 中心⚠️
  "repeat_of": null                     // 重复运行时填原 run_id（run diff 的配对依据）
}
```

## 6. 开放问题（冻结前必须定）

- [ ] `raw` 大文件（SQLite 全量）进 evidence 目录还是只留哈希+路径（gitignore 策略已排除，但网盘归档口径要定）——E+W2 定
- [ ] 故障定位四态的推导规则附录 A——C W2 冻结
- [ ] auditd 兜底采集的 Action 映射粒度（能否还原 tool 语义还是只有 syscall 级）——W2 实测
- [ ] 金标准/诱饵质检任务的存放格式（建议复用 probe+verdict 结构，加 `quality_check` 标记）——C W2 定
