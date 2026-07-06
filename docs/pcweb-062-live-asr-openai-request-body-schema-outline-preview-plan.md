# PCWEB-062 Live ASR OpenAI Request Body Schema Outline Preview Plan

## Goal

Add a local `SuggestionCardV1` schema outline preview to the existing Live ASR OpenAI-compatible request body preview endpoint.

PCWEB-060 made the future chat-completions request body visible, but its `response_format` only named the target schema. PCWEB-062 makes the expected suggestion-card output fields visible as a deterministic local outline so the future LLM executor, schema validator, card lifecycle, and frontend can align on the contract before any paid or credentialed model call is enabled.

This is not full JSON Schema generation. It is an outline-only preview derived from the current local `SuggestionCardV1` contract shape.

## Endpoint

Existing endpoint:

```http
GET /live/asr/sessions/{session_id}/llm-openai-request-body-previews
```

The endpoint keeps the same top-level safety posture:

- `preview_status=body_preview_only`
- `llm_call_status=not_called`
- `credentials_status=not_read`
- `config_source_status=not_read`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`

## Response Format Addition

Each preview keeps OpenAI-compatible `response_format.type=json_schema` and `json_schema.name=SuggestionCardV1`.

PCWEB-062 adds outline metadata inside `response_format.json_schema`:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "SuggestionCardV1",
      "strict": true,
      "schema_outline_status": "outline_only",
      "schema_outline_source": "local_contract_preview",
      "schema_outline": {
        "type": "object",
        "required": [
          "id",
          "type",
          "evidence_span_ids",
          "state_refs",
          "state_event_ids",
          "gap_rule_id",
          "trigger_reason",
          "trigger_source",
          "final_segment_at_ms",
          "state_event_at_ms",
          "card_created_at_ms",
          "latency_ms",
          "prompt_version",
          "model",
          "usage",
          "schema_result",
          "show_or_silence_decision",
          "segment_batch",
          "status"
        ],
        "optional": [
          "title",
          "suggested_question"
        ],
        "properties": {
          "id": {"type": "string"},
          "type": {"type": "string"},
          "evidence_span_ids": {"type": "array", "items": {"type": "string"}},
          "state_refs": {"type": "array", "items": {"type": "string"}},
          "state_event_ids": {"type": "array", "items": {"type": "string"}},
          "gap_rule_id": {"type": "string"},
          "trigger_reason": {"type": "string"},
          "trigger_source": {"type": "string"},
          "final_segment_at_ms": {"type": "integer", "minimum": 0},
          "state_event_at_ms": {"type": "integer", "minimum": 0},
          "card_created_at_ms": {"type": "integer", "minimum": 0},
          "latency_ms": {"type": "integer", "minimum": 0},
          "prompt_version": {"type": "string"},
          "model": {"type": "string"},
          "usage": {"type": "object"},
          "schema_result": {"type": "string"},
          "show_or_silence_decision": {"type": "string"},
          "segment_batch": {"type": "array", "items": {"type": "string"}},
          "status": {"type": "string", "default": "new"},
          "title": {"type": ["string", "null"]},
          "suggested_question": {"type": ["string", "null"]}
        },
        "additional_properties_status": "allowed_by_local_contract_extra"
      }
    }
  }
}
```

## Contract Source

The outline mirrors the current local `SuggestionCardV1` dataclass contract:

- Required constructor fields and always-emitted `to_dict()` fields are listed under `required`.
- Optional `to_dict()` fields are listed under `optional`.
- `extra` is represented by `additional_properties_status=allowed_by_local_contract_extra`; this is an explicit preview marker, not JSON Schema validation.

The outline is intentionally local and deterministic. It does not introspect provider capabilities, generate a provider-specific schema dialect, or validate model responses.

## Boundaries

- Read only.
- No gateway, relay, or remote LLM call.
- No API key, authorization header, bearer token, provider config, base URL, `configs/local/`, keychain, secret adapter, or environment secret read.
- No provider config file existence/readability/size/mtime/hash/fingerprint check.
- No token estimate or cost accounting.
- No full JSON Schema generation.
- No schema validation.
- No model response parsing.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No event mutation or appended preview event.
- No change to PCWEB-061 redaction semantics.

## Why This Matters

The product value is not a transcript viewer; it is real-time, context-aware meeting help. To get there safely, the future model call must have a stable input and output contract before credentials or paid execution are enabled.

PCWEB-062 closes a concrete gap in that path:

- PCWEB-048 creates no-LLM request drafts.
- PCWEB-060 converts those drafts into future OpenAI-compatible request bodies.
- PCWEB-061 prevents sensitive draft values from being reflected into previews.
- PCWEB-062 makes the expected `SuggestionCardV1` response structure visible without calling a model.

This lets later work add enabled executor mode, schema validation, degradation, token accounting, and card lifecycle against a known local contract.

## Tests

- A normal request body preview includes `schema_outline_status=outline_only` and `schema_outline_source=local_contract_preview` inside `response_format.json_schema`.
- The outline required fields match current local `SuggestionCardV1` always-emitted fields.
- The outline optional fields match current local `SuggestionCardV1` optional `to_dict()` fields.
- The outline properties include stable primitive/list/object type hints for every required and optional field.
- Reading schema outline previews does not mutate `/events`.
- Redaction still applies to messages/metadata and does not alter the schema outline.
- Empty preview queues still return no request body previews and do not generate schema results.
- The endpoint still does not read provider config, environment secrets, keychain, or call LLM.
