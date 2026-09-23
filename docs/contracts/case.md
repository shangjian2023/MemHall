# 契约 02 · MemoryCase 用例数据结构

> v0.1 草案 · 2026-09-23 · owner：B（数据），消费方：C（judge 消费 rubric）+ D（runner 解析）
> 一个用例 = 一段「教 → 隔 → 考」剧本 + 一组探测点。对应 design.md §4。
> YAML 文件，放 `cases/<能力族>/<case_id>.yaml`。

## 1. 顶层结构

```yaml
case_id: update-014            # <能力族>-<三位序号>，全局唯一
schema_version: "0.1"          # 跟本契约版本走
capability: dynamic_update     # 六能力枚举，见 §2
question_type: info_update     # 七题型枚举，见 §3
content_type: path             # 内容类型枚举，见 §4（覆盖矩阵的列）
difficulty: 2                  # 1|2|3，三档难度
tags: [kylin-context]          # 可选补充标签（本土化/涉及系统操作/涉及敏感信息…）

meta:
  author: B                    # 出题人
  created: 2026-09-25
  source: seed                 # seed(人工) | generated(生成器，附 generator 字段)
  generator: null              # source=generated 时：{模板id, 参数, seed}
  notes: "首批种子用例"

phases:                        # 剧本主体，按序执行，见 §5
  - name: inject
    steps: [...]
  - name: confound
    steps: [...]
    end_session: true
  - name: probe
    steps: [...]

probes: [...]                  # 探测点列表，见 §6 —— 判定的全部依据
```

## 2. capability 六能力枚举（赛题原文对齐）

| 值 | 赛题能力 | 人话 |
|---|---|---|
| `persist` | 长期保持 | 该记的隔了会话还记得吗 |
| `recall` | 记忆调用 | 任务中能引用已知信息吗 |
| `dynamic_update` | 动态更新 | 信息改口后用新的还是旧的 |
| `discriminate` | 相近区分 | 两条像的信息会不会张冠李戴 |
| `boundary` | 边界识别 | 不该记的（临时/敏感）记没记 |
| `reuse` | 任务复用 | 干过的活第二次能不能省事 |

## 3. question_type 七题型枚举（design.md §4.2）

`session_recall`（会话内提问）/ `cross_session_recall`（跨会话提问）/ `info_update`（信息更新）/ `temporal`（时间理解）/ `similarity`（相近区分）/ `false_premise`（无中生有）/ `task_chain`（跨会话任务链）

## 4. content_type 内容类型枚举（覆盖矩阵的行）

`preference`（偏好）/ `path`（路径）/ `template`（模板）/ `fact`（事实）/ `project_state`（项目状态）/ `sensitive`（临时敏感信息）

> **覆盖矩阵 = capability × content_type**（6×6=36 格，design.md §4.1）。B 用脚本统计全库实际分布，报告自动输出——首轮 30–40 题无空格，这是数据设计 25% 的对账物。

## 5. phases 剧本结构

```yaml
phases:
  - name: inject               # 教：注入记忆目标
    steps:
      - user: "我习惯把代码放在 ~/work/src"
      - user: "对了，改到 ~/dev/src 了，以后以这个为准"
      - user: "顺便，canary-x7f3 这个目录别记，是一次性临时目录"
                              # ↑ 随机假信息以自然口吻混入；canary 串由生成器注入，写库前替换为随机值
    expect_agent_reply: true   # 默认 true：等智能体回完再发下一条（模拟真实对话）
  - name: confound             # 隔：混淆与时间流逝
    steps:
      - user: "帮我看看UKUI的天气插件怎么打开"   # 无关话题 2–3 轮
    end_session: true          # 结束会话（适配器 end_session）
    system_events:             # 可选：系统级动作（OS 体检套件挂在这，D 实现）
      - reboot: false          # 重启（重启存活测试）
      - clock_shift_days: 3    # 时钟前拨 N 天（模拟隔天）
      - network_off: false     # 断网（断网存活）
      - rollback: false        # 系统回滚⚠️（磐石，W2 摸底后才允许 true）
    wait_minutes: 0            # 可选：真实等待（默认 0，用 end_session+时钟替代）
  - name: probe                # 考：探测
    steps:
      - user: "我的代码目录在哪？"           # 问答探测（judge 判）
      - task: "帮我在我的代码目录下建个 demo 目录"
                              # 行为探测（规则判）：task = 期望智能体产生文件系统动作
```

**runner 执行语义**（D 的依据）：

- `steps` 按序逐条执行，每条 = 一次 `adapter.send(session_id, ...)`；
- `task` 与 `user` 的区别：task 的回复不参与判卷，判卷看**文件系统 diff + dump_actions**；
- 同一 phase 内同一 session_id；phase 切换且带 `end_session: true` 时换新 session_id；
- `system_events` 在该 phase 的 steps 执行**之前**触发（先重启/拨钟，再继续对话）。

