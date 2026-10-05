# 麟阁 MemHall

**面向 openKylin 生态的智能体记忆能力评测基准**

A Memory Benchmark for Agents on the openKylin Ecosystem

[![CI](https://github.com/shangjian2023/MemHall/actions/workflows/ci.yml/badge.svg)](https://github.com/shangjian2023/MemHall/actions/workflows/ci.yml)

> 名字取自麒麟阁——汉代评定功臣、画像记名的殿堂：给记忆能力优秀的智能体立榜。

给跑在 openKylin 上的智能体测记性，全自动、可复现：用「教 → 隔 → 考」三阶段剧本驱动任意智能体，把对话日志、记忆快照、操作记录、文件变化统一为证据，自动评分并输出六维能力雷达图。

## 文档

| 文档 | 状态 | 内容 |
|---|---|---|
| [design.md](design.md) | **正式** | 总体技术设计（交付物 a 底稿） |
| [team-plan.md](team-plan.md) | **正式** | 五人分工、4 周排期、协作规约 |
| [environment.md](environment.md) | **正式** | 环境基线与搭建步骤 |
| [docs/contracts/](docs/contracts/README.md) | **契约** | 接口单一真相源（adapter / case / evidence-verdict） |
| [docs/dataset-card.md](docs/dataset-card.md) | **正式** | 用例库数据集说明卡 |
| [docs/engineering-tasks.md](docs/engineering-tasks.md) | 过程 | 工程化整改队列与进度 |
| [docs/review-tasks.md](docs/review-tasks.md) | 过程 | 评测设计专项排查（R01–R24）与评分口径 v2 |
| [okim-bench/README.md](okim-bench/README.md) | 归档 | W1 评分原型（权威实现在 src/memhall/scoring） |
| [docs/](docs/) 其余 | 过程稿 | 调研、阶段性环境记录、原型设计稿（头部有归档标注） |

## 目录结构

```
memhall/
├── design.md / team-plan.md / environment.md   # 三份基准文档
├── docs/            # 方案文档（A 总稿）+ contracts/（接口契约）
├── okim-bench/      # W1 评分原型（已归档；权威实现在 src/memhall/scoring）
├── src/memhall/     # 源码包
│   ├── adapters/    # 智能体适配器（mock/hermes/kylinbot/claude/qwen/opencode…）
│   ├── runner/      # 三阶段编排
│   ├── schema/      # case/evidence/verdict 数据模型
│   ├── scoring/     # 规则判卷 + 六维指标
│   ├── report/      # 报告与雷达图
│   ├── ui/          # Web UI（FastAPI + SSE 评测直播）
│   └── cli.py / discovery.py / notify.py / systests.py
├── cases/           # 用例库（full 43 / gen 21 / chains 4 / heldout 21※；quick=full 的虚拟冒烟子集，按 ID 引用不落盘）
├── scripts/         # deb/exe 打包、VM 通道、判卷自检等脚本
└── tests/           # 端到端与适配器测试
```

> ※ 正式口径 = **full + chains**；**heldout 为不可见防背题池**（`scripts/gen_cases.py --seed 4210 --out cases/heldout --prefix h` 评测时现场生成，题目文本不入公开仓库，seed 公布保复现）；全量跑 ≥2 轮报 mean±std + bootstrap 95% CI（`memhall aggregate`）。
> held-out 威胁模型：seed+生成器公开 = 题目可重构——防训练污染有效，防定向作弊无效；防"背题库"不防"背题型"（详见 dataset-card §8）。

## 使用

```bash
uv sync --group dev          # 装依赖（uv，Python ≥ 3.11）

uv run memhall doctor        # 一键发现本机/评测机智能体 + 评测环境体检
# 三路探测：本机（PATH+配置目录+版本）、openKylin VM（SSH 单往返复合探测，
# 含 brain.db 记忆库在位）、环境就绪度（密钥/SSH/网关可达），仿 brew doctor

uv run memhall ui            # Web UI（本地 127.0.0.1:8300，自动开浏览器）
# 浏览器里选适配器和用例库发起评测，问答与记忆快照逐条直播（SSE）；
# exe 双击默认走 pywebview 原生窗口；URL hash 可直达标签页

# 一轮评测（命令行）
uv run memhall run -a mock -c cases/full -o runs      # Mock 适配器，离线零成本
uv run memhall run -a hermes -c cases/full -o runs    # 真智能体（SSH 驱动 VM）
# 适配器：mock / hermes / kylinbot / openclaw（VM 内）/
#         hermes-local / claude-local / qwen-local / opencode（本机）
# 产物：runs/<run_id>/{manifest.json, verdicts.jsonl, metrics.json, radar.png, report.md}
#       runs/<run_id>/cases/<case_id>/evidence.jsonl（每条判定可下钻证据哈希）
#       manifest 记被测智能体版本 / 模型口径 / token 记账，report.md 头部可见
# 开跑前会按该智能体历史网关记账预估本轮 token 消耗（CLI 提示行 / UI 确认弹窗）

uv run memhall report runs/<run_id>     # 对已有 run 重渲染报告（缺 verdicts 时从证据重放）
uv run memhall compare runs/A runs/B    # 对比雷达 + 判定翻转明细（两智能体/两次运行）

uv run memhall systest -a hermes        # 系统级测试：重启/拨钟/多用户/断网（真机真做）

# 统一模型对照：被测智能体流量必经本地网关——同后端同模型，对照才有意义
# 网关侧配置（真凭据只放环境变量）：
export GATEWAY_UPSTREAM_URL=... GATEWAY_UPSTREAM_KEY=... GATEWAY_MODEL=qwen3.7-plus
uv run memhall gateway --host 0.0.0.0    # 监听 8311；模型一律网关说了算
export GATEWAY_MIN_INTERVAL=8                # 转发最小间隔秒（默认 8 ≈ 7.5 RPM
                                             #  防上游限流掐线；超速请求网关内排队，0 关闭）
# 智能体侧只需两个变量（.env），dummy key 自动按 memhall-<适配器> 派生：
export GATEWAY_URL=http://127.0.0.1:8311/v1          # 本机智能体
export GATEWAY_VM_URL=http://192.168.61.1:8311/v1    # VM 内智能体（hermes/kylinbot）
uv run memhall run -a hermes -c cases/full           # 流量过网关，逐请求记账
uv run memhall gateway --report                      # 按智能体出 token 账单
# claude-local 走 anthropic 协议不进统一车道（配了 GATEWAY_URL 会显式报错）；

# 双 LLM judge 判卷（可选，替代默认的离线脚本判卷；两 judge 需跨厂商）
export JUDGE_A_BASE_URL=... JUDGE_A_MODEL=... JUDGE_A_KEY=...
export JUDGE_B_BASE_URL=... JUDGE_B_MODEL=... JUDGE_B_KEY=...
uv run memhall run -a mock --judge dual

uv run pytest tests/ -q                # 测试（含端到端冒烟）
```

### 第三方使用（自带已装好的智能体）

前提只有一个：目标智能体已安装且配好了你自己的 key。麟阁不内置任何凭据。

1. `pip install -e .`（或装 deb/exe），`memhall doctor` 体检——自动发现本机已装智能体；
2. 在 `.env` 给被测智能体配 LLM 端点之一：
   - 直连（你的 key）：`AGENT_LLM_BASE_URL/KEY/MODEL`（OpenAI 兼容端点）；
   - 或统一网关（推荐做对照实验）：`GATEWAY_UPSTREAM_*` + `GATEWAY_URL`；
3. `memhall run -a <适配器> -c cases/full` 开跑。VM 型适配器另需 `VM_HOST/VM_USER/VM_PASS`。

机器相关的默认值全部可环境变量覆盖（见 .env.example）；沙箱一律建在
`~/.memhall*/` 下，不碰智能体的真实配置与真实记忆。

判定五态：正确 / 遗漏 / 混淆 / 错误持久化 / 错误复用；规则判不了的才升级语义判卷（脚本判卷 → 双 LLM judge 交叉仲裁，锚例随提示词下发，仲裁评委 A/B 轮值），未决判定单列 HUMAN_REVIEW 待人工复核，不计入运行无效。

评分口径 v2（2026-10-04，docs/review-tasks.md）：探测点分 **score / diagnostic** 两层——存储态断言（memory.*）与 actions 断言只进故障定位表（没存 / 存了没用上 / 存了但内容错 / 该删没删），不进六维分母；canary 一律教学时点判（probe 段删除洗白不了 over_persist）；无有效探测的维输出"未测"不画轴；报告附"未决按错计"保守下界与按用例等权总分。旧 run 可 `memhall report runs/<id>` 按新口径重渲染。

评测收尾可选 UKUI 桌面通知（notify-send）并自动弹出雷达图（xdg-open）。

## 安装（openKylin / Debian 系）

```bash
sudo dpkg -i memhall_*_all.deb     # 内置全部依赖 wheel，安装不联网（版本号随发行）
memhall run -a mock -c /usr/share/memhall/cases/full -o ~/memhall-runs
dpkg -r memhall                         # 卸载干净（prerm 清 /usr/lib/memhall）
```

系统目录只读：run 产物写 `~/memhall-runs`，配置读 `~/memhall.env`。

包构建在 openKylin 目标机上原生完成（`scripts/build_deb_vm.sh`：清华源拉依赖 wheel → 组装离线安装树 → dpkg-deb），保证 wheel ABI 与目标机 Python 精确匹配、可复现。

## 安装（Windows）

`scripts/build_exe.sh` 打包两种发行物：`dist/MemHall/`（onedir，启动快）与 `dist/麟阁MemHall-单文件版.exe`（单文件，可直发）。双击即进 Web UI 原生窗口。

## 平台支持（分级）

主力线是 openKylin：级别越高，验证深度越深——Tier 1 的真机系统级测试（重启/拨钟/多用户/断网）只能在 openKylin 真机上做，是其他平台复制不了的验证深度。

| 级别 | 平台 | 验证深度 | 证据 |
|---|---|---|---|
| **Tier 1 旗舰** | openKylin 3.0 | 全链路验收：全量测试 + 真机系统级测试（重启存活/拨钟隔天/多用户隔离/断网存活）+ 目标机原生构建 .deb + UKUI 桌面通知 | [CI](https://github.com/shangjian2023/MemHall/actions/workflows/ci.yml) · [build_deb_vm.sh](scripts/build_deb_vm.sh) · 上文安装节 |
| Tier 2 | 主流 Linux · Windows 10/11 | 核心功能等价：CI 矩阵（ubuntu/windows × Python 3.11/3.12）每提交跑题库门禁 + 全量测试；Windows 另有 exe 发行 | [CI](https://github.com/shangjian2023/MemHall/actions/workflows/ci.yml) · 上文安装节 |
| Tier 3 | macOS / 其他 Linux | 尽力而为：纯 Python 源码安装（uv/pip），未持续验证 | — |

注：WSL 能跑但无增益——评测目标在 VM 里，多一层反而慢，不作为支持目标。

功能影响：六维评测、双智能体判卷、雷达图、报告全链路平台无关；平台差异仅在安装方式（uv/pip vs .deb/exe）与控制台编码。

## 许可

Apache-2.0（见 [LICENSE](LICENSE)）

## 状态

v0.2.1（2026-09-29）：Web UI 评测直播、`compare` 对比 CLI、系统级测试（重启/拨钟/多用户/断网，hermes 真机 4/4）、claude/qwen 本机适配器（沙箱隔离配置目录）、UKUI 桌面通知；Mock v2 缺陷注入基线（措辞解耦后总分 62%，六维显式缺陷模式表）。里程碑见 team-plan.md。

2026-10-02：评测口径对齐主流基准——不可见 held-out 防背题池（seed 4210 现场生成）、N 轮方差口径（`memhall aggregate` 出六维 mean±std）、六会话长链 chain-004（对齐 LongMemEval/LoCoMo 的长程会话深度）；CI 平台矩阵（ubuntu/windows × py3.11/3.12）+ 平台分级表；deb 补 UKUI 菜单项。

2026-10-04：**评分口径 v2（基准设计专项排查 R01–R24，docs/review-tasks.md）**——探测点 score/diagnostic 分流（故障定位四态表落地）、canary 教学时点判、actions 证据覆盖门禁、未决保守下界 + 按用例等权总分、逐探测点 bootstrap 95% CI + 对比符号检验、判卷锚例进提示词 + 仲裁轮值、runner 单 case 异常隔离 + reset 彻底性防线、大容量注入题（persist-008/recall-008：16 条教 5 条考，检索竞争）。

2026-10-05：**评分口径 v3 + 复审二轮 R25–R60 整改（36 项，docs/review-tasks.md）**——头条切换 LLM 判卷重判口径、判卷溯源进 manifest（decided_by 含 scripted 枚举）、超串干扰防线（188 干扰项 100% 拒绝）、canary 全阶段语义化、fs 证据分阶段窗、公平性整改（超时残句不计分、拨钟失败即判无效、SSH 通道统一收口）、UI 原生形态单车道化。

三智能体马拉松（47 题×2 轮，统一模型 qwen3.7-plus）**正式头条口径：LLM 判卷离线重判**（R25，verdicts 快照重放免重跑真机；判卷模型 qwen3.7-plus，A/B 轮值仲裁架构就位——网关暂只授权单模型，当前单判运行，配 `JUDGE_B_*` 即恢复双判）：

| 智能体 | 总体（两轮 mean±std） | 两轮明细 | 长期保持 | 记忆调用 | 动态更新 | 相近区分 | 边界识别 | 任务复用 |
|---|---|---|---|---|---|---|---|---|
| openclaw | **67.1% ± 5.8** | 63.1 / 71.2 | 80% | 62% | 61% | 100% | 61% | 53% |
| kylinbot | 52.4% ± 5.7 | 48.4 / 56.5 | 74% | 50% | 33% | 38% | 81% | 33% |
| hermes | 44.4% ± 30.7 | 22.7 / 66.1 | 43% | 21% | 22% | 50% | 88% | 23% |

（聚合产物 `runs/_agg-*-dual`；各轮报告头条并列未决按错下界与未决数，未决每轮仅 0–2 个，保守下界与实测差 ≤1.6 分）

LLM 重判比脚本判卷全面低 7–37 分（六轮分差全部 >5 分，集中在记忆调用/动态更新两维），掉分机制已抽查证实：脚本把判不了的探测点剔出分母（幸存者偏差——如 kylinbot r1 记忆调用，脚本口径只剩 4 个可判点、对 3 个 = 75%；重判后 8 个点全计、对 3 个 = 38%），且模式匹配会把语义答错的回答计对。脚本判卷数字仅存档对账（`verdicts.scripted.jsonl`），不用于排名；此前 W2 双智能体对比（Hermes 81.6% vs KylinBot 61.5%，09-28）同为脚本口径，仅作历史对照。跨智能体差异叙事用配对符号检验/差值 CI（R28：CI 重叠≠不显著，"重叠所以没差别"是统计谬误，compare 已按方向翻转分桶检验）；v1 口径的 kylinbot 居首主因是死探针与存储态断言计入分母（R01/R07）。hermes r1 的 22.7% 是真实行为记录：该轮 hermes 工具调用与上游调用量仅为 r2 的 1/3～1/4（44/49 用例 inject 后零记忆，嘴上说记住实际没写），泄漏哨兵题全对证实并非跨题污染——干净起点保证的是起点公平，保证不了被测系统逐轮行为稳定，这正是两轮方差的测量对象；上游 5xx 窗口（网关日志 60 个 5xx）实际落在 r2 尾部（8 个 invalid_run）。
