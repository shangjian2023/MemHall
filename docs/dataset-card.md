# 数据集说明卡（Dataset Card）· MemHall 用例库 v0.1

> owner：B · 更新：2026-10-05（R46 对账重写：全部数字以 lint 输出为准；gen/heldout 重生成）
> 说明卡随题库发布，数据设计的每个主张都能在此对账（design.md §4.6）。
> **2026-10-04 口径 v2**（docs/review-tasks.md）：探测点分 score/diagnostic 两层——存储态断言（memory.*）与 actions 断言只进故障定位不进六维，跨族 canary 不计入宿主族；canary 一律教学时点判（after:inject）。旧 run 可用 `memhall report` 按新口径重渲染（快照优先，role 按断言推断对旧快照同样适用）。

## 1. 数据集构成

| 项 | 值 |
|---|---|
| 种子用例（人工） | **45 道**（cases/full/），覆盖 6 能力 × 6 内容 = 36 格覆盖矩阵，无空格（lint 实测）；含 2 道大容量注入题（persist-008 / recall-008：一次教 16 条互不相关事实再考 5 条，检索竞争） |
| 团队生成用例（模板扩量） | **21 道**（cases/gen/，scripts/gen_cases.py，seed=20260928；2026-10-05 重生成：值池随机化去 gen↔heldout 撞题、同族 subject 去重、boundary 补存储级探测、reuse 补复用任务+fs 断言，见 §3/§8） |
| 任务链 | **4 条**（cases/chains/chain-001~003 每条 3 会话；chain-004 六会话长弧，对齐 LongMemEval/LoCoMo 的长程会话深度） |
| 冒烟集 | **6 道**（虚拟集，无独立目录：full 中六能力各 1 题按 ID 引用，`paths.QUICK_IDS` 单源），适配器接入验收口径（随 full 更新，不再维护副本） |
| **held-out 防背题池（不可见）** | **21 道**（评测时现场生成：`scripts/gen_cases.py --seed 4210 --out cases/heldout --prefix h`，题目文本不入公开仓库；lint 已支持 `-hNN` ID 与子集模式（R41）。威胁模型与脱敏策略见 §8） |
| 生成器备用池（B 本地） | 72 道（seed=42，B 的 generators/generate_cases.py），与 gen/heldout 功能重叠，未并入 PR |

## 2. 覆盖矩阵（cases/full/ 实测，lint 自动统计，45 道，2026-10-05）

```
capability      | preference | path | template | fact | project_state | sensitive
persist         |     1      |  2   |    1     |  2   |       1       |    1
recall          |     1      |  2   |    1     |  3   |       1       |    1
dynamic_update  |     1      |  3   |    1     |  1   |       1       |    1
discriminate    |     1      |  1   |    1     |  1   |       1       |    1
boundary        |     1      |  1   |    1     |  1   |       1       |    3
reuse           |     1      |  1   |    1     |  1   |       1       |    1
```

（大容量注入题 persist-008/recall-008 并入后 fact 列 +2；数字随 lint 输出为准）

重点格（评审最关注的动态更新、边界识别）每格 2–3 题；sensitive 是边界识别的主场，boundary×sensitive 3 题（含两档契约）。
合并前团队 14 道种子只覆盖 9/36 格（27 空格）——本 PR 的 29 道补充题正是补空格、把矩阵做满；canary 用例从 2 → **13**。

## 3. 构造方式与出题四规矩（design.md §4.5）

| 规矩 | 落实方式 |
|---|---|
| 可判定 | 每题 probes 全部可落为规则断言（fs/memory/actions）或 judge rubric+verdict_map；lint 强制：judge rubric 非空、verdict_map 值合法、锚定例 ≥2、rule 必须含 default 兜底 |
| 防污染 | sensitive/boundary 类必须含 `canary-[a-z0-9]{4}` 假信息串（lint 强制，full 集 13 个）；路径/代号/版本号全部虚构半随机（~/proj/fjord、calypso-x3、3.1.2）；canary 只在 inject 段出现，probe 段提问禁止泄底（lint 检查） |
| 像人话 | 注入一律用用户口吻（"我习惯…""帮我记住…""对了，改成…"），探测按日常对话问，不用"请记住路径 X"式指令 |
| 本土化 | 场景扎根 openKylin 桌面语境：UKUI 天气插件/夜间模式/应用商店、麒麟软件源、~/文稿/~/图片/~/模板 路径习惯、WPS/火狐 |

## 4. 题型分布（45 道种子，lint 实测 2026-10-05）

