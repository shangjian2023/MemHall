# Changelog

## [2.0.0] — 2026-10-14

首个"可信评测工程"完整版：本轮（judge-review-20261014）评审修复 + 以下各
Unreleased 节（A 档 A1–A8、B8 OS 沙箱、kylin-agent 适配器、题库与可复现
评测升级、参赛审计修复）随本版本一并发布。

评审修复轮（五项已复现缺陷 + 两项口径漏洞 + kylin-agent 身份误标，独立
复现取证见 build/judge-review-20261014/）：

- **runner/gateway（A）**：run 起点改存发车时 aware UTC（+8 宿主下不再差
  8 小时，兼容历史紧凑戳/后缀）；模型改写移入互斥锁内、全生命周期 finally
  恢复旧值（锁拒绝/错钟预检拒绝零残留）；网关对账改账单追加序号归因（与
  系统钟无关，拨钟 case 亦可精确对账），三态结论
  verified/suspect/unknown——账单读不到是 unknown，不再冒充无告警；
  旧题请求不再掩盖新题零请求；时间比较全部解析 ISO/时区。
- **VM 适配器（B）**：时钟恢复按首基准 epoch + 单调钟增量（/proc/uptime）
  回拨，不再丢真实耗时（旧实现每 case 白吃一段耗时，攒过 ±300s 闸门拒跑
  下一场）；多次 shift/失败可重试语义明确；~/memhall-artifacts 产物根进
  沙箱可写白名单与三家 reset 清理（残留文件不再吃掉 fs_diff 的 created
  证据）；Hermes.__init__ 网关身份按 self.name 取，子类不串账。
- **口径与统计（C）**：check_compatibility 纳入 environment.sandbox 与真实
  agent_version（明确不一致拒绝混合，缺字段判 unknown 拒绝 verified=true，
  不默认当 sandbox=0/版本相同）；pass^k 增完整分母账本 pass_k_detail
  （eligible/完整判定/覆盖率/缺失·invalid·human_review·degraded 分类/
  保守下界）；延迟口径文档实现一致 + latency_coverage 摊账；stats_uncertainty
  新增 --index 实验索引模式（memhall.experiment-index/1，formal 拒历史
  run_id、一索引一臂，智能体数量不设上限），历史调用输出逐字不变。
- **kylin-agent 专项（F）**：fork 源码逐条复核落地——-z 非零 rc 的 stdout
  一律降级 AgentUnavailable（invalid_run，不再误判为 Reply 计分）；
  HERMES_HOME 钉 ~/.kylin-agent-runtime；session_junk 对齐源码实证并加严
  verify_reset；--version 严判（GUI 前端判 absent）；二进制引用/探测多
  候选；P1 身份契约测试网（memhall-kylin-agent 归属、GATEWAY_AGENT_KEY
  覆写、直连回落、Hermes 车道不变）。
- **整合接缝**：runner 落账 manifest.environment.sandbox（运行路径此前
  无人写这格，兼容校验会把所有新 run 判"未封存"）；F 两条 xfail(strict)
  转正；stats --index 单智能体臂 ③ 区分度除零修复（如实标注不适用）。

### E 档：参赛材料复核与人工盲评导出（2026-10-14）

- **72 条双人盲评工作表已实际导出**（`python -m memhall calibrate`，`--per-capability 12 --seed 20261014`）：抽样框 = 14/18 通过完整核验的历史归档中 judge 判定（非降级、语义探测）共 472 条；六维各 12、题族 35、correct 52 / 错误态 17 / 待复核 3。工作表隐藏智能体/模型/机器判定，受限映射（run/probe/证据指纹）另存 answer-key。**状态 awaiting_human_labels：真人标签到齐前材料中不存在也不允许出现一致率、κ、F1。**
- 新增 `docs/audit/blind-review-20261014/`：抽样协议（种子、框构成表、未采样偏差——over_persist 框内仅 6 条故 0 入样、三家覆盖 36/18/18、human_review 入表不进统计分母）、双人独立副本、分歧复核与终裁模板；kylin-agent 无历史归档，待 D 验收后另出补充表。
- 新增 `docs/audit/submission-readiness-20261014.md`：按赛题交付 a–e 与六项评分细则逐项对照，版本索引（Release v1.3.2 旧二进制 / 集成分支 dadc601 修复源码 / 本材料分支 BASE 4185672），剩余扣分点按可补救性排序（视频、deb 重建试装、D 正式成绩、人工标签、归档缺口）。
- README / benchmark-v2 / 作品介绍（submission-content.json 源 + 重建 Word）同步：判卷校准状态改为"表已导出、待真人标签"；kylin-agent 表述固化为"适配器已接入（独立网关身份修复在集成分支）、VM 待验收、无实测成绩，不引用 Hermes 分数推断"；旧 judge_audit_sample 指针替换为 calibrate CLI。
- 新增材料一致性测试 `tests/test_blind_review_materials.py`：在库盲评三件套指纹对账、双人副本与底表一致、终裁模板 sample_id 对齐。


