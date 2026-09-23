# 契约 01 · AgentAdapter 智能体适配器接口

> v0.1 草案 · 2026-09-23 · owner：A（架构），消费方：D（runner）
> 目标：评测器只认本接口，不关心被测智能体是什么——换智能体 = 换一个适配器配置文件。
> 对应 design.md §3（三档接入）。

## 1. 职责边界

适配器 = 评测器与被测智能体之间的翻译官，只做四件事：**转发消息、导出记忆、导出操作、管理会话生命周期**。它不做任何评判、不修改用例内容、不产生证据判定——评分层的事一件不碰。

```
Runner ──调用──▶ AgentAdapter ──协议/进程──▶ 被测智能体 ──落盘──▶ 记忆存储
   ▲                 │                                    （SQLite/文件/...）
   │                 └── dump_memory() 直读 ◀────────────────────┘
```

## 2. 接口定义（Python 基类）

```python
from memhall.schema.evidence import MemorySnapshot, Action, Reply   # 见契约 03

class AgentAdapter:
    """所有智能体适配器的基类。子类或配置驱动实现全部五个方法。"""

    name: str                      # 智能体标识，如 "kylinbot"、"hermes"

    def reset(self) -> None:
        """清空智能体记忆，回到初始状态。
        - 语义：评测可复现的前提——每个 case 开始前必须能归零。
        - 实现自由：调官方接口、删记忆文件、或恢复 VM 快照（重实现，D 提供 helper）。
        - 若智能体物理上无法清零（如云端记忆），抛 MemoryResetUnsupported，
          runner 降级为「快照回滚」或标记该 case 的 reset_method 记入 evidence。
        """

    def send(self, session_id: str, message: str) -> Reply:
        """向智能体发一条用户消息，等待并返回其回复。
        - session_id：会话隔离的最小单位。同一 case 的 inject/probe 若分属不同会话，
          runner 传不同 session_id；适配器负责映射到智能体自己的会话机制
          （新开对话窗口/新进程/新 API conversation id）。
        - message：纯文本，UTF-8。用例中的 user 话术原样透传，**适配器不得改写**。
        - 返回 Reply（契约 03 §2.1）：至少含 reply 文本、时间戳、耗时、token 用量（可 None）。
        - 超时（默认 120s，可配）抛 AgentTimeout → runner 记运行无效，不判分。
        - 智能体崩溃/答非所问到无法回复 → 抛 AgentUnavailable，同上。
        """

    def end_session(self, session_id: str) -> None:
        """结束一个会话：关窗口/杀进程/调登出接口，任选能实现的方式。
        - 语义：模拟「用户关掉应用过几天再来」，是混淆段的核心动作。
        - 之后对同一 session_id 的 send 行为由适配器定义（允许报错，runner 不会复用已结束的会话）。
        """

    def dump_memory(self) -> MemorySnapshot:
        """导出智能体当前全部记忆内容。
        - 返回 MemorySnapshot（契约 03 §2.2）：结构化条目列表 + 原始文件字节（可选）。
        - 没有导出接口的智能体：直读其存储文件（SQLite dump / JSON / 原文件拷贝），
          format 字段如实标注（"sqlite"、"json"、"files"）。
        - 完全无本地存储的智能体（纯云端）：抛 MemoryNotDumpable，
          评分层自动降级为纯行为判定（五态里丢「不该记的记下了」类，报告注明）。
        """

    def dump_actions(self) -> list[Action]:
        """导出智能体自上次 reset 以来的操作记录（工具调用、文件读写、命令执行）。
        - 来源优先级：MCP 调用日志⚠️ > 智能体自带日志 > auditd 兜底（D 提供 helper）。
        - 返回 Action 列表（契约 03 §2.3）；拿不到完整记录时返回能拿到的部分，
          coverage 字段标注完整程度，评分层按 coverage 决定「用错了/没用上」类判定是否可信。
        """
```

异常类型（`memhall.schema.errors`，随契约冻结）：

| 异常 | 触发 | runner 处理 |
|---|---|---|
| `AgentTimeout` | send 超时 | case 标**运行无效**，不计分单列 |
| `AgentUnavailable` | 进程崩溃/无响应 | 同上 |
| `MemoryResetUnsupported` | 记忆无法归零 | 降级快照回滚或标 reset_method |
| `MemoryNotDumpable` | 记忆无法导出 | 该 case 评分降级纯行为判定 |

## 3. 配置文件（adapters/*.yaml）

每个被测智能体一份配置，`memhall compare --agents all` 即遍历此目录：

```yaml
# adapters/kylinbot.yaml
name: kylinbot
display_name: KylinBot
tier: custom            # zero-code(cli|api|mcp) | doctor | custom，见 design.md §3
adapter_class: memhall.adapters.kylinbot.KylinBotAdapter   # tier=custom 时必填
config:                  # tier=zero-code 时这里是协议模板参数
  launch_cmd: null       # cli 档：启动命令
  api_base: null         # api 档：OpenAI 兼容端点
  memory_paths:          # doctor 探测结果 / 人工确认的记忆文件位置（W1 实测回填）
    - "~/.local/share/kylinbot/memory.db"
  dump_method: sqlite    # sqlite | json | files | none
env:                     # 进 runner 注入的环境变量；**密钥引用 .env 变量名，不写字面值**
  AGENT_LLM_KEY: ${AGENT_LLM_KEY}
notes: "3.0 内置；记忆存储位置 W1 摸底（environment.md §7）"
```

## 4. 三档接入与契约的关系

| 档 | 实现方式 | 满足本契约的程度 |
|---|---|---|
| 零代码（CLI/API/MCP 模板） | 配置模板 + 通用适配器类 | send/end_session 天然满足；dump_memory 依赖 `memory_paths` 配置正确 |
| doctor 自动探测 | `memhall doctor` 扫系统 + 写监控定位记忆文件 | 产出零代码档配置（自动填 memory_paths），契约同上 |
| 定制适配器 | 继承基类（KylinBot 预计落此档） | 全部方法自行实现，本契约 1:1 约束 |

**验收口径**：任何接入方式，跑通 `memhall doctor → run --cases quick → compare` 三条命令即算接入成功（quick 集 = 契约 02 定义的冒烟子集）。

## 5. 稳定性要求（对准评分维度「稳定可复现」）

- 适配器自身**不得有随机行为**：不 sleep 随机时长、不用随机 id 之外的随机源；
- 两次 reset→send→dump 的结果中，非智能体因素（时间戳、耗时、随机 id）之外必须可复现；
- `dump_memory` 导出的快照带哈希指纹（runner 计算），同一状态两次 dump 哈希一致即达标——这是 runner 验收适配器的固定测试。

## 6. 开放问题（冻结前必须定）

- [ ] KylinBot 的会话机制是什么粒度（多窗口/多进程/单进程内多会话）→ 决定 `session_id` 映射方案（W1 摸底，09.24 D+E 装机时一起看）
- [ ] MCP 调用日志的可观测性⚠️（environment.md §7）→ 决定 dump_actions 首选数据源
- [ ] `Reply.token_usage` 的口径：智能体侧返回 or 从 Token 中心对账⚠️ —— M1 先留 None，W2 实测后定
