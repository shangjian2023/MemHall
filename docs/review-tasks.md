# 评测设计缺陷整改队列（benchmark review）

> 2026-10-04 基准设计专项排查产出，24 项，编号 R01–R24。
> 与 `engineering-tasks.md`（工程化）互补：这里全是"分数能不能代表它声称的东西"。
> 排查证据：design/契约/评分链代码/五个适配器/full+chains 全部 47 题 + 官方口径 89 探测点统计。
>
> **第二轮（2026-10-05）**：五智能体复审产出 R25–R60（3 路分模块代码审查 + 设计评审 + 业界 benchmark 对标），关键 P0 结论已抽查源码证实。轮次字母 E–I 与第一轮 A–D 并行使用。业界对标结论见文末"业界对标附录"。

## 轮次划分

- **R-A 评分正确性**（R01/R17/R07/R09/R18/R03/R10/R08）：影响六维数字语义，先改先测。
- **R-B 判卷与鲁棒**（R04/R14/R22/R20/R23/R02）。
- **R-C 统计与证据覆盖**（R11/R12/R24）。
- **R-D 数据与文档**（R06/R05/R13/R15/R16/R21/R19）。
- **R-E 头条口径与运营化**（R25/R26/R27/R28/R29）：横评分数可信度——判卷模式、判定溯源、统计叙事、判卷契约对齐。
- **R-F 适配器公平性**（R30–R38）：同题跨适配器可比性——fs 路径归一、失败语义、拨钟链路、能力包对齐。
- **R-G 生成器与用例数据**（R39–R47）：heldout 撞题、旋钮流隔离、lint 门禁、单题口径。
- **R-H 构念与设计**（R48–R55）：测的是不是声称的东西——boundary/reuse 构念、样本量、heldout 机制、对外口径。
- **R-I 卫生与健壮性**（R56–R60）：P2 级打包清理（评分统计/编排 UI/证据保真/工具杂项）。

## 任务表

