# 麟阁 MemHall

**面向 openKylin 的智能体长期记忆自动化评测基准**

把“记得住、改得对、分得清、用得上”变成可执行、可解释、可复查的测试。

[![CI](https://github.com/shangjian2023/MemHall/actions/workflows/ci.yml/badge.svg)](https://github.com/shangjian2023/MemHall/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](pyproject.toml)

麟阁用 **教 → 隔 → 考** 三阶段剧本驱动完整智能体：先教约定，再加入干扰，最后检查回答与实际任务。对话、记忆快照、行动和文件变化连接成证据，输出六维画像、错误原因和逐题复查入口。

**已接入 Hermes、kylin-agent、KylinBot、OpenClaw；五套题包共181题。** 支持固定试卷、真实任务检查和逐题证据复查，服务智能体选型、问题定位与版本回归。

[作品介绍与报告](docs/contest/submission/README.md) · [设计说明](design.md) · [题库与扩展](docs/benchmark-v2.md) · [演示流程](docs/demo-script.md) · [贡献指南](CONTRIBUTING.md)

评审阅读可从[命题评分项与证据导航](docs/contest/REVIEW_GUIDE.md)进入，再按[机读证据索引](docs/contest/review-evidence.json)核对实现、测试与真实案例。索引中的哈希对应本次本地提交材料快照，核验时请使用随包文件。

## 四方面了解作品

| 方面 | 核心内容 |
|---|---|
| **项目背景** | 长期助理不仅要记住信息，还要更新旧约定、区分相似项目、遵守记忆边界。 |
| **技术架构** | 统一适配器、三阶段执行、规则与语义判卷、证据归档，连接从题目到报告的全过程。 |
| **应用场景** | 检查目录、偏好和模板复用，定位记忆机制问题，持续验证智能体版本更新。 |
| **创新与麒麟价值** | 在麒麟本机直接测，检查重启和权限变化；从UKUI桌面启动、接收完成提醒，用.deb安装，升级后用同一套题复查。 |

## 六项能力，分别回答六个问题

| 能力 | 观察什么 |
|---|---|
| 长期保持 | 经历后续交互，还能使用原有信息吗？ |
| 记忆调用 | 需要时，能找到相关约定吗？ |
| 动态更新 | 用户改口后，旧信息失效了吗？ |
| 相近区分 | 相似项目和任务会串台吗？ |
| 边界识别 | 不应保留的信息会在后续继续使用吗？ |
| 任务复用 | 记住的信息真正落实到文件和行动了吗？ |

目录更新案例同时检查“回答哪个路径”和“文件建在哪里”；边界案例检查明确禁止保留的合成信息是否在后续被复述。[真实案例与证据入口](docs/contest/submission/README.md#真实案例与证据)。

## 为 openKylin 设计的使用流程

- **在麒麟本机直接测，也可远程管理。** 个人用户直接运行，团队从另一台电脑管理麒麟测试机，两种方式使用同一套题库、评分和报告。
- **系统事件可测。** 提供重启、拨钟、多用户权限和断网后本地记忆检查；当前系统测试最小实现面向Hermes，断网存储检查不代表云端模型离线推理。
- **桌面触达。** 应用入口与图形会话完成通知，便于在UKUI桌面发起测试、返回报告。
- **面向目标机封装。** 原生.deb构建脚本封装程序、题库和冻结的Python依赖；发行版本验收按实际记录确认。
- **持续回归。** 固定试卷、历史轮换、确定性参数生成，支持复现与扩大覆盖。
- **运行条件可核查。** 网关对经过它的请求设置模型并按来源记账；可选文件系统沙箱限制写入范围。

## 快速开始

需要 Python 3.11 或更新版本及 uv。先用模拟适配器检查全流程：

```bash
uv sync --frozen --group dev
uv run memhall doctor
uv run memhall run -a mock -c quick --judge scripted -o runs
uv run memhall verify runs/<run_id>
```

`mock` 不调用真实模型，只验证工具流程。打开界面：

```bash
uv run memhall ui
```

真实评测需要已安装、已配置的智能体，以及模型和评委端点。配置项见 [.env.example](.env.example)；使用新测试目录，按适配器说明准备测试环境。

```bash
# 所有对照运行使用同一份固定试卷
uv run memhall sample -c cases/full --count 45 --seed 20261016 -o build/paper.json
uv run memhall run -a hermes --paper build/paper.json --judge llm -o runs
uv run memhall verify runs/<run_id>
```

内置适配器：`mock`、`hermes`、`kylinbot`、`kylin-agent`、`openclaw`、`hermes-local`、`claude-local`、`qwen-local`、`opencode`。接入与扩展见 [适配器契约](docs/contracts/README.md) 和 [扩展说明](docs/benchmark-v2.md)。系统级测试涉及真实重启、时间与网络操作，应在专用测试环境单独安排。

## 结果怎样阅读

先确认题包、配置与有效范围，再看六维结果，最后进入代表性题目核查证据。规则探测检查文件等确定性条件，在线语义探测调用LLM评委；无法可靠判断的项目进入复核，运行无效单列。计分项与诊断项按题目定义分别处理。

归档包含题目、原始证据、逐项判定、指标和报告。`verify` 检查完整性与变化，人工复核检查语义判定；两者承担不同工作。比较结果应同时报告输入条件、重复次数与有效分母。

## 安装与交付

openKylin原生打包入口为 [build_deb_vm.sh](scripts/build_deb_vm.sh)，Windows打包入口为 [build_exe.sh](scripts/build_exe.sh)。构建准备需要获取依赖；.deb安装阶段使用包内Python依赖，系统需满足包声明的基础条件。2026-10-16 的 2.0.0 已在现有 VM 完成升级、启动、两轮 mock、verify 与 stability 验收，详情见[装机记录](docs/audit/deb-2.0.0-20261016.md)；干净机安装和显式卸载尚未验收。

[提交材料目录](docs/contest/submission/README.md) 提供当前作品介绍、完整报告与演示入口。历史文档和数据保留用于追溯，不作为当前排行榜。

## 开发与协作

```bash
uv run pytest tests/ -q
uv run ruff check .
uv run mypy
```

[CI配置](.github/workflows/ci.yml) 包含题库门禁、离线判卷自检与Linux/Windows测试矩阵；openKylin系统级行为另在目标环境验证。

[数据集说明](docs/dataset-card.md) · [接口契约](docs/contracts/README.md) · [环境说明](environment.md) · [版本记录](CHANGELOG.md) · [安全反馈](SECURITY.md)

欢迎贡献新任务、适配器和真实失败案例。许可证：[Apache-2.0](LICENSE)。

## 开源治理与长期维护

项目由维护团队负责，以GitHub为主仓库、Gitee为镜像，通过Issue、PR和版本记录公开协作。题库、评分口径、适配器及交付文档共同维护；涉及结果可比性的变更记录影响与验证依据。

| 内容 | 执行规则与入口 |
|---|---|
| 许可与依赖 | [第三方说明](THIRD_PARTY_NOTICES.md)：锁定依赖，收集各组件许可原文，分发义务另行复核 |
| 贡献与审阅 | [贡献指南](CONTRIBUTING.md)：问题复现、题库标准、接口约定、测试和文档同步 |
| 职责与决策 | [治理规则](GOVERNANCE.md)：核心维护、模块复核、贡献参与及交接 |
| 维护与发布 | [维护手册](docs/maintenance.md)：问题优先级、检查命令、发行校验和支持边界 |
| 长期路线 | [路线图](docs/ROADMAP.md)：稳定交付、证据与评分改进、麒麟场景共建 |

仓库提供可执行的文档入口检查、运行依赖清单与许可材料生成、发行附件SHA256生成及复验工具；前两项接入CI。许可资料收集不替代对分发条件的人工审核。

```bash
uv run python scripts/check_governance.py
uv run python scripts/dependency_inventory.py --output build/compliance --check
```

欢迎麒麟用户贡献办公与研发场景，欢迎框架开发者完善适配器和证据采集。长期工作通过公开问题、贡献审阅和版本验收逐步推进。
