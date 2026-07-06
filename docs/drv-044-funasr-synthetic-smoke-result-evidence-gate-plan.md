# DRV-044: FunASR Synthetic Smoke Result Evidence Gate

> 日期：2026-07-04  
> 状态：Implemented + provenance-hardened  
> 目的：把未来本地 FunASR synthetic smoke 的结果证据固定成机器可验收合同，避免跑出 ASR 文本后继续争论“怎样算过”。  
> 边界：本文档和对应工具不授权运行 ASR、不下载 FunASR/ModelScope 模型、不访问麦克风、不读取真实用户音频或任何 `.m4a`、不读取 `configs/local/`、不读取 `data/local_runtime/` 或 `outputs/`、不下载公开音频、不调用远程 ASR/LLM。

## 1. 背景

DRV-041/042 已证明 mock/approved ASR event 能闭合到产品价值 preview：4 个工程场景能生成 preview，非工程 control 不伪造候选卡。DRV-043 已把本地 FunASR 模型/cache readiness 接入 DRV-032，但 readiness 只说明“模型文件可能就绪”，不是 ASR 质量证据。

DRV-044 解决的问题是：未来如果允许跑一次本地 FunASR synthetic smoke，结果必须满足什么 schema 和阈值，才能进入 ASR quality gate。它不执行 smoke 本身，只验证 caller-provided result JSON。

## 2. 证据合同

新增工具：

- `tools/funasr_synthetic_smoke_result_evidence.py`

新增测试：

- `tests/test_funasr_synthetic_smoke_result_evidence.py`

DRY/RUN 行为：

- 默认无输入时输出 schema contract：`evidence_status=not_provided`，`quality_evidence_status=not_evaluated`。
- `--evidence-report-path` 只允许读取 approved `artifacts/tmp/asr_reports/**.json`。
- path guard 在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。
- 所有 safety flags 均保持 false：不运行 ASR、不下载模型、不访问麦克风、不读用户音频、不读 `configs/local`、不调用远程 ASR/LLM。

`funasr_synthetic_smoke_result.v1` 的核心字段：

- `evidence_kind`：`single_synthetic_smoke` 或 `batch_synthetic_confirmation`。
- `provider`：必须是 `funasr_streaming`。
- `source_boundary`：必须是 `synthetic_audio_no_user_audio`。
- `scenario_results`：每个场景必须包含 event contract、latency metrics、RTF、raw/normalized technical entity recall、EvidenceSpan/state/card closure 和 safety flags。
- `batch_artifact_provenance`：仅 `batch_synthetic_confirmation` 必需；必须声明 `source_kind=local_funasr_synthetic_smoke_artifacts`，并为每个 `scenario_id` 绑定 approved artifact path 与 sha256。

Batch artifact path 只允许位于：

- `artifacts/tmp/asr_reports/**.json`
- `artifacts/tmp/asr_events/**.json`

工具会在读取 artifact 前阻断 forbidden roots、仓库外路径、`.m4a` 和非 JSON；读取 artifact 只用于计算 sha256，不运行 ASR、不读取音频、不下载模型。

## 3. 质量阈值

硬阈值固定为：

- 工程场景 normalized technical entity recall `>=0.80`。
- raw recall 和 normalized recall 必须分开记录。
- first partial latency p95 `<=2.0s`。
- final latency p95 `<=8.0s`。
- ASR RTF `<=0.60`。
- suggestion candidate latency p95 `<=30.0s`。
- event contract 必须包含 partial、final、end_of_stream，并且 `error_count=0`。
- 每张 candidate/card 必须能追溯到 EvidenceSpan。
- non-engineering control 的 state/candidate/card 必须为 `0`。

## 4. 单场景与 Batch 的区别

单场景 `single_synthetic_smoke`：

