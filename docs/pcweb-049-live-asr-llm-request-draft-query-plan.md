# PCWEB-049 Live ASR LLM Request Draft Query Plan

## Goal

Add a read-only endpoint for Live ASR no-LLM LLM request draft audit records.

The endpoint exposes request drafts that already exist in the Live ASR audit event stream. It does not call an LLM, execute a request, choose a real model, estimate tokens, validate schema, create suggestion cards, or mutate candidate status.

## Endpoint

```http
GET /live/asr/sessions/{session_id}/llm-request-drafts
```

Response shape:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "request_draft_count": 5,
  "request_drafts": [
    {
      "sequence": 6,
      "event_id": "llm_request_draft:asr_state_event_asr_seg_001",
      "event_type": "llm_request_draft_event",
      "at_ms": 3500,
      "payload": {
        "request_status": "draft_only",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created"
      }
    }
  ]
}
```

## Boundaries

- Read only.
- No LLM gateway or relay call.
- No real model selection.
- No token estimate.
- No schema validation.
- No `llm_schema_result`.
- No `suggestion_card`.
- No `suggestion_silenced`.
- No sorting, ranking, filtering, dedupe, or pagination in PCWEB-049.
- Missing session returns the same Live ASR 404 boundary as existing Live ASR endpoints.

## Why This Matters

PCWEB-047 lets later engines read the candidate queue. PCWEB-048 makes the future LLM request assembly visible in the event stream and draft review. The next stable boundary is a narrow query endpoint that lets a future scheduler/card engine inspect draft requests without parsing the full `/events` response and without accidentally mixing candidates, request drafts, schema results, or formal cards.

## Tests

- Request draft query returns only `llm_request_draft_event` projections.
- It preserves original `sequence`, `event_id`, `event_type`, `at_ms`, and full payload.
- It supports empty queues for transcript-only Live ASR sessions.
- It reads JSON persisted records across app instances.
- It returns 404 for missing Live ASR sessions.
- Existing candidate query remains candidate-only and does not include request drafts.
- No formal suggestion card, schema result, silenced event, or LLM call is created.