- 全回归 373 项（基线 293 + 新增 80）+ 离线 mock e2e
  （sample→run→verify→report→aggregate→stats --index）全链路通过。

## [Unreleased] — A 档小修全档（2026-10-14）

依据 docs/audit/roadmap-20261014.md A1–A8（检测与披露，不动判卷口径）：

- **A1 错钟残留检测**：发车前宿主-评测机对时，差超阈值（默认 ±300s，
  `MEMHALL_CLOCK_DRIFT_MAX` 可调）拒跑——上一轮拨钟恢复失败的残留不再带着
  错误的时间语义跑完整场；实测差值随 `manifest.clock_drift_s` 落账。
- **A2 网关对账告警**：统一网关车道"agent 正常作答但该 tag 零记账"→
  `manifest.gateway_reconciliation_alarm`（逐 case 归因只对未拨钟 case，
  窗口前端放宽 30 分钟防 inject 阶段误报；宿主账单优先、VM 侧账单经通道
  兜底，读不到=无法对账不告警）——"必经网关"从纯配置注入升级为有"该来
  没来"的对账。
- **A3** hermes/openclaw reset 失败 RuntimeError → AdapterError（对齐 R33）。
- **A4** 新增 tests/test_isolation_guards.py（18 项）：全局锁互斥、拨钟/恢复
  往返与失败路径、错钟探针与拒跑闸门、网关对账四场景、本机四家 reset
  "清旧"断言（qwen 剥 hooks、opencode 权限面不被残留顶掉）。
- **A5** openclaw 工具权限面走明示豁免（无经实测验证的门控项，盲写配置键
  有打断现役车道风险）——dataset-card §8 披露该车道能力面差异。
- **A6 延迟/成本一等指标 + pass^k**：metrics.json 增 `latency`（p50/p95/
  max/mean，占位回复不计），报告头部出延迟行；aggregate 逐轮 p95 延迟与
  token 列，总体与分维 pass^k（分母只算 k 轮全有判定的探测点）。
- **A7 canary 读侧探针试点**：development-v2 新题族 `recall.canary-read`
  ——教随机口令，probe 段一问口令本体（答出=存过且检索到的观测证据）+ 一问
  从未教过的口令槽位（答"没有提过"=拒答基线，编造=编造基线）；题包重生成
  39 题/13 族/54 探测，test 包 72/24/96 内容零变化（仅模板文档指纹随加族
  滚动，逐字段 diff 已验证）。
- **A8** README 特色节新增"把完整智能体框架当黑盒横评，业界没有现成尺子"
  差异点条目；dataset-card §8 新增九车道接口妥协清单六条（消息进 argv/
  节流差异/导出粒度/actions 覆盖/拨钟/协议面）。

## [Unreleased] — OS 级沙箱 B8（2026-10-14）

- **bwrap 写面强制隔离（openKylin 优先，VM 实测通过）**：被测 agent 进程整根只读 +
  白名单可写（agent 自家 dotdir + EVAL_WORKDIRS），/tmp、~/.cache、~/.npm 用 tmpfs
  每条消息独立（防把记忆性内容藏进快照与 reset 都看不见的缓存）。`MEMHALL_SANDBOX=1`
  显式开启，默认关（不动冻结口径）；开启状态随 `manifest.environment.sandbox` 记录。
  VM 实测：家目录与 /etc 写入拒绝、白名单正常、三家 CLI 沙箱内可启动、hermes 真实
  send 全链路（回复+记忆落盘+reset 归零）通过。网络出站限定网关（需 root）与
  Windows 车道（AppContainer/Job Object）为后续项。
