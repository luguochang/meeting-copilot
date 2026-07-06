# ASR Quality Exit Follow-up

> 日期：2026-07-04  
> 状态：Accepted as current ASR quality evidence  
> 结论：本轮没有让 ASR quality exit 通过；它把失败从黑盒变成可诊断，并把可观察 near-miss 修到当前 transcript 能支持的上限。真实麦克风仍 blocked。  
> 边界：本轮不读取私人录音、`.m4a`、Voice Memos、`configs/local/`、`data/local_runtime/` 或 `outputs/`，不访问麦克风，不调用远程 ASR/LLM，不写真实导出文件，不新增付费项。

## 1. 本轮做了什么

本轮目标不是继续开放式测评，而是把 `chunk20_hotword` 的 ASR quality blocker 收束成可行动结论：

```text
existing provider/events artifacts
  -> transcript report with bounded normalizer
  -> single-scenario smoke report with matched/missing entity diagnostics
  -> DRV-046 batch assembly
  -> DRV-032 ASR quality decision
  -> mainline runner regression with browser smoke
```

代码变更：

- `tools/funasr_synthetic_smoke_single_result_builder.py`
  - `technical_entity_metrics` 新增 `expected_entities`、`raw_matched_entities`、`raw_missing_entities`、`normalized_matched_entities`、`normalized_missing_entities`。
  - 目的：让每个场景明确知道“哪些实体缺失”，避免继续盲测。
- `tools/funasr_synthetic_smoke_result_evidence.py`
  - 新增可选 entity detail 一致性校验：若 smoke report 提供 matched/missing arrays，DRV-044 gate 会校验 recall 数值、matched/missing 覆盖关系和交集。
  - 目的：防止未来出现 `normalized_recall=1.0` 但 `normalized_missing_entities` 非空或漏填的矛盾诊断。
- `code/asr_runtime/scripts/transcript_normalizer.py`
  - 新增有上下文保护的 `字段 quest -> request_id`。
  - 新增有 backlog/lag/告警上下文保护的 `auder -> order-worker`。
  - 修复 `redis clusterQPS` 粘连，输出为 `redis cluster QPS`。
- `data/asr_eval/glossaries/technical-terms.zh.json`
  - 新增可观察 near-miss alias：`paymentway`、`ure store`、`redi coasterbqp`、`redi coasterbqpqps`、`trcoutservice service`。
- 测试补充：
  - `tests/test_funasr_synthetic_smoke_single_result_builder.py`
  - `code/asr_runtime/tests/test_transcript_normalizer.py`

## 2. TDD 证据

单场景 builder 红灯：

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q tests/test_funasr_synthetic_smoke_single_result_builder.py
Result: 1 failed, 2 passed
Failure: KeyError: 'expected_entities'
```

单场景 builder 绿灯：

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q tests/test_funasr_synthetic_smoke_single_result_builder.py
Result: 3 passed, 1 warning
```

normalizer 红灯：

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code/asr_runtime pytest -q code/asr_runtime/tests/test_transcript_normalizer.py
Result: 1 failed, 7 passed
Failure: paymentway / quest / ure store / REDi coasterBQP / auder / trcoutservice service not recovered
```

normalizer 间距红灯：

```text
Result: 1 failed, 8 passed
Failure: expected "redis cluster QPS", got "redis clusterQPS"
```

normalizer 最终绿灯：

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code/asr_runtime pytest -q code/asr_runtime/tests/test_transcript_normalizer.py
Result: 9 passed, 1 warning
```

DRV-044 entity detail 一致性补强：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 pytest -q \
  tests/test_funasr_synthetic_smoke_result_evidence.py::test_smoke_result_blocks_inconsistent_entity_detail_metrics
Result: 1 failed, 1 warning
Failure: inconsistent entity detail metrics were accepted

GREEN:
PYTHONDONTWRITEBYTECODE=1 pytest -q \
  tests/test_funasr_synthetic_smoke_result_evidence.py::test_smoke_result_blocks_inconsistent_entity_detail_metrics
Result: 1 passed, 1 warning

Regression:
PYTHONDONTWRITEBYTECODE=1 pytest -q \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_funasr_synthetic_smoke_single_result_builder.py
Result: 14 passed, 1 warning
```

## 3. 最新质量结果

重建顺序：

```text
transcript report
  -> smoke report
  -> funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json
  -> funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json