- 通过时输出 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`。
- `counts_as_asr_quality_go_evidence=false`。
- 下一步是跑 4 个工程场景 + 1 个非工程 control 的 batch confirmation。

Batch `batch_synthetic_confirmation`：

- 至少 4 个工程 synthetic scenarios。
- 至少 1 个 non-engineering control。
- 必须提供 `batch_artifact_provenance`，且每个场景都要有对应 artifact。
- `source_kind=fixture_only`、缺少 provenance、artifact sha256 不匹配、artifact path 越界或缺少任一 scenario artifact 时，必须 blocked。
- 工程场景全部满足 recall/latency/RTF/EvidenceSpan 阈值。
- 非工程 control 的 candidate/card 为 0。
- 通过时输出 `funasr_synthetic_smoke_quality_batch_confirmed`。
- `counts_as_asr_quality_go_evidence=true`，但 `counts_as_real_mic_go_evidence=false`。

这意味着 batch confirmation 可让 DRV-032 的 ASR quality gate 退出 `not_exited`，但仍不代表真实麦克风会议已经 ready 或已经发生。

## 5. DRV-032 接入

`tools/asr_quality_decision_gate.py` 新增：

- builder 参数 `funasr_smoke_result_report`。
- CLI 参数 `--funasr-smoke-result-path`。
- 报告字段：
  - `funasr_smoke_evidence_status`
  - `funasr_smoke_quality_evidence_status`
  - `funasr_smoke_counts_as_quality_go_evidence`
  - `funasr_smoke_counts_as_real_mic_go_evidence`
  - `funasr_smoke_scenario_summary`
  - `funasr_smoke_validation_errors`
  - `funasr_smoke_result_input_status`
  - `funasr_smoke_result_input_errors`

决策规则：

- 单场景 candidate：`decision_status=funasr_smoke_candidate_requires_batch_confirmation`，`quality_exit_status=not_exited`。
- Batch confirmed 且 `batch_artifact_provenance_status=validated`：`decision_status=asr_quality_current_gate_not_blocking`，`quality_exit_status=strict_quality_gate_not_blocking`。
- Batch confirmed 但缺少 validated artifact provenance：`decision_status=fix_funasr_smoke_result_evidence_first`，`quality_exit_status=not_exited`。
- blocked result：`decision_status=fix_funasr_smoke_result_evidence_first`。

## 6. TDD 记录

Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
```

Result：

```text
9 failed, 12 passed, 1 warning
```

失败原因：

- `tools/funasr_synthetic_smoke_result_evidence.py` 不存在。
- `build_asr_quality_decision_gate_report()` 不接受 `funasr_smoke_result_report`。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
```

Result：

```text
23 passed, 1 warning
```

Integrated：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  tests/test_asr_quality_decision_gate.py \
  tests/test_simulated_shadow_pipeline_smoke.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  tests/test_public_audio_source_whitelist.py \
  tests/test_public_audio_sample_extraction_plan.py \
  tests/test_public_audio_planned_sample_manifest_decision.py \
  -q -p no:cacheprovider
```

Result：

```text
77 passed, 1 warning
```

Additional input-guard red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

Result：

```text
1 failed, 15 passed, 1 warning
```

失败原因：`--funasr-smoke-result-path` 会接受未经 DRV-044 gate 验证的 raw smoke JSON，然后回退到缺模型阻塞；这会让无效输入不够显性。

修复后：

```text
16 passed, 1 warning
```

Provenance hardening red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py -q -p no:cacheprovider
```

Result：

```text
4 failed, 6 passed, 1 warning
```

失败原因：

- `batch_synthetic_confirmation` 缺少 artifact provenance 仍会通过。
- `source_kind=fixture_only` 未被阻断。
- artifact sha256 不校验。
- 输出缺少 `batch_artifact_provenance_status`。

Provenance hardening green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
```

Result：

```text
27 passed, 1 warning
```

## 7. 后续

DRV-044 已经完成 evidence gate，不代表本地 FunASR smoke 已实际执行。后续仍需满足以下任一路径：

- 用户提供已验证本地 FunASR 模型目录，并先通过 DRV-043 readiness。
- 用户明确批准 DRV-019 手动模型下载和一次本地 synthetic smoke。
- 用户显式接受降级试点风险，但该路径不算 ASR quality Go evidence。

在没有真实 batch confirmation 或显式降级试点接受前，真实麦克风 shadow test 仍保持 blocked。
