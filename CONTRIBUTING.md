# 贡献指南

麟阁 MemHall 是 openKylin 开源赛道参赛项目——面向 openKylin 生态的智能体
长期记忆评测基准。欢迎以 issue / PR 形式参与。

## 快速上手

```bash
git clone https://github.com/shangjian2023/MemHall && cd MemHall
uv sync --dev
uv run pytest -q                     # 147+ 测试应全绿
uv run python scripts/lint_cases.py  # 题库门禁（新题必过）
uv run memhall run -a mock -c cases/quick -o runs   # 离线冒烟
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

- 分支模型：单主线 dev，平台永不进分支名；PR 合并前 CI 四平台全绿
- 评测口径变更（判卷方式/分母/题集）必须同步 dataset-card 与 README，
  并在 CHANGELOG 记口径而非只记数字
- runs/ 产物不入库；heldout 相关 run 对外分享前先跑
  `scripts/redact_heldout.py`
