# LLM 质量评测

> 日期：2026-06-18  
> 目的：ASR 过关不等于 Copilot 过关。必须单独评测 LLM 对会议状态、建议和纪要的质量。

## 1. 评测对象

LLM 在本产品中承担四类任务：

- 从稳定 transcript 中抽取结构化状态。
- 根据状态和缺口规则生成建议卡片。
- 对 ASR 文本做保守归一化。
- 会后生成带证据的工程纪要。

LLM 不能承担：

- 无证据推断。
- 自动裁决技术方案。
- 自动分配责任人。
- 替代正式审批。

## 2. 输入数据集

每条样本至少包含：

- raw transcript。
- normalized transcript。
- evidence spans。
- 人工标注的 decisions、action_items、risks、open_questions、suggestion_cards。
- ASR 错误版本，用于鲁棒性测试。

样本类型：

- 完美转写。
- 轻微 ASR 错误。
- 关键技术实体错误。
- 多人插话。
- 候选决策后被推翻。
- 行动项 owner 不明确。
- 故意缺少 rollback/test/metric 的上线场景。

## 3. 指标

### 决策抽取

- precision。
- recall。
- confirmed/candidate/rejected 状态准确率。
- 被推翻决策识别率。

门槛：

- MVP precision/recall >= 85%。
- confirmed 决策必须有 evidence span。

### 行动项抽取

- action item precision/recall。
- owner 准确率。
- deadline 准确率。
- owner/deadline 不确定时的 abstain rate。

门槛：

- action item precision/recall >= 85%。
- owner/deadline 准确率 >= 80%。
- 不确定时宁可标记待确认，不允许猜。

### 风险和缺口

- risk precision/recall。
- suggestion card precision。
- suggestion card false positive rate。
- suggestion usefulness rate。

门槛：

- 建议卡片误报率 <= 20%。
- 每场有效建议 3-8 条。
- 用户保留率 >= 40%。

### 证据链

- evidence citation accuracy。
- quote 是否来自原文。
- 时间戳是否覆盖正确片段。
- 无证据结论数量。

门槛：

- evidence citation accuracy >= 95%。
- 无证据正式结论为 0。

### 幻觉与沉默

- hallucinated_entity_rate。
- hallucinated_decision_rate。
- low_confidence_abstain_rate。

门槛：

- 幻觉实体和幻觉决策不能进入正式纪要。
- 低置信度场景必须能沉默或降级。

## 4. 评测流程

1. 固定 transcript 和人工标注。
2. 运行状态抽取 prompt。
3. 校验 JSON schema。
4. 对比人工标注。
5. 运行建议卡片生成。
6. 对比人工建议和规则触发条件。
7. 运行会后纪要生成。
8. 检查证据引用和幻觉。
9. 输出分数、失败样本、prompt/model 版本。

## 5. 报告必须记录

- model。
- endpoint 类型。
- prompt version。
- schema version。
- temperature。
- input transcript hash。
- output JSON。
- parse failure。
- timeout。
- retry 次数。
- 人工评审结果。

