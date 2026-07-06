# PCWEB-054 Live ASR LLM Provider Masked Status Plan

## Goal

Add a local, read-only masked provider status endpoint that defines the future UI/API status envelope for OpenAI-compatible LLM provider configuration.

The endpoint must still be template-only and no-read. It does not load a config file, does not inspect environment secrets, does not call a provider, and does not infer whether a key/config exists. It only states what a future masked status response is allowed to expose after a separately reviewed provider config loader exists.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-provider-masked-status
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "status_kind": "masked_provider_status",
  "status_mode": "template_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "provider_status": "not_configured",
  "config_load_status": "not_loaded",
  "config_source_status": "not_read",
  "credentials_status": "not_read",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_execute": false,
  "display_values": {
    "base_url_origin": null,
    "model": null,
    "timeout_seconds": null,
    "ca_bundle_name": null,
    "api_key": null
  },
  "display_value_status": {
    "base_url_origin": "not_read",
    "model": "not_read",
    "timeout_seconds": "not_read",
    "ca_bundle_name": "not_read",
    "api_key": "never_display"
  },
  "masked_value_policy": {
    "api_key": "never_return_value_or_mask",
    "base_url": "origin_only_after_loader_review",
    "model": "display_allowed_after_loader_review",
    "timeout_seconds": "display_allowed_after_validation",
    "ca_bundle_path": "basename_only_after_loader_review"
  },
  "forbidden_status_signals": [
    "api_key_present",
    "api_key_valid",
    "api_key_length",
    "api_key_hash",
    "api_key_prefix",
    "api_key_suffix",
    "api_key_fingerprint",
    "authorization",
    "bearer_token",
    "raw_config"
  ],
  "block_reasons": [
    "template_only_status",
    "provider_config_not_loaded",
    "credentials_not_read",
    "llm_executor_disabled"
  ],
  "next_required_decisions": [
    "provider_config_loader_contract",
    "secret_storage_adapter",
    "authorized_masked_status_loader",
    "enabled_executor_mode_contract",
    "schema_validation_and_card_lifecycle"
  ]
}
```

The response may contain the literal key name `api_key` only as a display-value placeholder, masked-value policy key, or forbidden-signal name. Its value must be `null` in `display_values` and `never_display` / `never_return_value_or_mask` in policy fields.

## Boundaries

- Read only.
- No event mutation or appended status event.
- No API key, `configs/local/`, environment secret, gateway, relay, or remote LLM call.
- No call to `asr_bakeoff.llm_smoke`.
- No call to `load_llm_gateway_config`.
- No use of any masking helper that requires a real secret or provider URL.
- No real provider/model/base URL/CA path selection.
- No token estimate or budget charge.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No enabled-mode fallback, retry, queue worker, background task, cursor, or pagination.

## Why This Matters

PCWEB-053 defines field-level secret/display policy. PCWEB-054 defines the future masked status response envelope that UI and API consumers may use without turning status display into config inspection.

This makes the next real provider-config increment safer: a loader can be designed against a pre-existing response contract, while tests keep proving that the current Web MVP does not read or leak user secrets.

## Tests

- Masked status returns `template_only`, `not_loaded`, `not_read`, `not_called`, and `safe_to_execute=false`.
- It preserves session/source/trace kind.
- It returns `display_values` with `null` values only.
- `api_key` appears only as a placeholder/policy/forbidden-signal name, never as a real or masked value.
- It excludes configured sentinel values even when `MEETING_COPILOT_LLM_CONFIG` points to a readable local file.
- It proves that config path is not read by making reads of that path fail.
- It does not mutate the Live ASR event stream.
- Transcript-only Live ASR sessions return the same template-only status envelope.
- JSON persisted records can be read across app instances.
- Missing sessions return 404.
