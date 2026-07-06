# PCWEB-052 Live ASR LLM Provider Readiness Plan

## Goal

Add a local, read-only readiness endpoint that explains why Live ASR LLM execution cannot be enabled yet.

The endpoint must not read local provider config, API keys, relay settings, or `configs/local/`. It reports the future OpenAI-compatible provider contract and the current blockers while keeping real LLM execution disabled.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-provider-readiness
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "readiness_status": "not_ready",
  "executor_mode": "disabled",
  "enabled_mode_status": "blocked",
  "provider_protocol": "openai_compatible_chat_completions",
  "provider_config_status": "not_loaded",
  "provider_config_source": "not_read",
  "credentials_status": "not_read",
  "base_url_status": "not_configured",
  "model_status": "not_configured",
  "llm_call_status": "not_called",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "request_draft_count": 5,
  "execution_preview_count": 5,
  "disabled_run_count": 5,
  "queue_status": "has_request_drafts",
  "can_execute_llm": false,
  "block_reasons": [
    "llm_executor_disabled",
    "provider_config_not_loaded",
    "credentials_not_read",
    "enabled_mode_not_designed"
  ],
  "required_config_fields": ["base_url", "api_key", "model"],
  "next_required_decisions": [
    "provider_config_secret_boundary",
    "enabled_executor_mode_contract",
    "schema_validation_and_card_lifecycle",
    "token_cost_accounting",
    "timeout_retry_and_degradation_policy"
  ]
}
```

Transcript-only sessions keep the same disabled/no-call readiness status, but use `queue_status=empty` and add `no_request_drafts` to `block_reasons`.

## Boundaries

- Read only.
- No event mutation or appended readiness event.
- No API key, `configs/local/`, environment secret, gateway, relay, or remote LLM call.
- No call to the manual `asr_bakeoff.llm_smoke` path.
- No real provider/model selection.
- No token estimate or budget charge.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No enabled-mode fallback, retry, queue worker, background task, cursor, or pagination.

## Why This Matters

PCWEB-051 created a disabled executor action boundary. PCWEB-052 makes the next gating decision explicit: the product can see that an OpenAI-compatible provider is the intended future contract, but the runtime is not allowed to read credentials or execute calls until secret handling, schema validation, cost tracking, retry/degradation, and card lifecycle are separately designed.

This prevents a later implementation from quietly turning disabled dry-runs into paid calls.

## Tests

- Readiness returns `not_ready`, `executor_mode=disabled`, and `can_execute_llm=false`.
- It preserves session/source/trace kind and exposes request draft, execution preview, and disabled run counts.
- It reports provider config and credentials as `not_read` / `not_loaded`.
- It ignores any environment config path and still does not read credentials.
- It does not mutate the Live ASR event stream.
- Transcript-only Live ASR sessions return empty queue readiness.
- JSON persisted records can be read across app instances.
- Missing sessions return 404.
