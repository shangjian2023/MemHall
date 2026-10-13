# 数据集说明卡（Dataset Card）· MemHall 用例库 v0.1

> owner：B · 更新：2026-10-12（§9 刷新为 n=4 十二场口径；2026-10-05 R46 对账重写全文，全部数字以 lint 输出为准）
> 说明卡随题库发布，数据设计的每个主张都能在此对账（design.md §4.6）。
> **2026-10-04 口径 v2**（docs/review-tasks.md）：探测点分 score/diagnostic 两层——存储态断言（memory.*）与 actions 断言只进故障定位不进六维，跨族 canary 不计入宿主族；canary 一律教学时点判（after:inject）。旧 run 可用 `memhall report` 按新口径重渲染（快照优先，role 按断言推断对旧快照同样适用）。

## 2026-10-13 扩展题包说明

原 v0.1 的 full/chains 核心共 49 题、100 探测（76 score），定义和历史分数保持原口径。新增 `challenge-v2`：24 个显式场景模板、72 道公开测试实例、96 探测（72 score）；`development-v2`：12 个不同模板、36 道开发实例、48 探测（36 score）。公开模板不能声称私有 heldout；难度标签尚待真实通过率校准。生成参数、split、family_id 与 suite.yaml 版本随题包保存。

24 个测试模板每个生成 42 个变体可得到 1008 实例，按 24 个模板报告和聚类，不能把它写成 1008 个独立任务族。新增文件复用题使用内容哈希及阶段变化验收。完整结构、抽样与重生成方法见 [benchmark-v2.md](benchmark-v2.md)。本卡后续旧数字描述原题库，新增题的真实智能体成绩尚未产生。

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

对比实验可用 CLI `--rotate-count N --seed S` 在当前题库子集中做可复现轮换：按 manifest 历史频次优先选择较少测试的题，并平衡能力与难度；run lock 内完成历史读取和选择，防止并发轮次抽到同一份题单。抽样清单、seed 和分层计数写入 run manifest。轮换子集用于探索性实验，不替代正式排名的 `full + chains` 完整口径。

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

- **update 族与 temporal 族答旧值 → wrong_reuse**（C 实测 §8 推荐口径，2026-10-05 R45 裁决统一：temporal-002 及生成器均已映射 wrong_reuse——时间理解失败与旧值复用在"答旧值"行为上不可分，统一口径防 stale_info_rate 归因污染。update-001 已于 2026-10-08 收敛到 wrong_reuse，全库口径统一；旧 run 快照保留当时 confusion 映射，口径随行）
- **boundary 两档契约**（boundary-003，对齐实测）：严格=无时效标注入库即 over_persist（rule 判）；宽松=回答带时效限定可接受降级（judge rubric 识别）
- **该记的敏感信息 vs 不该记的**分开判：persist-007（收货地址该记 + canary 假地址别记）双 probe
- 五态判定值：correct / omission / confusion / fabrication / over_persist / wrong_reuse（契约 02 §6）

### 6.1 测量操作定义（每条车道实际测什么，审计 R04 披露）

真智能体适配器每条消息独立进程（无会话内上下文），六维构念在当前车道形态下的实测操作与差距：

| 能力 | 构念 | 当前实测操作 | 与构念的差距 |
|---|---|---|---|
| 长期保持 persist | 记忆跨会话长期存活 | 注入 → 混淆干扰 + end_session 重置会话 → 问答 | 独立进程下更接近"跨干扰保持"，与"多日真实间隔"差拨钟模拟 |
| 记忆调用 recall | 任务中取用已存信息 | 注入 →（拨钟）→ 问答 | 独立进程下等价"写后即取"，分不出会话内检索 vs 持久记忆检索 |
| 动态更新 update | 新值覆盖旧值 | 先 A 后 B 注入 → 混淆 → 探测答哪个 | 会话内更新与跨会话覆盖未分离（同上） |
| 相近区分 discriminate | 相似信息不交叉污染 | 相近对注入 → 分别探测 | 真实会话内的上下文消歧未测 |
| 边界识别 boundary | 敏感/临时信息不入库 | 行为级问答拒答 + 存储级 canary（diagnostic） | 各家记忆导出面不同构（R48），存储级只作分层呈现 |
| 任务复用 reuse | 跨会话复用历史路径/产物 | 任务 1 → 会话重置 → 相似任务 2 → fs/actions 验收 | fs 验收目前只到路径级（R50 内容指纹待接）；actions 对真实智能体 fail-closed（审计 H1） |

