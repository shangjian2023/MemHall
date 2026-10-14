# 第二轮复审 · 整改核销（round2-verify）

> 审核人：round2 整改核销（只读；本文为唯一写入文件）。
> 对象：提交 713d9ff「报告第一轮审核整改——三份审核 45 条发现全部处置」对 src 八模块 + build_docx.py 的全部改动。
> 方法：逐条比对 round1 三份报告（facts/consistency/style）与 HEAD~1…HEAD diff 及当前文本；全书 grep 残留扫描；重跑 `build_docx.py --all`；python-docx 抽查渲染成品。日期：2026-10-14。

## 一、逐条核销台账

结论标记：[FIXED] 已修且正确｜[REGRESSION] 修复引入新问题（详第二节）｜[OPEN] 未修｜[DEFERRED] 有意不改且合理。

### MUST（三份去重 7 条，全部核销）

| # | 发现（原出处） | 结论 | 位置/证据 |
|---|---|---|---|
| M1 | ch03 六能力分布序列错 29/31/29/27/32/33（facts·M1＝cons·M3） | [FIXED] | ch03:53 现为 31/33/32/27/29/29，与 ch04 t4-3 行计、figures-registry fig:matrix36 备注一致；docx 正文已含该序列 |
| M2 | "challenge-v2 的六个更新模板"归属错误（facts·M2） | [FIXED] | ch04:154 改为"v2 题包共六个更新模板：challenge-v2 四个＋development-v2 另有两个"，与 cases/ 两包实文件对齐 |
| M3 | "五态判定"两处（cons·M1） | [FIXED] | ch01:19、ch04:206 均改"六态"；全书 grep"五态"零命中 |
| M4 | 七题型中文译名三套写法（facts·S5＝cons·M2） | [FIXED] | t3-1/ch03:30/ch04 t4-1/ch04:206 全部统一为 时间理解/相近区分/会话内召回。残留核查：grep"时间推理"仅 ch04:164 标题"temporal 拨钟：相对时间推理"——机制描述非题型名，不算残留；"相似干扰"4 处（ch00:5、ch01:17、ch04:148、ch08:7）均为场景词/设计名，其中 ch01:17 属赛题场景表述按约定保留；裸"会话召回"零命中 |
| M5 | 孤儿表 t4-1-schema、t4-2-suites 零引用（cons·M4＝style·M3） | [FIXED] | ch04:7 补"见表~t4-1-schema~"、ch04:122 补"见表~t4-2-suites~"；附带 ch04:191 补 t4-3-stats 引用、:193 表题补"（全库 181 题）" |
| M6 | matrix36 图题缺"全库 181 题口径"（cons·S2＝style·M1） | [FIXED] | ch03:51 图题已含"（全库 181 题口径）"；docx 渲染抽查命中 |
| M7 | Gitee 个人账号名 mazhuoran23（facts·S4＝style·M2） | [FIXED] | ch08:33 改"GitHub 与 Gitee 双托管（仓库地址见 README 与交付材料）"；report-draft 全目录 grep mazhuoran23 零命中（review/ 自身除外） |

### SHOULD（去重 15 条）