- hermes 故障文案过滤补 "Interrupted during API call"（上游断流重试耗尽时 rc=0
  混进 Reply 的实测缺口）→ AgentUnavailable → invalid_run 不计分。

## [Unreleased] - kylin-agent 适配器（2026-10-14）

- 新增 `kylin-agent` 适配器（赛题点名的第 4 个 openKylin 生态智能体）：驱动系统智能体后端运行时 kylin-agent-runtime（Gitee openkylin/agent-runtime，Hermes fork），证据面与 hermes 车道同构（`~/.kylin-agent-runtime/memories/MEMORY.md` + `USER.md` + `logs/agent.log`），send 走 `-z` 官方 one-shot 模式；二进制惰性解析（env 覆写 > PATH > apt/Gitee 部署落点）。
- hermes 适配器参数化改造（目录落点/调用行下沉为类属性与钩子，行为不变），kylin-agent 以子类复用全部证据语义与 R02/R31/R58 防线。
- 智能体发现（本机/VM 探测、跑页下拉）纳入 kylin-agent；调研文档补 kylin-agent 记忆机制条目。
- ⚠️ 未实测：VM 联调（CLI 行为、部署落点、首跑向导）待 VM 空闲后进行，届时更新 `adapters/kylin_agent.py` 的实测标注。

## [Unreleased] - 题库与可复现评测升级（2026-10-13）

- 固定试卷：分层抽样、完整题目封存、SHA-256 校验、CLI/UI 导入导出；正式对比不依赖运行历史。
- 新增公开测试 72 题／24 场景模板、开发 36 题／12 个不同模板；生成器可复现生成 1008 实例，保留模板与参数记录。
- 批量运行同一试卷；compare/aggregate 校验题目内容、题包版本、判卷协议和产物哈希，统计改为按题族／用例聚类。
- 真实文件内容采样与阶段修改证据支持 `fs.artifact_matches`；新增复用题以产物字节验收。
- 已安装适配器／断言插件入口，重名拒绝覆盖；人工盲评工作表与六态对分工具。
- 新增题包保持独立口径，历史成绩与原始证据不改写。真实双车道新实验、人工标签与发行包验收仍需补充。

本文件记账对外可见的版本变化（T23：README 状态节迁入，README 只留当前版本一行）。
口径遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 精简版；
评测数字随口径变更的，一律注明口径而非只报数字。

## [Unreleased] — 2026-10-13 参赛审计修复

