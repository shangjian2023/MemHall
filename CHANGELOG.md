# Changelog

本文件记账对外可见的版本变化（T23：README 状态节迁入，README 只留当前版本一行）。
口径遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 精简版；
评测数字随口径变更的，一律注明口径而非只报数字。

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