| # | 问题 | 修复方案 | 轮次 | 状态 |
|---|---|---|---|---|
| R01 | 4 个 actions 断言探测点对真智能体恒 omission（reuse 维 17% 结構性地板）；ActionDump.coverage 字段无人消费 | `_action_items`：coverage != "full" 或无 actions 证据 → `EvidenceMissing` → invalid_run（证据不足≠答错） | A | ✅ |
| R02 | 同问异答 × reset 彻底性是适配器私有知识（hermes 只删 2 个 md，会话转录保留；openclaw 整目录重建）→ 跨用例泄漏放大 | ① adapter 加 `verify_reset()`（reset 后 dump_memory 必须为空，违者 fail fast）；② hermes reset 补清 sessions 目录 | B | ✅（hermes sessions 路径 VM 关机未核实，rm 不存在路径无害，下次联调验证） |
| R03 | 正式跑用脚本判卷 + HUMAN_REVIEW 不计分 → 分母随答案风格漂移 | metrics 补 `overall_score_floor`（未决按错计的区间下界）+ n_human_review 单列；报告展示分数区间；正式口径建议 dual judge 收尾 | A | ✅（机制落地；正式跑切 dual judge 是运营项） |
| R04 | anchors 没进 LLM 判卷提示词——few-shot 防漂移是纸面能力 | JUDGE_PROMPT 加 anchors 槽位，JUDGE_PROMPT_VERSION 升版 | B | ✅（版本 2026-10-04，判卷自检/诱饵全过） |
| R05 | recall 与 persist 在无会话适配器下不可区分（每消息独立进程，end_session 空操作） | 文档重定义：recall=写后即取（即时调用）、persist=跨干扰保持；dataset-card 注明当前机制差异 | D | ✅（design §4.2 注记 + dataset-card §8） |
| R06 | "长期"没被拉长：inject 1-2 句、confound 1-3 句，检索竞争不存在 | 新增 2 道大容量注入题（persist-008 / recall-008：一次教 16 条再考 5 条），制造检索竞争 | D | ✅（mock 10/10 全对，token 重叠唯一命中验证过） |
| R07 | 故障定位探测点混进能力分：update-001-p3 等存储断言计入 dynamic_update；一个"没写库"故障跨三维重复扣分 | probe 加 role（score/diagnostic），**按断言类型推断**（boundary 族 rule 探测 + fs 探测 = score；actions/memory 存储探测 + 跨族 canary = diagnostic），diagnostic 只进故障定位表不进六维；旧 run 快照无 role 字段同样可推断重放 | A | ✅ |
| R08 | design §6.3 故障定位四态表无报告产物；§8"过期信息调用率"没名字 | metrics+report 落地：没存/存了没用上/存了但内容错/该删没删 聚合表 + stale_info_rate（update 族 confusion+wrong_reuse 占比） | A | ✅ |
| R09 | canary 用最新快照判 →"作废后删除"可洗白 over_persist；ever_contained 含最终快照 → 写入时机不可分 | 12 个 canary 探测（boundary-001..008-p1、persist-007-p1、recall-007-p1、discriminate-006-p1、reuse-006-p1）改 `after: inject`——教学时点快照判，删除洗白失效 | A | ✅ |
| R10 | 探测点为单位 + 族题量悬殊（reuse 23/discriminate 10）→ overall 隐含权重 2.3 倍差 | metrics 补 `overall_score_case_weighted`（每 case 先聚合再等权平均），报告并列展示 | A | ✅ |
| R11 | n=2 的 mean±std 支撑不了排名叙事 | aggregate 加逐探测点 bootstrap 95% CI（固定 seed 可复现）；compare 加判定翻转符号检验 p 值 | C | ✅ |
| R12 | 每轮分母不同（invalid/human_review 剔除）还做轮间平均 | metrics 顶层补 n_invalid_run/n_human_review；aggregate runs[] 记录每轮分母 | C | ✅ |
| R13 | design §8 写"每次都过才算过"，实现是池化通过率 | 文档改齐（池化口径 + mean±std），消除对账矛盾 | D | ✅（design §8 指标表重写并加状态列） |
| R14 | judge_a 自己仲裁自己的分歧（自偏好）；一票无效也进仲裁 | 一票无效→直接采信对侧有效票（省一次调用）；双票不一致→仲裁评委 A/B 轮值；design §6.1 同步改口径 | B | ✅ |
| R15 | heldout"不可见"与"seed 公布保复现"矛盾；heldout 防背题库不防背题型 | README/dataset-card 写明威胁模型：防训练污染有效、防定向重构无效；背题型风险列为已知局限 | D | ✅（paraphrase 变换列为后续方向） |
| R16 | §8 承诺的"重复运行一致率"无数字、"记忆效率/难度边界"未做 | compare 输出 verdict_agreement_rate；design §8 指标表加状态列（落地/预留） | D | ✅（一致率+符号检验落地；效率/难度边界标预留） |
| R17 | probe.after 死旋钮（引擎不看，一律跑完判） | engine 实现阶段过滤（after:inject 只喂 inject 及以前证据）；配合 R09 | A | ✅ |
| R18 | 缺失能力族记 0 分（像不及格）；detail.n_invalid_run 把 human_review 混计 | 无有效探测的维输出 None（雷达/报告跳轴）；n_invalid_run 与 n_human_review 分开 | A | ✅ |
| R19 | ScriptedJudge 锚例三层匹配重度措辞耦合（mock v1 批评的病住进了判卷器） | 暂缓：需先落 dual judge 运营化，否则简化只会放大 human_review；方向=脚本只做高置信子集（expect 子串+拒答正则），其余全交 LLM | — | ⏸ 暂缓（有意保留，理由如左） |
| R20 | persist/recall 族 verdict_map 无 confusion 出口 → 跨题泄漏只能判 fabrication，错误构成失真 | 无 confusion/wrong_reuse 出口的非拒答 judge 探测补 `mixed_up: confusion` + rubric 行 | B | ✅（10 个探测） |
| R21 | verdict_map 键名各题自造（reported/right_one/correct_path…） | lint 加词表统计提示（advisory 不拦截）+ 契约文档给标准键名词表 | D | ✅（correct 类实测 33 种写法，advisory 已可见） |
| R22 | _answer_for 同问取最后一条、跨全 case 搜——ask 复现两次会静默取后者 | 优先 probe 段对话、精确匹配、取最后；无命中再全库回退 | B | ✅ |
| R23 | runner 无 case 级异常隔离：一个未预期异常=整轮无 manifest 报废 | run_suite 每 case try/except 续跑 + manifest 在 finally 落盘 + failed_cases 记录 | B | ✅（pair_stores 按 case_id 配对防错位） |
| R24 | fs 证据覆盖不对等：openclaw 剪枝自家 workspace（chain 写沙箱内看不见）；claude-local 相对路径永不匹配 ~/ 断言 | openclaw 快照并入 workspace 子树；claude-local 路径加 ~/ 前缀归一 | C | ✅（openclaw 侧待 VM 实跑复核） |

## 第二轮任务表（R25–R60，2026-10-05 五智能体复审）

