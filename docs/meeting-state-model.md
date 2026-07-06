# 会议状态模型

> 日期：2026-06-18  
> 目的：让实时 Copilot 有可测试、可追踪的结构化状态，而不是完全依赖 prompt 临场发挥。

## 1. 状态对象总览

```text
TranscriptSegment
  -> EvidenceSpan
  -> TechnicalEntity
  -> Topic
  -> DecisionCandidate
  -> ActionItem
  -> Risk
  -> OpenQuestion
  -> MeetingStateEvent
  -> SuggestionCard
  -> MeetingSummary
```

所有高级对象都必须能追溯到 EvidenceSpan。

## 2. TranscriptSegment

```json
{
  "id": "seg_001",
  "start_ms": 12000,
  "end_ms": 18400,
  "speaker_hint": "local|remote|unknown",
  "raw_text": "先灰度百分之十，如果错误率超过千分之一就回滚",
  "normalized_text": "先灰度 10%，如果错误率超过 0.1% 就回滚。",
  "asr_confidence": 0.86,
  "is_final": true,
  "revision_of": null
}
```

规则：

- raw_text 永久保留。
- normalized_text 可以修正标点、数字、中英混排和术语，但不能改变事实。
- revision 必须保留前后版本关系。
- TranscriptSegment 只能由 provider final 或 stabilizer 确认的 stable final 生成。
- ASR partial 只能生成 candidate signal 或 UI 预览，不能生成正式 EvidenceSpan。
- `revision_of` 影响已有状态时，下游必须生成可追踪更新事件；被影响的状态对象要么更新证据，要么降级为待确认，要么保留旧证据并标记 stale。

## 3. EvidenceSpan

```json
{
  "id": "ev_001",
  "segment_ids": ["seg_001"],
  "start_ms": 12000,
  "end_ms": 18400,
  "quote": "先灰度百分之十，如果错误率超过千分之一就回滚",
  "confidence": 0.86
}
```

规则：

- 决策、待办、风险、建议、纪要均不得脱离 EvidenceSpan。
- evidence quote 使用原文或最小必要片段。
- quote 不能被 LLM 改写成更强结论。
- EvidenceSpan 的 quote 默认来自 final/revision 的 raw transcript；normalized transcript 可用于检索、归一化和 LLM 上下文，但不能替代原始证据。

## 4. TechnicalEntity

```json
{
  "id": "ent_001",
  "type": "field",
  "raw_text": "trace id",
  "normalized": "trace_id",
  "aliases": ["trace id", "TraceID"],
  "evidence_span_id": "ev_002",
  "confidence": 0.91,
  "confirmed": false
}
```

实体类型：

- service、endpoint、field、table、metric、error_code、component、dependency、person、team、date、percentage、threshold。

## 5. Topic

```json
{
  "id": "topic_001",
  "title": "trace_id 字段兼容方案",
  "status": "active|closed|reopened",
  "evidence_spans": ["ev_002", "ev_003"],
  "started_at_ms": 30000,
  "ended_at_ms": null
}
```

规则：

- 议题切换需要证据。
- 可以存在短暂 uncertain topic。
- 会后可合并相邻同义议题。

## 6. DecisionCandidate

```json
{
  "id": "decision_candidate_001",
  "topic_id": "topic_001",
  "statement": "新增 trace_id 字段，并兼容老版本调用方。",
  "status": "candidate|confirmed|rejected|superseded|needs_confirmation",
  "evidence_spans": ["ev_004"],
  "confidence": 0.78,
  "confirmed_by_user": false
}
```

规则：

- 默认是 candidate，不是 confirmed。
- 检测到反对、推翻或改口时更新状态。
- 无证据不得进入决策列表。

## 7. ActionItem

```json
{
  "id": "action_001",
  "description": "补充兼容性测试用例。",
  "owner": "张三",
  "owner_confidence": 0.74,
  "deadline": "下周三",
  "deadline_confidence": 0.8,
  "acceptance_criteria": null,
  "status": "candidate|confirmed|needs_owner|needs_deadline|done",
  "evidence_spans": ["ev_005"]
}
```

规则：

- owner 不明确时必须写 null 或 needs_owner。
- deadline 不明确时必须写 null 或 needs_deadline。
- 不能根据说话人推断 owner，除非文本明确。

## 8. Risk

```json
{
  "id": "risk_001",
  "description": "老版本调用方可能不兼容新增字段。",
  "impact": "调用失败或解析异常",
  "mitigation": "兼容两个版本并补充测试",
  "status": "open|mitigated|accepted|needs_response",
  "evidence_spans": ["ev_006"]
}
```

## 9. OpenQuestion

```json
{
  "id": "question_001",
  "question": "错误率超过多少触发回滚？",
  "status": "open|answered|deferred",
  "related_state_ids": ["decision_candidate_002"],
  "evidence_spans": ["ev_007"]
}
```

## 10. 状态流转

### MeetingStateEvent

`MeetingStateEvent` 是证明系统在会中持续维护状态的关键对象。最终快照不够，MVP 必须保存状态事件日志。

```json
{
  "id": "state_event_001",
  "event_type": "created|updated|merged|superseded|answered|confirmed|dismissed|reopened",
  "target_type": "Topic|DecisionCandidate|ActionItem|Risk|OpenQuestion|SuggestionCard",
  "target_id": "question_001",
  "before": {
    "status": "open",
    "question": "谁负责回滚？"
  },
  "after": {
    "status": "answered",
    "answer": "张三负责回滚。"
  },
  "evidence_span_ids": ["ev_007", "ev_011"],
  "source": "asr_final|llm_analysis|gap_rule|user_action",
  "created_at_ms": 148000,
  "reason": "新的 final segment 明确补充了回滚负责人。"
}
```

规则：

- 每次创建、合并、更新、确认、忽略状态对象都必须写入事件日志。
- 事件必须引用触发它的 EvidenceSpan；纯用户操作也要记录当时关联的状态对象和证据。
- `before` 和 `after` 保存最小必要字段，避免复制整个会议状态。
- 没有事件日志的状态更新不能计入“实时 Copilot”验收。

```text
partial transcript
  -> candidate signal
  -> final transcript
  -> evidence span
  -> state candidate
  -> MeetingStateEvent
  -> suggestion card or silent update
  -> user action
  -> MeetingStateEvent
  -> confirmed / dismissed / needs_confirmation
```

核心规则：

- partial 不生成强结论。
- final 才能生成 evidence。
- LLM 输出必须经过 JSON schema 校验。
- 用户确认优先于模型后续推断。
- Demo 至少展示 5 条状态事件链路。
- 会后纪要必须能从状态对象回溯到事件，再回溯到 EvidenceSpan。
