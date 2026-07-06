# PCWEB-110 Short Local Simulated Input Timeline Report Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`EvidenceSpan/state/gap`、`candidate/card/feedback`、`pilot`  
> 边界：本计划不授权访问麦克风、不读取真实用户音频、不读取 `.m4a`、不读取 `data/asr_eval/local_samples/`、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载模型、不下载公开音频、不运行 Cargo/Tauri。

## 1. 目标

PCWEB-108 已证明 approved synthetic event file 可以经 Web Live ASR pipeline 形成 transcript final、EvidenceSpan、state、scheduler、suggestion candidate 和 LLM request draft closure。PCWEB-110 将 M5 `Short local simulated input` 收束为可审计 timeline report：同一个 replay report 必须输出 ASR metrics、EvidenceSpan timeline、state timeline 和 candidate/card timeline。

这一步仍然不是读取本地真实短音频，也不是开麦克风。输入只能是：

- 合成生成音频产生的 approved ASR event file。
- mock streaming events。
- `artifacts/tmp/asr_events` 下 approved synthetic event JSON。

## 2. 范围

修改：

- `tools/asr_live_pipeline_replay.py`
- `tests/test_asr_live_pipeline_replay.py`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `README.md`

不修改：

- ASR provider/model runtime。
- Web Live ASR extraction rules。
- Tauri/Rust runtime。
- mic adapter。
- LLM provider config 或 `configs/local/`。
- 任何真实音频、用户录音或 `.m4a`。

## 3. 验收

Replay report 必须新增：

- `short_local_simulated_input_status`
- `input_source_kind=approved_synthetic_event_file`
- `timeline_window_ms`
- `asr_metrics`
- `evidence_span_timeline`
- `state_timeline`
- `candidate_card_timeline`

工程场景必须能形成：

- 至少 1 个 EvidenceSpan。
- 至少 1 个 state event。
- 至少 1 个 suggestion candidate。
- candidate/card timeline 中 `llm_call_status=not_called`。
- candidate/card timeline 中 `card_status=not_created`，即不生成正式卡、不调用 LLM。

非工程 control 必须保持：

- EvidenceSpan 可存在。
- `state_event_count=0`。
- `suggestion_candidate_count=0`。
- `candidate_card_timeline=[]`。
- `short_local_simulated_input_status=no_engineering_candidate_detected`。

## 4. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py::test_replay_report_converts_asr_events_to_live_pipeline_without_llm_calls \
  tests/test_asr_live_pipeline_replay.py::test_replay_report_keeps_non_engineering_control_at_zero_candidates \
  -q -p no:cacheprovider
```

结果：`2 failed, 1 warning`。失败原因是 report 中缺少 `short_local_simulated_input_status` 和 timeline 字段。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py::test_replay_report_converts_asr_events_to_live_pipeline_without_llm_calls \
  tests/test_asr_live_pipeline_replay.py::test_replay_report_keeps_non_engineering_control_at_zero_candidates \
  -q -p no:cacheprovider
```

结果：`2 passed, 1 warning`。

完整 replay gate：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py \
  -q -p no:cacheprovider
```

结果：`7 passed, 1 warning`。

## 5. 本地样本自测

工程样本：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_live_pipeline_replay.py \
  --events-path artifacts/tmp/asr_events/api-review-001.mock.events.json \
  --provider mock_streaming \
  --session-id m5-api-review
```

关键结果：

- `short_local_simulated_input_status=closed_to_candidate_timeline`
- `evidence_span_count=3`
- `state_event_count=1`
- `suggestion_candidate_count=1`
- `llm_request_draft_count=1`
- `candidate_card_timeline[0].llm_call_status=not_called`
- `candidate_card_timeline[0].card_status=not_created`

非工程 control：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_live_pipeline_replay.py \
  --events-path artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json \
  --provider mock_streaming \
  --session-id m5-non-engineering-control
```

关键结果：

- `short_local_simulated_input_status=no_engineering_candidate_detected`
- `evidence_span_count=3`
- `state_event_count=0`
- `suggestion_candidate_count=0`
- `candidate_card_timeline=[]`

## 6. 后续

PCWEB-110 完成 M5 的受限本地模拟 timeline report。下一步仍不能直接进入真实麦克风会议；还需要 desktop runtime/worker/mic adapter/export/feedback 链路具备，并由用户显式启动真实 shadow test。

PCWEB-110 不生成正式 suggestion card，不产生真实用户 feedback，不执行 export，也不代表真实 ASR 或真实麦克风链路已完成。它只为后续 feedback/export/pilot report 提供可追溯的 candidate/card timeline 输入。
