# 最小有价值 Demo 验收脚本

> 日期：2026-06-18  
> 目的：防止 demo 退化为“录音转文字 + 会后总结”。最小 demo 必须证明实时 Copilot 的差异化价值。

## 1. Demo 一句话目标

真实录音进入系统后，系统不仅能转写，还能基于证据片段维护会议状态，发现工程讨论缺口，生成低频建议卡片，并在会后输出可追溯纪要。

## 2. 最小链路

```text
真实录音
  -> 本地 ASR final segment
  -> EvidenceSpan
  -> 工程语境门禁
  -> 增量会议状态机
  -> 状态 diff / gap rule
  -> 工程缺口卡片
  -> 带证据会后纪要
  -> 用户反馈进入评测
```

硬门槛：

- 非软件工程会议可以总结，但不能输出工程缺口卡片。
- 工程建议必须由“稳定证据 + 状态变化 + 缺口规则”触发，不能只由会后全文总结触发。
- Demo 必须展示状态增量变化，例如 `DecisionCandidate created`、`ActionItem updated needs_owner`、`OpenQuestion answered`。
- 主体验不能以连续 transcript 流为中心；transcript 是证据面板，不是产品价值本身。

## 3. Demo 场景

第一轮只打穿两个场景：

- 主场景：上线 / 灰度评审会。
- 第二场景：API 评审会。

真实或模拟音频中必须包含：

- 一个候选决策。
- 一个行动项。
- 一个没有 owner 的行动项。
- 一个上线/灰度讨论。
- 一个缺少 rollback 或 metric 的缺口。
- 一个缺少 test/verification 的缺口。
- 一个被推翻或待确认的候选结论。

主 Demo path：

```text
候选决策：“先灰度 10%”
  -> 系统发现缺少 rollback owner / 监控指标 / 验证方式
  -> 10-30 秒内生成一张低频建议卡
  -> 用户采纳或复制追问
  -> 会议中补充“由张三回滚，错误率超过 0.1% 或 P99 超过 800ms 触发”
  -> 系统将 OpenQuestion 标记 answered
  -> DecisionCandidate 更新为更完整的候选决策
  -> 会后纪要展示前后 EvidenceSpan
```

## 4. 必须展示的能力

### 4.1 稳定 Transcript

必须展示：

- raw transcript。
- final segment。
- 时间戳。
- ASR 耗时。
- 低质量/失败状态。

可以暂缓：

- 完整多人说话人识别。
- 漂亮 UI。

### 4.2 证据化会议状态机

最小状态对象：

- `DecisionCandidate`。
- `ActionItem`。
- `Risk`。
- `OpenQuestion`。
- `MeetingStateEvent`。

每个对象必须包含：

- statement / description。
- status：candidate、confirmed、needs_confirmation。
- evidence_span。
- confidence。

禁止：

- 无证据状态。
- 把候选决策写成已确认决策。
- 猜 owner、deadline、rollback threshold。

状态事件至少包含：

- `event_type`：created、updated、merged、superseded、answered、confirmed、dismissed。
- `target_type`。
- `target_id`。
- `before`。
- `after`。
- `evidence_span_ids`。
- `source`：asr_final、llm_analysis、gap_rule、user_action。
- `created_at_ms`。
- `reason`。

Demo 至少展示 5 条状态事件链路。

### 4.3 工程缺口卡片

最小卡片类型：

- `owner_gap`。
- `rollback_gap`。
- `test_verification_gap`。
- `metric_monitoring_gap`。

每张卡片必须包含：

- trigger_reason。
- suggested_question。
- evidence_span。
- confidence。
- actions：keep、dismiss、mark_wrong。

时效要求：

- 实时建议应在相关 final segment 后 10-30 秒内出现。
- 如果议题已经关闭，或会议已转入下个主题超过阈值，该缺口只能降级为会后待确认项。
- `too_late` 必须计入失败率，而不是只作为调试标签。

卡片语气：

- 使用“是否需要确认”“可能缺少”。
- 不使用“必须”“方案错误”“不可上线”。

### 4.4 带证据纪要

输出内容：

- 背景。
- 候选/已确认决策。
- 行动项。
- 风险。
- 未闭环问题。
- 建议追问。
- 证据时间戳。

每条正式内容必须能回到 EvidenceSpan。

## 5. 成功标准

ASR：

- 能输出可读中文。
- final segment 可用于 LLM 分析。
- 关键技术实体不能大面积错。
- 记录耗时和 RTF。
- 技术实体必须进入单独评测，不能只看全文可读性。

Copilot：

- 至少识别 1 个候选决策。
- 至少识别 1 个行动项。
- 至少识别 1 个风险或未闭环问题。
- 至少生成 1 张有证据的工程缺口卡片。
- 正式纪要中无证据结论数量为 0。
- 固定验收集至少包含 2 个场景。
- 每个场景至少 8-12 个金标对象。
- 至少 4 类缺口中的 3 类命中。
- 有证据卡片 precision 最低线为 70%。
- `unsafe` 为 0。
- `too_late + too_intrusive` 不超过人工评审可接受阈值。

体验：

- 用户看得出哪些内容来自录音证据。
- 用户看得出何时调用 LLM。
- 用户可忽略或标记建议错误。

## 6. 失败标准

出现以下任一情况，不能判定 demo 成功：

- 只有转写，没有状态机。
- 只有会后总结，没有实时/准实时缺口卡片。
- 只有最终 JSON，没有状态 diff 或事件日志。
- 主体验以连续转写流为中心，状态对象只是会后附属输出。
- 建议卡片没有证据。
- LLM 猜测 owner、deadline 或 rollback threshold。
- 非工程会议触发工程缺口卡片。
- ASR 文字不可读。
- 纪要无法回溯证据。
- 低质量 transcript 仍触发强建议。

## 7. 人工标注口径

建议卡片人工评审标签：

- `useful`：确实帮助补问工程缺口。
- `obvious`：正确但价值低。
- `wrong`：理解错误。
- `too_late`：出现太晚。
- `too_intrusive`：打扰会议节奏。
- `unsafe`：暗示责任归因或替人下结论。

## 8. Debug Trace 要求

每条卡片和状态对象应能追踪：

```text
transcript segment
  -> evidence span
  -> state candidate
  -> LLM request
  -> JSON response
  -> suggestion card or silence reason
  -> user action
```

没有 trace 的输出不能进入质量评测。

## 9. 反馈回归要求

每次用户操作都必须保存为质量资产：

- `keep`。
- `dismiss`。
- `mark_wrong`。
- `too_late`。
- `too_intrusive`。

每条反馈至少保存：

- `evidence_span`。
- `card_type`。
- `trigger_reason`。
- `state_refs`。
- `model_output`。
- `silence_or_show_decision`。
- `user_action`。

Demo 结束必须导出 `evaluation.json` 或等价报告，用于展示哪些卡片 useful、wrong、too_late，并进入回归样本。
