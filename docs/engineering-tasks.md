# 工程化整改任务清单

> 来源：2026-10-02 全库排查（正确性 → 凭据卫生 → 判卷可信度 → 仓库卫生 → 基建 → 发布）。
> 目标：把"联调现场"痕迹清掉，达到可用化/标准化/官方化。
> 排期锚点：比赛截止 **10-11**；与主线剩余任务（统一模型对照 / 全量跑 / 演示）并行，P0/P1 必须落地，P2 按余量裁剪。

优先级含义：P0 = 正确性硬伤，半天内可修；P1 = 评审最扎眼的可信度/安全问题；P2 = 官方化外观与基建，可裁。

> 进度：P0 六条已于 2026-10-02 落地（mock 基线分数不变：full 62.2% / quick 71.4%，71 测试全绿）。顺带修掉 ruff F 检查逮到的存量真 bug：`_open_app_window` 引用未导入的 `shutil`（pywebview 缺失时的 Edge 回退路径会 NameError）。lint 全库门禁时发现 gen 生成题无字面 canary——按 `source=generated` 豁免（seed 随机 token 防污染等价，存档稳定性约束生成器不可改）。
>
> P1 六条同日落地（T13-T16 仓库卫生同日完成：okim-bench/out 运行产物出库并归档标注、比赛材料移 docs/contest/、顶层 8 个空目录清理、README 文档表加状态列+过程稿头部标注、.pytest_cache 进 gitignore）。
>
> 原 P1 记录：judge 判卷换 httpx（单题总预算 JUDGE_TOTAL_BUDGET=300s 兜底，替代 12 分钟级阻塞）；判卷离线自检进 CI（ScriptedJudge × 金标准错判零容忍——实现过程中顺带修掉两个真实宽松缺陷：①新旧并列回答被单侧子串命中误判 ②同模板异值回答被锚例 0.8 重叠误配，均改为转人工）；`_answer_for` 精确匹配优先；凭据全走 stdin/base64（hermes 的 API key、sudo 密码不再落 VM 命令行）；SSH 主机密钥 TOFU 钉扎（~/.memhall/known_hosts，指纹变化拒连）；实验室 IP 移出发行默认值（.env.example/remote/discovery，vm_* 运维工具保留可覆盖的私有默认）。mock 基线仍为 62.2%（六维逐项一致），83 测试全绿。
>
> **T17-T20 工程基建同日完成（2026-10-02）**：logging 全链路（`memhall.logs`，CLI `-v/--verbose`；runner 逐 case/耗时、SSH 命令/耗时、判卷重试/预算耗尽；窗口 exe 的 memhall.log 接同一套）；ruff+mypy 进 CI（新 lint job，规则集 E/F/W/I/B/UP/SIM/RET；存量 127 条一次清零——顺手修了 zip 无 strict、两个变量遮蔽、test_opencode 的裸 Exception 断言，mypy 拧出 judge._client 无类型标注等 14 处）；manifest 记 tool_version+python（--version 已在 T01 做）；dotenv 收敛 `memhall.env` 单源（值含 " #" 截断修复，引号包裹可保真；UI/CLI 两份手搓解析删除）。mock 基线仍逐字不变（62.2%/71.4%），86 测试全绿。
>
> **主线·统一模型对照机制化落地（2026-10-02，本清单外主线任务）**：`memhall gateway`（src/memhall/gateway.py，自研薄层 ~250 行，零新依赖——cc-switch 是 GUI 配置改写器、LiteLLM 对离线 deb 太重，调研后弃用）；model 强制改写 + 凭据单点（dummy key `memhall-<agent>` 兼作记账归因）+ 逐请求 token 记账（`--report` 出账单）；六适配器接入（hermes×2/opencode/qwen-local 走 GATEWAY_URL，kylinbot config.toml 自动改指+备份走 GATEWAY_VM_URL，claude-local anthropic 协议显式拒绝）；manifest 记 model_backend + compare 口径不一致告警；上游 TLS 断流重试。**实跑验证三车道全通**（hermes VM 100%、hermes-local 100%、kylinbot 100% 单用例；账单 hermes 91k/hermes-local 63k/kylinbot 137k tokens 全 0 错误）。顺手修真 bug：hermes send 的 stdin 两段式在管道上丢数据（head 吞读）+ env 文件裸赋值不 export——T10 改造引入、VM 实跑逮到，重写为 cat 落盘再拆行；kylinbot 拨钟密码内联（T10 漏网）。

---

## P0 · 正确性硬伤（全部做完 ≈ 1.5 天）

