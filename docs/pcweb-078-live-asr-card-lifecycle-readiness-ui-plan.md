# PCWEB-078 Live ASR Card Lifecycle Readiness UI Plan

## Context

PCWEB-077 exposes a response-only card lifecycle readiness summary for a Live ASR LLM request draft. The endpoint answers whether a future suggestion card lifecycle is blocked, safely replayable, partially replayed, or conflicted, while keeping every write, LLM, credential, transaction, idempotency, and audit-persistence capability disabled.

The Web workbench still does not surface this information. In Live ASR mode the user can see transcript, state candidates, scheduler events, suggestion candidates, request drafts, and the draft Markdown report, but the UI does not yet answer the product-critical question: why has this candidate not become an AI suggestion card yet?

PCWEB-078 adds a read-only workbench panel that consumes PCWEB-077 automatically after a Live ASR terminal summary. It keeps the product differentiated from a transcription tool by showing the card lifecycle blocker, phase readiness, next decisions, and disabled write/LLM boundaries directly in the meeting workbench.

## Scope

Add a `card-lifecycle-readiness-panel` to the local Web workbench. In Live ASR mode, after the finite local ASR SSE stream reaches `evaluation_summary`, the frontend selects one eligible no-LLM request draft from the already received event stream, builds a local contract probe candidate response, and calls:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries
```

with:

```json
{
  "mode": "summary_only",
  "request_id": "<request draft id from llm_request_draft_event>",
  "candidate_response": {
    "id": "card_readiness_probe_<request id>",
    "model": "not_called",
    "usage": {"total_tokens": 0}
  }
}
```

The local contract probe is not an LLM output. It is derived only from already visible Live ASR events:

- `llm_request_draft_event.payload.request_id`
- `target_type` and `target_id`
- `gap_rule_id`
- `source_event_ids`
- `evidence_span_ids`
- `segment_batch`
- transcript final/revision event `at_ms`
- state event `at_ms`

The UI must prefer an eligible draft whose evidence is active and whose candidate degradation list is empty. If none is eligible, it shows a local no-summary state rather than calling the endpoint with a knowingly invalid probe.

Implementation decision after browser smoke debugging: the terminal `evaluation_summary` handler must build the probe from the complete accumulated Live ASR event stream, not from the single terminal event. Otherwise the panel cannot see prior request drafts, state events, transcript timings, or evidence lifecycle updates. When the browser falls back to the non-SSE event list, that full list remains valid input.

Implementation decision for the local probe card type: the probe must use a core-gate allowed suggestion card type. The current core whitelist is `owner_gap`, `rollback_gap`, `test_verification_gap`, and `metric_monitoring_gap`; therefore the UI readiness probe uses `owner_gap` even when the source request draft targets an `ActionItem`, `Risk`, or `OpenQuestion`. This is intentionally a response-only lifecycle contract probe, not a formal product card taxonomy decision.

Review follow-up decision: if every visible request draft has stale/missing evidence or candidate degradation reasons, the UI must render a local no-summary state and skip the readiness POST. The workbench should not ask the backend to summarize a lifecycle for a probe it already knows is not eligible. The EventSource error recovery path must also run terminal side effects after a JSON `/events` fallback returns an `evaluation_summary`, matching the browser no-EventSource fallback.

## UI Contract

The panel renders:

- Overall readiness status.
- Source preflight kind/status.
- LLM/config/credential/cost statuses.
- Disabled mutation statuses: event append, audit append, idempotency store write, repository transaction commit.
- Phase count and a 12-row phase list.
- Block reasons.
- Next required decisions.
- Scoped `card_lifecycle_safe_to_*` flags as false-only status chips.

The UI must not expose upstream unscoped `safe_to_*` fields. It may render the PCWEB-077 scoped `card_lifecycle_safe_to_*` flags because PCWEB-077 deliberately keeps them false.

## Boundaries

- No real LLM call.
- No provider config read.
- No keychain, environment secret, authorization header, API key, bearer token, or `configs/local/` read.
- No remote ASR call.
- No card creation.
- No `llm_schema_result`, `suggestion_card`, or `suggestion_silenced` event creation.
- No Live ASR audit record mutation.
- No append result audit event persistence.
- No idempotency-store write or marker.
- No repository transaction begin, commit, rollback, lock, or lease.
- No formal report request in Live ASR mode.
- No local audio or real user recording access.

## Non-Goals

- Do not add a frontend build toolchain, React, Vite, Electron, Tauri, or desktop shell.
- Do not implement true LLM execution.
- Do not make readiness status actionable.
- Do not add card approval buttons for readiness summaries.
- Do not persist the UI summary.
- Do not enable hidden paid ASR or LLM services.

## Tests

- Static asset tests require the new panel DOM, render functions, endpoint string, and summary-field references.
- Browser smoke in Live ASR mode must verify that after terminal `evaluation_summary`:
  - the browser POSTs `/live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries`;
  - the panel shows `blocked_until_enabled`;
  - the panel shows `12 phases`;
  - the panel shows no-LLM/no-write statuses such as `not_called`, `not_appended`, and `not_written`;
  - exactly 12 lifecycle phase rows render;
  - Live ASR still creates zero formal suggestion cards and zero lifecycle events.
- Browser smoke must inspect the readiness POST body and prove `mode=summary_only`, `candidate_response.type=owner_gap`, `model=not_called`, and zero token usage.
- A degraded-only Live ASR stream must show a local no-summary state and make zero readiness-summary POSTs.
- The browser no-EventSource fallback must still render the readiness summary and use the same core-gate-compatible probe.
- README/docs gate must mention PCWEB-078 and its no-cost/no-write UI boundary.

## Implementation Status

- Status: implemented for the PCWEB-078 TDD increment.
- Frontend files: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`, `app.js`, `styles.css`.
- Browser gate: `code/web_mvp/e2e/browser_smoke.mjs`.
- Static/docs gate: `code/web_mvp/backend/tests/test_app.py`.

## Verification Plan

- RED: focused static/docs/browser-script tests must fail before implementation.
- GREEN: focused tests pass after implementation.
- Browser smoke: `node e2e/browser_smoke.mjs`.
- Backend regression: `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`.
- PC Web quality gate: `python3 tools/run_quality_gate.py --profile pc-web`.
- Final hygiene: remove test caches, confirm no app ports are left listening, and run local sensitive marker scan excluding `configs/local/**`.
