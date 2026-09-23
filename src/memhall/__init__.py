"""memhall —— 麟阁：面向 openKylin 生态的智能体记忆能力评测基准。

包结构（对齐 team-plan.md §4.1 仓库目录，src 布局）：
  schema/     pydantic 数据模型（契约 02/03 的实现，B/C 各管其半）
  adapters/   智能体适配器（契约 01，A 管；含 MockAdapter）
  runner/     三阶段剧本编排（D）
  evidence/   证据采集与存储（D）
  scoring/    规则验证器 + LLM judge（C）
  report/     指标与雷达图（C）
  cli/        memhall 命令行入口（D）
"""

__version__ = "0.1.0"