- [x] **T01 版本号单一来源化**（D，0.5h）
  - 证据：`pyproject.toml`=0.2.1，`src/memhall/__init__.py` 还是 0.1.0（已漂移），`scripts/build_deb_vm.sh` 硬编码 3 处，README 2 处
  - 做法：`__init__.py` 改读 `importlib.metadata.version("memhall")`；deb 脚本从 pyproject 提取版本号
  - 验收：全库 grep 版本号只出现在 pyproject + README 状态节；`memhall --version` 输出正确（与 T19 一起做也行）

- [x] **T02 修 `memhall report` 安装态路径**（D，1h）
  - 证据：`cli.py:174` 用 `parents[2]` 反推源码树；deb 装机后指向 `/usr/lib/memhall`，找不到 cases，六维指标静默算空
  - 做法：case 目录解析收敛到一处（复用 UI 的 `_case_roots()` 逻辑或抽公共函数），report/run/aggregate 共用
  - 验收：deb/exe 装机后 `memhall report <run_id>` 能出完整六维报告

- [x] **T03 heldout run 的 report 支持**（D，0.5h）
  - 证据：`cases/heldout/` 不入仓库（防背题，正确），但 `cmd_report` 只从仓库 `cases/` 找用例 → 换机器对 heldout run 出报告直接 KeyError（本机有 heldout 目录所以没撞上）
  - 做法：run 目录落盘用例快照（或 manifest 记 case 源），report 优先从 run 目录读
  - 验收：新 clone 的机器上对 heldout run 执行 `memhall report` 不报错、指标完整

- [x] **T04 适配器注册表收敛**（A，1h）
  - 证据：`cli.py:70-91` 与 `ui/app.py:252-271` 两份逐字重复的 if/elif；加适配器要同步改两处
  - 做法：单一注册表 `{name: 类路径}`，lazy import 保留；CLI/UI/discovery 的 `/api/adapter-status` 共用
  - 验收：新增一个假适配器只改一处，CLI 和 UI 下拉同时出现

- [x] **T05 `fs.path_exists` 去掉判卷机本地兜底**（C，0.5h）
  - 证据：`rules.py:91` 无 fs_diff 证据时 `Path(target).expanduser().exists()` 检查的是判卷机磁盘——宿主机恰好有 `~/dev` 之类路径时 mock 分数被污染
  - 做法：无证据 → 未决（HUMAN_REVIEW 或按出题口径映射），绝不查本地盘
  - 验收：删掉 mock 的 fs 证据后，reuse 判定不随评测机磁盘状态变化；相关测试补上

- [x] **T06 CI 题库门禁覆盖全部用例集 + quick 去副本化**（B，1.5h）
  - 证据：`lint_cases.py:113` 默认只查 `cases/full`，chains（含 chain-004）/gen/quick 不进门禁；quick 是 full 的手工复制副本且行尾已漂移（quick=LF、full=CRLF）
  - 做法：lint 默认遍历 full+gen+chains（heldout 现场生成不在库，跳过）；quick 改为构建期从 full 复制/引用清单，不入 git
  - 验收：CI 对全部入库用例跑 lint；改 full 某题后 quick 自动跟随

## P1 · 判卷可信度（C 主责，≈ 1.5 天）

- [x] **T07 `_answer_for` 匹配收紧**（C，1h）
  - 证据：`engine.py:43-45` 双向子串匹配问题↔回复，同 case 两个探测问题措辞相近时取错回答
  - 做法：精确匹配优先 → 再退子串；按 phase 收窄搜索范围
  - 验收：构造同前缀双问题的用例，判定各取各的回复

- [x] **T08 判卷自检进 CI**（C，1h，性价比最高的一条）
  - 证据：`scripts/judge_selftest.py` 存在但 CI 不跑——ScriptedJudge 的尾段/bigram 启发式是对自家 mock 调参的，改一行 `_norm` 历史分数就漂移且无人知晓
  - 做法：ci.yml 加一步跑 judge_selftest（离线、确定性、秒级）
  - 验收：改坏 `_norm` 的分支 CI 变红

- [x] **T09 judge HTTP 换 httpx + 总超时**（C，2h）
  - 证据：`judge.py:255-270` 手搓 `http.client` + 8 次指数退避（单题最长阻塞约 12 分钟），无总预算、无并发；hermes 侧已实测 httpx keep-alive 稳
  - 做法：换 httpx（项目内已有实证），单题总超时上限（如 5 分钟），可配并发
  - 验收：judge 端点挂死时单题在总超时处放弃并走降级路径，不拖垮整轮