| # | 发现（原出处） | 结论 | 位置/证据 |
|---|---|---|---|
| S1 | ch03"每格 2–3 题"与矩阵矛盾（facts·S1） | [FIXED]* | ch03:53 改"重点格刻意加厚——full 集动态更新×路径与边界×敏感各 3 题……"；主体方向正确，但新句末"其余格 1–2 题"与同章 t3-2 的 记忆调用×事实=3 矛盾 → 见 R1 |
| S2 | ch08"实接真机四家"过度声明（facts·S2＝cons·S8） | [FIXED] | ch08:5 改"hermes、kylinbot、openclaw 已 VM 实测联调，kylin-agent 已接入、待真机验收"，与 ch06:27-29 诚实声明及 README 口径一致 |
| S3 | boundary-003 canary 分支机制半对（facts·S3） | [FIXED] | ch04:158 改为 8080=曾经包含、canary 串"多数边界题曾经包含，本题 canary 分支为特例只查最新快照（严格度不同如实标注）"——与 cases/full/boundary-003.yaml 及 rules.py 语义逐点相符 |
| S4 | ch07:43 内部代号"等 D 的"泄漏（cons·S1） | [FIXED] | 改"待正式矩阵（sandbox=1，四家）出数后回填" |
| S5 | 摘要"均值±置信区间"口径不一（cons·S3） | [FIXED] | ch00:9 改"均值±样本标准差与 bootstrap 置信区间"，与 ch07:43/:70 一致 |
| S6 | 历史批次"v1.3.2 口径"vs"tool_version 1.3.1"（facts·N2＝cons·S4） | [FIXED] | ch07:3 与 :72 双处括注"归档 tool_version 1.3.1，统计口径为 v1.3.2 终榜 v3"；附录 D :121 同口径，三处互洽。7.5:86 保留简称"v1.3.2 口径"属定义后简称，可接受 |
| S7 | ch06"五类产物"缺证据 JSONL（cons·S5） | [FIXED] | ch06:9 改"六类产物……与逐题证据 JSONL"，与 ch03:81、ch05:95 清单一致 |
| S8 | t4-2 gen 行探测列"—"致合计对不上（facts·N1＝cons·S6） | [FIXED] | ch04:131 gen 行改 42（26）；加总 100+96+54+42=292 闭合，docx 表格实测确认 |
| S9 | outline 承诺"mock 三平台逐字一致"无实证（cons·S7） | [FIXED]（撤） | outline 对账表已删该项并注明"无仓库实证，已从承诺撤下"；src 未引入相关表述 |
| S10 | ch04:134 裸星号脚注原样渲染（style·D1） | [FIXED] | 改括注式"（full 与 chains 合计：……）"；docx 段首裸星号 0 处 |
| S11 | qc-trio 状态与登记表不一致（style·D2） | [FIXED] | ch05:35 改"待出数"，与登记表 B 类预留位语义取齐 |
| S12 | ch03 3.3 缺证据收尾（style·D4） | [FIXED] | ch03:65 新增"逐态落位见表~t5-1-verdicts~……金标准错判 0、诱饵 188/188 在 5.3 展开"；表 ID 真实存在（ch05:61），builder 交叉引用全解析 |
| S13 | ch06 6.1 缺结果证据（style·D4） | [FIXED] | ch06:11 补 exe 冒烟实测内容（版本自检/doctor/干净目录 mock 全链路 verify 通过） |
| S14 | ch06 6.3 缺结果性收尾（style·D4） | [FIXED] | ch06:54 补"三态对账块随每场评测写入 manifest，对账未过的场进不了同一张榜"——机制+榜级纪律收尾，正式矩阵出数即成实测落点 |
| S15 | 页幅缓解：26 图后估 45–50 页（style·D6） | [DEFERRED] | 合理：主体动作（B 类双联子图、YAML 节选）须等图到位。其中可先行的"6.1 三条上手命令与附录 B 重复"已做（ch06:19 改指向附录 B），但引入 R2 |

### NIT（去重 23 条，含同义词对拆分）

