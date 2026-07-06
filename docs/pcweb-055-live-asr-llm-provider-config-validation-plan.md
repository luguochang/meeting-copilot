# PCWEB-055 Live ASR LLM Provider Config Validation Plan

## Goal

Add a local provider config validation endpoint for Live ASR sessions.

The endpoint validates an explicitly submitted OpenAI-compatible provider config draft from the request body. It does not read `configs/local/`, does not read environment secrets, does not call the LLM gateway, and does not store or return the submitted `api_key`.

This increment is a contract and safety boundary before any real provider config loader or enabled executor is introduced.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-provider-config-validation
```

Required request fields:

```json
{
  "provider_protocol": "openai_compatible_chat_completions",
  "base_url": "https://provider.example.invalid/v1",
  "api_key": "provided-by-caller-but-never-returned",
  "model": "gpt-5.5"
}
```

Optional request fields:

```json
{
  "timeout_seconds": 30,
  "ca_bundle_path": "certs/root-ca.pem"
}
```

Successful response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "validation_kind": "provider_config_request_body",
  "validation_status": "valid",
  "validation_mode": "request_body_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_source_status": "request_body_only",
  "config_file_status": "not_read",
  "credentials_status": "provided_but_not_returned",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_execute": false,
  "validated_fields": [
    "provider_protocol",
    "base_url",
    "api_key",
    "model",
    "timeout_seconds",
    "ca_bundle_path"
  ],
  "display_values": {
    "base_url_origin": "https://provider.example.invalid",
    "model": "gpt-5.5",
    "timeout_seconds": 30,
    "ca_bundle_name": "root-ca.pem",
    "api_key": null
  },
  "display_value_status": {
    "base_url_origin": "derived_from_request_body",
    "model": "provided_non_secret",
    "timeout_seconds": "provided_non_secret",
    "ca_bundle_name": "basename_only",
    "api_key": "never_display"
  },
  "forbidden_response_fields": [
    "api_key",
    "authorization",
    "bearer_token",
    "raw_config"
  ],
  "forbidden_status_signals": [
    "api_key_present",
    "api_key_valid",
    "api_key_length",
    "api_key_hash",
    "api_key_prefix",
    "api_key_suffix",
    "api_key_fingerprint"
  ],
  "next_required_decisions": [
    "secret_storage_adapter",
    "authorized_config_file_loader",
    "enabled_executor_mode_contract",
    "schema_validation_and_card_lifecycle"
  ]
}
```

## Validation Rules

- `provider_protocol` must be exactly `openai_compatible_chat_completions`.
- Request body must be a JSON object; top-level string/list bodies return a generic 422 without echoing the submitted input.
- `base_url` must be an HTTPS URL with a host and must not include userinfo credentials.
- `api_key` must be present and non-empty, but the value must never be returned, masked, hashed, counted, persisted, or used for provider calls.
- `model` must be a non-empty string and may be displayed as a non-secret model identifier.
- `timeout_seconds`, when present, must be an integer from 1 to 120.
- `ca_bundle_path`, when present, must be a relative path without path traversal; only its basename may be displayed.
- Unknown fields are rejected with 422. This prevents `authorization`, `bearer_token`, `raw_config`, and other accidental secret carriers from entering the response contract.

Invalid request responses must use generic 422 details that do not echo the submitted secret or authorization value.

## Boundaries

- Validates only the caller-supplied JSON body.
- No config file read.
- No `configs/local/` access.
- No environment secret read.
- No `load_llm_gateway_config`.
- No remote ASR or LLM call.
- No request execution.
- No token estimate or cost accounting.
- No schema generation.
- No formal suggestion card.
- No `suggestion_silenced`.
- No mutation of the Live ASR audit event stream.
- No logging, storing, masking, hashing, prefix/suffix extraction, length reporting, or presence/validity reporting for the submitted `api_key`.

## Why This Matters

PCWEB-052 reports provider readiness as blocked. PCWEB-053 defines the config field boundary. PCWEB-054 defines masked status. PCWEB-055 adds the next safe step: the UI/API can test whether a config draft is shaped correctly without loading real local config or making a paid call.

The product still avoids extra default costs. The only future paid path remains the explicit OpenAI-compatible LLM gateway, and this endpoint does not enable that path.

## Tests

- Valid request body returns `validation_status=valid`, `request_body_only`, `not_read`, `not_called`, and `safe_to_execute=false`.
- Response preserves `session_id`, `source`, and `trace_kind`.
- Response displays only safe non-secret derived values: base URL origin, model, timeout, and CA basename.
- Response never returns the submitted `api_key`.
- Config file and environment secret sentinel values never appear in the response.
- File/env reads for provider config and secrets are guarded in tests and must not occur.
- The Live ASR `/events` response before and after validation is identical.
- Transcript-only Live ASR sessions can validate a config draft.
- JSON persisted audit records can be validated across app instances.
- Missing sessions return 404 without leaking submitted secrets.
- Missing fields, extra fields, unsupported protocol, invalid base URL, empty secret/model, invalid timeout, absolute CA path, and path traversal return 422 without leaking submitted secrets.
- Top-level non-object request bodies and base URLs with embedded credentials return 422 without leaking submitted secrets.