## P1 · 凭据卫生（D 主责，≈ 1 天）

- [x] **T10 API key / sudo 密码不再内联 shell 命令行**（D，3h）
  - 证据：`hermes.py:44-47`（`export DEEPSEEK_API_KEY='...'`）、`hermes.py:131,140` 与 `systests.py:54`（`echo '{password}' | sudo -S`）——VM 内 ps/history 可见，值含引号即命令碎裂；消息体已走 base64，凭据却裸拼
  - 做法：凭据走环境变量注入（SSH `exec_command(environment=...)`）或 stdin 传 sudo；key 单引号问题随之消失
  - 验收：VM 上 `ps aux | grep hermes` 看不到明文 key；key 含特殊字符不炸

- [x] **T11 SSH 主机密钥钉扎**（D，1h）
  - 证据：`remote.py:39` `AutoAddPolicy` 不校验主机密钥
  - 做法：首次连接记录指纹到 `~/.memhall/known_hosts`，之后不匹配即拒绝并提示
  - 验收：改 IP 指向别的机器时连接报错而非静默接受

- [x] **T12 发行默认值去实验室环境**（D，0.5h）
  - 证据：`.env.example` 预填 `VM_HOST=192.168.61.133`，`remote.py:28` 同 IP 做默认值——个人实验环境事实进了发行物
  - 做法：默认值留空 + 文档给配置示例；本机配置走 .env
  - 验收：新机器解包后不带任何 192.168.* 痕迹

## P2 · 仓库卫生（E 主责，半天）

- [x] **T13 okim-bench 归档 + out/ 产物出库**（E，1h）
  - 证据：`okim-bench/out/` 提交了运行产物（brain_final.db、radar png、metrics.json）；okim-bench 与 `src/memhall/scoring` 双实现（judge.py 自注"移植自 okim-bench"）
  - 做法：`git rm -r okim-bench/out`；okim-bench/README 头部加"W1 原型，权威实现在 src/memhall/scoring"标注
  - 验收：git 里无 out/ 目录；README 标注在位

- [x] **T14 比赛材料移出仓库根**（E，0.5h）
  - 证据：`ai_open_kylin_..._XaCQyZepC8.pdf`、`challenge_fitz.txt` 躺在根目录
  - 做法：移入 `docs/contest/`（保留可追溯）或出库
  - 验收：仓库根只有工程文件

- [x] **T15 清理死目录与过时文档头**（E，0.5h）
  - 证据：顶层 8 个空目录（adapters/ cli/ runner/ scoring/ schema/ generators/ evidence/ report/，旧布局残留）；`src/memhall/__init__.py` docstring 还描述已不存在的 evidence/、cli/ 子包
  - 做法：rmdir 空目录；docstring 改成当前真实结构
  - 验收：顶层无空目录；docstring 与实际一致

- [x] **T16 docs 分级**（E，1h）
  - 证据：`docs/` 混着带日期临时文档（environment-vbox-20260924.md）和带版本号草稿（okim-bench-系统设计-v0.1.md），与正式契约文档不分级
  - 做法：定稿/契约/归档三层目录或在 README 索引标状态
  - 验收：新读者按 README 文档表 30 秒分清"权威文档"与"过程稿"

## P2 · 工程基建（D 主责，1-2 天）

- [x] **T17 引入 logging**（D，3h）
  - 证据：全库 0 处 `import logging`，长评测（hermes 单轮小时级）挂掉后无日志可查，只有 print
  - 做法：runner/orchestrator、adapters、judge 走 logging；CLI 加 `--verbose`；exe 窗口模式已有的 memhall.log 接同一套
  - 验收：评测中断后能从日志定位到 case/step/命令

- [x] **T18 ruff + mypy 进 CI**（D，2h）
  - 证据：无任何静态检查配置；`orchestrator.py:164` 访问 `store._items` 私有成员、`orchestrator.py:94` 生产代码 assert，这类问题无门禁
  - 做法：ruff（含 flake8 规则集）+ mypy（先宽松档）进 ci.yml，存量告警一次清零
  - 验收：CI 静态检查绿；故意加 `assert 1==2` 类问题会被拦（mypy 拦不了 assert——换成 ruff 能拦的例子：未用变量/F-string）
  - 落地注：私有访问改走 EvidenceStore.items()、生产 assert 改显式 ValueError；存量 127 条（E501/B905/SIM105/E741 等）一次清零，UP042（str-Enum→StrEnum）豁免——schema 表示层不动