| # | 发现（原出处） | 结论 | 位置/证据 |
|---|---|---|---|
| N1 | ch00"3 题包×4 轮"省 ×4 家（cons） | [FIXED] | ch00:9 现为"4 家 × 3 题包 × 4 轮" |
| N2 | ch05:73 62.2/41.7 缺"分别为"（cons） | [FIXED] | 已补"分别为" |
| N3 | ch03"13 个 canary"口径（cons） | [FIXED] | 已加"（full 种子口径）" |
| N4 | t4-2"场景模板"列文数混搭（cons） | [DEFERRED] | 合理：该列信息本身异质（full/chains 无模板数、gen 为生成制），强行数值化反而损信息；排版偏好级 |
| N5 | fabrication 口语注"瞎编"vs 正式名"编造"（cons） | [FIXED] | t5-1 定名"编造"（ch05:67）、ch05:89 用"编造"；ch03:59 括注保留"瞎编"并注明"答出编造内容"，两处互认；题内 rubric 引文按约定不动 |
| N6a | "操作记录"→"行动轨迹"（cons） | [FIXED] | ch03:9/11/63/65、ch08:33 全改；grep"操作记录"零命中。ch04:20 断言链内"操作"、ch06:25"导出操作"为适配器短语，按约定不算 |
| N6b | "试卷/考卷"混用（cons） | [OPEN] | ch04:122"考卷统一"、:176"不可见的考卷"两处仍用"考卷"，全书其余均"试卷"。低危 |
| N6c | "锚例/锚定例"混用（cons） | [OPEN] | 锚定例 6 处（ch03:61、ch04:20/116/187、ch05:21/23）、锚例 7 处（ch04:118/187、ch05:19×3/:41/:71、ch08 附录C）。ch05:21 有"锚例是什么：……自带'锚定例'"定义句兜底，读者不致迷路，但未按 round1 建议统一其一。低危 |
| N6d | "claude、qwen"适配器名（facts·N3） | [FIXED] | ch03:81 改 claude-local、qwen-local，与 t6-1/README 一致 |
| N6e | "Hermes Agent"vs"Hermes"（cons） | [FIXED]（合理保留） | ch01:5/:35 的"Hermes Agent"是赛题原文用词（official-challenge-current.txt:9"Hermes Agent 等"），首提全称后文简称属规范写法，不改正确 |
| N7 | t4-3 表题未标口径、本章零引用（cons） | [FIXED] | 见 M5 附带项 |
| N8 | ch99-selftest 复用图 ID 需构建排除（cons） | [FIXED] | build_docx.py:419 `if "selftest" not in p` 已排除 |
| N9 | ch03 3.3 与 ch05 5.5 fail-closed 两句级重叠（cons） | [DEFERRED] | round1 自评"可接受，统稿时酌情收敛"；现 ch03:65 收尾已转向证据引用，重叠度进一步下降 |
| N10 | LoCoMo"63%"同章两次（cons） | [DEFERRED] | round1 自评"叙事钩子可接受" |
| N11 | "以〔待填〕占位"等草稿元话语（cons） | [DEFERRED] | by-design：随 D 矩阵出数回填时一并清除，现清除会在正文留下无来源空档 |
| N12 | ch01"五路文献调研"数字存疑（facts·N4） | [FIXED] | 改"内部四路代码面加外部两路文献"，与 roadmap 正文一致 |
| N13 | ch08"四件事"只列三件（facts·N5） | [FIXED] | ch08:33 补全"转发消息、导出记忆、导出操作、管理会话生命周期"，与 ch06:25 一致 |
| N14 | ch07:96 直角引号「」（style·D5） | [FIXED] | 改弯引号；全书直角引号 0 命中 |
| N15 | 封面赛道名杜撰"开源贡献赛"（style·D5） | [FIXED] | build_docx.py:390 改"openKylin 赛题 · 开源 AI 工具赛道"；docx 封面首行渲染确认 |
| N16 | 目录空白 TOC 域需 Word 刷新（style·D1） | [DEFERRED] | 操作提醒非文件缺陷，导 PDF 前执行 |
| N17 | 登记表 B 类图题近义改写回写（style·D2） | [DEFERRED] | 终稿动作（一处真源收口） |
| N18 | ch02/2.1 三段收尾均为交叉引用（style·D4） | [DEFERRED] | round1 自评"背景章可接受" |
| N19 | 图位时间风险：9 张 A 类未落盘（style·D6） | [DEFERRED] | 管理动作（等图/催图），非文本问题 |

## 二、回归（整改引入的新问题，2 处，均低危、一句话级）

**R1｜ch03-overview.md:53｜"其余格 1–2 题"与同章 t3-2 表矛盾。**
新句"重点格刻意加厚——full 集动态更新×路径与边界×敏感各 3 题……其余格 1–2 题"。实测 full 45 矩阵值为 3 的格有三处：动态更新×路径=3、边界×敏感=3、**记忆调用×事实=3**（ch03 t3-2:44、dataset-card §2 recall 行 fact=3、lint 实测一致）。"其余格 1–2 题"漏了第三格，评审对表即见。溯源：round1 审核员建议文本自带此疏漏，整改照抄未对表复核。改法（三选一）：①"动态更新×路径、记忆调用×事实与边界×敏感各 3 题，其余格 1–2 题"；②删"其余格 1–2 题"半句；③改"其余格至多 3 题"。

