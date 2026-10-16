# 2.0.0 deb与Windows exe核验（2026-10-16）

本次核对本地交付文件。版本号均为2.0.0；Windows exe按当前工作快照重新构建，原exe保留为备份。该检查不代表GitHub Release附件已经更新。

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `dist/memhall_2.0.0_amd64.deb` | 52573520 | `4016b61bfb0a7672621846c9d33981fc3a02f0511287f9522c2a27af2ef7756e` |
| `dist/麟阁MemHall-单文件版.exe` | 55875246 | `4c228783f1449826b1e59751b05e76af75b0f9963b7e0a417d2ba8661764f332` |
| `dist/MemHall-onefile-v2.0.0.exe` | 55875246 | `4c228783f1449826b1e59751b05e76af75b0f9963b7e0a417d2ba8661764f332` |
| `dist/refresh-20261016/MemHall/MemHall.exe` | 15710317 | `7c640776754d653600842f78333582daf33bc40e2152ea9ecf3877b77743cccd` |

## deb

包声明为amd64，要求Python 3.12。内置项目wheel的65个程序文件与当前源码逐字节一致。包哈希与[目标机升级验收](deb-2.0.0-20261016.md)记录一致；保留已有两轮离线mock、verify与stability验收结果。本次未重新安装，也未触发真实智能体评测。

## Windows exe

目录版与单文件版分别在独立测试目录执行：版本命令、离线mock quick、归档verify，以及本地UI启动。两种形式均通过；每轮9/9计分探测有效。UI返回版本2.0.0，页面与当前源码一致，配置不含第二评委设置。模拟分数用于验证流程，不表示真实智能体能力。

最新单文件使用 `dist/麟阁MemHall-单文件版.exe` 或等内容的 `dist/MemHall-onefile-v2.0.0.exe`；最新目录版位于 `dist/refresh-20261016/MemHall/`，分发时需保留整个目录。旧的 `dist/MemHall/` 为历史目录版。

构建日志、原包备份、机器可读核验记录与流程归档保留于 `build/package-check-20261016/`、`build/exe-check-20261016/`。
