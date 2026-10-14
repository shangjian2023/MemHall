# 报告配图登记表（语义 ID = 全书唯一，builder 据此自动编号）

> 用户已开工 9 张（状态：绘制中）。其余为预留位：真机截图（C 类）等 D 录屏时截；数据图（B 类）等 D 全量矩阵出数。
> PNG 终稿命名：`docs/contest/report-draft/figures/<语义ID>.png`（到位即自动嵌入；300dpi、中文标注、主色麒麟蓝 #3790FA、数据系列 Okabe-Ito、截图抹凭据/主机名）。

## A. 用户正在画的 9 张（P0 骨架）

| 语义 ID | 图题 | 章·节 | 内容要点 | 状态 |
|---|---|---|---|---|
| `fig:landscape` | 同类记忆评测方案横向对比 | 2.2 | 我们 vs LoCoMo 系/OmniMemEval/各家自带记忆横评：维度=被测对象（记忆模块 vs 完整框架）、证据锚（人工标注 vs 运行证据）、判卷（LLM 裸判 vs 规则+单评委+质检）、复现（— vs 冻结试卷+封印）——突出"完整框架黑盒横评无先例"空位 | 绘制中 |
| `fig:arch` | 麟阁总体架构 | 3.4 | 六层自下而上：被测智能体（四家，bwrap 沙箱内）→ 适配器层（统一接口+证据采集）→ runner（三阶段剧本/互斥锁/UTC）→ 证据层（四证据 SHA256 封印）→ 判卷层（规则+单 LLM 评委+质检三件套）→ 报告层（六维/雷达/pass^k/verify）；旁注一条命令全产物 | 绘制中 |
| `fig:phases` | 三阶段剧本时序 | 3.1 | 单 case 教→隔→考时间线：会话切换点、拨钟注入点、四类证据采集时点、reset 边界 | 绘制中 |
| `fig:matrix36` | 能力×内容 36 格覆盖热力图（全库 181 题口径） | 3.2 | 数据源=lint_cases.py 全库实测（2026-10-14 总控复跑，校验通过、36 格无空格）：persist 行 7/9/5/5/4/1、recall 4/5/4/12/4/4、dynamic_update 4/12/1/7/7/1、discriminate 4/10/4/7/1/1、boundary 1/1/1/1/1/24、reuse 1/4/19/1/3/1（列序 preference/path/template/fact/project_state/sensitive；行和 31/33/32/27/29/29，列和 21/41/34/33/20/32）。注意：第三章 t3-2-matrix 表是 full 45 正式题包口径，图题须标"全库 181 题"区分 | 绘制中 |
| `fig:pipeline` | 判卷三级流水线与质检三件套 | 5.2 | 证据→规则判卷（锚例三层命中+拒答值门）→LLM 评委（锚例进提示词，一票定案）→human_review 出口；右侧挂质检三件套（金标准 0 错判/诱饵 188 拒收/人工盲评 72 条） | 绘制中 |
| `fig:evidence` | 证据链与封印审计 | 5.1 | case.yaml→证据 JSONL→verdicts→metrics→output_hashes；verify 哈希复算、残缺轮无法混入 | 绘制中 |
| `fig:gateway` | 统一模型网关原理 | 6.3 | 智能体（dummy key memhall-\<agent\>）→网关（模型改写表/逐请求记账 jsonl/限速退避）→上游 qwen3.7-plus；(agent,model) 二元组=对照仪器；记账归因 tag | 绘制中 |
| `fig:sandbox` | bwrap OS 沙箱写面隔离 | 6.4 | 整根只读+白名单可写（agent dotdir+~/memhall-artifacts）+tmpfs(/tmp、~/.cache、~/.npm)；出口过滤未实装如实标注 | 绘制中 |
| `fig:topology` | 实验环境拓扑 | 7.1 | 宿主（网关 8311/relay）—VM openKylin 3.0（四家 CLI、runner 源码车道 ~/memhall-releases）—上游；标 sandbox=1/统一模型/UTC 对时 | 绘制中 |

## B. 预留位（正文引用用同样语义 ID，图位留白）

| 语义 ID | 图题 | 章·节 | 来源/时点 |
|---|---|---|---|
| `fig:radar-final` | 终榜四家雷达 | 7.3 | D 矩阵出数后 make_readme_figures 重出 |
| `fig:bars-final` | 六维均值±样本 std | 7.3 | 同上 |
| `fig:ci-final` | bootstrap CI+聚类稳健检验 | 7.4 | 同上（并列如实标"不显著"） |
| `fig:verdict-mix` | 判定构成（错误集中在哪维） | 7.4 | panels 重出 |
| `fig:mock-baseline` | mock 缺陷注入基线 vs 真实 | 7.2 | 四家版重做 |
| `fig:passk` | pass^k 分母/覆盖率 | 7.4 | 新画小图 |
| `fig:ui-launch` | Web UI 发车页 | 6.1 | D 真机截图 |
| `fig:ui-live` | 直播流（inject/probe/拨钟行） | 6.1 | 同上 |
| `fig:ui-report` | 报告详情页（雷达+人工复核入口） | 6.1 | 同上 |
| `fig:desktop` | openKylin 桌面+UKUI 菜单项+deb 安装 | 6.1 | 同上 |
| `fig:cli-run` | CLI 一条命令全产物+verify 通过 | 5.5/6.1 | 同上 |
| `fig:kylin-evidence` | kylin-agent 真机证据（记忆目录/网关记账行） | 6.2 | D 验收记录 |
| `fig:ev-pair` | 教→考证据对（inject+probe+判据展开） | 5.4 | runs 归档挑选 |
| `fig:ev-memory` | 记忆文件写入前后 diff | 5.4 | 同上 |
| `fig:ev-boundary` | 边界陷阱案例（拒答 vs over_persist） | 5.4 | 同上 |
| `fig:blind-review` | 盲评工作表（72 条双人抽样） | 5.3 | E 已导出直接截 |
| `fig:qc-trio` | 质检三件套量化结果 | 5.3 | selftest/decoy 输出可视化 |

## 编号说明

builder 按各语义 ID 在整本正文中的**出现顺序**分配连续图号（图 1、图 2 …，貔貅口径），模块写作时禁止手写"图 3-1"式编号，正文引用一律写 `图~语义ID~`，渲染时替换为实际编号。
