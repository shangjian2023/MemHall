# 第一轮审核 · 事实核对（checklist A 节）

> 审核范围：ch00/ch01/ch03/ch04/ch05/ch06/ch07/ch08 全部断言对照仓库核验（只读）。
> 验证手段：lint_cases.py 全库实跑、judge_decoy.py 实跑、judge_selftest.py --offline 实跑、pytest --collect-only 计数、MemoryCase+probe_role 逐套件探测点计数、cases/ 逐文件比对、CHANGELOG/README/dataset-card/benchmark-v2/contracts/调研/审计文档/05-vm-full-rerun 逐条对账、官方命题原文（benchmark-sources/official-challenge-current.txt）引文核对、匿名扫描。
> 日期：2026-10-14。审核员：round1-facts。

## 发现清单

### MUST

- **[MUST] ch03·3.2（ch03-overview.md:53）| 全库能力分布顺序写错** | 写"29/31/29/27/32/33"，但按本句前文刚列出的六能力顺序（长期保持、记忆调用、动态更新、相近区分、边界识别、任务复用），lint 实测为 **31/33/32/27/29/29**；与 ch04 表 t4-3-stats 行计（31/33/32/27/29/29）直接打架，两章数字不一致评审必抓 | 证据：`scripts/lint_cases.py` 全库输出"能力分布: {'persist': 31, 'recall': 33, 'dynamic_update': 32, 'discriminate': 27, 'boundary': 29, 'reuse': 29}"；ch04-dataset.md:193-202 | 改法：ch03:53 改为"实测为 31/33/32/27/29/29"（多集合同、错在按能力顺序的对应关系）。

- **[MUST] ch04·4.3.2（ch04-dataset.md:154）| "challenge-v2 的六个更新模板"归属错误** | challenge-v2 只有 **4 个**更新模板（replacement=直接迁移、multi-hop=两跳改口、partial-field=只改一个字段、scoped-override=全局约定与一次例外）；"提议被否决（根本没改）"= development-v2 的 `dynamic_update-v2-cancelled-change`、"试验后回退重新固定"= development-v2 的 `dynamic_update-v2-version-pin`——六形态分布在两个题包 | 证据：`ls cases/challenge-v2/`（dynamic_update-v2-* 仅 4 族×3 变体）；`ls cases/development-v2/`（另 2 族）；两 dev 模板 inject 台词与报告六形态逐一对应（cancelled-change:"提议已被否决，仍用原配置"；version-pin:"试验已结束，回退并重新固定"） | 改法："v2 题包共六个更新模板（challenge-v2 四个＋development-v2 两个）各考一种改口形态……"。

### SHOULD

- **[SHOULD] ch03·3.2（ch03-overview.md:53）| "动态更新、边界识别每格 2–3 题"与本章自己的矩阵矛盾** | t3-2 与 lint 实测：两行绝大多数格为 1，加厚只发生在 动态更新×路径=3、边界×敏感=3。该句抄自 dataset-card §2 的陈旧注释（dataset-card 自己的矩阵同样打脸） | 证据：lint 矩阵（dynamic_update 行 1/3/1/1/1/1，boundary 行 1/1/1/1/1/3）；ch03 t3-2（:43-48） | 改法："重点格加厚——动态更新×路径 3 题、边界×敏感 3 题（含严格/宽松两档契约），其余格 1–2 题"。

- **[SHOULD] ch08·其一（ch08-wrap.md:5）| "实接 openKylin 真机四家"对 kylin-agent 过度声明** | kylin-agent 仅适配器接入＋fork 源码级复核，VM 真机验收未完成、无实测成绩；ch06 6.2 与 README 口径均为"已接入、待验收、不引用 Hermes 分数推断" | 证据：README.md:86；ch06-engineering.md:27-29 诚实声明；CHANGELOG 2.0.0 kylin-agent 节（"⚠️ 未实测：VM 联调待进行"） | 改法："接通九车道——真机四家（hermes/kylinbot/openclaw 已 VM 实测联调，kylin-agent 待真机验收）、本机车道与 mock 基线"。

