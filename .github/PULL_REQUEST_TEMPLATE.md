## 改动说明

<!-- 一句话说清这个 PR 做什么、为什么 -->

## 自查清单

- [ ] `uv run pytest -q` 全绿
- [ ] `uv run ruff check .` 与 `uv run mypy` 零告警
- [ ] 新增用例已过 `uv run python scripts/lint_cases.py` 全检

## 评测口径改动（如有）

涉及判卷方式 / 分母 / 题集的改动：

- [ ] 已同步 dataset-card 与 README
- [ ] CHANGELOG 记的是口径而不是只有数字

## 产物纪律

- [ ] runs/ 产物未入库
- [ ] heldout 相关产物对外分享前已跑 `scripts/redact_heldout.py`

## 证据

<!-- 测试输出 / 截图 / runs 产物路径，方便复核 -->
