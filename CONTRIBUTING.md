# 贡献指南

麟阁 MemHall 是 openKylin 开源赛道参赛项目——面向 openKylin 生态的智能体
长期记忆评测基准。欢迎以 issue / PR 形式参与。

## 快速上手

```bash
git clone https://github.com/shangjian2023/MemHall && cd MemHall
uv sync --frozen --group dev
uv run pytest tests/ -q
uv run python scripts/lint_cases.py  # 题库门禁（新题必过）
uv run memhall run -a mock -c quick --judge scripted -o runs   # 离线冒烟
```

## 出题（cases/）

出题四规矩：可判定、防污染（token 不与真实语料撞）、像人话、本土化场景。
新增用例必须过 `scripts/lint_cases.py` 全检（schema/覆盖矩阵/探测点引用/
canary 扫描）；判卷词表按 docs/contracts/02 的标准词表写。

## 适配器（src/memhall/adapters/）

新智能体接入实现 `AgentAdapter` 契约（reset/verify_reset/send/dump_memory/
fs_snapshot/clock_*），并在 `ADAPTERS` 注册表登记；统一模型车道优先走
`gateway_settings()`。提交前 `uv run ruff check .` 与 `uv run mypy` 零告警。

## 提交纪律

- 稳定主线为 main；从目标分支创建任务分支，通过 PR 审阅合入。dev 用于需要的集成工作；PR 合并前检查 CI 结果。
- 评测口径变更（判卷方式/分母/题集）必须同步 dataset-card 与 README，
  并在 CHANGELOG 记口径而非只记数字
- runs/ 产物不入库；heldout 相关 run 对外分享前先跑
  `scripts/redact_heldout.py`

## 许可与文档

提交内容需为本人有权贡献的代码或材料；项目代码贡献按Apache-2.0提供。第三方材料保留来源、版权与原有许可；不能将他人代码重新标为项目许可。新增依赖同步uv.lock，检查许可及发行义务，并运行：

```bash
uv run python scripts/check_governance.py
uv run python scripts/dependency_inventory.py --output build/compliance --check
```

新增功能同步使用说明；新增配置同步模板与说明；接口变更同步契约；题库或评分变更同步数据说明、README与CHANGELOG。PR描述给出实际测试范围，不以模拟运行证明真实智能体能力。

## 问题与审阅

Issue提供环境、版本、复现步骤、预期行为和脱敏证据。安全问题使用[私密反馈渠道](SECURITY.md)，不要公开凭据。维护者按影响安排处理，相关模块由维护者或PR指定评审者复核；重要争议保留讨论与决策依据。贡献者可从文档纠错、独立用例、复现测试开始参与。

职责见[治理规则](GOVERNANCE.md)，发布与交接见[维护手册](docs/maintenance.md)，后续方向见[长期路线](docs/ROADMAP.md)。