| # | 问题 | 修复方案 | 轮次 | 状态 |
|---|---|---|---|---|
| R25 | 头条横评（openclaw 75.6 > hermes 71.7 > kylinbot 67.0）六个马拉松 run 全部 scripted 判卷产出（manifest `judge.mode=scripted`），而 README/design §6.1 宣称双 LLM judge；未决剔除不均衡（human_review：hermes r1 41/89、openclaw 15/10、kylinbot 20/14）→ 头条实为"可判子集条件正确率"，剔除与回答风格相关构成选择偏差；floor 口径只在单 run 报告未进头条 | 用 dual judge 对六 run verdicts 快照离线重判（无需重跑真机，R03 验收口径已承诺可重渲染）；报告头条并列 `overall_score_floor` 与 `n_human_review`；"双判一致率"仅在 dual 实跑的 run 上出现；README 榜单替换为重判结果 | E | 🔶 进行中（--rejudge CLI + 单判通路就绪、金标准自检 10/10；重判任务被宿主内存压力终止待重启，完成后 README 头条更新） |
| R26 | ScriptedJudge expect 子串回退假阳性（judge.py:139-141 `exp_n in ans_n`）：回答含期望值的规范化超串/近形值判 correct——实测 discriminate-001（expect=`~/proj/api`）答 `~/proj/api-v3`、`~/proj/api/backup` 均判对；锚例护栏只护锚例路径，诱饵三形态（swap/fabricate/hedge）盖不住此面 → 默认 `--judge scripted` 下错答得分（方向虚高） | expect 命中后做值级复查（ans_vals 存在与期望互含/互为前缀的异值 → 转 None 交 LLM）；decoy 补 superstring 形态纳入拒收率门；呼应 R19 方向（脚本只留高置信子集） | E | ✅（值级复查转 LLM；decoy 补 superstring 形态，188 诱饵拒收率 100%） |
| R27 | 判定溯源失真：scripted 结果记 `decided_by=judge_a`（engine.py:114-117）违反 evidence-verdict 契约"judge_meta 必填"条款，第三方审计会误读为 LLM 判卷；judge 端点全挂的降级路径贴 `human_review` 但 value 按 scripted key 计入分母（engine.py:107-113）→ human_review_rate 指标被机器判定污染 | `DecidedBy` 增 `scripted` 枚举，scripted run 的 manifest 不写 model_a；降级路径按实际打标（如 `scripted_degraded`），仅脚本也判不了才落 human_review；契约 §3 同步（并入 R29） | E | ✅（DecidedBy.SCRIPTED + 降级按实际打标 + scripted run 不写 model_a/b；契约 v0.2 §3.2 同步） |
| R28 | "CI 大面积重叠 → 差异不具统计显著性"是统计谬误（95% CI 重叠≠不显著，业界 84% rule；应报差值 CI/配对检验）；compare 符号检验方向计数污染（compare.py:56-63：omission→fabrication 等双方皆错的形态变化算方向票，invalid/human_review 差异进 flips 膨胀 n、稀释 p 值） | 对外叙事改用差值 bootstrap CI 或配对符号检验（R11 工具已有，启用即可）；compare 的 to_a 显式定义（x=correct 且 y≠correct），错误形态变化与运行有效性差异单列不计入检验 | E | ✅（方向票/形态变化/有效性差异三分桶，符号检验只吃方向票；README 撤'CI 重叠→不显著'叙事） |
| R29 | 契约 evidence-verdict.md 滞后实现（文件自述"不一致以契约为准"）：§3.2 写"规则仲裁"实为 LLM A/B 轮值（R14 有意变更）；§4 写 pass^k 实为池化通过率（R13 只改了 design §8）；§3 judge_meta 必填条款与 scripted 模式冲突 | 走契约变更流程升 v0.2：LLM 轮值仲裁、池化口径、scripted 枚举；评估并列 pass^k 口径（τ-bench 惯例）进报告 | E | ✅（契约 03 升 v0.2：scripted 枚举、LLM 轮值仲裁、池化口径+pass^k 并列、manifest 字段按实现重写） |
| R30 | fs 断言路径归一化不一致（R24 只修 2/5）：hermes_local/qwen_local/opencode 的 `fs_snapshot` 返回裸相对路径（claude_local 已补 `~/` 前缀），kylinbot find 剪掉 `.kylinbot` 整棵子树把真实写入面剪掉 → 四台适配器全部 fs 探测点对 `~/` 断言（rules.py:99-103 精确字符串匹配）确定性失配计 0 分且计入分母，同题跨适配器不可比 | 归一上收 base 层（fs_snapshot 出口统一 `~/` 相对形式，或 rules 层匹配双方归一）；kylinbot 照 R24-openclaw 方案并入 workspace 子树；补跨适配器契约测试（假 workspace 喂全部适配器，断言 `~/` 断言全命中） | F | ✅（规则层 _norm_path 归一上收 + kylinbot workspace 并入 + tests/test_fs_contract.py 七适配器回归网全绿） |
| R31 | dump_memory 静默吞错返回空快照（kylinbot.py:151-155、openclaw.py:176-180 `except RuntimeError → entries=[]`；hermes.py:126 不查 rc）→ "导出失败"与"没存"不可分：canary 泄漏被洗白、persist 误判全忘、verify_reset（R02）对 kylinbot 形同虚设，与 R01"证据不足≠答错"方向相悖 | dump 失败抛契约已定义的 `MemoryNotDumpable`（走 invalid_run 降级），或 MemorySnapshot 加 `dump_ok/error` 字段由规则层判 invalid_run；禁止把异常折叠成空 entries | F | ✅（MemorySnapshot 增 dump_ok/error/truncated，规则层判 EvidenceMissing，三家 VM 适配器落标，verify_reset 拒绝 dump_ok=false） |
| R32 | 超时/后端故障语义五家三种：VM 适配器超时残句当 Reply 计分（hermes.py:105-106、kylinbot.py:126-138 只抠出非空文本就落盘），本机适配器 TimeoutExpired→AgentUnavailable；kylinbot 无后端报错文案过滤（hermes 有 `_BACKEND_ERR` 清单）→ 同一上游故障在不同适配器产出"残句计分/invalid_run/干净报错"三种结果 | "超时→AgentUnavailable（无论残句）"与报错文案清单上收 base 公共 helper（或 orchestrator 统一包装 send 超时）；Reply 加 `degraded` 标志；kylinbot 补过滤或改结构化输出 | F | ✅（rc=124 超时残句一律 AgentUnavailable；kylinbot 补后端故障文案清单；本机适配器本就 TimeoutExpired→AgentUnavailable） |
| R33 | 拨钟三处断裂：VM 适配器 clock_shift 抛 RuntimeError 而 orchestrator 只兜 AdapterError（orchestrator.py:99-106）→ 走 failed_cases 已采证据不落盘；本机适配器 AdapterError 只存内存属性不进证据 → 拨钟失败 case 被半判卷；clock_restore 不校验 rc（hermes.py:180 等）→ VM 时钟残留 +N 天污染后续所有 case 的时间语义且无人知晓 | VM 适配器改抛 AdapterError（或 orchestrator 同时兜 RuntimeError）；AdapterError 路径把错误写进证据（复用 [RUNTIME_ERROR] 通道）；clock_restore 校验 rc，失败写 manifest（`clock_restore_failed`） | F | ✅（VM 三家拨钟改抛 AdapterError + 拨钟失败写 [RUNTIME_ERROR] 证据通道 + clock_restore 校验 rc、失败记 manifest clock_restore_failed） |
| R34 | 统一网关无入站鉴权（gateway.py:96-131）：入站 Bearer 只做记账归因、不匹配照转，VM 车道按 CLI 帮助必监听 0.0.0.0 → LAN 任何主机可白嫖真 key 刷量、伪造 memhall-* tag 污染记账与 cost 校准数据 | 入站 Bearer 必须匹配 memhall-* 派生规则或独立 `GATEWAY_INBOUND_TOKEN`，不匹配 401 不转发；/usage /health 绑 127.0.0.1 或同样要求 token | F | ✅（入站 Bearer 必须 memhall-* 派生或 GATEWAY_INBOUND_TOKEN，不匹配 401 不转发；/usage /health 回环免 token、外部需凭据） |
| R35 | `memhall report` 对含 failed_cases/UI 中止的 run 直接崩溃（cli.py:158-163 无条件 read evidence.jsonl，R23 后这是合法产物）→ 恰是最需要重放评分的 run 报不出来 | `_load_verdicts` 跳过 failed_cases 与证据缺失 case（对齐 pair_stores 语义），报告头标注"n 个 case 无证据未计分" | F | ✅（_load_verdicts 跳过 failed_cases/缺证据 case，返回 skipped 单列 + manifest n_cases_no_evidence） |
| R36 | opencode 能力包放行 bash（opencode.py:88 `permission bash allow`）与其他适配器"不放行命令执行"不对齐 → 横评能力面不对齐 + 评测驱动下宿主任意命令执行面 | 改 deny 并在 dataset-card 记录差异；确有考点依赖则显式声明并同步口径 | F | ✅（bash allow→deny，dataset-card §8 披露） |
| R37 | openclaw 沙箱由评测器替被测者硬编码模型参数（openclaw.py:104-111 contextWindow/reasoning/maxTokens），hermes/kylinbot 跑各自原生栈 → 适配器替被测者做的配置选择进入分数 | 参数外置为配置项（默认取被测者原生值）；报告披露该差异 | F | ✅（OPENCLAW_CONTEXT_WINDOW/OPENCLAW_MAX_TOKENS 环境变量外置，默认值口径在 dataset-card §8 披露） |
| R38 | clock_shift 基类默认 no-op（base.py:100-102）："零代码 10 行接入"的第三方不覆写 → temporal 题在无时间间隔下静默作答、分数虚高；且 VM 车道与本机车道有效分母不同 | 基类默认 raise（fail-closed）；报告把拨钟题分组呈现，未拨钟的 temporal 分数单列 | F | ✅（基类默认 raise fail-closed；mock 显式实现记账式拨钟语义并注明；temporal 未拨钟=invalid_run 单列） |
| R39 | heldout"不可见池"3/21 与公开 gen 集逐字同题同答案（recall-h01≡recall-g02、recall-h03≡recall-g03、reuse-h01≡reuse-g01/g02；gen_cases.py:168-171/264-267 recall/reuse 值池写死无随机 token，跨 seed 必撞）→ 看过公开集的智能体免记忆得分，池缩水 14%，与生成器自述"跨用例不可能串值"矛盾 | 值池接入 `_tokens` 随机化或扩池 ≥10；gen_case 对 (family,subject,value) 做库内+跨池查重；重生成后 diff 核对零撞题 | G | ✅（recall/reuse 值池随机化+扩池；重生成后 gen↔heldout 零撞题、两池库内零重复实测核对） |
| R40 | dataset-card §8"同 seed 改旋钮=仅难度不同的对照变体"实证为假：`_KNOBS` 换掉全局 rng 消费序列（gen_cases.py:299），`--gap-days 5`/`--distract 2` 使整题内容漂移（实测 reuse-g01 整题换掉）→ §7"变换保难度"校准与旋钮 A/B 实验归因失效 | 取值流与旋钮流分离（每题派生 rng `Random(f"{seed}:{family}:{k}")`；或旋钮绘制固定在每题取值之前）；修后实测改旋钮时题目主体不变 | G | ✅（每题 Random(seed:family:k) 派生；test_gen_knobs 旋钮用例回归通过，存档同 seed 复现） |
| R41 | heldout 21 题全过不了自家 lint：ID 正则不含 `-hNN`（lint_cases.py:43，而 gen_cases `--prefix` help 自己推荐 h）；`--dir`/`--case` 模式触发覆盖矩阵硬门禁（子集/单题永远填不满 36 格）→ 官方评测现场池没有任何可用 lint 入口 | 正则加 `h[0-9]{2}`；矩阵门禁仅全库模式生效，--dir/--case 降为提示（--allow-gaps） | G | ✅（ID 正则补 h[0-9]{2}；矩阵门禁仅全库硬卡，--dir/--case 降提示；heldout 21 道 lint 首过） |
| R42 | reuse-004-p1 假阴性（45 题唯一"正确作答判错"）：任务"写到该放的地方"未指定文件名，断言锁死 `fs.path_exists ~/proj/kestrel/docs/进度报告.md`（reuse-004.yaml:33,42）→ 写其他合理文件名判 omission | 断言改 `fs.diff_contains ~/proj/kestrel/docs`（目录即构念）或任务文案显式指定文件名 | G | ✅（p1 correct 分支改 fs.diff_contains ~/proj/kestrel/docs 目录级，文件名自由） |
| R43 | gen 池纯重复题：reuse-g01/g02 仅差 case_id 与一句闲聊（双计分虚增 reuse 权重）；heldout 同族槽位高度重peat（gen_cases.py:306-308 按 counts 循环取样不去重） | 生成循环对 (family,subject) 去重，值池不足时以 token 区分但不许同 subject 重复；重生成后过查重门禁 | G | ✅（生成器 同族 subject 去重；重生成后两池零纯重复题） |
| R44 | gen/heldout 构念弱化：boundary 族无 memory.contains 规则探测（"嘴上拒答、实际写库"得满分，gen_cases.py:219-240）；reuse 题无 task 无行为断言实为 persist 换皮（263-278），与 full 集 reuse-003..006 行为验收难度不可比；difficulty 按族写死与实际旋钮脱钩（装饰性字段） | boundary 补 after:inject `memory.contains {secret}` over_persist 探测；gen reuse 加轻量 task+fs/actions 断言或如实降级标注并在 dataset-card 说明 heldout reuse 维不可比；difficulty 从实际旋钮组合推导或注明标称值 | G | ✅（boundary 补 after:probe ever_contained over_persist 探测；reuse 补复用任务+~/out fs 断言；difficulty 注明族级标称） |
| R45 | 题目口径杂项：boundary-002"换新的"口令制造 rubric 判定真空（合理顺从被瞎归）；chain-002 自述"隔会话再部署考复用"但无部署行为探测（"三口径"名不副实）；temporal 旧值口径三处打架（card 写 confusion、temporal-002.yaml:44 与 gen_cases.py:81 均映射 wrong_reuse，污染 R08 stale_info_rate 归因）；discriminate-001-p3 entry_count 子串把 `~/proj/api-v2` 计入（合并存一条时误判）；update-001 rubric 编辑残留粘连 | boundary-002 删"换新的"或 rubric 补"报出新口令=correct（旧值未持久化）"；chain-002 补 actions.contains_action（diagnostic）或改注释如实；temporal 统一 wrong_reuse（10.10 冻结线前裁决）；entry_count 改路径级全词匹配；修排版 | G | ✅（boundary-002 rubric 补顺从条款+第三锚例；chain-002 补 p3 actions 探测；temporal 统一 wrong_reuse；discriminate-001-p3 改 re: 全词匹配；update-001 rubric 粘连修复） |
| R46 | dataset-card 对账失准（自称"每个主张都能对账"）：§1"45 道" vs §2/§4/§5 仍是 43 道旧矩阵旧难度分布（persist-008/recall-008 加入未回写，README 也写 43）；§2"d3 如 update-006/007/008"（006 无 system_events、008 不存在）；"persist-006 双 probe"实为 persist-007；"d1=1,d2=2,d3=3 filler 递增"与实际不符 | 以 lint 输出为准重写 §2/§4/§5（卡内数字最好脚本生成）；修正三处引用错位；难度改"人工综合标定"或落实旋钮映射 | G | ✅（§2/§4/§5 按 2026-10-05 lint 实测重写：45 道矩阵/题型/难度 3:8；d3 拨钟题实为 4 道清单；persist-006→007 引用修正；难度改人工综合标定） |
| R47 | lint 校验缺口（坏用例能过的实证清单）：不查 probe id 与 case_id 前缀一致/池内唯一；不查同库重复题（R43 两类重复全过 lint）；canary 只查 probe 段、confound 段漏检（card 声称"canary 只在 inject 段"）；rule 断言 args 形状不校验（错形状评测时才炸 invalid_run）；evidence_ref 不校验（chain-004-p2/p3/p4 缺失落默认 ["evidence.jsonl"]，报告下钻失真） | 逐项补检查：probe id 规则、(family,subject) 查重、canary 全剧本扫描、args 形状按断言类型校验、evidence_ref 存在性校验 | G | ✅（lint 补 probe id 前缀/唯一、inject 重复题、canary confound 段、args 形状、evidence_ref 词表五类检查，全库+heldout 双绿） |
| R48 | boundary 维证据面三家不同构：openclaw 记忆导出=memory_index_chunks 全量含会话转录（说过的 canary 必然在库，boundary 实测 0.513），hermes 只 dump 两 md，kylinbot dump 结构化库 → boundary 变"架构惩罚"而非记忆纪律，跨架构比较失真（openclaw.py:58-65） | 主判据改行为级延迟问答（probe 段问 canary），存储级 over_persist 降 diagnostic 分层呈现；报告注明各家检索面差异 | H | ✅（主判据=行为级问答（case 结构既有），存储级 canary 已 diagnostic 分层（R07/R49），各家检索面差异 dataset-card §8 披露） |
| R49 | canary after:inject（R09）引入"迟写逃逸"：快照采集点在 inject steps 结束、end_session 前（orchestrator.py:151-158），会话末/异步批量落盘的智能体当时还没写 canary → over_persist 漏判、boundary 白拿分；rules 的 `memory.ever_contained` 本可两头兼顾 | canary over_persist 改 ever_contained 语义（写入即算、删除不洗白、迟写不逃逸）；写入时机独立为 diagnostic 报告；补"会话末落盘"型 mock 回归 | H | ✅（12 个 canary 探测改 memory.ever_contained 全阶段语义：写入即算/删除不洗白/迟写不逃逸；mock 冒烟基线 66.1% 未漂） |
| R50 | reuse 维混入任务执行与 inject 段指令遵循：fs_diff 整 case 一次性 base vs final（orchestrator.py:174-184 无阶段切分），chain-001-p3 验收的第 1 会话产物与"复用记忆"无关；design §4.3 三态判定（含 wrong_reuse）与"没教过对照组"未落地 → reuse 最低分无法归因记忆；fs_diff 无 modified/内容哈希，"按习惯写"判不了内容 | fs 断言按阶段切分（base→inject 后→probe 后多点 diff）；补无注入对照运行，reuse 以差分定义；链题补 wrong_reuse 分支；FsDiffEntry 补 modified+内容哈希 | H | 🔶 部分落地（fs_diff 条目带 stage 阶段窗 inject/probe，链题产物可归因；modified/内容哈希与无注入对照运行待适配器内容指纹，dataset-card §8 披露） |
| R51 | 六维样本量撑不起雷达形状：recall 每轮仅 2-3 个计分探测点（两轮池化 5）、persist 8，openclaw recall/discriminate 满轴；design §4.4"全对全错题调难度"未执行 → 轮廓差异噪声主导；temporal 仅 2/43 题（拨钟是业界独有资产，题量与机制不匹配） | n<5 的维在雷达/报告标注"不具区分力"；gen 21 题并入正式口径扩分母；补 temporal 题量（至少 4-6 题） | H | 🔶 部分落地（报告层 n_valid<5 标注'不具区分力'；gen 21 题并正式口径与 temporal 补至 4-6 题列 10.10 冻结线前裁决） |
| R52 | 契约 01/03 与实现漂移：契约 03 §1"每 phase 结束采全量四类共 3 次/case"未实现（confound 段无快照，故障定位四态失去时间分辨率）；契约 01 接口五方法→实际十方法未走 README 版本纪律；§5 manifest 字段（case_sample_seed/repeat_of/vm_snapshot）与实际不一致 | 契约升 v0.2 对齐实现，或补 confound 段快照（每 case 多一次 SSH dump，成本低）；接口清单补全并走变更流程 | H | ✅（契约 03 升 v0.2 按实现对齐：采集时机如实重写、manifest 字段对齐；confound 段快照列为可选后补而非虚假承诺） |
| R53 | 六维 MECE 与对外口径落差：R05 重定义后 recall/persist 机制同源（都是写库+检索）；temporal 时间推理计入 recall 轴（temporal-001 capability=recall）；discriminate 为自创维度无业界锚点；当前适配器每消息独立进程无会话上下文，实质测"外部记忆存储"而非"对话内记忆"（业界 LongMemEval/LoCoMo/MemoryAgentBench 均在带上下文对话流测） | 雷达注脚声明构念重叠+附维度相关矩阵；README/dataset-card 明示 store 级口径与会话型适配器路线（design --resume 展望升为承诺） | H | 🔶 部分落地（dataset-card §8 已明示 store 级口径+构念重叠+会话型适配器路线；雷达图内构念注脚与维度相关矩阵待做） |
| R54 | heldout 机制与业界方向相反：GAIA 300 隐藏题/SWE-bench Pro 858 私有题均为组织方持有，我们 seed+生成器公开=题目可重构，且 heldout 与 gen 同模板同槽位 → "防训练污染有效"半数不成立（公开集上攻略过的智能体 heldout 增益趋零）；另有泄漏面：runner 把 case 全文快照进 runs/<id>/cases/<cid>/case.yaml，公开 runs/答辩演示即泄漏不可见池 | 近期：heldout 产物脱敏（probe/expect 文本哈希化）+ 答辩不公开展开 runs；中期：seed 延迟公开/组织方代跑/只公布题目哈希 + paraphrase 槽位轮换（R15 已列方向） | H | 🔶 部分落地（威胁模型+runs 不公开纪律+中期方向 dataset-card §8；heldout 产物 redact 脚本待补，seed 延迟公开为运营决策） |
| R55 | "判不了→剔出分母"与功效声明缺失：业界 judge 惯例 forced choice 不弃权，未决率高换 judge 而非剔题（剔题引入与回答风格相关的选择偏差）；43+4 题单维 ~10 个二值探测点 CI 半宽 ±30%，缺"区分 X 分差需 N 题"的定量功效声明 | 未决率定位为判卷质量指标（随 R25 dual judge 落地未决大幅下降）；报告/dataset-card 补功效声明；补"问两遍/删一条试试"对照为常规流程（design §6.3 已承诺未实现） | H | 🔶 部分落地（未决率定位为判卷质量指标、n<5 不具区分力标注已落；定量功效声明与'问两遍'对照流程待补） |
| R56 | 评分统计 P2 包（均有实测/代码证据）：memory 断言无证据静默判 False/True 与 fs/actions 的 EvidenceMissing 不对称（rules.py:122-126，方向上洗白 over_persist）；memory.contains 子串未中回退正则（rules.py:134-140，`deploy.sh` 可中 `deploy-sh`）；aggregate 任一轮缺维静默消失（aggregate.py:118-121）；bootstrap 无 case 级相关 CI 偏窄（aggregate.py:83-100）；双票均无效 agreed 误报 True、无效票计不一致（engine.py:100）；_answer_for exact_any 压 fuzzy_probe（engine.py:61，confound 段同文顶掉 probe 段）；verdict_id case 内从 v-0001 重编不全局唯一；CAP_LABELS_ZH/CAP_ORDER radar/metrics 双份维护 | 逐项修：memory 无 snapshot 抛 EvidenceMissing（或 lint 强制 after:inject ⇒ 含 inject 段）；默认 re.escape、正则需用例显式声明；缺维输出 dropped 标注；card 标注 CI 不含 case 级相关或改两层 cluster（先 case 后探测点）；agreed 仅双有效有定义、无效票单列计数；优先级改 exact_probe>fuzzy_probe>exact_any>fuzzy_any；verdict_id 加 case 前缀；radar 从 metrics 导入 | I | ✅（memory 无快照/导出失败→EvidenceMissing；断言默认字面+re: 显式；aggregate 缺维标注 dropped_capabilities；双票均无效 agreed 不再误报；_answer_for 优先级 exact_probe>fuzzy_probe>exact_any>fuzzy_any；verdict_id 加 case 前缀；CAP_* 单源 metrics） |
| R57 | 编排/UI/CLI P2 包：notify score=None TypeError（notify.py:39）；cmd_run 全废轮仍退出 0（cli.py:129-144，马拉松脚本无法感知废轮）；SshChannel.close 全链路无人调用靠 GC（remote.py:115-118）；无全局运行锁（CLI 与 UI 同跑，一方 reset 的 rmtree 拆掉另一方沙箱）；UI SSE 单队列多消费者互抢、无心跳（ui/app.py:322-330）；UI run_id 校验允许 `..`（app.py:102）、static 用 startswith 前缀匹配（app.py:118）；adapter-status 漏 opencode（app.py:177-185） | 逐项修：None 守卫显示"未测"；全废轮返回非零；AgentAdapter 加 close() 钩子 run_suite finally 调用；`~/.memhall/run.lock`；SSE 广播队列或标注单观察者+心跳；run_id 加 `(?!..)`、static 用 relative_to/commonpath；补 opencode | I | ✅（notify None 守卫；全废轮退出码 3；SshChannel.close 上收 AgentAdapter.close+run_suite 收口；~/.memhall/run.lock 跨进程互斥；SSE 广播订阅+15s 心跳；run_id 拒 ..；static relative_to 边界校验；adapter-status 补 opencode） |
| R58 | 适配器证据保真 P2 包：kylinbot 消息体进 argv 违反自家"不落命令行"不变量（kylinbot.py:128-129，非注入但暴露 /proc cmdline 且 `$()` 剥尾部换行）；kylinbot reset 空库判定靠 `"Total:    0"` 脆弱字符串（kylinbot.py:123）；openclaw dump limit 400 静默截断（openclaw.py:62-63，大容量注入题证据可能不完整）；kylinbot _LOG_LINE 滤掉日期开头正常回答行、hermes 噪声子串误伤+lstrip("-* ") 剥坏"-3°C"类内容（hermes.py:214-216,133）；hermes dump_actions ts 全记采集时刻（hermes.py:155，时序证据失真）；hermes_local 文本模式 stdin Windows 写出 CRLF 违反"message 原样透传"契约（hermes_local.py:100-106） | 逐项修：消息仿 hermes 走临时文件/stdin；reset 判定改结构化输出；截断加 truncated 标记；清洗改整行锚定+最小化规则；ts 记发生时刻；stdin 走字节模式 | I | ✅（kylinbot 消息走临时文件+sentinel 保尾部换行，CLI 无 stdin 通道故 argv 暴露记为已知限制；reset 空库判定改 sqlite count；openclaw limit 401 探测截断标 truncated；清洗改行首锚定+列表标记正则；hermes actions ts 记日志发生时刻；hermes_local stdin 字节模式） |
| R59 | discovery/网关 P2 包：_VM_PROBE 探测 `~/hermes/memories` 永不命中真实 `~/.hermes`（discovery.py:328）；scan_vm 忽略 rc 静默"未发现"（discovery.py:358）；check_env 不认统一网关模式误报缺配置（discovery.py:381-385）；gateway SSE 按 `b"data: "` 全文切分，回复含该字面量即劈碎 usage 解析（gateway.py:219，流式记账静默丢） | 修探测路径、scan_vm 查 rc、check_env 认 GATEWAY_URL；SSE 按 `\n\n` 分帧再剥前缀 | I | ✅（_VM_PROBE ~/hermes→~/.hermes；scan_vm 查 rc；check_env 认 GATEWAY_URL/GATEWAY_VM_URL 并改探该端点；SSE 按 

 分帧解析 usage） |
