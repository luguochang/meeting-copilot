# Copilot Product Value Tri-Lane Gate

> 日期：2026-07-03  
> 状态：Implemented as DRV-025  
> 范围：在继续 ASR/provider 横评前，用 perfect transcript / mock ASR / real ASR 三路对照判断实时 Copilot 的产品价值瓶颈。  
> 边界：该 gate 不访问麦克风，不读取真实用户音频，不读取 `configs/local/`，不调用远程 ASR/LLM，不下载模型，不写 runtime audio。

## 1. 为什么需要这个 gate

之前的 `synthetic_product_value_gate` 能判断真实 ASR smoke 是否满足 first-pilot 门槛，但它容易把两个问题混在一起：

- Copilot brain 本身是否能从会议文字里发现工程缺口。
- ASR 是否把中文技术实体、切句和 final/revision 事件保留下来。

`tools/copilot_product_value_tri_lane_gate.py` 把这两个问题拆开。它对同一个 synthetic scenario 同时跑三路：

| Lane | 输入 | 回答的问题 |
| --- | --- | --- |
| `perfect_transcript` | synthetic script 的标准 turns | 如果 ASR 完美，产品逻辑能否发现工程缺口 |
| `mock_asr` | mock/fixture ASR events | streaming event contract 和增量链路是否可用 |
| `real_asr` | real ASR events + smoke report | 当前真实本地 ASR 是否拖累产品价值 |

三路都会复用 Web Live ASR builder：

```text
final/revision
  -> EvidenceSpan
  -> state_event
  -> scheduler_event
  -> suggestion_candidate_event
  -> llm_request_draft_event
```

因此该 gate 不是另起炉灶的打分器，而是在当前 Copilot 主链路上做产品价值拆因。

## 2. 输出字段

顶层字段：

- `report_mode=copilot_product_value_tri_lane_gate`
- `report_version=copilot_product_value_tri_lane_gate.v1`
- `scenario_id`
- `scenario`
- `is_engineering_value_script`
- `overall_decision`
- `next_action`
- `lane_count=3`
- `lanes`
- `expected_gap_candidates`
- `expected_card_count`
- `non_engineering_candidate_count`
- `feedback_rubric_required=true`
- `feedback_labels`
- 所有安全 flags 为 false。

每个 lane 输出：

- `lane`
- `lane_status`
- `input_kind`
- `provider`
- `decision`
- `block_reasons`
- `expected_gap_count`
- `expected_card_count`
- `detected_gap_count`
- `candidate_count`
- `evidence_span_count`
- `state_event_count`
- `scheduler_event_count`
- `suggestion_candidate_count`
- `llm_request_draft_count`
- `formal_card_creation_status=not_created`
- `candidate_latency_window_status`
- `non_engineering_candidate_count`
- `input_event_counts`
- `live_event_counts`
- `normalized_technical_entity_recall`
- `normalized_technical_entity_precision`
- 所有安全 flags 为 false。

## 3. 决策含义

| Decision | 含义 | 下一步 |
| --- | --- | --- |
| `product_logic_ready` | 当前 lane 能生成 evidence-backed candidate，非工程负控无误报 | 可继续下一链路 |
| `blocked_by_product_logic` | perfect transcript 都无法发现 expected gap，或非工程负控误报 | 先修 gap/candidate 逻辑，暂停 ASR 横评 |
| `blocked_by_stream_contract` | mock/real event contract 或 final/eos/provider error 失败 | 先修 streaming contract |
| `blocked_by_asr_quality` | perfect/mock 可用，但 real ASR 技术实体 recall 不达标 | 继续 ASR/hotword/normalizer/FunASR 模型决策 |

顶层 `overall_decision` 的优先级：

1. perfect transcript lane 失败：`blocked_by_product_logic`
2. mock ASR lane 失败：`blocked_by_stream_contract`
3. real ASR lane 质量失败：`blocked_by_asr_quality`
4. 三路都通过：`product_logic_ready`

## 4. 当前 smoke

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_tri_lane_gate.py \
  --script-json data/asr_eval/synthetic_meetings/scripts/api-review.json \
  --mock-events artifacts/tmp/asr_events/api-review-001.sherpa.events.json \
  --real-events artifacts/tmp/asr_events/api-review-001.sherpa.events.json \
  --real-smoke-report artifacts/tmp/asr_reports/api-review-001.sherpa.smoke-report.json \
  --real-provider sherpa_onnx_streaming
```

关键结果：

```json
{
  "scenario_id": "api-review-001",
  "overall_decision": "blocked_by_asr_quality",
  "next_action": "improve_real_asr_quality_or_prepare_model_approval",
  "lanes": [
    {
      "lane": "perfect_transcript",
      "decision": "product_logic_ready",
      "candidate_count": 1,
      "evidence_span_count": 3,
      "candidate_latency_window_status": "within_expected_window"
    },
    {
      "lane": "mock_asr",
      "decision": "product_logic_ready",
      "candidate_count": 1,
      "evidence_span_count": 1
    },
    {
      "lane": "real_asr",
      "decision": "blocked_by_asr_quality",
      "normalized_technical_entity_recall": 0.5,
      "block_reasons": [
        "normalized technical entity recall below first-pilot threshold"
      ]
    }
  ]
}
```

解释：

- 当前 API review 场景中，perfect transcript lane 能生成 evidence-backed candidate，说明本地确定性 Copilot skeleton 对该场景并非完全失效。
- mock/real 事件链路能生成 EvidenceSpan 和 candidate，说明 event contract 到 no-LLM request draft 的链路可用。
- real ASR lane 仍因 sherpa normalized technical entity recall 0.5 低于 first-pilot 0.8 被阻断。
- 因此下一步不是继续证明“能转文字”，而是：要么提升 real ASR/normalizer/FunASR，要么继续扩大 perfect/mock lane 的产品逻辑覆盖。

## 5. 验证命令

Focused：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_copilot_product_value_tri_lane_gate.py \
  -q -p no:cacheprovider
```

当前结果：

```text
6 passed, 1 warning
```

完整本地门禁仍使用：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

