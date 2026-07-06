# PCWEB-047 Live ASR Suggestion Candidate Query Plan

## Goal

Expose the Live ASR no-LLM suggestion candidate audit queue through a narrow read-only API endpoint.

The endpoint is for scheduler/card-engine preparation and review tooling. It does not create suggestion cards, call LLMs, sort candidates, or change the Live ASR event stream.

## Endpoint

`GET /live/asr/sessions/{session_id}/suggestion-candidates`

Response:

```json
{
  "session_id": "local_asr_stream_review",
  "source": "live_asr_stream",
  "trace_kind": "live_event",
  "candidate_count": 5,
  "candidates": [
    {
      "sequence": 4,
      "event_id": "suggestion_candidate:asr_state_event_asr_seg_001",
      "event_type": "suggestion_candidate_event",
      "at_ms": 3500,
      "payload": {
        "candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
        "candidate_type": "state_gap_review",
        "candidate_policy_version": "asr-candidate-policy.v1",
        "confidence_source": "local_deterministic_heuristic",
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "llm_call_status": "not_called",
        "card_status": "not_created"
      }
    }
  ]
}
```

## Contract

- Reads only persisted/in-memory Live ASR audit records.
- Returns only events where `event_type == "suggestion_candidate_event"`.
- Preserves original event `sequence`, `id`, `at_ms`, and payload.
- Keeps response `source=live_asr_stream` and `trace_kind=live_event`.
- Returns `404` for missing session.
- Reuses the existing JSON persistence session-id validation for route-reachable invalid ids. Path traversal forms such as `../bad` are rejected by routing before this endpoint runs; existing create/delete tests continue to cover repository-level unsafe session-id protection.

## Boundaries

- Does not call ASR providers.
- Does not call LLM gateway or user relay.
- Does not generate `suggestion_card`.
- Does not generate `llm_schema_result`.
- Does not generate `suggestion_silenced`.
- Does not sort, rank, dedupe, merge, or filter candidates.
- Does not expose raw audio, audio chunks, local secrets, or `configs/local/**`.

## Why This Matters

PCWEB-045 and PCWEB-046 proved that Live ASR can emit candidate audit records with gap-rule and quality metadata. The next engine layer needs a stable way to inspect just that queue without parsing the full transcript/state/scheduler event stream. This endpoint makes the candidate boundary explicit and testable before any paid LLM work begins.

## Tests

- API returns exactly the candidate events for a Live ASR session.
- Candidate order follows original event sequence.
- Candidate payload retains policy/quality metadata and no-LLM/card-not-created markers.
- API returns an empty list for a valid Live ASR session with no state candidates.
- JSON persistence can read the query across app instances.
- Missing session returns 404.
- Existing Live ASR create/delete persistence tests continue to cover unsafe session id validation.