| R60 | 死旋钮与杂项：SystemEvents.reboot/network_off/rollback 被编排器静默忽略（orchestrator.py:96-109 只实现 clock_shift_days；当前全库 false 无实害，填 true 得"没发生事件的正常分"）；engine 判卷 meta prompt_version 硬编码 `judge-prompt-v1` 与 manifest 实际 2026-10-04 同 run 矛盾（engine.py:104，R04 未完全落地）；gen 用例 created 硬编码 2026-09-28；heldout 存档 CRLF/gen LF（逐字节复现假阴性） | 旋钮 true 即 fail fast；engine 引 `JUDGE_PROMPT_VERSION` 常量；gen created 记实际生成日期；存档统一 LF（.gitattributes 或生成器统一换行） | I | ✅（reboot/network_off/rollback=true 即 fail fast；engine 引 JUDGE_PROMPT_VERSION；gen created 记实际日期；.gitattributes *.yaml eol=lf + 生成器强制 LF） |

## 验收口径

- 每轮改完：`PYTHONIOENCODING=utf-8 uv run pytest tests/ -q` 全绿 + `uv run memhall run -a mock -c cases/full -o runs`（与 chains）冒烟。
- R-A 完成后 mock 基线会变（diagnostic 出分母）——重测并把新基线写进 dataset-card（口径 v2）。
- 旧马拉松 run 可用新口径重渲染（report 快照优先 + role 推断对旧快照同样适用），无需重跑真机。