- **[SHOULD] ch04·4.3.3（ch04-dataset.md:158）| boundary-003 机制句半对** | "任何时点出现过 8080 **或 canary 串**即判 over_persist——断言用'曾经包含'"：8080 分支确为 `memory.ever_contained`，但 canary-v9cd 分支实为 `memory.contains`（只看最新快照，考段删除可洗白）；全库其余边界题（boundary-001/002/004/005…）的 canary 才用 ever_contained | 证据：cases/full/boundary-003.yaml:41-46（8080→ever_contained，canary-v9cd→contains）；boundary-001.yaml:38-41（canary→ever_contained）；src/memhall/scoring/rules.py:192-222（contains=latest 快照，ever_contained=全阶段快照） | 改法：改为"任何时点出现过 8080 即判 over_persist（'曾经包含'断言，事后删除洗白不了）；canary 串在多数边界题同样用'曾经包含'断言"，或如实注明 boundary-003 的 canary 分支为当前包含。

- **[SHOULD] ch09·9.2（ch08-wrap.md:33）| 匿名风险：写明"Gitee 镜像 mazhuoran23/MemHall"** | 个人账号名（疑似含姓名线索），违反公共纪律"不出现学校/姓名/导师"与 checklist D2 匿名扫描要求。全文其余匿名扫描干净（无学校/姓名/导师/Windows 用户名/主机名/IP/凭据） | 证据：module-brief-common.md 第 6 条；review-checklist D 节 | 改法：删账号名，改"Gitee 镜像（仓库地址见 README 与交付材料）"；若组委会材料必须精确到仓库，向总控确认匿名边界后再定。

- **[SHOULD] ch03·t3-1 ↔ ch04·t4-1 | 七题型中文译名全书不统一** | ch03 用"时间推理/相似干扰"（:23-24、:30、:59），ch04 用"时间理解/相近区分"（:14、:206）；design.md §4.2 权威名为"时间理解/相近区分"。且题型"相似干扰"与六能力"相近区分"并排出现，读者易混 | 证据：design.md:92-93；ch03-overview.md:23,24,30,59；ch04-dataset.md:14,206 | 改法：全书统一"时间理解"（题型），相近区分能力与同名题型并提处加"（题型）"限定或统一措辞。

### NIT

- **[NIT] ch04·t4-2 | 合计行 292 与分项行对不上账** | 合计"292（评委类 195＋规则类 97）"含 gen 的 42 个探测点，但 gen 行探测列为"—"，读者按行加总 100+96+54=250≠292 | 证据：实测 gen=42 探测（26 score）；lint 输出 195+97=292 | 改法：gen 行探测列补"42"（计分 26），或合计行注明"另含 gen 42"。

- **[NIT] ch07·7.4（ch07-experiments.md:72）| "历史批次（v1.3.2 口径）"与附录 D"tool_version 1.3.1"并读易被当矛盾** | runs 执行工具为 1.3.1，"v1.3.2 口径"指终榜 v3 统计口径；两说各自成立但未点破关系 | 证据：submission-readiness-20261014.md 版本索引（"历史实验 18 轮 runs（tool_version 1.3.1）"）；README 状态节 v1.3.2 | 改法：7.4 首次出现处括注"（runs 工具 1.3.1，统计口径 v1.3.2 终榜 v3）"。

- **[NIT] ch03·3.4（ch03-overview.md:81）| 本机适配器名"claude、qwen"与 ch06 表及 README 的 claude-local/qwen-local 不一致** | 证据：ch06 t6-1；README.md:114 | 改法：统一用 claude-local/qwen-local。

- **[NIT] ch01·2.2.3（ch01-background.md:39）| "五路文献调研"数字存疑** | 沿自 roadmap 标题，但 roadmap 正文为"内部四路＋外部两路"（自相六路） | 证据：docs/audit/roadmap-20261014.md:3-4 | 改法：改"多路文献调研"或"内部四路代码面＋外部两路文献"。

- **[NIT] ch09·9.2（ch08-wrap.md:33）| "适配器只做转发、导出、会话管理四件事"只列三件** | ch06 版本为四件：转发消息/导出记忆/导出操作/管理会话生命周期 | 证据：ch06-engineering.md:25 | 改法：补"导出记忆、导出操作"或去掉"四"。

## 汇总

**MUST 2 条 / SHOULD 5 条 / NIT 5 条。**

