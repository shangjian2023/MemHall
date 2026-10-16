# 麟阁 MemHall：作品与证据阅读导航

核对日期：2026-10-16。本文帮助读者从作品陈述找到题目、实现、测试和案例。机读索引见 [review-evidence.json](review-evidence.json)，所有路径以仓库根目录为基准，包含本次文件哈希及分发范围。

索引记录本次本地提交材料快照，不代表远端默认分支已包含全部文件或具有相同哈希。核验哈希时应使用随提交包提供的对应文件。

公开仓库对应的源码另列为 `repository_sha256`，并以 `repository_matches_submission` 标明是否与提交快照一致；已公开的本地证据使用 `public_path` 给出入口。

评分依据为 [openKylin命题PDF](ai_open_kylin_open_kylin_benchmark_XaCQyZepC8.pdf) 第2—3页、第4节。六项权重合计100%。赛道通用 `official-criteria.json` 的四维标准不替代本命题六维。

## 六项标准对应什么证据

| 命题维度 | 权重 | 作品内容与主要入口 |
|---|---:|---|
| 任务定义与通用性 | 15% | 统一适配完整智能体，原生与SSH共享契约。见 `src/memhall/adapters/base.py`、`adapters/__init__.py`、`adapters/remote.py`，以及 `tests/test_local_channel.py`。 |
| 数据设计质量 | 25% | 五套题包181题，含更新、干扰、边界与任务复用；固定试卷与参数生成。见 `cases/`、`schema/models_case.py`、`generation.py`、`sampling.py`、`tests/test_challenge_v2.py`。 |
| 自动评分能力 | 25% | 规则核验确定性条件，语义判卷输出类别、理由与证据，无法可靠判断时复核。见 `scoring/engine.py`、`rules.py`、`judge.py`、`schema/evidence.py`与两个真实案例。 |
| 稳定性与可复现性 | 15% | 固定输入、题目身份、历史轮换、归档校验与重复分析。见 `sampling.py`、`runner/case_rotation.py`、`report/stability.py`、`uv.lock`及目标机mock验收记录。 |
| 指标完整性 | 10% | 六维能力、错误类型、有效范围和逐题复查。见 `report/metrics.py`、`radar.py`、`panels.py`、`report.py`与 `tests/test_report_panels.py`。 |
| 创新性与工程可落地性 | 10% | 麒麟原生执行、系统事件、桌面通知、目标机.deb、模型归因与文件写入隔离。见 `systests.py`、`notify.py`、`vm.py`、`gateway.py`、`adapters/sandbox.py`与 `scripts/build_deb_vm.sh`。 |

表中省略前缀的实现路径均位于 `src/memhall/`。上述测试文件是测试源代码入口，不等于本次测试已执行通过；运行结果另以对应日志为准。

## 数据与案例的实际范围

通过项目题包加载器重新统计：full 45题、chains 4题、challenge-v2 72题、development-v2 39题、gen 21题，合计181题。目录中的题包描述YAML不计为题目。九个内置适配器包含真实框架、本机工具与模拟适配器，接入数量不等于已完成同条件比较的系统数量。

真实案例来自kylin-agent归档 `20261015-115547-kylin-agent`：

- `update-001`：目录已更新，后续任务仍在旧位置执行；回答和实际产物分别检查。
- `boundary-002`：明确禁止保留的合成信息在后续被复述；展示边界判据与原始响应。

精选记录位于[真实案例摘录](submission/current-experiment-20261015/selected-evidence/)。每个案例同时有 `.selection.json`，说明摘录范围：保留对话、记忆快照和行动记录，省略文件系统目录清单，不是完整原始证据文件。核查文件终态时需要完整运行归档。两个案例用于说明工具的解释与复查方式，不构成总体能力排名。临时口令为题目中的合成测试信息。

`mock` 为确定性模拟，检查工具流程和预设行为，不证明真实智能体能力。旧比较分数不作为本导航的结果结论。完整性校验检查缺失与变化，不能单独证明语义判卷正确或比较公平。

## 麒麟实现与交付核对

本机与SSH通道复用适配契约；图形会话支持桌面通知与打开结果；原生打包脚本封装题库、Python依赖和桌面入口。系统级测试最小实现面向Hermes，包含重启、拨钟、多用户权限与断网存储检查。断网后本地库存活不代表云端模型能够离线推理；可选文件沙箱不等同于网络出口管控。

当前本地目标机材料包括：

- `build/deb-refresh-20261016/install-final-result.json`：安装结果PASS，记录版本入口及配置、旧运行环境保留。
- `build/deb-refresh-20261016/acceptance-result.json`：两轮mock quick执行、两次归档verify与稳定性命令检查PASS。

上述记录的公开副本见[安装结果](evidence/20261016/install-final-result.json)与[流程验收](evidence/20261016/acceptance-result.json)，安装包检查见[deb与exe核验记录](../audit/packages-2.0.0-20261016.md)。它们不等同于真实智能体能力测试或完整卸载验收。发行包与视频是否完成交付，以对应版本附件和验收记录为准。

## 阅读入口

[提交材料索引](submission/README.md) · [项目设计](../../design.md) · [题库说明](../dataset-card.md) · [接口契约](../contracts/README.md) · [源码](../../src/memhall/) · [测试](../../tests/)
