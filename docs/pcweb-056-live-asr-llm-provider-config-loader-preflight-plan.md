# PCWEB-056 Live ASR LLM Provider Config Loader Preflight Plan

## Goal

Add a local provider config loader preflight endpoint for Live ASR sessions.

The endpoint validates a future config-loader request shape and returns path-display policy metadata. It does not read the config file, does not check file existence, does not read environment secrets, does not return the raw path, and does not call the LLM gateway.

This increment keeps the product moving toward a real OpenAI-compatible provider loader while preserving the no-extra-cost and no-secret-leak boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-provider-config-loader-preflight
```

Request shape:

```json
{
  "loader_mode": "preflight_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_path": "/local/private/path/llm-gateway.local.json",
  "requested_fields": [
    "base_url",
    "api_key",
    "model",
    "timeout_seconds",
    "ca_bundle_path"
  ],
  "authorization": {
    "user_confirmed_local_config_access": true,
    "allow_secret_read": false,
    "allow_llm_call": false
  }
}
```

Successful response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "preflight_kind": "provider_config_loader",
  "preflight_status": "accepted",
  "preflight_mode": "metadata_only",
  "loader_mode": "preflight_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_source_status": "caller_supplied_path_metadata",
  "config_file_status": "not_read",
  "config_existence_status": "not_checked",
  "credentials_status": "not_read",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_execute": false,
  "safe_to_load_config": false,
  "path_display": {
    "config_path_label": null,
    "config_path_parent_name": null,
    "config_path": null
  },
  "requested_fields": [
    "base_url",
    "api_key",
    "model",
    "timeout_seconds",
    "ca_bundle_path"
  ],
  "authorization_summary": {
    "user_confirmed_local_config_access": true,
    "allow_secret_read": false,
    "allow_llm_call": false
  },
  "forbidden_response_fields": [
    "api_key",
    "authorization",
    "bearer_token",
    "raw_config",
    "config_path",
    "absolute_config_path"
  ],
  "forbidden_status_signals": [
    "config_file_exists",
    "api_key_present",
    "api_key_valid",
    "api_key_length",
    "api_key_hash",
    "api_key_prefix",
    "api_key_suffix",
    "api_key_fingerprint"
  ],
  "block_reasons": [
    "preflight_only",
    "config_file_not_read",
    "secret_read_not_authorized",
    "llm_executor_disabled"
  ],
  "next_required_decisions": [
    "secret_storage_adapter",
    "authorized_config_file_reader",
    "masked_status_loader",
    "enabled_executor_mode_contract"
  ]
}
```

## Validation Rules

- Request body must be a JSON object.
- `loader_mode` must be exactly `preflight_only`.
- `provider_protocol` must be exactly `openai_compatible_chat_completions`.
- `config_path` must be a non-empty local filesystem path string; URL schemes/netlocs are rejected, control characters such as NUL are rejected, and path traversal is rejected for both POSIX `/../` and Windows `\..\` separators.
- `requested_fields` must be a non-empty list drawn only from `base_url`, `api_key`, `model`, `timeout_seconds`, and `ca_bundle_path`, with no duplicates.
- `authorization` must contain exactly `user_confirmed_local_config_access=true`, `allow_secret_read=false`, and `allow_llm_call=false`.
- Unknown top-level fields are rejected with 422.

Invalid request responses must use generic details that do not echo submitted absolute paths, API keys, authorization values, bearer tokens, or raw config.

## Boundaries

- Metadata-only preflight.
- No config file read.
- No existence check.
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
- No raw `config_path` or absolute path in response.
- No `api_key` value, masked value, presence, validity, length, hash, prefix, suffix, or fingerprint.

## Why This Matters

PCWEB-055 validates an explicitly submitted config draft. PCWEB-056 defines the next boundary: how a future loader request may be preflighted before any local file read is allowed.

The endpoint lets UI/API consumers understand the future loader contract without turning the Web MVP into a secret reader. It also keeps the product aligned with the user's cost constraint: no default extra fee and no remote call outside the explicit LLM gateway path.

## Tests

- Valid preflight returns `accepted`, `metadata_only`, `not_read`, `not_checked`, `not_called`, and `safe_to_load_config=false`.
- Response preserves `session_id`, `source`, and `trace_kind`.
- Response does not display the raw submitted path, filename, parent directory, or any path-derived label.
- Config file and environment secret sentinel values never appear in the response.
- File/env reads for provider config and secrets are guarded in tests and must not occur.
- The Live ASR `/events` response before and after preflight is identical.
- Transcript-only Live ASR sessions can run preflight.
- JSON persisted audit records can run preflight across app instances.
- Missing sessions return 404 without leaking submitted paths.
- Invalid bodies, missing fields, extra fields, unsupported mode/protocol, empty URL/control-character/traversal paths, unsupported or duplicate requested fields, and authorization flags that allow secret reads or LLM calls return 422 without leaking paths or secrets.