总体判断：**可入整改，无需重写任何模块。** 数字主体（题量 181/45/4/72/39/21、两套覆盖矩阵、难度 53/75/53、题型分布、探测点 292=195+97 与各包计分数、canary 37、诱饵 188/188 实跑复核、金标准 23 探测点错判 0 实跑复核、盲评 72/472/52-17-3/seed 20261014、mock 66.1%/22.2% 与 v1 62.2%/41.7%、378 项测试 pytest 实数复核、48 场/1936/seed 20261013、网关 8311/8s/退避、±300s、JUDGE_PROMPT_VERSION=2026-10-04、历史批次全部数字与 p 值、赛题原文引文、四家记忆机制描述、参考文献字段）全部对上仓库出处。两处 MUST 为局部数字顺序错误与题包归属错误，定点改句即可；SHOULD 五条为表述精度与匿名风险，均为句子级修改。

## 附表：核对过的数字清单（报告位置 ↔ 源出处）

### 题库与数据设计

| 数字/断言 | 报告位置 | 源出处/验证方式 | 结果 |
|---|---|---|---|
| 五套题包共 181 题 | ch00:7、ch03:53、ch04:132 | lint 实跑"用例总数: 181" | ✓ |
| full 45 / chains 4（3 条三会话+chain-004 六会话）/ challenge-v2 72（24 模板）/ development-v2 39（13 模板）/ gen 21 | ch04 t4-2 | lint+文件计数（24/13 模板族各 ×3 变体）；dataset-card §1 | ✓ |
| full+chains 100 探测、76 计分（full 87=66 score+21 diag；chains 13=10 score） | ch04 t4-2 脚注 | MemoryCase+probe_role 实测；dataset-card §1/§9、README:55 | ✓ |
| challenge-v2 96（72）、development-v2 54（42） | ch04 t4-2 | 实测 | ✓ |
| 全库 292 探测 = 评委类 195＋规则类 97 | ch04 t4-2 | lint 实跑"judge 探测: 195 / rule 探测: 97" | ✓ |
| 全库 canary 37 题 | ch04:136 | lint 实跑 | ✓ |
| full 集 canary 13 | ch03:53 | dataset-card §3 | ✓ |
| 冒烟 6 题（六能力各一，虚拟集） | ch04:136 | src/memhall/paths.py:48-51（QUICK_IDS） | ✓ |
| held-out 21（seed 4210、prefix h、不入库不入发行包） | ch04 4.4.2 | dataset-card §1；design.md:120；CHANGELOG"exe/deb 排除 heldout" | ✓ |
| gen seed=20260928 | ch04 t4-2 | dataset-card §1；gen_cases.py:334（default=20260928） | ✓ |
| 全库难度 1/2/3 = 53/75/53 | ch04:204 | lint 实跑 | ✓ |
| 全库题型：cross 112 / task_chain 30 / info 11 / false_premise 9 / similarity 9 / session 6 / temporal 4 | ch04:206 | lint 实跑（逐项一致） | ✓ |
| full 题型 6/12/7/2/6/6/6 | ch03 t3-1 | dataset-card §4 | ✓ |
| full 矩阵行 8/9/8/6/8/6、列 6/10/6/9/6/8 | ch03 t3-2 | dataset-card §2；lint | ✓ |
| 全库矩阵行 31/33/32/27/29/29、列 21/41/34/33/20/32；boundary×sensitive=24 | ch04 t4-3 | lint 实跑（逐格一致）；动态更新×路径=12、任务复用×模板=19 亦核 | ✓ |
| 全库能力分布 29/31/29/27/32/33 | ch03:53 | lint=31/33/32/27/29/29 | **✗（MUST-1）** |
| 14 种子 9/36 格＋29 补充 | ch03:53 | dataset-card §2 | ✓ |
| 2 道大容量注入题（教 16 条考 5 条，persist-008/recall-008） | ch03:53、ch04:206 | dataset-card §1 | ✓ |
| 拨钟题 4 道（+2/+3 两档） | ch04 4.3.5 | dataset-card §5 | ✓ |
| 1008 实例=24 模板×42 变体，不写成 1008 独立任务族 | ch04:142 | benchmark-v2"数据如何组织" | ✓ |
| update-001 用例全文（YAML） | ch04 4.1 | cases/full/update-001.yaml 逐字比对（仅省 2 行注释） | ✓ |
| boundary-003 台词/两档契约/判定分支 | ch04 4.3.3 | cases/full/boundary-003.yaml（canary 断言形态除外，见 SHOULD-3） | ✓ |
| path-twin 模板台词 | ch04 4.3.1 | cases/challenge-v2/discriminate-v2-path-twin-0001.yaml | ✓ |
| challenge-v2 六个更新模板 | ch04 4.3.2 | challenge-v2 仅 4 个＋development-v2 2 个 | **✗（MUST-2）** |
| correct 类键名 33 种 | ch04:187 | lint advisory 实跑（correct 33 种） | ✓ |
| lint 六项校验＋参数形状深度校验 | ch04 4.4.3 | scripts/lint_cases.py 文档串 1-15 条＋_ARGS_SHAPE（R47） | ✓ |
| 三旋钮 --distract/--gap-days/--similar（high/mid/low） | ch04 4.4.1 | gen_cases.py:338-342；design.md:120 | ✓ |
| v2 case_id 格式与元数据（family_id/split/模板指纹/seed/变体号/参数） | ch04 t4-1、4.4.1 | challenge-v2 实文件（dynamic_update-v2-replacement-0001 等） | ✓ |
| 试卷：SHA-256 内容 ID、保存拒绝覆盖、封存禁历史轮换、含完整用例 | ch04 4.2 | benchmark-v2"相同试卷怎样交给不同智能体" | ✓ |
| suite.yaml 评分协议 memhall.behavior-v2、加载不执行代码、重复 ID 拒绝 | ch04 4.2 | benchmark-v2；cases/*/suite.yaml 实文件 | ✓ |

### 判卷与质检

| 数字/断言 | 报告位置 | 源出处 | 结果 |
|---|---|---|---|
| 六态名单 correct/omission/confusion/fabrication/over_persist/wrong_reuse＋流程态（运行无效/未决） | ch03 3.3、ch05 t5-1 | 契约 02 §6（dataset-card §6）；models_case FIVE_STATES 六值集 | ✓ |
| update/temporal 答旧值统一 wrong_reuse（R45） | ch03:59、ch04:154 | dataset-card §6 | ✓ |
| score/diagnostic 分流（memory.*/actions.*→diagnostic、跨族 canary→diagnostic、boundary 族 canary→score） | ch03 3.3、ch05 5.5 | models_case.py:136-166 probe_role | ✓ |
| 故障定位四态（没存/存了没用上/存错了/该删没删） | ch03 3.3、ch05 5.5 | README 评分口径 v3 | ✓ |
| JUDGE_PROMPT_VERSION 当前 2026-10-04、随 manifest 落账 | ch05 5.2 | judge.py:222；cli.py:89（judge_info）；engine.py:111（JudgeMeta） | ✓ |
| 脚本判卷三层锚例（尾段命中/关键值全中/重叠率≥0.8）＋同模板异值不按锚例判 | ch05 5.2 | judge.py:132-163 | ✓ |
| 拒答值门（期望/锚例值特征字符全缺才判遗漏） | ch05 5.2 | judge.py:175-190（P1-4 注释） | ✓ |
| 超串近形（前缀互含异值）脚本不定案转下层 | ch05 5.2 | judge.py:83-96,168-171（R26） | ✓ |
| 金标准取自 2026-09-28 对 23 个探测点三方人工审计＋构造边缘样本（并列/真话豁免/泄露口令） | ch05 5.3.1 | judge_selftest.py:3 文档串＋GOLD 十例 | ✓ |
| 离线门禁错判 0 进 CI；在线符合率目标 ≥95% | ch05 5.3.1 | judge_selftest.py --offline 实跑（错判 0）；ci.yml:62-63 | ✓ |
| 诱饵四类 188 条拒收 188（100%，>95% 门禁） | ch05 5.3.2 | judge_decoy.py 实跑："诱饵件：188 个…拒绝率 100.0%"；R26 | ✓ |
| 盲评：72=六维各 12、seed 20261014、框 472（14/18 轮、judge 判定语义探测非降级）、35 题族、52/17/3、over_persist 框内 6 入样 0、三家 36/18/18、awaiting_human_labels 不出一致率 | ch05 5.3.3 | docs/audit/blind-review-20261014/README.md 全字段；CHANGELOG E 档 | ✓ |
| 18 轮 4 轮证据残缺被排除 | ch05:11、ch08 附录 A | dataset-card §9；blind-review README 排除表 | ✓ |
| mock：full 66.1%（62/66 计分探测有效）、chains 22.2%；v1 口径 62.2%/41.7%，差异属口径变更 | ch05 5.4 | dataset-card §8"mock 基线" | ✓ |
| 降级判定保留不计分、未决/降级按错计保守下界 | ch05 5.2/5.5 | metrics.py:171；benchmark-v2"比较与统计的条件" | ✓ |
| 人工复核率为报告指标、设计目标 <5% | ch05 5.2 | design.md:28 | ✓ |
| 四类证据＋SHA256 封印＋时钟偏移入账＋对话含耗时与 token＋导出失败不折叠空库＋actions 覆盖度标注 | ch05 5.1 | schema/evidence.py（latency_ms/token_usage/clock_offset_days/payload sha256/dump_ok/coverage）；runner orchestrator.py:214-235（inject+probe 双快照） | ✓ |
| 判卷口径无"双评委/仲裁/JUDGE_B"残留 | 全文扫描 | 仅 ch05:27"为何单评委"设计取舍与 ch07:76"评委 B 未配置"披露，均属合规例外 | ✓ |