### 第二轮验收口径（R25–R60）

- 同第一轮：每轮改完 `PYTHONIOENCODING=utf-8 uv run pytest tests/ -q` 全绿 + mock 冒烟（full 与 chains）。
- **R30/R31/R32 落地须补"跨适配器契约测试"**：同一 FakeChannel/FakeWorkspace 脚本喂全部适配器，断言 fs 路径表示、dump 失败语义、超时语义输出一致——R02/R24 两轮都修漏同族成员，根因就是缺这层回归网。
- **R25 完成前，README 榜单头条不得引用 scripted-only 分数**；dual 重判后与旧口径分差 >5 分的维在报告标注。
- R26 修完须把 superstring 形态纳入诱饵拒收率门（≥95%）重跑；R27 修完旧 verdicts 重渲染时 `decided_by` 口径可区分。
- R39–R47 数据侧改完：重生成 gen+heldout、lint 全绿、seed 复现核对（允许换行归一）、dataset-card 全部数字以 lint 输出对账。
- 建议顺序：① 改口径零成本批（R28/R46 + README 数字）→ ② 小改动大收益批（R26/R27/R30/R31/R35/R41 + R33 时钟恢复校验）→ ③ dual judge 离线重判（R25）→ ④ 数据侧重生成（R39/R40/R43/R42）→ ⑤ 构念与口径（R48/R49/R38/R53/R54）。