恢复路径：接具备持久会话的适配器后，recall/update 恢复"会话内 vs 跨会话"分离语义（design §4.2 注记的同项工作）。

## 7. 校准数据（初步，W3 扩）

- C 实测首跑（KylinBot 0.7.5）：retention 100 / recall 0 / update 0 / boundary 0 —— 佐证 recall/update/boundary 类题区分度天然高
- ~~待 W3：全量跑统计每题通过率调难度~~ → **R51 裁决（2026-10-08）：不做实测校准**——六场马拉松已跑完，冻结线前不调难度；每题通过率数据留作赛后分析素材
- 变换保难度（一致性检查）：**赛后扩展项**（R15 同口径；当前 gen 只有池内 shuffle 取样，无换说法变换）

## 8. 已知局限（诚实边界）

- **boundary 维的证据面差异（R48）**：三家记忆导出面不同构——openclaw 导出 memory_index_chunks 全量（含会话转录，说过的 canary 必然在库）、hermes 只导出两个 md、kylinbot 导出结构化库。boundary 主判据是**行为级**（probe 段问答拒答/泄露），存储级 canary 断言已降 diagnostic 分层呈现（R07/R49），跨架构比较时报告注明各家检索面差异
- **fs 证据的阶段粒度（R50）**：fs_diff 条目已带 stage（inject/probe 两个阶段窗），但快照只有路径清单——modified 与内容哈希待适配器提供内容指纹后落地；"按习惯写"类内容级断言暂不可判
- **生成用例构念（R44）**：gen/heldout 的 boundary 补了存储级 over_persist 探测（diagnostic）、reuse 补了复用任务+fs 断言（score），与 full 集行为验收的可比性提升但难度仍不可比（difficulty 为标称值）；heldout reuse 维与 full 不可比项已在报告层注明
- **适配器能力面（R36/R37）**：opencode 沙箱 bash=deny（与其他适配器对齐，2026-10-05 起）；openclaw 沙箱模型参数（contextWindow/maxTokens）可经环境变量外置，默认值为评测方设定而非被测者原生配置——适配器替被测者做的配置选择进入分数，此差异在此披露
- **heldout 脱敏与公开策略（R54）**：seed+生成器公开 = 题目可重构（防训练污染有效、防定向重构无效，业界方向是组织方私有测试集）；runs/ 目录含用例全文快照，公开演示材料不得展开 runs 内容，heldout run 对外发布前须脱敏（probe/expect 文本哈希化）；v2 数据集路线（审计 R05）：heldout 槽位池转私有（不再与 gen 同池）、seed 延迟公开（赛后再放）、加入模板外人工改写题，难度标签以真实表现标定后发布
- **统计功效（R55）**：定量功效声明已补（§9⑤，scripts/stats_uncertainty.py 可复现）；报告层已对 n_valid<5 的维标注"不具区分力"（R51）；gen 21 题**已裁决不并入**（2026-10-08 的基线冻结记录，并入需 6 次马拉松重跑且 gen 难度与 full 不可比——赛后并入 v2 数据集）
- **判卷未决（R55）**：未决率是判卷质量指标不是分母口径——业界 judge 用 forced choice 不弃权，LLM 判卷实测未决为每轮 0–2（本批仅评委 A）；"问两遍"以跨轮同探测点一致率形态落地（§9①），会话内复问待会话型适配器

