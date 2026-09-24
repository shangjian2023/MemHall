# okim-bench 环境配置说明

> 配套 `team-plan.md` 使用。本文档记录开发与评测环境的**版本基线**（全员统一）与搭建步骤。
> 变更本文件的版本号 = 开 issue 通知全员，评测环境以本文档为准。

## 1. 目标系统：openKylin 3.0 桌面版（唯一评测环境基线）

| 项 | 值 |
|---|---|
| 版本 | openKylin 3.0 正式版（官网版本页口径 2026-09-05 发布） |
| ISO 文件名 | **openkylin3_0-20260905**（场景 = 桌面，本项目基线） |
| 构建批次 | 2026-09-05（桌面版当前批次） |
| SHA256 | 待回填：下载后执行 `sha256sum <iso>`（Git Bash）或 `certutil -hashfile <iso> SHA256`（CMD），与下载站校验值核对一致后回填此处 |
| 架构 | x86_64（X86 架构 / 通用机型） |
| 场景 | **桌面场景（desktop）** |
| 下载入口 | 官网 https://www.openkylin.top → 下载 → 多架构镜像下载站；筛选：架构 = x86_64，场景 = 桌面 |
| 备选镜像 | 阿里云 https://mirrors.aliyun.com/openkylin-cdimage/（国内首选）等官方镜像列表 https://www.openkylin.top/downloads/mirrors-cn.html；海外备用 https://cdimage.debian.org/mirror/openkylin.top/ |

### ⚠️ 版本选择记录（踩坑备忘）

- **下载站实测文件名对照**：`openkylin3_0-20260905`（场景列 = 桌面，✅ 基线，就用它）；
  `openkylin3_0_server-20260916`（场景列 = 服务器，❌ 禁用：无 UKUI 桌面、无内置 KylinBot）。
  赛题交付 e 硬性要求"在 openKylin 桌面环境下"录演示视频；KylinBot（第一个被测智能体）内置
  于 3.0 桌面版。服务器版只适合未来做无头批量跑分，不是本项目基线。
- 命名规律：旗舰桌面变体不带场景后缀，专用变体带后缀（`_server` 等）。桌面与服务器变体的
  构建批次日期不同属正常，**认准场景列 = 桌面**，不要只看文件名列表。
- **不用 AgentOS 版**：赛题原文全文无 "AgentOS" 字样，通篇要求 openKylin 本体（操作系统上编译
  运行 / 软件源分发 / 桌面环境演示）；"AgentOS" 仅出现在 OSCHINA 赛题报道（2026-08-24）的生态
  背景段。评分细则"与 openKylin 智能体操作系统架构良好集成"指 3.0 的产品定位（官方口号
  "开源智能体操作系统"），非 AgentOS 衍生版（其基座为 2.0 SP2，版本更旧）。
  AgentOS 可选作第二/第三智能体的获取来源或辅助环境，不作基线。

### 虚拟机配置（VMware Workstation，宿主机已装好）

| 项 | 值 | 说明 |
|---|---|---|
| 虚拟化 | VMware Workstation | 本机已装（VirtualBox 备用）；装完系统后 `apt install open-vm-tools` |
| CPU / 内存 | 4 核 / 8 GB | 跑智能体 + 评测工具的最低舒适线 |
| 磁盘 | 64 GB+（D 盘，宿主剩 257G） | 预留证据/快照空间 |
| 固件 | UEFI（关闭 Secure Boot） | 按官方安装指南默认走 |
| 快照 | 装完系统 + 基础依赖后立即拍 `clean-baseline` | 复现性根基；每次评测前回滚 |

### VMware 安装完整步骤（详细版，照做即可）

> 官方口径：社区论坛《系统安装指南》（bbs.openkylin.top，覆盖 X86 三种场景，含虚拟机）；
> 完整文档仓库 gitee.com/openkylin/docs。本节为结合上方基线配置的操作细化，预计 40–60 分钟。

#### 第 0 步 · 装机前检查（1 分钟）

- 校验镜像：Git Bash 执行 `sha256sum openkylin3_0-20260905.iso`，与下载站校验值比对，一致后回填 §1 表格的 SHA256 行；
- 确认 CPU 虚拟化已启用：任务管理器 → 性能 → CPU →"虚拟化：已启用"（宿主机 VMware 服务在运行则基本无虞）。

