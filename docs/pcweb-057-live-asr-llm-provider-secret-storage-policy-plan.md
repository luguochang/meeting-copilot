# PCWEB-057 Live ASR LLM Provider Secret Storage Policy Plan

## Goal

Add a local, read-only secret storage policy endpoint for Live ASR sessions.

The endpoint defines where future provider credentials may live and which secret signals are forbidden before any real provider config file reader or enabled executor exists. It does not read config files, does not read environment secrets, does not inspect keychain state, does not return masked or real credentials, and does not call the LLM gateway.

This increment keeps the product moving toward a real OpenAI-compatible provider loader while preserving the no-extra-cost and no-secret-leak boundary.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-provider-secret-storage-policy
```

Successful response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "policy_kind": "provider_secret_storage",
  "policy_status": "template_only",
  "provider_protocol": "openai_compatible_chat_completions",
  "config_source_status": "not_read",
  "secret_storage_status": "not_connected",
  "credentials_status": "not_read",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_execute": false,
  "safe_to_read_secret": false,
  "recommended_storage_order": [
    "os_keychain",
    "enterprise_secret_provider",
    "environment_variable_for_development_only"
  ],
  "allowed_secret_references": [
    "keychain_item_reference",
    "enterprise_secret_reference",
    "env_var_name_reference"
  ],
  "forbidden_storage_locations": [
    "repository_files",
    "configs_local_plaintext_api_key",
    "session_json",
    "live_asr_audit_events",
    "logs",
    "reports",
    "browser_local_storage"
  ],
  "forbidden_response_fields": [
    "api_key",
    "authorization",
    "bearer_token",
    "raw_config",
    "masked_api_key",
    "api_key_hash",
    "api_key_prefix",
    "api_key_suffix",
    "api_key_length",
    "api_key_fingerprint"
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
  "required_loader_guards": [
    "explicit_user_authorization",
    "path_privacy_redaction",
    "secret_value_redaction",
    "no_secret_in_error_response",
    "no_secret_in_audit_event",
    "no_secret_in_logs",
    "no_secret_in_browser_storage"
  ],
  "block_reasons": [
    "template_only_policy",
    "secret_storage_adapter_not_connected",
    "provider_config_not_loaded",
    "credentials_not_read",
    "llm_executor_disabled"
  ],
  "next_required_decisions": [
    "os_keychain_adapter",
    "enterprise_secret_provider_adapter",
    "authorized_config_file_reader",
    "authorized_masked_status_loader",
    "enabled_executor_mode_contract"
  ]
}
```

## Boundaries

- Read-only template policy.
- No config file read.
- No `configs/local/` access.
- No environment secret read.
- No keychain access.
- No provider config parsing.
- No raw, masked, hashed, prefixed, suffixed, fingerprinted, counted, or presence/validity credential signal.
- No Live ASR audit event mutation.
- No network request.
- No remote ASR or LLM call.
- No token estimate or cost accounting.
- No schema generation.
- No formal suggestion card.
- No `suggestion_silenced`.

## Why This Matters

PCWEB-056 preflights a future config loader request, but a real loader still cannot safely exist until secret storage policy is explicit. This endpoint gives UI/API consumers a stable template that says credentials must be referenced through an authorized storage adapter, not copied into session JSON, audit events, logs, reports, browser storage, or repository config files.

It also keeps the cost policy intact: current behavior remains local and free except for future explicitly enabled LLM calls.

## Tests

- Secret storage policy returns `template_only`, `not_connected`, `not_read`, `not_called`, `safe_to_execute=false`, and `safe_to_read_secret=false`.
- Response preserves `session_id`, `source`, and `trace_kind`.
- Response lists recommended storage order and forbidden storage locations.
- Response does not contain real or masked key values, key presence/validity/length/hash/prefix/suffix/fingerprint signals as top-level status.
- Config file, environment secret, keychain, and LLM gateway sentinels never appear in response.
- File/env reads for provider config and secrets are guarded in tests and must not occur.
- The Live ASR `/events` response before and after policy read is identical.
- Transcript-only Live ASR sessions can read the policy.
- JSON persisted audit records can read the policy across app instances.
- Missing sessions return 404.
