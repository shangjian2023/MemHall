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

## 依赖、文档与兼容性

- [ ] 新增依赖已核对来源、许可、分发要求，并同步锁文件（无新增则不适用）
- [ ] 功能、配置、接口和评分口径对应文档已同步
- [ ] 已说明兼容性、实际验证范围与尚未验证事项
- [ ] 第三方图文、代码和数据具有来源及使用依据

## 证据

<!-- 测试输出 / 截图 / runs 产物路径，方便复核 -->