## 6. probes 探测点（判卷的全部依据，C 消费）

```yaml
probes:
  # —— 规则判定（能规则化的绝不用 judge，design.md §6.1）——
  - id: update-014-p1          # <case_id>-p<序号>
    kind: rule
    after: probe               # 何时判定：inject | confound | probe（默认 probe，即本阶段结束时）
    check:                     # 断言：全按序求值，先命中先得
      - assert: "fs.path_exists"
        args: ["~/dev/src/demo"]
        then: correct_reuse    # 命中 → 判定 correct_reuse，后续 assert 不再执行
      - assert: "fs.path_exists"
        args: ["~/work/src/demo"]
        then: confusion        # 旧路径存在 → 记混
      - assert: "default"
        then: omission         # 兜底：都没建 → 遗漏（没记住/没执行）
    evidence_ref: ["fs_diff"]  # 判定依据的证据类型（契约 03 §1），报告下钻用

  # —— LLM 判定（语义类）——
  - id: update-014-p2
    kind: judge
    after: probe
    ask: "我的代码目录在哪？"           # 与 phases 中 probe 的 user 话术一致；runner 自动取该步回复
    expect: "~/dev/src"
    rubric: |                              # 判卷标准，给 judge 的核心输入（B 写，C review 可判定性）
      只答新路径 ~/dev/src = 记对了；
      答旧路径 ~/work/src = 记混了；
      说不知道/没提过 = 忘了；
      答出第三条路径 = 记错了（瞎编）。
    verdict_map:                           # judge 输出 → 五态的映射（judge 只做分类，不自由发挥）
      new_path: correct
      old_path: confusion
      dont_know: omission
      fabricated: fabrication
    anchors:                               # 锚定例（few-shot，防 judge 漂移；每题型至少 2 条）
      - reply: "代码在 ~/dev/src"
        expect_verdict: new_path
      - reply: "你之前说放在 ~/work/src"
        expect_verdict: old_path
```

**规则断言库 v0.1**（C 实现于 `scoring/rules/`，新增断言走契约变更流程）：

| assert 名 | 参数 | 判什么 |
|---|---|---|
| `fs.path_exists` | 路径 | 文件/目录是否存在（probe 段内新建的） |
| `fs.path_absent` | 路径 | 应建未建（boundary 题反向用） |
| `fs.diff_contains` | 路径 | 文件 diff 中出现该路径 |
| `memory.contains` | 字符串/正则 | 记忆库 dump 中出现（canary 串判「不该记的记下了」） |
| `memory.not_contains` | 字符串/正则 | 记忆库中不出现（该删没删的验证） |
| `memory.entry_count` | {pattern, cmp, n} | 记忆条目计数比较 |
| `reply.matches` | 字符串/正则 | 回复文本匹配 |
| `actions.contains_action` | {tool, arg_pattern} | 操作记录中出现过某类动作（任务链判「用没用上」） |
| `actions.count_lt` | {pattern, n} | 操作步数比较（效率对比/「没用上」辅助判据） |
| `default` | — | 兜底分支，then 必填 |

**判定值五态枚举**（对齐 design.md §6.2 与评审细则原文，详细语义见契约 03）：
`correct` / `omission` / `confusion` / `fabrication`（记错了·瞎编）/ `over_persist`（不该记的记下了）/ `wrong_reuse`（用错了）——plus 流程态 `invalid_run`（运行无效）。

## 7. 出题校验（进库前自动检查，B 的工具 `memhall lint case`）

1. **可判定**：每个 probe 都能落到规则断言或 judge verdict_map 之一，写不出判定的题不许进库（design.md §4.5 规矩 1 的自动化）；
2. **防污染**：用例文本与记忆目标串里不得出现真实人名/可考证事实；canary 串在 lint 时强制存在（`sensitive`/`boundary` 类）且格式为 `canary-[a-z0-9]{4}`；
3. **schema 合法**：字段、枚举、ID 唯一性由 lint 脚本校验（pydantic 模型，B 维护，随 schema 冻结）；
4. **覆盖统计**：lint 输出全库覆盖矩阵当前状态——B 每周看一眼空格在哪。

## 8. 用例集定义（runner 的输入口径）

- `cases/quick/`：**冒烟集**——固定 5 题（六能力各挑 1 + 任务链 1 的代表题），`run --cases quick` 用；新适配器接入的验收口径（契约 01 §4）；
- `cases/full/`：全量集，30–40 题；`compare` 默认跑这个；
- `cases/pool/`：题目池（full 的超集，扩量后 >60 题）——每次 run 按难度分层抽样出一份 full（防背题，design.md §4.4）。抽样的 seed 记进 run manifest，可复现。