```

逐场景结果：

| Scenario | normalized recall | matched | missing |
| --- | ---: | --- | --- |
| `api-review-001` | `1.0` | `40012`, `P99`, `payment-gateway`, `request_id` | none |
| `architecture-review-001` | `1.0` | `QPS`, `feature-store`, `mysql`, `recommendation-service`, `redis cluster` | none |
| `incident-review-001` | `0.5` | `lag`, `order-worker` | `timeout`, `监控阈值` |
| `release-review-001` | `0.75` | `P99`, `checkout-service`, `error_rate` | `staging` |
| `non-engineering-control-001` | `0.0` | none | none |

DRV-046/DRV-044 结果：

```text
assembly_status=drv044_batch_evidence_blocked
counts_as_asr_quality_go_evidence=false
engineering_min_normalized_recall=0.5
validation_errors=[
  engineering normalized_recall must be >= 0.8,
  engineering normalized_recall must be >= 0.8
]
negative_control_candidate_cards=0
```

本轮复审后重跑 DRV-046/DRV-032，未出现 entity detail consistency error；当前阻塞仍只来自 `incident-review-001` 和 `release-review-001` 两个工程场景 recall 低于 `0.8`。

DRV-032 结果：

```text
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
blocked_reasons=[
  funasr_smoke_assembly must be drv044_batch_evidence_validated,
  funasr_smoke_assembly must count as ASR quality go evidence,
  funasr_smoke_assembly nested DRV-044 must be batch confirmed,
  funasr_smoke_assembly nested DRV-044 must count as ASR quality go evidence,
  engineering normalized_recall must be >= 0.8,
  engineering normalized_recall must be >= 0.8
]
```

关键判断：

- `api-review-001` 和 `architecture-review-001` 已经在当前 transcript 证据内过线。
- `incident-review-001` 仍缺 `timeout`、`监控阈值`。
- `release-review-001` 仍缺 `staging`。
- 这些剩余实体在当前 transcript 中没有可观察证据；把它们硬补进 normalizer 会变成从 golden script 反填答案，不能算 ASR quality Go evidence。

## 4. 产品主线回归

主链路 runner：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_followup_mainline_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
Result: exit 0
```

关键字段：

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
browser_smoke.browser_smoke_status=passed
mainline_trial.trial_id=mainline_asr_event_artifact_trial
mainline_trial.mainline_decision_id=DEC-214
closure.closure_status=mainline_trial_feedback_export_preview_created
gap_summary.implemented_and_verified=8
gap_summary.blocked_by_asr_quality=1
gap_summary.blocked_requires_m2_system_audio_capture=1
gap_summary.blocked_requires_explicit_user_approval=1
```

这说明产品链路仍可跑：

```text
approved ASR event artifact
  -> Web Live ASR SSE
  -> transcript / EvidenceSpan
  -> meeting state
  -> suggestion candidate
  -> no-call LLM request draft
  -> feedback/export preview
```

但 ASR quality gate 仍然阻断真实麦克风会议。

## 5. 验证命令

全量本地 gate：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
Result:
  asr-runtime-pytest: 81 passed, 1 warning
  asr-bakeoff-pytest: 18 passed, 1 warning
  root-pytest: 507 passed, 2 warnings
  core-pytest: 34 passed, 1 warning
  web-backend-pytest: 336 passed, 2 warnings
  quality gate profile=all-local passed
```

语法检查：

```text
node --check code/web_mvp/e2e/browser_smoke.mjs
Result: exit 0

python3 -m py_compile \
  tools/funasr_synthetic_smoke_result_evidence.py \
  tools/funasr_synthetic_smoke_single_result_builder.py \
  code/asr_runtime/scripts/transcript_normalizer.py \
  code/asr_runtime/scripts/transcript_report.py \
  tools/funasr_synthetic_smoke_batch_evidence_assembler.py \
  tools/asr_quality_decision_gate.py \
  tools/mainline_usable_e2e_runner.py
Result: exit 0
```

敏感信息检查：

```text
rg -n "sk-[A-Za-z0-9]{20,}|codexai\\.club" .
Result: no matches outside ignored/forbidden runtime roots
```

端口检查：

```text
lsof -nP -iTCP:8767 -sTCP:LISTEN
lsof -nP -iTCP:9223 -sTCP:LISTEN
Result: no listeners
```

## 6. 下一步建议

下一轮不应继续堆 normalizer。当前唯一合理的 ASR quality 后续是受控地重跑本地 ASR 输入质量/参数实验：

1. 固定 `chunk20_hotword` 为速度基线，因为 RTF 已过线。
2. 对 `incident-review-001` 和 `release-review-001` 做最小参数/输入实验，目标只看 `timeout`、`监控阈值`、`staging` 是否能在 transcript 中真实出现。
3. 优先检查 synthetic meeting script -> synthetic audio 的发音/文本输入是否真的承载这些实体；如果音频生成阶段没有清楚表达这些词，应修评测资产，而不是修 ASR gate。
4. 如果音频确实包含这些实体但本地 FunASR 稳定漏识别，则尝试热词机制、chunk/window 参数或更合适的本地/远程 ASR provider。
5. 若仍无法达到 `normalized recall >= 0.8`，进入产品决策：远程 ASR 成本/隐私审批，或一次显式 degraded pilot，仅验证 timing/feedback，不宣称 ASR quality Go。

当前不建议进入真实麦克风主流程。原因不是产品链路没跑通，而是 ASR quality evidence 仍没有达到中文技术会议可用阈值。
