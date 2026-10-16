# 第三方组件与材料

麟阁项目代码采用 [Apache-2.0](LICENSE)。第三方依赖、原生库、字体、图文和外部数据保留各自许可，项目许可证不覆盖它们。

运行依赖以 pyproject.toml 声明、uv.lock 锁定；deb 依赖导出见 packaging/deb-wheels.txt。新增或升级组件须检查来源、许可与分发条件。

生成当前平台的实际依赖与许可材料：

```bash
uv run python scripts/dependency_inventory.py --output build/compliance --check
```

输出包含 dependency-inventory.json、THIRD_PARTY_NOTICES.md 和 licenses/ 原文副本。保留它们并随对应平台的发行物交付。工具会识别缺失依赖、锁版本漂移及缺少许可资料，并以非零状态提示；许可元数据或许可文本存在不等于已完成条款兼容性审查。

2026-10-16 Windows/Python 3.11实测生成材料见[依赖与许可快照](docs/compliance/windows-py311-20261016/THIRD_PARTY_NOTICES.md)，覆盖40项运行依赖及63份许可资料副本。其他平台应在对应构建环境重新生成。

目前直接依赖包含Paramiko（其安装元数据标为LGPL-2.1）。发行者需结合打包形式履行相应义务；完整清单以当前环境生成结果为准。原生库及打包工具不完全属于Python运行依赖闭包，应单独核对，不得将这份清单称为完整SBOM或全面合规证明。

引用材料需记录来源、用途及许可依据；真实运行证据按题目与实验配置说明范围，禁止公开真实凭据和未公开题目。发现版权或许可问题，通过安全策略中的私密渠道联系维护者。
