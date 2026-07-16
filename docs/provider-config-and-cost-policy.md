# Provider Config And Cost Policy

> 日期：2026-07-08
> 状态：P1-2 implemented
> 范围：Meeting Copilot 本地 Web/PC MVP 的 ASR、LLM provider 配置、费用和隐私边界。

## 1. 默认成本策略

默认链路：

```text
本地音频采集/录音导入
  -> 本地 ASR
  -> 本地 transcript / semantic quality gate
  -> OpenAI-compatible LLM gateway
  -> 建议卡 / 方案分析 / 会议纪要
```

默认付费项：

```text
LLM gateway only when AI analysis is enabled
```

默认不启用：

```text
remote_asr_default_enabled=false
raw_audio_uploaded_by_default=false
```

## 2. LLM Provider

LLM 使用 OpenAI-compatible gateway，通过环境变量配置：

```text
LLM_GATEWAY_BASE_URL
LLM_GATEWAY_API_KEY
LLM_GATEWAY_MODEL
LLM_GATEWAY_PROVIDER_LABEL
LLM_GATEWAY_TIMEOUT_SECONDS
LLM_GATEWAY_IS_MOCK
```

生产验收边界：

- `LLM_GATEWAY_IS_MOCK=true` 不能进入正式派生端点。
- UI、provider health、evidence 和 release report 不显示 API key。
- `/providers/health` 只暴露非密钥字段：configured、provider、model、is_mock、credential_configured。
- evidence bundle 汇总 `llm_call_count` 和 `llm_usage_total_tokens`。
- release acceptance 汇总所有 lane 的 `llm_call_count` 和 `llm_usage_total_tokens`。

## 3. ASR Provider

默认 ASR：

```text
file_asr_provider=local_funasr_batch
realtime_asr_default_order=sherpa_onnx_realtime, funasr_realtime
```

远程 ASR：

```text
default_enabled=false
enabled=false
providers=[]
adapter_contract=optional_openai_compatible_or_vendor_adapter_disabled_by_default
```

远程 ASR 只能作为后续可选 provider 或质量对照，不能作为 MVP 默认依赖。若后续启用，必须在 evidence/report 中显式记录：

```text
remote_asr_called=true
remote_asr_provider=<provider>
raw_audio_uploaded=<true/false>
```

## 4. Provider Health Endpoint

Endpoint：

```text
GET /providers/health
```

返回结构：

```json
{
  "schema_version": "provider_health.v1",
  "llm": {
    "configured": true,
    "provider": "openai_compatible_gateway",
    "model": "gpt-5.5",
    "is_mock": false,
    "credential_configured": true
  },
  "asr": {
    "file_provider": "local_funasr_batch",
    "file_asr_available": true,
    "realtime_providers": ["sherpa_onnx_realtime"],
    "realtime_asr_available": true
  },
  "remote_asr": {
    "default_enabled": false,
    "enabled": false,
    "providers": [],
    "adapter_contract": "optional_openai_compatible_or_vendor_adapter_disabled_by_default"
  },
  "cost_policy": {
    "default_paid_services": ["llm_gateway_when_ai_analysis_enabled"],
    "remote_asr_default_enabled": false,
    "raw_audio_uploaded_by_default": false
  }
}
```

禁止返回：

```text
api_key
LLM_GATEWAY_API_KEY value
configs/local path or content
```

## 5. Workbench UI

Workbench 启动检查读取：

```text
GET /audio/check
GET /providers/health
```

UI 只展示：

```text
provider
model
configured / not configured
local ASR readiness
remote ASR disabled by default
```

UI 不展示：

```text
API key
secret file path
raw provider request body
```

## 6. Verification

Focused verification：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_provider_health_endpoint_masks_llm_secret_and_disables_remote_asr_by_default \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_runs_startup_audio_check_and_explains_provider_readiness \
  tests/test_mainline_evidence_bundle_runner.py::test_simulated_realtime_lane_bundle_streams_wav_to_ws_and_writes_traceable_bundle \
  tests/test_release_acceptance_runner.py::test_release_acceptance_sums_lane_llm_usage

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
```

当前结果：

```text
4 passed, 2 warnings
node --check passed
```