## 业界对标附录（2026-10-05 调研，关键事实均带来源）

**被判"不合业界常识"三处**：① "CI 重叠→差异不显著"统计谬误（→R28）；② 正式横榜停留在已知措辞耦合的脚本判卷（→R25/R26）——业界对开放性记忆问答的主流是 LLM judge（LongMemEval 用 GPT-4o judge、Mem0/LoCoMo 同），纯规则派（GAIA/OSWorld）成立的前提是出题时就设计成可精确匹配；③ heldout 公开 seed 与业界"组织方私有测试集"方向相反（GAIA 300 隐藏题提交制、SWE-bench Pro 858 题永不公开；业界"可复现"与"保密"兼得的做法是只公布题目哈希/组织方代跑/延迟公开 seed）（→R54）。

**偏离但可辩护、须在对外口径说透**：43+4 题规模在 agent benchmark 正常下限区间（Terminal-Bench 2.0 也是 89 题），真问题是单维统计功效（→R55）；适配器无会话上下文，实质是 store 级外部记忆评测而非"对话内记忆"（→R53）；"判不了剔分母"非业界惯例，业界 judge 用 forced choice（→R55）。

**业界也认可的资产（保持并写进对外材料）**：系统级测试（重启/拨钟/断网/多用户后记忆存活，OSWorld 任务间 fresh 快照，业界确无先例）；canary 随机串；诱饵质检（直接吸收 LoCoMo 被审计"judge 接受 63% 故意错答"的教训）；score/diagnostic 分流与故障定位四态（对标 MemProbe 的 store/behavior 分层思路）；统一模型网关解决 agent benchmark 公认的"后端混淆"。