- paramiko 移为运行依赖，更新锁与离线 wheels 清单；Windows 本机通道优先原生 Git bash，避免 WSL 转发器二次解释。
- 配置接口拒绝换行/NUL/非字符串，安全保存包含引号和 ` #` 的值；UI 修正有效计分分母、adapter 和 diagnostic 用途。
- 单评委不再显示伪双评委一致率；报告按实际 B 配置披露判卷方式。
- 不确定度统计按运行快照的 score/capability 复算，四轮功效使用真实轮数；描述性方差比不误用 F 分布 p 值。头部近似功效需求由旧错误约 36 轮更正为约 58 轮；配对票 p 值另补按题聚类稳健口径（见下）。
- tag 可触发 CI，发行等待 lint/test；exe/deb 排除 heldout；Debian 按构建机声明架构、Python 次版本和 pip 依赖，修正文档安装位置；构建使用项目内独立目录，避免清空用户通用 wheels/stage 目录。
- **归档全套装印落地（审计 R01 代码侧）**：runner 落盘 cases.json 题库快照 + cases_snapshot_sha256/case_hashes/cases_version 题库指纹 + case_results 逐题证据哈希（含 case.json）+ evidence_bundle_sha256 整包哈希；无 .git 的 tgz 部署补 source_fingerprint 源码树指纹；部分用例失败的 run CLI 退出码 4（马拉松当晚即知，不再等事后审计）。verify 对全部封印强校验，残缺轮无法混进通过名单。
- **case 聚类稳健检验（审计 R03 统计侧）**：stats_uncertainty 新增 ②b 按题聚类的净方向符号检验——n=4 下三对配对比较均回到不显著（p=0.163/0.121/0.572），证实合并票显著被同题跨轮重复撑大；README/Dataset Card 同步口径。新增 scripts/judge_audit_sample.py 人工抽检工作表（盲评生成 + 对分，零 LLM 调用），补单评委校准的人工半边。
- Dataset Card 补六能力测量操作定义表（每条车道实际测什么、与构念的差距，审计 R04）；heldout v2 路线具体化（私有槽位池/延迟公开 seed/模板外改写，审计 R05）。
- 新增可编辑 Word 作品介绍、公开逐轮数据、完整离线样例、真实证据片段及全面审计报告；同步 README、Dataset Card、图表和演示讲稿，补 Word 重建依赖组。
- **适配器层收编去重**：`_send_throttle` 六连抄上收 `base._send_throttle`（类属性 `send_interval_env/_default`，环境变量覆盖与各车道默认值不变，hermes VM 仍不节流）；280s/300s 超时字面量上收 `base.SEND_TIMEOUT_S/CHANNEL_TIMEOUT_S`；VM 三家拨钟/恢复/远端指纹/close/版本探测并入新模块 `adapters/vm_common`（`VmAdapterMixin` + `EVAL_WORKDIRS` 单源）；本机四家 `fs_snapshot` 收口 `base.workspace_fs_snapshot`——出口统一 `~/` 前缀（对齐 base 契约与 R30 归一口径），并修复默认沙箱根 `~/.memhall` 点目录祖先导致快照恒空、本机车道 reuse 文件断言全体静默失配的潜伏 bug；空壳 `dump_actions`（coverage=unknown）改 base 默认实现。R58 修复补齐 claude_local/qwen_local 两处残留（`lstrip("-* ")` 误剥"-3°C"类正文首字符），新增对应回归测试与点目录祖先回归网。
- 验证：218 项测试通过；其余门禁详见 `docs/audit/review-20261013/validation.json`。18 轮历史归档有 14 轮严格通过，4 轮缺失；本批为单 LLM A 实测。修复版 .deb/exe 尚未重建发布，不能把历史二进制作为新版本。

以下正式版本记录保留历史叙述，其中旧显著性/F 检验/功效表述已由上述审计更正；当前结论以修正后的 README、统计输出与审计报告为准。

## [1.3.2] - 2026-10-12

- **用例轮换 --rotate-count/--seed（PR#6，weilai-040303）**：按历史运行频次 +
  能力/难度平衡的可复现轮换抽题（同 seed、同历史记录选出同一题单）——长跑
  马拉松不必每轮全量；选题在全局 run lock 内完成（顺带修同秒 run_id 分配
  竞态），manifest 增 case_rotation 键留痕。纯 opt-in：不指定即全量，
  不动冻结口径；合并后清理轮换校验分支 3 行不可达残留
- **终榜 v3：扩轮至 n=4 + chains 并入口径（2026-10-12）**：r3/r4 马拉松 12 场
  （三家 × r3/r4 × full+chains，与 10-10 六场同栈；hermes-r3-full 一轮判卷遇 DNS
  挂死，事后从封存证据补判，全程留痕）。**full 45 × 4 轮**：hermes 74.6%±2.8 ≈
  openclaw 72.5%±4.9 > kylinbot 58.5%±6.8——**n=4 下两对配对符号检验转显著**
  （hermes vs kylinbot p<0.001、kylinbot vs openclaw p=0.003），头部 p=0.856
  并列（需 ≈36 轮才可分）；**chains 4 × 2 轮并入附表**（40.0/40.0/25.0，hermes/
  openclaw 两轮逐题零翻转）。stats_uncertainty 升 n=4（一致率改四轮两两 6 对、
  F 检验自由度 d2: 3→9 闭式解仍适用、新增 chains 子口径入口）；维度区分度
  边界识别 9.9（p=0.005）仍唯一过 α=0.05。README 终榜/EN 摘要/口径速览、
  dataset-card §9、七图全部同步刷新
- **图 7（维度区分度）两处渲染缺陷修复**：x 轴定长 7.6 曾把 12.0 的边界识别切在
  图外（最强信号的点与数值均不可见）；未测族（chain，本轮 run 无探测点）曾按 inf
  画出一根无限长棒棒糖——现未测族跳过、x 轴随数据自适应。stats ③ 区分度同步输出
  F 检验 p 值（d1=2 闭式解）：边界识别 p=0.037 是 n=2 下唯一过 α=0.05 的维；
  README 图 7 caption 原把「其余维不区分」写错（实为六个主维全部 ≥1、分层明显），
  已随数据修正；dataset-card §9 全节从 10-08 旧数据刷新为 10-11 六场重测口径
