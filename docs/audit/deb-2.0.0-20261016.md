# 2.0.0 deb 装机修订验收（2026-10-16）

本次在现有 openKylin VM 上从 1.3.2 升级到本地源码构建的 2.0.0。并非对外发行版本追踪或干净机器验收。

- 当前 UI、配置 API、环境模板与新生成判定已统一为单评委；不再展示第二个判卷 LLM 设置。历史双判归档仍兼容读取，未改写历史证据。
- 本次相关测试 64 项通过；相关 Ruff 与 mypy 检查通过。
- 新依赖隔离到 `/usr/lib/memhall/2.0.0/pylib`。升级保留旧运行时，避免中断既有 Hermes 测评和 OpenClaw 等待任务。旧依赖文件校验在升级前后相同：`97883f51dd563fa647cd992afa0b2aa33d4b8edf48820f33400f0a5e6691db53`。
- 原用户配置保留。安装后的 `/api/meta` 返回版本 2.0.0。
- 两次独立 HOME 的 mock quick 执行、各自 verify、双轮 stability 均以退出码 0 完成。未进行干净机安装与显式卸载验收。
- 最终安装源码目录：`/home/okim/memhall-releases/deb-2.0.0-clean-20261016-79353466f1`。
- 最终 deb：`dist/memhall_2.0.0_amd64.deb`；SHA256：`4016b61bfb0a7672621846c9d33981fc3a02f0511287f9522c2a27af2ef7756e`。

安装与执行原始记录在 `build/deb-refresh-20261016/install-final-result.json`、`acceptance-result.json`。历史源码、归档、仍被后台任务使用的旧依赖作为运行与审计证据保留，未进行破坏性清除。

演示使用独立 HOME 的 8301 服务，实际执行离线 mock；真实结果回放选择 `20261015-054036-kylin-agent`：45 题、87 探测（66 计分 + 21 诊断），总分 77.27%，66/66 计分有效，计分无 invalid、human_review 或 degraded，归档 verify 通过。该单轮展示不代表最终排名。影片明确标注 mock 与真实归档，中文字幕、无配音。

成片位置：`dist/麟阁MemHall-2.0-核心功能与使用流程-中文字幕.mp4`。原始 OBS 录制、时间线、字幕与编码日志保留在 `build/recording-20261016` 和 `build/deb-refresh-20261016`。

## 成片与最终状态

- 成片时长 00:04:11，1920×1080，30fps，H.264/yuv420p，无音轨；抽查 12 个时间点，完整解码退出码 0 且无错误。
- 成片 SHA256：`095f3042ba4ca7b947d244d7a869167e602940a0aad1422592e9ec75471e482b`。详细记录：`build/deb-refresh-20261016/video-validation.json`。
- 最终复核时 Hermes PID 60720 已退出，轮次 `20261016-031002-hermes` 已生成报告，但 verify 报 `persist-005:probe` 缺少 dialogue；未将它作为视频精选结果。未修改或补造该证据。
- OpenClaw 等待进程 PID 76787 保留，观察状态为 `T (stopped)`；未发送信号或擅自启动新的真实评测。记录见 `build/deb-refresh-20261016/final-background-status.json`。
- OBS 已停止录制并停用临时实时采集源。独立演示 UI 与主 UI 保留。