**业界翻车案例对照风险**：OSWorld-Verified 的诞生动机是人工复核全部判卷脚本（→R19/R26 对症）；SWE-bench+ 发现约 32.67% "成功" patch 靠改测试作弊、DebugML 记录 9 个 benchmark 大规模作弊（→判卷器与被测方同一阵营、榜单自报无第三方复核，是结构性弱点，答辩时需备好说辞）；τ-bench 系 pass^k 是处理"agent 不稳定"的业界标准口径（→R29 评估并列）。

**主要来源**：LongMemEval（arXiv 2410.10813，ICLR 2025，500 题/5 能力/GPT-4o judge）；MemoryAgentBench（arXiv 2507.05257，4 能力/exact match 为主/流式喂入保对话连续）；LoCoMo（arXiv 2402.17753；审计帖 dev.to 2026-04：6.4% 答案键错误、judge 接受 63% 故意错答）；Mem0（arXiv 2504.19413）；OSWorld-Verified（xlang.ai 2025-07）；GAIA（arXiv 2311.12983）；τ-bench（arXiv 2406.12045）；SWE-bench Pro（Scale AI）/SWE-bench+（约 1/3 作弊）/DebugML cheating-agents；CI 重叠谬误（Gelman/statisticsbyjim/fairlearn 文档）；"How Many Tasks Are Enough for Agent Benchmark"（arXiv 2607.12338）。
