# PCWEB-058 Live ASR LLM Provider Config Reader Dry Run Plan

## Goal

Add a local, no-read authorized provider config reader dry-run endpoint for Live ASR sessions.

The endpoint validates a future config file reader request shape, authorization envelope, path privacy guards, and secret reference policy. It does not read the config file, does not check file existence, does not read environment secrets, does not resolve keychain or enterprise secret references, does not return raw paths or secret references, and does not call the LLM gateway.

This increment moves one step closer to a real OpenAI-compatible provider config reader while preserving the no-extra-cost and no-secret-leak boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-provider-config-reader-dry-run
```

Request shape:

```json
{
  "reader_mode": "dry_run_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_path": "/local/private/path/llm-gateway.local.json",
  "secret_reference": {
    "reference_type": "keychain_item_reference",
    "reference_id": "meeting-copilot/openai-compatible"
  },
  "authorization": {
    "user_confirmed_local_config_access": true,
    "acknowledged_secret_storage_policy": true,
    "allow_config_file_read": false,
    "allow_secret_read": false,
    "allow_llm_call": false,
    "allow_event_mutation": false
  }
}
```

Successful response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "dry_run_kind": "authorized_config_file_reader",
  "dry_run_status": "blocked",
  "dry_run_mode": "dry_run_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_source_status": "caller_supplied_path_reference",
  "config_file_status": "not_read",
  "config_existence_status": "not_checked",
  "secret_reference_status": "provided_not_resolved",
  "secret_storage_status": "not_connected",
  "credentials_status": "not_read",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_read_config": false,
  "safe_to_read_secret": false,
  "safe_to_execute": false,
  "path_display": {
    "config_path_label": null,
    "config_path_parent_name": null,
    "config_path": null
  },
  "secret_reference_display": {
    "reference_type": "keychain_item_reference",
    "reference_id": null
  },
  "authorization_summary": {
    "user_confirmed_local_config_access": true,
    "acknowledged_secret_storage_policy": true,
    "allow_config_file_read": false,
    "allow_secret_read": false,
    "allow_llm_call": false,
    "allow_event_mutation": false
  },
  "required_loader_guards": [
    "explicit_user_authorization",
    "path_privacy_redaction",
    "secret_reference_only",
    "secret_value_redaction",
    "no_secret_in_error_response",
    "no_secret_in_audit_event",
    "no_secret_in_logs",
    "no_secret_in_browser_storage"
  ],
  "forbidden_response_fields": [
    "api_key",
    "authorization",
    "bearer_token",
    "raw_config",
    "config_path",
    "absolute_config_path",
    "secret_reference_id",
    "masked_api_key",
    "api_key_hash",
    "api_key_prefix",
    "api_key_suffix",
    "api_key_length",
    "api_key_fingerprint"
  ],
  "forbidden_status_signals": [
    "config_file_exists",
    "config_file_readable",
    "config_file_size",
    "config_file_mtime",
    "config_file_hash",
    "api_key_present",
    "api_key_valid",
    "api_key_length",
    "api_key_hash",
    "api_key_prefix",
    "api_key_suffix",
    "api_key_fingerprint"
  ],
  "block_reasons": [
    "dry_run_only",
    "config_file_read_not_authorized",
    "secret_value_read_not_authorized",
    "secret_storage_adapter_not_connected",
    "llm_executor_disabled"
  ],
  "next_required_decisions": [
    "authorized_config_file_reader",
    "os_keychain_adapter",
    "enterprise_secret_provider_adapter",
    "authorized_masked_status_loader",
    "enabled_executor_mode_contract"
  ]
}
```

## Validation Rules

- Request body must be a JSON object.
- `reader_mode` must be exactly `dry_run_only`.
- `provider_protocol` must be exactly `openai_compatible_chat_completions`.
- `config_path` must be a non-empty local filesystem path string; URL schemes/netlocs are rejected, control characters such as NUL are rejected, and path traversal is rejected for both POSIX `/../` and Windows `\..\` separators.
- `secret_reference` must contain exactly `reference_type` and `reference_id`.
- `reference_type` must be one of `keychain_item_reference`, `enterprise_secret_reference`, or `env_var_name_reference`.
- `reference_id` must be a non-empty string without control characters; it is never returned.
- `authorization` must contain exactly `user_confirmed_local_config_access=true`, `acknowledged_secret_storage_policy=true`, `allow_config_file_read=false`, `allow_secret_read=false`, `allow_llm_call=false`, and `allow_event_mutation=false`.
- Unknown top-level fields are rejected with 422.

Invalid request responses must use generic details that do not echo submitted absolute paths, secret reference ids, API keys, authorization values, bearer tokens, or raw config.

## Boundaries

- Dry-run only.
- No config file read.
- No existence/readability/size/mtime/hash/fingerprint check.
- No `configs/local/` access.
- No environment secret read.
- No keychain or enterprise secret provider access.
- No secret reference resolution.
- No raw `config_path`, basename, parent directory, or path-derived label in response.
- No raw `reference_id` in response.
- No API key value, masked value, presence, validity, length, hash, prefix, suffix, or fingerprint.
- No Live ASR audit event mutation.
- No network request.
- No remote ASR or LLM call.
- No token estimate or cost accounting.
- No schema generation.
- No formal suggestion card.
- No `suggestion_silenced`.

## Why This Matters

PCWEB-057 defines where secrets may live. PCWEB-058 defines the next no-read boundary for a future config file reader: callers can submit a path reference, a secret reference, and explicit authorization flags, but current Web MVP still refuses to read config or secret values.

This keeps the implementation aligned with the product constraint that default local development remains free and does not leak local paths or credentials.

## Tests

- Valid dry-run returns `blocked`, `dry_run_only`, `not_read`, `not_checked`, `not_connected`, `not_called`, `safe_to_read_config=false`, `safe_to_read_secret=false`, and `safe_to_execute=false`.
- Response preserves `session_id`, `source`, and `trace_kind`.
- Response does not display raw submitted path, filename, parent directory, path-derived label, or `secret_reference.reference_id`.
- Config file, environment secret, keychain, secret reference, and LLM gateway sentinels never appear in the response.
- File/env reads for provider config and secrets are guarded in tests and must not occur.
- The Live ASR `/events` response before and after dry-run is identical.
- Transcript-only Live ASR sessions can run dry-run.
- JSON persisted audit records can run dry-run across app instances.
- Missing sessions return 404 without leaking submitted paths or secret references.
- Invalid bodies, missing fields, extra fields, unsupported mode/protocol, empty URL/control-character/traversal paths, invalid secret reference, and authorization flags that allow config reads, secret reads, event mutation, or LLM calls return 422 without leaking paths or secrets.
