# PCWEB-053 Live ASR LLM Provider Config Boundary Plan

## Goal

Add a local, read-only provider config boundary endpoint before any real LLM provider config loader exists.

The endpoint describes which OpenAI-compatible provider settings may be displayed, masked, or never returned. It must not read local provider config, environment secrets, API keys, relay settings, or `configs/local/`. It also must not call any LLM provider.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-provider-config-boundary
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "boundary_status": "template_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_load_status": "not_loaded",
  "config_source_status": "not_read",
  "credentials_status": "not_read",
  "llm_call_status": "not_called",
  "safe_to_execute": false,
  "field_count": 5,
  "required_field_names": ["base_url", "api_key", "model"],
  "fields": [
    {
      "name": "base_url",
      "classification": "public_endpoint",
      "display_policy": "origin_only_or_masked",
      "required": true,
      "response_value_policy": "never_return_raw_value"
    },
    {
      "name": "api_key",
      "classification": "secret",
      "display_policy": "never_display",
      "required": true,
      "response_value_policy": "never_return_value"
    },
    {
      "name": "model",
      "classification": "public_model_id",
      "display_policy": "display_allowed",
      "required": true,
      "response_value_policy": "return_configured_value_only_after_loader_mask_review"
    },
    {
      "name": "timeout_seconds",
      "classification": "non_secret_runtime",
      "display_policy": "display_allowed",
      "required": false,
      "response_value_policy": "return_configured_value_after_validation"
    },
    {
      "name": "ca_bundle_path",
      "classification": "local_path_sensitive",
      "display_policy": "basename_only_or_not_displayed",
      "required": false,
      "response_value_policy": "never_return_absolute_path"
    }
  ],
  "allowed_response_fields": [
    "provider_protocol",
    "model",
    "base_url_origin",
    "timeout_seconds",
    "config_status"
  ],
  "forbidden_response_fields": [
    "api_key",
    "authorization",
    "bearer_token",
    "raw_config"
  ],
  "secret_storage_policy": "configs/local_only_or_os_keychain_future",
  "next_required_decisions": [
    "provider_config_loader_contract",
    "secret_storage_adapter",
    "masked_provider_status_response",
    "enabled_executor_mode_contract",
    "schema_validation_and_card_lifecycle"
  ]
}
```

The string `api_key` may appear only as a field name or forbidden-field name in static metadata. The endpoint must never return a configured key, bearer token, authorization header, raw config object, local absolute config path, or relay secret.

Transcript-only sessions keep the same template-only boundary and `safe_to_execute=false`; this endpoint is a policy projection, not a queue executor.

## Boundaries

- Read only.
- No event mutation or appended boundary event.
- No API key, `configs/local/`, environment secret, gateway, relay, or remote LLM call.
- No call to the manual `asr_bakeoff.llm_smoke` path.
- No call to `load_llm_gateway_config`.
- No real provider/model selection.
- No token estimate or budget charge.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No enabled-mode fallback, retry, queue worker, background task, cursor, or pagination.

## Why This Matters

PCWEB-052 reports provider readiness as blocked. PCWEB-053 defines the secret/display boundary that must exist before a real provider config loader is allowed. This keeps future中转站/OpenAI-compatible integration from accidentally leaking `api_key`, bearer tokens, raw JSON config, or local paths through status APIs and UI panels.

## Tests

- Boundary returns `template_only`, `not_loaded`, `not_read`, `not_called`, and `safe_to_execute=false`.
- It preserves session/source/trace kind.
- It returns the exact static field classifications and display policies.
- It includes `api_key` only as field metadata with `display_policy=never_display`.
- It excludes configured sentinel values even when `MEETING_COPILOT_LLM_CONFIG` points to a readable local file.
- It proves that config path is not read by making reads of that path fail.
- It does not mutate the Live ASR event stream.
- Transcript-only Live ASR sessions return the same template-only boundary.
- JSON persisted records can be read across app instances.
- Missing sessions return 404.