#### 第 1 步 · 创建虚拟机（5 分钟）

1. VMware Workstation → 文件 → 新建虚拟机 → **典型**；
2. 选 **"稍后安装操作系统"**——不要在这一步直接挂 ISO，否则 VMware 简化安装会接管流程，国产系统易在此翻车；
3. 客户机操作系统：**Linux → Ubuntu 64 位**（VMware 无 openKylin 档案；openKylin 为 Debian 系，此选项最稳）；
4. 虚拟机名称 `openkylin30`，位置 `D:\VMs\openkylin30\`（目录自建）；
5. 磁盘 **64 GB**，选"将虚拟磁盘拆分成多个文件"，**不勾**"立即分配所有磁盘空间"；
6. 点"自定义硬件"：内存 **8192 MB**、处理器 **4 核**（2 处理器 × 2 内核）、移除打印机（可选），完成向导；
7. 设 UEFI：选中虚拟机 → 管理 → 虚拟机设置 → **选项**选项卡 → 高级 → 固件类型 = **UEFI**，**Secure Boot 取消勾选**；
8. 回**硬件**选项卡 → CD/DVD (SATA) → "使用 ISO 映像文件"选中 `openkylin3_0-20260905.iso` → 勾选**启动时连接** → 确定。

#### 第 2 步 · 安装 openKylin（20–40 分钟，视磁盘速度）

9. **开启此虚拟机**，从 ISO 引导进 live 环境，双击桌面"安装系统"；
10. 安装途径选 **从 Live 安装**（标准全新安装；"从 Ghost 安装"是还原预制镜像，用于批量部署同配置机器，本项目不用）；
11. 后续依次：语言 **中文(简体)** → 时区 **Shanghai** → 键盘 **汉语**；网络、I/O 等步骤按默认；
12. 分区/硬盘步骤选**整盘安装（清除整个磁盘）**（虚拟机磁盘无其他数据，自动分区最稳，勿手动分区）；
13. 设置用户名/密码/主机名——**全队统一用户名 `okim`**（home 路径一致，脚本与文档不漂移）；
   **密码不入任何文档与 git**（本文档也不记录），测试机共用时口头同步；建议勾选"开机自动登录"
   （Runner 需反复重启会话，自动登录避免评测与录屏被登录页卡住）；
14. 等待安装完成，提示重启时：**先断开光盘再点"现在重启"**，防再次从光盘引导。断开方式二选一：
    - 菜单栏 虚拟机(M) → 可移动设备(D) → CD/DVD (SATA) → **断开连接**（见下图）；
    - 或 虚拟机设置 → CD/DVD → 取消勾选"启动时连接"。
    断开后点安装器上的"现在重启"，进 UKUI 桌面。

    ![安装完成：先断开 CD/DVD 再点"现在重启"](images/step14-install-done-disconnect-cd.png)

#### 第 3 步 · 进桌面后（10 分钟）

15. 开终端，执行下方"系统内基础依赖"命令块（含 open-vm-tools）；装完**重启一次**，分辨率自适应、主机↔虚拟机复制粘贴即生效；
16. apt 慢则将 sources.list 换为阿里云镜像（按官方镜像列表改）；
17. 打开预装「Token 中心」→ 登录绑定设备领 token → 试与 KylinBot 对话，确认可用；
18. 全部正常后：VMware → 虚拟机 → 快照 → 拍摄快照，命名 **`clean-baseline`**；此后每次评测前回滚到该快照。

#### 常见坑速查

| 现象 | 原因与处理 |
|---|---|
| 开机黑屏 / 进不了安装器 | 固件类型与 ISO 引导不匹配；UEFI 不行就换 BIOS（传统）再试 |
| 屏幕小、无法复制粘贴 | open-vm-tools 未装，或装后未重启 |
| 卡在分区步骤 | 回退改选全盘自动分区 |
| 重启后又进安装器 | 第 13 步未取消 CD/DVD"启动时连接" |

### 系统内基础依赖（进快照前装好）

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git open-vm-tools fonts-noto-cjk
# fonts-noto-cjk = 雷达图中文标签字体，缺了 matplotlib 出方框
# apt 源换阿里云镜像加速（按官方镜像列表改 sources.list）
```

