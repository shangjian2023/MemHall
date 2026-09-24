# okim-bench 评分子系统（C 角色）

对应《okim-bench-系统设计》§4。原则：**能规则判的绝不用 AI**——L1 确定性检查 → L2 语义规则 → L3 双 LLM judge 交叉仲裁，全程证据引用强制化。

## 结构

```
scoring/
  models.py          # 数据模型：ProbeSpec / EvidenceBundle / ScoreResult / 五类错误 / 六维能力
  l1.py              # L1 确定性检查：store diff、答案比对、产物断言、PII 检出
  l2.py              # L2 语义规则：embedding 相似度（可插拔，默认离线 Jaccard 回退）
  judges.py          # L3 双 judge：跨家族、结构化输出、无证据票无效、不一致仲裁、Cohen's Kappa
  tree.py            # 判定决策树：L1 → L2 → judge 流水线 + needs_review 人工闸门
  metrics.py         # 六维能力分 + 五类错误矩阵 + 错误构成 + 规则评分率
  radar.py           # 六维雷达图（CJK 字体回退链，openKylin 用 fonts-noto-cjk）
  selftest_gold.py   # 金标准自检集（15 题，含 8 个故意错答案变体 + 脚本化 judge）
run_selftest.py      # 自检运行器：24 项断言，全过退出码 0
```

## 运行自检

```bash
python run_selftest.py
# 产出 out/metrics.json（六维指标）与 out/radar_gold.png（雷达图）
```

## 接入真实 judge

```bash
export JUDGE_A_BASE_URL=https://api.deepseek.com/v1   # 家族 A
export JUDGE_A_MODEL=deepseek-chat
export JUDGE_A_KEY=sk-...
export JUDGE_B_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1  # 家族 B（跨家族！）
export JUDGE_B_MODEL=qwen-max
export JUDGE_B_KEY=sk-...
```

```python
from scoring.judges import OpenAICompatJudge
ja = OpenAICompatJudge.from_env("A"); jb = OpenAICompatJudge.from_env("B")
result = score_probe(spec, evidence, judge_a=ja, judge_b=jb)
```

## 关键设计决策（评审可追问点）

- **五类错误判定**：错误持久化 > 混淆 > 遗漏按严重性优先上报；行为级命中过期答案单判"错误复用"
- **答对但库里没存 ≠ 正确**：qa 答对但记忆库无对应条目时判遗漏——防止"靠上下文运气"冒充长期记忆
- **无证据不得分**：judge 输出缺 evidence_refs 即票无效，强制走仲裁
- **低人工介入证据**：metrics.json 的 scored_by 字段统计规则评分占比（自检集 82%），needs_review 闸门只兜无法判定的少数