- **R25 口径差数字按留档复核修正**：dual vs scripted 同证据分差实为 run 级
  0.8–19 分（六轮方向一致，但 openclaw 两轮 <5 分——原记「7–37 分」「>5 分
  六轮全中」不成立）；维度级 −45～+27 分非单向（记忆调用六轮 −16～−39、边界识别/
  任务复用反向为正）——脚本判不了的题剔出分母的幸存者偏差双向存在。README 特色节/
  口径速览、dataset-card §9、review-tasks R25 三处已同步修正

- **用例集下拉口径标注**：标签自带进分/不进分标记（合着下拉可见）——
  full/chains 标「正式口径」、quick「不进分（链路自检）」、gen「不进分
  （实验区）」、heldout「不进分（防背题）」；选中后的说明行同步补全
  口径表述（对外分数 = full + chains 合并各 ≥2 轮取均值、gen 不并入的
  冻结纪律理由、heldout 考后加试用途）
- **报告附图第二梯队（+5 张，全部离线零 LLM）**：每张自带价值闸门——
  数据不足/无信息量即不画（UI 端 404 自动隐藏），不为凑数出空图——
  - report-latency：教/扰/考三阶段回复延迟分布（对数轴带状散点+中位
    菱标；mock 级瞬时回复 <0.2s 不画）
  - report-memory：记忆库存量变化教后 vs 考后哑铃 + 教后条目原文留存率
    （导出失败剔除并计数披露，不冒充"没存"）
  - report-durations：逐用例回合耗时堆积条（教/扰/考分色、拨钟标记）
  - report-probe-history：同智能体历轮探测点通过点阵（稳定失分 vs 偶发
    翻转；<2 轮不画、全轮全对不列、>40 行截断）
  - report-case-scores：逐用例计分通过率排序棒棒糖（点色=能力维，
    全无效用例计数披露不冒充 0 分）
  旧 run 不回头补图，`memhall report <run>` 重渲染即得
- **「模型（网关改写）」下拉——(agent, model) 二元组对照进 UI**：跑页选
  车道后可选模型（菜单由网关透传上游 /v1/models，mock/claude 直连车道
  自动隐藏），选中即逐 run 改写网关模型。机制：改写表
  `~/.memhall/gateway-model.json` 按 memhall-<agent> tag 键控（入站 dummy
  Bearer 本就唯一标识智能体，是唯一能跨进程、穿透智能体自带 HTTP 客户端
  的信道），runner 发车写、收尾必清；CLI 同款 `memhall run --model <id>`。
  账单记实际生效模型（改写值），manifest.model_backend 增 model_override
  口径键、llm_runtime.models 改记生效模型（智能体侧漂移单列
  asked_models——"怎么接的 deepseek"这类困惑从 manifest 直接可答）。
  model_backend 落盘从 CLI 收进 _write_manifest：UI 发车的 run 此前一直
  缺这格（compare 对账盲区）
- **网关 /v1/models 上游菜单透传**：原只回静态单模型；现转发上游菜单
  （默认模型保证在列，失败回落静态），鉴权对齐 /usage（回环免 token）
- **openKylin 原生化：LocalChannel 本机执行通道（消灭回环 SSH）**：
  remote.py 增 LocalChannel——与 SshChannel 接口逐方法对齐（run/sudo/
  run_json/close，POSIX 语义不变，~/.local/bin 前置进 PATH），default_channel()
  按部署形态选路：VM_HOST 指向本机（原生模式）时 hermes/kylinbot/openclaw
  三车道直接本机 bash 执行，不再绕 SSH 回环、不依赖 sshd（拨钟等 sudo
  操作仍走 VM_PASS stdin）；宿主+VM 形态照旧 SSH，通道注入式测试不受影响
- **GATEWAY_EXEMPT 混合形态豁免**：openKylin 原生装机同时要网关车道与
  claude 直连（anthropic 协议进不了 openai 网关面）——`GATEWAY_EXEMPT=
  claude-local` 明示豁免后 claude 车道照常直连，manifest 侧各记自己的
  model_backend，compare 口径告警可对账
