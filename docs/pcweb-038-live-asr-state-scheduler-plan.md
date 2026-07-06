# PCWEB-038 Live ASR 本地状态/调度骨架计划

> 日期：2026-07-01  
> 状态：Implemented as local deterministic skeleton  
> 目的：把 `live_asr_stream` 从“只展示转写和证据”推进到“能触发本地会议状态候选和调度占位”，但仍不调用 ASR 模型、远程 ASR、LLM 或读取真实音频。

## 1. 背景

`PCWEB-037` 已建立本地 ASR streaming event 到 Web/API/UI 的边界：`partial` 只做预览，`final/revision` 生成 EvidenceSpan，前端可以通过 `EventSource` 从空 live view 增量渲染 transcript/evidence。

但产品价值不能停留在“实时转写工具”。为了证明后续真实 state engine / scheduler / LLM 能接到 Live ASR 链路，本轮先做一个完全本地、确定性、可测试的状态/调度骨架。

## 2. 决策

新增 `PCWEB-038: Live ASR final/revision 本地状态/调度骨架`。

实现口径：

- 当 `live_asr_stream` 的 `final` 或 `revision` 文本包含 `灰度` 时，生成一个本地 `DecisionCandidate`。
- `DecisionCandidate` 必须引用当前 active EvidenceSpan。
- 同步生成一个 `scheduler_event`，表示本地 state gap 被检测到。
- `scheduler_event` 明确标记 `prompt_version=not-called`、`model=not-called`。
- 不生成 `llm_schema_result`、`suggestion_card`、`suggestion_silenced` 或报告。
- 仍保持 `source=live_asr_stream`、`trace_kind=live_event`。

## 3. 事件契约

示例序列：

```text
transcript_partial
transcript_final
state_event
scheduler_event
transcript_revision
state_event
scheduler_event
provider_error
evaluation_summary
```

`state_event.payload.state_item` 示例：

```json
{
  "id": "asr_decision_asr_seg_001",
  "statement": "先灰度 10%。",
  "evidence_span_ids": ["asr_ev_asr_seg_001"],
  "source": "live_asr_stream",
  "state_origin": "local_deterministic_asr_skeleton"
}
```

`scheduler_event.payload` 示例：

```json
{
  "scheduler_event_type": "state_gap_detected",
  "card_id": "",
  "gap_rule_id": "asr.state_candidate.review",
  "trigger_source": "live_asr_state_skeleton",
  "trigger_reason": "ASR final/revision produced a deterministic state candidate; LLM disabled in PCWEB-038",
  "segment_batch": ["asr_seg_001"],
  "source_event_ids": ["asr_state_event_asr_seg_001"],
  "prompt_version": "not-called",
  "model": "not-called"
}
```

## 4. 明确范围外

- 桌面麦克风或系统音频采集。
- FunASR、sherpa-onnx 或其他模型加载。
- 远程 ASR provider。
- 真实 state engine。
- 真实 scheduler event log。
- LLM 中转站调用。
- 建议卡片生成、schema 校验、卡片静默/失效、报告生成。
- ASR 中文技术词质量或 endpoint final 延迟达标声明。

## 5. 验收口径

`PCWEB-038` 只有在以下证据齐全时才算完成：

- 后端单元测试证明 Live ASR final/revision 会生成 `state_event` 和 `scheduler_event`。
- 测试证明 Live ASR 不会生成 `llm_schema_result`、`suggestion_card` 或 `suggestion_silenced`。
- API/SSE 测试证明 JSON 和 SSE 都输出 `state_event`、`scheduler_event` 和 `not-called` 边界标记。
- 浏览器 smoke 证明 Web 工作台 Live ASR 模式从空 view 增量显示 transcript/evidence/state，并展示 scheduler placeholder；同时保持 0 张建议卡片和空报告。
- 文档明确这是本地确定性骨架，不是正式智能层。

## 6. TDD 证据

- 先新增 `test_build_asr_live_events_emits_local_state_and_scheduler_skeleton`，观察到失败：Live ASR event list 中没有 `state_event/scheduler_event`。
- 实现 `asr_live_events.py` 中的本地状态/调度骨架后，`tests/test_live_events.py` 通过。
- 更新 `test_create_asr_live_session_events_json_and_sse_use_asr_boundary`，验证 API/SSE 输出新事件和 `not-called`。
- 更新浏览器 smoke，验证 Live ASR 状态板出现 `先灰度 5%`，事件流出现 `scheduler_event` 和 `not-called`，建议卡片仍为 0，报告仍为空。

## 7. 后续复审条件

- 接入真实 state engine 后，必须替换 `灰度` 字符串启发式，并让 state extraction 支持多类状态对象。
- 接入真实 scheduler 后，`scheduler_event` 必须来自 scheduler event log，而不是本地占位生成器。
- 接入 LLM 后，才允许生成 `llm_schema_result` 和 `suggestion_card`，且必须满足 EvidenceSpan、schema、成本、延迟和降级 gate。
- 接入真实 ASR provider 后，必须重新评估 revision 对既有状态候选的更新/撤销策略。