## 2. 被测智能体（≥2 个，交付 e 硬性要求）

| 智能体 | 获取方式 | 备注 |
|---|---|---|
| KylinBot | 3.0 桌面版内置 | 入口：任务栏「小K」图标，或应用菜单搜"小K / KylinBot"；首次使用先开预装「Token 中心」登录绑定设备领 token 额度；W1 任务：摸清其记忆存储位置（本地文件 / SQLite，无导出接口则直读） |
| 第二个（OpenClaw / Hermes Agent / kylin-agent 三选一） | 装入同一虚拟机 | W2 内由 A 定死。选型情报：OpenClaw 与 Hermes Agent 均有公开学术评测先例（MPBench，arXiv 2606.04329，对二者做过"记忆写入+检索"两阶段评测），部署路径与记忆存储结构资料较全，OpenClaw 目前领先 |

## 3. 宿主机开发环境（Windows，已就绪）

| 项 | 本机现状 | 用途 |
|---|---|---|
| Python | 3.11.9 ✓ | 与目标环境 3.10+ 对齐 |
| git | 2.52 ✓ | 代码托管 Gitee 优先 + GitHub 双推（GitHub 走 ghproxy.net 前缀） |
| uv | 0.12.16 ✓ | 依赖与虚拟环境管理，`uv init` 建 monorepo |
| WSL2 | Ubuntu 24.04 ✓ | 仅当 Linux 工具箱：`.deb` 打包先在 WSL 用 `dpkg-deb` 练手，再进虚拟机干净验证 |
| openKylin WSL 镜像 | **不装（2026-09-23 决策）** | 官网虽有 Desktop WSL（≈6.1G）/轻量版（≈336M），但开发走 WSL2 Ubuntu 已覆盖 95%（Python 开发、dpkg-deb 练手，openKylin 为 Debian 系工具链几乎一致）；必须真 openKylin 的环节（.deb 试装、KylinBot 适配、录屏）全在 VMware 基线 VM。4 周赛期环境越少越好；仅当后期 CLI 调试嫌 VM 启动慢，D 可选装 |

## 4. LLM judge API

- OpenAI 兼容端点 ×2（双 judge 交叉用不同模型，如 DeepSeek + 通义/Qwen-Max），key 分列 `JUDGE_A_KEY` / `JUDGE_B_KEY` 放 `.env`（模板 `.env.example` 入库，真 key 不进 git）。
- 被测智能体的 LLM 后端复用同家账号但独立 key，与 judge 消耗分开记账。
- 账号由 E 保管，预算上限 + 每次 run 的 token 成本写入 manifest（C 记账，按端点分列）。

## 5. 环境就绪验收（W1 末，对应 M1）

- [ ] openKylin 3.0 桌面版虚拟机可开机进入 UKUI 桌面，`clean-baseline` 快照已拍
- [ ] 虚拟机内 python3 / git 可用，apt 换源完成
- [ ] Token 中心已登录，KylinBot 可对话
- [ ] WSL 内 `dpkg-deb --build` 打出空包并解包验证通过
- [ ] 宿主机 monorepo 初始化完成（按 team-plan.md §4.1 目录结构）
- [ ] judge 双端点各完成 1 次调用，成本记账链路通（C 验证）

## 6. 录制与可视化（W4 用，不进 clean-baseline 快照）

- 录屏：优先 UKUI 自带录屏工具，不够用再 `apt install obs-studio`（源里没有就下官方 Linux 包）；W4 初先试录 30 秒验证可用，别等交片当天才发现问题。
- 雷达图：matplotlib 由 uv 在项目内管理；中文渲染依赖 §1 已装入系统的 `fonts-noto-cjk`。
- 录屏与证据大文件归档走共享网盘素材库（E 维护），git 只留 manifest。

---

## 7. 实测更新（2026-09-23 晚，Kimi 实测）

> 本节为虚拟机实测结果，与上文冲突处以本节为准。

### 7.1 虚拟机实际情况（与 §1 基线的差异）

