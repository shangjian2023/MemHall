# 演示视频脚本（10-08 版，5-7 分钟）

> 录制顺序按"最稳的先录"排：mock/UI 全离线零风险，真智能体结果用已跑完的
> 终榜产物（runs/_agg-*-dual），不现场跑马拉松。录屏 1440p，鼠标指哪说哪。

## 0. 开场（30s）

- 一句话问题：智能体都号称有记忆，但没有尺子——同一智能体隔天重跑分数能差 43 分。
- 出示对照：hermes 两轮 22.7% vs 66.1%（同一套题、同一模型、同一判卷）。
  「这不是 bug，这是我们要测的东西。」

## 1. 装机即用（40s）

- openKylin 桌面：菜单点「麟阁 MemHall」→ 浏览器开 UI（原生形态）。
- 体检页：四智能体检出（hermes/kylinbot/openclaw + mock）、LLM 网关绿灯。
- 一句 Tier 说明：deb 在目标机原生构建，48MB，`dpkg -i` 即用。

## 2. 跑一轮给你看（90s，全离线）

- 选 mock（离线演示适配器）+ quick 用例集 → 开跑。
- 直播页：三阶段剧本滚动（教→隔→考）、ask/reply 实时流出、记忆条数。
- 结束出报告：六维雷达 + 六态判定 + 写入卫生/过期调用率指标。
- 点题：mock 是设计好的缺陷注入基线（66.1%），它全绿说明尺子没坏。

## 3. 真智能体终榜（90s）

- aggregate 产物或 README 榜单：openclaw 67.1±5.8 / kylinbot 52.4±5.7 /
  hermes 44.4±30.7，统一模型 qwen3.7-plus 经网关。
- 六维雷达对比图（images/radar-hermes-vs-kylinbot.png）：
  kylinbot 检索强、hermes 峰高方差大——同模型下差异只能来自记忆系统。
- 统一模型网关：凭据单点 + model 强制改写 + 逐请求记账（`gateway --report`）。

## 4. 尺子的可信度（90s，差异化重点）

- 判卷：dual LLM judge + 锚例 + 干扰项（188 诱饵 100% 拒绝）+ Kappa 复核；
  未决从 41/89 降到 0-2。
- 每个判决带溯源（decided_by 进 manifest），verdicts 可离线重放对账。
- 测量边界页（README 榜单段/`stats_uncertainty.py` 输出）：
  同探测点两轮一致率 47/73/85%；配对符号检验谁显著谁不显著说清楚
  （kylinbot vs openclaw 38:38 并列）——「我们连自己排名的盲区都量化了」。

## 5. openKylin 独有深度（60s）

- 系统级测试：拨钟隔天（sudo date -s + 恢复校验）、重启存活、多用户隔离、
  断网存活——hermes 真机 4/4（截图或 runs/systest 报告页）。
- SSH 车道驱动真机智能体：reset 残留防线（lock 文件事故一语带过当彩蛋）。

## 6. 收尾（30s）

- 交付物：CLI/.deb/exe 三形态 + 47 题库（21 heldout 不可见池）+ 六适配器 +
  判卷契约文档 + CHANGELOG。
- 一句话：「给记忆能力优秀的智能体立榜」——麟阁。

---

## 录制注意

- UI 走原生形态（VM_HOST=127.0.0.1），别露 .env 内容或密钥；
- runs/ 页面只展开非 heldout 用例（heldout 先过 `scripts/redact_heldout.py`）；
- 分数口径口径口径：出口的每个数字带判卷方式与轮数（design §5 诚实优先级）。
