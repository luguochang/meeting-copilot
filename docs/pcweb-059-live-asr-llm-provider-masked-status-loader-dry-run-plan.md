# PCWEB-059 Live ASR LLM Provider Masked Status Loader Dry Run Plan

## Goal

Add a local, no-read masked provider status loader dry-run endpoint for Live ASR sessions.

The endpoint validates a future masked-status loader request shape and authorization envelope. It does not read provider config files, does not check file existence, does not read environment secrets, does not resolve keychain or enterprise secret references, does not return raw paths or secret reference ids, and does not call the LLM gateway.

This increment moves one step closer to a real OpenAI-compatible provider settings UI while preserving the no-extra-cost and no-secret-leak boundary.

## Endpoint

```http
POST /live/asr/sessions/{session_id}/llm-provider-masked-status-loader-dry-run
```

Request shape:

```json
{
  "loader_mode": "masked_status_dry_run_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_path": "/local/private/path/llm-gateway.local.json",
  "secret_reference": {
    "reference_type": "keychain_item_reference",
    "reference_id": "meeting-copilot/openai-compatible"
  },
  "requested_display_fields": [
    "base_url_origin",
    "model",
    "timeout_seconds",
    "ca_bundle_name",
    "api_key"
  ],
  "authorization": {
    "user_confirmed_local_config_access": true,
    "acknowledged_secret_storage_policy": true,
    "allow_config_file_read": false,
    "allow_secret_read": false,
    "allow_llm_call": false,
    "allow_event_mutation": false,
    "allow_status_value_inference": false
  }
}
```

Successful response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "dry_run_kind": "authorized_masked_status_loader",
  "dry_run_status": "blocked",
  "dry_run_mode": "masked_status_dry_run_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_source_status": "caller_supplied_path_reference",
  "config_file_status": "not_read",
  "config_existence_status": "not_checked",
  "secret_reference_status": "provided_not_resolved",
  "secret_storage_status": "not_connected",
  "credentials_status": "not_read",
  "status_value_status": "not_inferred",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_read_config": false,
  "safe_to_read_secret": false,
  "safe_to_infer_status": false,
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
  "requested_display_fields": [
    "base_url_origin",
    "model",
    "timeout_seconds",
    "ca_bundle_name",
    "api_key"
  ],
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
    "base_url": "origin_only_after_authorized_loader",
    "model": "display_allowed_after_authorized_loader",
    "timeout_seconds": "display_allowed_after_authorized_loader",
    "ca_bundle_path": "basename_only_after_authorized_loader"
  },
  "authorization_summary": {
    "user_confirmed_local_config_access": true,
    "acknowledged_secret_storage_policy": true,
    "allow_config_file_read": false,
    "allow_secret_read": false,
    "allow_llm_call": false,
    "allow_event_mutation": false,
    "allow_status_value_inference": false
  },
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
    "status_value_inference_not_authorized",
    "secret_storage_adapter_not_connected",
    "llm_executor_disabled"
  ],
  "next_required_decisions": [
    "authorized_config_file_reader",
    "os_keychain_adapter",
    "enterprise_secret_provider_adapter",
    "enabled_executor_mode_contract",
    "schema_validation_and_card_lifecycle"
  ]
}
```

## Validation Rules

- Request body must be a JSON object.
- `loader_mode` must be exactly `masked_status_dry_run_only`.
- `provider_protocol` must be exactly `openai_compatible_chat_completions`.
- `config_path` must be a non-empty local filesystem path string; URL schemes/netlocs are rejected, control characters such as NUL and ASCII DEL are rejected, and path traversal is rejected for both POSIX `/../` and Windows `\..\` separators.
- `secret_reference` must contain exactly `reference_type` and `reference_id`.
- `reference_type` must be one of `keychain_item_reference`, `enterprise_secret_reference`, or `env_var_name_reference`.
- `reference_id` must be a non-empty string without control characters or ASCII DEL; it is never returned.
- `requested_display_fields` must contain one to five unique values from `base_url_origin`, `model`, `timeout_seconds`, `ca_bundle_name`, and `api_key`.
- `authorization` must contain exactly `user_confirmed_local_config_access=true`, `acknowledged_secret_storage_policy=true`, `allow_config_file_read=false`, `allow_secret_read=false`, `allow_llm_call=false`, `allow_event_mutation=false`, and `allow_status_value_inference=false`.
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
- No status-value inference from file existence, env vars, config fields, or secret refs.
- No API key value, masked value, presence, validity, length, hash, prefix, suffix, or fingerprint.
- No Live ASR audit event mutation.
- No network request.
- No remote ASR or LLM call.
- No token estimate or cost accounting.
- No schema generation.
- No formal suggestion card.
- No `suggestion_silenced`.

## Why This Matters

PCWEB-054 defines a template-only masked status envelope. PCWEB-058 defines a no-read authorized config reader request. PCWEB-059 connects those ideas without turning status display into a side-channel: the UI can ask what it would be allowed to display after a future authorized loader, but the Web MVP still refuses to read, infer, or execute anything.

This keeps the implementation aligned with the product constraint that default local development remains free and does not leak local paths or credentials.

## Tests

- Valid dry-run returns `blocked`, `masked_status_dry_run_only`, `not_read`, `not_checked`, `not_connected`, `not_inferred`, `not_called`, `safe_to_read_config=false`, `safe_to_read_secret=false`, `safe_to_infer_status=false`, and `safe_to_execute=false`.
- Response preserves `session_id`, `source`, and `trace_kind`.
- Response does not display raw submitted path, filename, parent directory, path-derived label, or `secret_reference.reference_id`.
- Response `display_values` are all `null`; `api_key` is `never_display` and `never_return_value_or_mask`.
- Endpoint must not read config file contents, call `Path.exists`, `Path.stat`, `os.stat`, read environment secrets, load gateway config, or access keychain.
- Endpoint must not mutate the Live ASR event stream.
- Transcript-only Live ASR sessions return the same blocked dry-run status.
- JSON persisted records can be read across app instances.
- Missing sessions return 404 without leaking submitted path or secret reference.
- Invalid request cases return 422 without leaking submitted path, secret reference, API key, bearer token, authorization, or raw config.