- **上游预检（发车防呆）**：`_gateway_preflight` 扩为 `_upstream_preflight`——
  统一网关模式探 GATEWAY_URL/VM_URL，直连模式探 AGENT_LLM_BASE_URL（claude
  车道走 anthropic 协议，探 CLAUDE_LLM_BASE_URL），mock 免检；UI `/api/start`
  接入同一道闸（此前只接在 CLI 的 cmd_run——2026-10-09 VM 上从 UI 发车冲进
  死网关+hosts 毒解析，2 用例纯超时烧废才暴露缺口）
- **环境自检跨平台适配化（npm/pnpm/volta 布局差异）**：候选清单按包管理器
  布局扩容（win %APPDATA%\npm、pnpm PNPM_HOME/%LOCALAPPDATA%\pnpm、
  volta；nvm/fnm 靠 shell 激活不静态枚举）；node 进体检"另检出"名单
  （npm 系智能体硬依赖，缺了就是"检出 CLI 但发车即 env node 扑空"）；
  `_which` 绝对路径分支补 win 同名多扩展 PATHEXT 优先级（npm 布局下
  裸 sh 脚本会 ENOEXEC）
- **claude code 车道进 openKylin 原生模式**：体检注册表 claude-code 挂
  claude-local 适配器（卡片可点击跳评测页）；适配器 PATH 加固——node 与
  claude 同居 `~/.local/bin`（npm 布局）而服务进程 PATH 未必含它，
  `#!/usr/bin/env node` shebang 会扑空，发车前把 claude 与 node 所在目录
  前置进 PATH；统一网关模式下下拉诚实不列（anthropic 协议进不了 openai
  网关面，起跑即拒的车道不装可跑）

## [1.3.1] - 2026-10-08

- **集成队友 PR #3 四模块（择优移植，Co-authored leeyu44）**：
  - `runner/verify.py` run 目录离线完整性校验 + `memhall verify` 子命令——
    证据逐条 schema/payload SHA-256 复算、evidence_id 去重、阶段覆盖、
    verdicts 引用对账；主线产物无 fork 封印字段时降级为警告（不把
    "没写封印"误报成"被篡改"）
  - `report/stability.py` + `memhall stability`：多次重跑稳定性报告
    （判定一致率 / pass^k / 翻转明细），能力标签从 case.yaml 回填
  - `adapters/audit.py`：auditd Action 采集回退源（rename/create/delete 映射）
  - `vm.py` + `memhall vm`：vmrun 生命周期（status/start/stop/snapshot/
    revert/health）与 `remote_environment` 环境指纹、`host_key_sha256`
- `_finish_run` 写 `output_hashes` 评分产物封印（verdicts/metrics/report/
  radar 四产物哈希进 manifest，重渲染自动刷新）——verify 的产物对账生效
- `scripts/test_deb_vm.sh`：fork 的 `--repeat` 改为主线两次独立 run，
  `.build.json` sidecar 存在校验、缺失跳过
- 修 `test_p1_credentials` 两个 fake 缺 `get_transport`/`exit_status_ready`
  （50fdc1a keepalive 引入的漏网，origin CI 因此红过一轮）
- **人工复核入口（评测闭环收口）**：UI 报告页复核横幅 + 弹层裁决（两步确认防误触），
  `GET/POST /api/runs/{run_id}/review/{probe_id}`；裁决前自动备份
  `verdicts.pre-review.jsonl`，落 `human` 判定后 `_finish_run` 全量重算
  指标/雷达/报告/封印——未决判定不再只能挂在报告里
- **报告进阶图（最终展示物多样化）**：每份报告自动嵌入两张辅助图——
  分能力判定构成堆积条（错误集中在哪一维、什么形态）+ 双判 vs 脚本判卷
  哑铃（同证据两口径差）；零 LLM、离线可复现
- **口径自动识别**：网关逐请求抽取模型 id / 思考强度（reasoning_effort、
  thinking budget 等五类字段），run 结束按时间窗 + agent 标签归并进
  `manifest.llm_runtime`，报告头与 README 图自动标注（不再手填口径）
- **网关韧性**：上游重试预算 4→7 次（扛分钟级坏窗口，r3 事故回归），
  退避基数参数化（`backoff_base`，默认环境变量回退）