**R2｜ch06-engineering.md:19｜"三条命令"清单与附录 B 不再字面一致。**
整改前 ch06 直接列 `uv sync → doctor → run`，与附录 B 完全一致；整改后改括注"（doctor 体检 → mock 试跑 → verify 对账，全文见附录 B 复现指南）"，而附录 B 的"三条命令"= uv sync / doctor / run（verify 在块外散文）。两处各称"三条命令"但成员不同（ch06 含 verify 无 sync；附录 B 含 sync 无 verify）。无事实错误、仅读者小困惑。改法：ch06 括注改"（装依赖 → 体检 → mock 试跑，全文见附录 B）"即可对齐。

## 三、专项复核（任务单 a–j）

| 项 | 结果 |
|---|---|
| a 题型译名统一 | 通过。grep"时间推理"仅 ch04:164 标题"相对时间推理"（机制描述）；"相似干扰"4 处均场景/设计词，ch01:17 按约定保留；裸"会话召回"0 |
| b ch03:53 与 t3-2 自洽 | 序列 31/33/32/27/29/29（全库）与加厚句（full 口径）分句表述、口径标注清楚；**唯"其余格 1–2 题"半句与 t3-2 矛盾 → R1** |
| c ch04:154 模板归属 + :158 canary | 均精确。:154 四+二归属与两包实文件一致；:158 三种断言形态（8080=ever_contained、本题 canary=contains 特例、他题 canary=ever_contained）与 cases/rules.py 逐点相符 |
| d t4-2 合计对账 | 通过。100+96+54+42=292 闭合；docx 表格实测 gen 行"42（26）"、合计"292（评委类 195＋规则类 97）" |
| e 匿名 mazhuoran23 | 通过。report-draft 全目录（除 review/ 自身）grep 零命中 |
| f ch00/ch07 口径统一 | 通过。均值±样本标准差+bootstrap CI、4 家×3 题包×4 轮、历史批次双口径括注三处（ch07:3/:72、附录 D:121）互洽 |
| g t5-1-verdicts 引用 + builder | 通过。ch03:65 引用的表 ID 存在于 ch05:61；`build_docx.py --all` 一次通过，输出"图 26 个（占位 26）｜表 13 张｜待填标记 34 处"，与预期完全一致，交叉引用零残留（docx 内 `~id~` 0 处） |
| h ch06:19 → 附录 B 三条命令 | 命令本身齐全：附录 B 含 uv sync --group dev / doctor / run -a mock 三条 + verify 散文；**但两处"三条"成员清单不一致 → R2** |
| i "操作记录"残留 | 通过。全书 0 处；ch04:20"操作"、ch06:25"导出操作"为适配器短语，按约定不算 |
| j 封面行渲染 | 通过。docx 首段实测"openKylin 赛题 · 开源 AI 工具赛道" |

## 四、汇总与最终判断

**核销结果：45 条发现（去重后）＝ FIXED 34 / OPEN 2 / DEFERRED 9；另查实 REGRESSION 2。**

- FIXED 34：MUST 7 条全清，SHOULD 14/15 清（页幅缓解转 DEFERRED），NIT 13 条清。
- OPEN 2（N6b 试卷/考卷、N6c 锚例/锚定例）：均为低危同义词残留，各有定义句或语境兜底，不误导评审；建议回填数字时顺手统一（考卷→试卷 2 处；锚定例为全称、锚例为简称，统一叙述用词或保留 ch05:21 的定义句均可）。
- DEFERRED 9：全部有明确归属时点且合理——等图（S15、N19）、终稿（N17）、回填随行（N11）、导 PDF 操作（N16）、统稿酌情（N9/N10/N18）、排版偏好（N4）。
- REGRESSION 2（R1/R2）：均为一句话级，R1 建议必须修（同章文字与表格直接矛盾，评审必抓），R2 可选修。

**最终判断：达到"可交队友配图 / 等 D 出数回填"状态。** 整改覆盖面与精度总体扎实——7 条 MUST 全部修对，digit-level 抽查（分布序列、canary 分支、模板归属、合计对账、双口径括注、译名统一、匿名）无一错改；builder 全量渲染通过且计数与预期逐项吻合。回填前需处理的仅三件小事：R1 一句改写（必改）、R2 括注对齐（顺手）、OPEN 两条同义词（可选）。其余等图、等数、终稿收口即可，无需再动结构。