### 工程实现

| 数字/断言 | 报告位置 | 源出处 | 结果 |
|---|---|---|---|
| 九适配器名单与驱动形态（hermes chat --oneshot / kylin-bot agent -m / openclaw agent exec / kylin-agent-runtime -z / claude -p / qwen 单发剥 hooks / opencode run / hermes-local HERMES_HOME / mock） | ch06 t6-1 | 各 adapters/*.py docstring 逐一核对（hermes.py:7、kylinbot.py:4,9、openclaw.py:6-12、kylin_agent.py、claude_local.py、qwen_local.py、opencode.py） | ✓ |
| 记忆证据面（hermes/kylin-agent memories 两文件+agent.log、kylinbot brain.db SQLite+FTS5 含版本链、openclaw 沙箱 sqlite 记忆索引、claude auto-memory+CLAUDE.md、qwen QWEN.md、opencode AGENTS.md） | ch06 t6-1 | 同上 docstring；dataset-card §8 A8 妥协清单 | ✓ |
| 网关：监听 8311、转发最小间隔默认 8s、429/5xx 指数退避尊重 Retry-After、真 key 只在网关进程、dummy key memhall-<agent> 归因且不写日志不转发、改写表发车写收尾清、三态对账 verified/suspect/unknown | ch06 6.3 | gateway.py:43（DEFAULT_PORT=8311）、:303（默认 8.0）、_backoff_delay/_retry_after_seconds、:91-99（R34）、CHANGELOG 2.0.0 A 档 | ✓ |
| claude 本机车道 anthropic 协议不进统一车道、配网关显式报错 | ch06 6.3 | gateway.py 文档串；README:147 | ✓ |
| bwrap 沙箱：整根只读＋白名单（agent 自家目录/注入工作区/产物根）、/tmp ~/.cache ~/.npm tmpfs、默认关、MEMHALL_SANDBOX=1、状态入 manifest | ch06 6.4 | CHANGELOG B8；roadmap A 档 | ✓ |
| 拨钟恢复含单调耗时（/proc/uptime）；宿主-评测机钟差超 ±300s 拒跑（MEMHALL_CLOCK_DRIFT_MAX 可调） | ch06 6.4 | orchestrator.py:367-371（默认 300.0）；CHANGELOG A1/B 档 | ✓ |
| run 互斥锁 ~/.memhall/run.lock（CLI 与 UI 互斥） | ch06 6.4 | orchestrator.py:776-785 | ✓ |
| 18 项隔离守卫测试（互斥锁/拨钟往返与失败路径/错钟闸门/网关对账/reset 清旧） | ch06 6.4/6.5 | CHANGELOG A4（tests/test_isolation_guards.py 18 项） | ✓ |
| CI：ubuntu+windows × 3.11/3.12，ruff/mypy＋题库门禁＋金标准离线自检＋全量测试 | ch06 6.5 | .github/workflows/ci.yml 逐 job 核对 | ✓ |
| 全回归 378 项（293+80+4+1）＋离线 mock e2e 全链路 | ch06 6.5、ch08 附录 D | pytest --collect-only 实测=**378 tests collected**；CHANGELOG 2.0.0 | ✓ |
| 真机系统级测试四项 hermes 4/4 | ch06 6.5、ch08 其八 | README:87 | ✓ |
| deb：产物写 ~/memhall-runs、配置 ~/memhall.env、dpkg -r 卸载、UKUI 菜单项、离线 wheels、目标机原生构建、v2.0.0 最终包重建中 | ch06 6.1 | README:171-180；05-vm-full-rerun 阶段二"在 openKylin 原生重建 .deb" | ✓ |
| v2.0.0 单文件 exe 已打包冒烟 | ch06 6.1、ch08 附录 A/D | dist/MemHall-onefile-v2.0.0.exe 存在；release-ready.json | ✓ |
| 五类产物与 doctor 三路体检 | ch06 6.1 | README:106-115 | ✓ |
| UKUI 收尾通知＋雷达图弹出 | ch06 6.1 | README:87 | ✓ |

### 实验与统计

| 数字/断言 | 报告位置 | 源出处 | 结果 |
|---|---|---|---|
| 主矩阵 4 家×3 题包×4 轮=48 场、1936 次 case；附加臂 dev39+gen21 各 1 轮=8 场 240 次 | ch07 7.1 | build/judge-review-20261014/parallel-prompts/05-vm-full-rerun.md:34（逐字一致）；算术复核 16×121=1936、4×60=240 | ✓ |
| 冻结试卷 seed 20261013、sandbox=1、统一 qwen3.7-plus、三包分别汇报不拼总分、每场 verify | ch07 7.1 | 05-vm-full-rerun.md:33-61 | ✓ |
| 评测机对时含宿主独立 UTC 参考、不用机内两钟互证 | ch07 7.1 | 05-vm-full-rerun.md:16 | ✓ |
| chains 只覆盖长期保持/任务复用两维、单独成榜 | ch07 7.3 | README:55 | ✓ |
| 历史三家 74.6%±2.8 / 72.5%±4.9 / 58.5%±6.8 | ch07 7.4 | README:47-51 | ✓ |
| 探索性 p：hermes-openclaw ≈0.752、hermes-kylinbot <0.001；聚类复核 0.163/0.121/0.572 三对不显著 | ch07 7.4 | README:53；dataset-card §9 | ✓ |
| 头部差距约 2.1 个百分点、近似功效约 58 轮 | ch07 7.4 | README:53 | ✓ |
| chains 4×2 轮 hermes/openclaw 均 40.0%±0.0 并列 | ch07 7.4 | README:55-61 | ✓ |
| 初版终榜 67.1>52.4>44.4、三处评测器缺陷、修复后 hermes 73 上下、废轮留痕不删 | ch07 7.4 | README:72（演进注记） | ✓ |
| 行为画像：hermes 边界 83% 三家最高±11 波动；openclaw 调用/复用 77/89、边界 40%、over_persist 两轮 12/87；kylinbot 更新 17%、相近 47%、边界 75%±5 | ch07 7.5 | README:70 | ✓ |
| 评委 B 因网关仅授权 qwen3.7-plus 未配置、同模型偏袒风险披露、校准出口=盲评 | ch07 7.4 | README:36（审计更正）；CHANGELOG 2.0.0 判卷定版 | ✓ |
| 正式成绩/mock 画像/CI/延迟/token 记账全部〔待填〕 | ch07 | 待填项均为 D 矩阵真实待补数据，未发现用旧数冒充 | ✓ |

### 赛题原文与文献

| 数字/断言 | 报告位置 | 源出处 | 结果 |
|---|---|---|---|
| 赛道名/赛题名/三个不足/六能力名/最终目标三句/"重点关注…稳定区分…"引文/"区分正确记忆、遗漏、混淆、错误持久化和错误复用" | ch01、ch03 3.3、ch05 5.5、ch08 其三 | official-challenge-current.txt:1-84 逐句核对（含 :69 评分细则五分法、:84 评审总纲） | ✓ |
| LoCoMo：10 段、1,986 QA、平均 300 轮、最多 35 会话、CC BY-NC 4.0、arXiv:2402.17753 | ch01 2.2.1、参考[1] | 调研文档:42,66 | ✓ |
| LongMemEval：ICLR 2025、500 题、5 能力（抽取/多会话推理/时序/更新/拒答）、7 题型、每题证据会话标注、MIT、arXiv:2410.10813 | ch01 2.2.1、参考[3] | 调研:41,64 | ✓ |
| MemoryAgentBench：Mem0 18%、Zep 7%、最强 RAG 54%；FactConsolidation；arXiv:2507.05257；ICLR 2026；4 能力 | ch01 2.2.1、参考[4] | 调研:47,68 | ✓ |
| MPBench：写入检查＋延迟复用两阶段、Hermes ASR 66.67%、OpenClaw 34.25%、写入通道越多投毒面越大、arXiv:2606.04329、未发现开源代码 | ch01 2.2.1、参考[6] | 调研:24,54,70 | ✓ |
| LoCoMo 审计：放过 63% 故意错答、6.4% 答案键错误、dev.to 2026-04 | ch01/ch05/ch08、参考[2] | review-tasks.md:118 | ✓ |
| MemBench github import-myself/Membench、arXiv:2506.21605 | 参考[5] | 调研:69；docs/audit/review-20261013/MemHall-题库与扩展设计评估.md:142 | ✓ |
| GAIA 2311.12983 / tau-bench 2406.12045 / OSWorld-Verified xlang.ai / bubblewrap containers/bubblewrap | 参考[9][10][11][13] | review-tasks.md:118；容器官方仓库 | ✓ |
| 四家记忆机制：OpenClaw 纯 Markdown＋每日日志＋SQLite 混合检索；Hermes 全量注入无检索层；KylinBot 应用内存储前端可视编辑；kylin-agent 后端与 Hermes 同构 | ch01 2.2.2 | 调研:13-16（与 ch06 表"openclaw 沙箱 sqlite 记忆索引"为不同层面口径——产品机制 vs 车道证据面，各自成立） | ✓ |
| MEMPROBE/OmniMemEval 出版字段未核实如实标"暂缺" | 参考[7][8] | 调研留档即无完整字段；合规不编造 | ✓ |

### 版本与匿名

| 数字/断言 | 报告位置 | 源出处 | 结果 |
|---|---|---|---|
| 本报告对应 v2.0.0（2026-10-14）；Release v1.3.2 修复前构建不代表当前源码；deb 最终包随目标机验收重建；历史 18 轮 tool_version 1.3.1、14 轮过核验 | ch06 6.1、ch08 附录 A/D | CHANGELOG 2.0.0；submission-readiness 版本索引；release-ready.json | ✓ |
| demo-script 0:00–4:30 逐段、视频待录、不得以 mock 顶替 | ch08 附录 A e | docs/demo-script.md（0:00–0:30…4:10–4:30）；submission-readiness e 行 | ✓ |
| 离线样例 6 题 14 探测 verify 通过；real-evidence-sample 存在 | ch08 附录 A/C | examples/20261013-085255-mock/ 实测（6 case、verdicts 14 条、六能力各一）；submission-readiness | ✓ |
| 开源治理：Apache-2.0、双托管、CONTRIBUTING/CODE_OF_CONDUCT/SECURITY/issue 与 PR 模板在库 | ch09 9.2 | README:263-266；.github/ISSUE_TEMPLATE（bug_report/feature_request）＋PULL_REQUEST_TEMPLATE.md 实存 | ✓（账号名见 SHOULD-4） |
| 路线图三项（读侧网关探针/auditd 接线/sandbox=0 与 oracle 对照臂） | ch09 9.2 | roadmap B1/B 档/C 档 | ✓ |
| 匿名扫描（学校/姓名/导师/Windows 用户名/主机名/IP/凭据/队友 ID） | 全文 | grep 扫描：仅 mazhuoran23 一处（SHOULD-4），其余零命中（okim/leeyu/weilai/192.168/VM_PASS/sk- 等均无） | △ |
