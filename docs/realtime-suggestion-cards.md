# 实时建议卡片规范

> 日期：2026-06-18  
> 目的：定义 Copilot 会中如何介入，防止变成高频打扰或泛泛总结。

## 1. 基本原则

- 低频：默认每场会议 3-8 条有效建议。
- 带证据：每条建议必须引用 transcript segment。
- 可撤销：用户可忽略、保留、标记误报。
- 不裁判：建议以追问和确认为主，不替用户下结论。
- 置信度不足时沉默。
- partial 文本只生成候选信号，final 文本才能触发强建议。
- 必须先通过工程语境门禁：非软件工程会议不输出工程缺口卡片。
- 卡片必须来自状态变化和缺口规则，不能由会后整段总结直接生成。

## 2. 卡片结构

```json
{
  "id": "suggestion_001",
  "type": "rollback_gap",
  "severity": "medium",
  "confidence": 0.82,
  "title": "回滚方案待确认",
  "suggested_question": "如果灰度期间错误率超过阈值，谁负责回滚，阈值是多少？",
  "reason": "检测到上线/灰度候选决策，但未检测到回滚 owner 和阈值。",
  "evidence_spans": ["ev_001", "ev_002"],
  "state_refs": ["decision_candidate_003"],
  "actions": ["keep", "dismiss", "copy_question", "mark_wrong", "convert_to_open_question"],
  "created_at_ms": 123400,
  "cooldown_key": "rollback_gap:release_review"
}
```

## 3. MVP 卡片类型

### owner_gap

触发：

- 检测到行动项或承诺动作。
- 没有明确负责人。

不触发：

- 头脑风暴阶段。
- 只是在列举可能方案。
- 文本置信度低或说话人混乱。

建议语句：

```text
这个行动项还没有明确 owner。是否需要确认由谁负责推进？
```

### deadline_gap

触发：

- 检测到行动项。
- 没有时间点、迭代、日期或会议后时限。

建议语句：

```text
这个待办似乎还没有 deadline。是否需要确认完成时间或下一次同步节点？
```

### rollback_gap

触发：

- 检测到上线、灰度、发布、迁移、开关变更。
- 没有回滚条件、回滚负责人或停止阈值。

建议语句：

```text
上线方案已形成候选决策，但还没有听到回滚条件。是否需要确认错误率、P99 或告警阈值触发后的处理人？
```

### test_verification_gap

触发：

- 检测到接口变更、架构调整、修复方案、上线计划。
- 没有测试、压测、回归、验收或验证指标。

建议语句：

```text
当前方案还没有明确验证方式。是否需要补充兼容性测试、回归用例或压测指标？
```

### metric_monitoring_gap

触发：

- 检测到上线、事故修复、性能优化或风险缓解。
- 没有监控指标、告警阈值或观测方式。

建议语句：

```text
这里提到了上线/修复动作，但还没有明确观测指标。是否需要确认错误率、P95/P99、QPS 或业务指标？
```

## 4. 触发冷却

- 同类卡片 5 分钟内不重复打扰。
- 同一 evidence span 不重复生成多张强建议。
- 同一 segment/event batch 不重复发起 LLM 分析。
- 卡片生成必须经过 scheduler/gap engine，不能由每个 ASR partial 直接触发。
- 若用户连续 dismiss 同类型卡片，本场会议降低该类型权重。
- 若用户 mark_wrong，本场会议暂停该触发规则并记录样本用于回归。

## 5. 置信度与降级

强建议需要同时满足：

- ASR final segment 可用。
- `meeting_context.is_engineering_meeting = true`。
- 关键技术实体置信度足够。
- 状态机检测到候选决策或行动项。
- 证据片段完整。
- LLM 输出结构化 JSON 校验通过。
- 建议仍处在可追问窗口内。

否则降级为：

- 只更新会议状态，不显示卡片。
- 放入“待观察”队列。
- 会后作为待确认项呈现。

## 6. 时效要求

实时建议的价值在于会议还没错过追问窗口。

成功：

- 卡片在相关 final segment 或 evidence batch 确认后 10-30 秒内出现。
- 议题仍处于 active 或刚刚切换。
- 用户可以直接复制追问，会议仍有机会补齐信息。

失败或降级：

- 同一议题已经关闭。
- 会议已进入下一个主题超过阈值。
- 卡片在会后才生成。
- 用户标记为 `too_late`。

降级结果：

- 不算实时建议成功。
- 可以进入会后“待确认项”。
- `too_late` 必须进入质量评估，也应作为 scheduler/eval 的降级结果记录。

## 7. 不允许的卡片

- 无证据卡片。
- 非工程会议中的工程缺口卡片。
- 对个人能力、责任、绩效作评价的卡片。
- 替参会者发言的卡片。
- 断言“不能上线”“方案错误”的裁判式卡片。
- 基于低置信度 ASR 的强提醒。
- 把未确认候选决策写成正式结论的卡片。

## 8. 反馈回归

用户对卡片的操作必须成为回归样本：

- `keep`：保留为正样本。
- `dismiss`：记录为低价值或当前上下文不需要。
- `mark_wrong`：暂停本场同类触发并进入负样本。
- `too_late`：进入时效失败样本。
- `too_intrusive`：进入节奏打扰样本。

每条样本保存：

- evidence spans。
- state refs。
- trigger reason。
- prompt version。
- model output。
- show/silence decision。
- user action。
