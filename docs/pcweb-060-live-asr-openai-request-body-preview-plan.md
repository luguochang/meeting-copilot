# PCWEB-060 Live ASR OpenAI-Compatible Request Body Preview Plan

## Goal

Add a local, read-only OpenAI-compatible chat completions request body preview for Live ASR LLM request drafts.

The preview converts existing no-LLM `llm_request_draft_event` records into deterministic request body envelopes that a future enabled executor can send to an OpenAI-compatible chat completions provider. It does not read provider config, does not read credentials, does not call the LLM gateway, does not estimate tokens, does not generate schema results, does not create suggestion cards, and does not mutate Live ASR audit events.

This increment moves the product closer to real-time AI suggestions by fixing the future LLM request contract before any paid or credentialed execution is enabled.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-openai-request-body-previews
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "provider_protocol": "openai_compatible_chat_completions",
  "preview_status": "body_preview_only",
  "llm_call_status": "not_called",
  "credentials_status": "not_read",
  "config_source_status": "not_read",
  "schema_status": "not_generated",
  "card_status": "not_created",
  "cost_status": "not_estimated",
  "safe_to_execute": false,
  "request_body_preview_count": 5,
  "request_body_previews": [
    {
      "request_body_preview_id": "asr_openai_request_body_preview_asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "request_body_status": "preview_only",
      "request_id": "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
      "request_draft_sequence": 6,
      "request_type": "llm_suggestion_card_draft",
      "target_candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "target_type": "DecisionCandidate",
      "target_id": "asr_decision_asr_seg_001",
      "gap_rule_id": "release.rollback.owner.required",
      "idempotency_key": "live_asr_openai_request_body_preview:local_asr_stream_review:asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001",
      "provider_protocol": "openai_compatible_chat_completions",
      "endpoint_family": "chat_completions",
      "http_method": "POST",
      "request_path": "/v1/chat/completions",
      "model": "not_configured",
      "temperature": 0.2,
      "max_output_tokens": 600,
      "messages": [
        {
          "role": "system",
          "content": "You are Meeting Copilot. Generate one concise suggestion card for an engineering meeting. Use only the provided evidence."
        },
        {
          "role": "user",
          "content": "Target: DecisionCandidate asr_decision_asr_seg_001\nGap rule: release.rollback.owner.required\nEvidence spans: asr_ev_asr_seg_001\nSegment batch: asr_seg_001\nCandidate quality: high (0.9)\nSuggested prompt: 确认决策是否包含 owner、回滚条件和监控口径。\nInput summary: DecisionCandidate asr_decision_asr_seg_001 from asr_seg_001 using asr_ev_asr_seg_001"
        }
      ],
      "response_format": {
        "type": "json_schema",
        "json_schema": {
          "name": "SuggestionCardV1",
          "strict": true
        }
      },
      "metadata": {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "request_origin": "local_deterministic_asr_request_draft",
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "segment_batch": ["asr_seg_001"],
        "candidate_confidence": 0.9,
        "candidate_confidence_level": "high",
        "candidate_degradation_reasons": []
      },
      "forbidden_request_fields": [
        "api_key",
        "authorization",
        "bearer_token",
        "base_url",
        "raw_config",
        "config_path"
      ],
      "llm_call_status": "not_called",
      "schema_status": "not_generated",
      "card_status": "not_created",
      "cost_status": "not_estimated"
    }
  ],
  "forbidden_request_fields": [
    "api_key",
    "authorization",
    "bearer_token",
    "base_url",
    "raw_config",
    "config_path"
  ],
  "block_reasons": [
    "request_body_preview_only",
    "provider_config_not_loaded",
    "credentials_not_read",
    "llm_executor_disabled"
  ],
  "next_required_decisions": [
    "authorized_config_file_reader",
    "secret_storage_adapter",
    "enabled_executor_mode_contract",
    "schema_validation_and_card_lifecycle",
    "token_cost_accounting"
  ]
}
```

## Request Body Preview Rules

- One request body preview is produced per `llm_request_draft_event`.
- Preview order follows original Live ASR event order.
- `model` is always `not_configured`; the endpoint never reads model config.
- `request_path` is always `/v1/chat/completions`.
- `messages` always contain a deterministic system message and a deterministic user message derived only from request draft metadata already present in the Live ASR audit record.
- `response_format` declares the target schema name `SuggestionCardV1` but does not include a full JSON Schema in PCWEB-060. Full schema payload design remains a later increment.
- `metadata` carries request origin, source events, evidence ids, segment batch, and candidate quality signals already present in the draft.
- `temperature` and `max_output_tokens` are fixed local preview defaults; they are not a cost estimate and they do not imply execution readiness.

## Boundaries

- Read only.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No real provider/model selection.
- No token estimate or cost accounting.
- No full JSON Schema generation.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No event mutation or appended preview event.
- No ranking, dedupe, retry, status transition, pagination, or background worker in PCWEB-060.

## Why This Matters

PCWEB-048 made future LLM requests visible as no-call draft audit events. PCWEB-050 converted those drafts into execution preview envelopes. PCWEB-060 fixes the next concrete contract: the request body that will eventually go to an OpenAI-compatible chat completions endpoint.

This is more product-relevant than another provider setting boundary because it verifies the core Meeting Copilot value path: ASR text -> local state candidate -> suggestion candidate -> LLM request draft -> future request body. The real executor can later be enabled behind config, secret storage, timeout, cost, and schema lifecycle gates without redesigning the prompt envelope.

## Tests

- Request body preview query returns one preview per `llm_request_draft_event`.
- It preserves linkage to request id, request draft event id, source state events, evidence ids, segment batch, target candidate, target state, gap rule, and candidate quality metadata.
- It builds deterministic OpenAI-compatible `messages` and `response_format` preview payloads.
- It marks every preview and top-level response as `preview_only` / `not_called` / `not_generated` / `not_created` / `not_estimated` / `safe_to_execute=false`.
- It never includes provider config, base URL, API key, authorization header, bearer token, raw config, config path, or any secret-derived field.
- It returns empty previews for transcript-only Live ASR sessions.
- It reads JSON persisted records across app instances.
- It returns 404 for missing Live ASR sessions.
- Reading previews must not mutate the Live ASR event stream or append a new event.
- Existing request-draft, execution-preview, provider-boundary, and dry-run endpoints remain narrow and unchanged.