- [x] **T19 `memhall --version` + manifest 复现元数据**（D，1h）
  - 证据：无 version 子命令；manifest 缺包版本/case 源目录，deb/exe 下 git_hash 恒 "unknown"（`orchestrator.py:167-175`）
  - 做法：CLI 加 --version；manifest 记 memhall 版本 + case 目录名 + （有 git 时）hash，安装态记包版本兜底
  - 验收：deb 装机的 manifest 能看出"哪个版本、哪套题"
  - 落地注：--version 在 T01 已做；本批补 manifest `tool_version` + `python`（git_hash/case_source 已在 P0 记录）

- [x] **T20 dotenv 解析与装机路径收敛**（D，2h）
  - 证据：手搓 dotenv 两份（`cli.py:215`、`ui/app.py:497`），`v.split(" #")` 值含 " #" 即碎；deb 装机路径逻辑 cmd_run（`/usr/share/memhall`）与 UI `_case_roots()` 各一套
  - 做法：抽 `memhall/env.py`（解析+候选路径），CLI/UI 共用
  - 验收：含 `#` 的配置值不被截断；路径逻辑全库只有一份
  - 落地注：装机路径已在 P0 收敛进 memhall.paths；本批收敛 dotenv 并修复截断（引号包裹值保真，测试固化）

## P2 · 打包与发布（D+E，1-2 天，时间紧可裁到只剩 T21）

- [ ] **T21 deb 依赖锁版本（可复现）**（D，2h）
  - 证据：`build_deb_vm.sh:10` `pip3 download` 不锁版本——README 称"可复现"但 wheel 是构建当日最新；uv.lock 根本没用于 deb
  - 做法：`uv export --frozen` 出 requirements 再 download；deb 脚本记录 lock 摘要
  - 验收：两次构建 wheel 清单哈希一致

- [x] **T22 deb 元数据达标**（E，1h）——2026-10-08：真实 maintainer + DEBIAN/copyright + changelog（构建版本号注入）；lintian 实跑随下次 VM 构建
  - 证据：Maintainer `memhall@openkylin.example` 占位符；无 `DEBIAN/copyright`、无 changelog
  - 做法：真实 maintainer + copyright 文件 + changelog；对照 lintian 清一遍
  - 验收：lintian 无 error 级告警

- [x] **T23 发布流程与社区文件**（E，2h）——2026-10-08：ci.yml tag(v*) 触发 release（Windows runner 出 exe 双发行物挂 Release，deb 仍目标机原生构建）；CHANGELOG.md 按版本记账；CONTRIBUTING/SECURITY/issue 模板三件套
  - 证据：无 GitHub Release/工件上传、无 CHANGELOG（README 状态节手工记账且已出现无版本号条目）、无 CONTRIBUTING/SECURITY/issue 模板
  - 做法：ci.yml 加 tag 触发 release（附 exe/deb 工件）；CHANGELOG.md 按版本记账（README 状态节迁入）；补三件套
  - 验收：打 tag 自动出 release 带产物

## 不整改（有意保留，评审问起照此口径）

| 项 | 口径 |
|---|---|
| mock 适配器 | 设计好的缺陷注入基线（DESIGNED_PROFILE 机器可读），不是偷懒 |
| UI 无鉴权 | 绑定 127.0.0.1 的本机工具；README 安全小节补一句"勿暴露端口" |
| systest 仅 hermes | 代码与文档均已声明，kylinbot 按同契约扩展 |
| hermes 回复解析 TUI 边框 | hermes 无结构化输出接口前的唯一途径；已锚定 v0.21.x，契约 adapter.md 标注风险 |

---

## 排期建议（与主线剩余任务并行）

| 时间 | 内容 |
|---|---|
| 10-02 晚 ~ 10-03 | P0 全部（T01-T06，1.5 天量）——都是半天级修复，先出 PR |
| 10-04 ~ 10-05 | P1 判卷（T07-T09）+ 凭据（T10-T12） |
| 10-06 ~ 10-08 | 主线让路：统一模型对照 + 全量跑（比赛核心得分项，优先于 P2） |
| 10-08 ~ 10-10 | P2 按余量挑：仓库卫生（T13-16，便宜且出观感）→ T21 deb 锁版本 → 其余能做多少做多少 |
| 10-10 ~ 10-11 | 演示彩排；未完成的 P2 条目移出比赛范围，赛后收尾 |

原则：**P0/P1 落地是底线（影响评测结果正确性与评审第一印象），P2 是加分项，不得挤占主线收尾。**
