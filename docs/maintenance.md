# 维护与发布手册

## 日常维护

用户先按 Issue 模板提交版本、部署形态、复现步骤及脱敏证据。维护者确认问题影响，将安装运行、证据完整性和错误判定优先处理。安全问题按照 [安全策略](../SECURITY.md) 私密报告。

PR 合入前运行相关回归测试、题库检查和静态检查。新增配置、命令或接口同步更新 README、接口契约和使用说明；题库与评分口径变更同步 Dataset Card 与 CHANGELOG。修改跨越多个模块时，在 PR 中说明复核范围。

```bash
uv sync --frozen --group dev
uv run python scripts/check_governance.py
uv run python scripts/dependency_inventory.py --output build/compliance --check
uv run python scripts/lint_cases.py
uv run python scripts/judge_selftest.py --offline
uv run pytest tests/ -q
uv run ruff check .
uv run mypy
```

## 依赖及材料审核

新增或升级依赖时核对来源、许可证和分发要求；提交 uv.lock，必要时更新 packaging/deb-wheels.txt。依赖清单工具从当前环境实际安装的运行依赖生成版本、许可证元数据和许可文本副本，并与锁文件核对。输出反映对应平台，不代表其他平台或所有原生系统库的完整审计。

自动检查不能替代许可证条款审核。对于 LGPL 等含额外义务的组件，发布者须结合实际分发形式核对许可文本、修改说明、源码获取及替换/重新链接条件；不能仅因工具成功就宣称整个发行物完成合规审计。原生库、打包器和外部图文材料也应在发行前另行核对。参见 [第三方说明](../THIRD_PARTY_NOTICES.md)。

## 发布清单

- 确认版本来自 pyproject.toml，源码标签对应构建快照；不覆写已发布标签。
- 生成依赖清单并保留许可文本；对 Windows 与 openKylin 分别生成，随对应发行包交付。
- 运行离线 mock、verify、界面启动检查；openKylin 上另验收 deb 安装与桌面入口。记录实际范围，不以 mock 代替真实能力实验。
- 保留 CHANGELOG、验收范围、已知问题、源码提交和制品 SHA256。
- 使用下列工具为待发布附件生成清单；上传后核对远端哈希及可下载性。

```bash
uv run python scripts/release_checksums.py --output build/release-checks \
  dist/memhall_2.0.0_amd64.deb dist/MemHall-onefile-v2.0.0.exe
uv run python scripts/release_checksums.py --verify build/release-checks/release-manifest.json
```

release_checksums 会记录当前 Git 提交、工作树是否有修改，以及实际程序文件的指纹。工作树有修改时，提交号只是基线，不得冒充完整构建来源。生成校验文件后再改动附件，verify 会失败。

## 接续维护

交接材料包括：公开问题与优先级、支持版本、模块和接口说明、回归命令、发行构建步骤、依赖许可材料、未完成验收及最近的发布清单。密钥单独移交并轮换，不以聊天记录代替文档。支持能力随版本更新，未提供固定维护年限或响应 SLA。
