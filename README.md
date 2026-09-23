# 麟阁 MemHall

**面向 openKylin 生态的智能体记忆能力评测基准**

A Memory Benchmark for Agents on the openKylin Ecosystem

> 名字取自麒麟阁——汉代评定功臣、画像记名的殿堂：给记忆能力优秀的智能体立榜。

给跑在 openKylin 上的智能体测记性，全自动、可复现：用「教 → 隔 → 考」三阶段剧本驱动任意智能体，把对话日志、记忆快照、操作记录、文件变化统一为证据，自动评分并输出六维能力雷达图。

## 文档

| 文档 | 内容 |
|---|---|
| [design.md](design.md) | 总体技术设计（交付物 a 底稿） |
| [team-plan.md](team-plan.md) | 五人分工、4 周排期、协作规约 |
| [environment.md](environment.md) | 环境基线与搭建步骤 |

## 目录结构

```
memhall/
├── design.md / team-plan.md / environment.md   # 三份基准文档
├── docs/            # 方案文档（A 总稿）+ contracts/（接口契约）
├── schema/          # case/evidence/verdict schema
├── cases/           # 种子用例库
├── generators/      # 用例生成器
├── adapters/        # 智能体适配器（三档接入）
├── runner/          # 三阶段编排 + 系统级测试
├── evidence/        # 证据采集与存储
├── scoring/         # 评分引擎
├── report/          # 指标与雷达图
├── cli/             # memhall 命令行入口
├── packaging/       # .deb 打包配置
└── tests/           # 端到端测试
```

## 使用（开发中）

```bash
memhall doctor              # 自动发现已装智能体 + 生成配置 + 定位记忆文件
memhall run --agent <cfg> --cases quick    # 冒烟评测
memhall compare --agents all              # 批量对比出雷达图
```

## 许可

Apache-2.0（见 [LICENSE](LICENSE)）

## 状态

W1（2026-09-23 起）：接口冻结与假数据全链路冒烟。里程碑见 team-plan.md。