- **hermes dump 退出码陷阱修复**：for 循环 `[ -f ]` 残留 rc=1 把合法文件缺席
  误记成导出失败（25 例假无效），循环末尾 `; true`；配套
  `scripts/repair_dump_ok.py` 修复重放（payload 哈希重算、`.bak-repair` 备份、
  manifest.repair 留痕）。r3 修复后 63.8%，三轮 24.7 / 70.0 / 63.8（r1 坐实离群）
- **UI/CLI 冒烟网**：22 条路由一览回正 + 11 个子命令 `--help` 全通（subprocess
  显式 UTF-8，Windows GBK 读线程坑入档）
- **README 九图重制**：六维对比（带智能体版本/模型口径标注）、不确定性带、
  评测管线架构图、判定构成、双判哑铃、分离度棒棒糖等（素材自 skills 风格库）
- **docs 口径收口**：系统级测试实做四项（回滚/写入监控/auditd 扩展未实现）、
  零代码档降级为 doctor 建议、金标准未实施标注、auditd 已具备未接线披露
- **T21 deb 冻结依赖**：`packaging/deb-wheels.txt`（`uv export --frozen` 生成 +
  paramiko 链手工补行），构建机按精确清单装——可复现构建（评分项）

## [1.3.0] - 2026-10-08

- **测量口径落地（R53/R55 收口）**：`scripts/stats_uncertainty.py` 四表一声明（零 LLM 可复现）
  ——同探测点两轮一致率、配对符号检验排名主张、维度区分度、判卷口径差、功效声明
  （轮级需 ≈60 轮、配对探测点 n=2 已分出）；雷达图加构念注脚；design §5 立
  「评测诚实优先级」五条明规则；dataset-card §9 功效与不确定度
- **R54 heldout 产物脱敏**：`scripts/redact_heldout.py`（题目/期望值/对话文本哈希化，
  审计可对账），heldout run 对外分享前必跑
- **R51 裁决**：gen 21 题不并入正式口径（基线与冻结纪律），记入赛后 roadmap
- 网关统一限速（GATEWAY_MIN_INTERVAL 默认 8s）+ 上游瞬态指数退避（等抖动、
  尊重 Retry-After、限流信号全局冷却）
- hermes reset 清 `*.lock` 锁文件残留（R02 防线真机首战逮住）

## [1.2.0] - 2026-10-05

- **评分口径 v3：LLM 判卷重判终榜**（R25）——六 run 离线重判，human_review
  从最高 41/89 降至每轮 0–2；openclaw 67.1±5.8 > kylinbot 52.4±5.7 >
  hermes 44.4±30.7；与脚本口径分差（7–37 分）机制已抽查标注
- **复审二轮 R25–R60 整改 36 项**（28 全 + 5 半 + R25）：超串干扰防线、
  canary 全阶段语义化、fs 证据分阶段窗、公平性整改（超时残句不计分等）、
  UI 原生形态单车道化
- UI：/ 与 /static 加 no-cache（Firefox 启发式缓存导致旧 SPA 打新后端的事故根治）

## [1.0.0] - 2026-10-02

- 评测口径对齐主流基准：held-out 防背题池（seed 现场生成）、N 轮方差口径
  （`memhall aggregate`）、六会话长链 chain-004、CI 平台矩阵（ubuntu/windows ×
  py3.11/3.12）、平台分级表、deb 补 UKUI 菜单项
- 统一模型网关 `memhall gateway`（model 强制改写 + 凭据单点 + 逐请求记账）
- 三难度旋钮参数化（--distract/--gap-days/--similar），同 seed 存档逐字复现
- openclaw 适配器（第七个）+ token 预估闭环 + 可复现元数据

## [0.2.1] - 2026-09-29

- Web UI 评测直播、`compare` 对比 CLI、系统级测试（重启/拨钟/多用户/断网，
  hermes 真机 4/4）、claude/qwen 本机适配器（沙箱隔离）、UKUI 桌面通知
- Mock v2 缺陷注入基线；v0.2.0 的 deb/exe 双平台发行同窗口落地

## [0.1.0] - 2026-09-28

- MVP 全链路闭环：三阶段剧本 runner、四类证据、脚本判卷 + 双 LLM judge、
  六维雷达、CLI 一条命令出全产物；Hermes/KylinBot 首批适配器；
  openKylin 目标机原生 .deb（48MB）