| 项 | 基线文档 | 实测 |
|---|---|---|
| 虚拟化 | VMware Workstation | **VirtualBox 7.2.6**（`D:\28012\`），VM 名 `openKylin3` |
| 用户名 | okim | **lcy**（home = /home/lcy，脚本路径注意替换） |
| Guest Additions | — | **未安装**（guestcontrol 不可用） |
| 宿主→虚拟机通道 | — | **已打通**：`VBoxManage screenshotpng` 截屏 + `keyboardputstring` 键盘注入 + SSH（NAT 端口转发 2222→22，paramiko 密钥/密码登录） |
| SSH | — | 已装 openssh-server 并 enable；虚拟机内执行助手见 `okim-bench/vm_exec.py`（密码走环境变量 VM_PASS，不落盘） |

### 7.2 KylinBot 记忆存储实测（W1 阻塞项关闭）

- 被测智能体：**KylinBot 0.7.5**，CLI 为 `kylin-bot`（`agent -m` 单发消息、`memory list/stats/clear`、`acp` JSON-RPC）
- **记忆库：`~/.kylinbot/workspace/memory/brain.db`**（SQLite + FTS5 + embedding_cache）
  - `memories(id, key UNIQUE, content, category, embedding, created_at, updated_at, session_id, namespace, importance, superseded_by)`
  - `superseded_by` = 动态更新版本链证据；`session_id` = 记忆来源会话证据；`key` 唯一约束（upsert 语义，同 key 写入即覆盖——出题时注意）
  - WAL 模式：拷贝取证必须连 `-wal`/`-shm` 一起拷
- 会话库：`~/.kylinbot/workspace/sessions/sessions.db`；人格：`workspace/SOUL.md`、`IDENTITY.md`、`HEARTBEAT.md`
- 记忆卫生守护：`workspace/state/memory_hygiene_state.json`（自动归档/清理，评测干扰项，出题需避开或利用）
- 桌面助手 kylin-aiassistant（小K GUI）：`~/.config/kylin-aiassistant/`（RECORD 消息表、knowledge_base、vector.db 为加密格式）
- **评测适配器已实现**：`okim-bench/adapters/kylinbot.py`（brain.db → 统一 MemorySnapshot），虚拟机内实测通过
- **待办（需人操作）**：KylinBot 对话需模型额度——虚拟机内登录「Token 中心」领 token，或 `kylin-bot onboard` 配 OpenRouter key

## 8. 真实「教→隔→考」闭环首跑（2026-09-23 晚，KylinBot 0.7.5 + DeepSeek）

链路：SSH（NAT 2222→22）驱动 `kylin-bot agent` → brain.db 取证（checkpoint 后 base64 导出）→ 宿主机 scoring.tree 评分。

| 阶段 | 操作 | 实测结果 |
|---|---|---|
| 教 | 存"日报模板路径 + 收件地址（滨江区）" | 写入成功：core 类 2 条 + conversation 原始消息 1 条 |
| 隔 | 3 个无关 filler 会话 | 正常；记忆库未被污染（另有 2 条 filler 未入库） |
| 考 | 问模板路径与地址 | **提取失败**：agent 答"没有记录"，但 brain.db 中两条 core 记忆均在（典型"存了取不出"） |
| 更新 | 地址改西湖区 | 未走 superseded_by 版本链，新建 `user_shipping_address`，旧 `user_mailing_address` 残留 → 双地址冲突 |
| 边界 | 临时端口 8080 | 入库（daily 类 + "仅限本次"时效标注），按严格契约判 false_persistence |

评分（5 probes，全部 L1 规则判定，0 AI 成本，0 人工复核）：
- retention 100 / recall 0 / update 0 / boundary 0，overall 0.2
- 产物：`okim-bench/out/realrun/{results.jsonl, metrics.json, radar.png}`

双 judge 真实链路（k3-agent + k2d8-preview，同家族仅验证链路）：判定正确率 6/6，双评委一致 4/4，Cohen's Kappa = 1.000，12 次 API 调用 63s。修复两处：ScoreResult 透传双评委原始判定（统计用）；_render 补 outdated_answers/distractor_patterns 并在 JUDGE_PROMPT 明确"命中过期值（含简称）优先判 false_reuse"。金标准自检回归 24 项全过。

**对题库（B）的输入**：KylinBot 当前"写入可用、读取未接入对话回路"，update 不走版本链 → update/false_reuse 类题目区分度天然高；boundary 题需区分"带时效标注入库"（可接受降级）与"无标注污染"（严格错误）两档契约。
