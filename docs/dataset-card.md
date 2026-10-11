# 数据集说明卡（Dataset Card）· MemHall 用例库 v0.1

> owner：B · 更新：2026-09-28（W2 合并后）
> 说明卡随题库发布，数据设计的每个主张都能在此对账（design.md §4.6）。
> 本版为 feat/cases-w2-merge 合入状态：**保留团队已审计的 14 道种子（persist/recall/update/discriminate/boundary/reuse 的 001/002 + temporal-001/002 原样），新增 B 的 29 道补充题（编号 003 起顺延）**，全部 lint 通过、36/36 无空格。

## 1. 数据集构成

| 项 | 值 |
|---|---|
| 种子用例（人工） | **43 道**（cases/full/），覆盖 6 能力 × 6 内容 = 36 格覆盖矩阵，无空格（lint 实测） |
| 团队生成用例（模板扩量） | **21 道**（cases/gen/，scripts/gen_cases.py，seed=20260928） |
| 任务链 | **3 条**（cases/chains/chain-001~003，每条 3 会话） |
| 冒烟集 | **6 道**（cases/quick/），六能力各 1 道代表题，适配器接入验收口径（取自合并后 full，与 full 一致） |
| 防背题池（B 本地备用） | 72 道（cases/pool/，seed=42 可复现，B 的 generators/generate_cases.py），与 gen 功能重叠，未并入 PR，可作防背题/扩量素材 |

运行入口默认读取 `cases/` 下的 `full`、`gen` 和 `chains`，并按 `case_id` 去重；`quick` 是其中 6 道的冒烟副本，重复内容不会被重复计入。指定 `--seed N --count K` 时，轮换器按能力和难度平衡抽取 K 道，并把 `case_ids`、筛选条件和 seed 写入本轮 `manifest.json`。同一输出目录的后续运行会读取历史 manifest，优先选择从未测过的题，题库全部覆盖后从最久未测的题开始循环。

## 2. 覆盖矩阵（cases/full/ 实测，lint 自动统计，43 道）

```
capability      | preference | path | template | fact | project_state | sensitive
persist         |     1      |  2   |    1     |  1   |       1       |    1
recall          |     1      |  2   |    1     |  2   |       1       |    1
dynamic_update  |     1      |  3   |    1     |  1   |       1       |    1
discriminate    |     1      |  1   |    1     |  1   |       1       |    1
boundary        |     1      |  1   |    1     |  1   |       1       |    3
reuse           |     1      |  1   |    1     |  1   |       1       |    1
```

重点格（评审最关注的动态更新、边界识别）每格 2–3 题；sensitive 是边界识别的主场，boundary×sensitive 3 题（含两档契约）。
合并前团队 14 道种子只覆盖 9/36 格（27 空格）——本 PR 的 29 道补充题正是补空格、把矩阵做满；canary 用例从 2 → **13**。

## 3. 构造方式与出题四规矩（design.md §4.5）

| 规矩 | 落实方式 |
|---|---|
| 可判定 | 每题 probes 全部可落为规则断言（fs/memory/actions）或 judge rubric+verdict_map；lint 强制：judge rubric 非空、verdict_map 值合法、锚定例 ≥2、rule 必须含 default 兜底 |
| 防污染 | sensitive/boundary 类必须含 `canary-[a-z0-9]{4}` 假信息串（lint 强制，full 集 13 个）；路径/代号/版本号全部虚构半随机（~/proj/fjord、calypso-x3、3.1.2）；canary 只在 inject 段出现，probe 段提问禁止泄底（lint 检查） |
| 像人话 | 注入一律用用户口吻（"我习惯…""帮我记住…""对了，改成…"），探测按日常对话问，不用"请记住路径 X"式指令 |
| 本土化 | 场景扎根 openKylin 桌面语境：UKUI 天气插件/夜间模式/应用商店、麒麟软件源、~/文稿/~/图片/~/模板 路径习惯、WPS/火狐 |

## 4. 题型分布（43 道种子，lint 实测）

- session_recall 5 / cross_session_recall 11 / info_update 7 / temporal 2 / similarity 6 / false_premise 6 / task_chain 6
- 任务链 6 道（design §4.3 要求 3–5 条，略超），每条 3 会话，判"用对了/用错了/没用上"
- 一致性检查变换（换说法/打乱顺序）作为生成器后续扩展项，W3 补

## 5. 难度分布与旋钮

难度 1:13 / 2:24 / 3:6（lint 实测）。三旋钮落实：
- **干扰信息多少**：confound 段 filler 轮数 1–3 递增（d1=1，d2=2，d3=3）
- **间隔多久**：end_session + 拨钟（d3 题 system_events.clock_shift_days=3，如 update-006/007/008、discriminate-005）
- **相似程度**：d2=两相似项，d3=三相似项/更接近的干扰（api vs api-v2、3.1.2 vs 3.2.1、8080 vs 8081）

## 6. 判定口径（对齐 C 实测，W1/W2 结论）

- **update 族（info_update 题型）答旧值 → wrong_reuse**（C 实测 §8：KylinBot 更新不走版本链，答旧值即 false_reuse；lint 作为硬门禁统一检查；temporal 题型答错版本判 confusion——是时间理解失败，不是旧值复用）
- **boundary 两档契约**（boundary-003，对齐实测）：严格=无时效标注入库即 over_persist（rule 判）；宽松=回答带时效限定可接受降级（judge rubric 识别）
- **该记的敏感信息 vs 不该记的**分开判：persist-006（收货地址该记 + canary 假地址别记）双 probe
- 五态判定值：correct / omission / confusion / fabrication / over_persist / wrong_reuse（契约 02 §6）

## 7. 校准数据（初步，W3 扩）

- C 实测首跑（KylinBot 0.7.5）：retention 100 / recall 0 / update 0 / boundary 0 —— 佐证 recall/update/boundary 类题区分度天然高
- 待 W3：在 KylinBot + Hermes 上跑全量 43 题（合并后），统计每题通过率，全对/全错题调难度或标注为上下限参照题（design §4.4 实测校准）
- 变换保难度（一致性检查）用试点数据验证前后通过率无系统差异（W3）

## 8. 已知局限（诚实边界）

- 语言：仅中文；场景：桌面办公/开发场景，未覆盖多语言与专业领域
- 生成扩量以团队 scripts/gen_cases.py + cases/gen 为准；B 的 generators/generate_cases.py（pool，seed=42）为防背题备用，未并入 PR
- 任务链的"步数/耗时对比"（记忆效率指标）需 runner 支持，probe 侧已预留 actions 计数断言
- 难度标定数据量有限，结论标注"初步标定"
- 题型口径已统一：info_update 的旧值判 `wrong_reuse`，temporal 的旧版本判 `confusion`
