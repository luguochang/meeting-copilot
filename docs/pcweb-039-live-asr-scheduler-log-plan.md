# PCWEB-039 Live ASR 本地 Scheduler Event Log 计划

> 日期：2026-07-01  
> 状态：Planned / in implementation  
> 目的：把 `live_asr_stream` 的 scheduler 事件从单一占位升级为本地可审计的 scheduler decision log，用来表达“为什么会进入 LLM 候选队列、为什么被 cooldown/budget 跳过”，但仍不调用 LLM、不生成建议卡。

## 1. 背景

`PCWEB-038` 已让 Live ASR `final/revision` 能生成本地 `DecisionCandidate` 和 no-LLM `scheduler_event`。这证明了转写可以驱动状态和调度 UI，但 scheduler payload 仍只是单一 `state_gap_detected` 占位，无法表达真实调度器必须具备的冷却窗口、小时预算和跳过原因。

当前文档仍把“真实 scheduler event log 尚未接入”列为 P0 缺口。`PCWEB-039` 不直接接真实 LLM，而是先固定本地 scheduler audit contract。

## 2. 决策

新增 `PCWEB-039: Live ASR 本地 scheduler event log`。

实现口径：

- Live ASR 的 `final/revision` 如果生成了本地状态候选，就进入本地 scheduler decision。
- scheduler decision 复用已有增量调度语义：`partial` 不触发、`final/revision` 可触发、状态变化使用较短 cooldown、小时预算可限制候选。
- scheduler event 仍是 `event_type=scheduler_event`，但 `payload.scheduler_event_type` 从单一 `state_gap_detected` 扩展为：
  - `llm_candidate_queued`
  - `llm_candidate_skipped`
- payload 必须包含：
  - `decision_reason`
  - `would_call_llm`
  - `llm_call_status=not_called`
  - `cooldown_remaining_ms`
  - `budget_remaining`
  - `call_count_last_hour`
  - `source_event_ids`
  - `segment_batch`
  - `prompt_version=not-called`
  - `model=not-called`
- 不生成 `llm_schema_result`、`suggestion_card`、`suggestion_silenced` 或报告。

## 3. 默认调度口径

默认本地配置：

```text
min_final_interval_ms = 30000
min_state_change_interval_ms = 10000
max_calls_per_hour = 80
```

在当前 synthetic Live ASR 样本中：

- 第一条 `final` 带状态变化，生成 `llm_candidate_queued`。
- 紧接着的 `revision` 也带状态变化，但距离上一条 queued candidate 不足 10 秒，生成 `llm_candidate_skipped`，`decision_reason=cooldown`。

这能证明调度日志会记录“不是每次状态变化都调用 LLM”的成本和实时控制边界。

## 4. 明确范围外

- 不调用 LLM 中转站。
- 不生成建议卡片。
- 不做 schema 校验。
- 不接真实 ASR provider endpoint。
- 不接桌面音频采集。
- 不持久化 scheduler log 到 repository。
- 不替代未来真实 scheduler；当前只是本地 audit contract。

## 5. 验收口径

`PCWEB-039` 只有在以下证据齐全时才算完成：

- 单元测试证明第一条状态变化可产生 `llm_candidate_queued`，且 payload 标记 `llm_call_status=not_called`。
- 单元测试证明密集 revision 会产生 `llm_candidate_skipped` 和 `decision_reason=cooldown`。
- 单元/API/SSE 测试证明 scheduler event payload 包含预算、cooldown、source_event_ids、segment_batch 和 no-LLM 标记。
- 浏览器 smoke 证明 Live ASR timeline 显示 queued/skipped scheduler decision，同时仍不出现 LLM schema、suggestion card、silenced event 或 report 请求。
- 文档同步 `PCWEB-039`、`AC-PCWEB-032`、`DEC-039`。

## 6. 后续复审条件

- 接入真实 scheduler 后，`scheduler_event` 必须来自真实 scheduler event log，而不是本地 builder。
- 接入真实 LLM 后，queued candidate 才能进一步生成 `llm_schema_result` 和 `suggestion_card`。
- 接入真实会议数据后，应把 `cooldown`、`budget_exhausted`、`too_late` 和用户反馈进入质量资产。