- session_recall 6 / cross_session_recall 12 / info_update 7 / temporal 2 / similarity 6 / false_premise 6 / task_chain 6
- 任务链 6 道（design §4.3 要求 3–5 条，略超），每条 3 会话，判"用对了/用错了/没用上"
- 一致性检查变换（换说法/打乱顺序）作为生成器后续扩展项，W3 补

## 5. 难度分布与旋钮

难度 1:13 / 2:24 / 3:8（lint 实测 2026-10-05）。difficulty 为**人工综合标定**（族级标称值），
与三旋钮组合解耦——生成器侧已注明（R44），不承诺"同难度同旋钮组合"：
- **干扰信息多少**：confound 段 filler 轮数实测 d1 中位 1（1 轮 9 题、0 轮 3 题）、d2 1–2 轮（21+3 题）、d3 2–3 轮——不是严格 d=k 递增
- **间隔多久**：拨钟题实为 4 道（temporal-001 +3d、temporal-002 +2d、update-007 +3d、discriminate-005 +3d），其余隔会话靠 end_session
- **相似程度**：d2=两相似项，d3=三相似项/更接近的干扰（api vs api-v2、3.1.2 vs 3.2.1、8080 vs 8081）
- **旋钮流与取值流已分离（R40）**：`Random(seed:family:k)` 每题派生——同 seed 改旋钮=同题仅难度变（对照实验归因成立；此前会整题漂移）

## 6. 判定口径（对齐 C 实测，W1/W2 结论）

- **update 族与 temporal 族答旧值 → wrong_reuse**（C 实测 §8 推荐口径，2026-10-05 R45 裁决统一：temporal-002 及生成器均已映射 wrong_reuse——时间理解失败与旧值复用在"答旧值"行为上不可分，统一口径防 stale_info_rate 归因污染。团队审计版 update-001 仍用 confusion，两版并存，待 C/A 统一）
- **boundary 两档契约**（boundary-003，对齐实测）：严格=无时效标注入库即 over_persist（rule 判）；宽松=回答带时效限定可接受降级（judge rubric 识别）
- **该记的敏感信息 vs 不该记的**分开判：persist-007（收货地址该记 + canary 假地址别记）双 probe
- 五态判定值：correct / omission / confusion / fabrication / over_persist / wrong_reuse（契约 02 §6）

## 7. 校准数据（初步，W3 扩）

- C 实测首跑（KylinBot 0.7.5）：retention 100 / recall 0 / update 0 / boundary 0 —— 佐证 recall/update/boundary 类题区分度天然高
- 待 W3：在 KylinBot + Hermes 上跑全量 43 题（合并后），统计每题通过率，全对/全错题调难度或标注为上下限参照题（design §4.4 实测校准）
- 变换保难度（一致性检查）用试点数据验证前后通过率无系统差异（W3）

## 8. 已知局限（诚实边界）

- **boundary 维的证据面差异（R48）**：三家记忆导出面不同构——openclaw 导出 memory_index_chunks 全量（含会话转录，说过的 canary 必然在库）、hermes 只导出两个 md、kylinbot 导出结构化库。boundary 主判据是**行为级**（probe 段问答拒答/泄露），存储级 canary 断言已降 diagnostic 分层呈现（R07/R49），跨架构比较时报告注明各家检索面差异
- **fs 证据的阶段粒度（R50）**：fs_diff 条目已带 stage（inject/probe 两个阶段窗），但快照只有路径清单——modified 与内容哈希待适配器提供内容指纹后落地；"按习惯写"类内容级断言暂不可判
- **生成用例构念（R44）**：gen/heldout 的 boundary 补了存储级 over_persist 探测（diagnostic）、reuse 补了复用任务+fs 断言（score），与 full 集行为验收的可比性提升但难度仍不可比（difficulty 为标称值）；heldout reuse 维与 full 不可比项已在报告层注明
- **适配器能力面（R36/R37）**：opencode 沙箱 bash=deny（与其他适配器对齐，2026-10-05 起）；openclaw 沙箱模型参数（contextWindow/maxTokens）可经环境变量外置，默认值为评测方设定而非被测者原生配置——适配器替被测者做的配置选择进入分数，此差异在此披露
- **heldout 脱敏与公开策略（R54）**：seed+生成器公开 = 题目可重构（防训练污染有效、防定向重构无效，业界方向是组织方私有测试集）；runs/ 目录含用例全文快照，公开演示材料不得展开 runs 内容，heldout run 对外发布前须脱敏（probe/expect 文本哈希化）；中期方向：paraphrase 槽位轮换 + seed 延迟公开
- **统计功效（R55）**：定量功效声明已补（§9⑤，scripts/stats_uncertainty.py 可复现）；报告层已对 n_valid<5 的维标注"不具区分力"（R51）；gen 21 题**已裁决不并入**（2026-10-08：47 题基线冻结，并入需 6 次马拉松重跑且 gen 难度与 full 不可比——赛后并入 v2 数据集）
- **判卷未决（R55）**：未决率是判卷质量指标不是分母口径——业界 judge 用 forced choice 不弃权，dual judge 落地后未决已降至每轮 0–2；"问两遍"以跨轮同探测点一致率形态落地（§9①），会话内复问待会话型适配器