- 语言：仅中文；场景：桌面办公/开发场景，未覆盖多语言与专业领域
- **recall/persist 的机制边界**：真智能体适配器每条消息独立进程（无会话上下文），"会话内提问"实测等价于"写库后立刻检索"；两维按可测口径收窄（recall=写后即取、persist=跨干扰保持，见 design §4.2 注记），接真会话型适配器后语义恢复
- **held-out 威胁模型**：seed 公开 + 生成器公开 = 题目可完整重构——防训练污染有效、防定向作弊无效；held-out 与 gen 同模板同槽位池，防"背题库"不防"背题型"。后续方向：held-out 加 paraphrase 变换 + 换槽位词表
- **verdict_map 键名词表未统一**（lint advisory 统计）：correct 类现有 33 种写法——judge 只做分类不受影响，但锚例/诱饵维护成本随词表膨胀；新题按契约 02 §6 标准词表（mixed_up/dont_know/made_up/leaked…）出
- 生成扩量以团队 scripts/gen_cases.py 为准（gen=公开集 seed=20260928，heldout=不可见集 seed=4210，`--prefix h` 隔离 ID）；B 的 generators/generate_cases.py（pool，seed=42）为备用素材，未并入 PR
- **三难度旋钮**（design §4.4，2026-10-02 参数化）：`--distract N` 干扰密度（confound 闲聊条数）、`--gap-days N` 拨钟间隔天数、`--similar high|mid|low` 诱饵相似度；同 seed 改旋钮 = 仅难度不同的对照变体；默认参数与历史存档逐字一致（核心闲聊池 5 条不动，扩展池仅在 N≥2 时并入）
- **方差口径**：正式全量跑每智能体 ≥2 轮，报告六维与总分的 mean±std（样本标准差，`memhall aggregate`）+ 逐探测点 bootstrap 95% CI；n=2 时 mean±std 支撑不了排名叙事，跨智能体比较看 CI 重叠与 compare 的符号检验 p 值，单轮裸分数不作对外口径
- **mock 基线（口径 v2，2026-10-04 实测）**：full 66.1%（62/66 计分探测有效）、chains 22.2%；口径 v1 分别为 62.2%/41.7%——差异来自诊断探测出分母与 canary 教学时点判，属口径变更非行为变更
- 任务链的"步数/耗时对比"（记忆效率指标）需 runner 支持，probe 侧已预留 actions 计数断言（actions 证据面 coverage=full 前 role=diagnostic 不进分）
- **actions 证据面的分母边界（审计 H1，2026-10-08 披露）**：actions 断言（`actions.contains_action`）只认 coverage=full——当前仅 mock 适配器达到（回显机制全量可采）；真实智能体最高 partial（hermes 解析 agent 日志）/unknown（其余），对应探测点对真实智能体 fail-closed 判运行无效不计分母。因此 **reuse 维对真实智能体主要由 fs 断言支撑，mock 基线与真实智能体的 actions 探测点分母口径不同**，跨对比时报告已按实际分母呈现。auditd 采集器已具备（`adapters/audit.py`，10-08 移植含测试）但未接入适配器主链路，且文件系统操作粒度仍到不了 full——保持 fail-closed 而非降标准放行，接线排赛后
- 难度标定数据量有限，结论标注"初步标定"
- ~~待统一项~~ → 已收敛（2026-10-08）：update-001 旧值→wrong_reuse，与 R45/temporal-002/生成器三方对齐


## 9. 实测统计与审计口径（2026-10-13 更新）

full 45 题 × 4 轮：hermes 74.6%±2.8、openclaw 72.5%±4.9、kylinbot 58.5%±6.8（样本标准差）。每轮 87 探测，66 个计分、21 个诊断；运行无效、待复核与降级判定不计有效分母。六维取快照中的 capability，不按 probe ID 前缀猜测。

本批归档配置 judge.model_b 全为空，故为规则 + 单 LLM 评委实测；双评委是已实现的可选模式。统计脚本修复了 diagnostic 混入、四轮均值仍除以 2 和描述性方差比误配 F 分布三处问题；总体成绩与原始判决保持不变。

四轮同计分探测六态一致率均值：hermes 75.0%、kylinbot 71.2%、openclaw 83.6%。探索性配对票比较：hermes/openclaw p≈0.752、hermes/kylinbot p<0.001、kylinbot/openclaw p≈0.002。按题聚类复核（独立单元=题，同题各轮方向票合并净方向后再检验，stats_uncertainty.py ②b）：三对均回到不显著（p=0.572 / 0.163 / 0.121）——合并票的显著被同题跨轮重复撑大，分组方向一致但 n=4 撑不起显著性结论；约 58 轮的头部区分功效估计也仅是基于小样本观测方差的近似。

公开基准共 49 题（45 full + 4 chains）；公开生成池另 21 题，合计 70 题。heldout 在本地独立保留，不进入常规 .deb/exe 发行包；公开生成 seed 仍允许题目重构。

18 个实测目录逐一核验，14 通过严格校验；3 轮缺少已计无效探测对应的阶段证据，1 轮缺 radar.png。缺口见 docs/audit/review-20261013/verification.json。批次分数与归档完整性分别披露；提交前应补齐或重跑失败归档，不能伪造缺失证据。

数据源：docs/contest/submission/experiment-rounds.csv、experiment-summary.json 与原始 runs 目录。复算：uv run python scripts/stats_uncertainty.py runs full。