- 语言：仅中文；场景：桌面办公/开发场景，未覆盖多语言与专业领域
- **recall/persist 的机制边界**：真智能体适配器每条消息独立进程（无会话上下文），"会话内提问"实测等价于"写库后立刻检索"；两维按可测口径收窄（recall=写后即取、persist=跨干扰保持，见 design §4.2 注记），接真会话型适配器后语义恢复
- **held-out 威胁模型**：seed 公开 + 生成器公开 = 题目可完整重构——防训练污染有效、防定向作弊无效；held-out 与 gen 同模板同槽位池，防"背题库"不防"背题型"。后续方向：held-out 加 paraphrase 变换 + 换槽位词表
- **verdict_map 键名词表未统一**（lint advisory 统计）：correct 类现有 33 种写法——judge 只做分类不受影响，但锚例/诱饵维护成本随词表膨胀；新题按契约 02 §6 标准词表（mixed_up/dont_know/made_up/leaked…）出
- 生成扩量以团队 scripts/gen_cases.py 为准（gen=公开集 seed=20260928，heldout=不可见集 seed=4210，`--prefix h` 隔离 ID）；B 的 generators/generate_cases.py（pool，seed=42）为备用素材，未并入 PR
- **三难度旋钮**（design §4.4，2026-10-02 参数化）：`--distract N` 干扰密度（confound 闲聊条数）、`--gap-days N` 拨钟间隔天数、`--similar high|mid|low` 诱饵相似度；同 seed 改旋钮 = 仅难度不同的对照变体；默认参数与历史存档逐字一致（核心闲聊池 5 条不动，扩展池仅在 N≥2 时并入）
- **方差口径**：正式全量跑每智能体 ≥2 轮，报告六维与总分的 mean±std（样本标准差，`memhall aggregate`）+ 逐探测点 bootstrap 95% CI；n=2 时 mean±std 支撑不了排名叙事，跨智能体比较看 CI 重叠与 compare 的符号检验 p 值，单轮裸分数不作对外口径
- **mock 基线（口径 v2，2026-10-04 实测）**：full 66.1%（62/66 计分探测有效）、chains 22.2%；口径 v1 分别为 62.2%/41.7%——差异来自诊断探测出分母与 canary 教学时点判，属口径变更非行为变更
- 任务链的"步数/耗时对比"（记忆效率指标）需 runner 支持，probe 侧已预留 actions 计数断言（actions 证据面 coverage=full 前 role=diagnostic 不进分）
- 难度标定数据量有限，结论标注"初步标定"
- 待统一项：update-001 的旧值判定（confusion vs wrong_reuse），由 C/A 裁决后收敛


## 9. 功效与不确定度（测量口径，2026-10-08 实测）

复现命令：`uv run python scripts/stats_uncertainty.py`（零 LLM 调用，读六轮 dual 重判 verdicts）。

**① 系统内稳定性（同一探测点两轮一致率）**——被测系统的随机性是属性不是噪声：
hermes 六态一致 47.2%（44% 的题两轮对错翻转）、kylinbot 73.0%（23% 翻转）、openclaw 85.4%（13% 翻转）。

**② 排名主张（两轮合并的探测点配对符号检验）**：hermes vs kylinbot p=0.002（显著偏 kylinbot）、
hermes vs openclaw p=0.027（显著偏 openclaw）、kylinbot vs openclaw 38:38 p=1.0
（**不显著**——均值差 0.6 分不构成名次差）。

**③ 维度区分度（组间/组内方差比，描述性）**：n=2 下仅边界识别（3.0）与 temporal（6.8）比值 >1，
其余维度轮间方差吞掉智能体间差距——维度级比较需更多轮或更大题量；雷达图已加构念注脚（R53）。

**④ 判卷口径差（同证据 dual vs scripted）**：-0.8 ～ -19.0 分且方向一致（dual 更严）——
仪器方差上界，数字必须带判卷口径出行。

**⑤ 功效声明**：轮级总分口径下，hermes 对另两家的 ~11 分差距需 ≈60–68 轮（α=0.05、power 80%）
才能分开；**配对探测点检验（②）用现有 n=2 已可分开**——这是本基准采用配对设计的直接理由；
kylinbot vs openclaw 的 0.6 分差距在合理轮数内不可分，榜单将其并列呈现。
