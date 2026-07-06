# PCWEB-075 Live ASR Card Lifecycle Append Idempotency Store Write Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only, response-only idempotency store write preflight after PCWEB-074 that defines future idempotency store write behavior without writing the store or enabling event mutation.

**Architecture:** The new endpoint validates a `preflight_only` request, loads the Live ASR session, reuses PCWEB-074 transaction commit preflight as its source of truth, then projects commit checks into idempotency-store write contract checks. Fresh append is blocked until enabled but exposes future idempotency record identities; safe replay requires no write; partial replay, retry/replay conflicts, and transaction/mutation blockers remain blocked.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest contract tests in `code/web_mvp/backend/tests/test_app.py`, local docs under `docs/`.

---

### Task 1: Document PCWEB-075 Contract

**Files:**
- Create: `docs/pcweb-075-live-asr-card-lifecycle-append-idempotency-store-write-preflight-plan.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `code/web_mvp/README.md`

- [x] **Step 1: Define the endpoint contract**

Document:

```http
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-idempotency-store-write-preflights
```

with request fields `mode`, `request_id`, and `candidate_response`; `mode` must equal exactly `preflight_only`.

- [x] **Step 2: Define readiness statuses**

Document top-level `idempotency_store_write_readiness_status` values:

```text
blocked_until_enabled
safe_replay_existing_events
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_transaction_commit_preflight
```

Document per-check `idempotency_store_write_preflight_check_status` values:

```text
blocked_until_enabled
write_not_required_for_safe_replay
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_transaction_commit_preflight
```

- [x] **Step 3: Define write boundaries**

Document that PCWEB-075 must not mutate `/events`, write idempotency store records, begin/commit/rollback repository transactions, write audit events, create cards, read secrets/config, estimate cost, or call remote ASR/LLM.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add fresh append test**

Add a test that posts `mode=preflight_only` to the new endpoint for a fresh session and expects `idempotency_store_write_readiness_status=blocked_until_enabled`, two checks, `future_idempotency_record_status=would_write_if_enabled`, all safe flags false, and unchanged `/events`.

- [x] **Step 2: Add safe replay test**

Seed both matching lifecycle events with `_append_persisted_lifecycle_event`, call the endpoint, and expect `idempotency_store_write_readiness_status=safe_replay_existing_events`, per-check `write_not_required_for_safe_replay`, `future_idempotency_record_status=not_required_existing_replay`, persisted bytes unchanged, and `safe_to_write_idempotency_store=false`.

- [x] **Step 3: Add partial replay and conflict tests**

Seed only one matching lifecycle event for partial replay and mismatched lifecycle events for conflict. Assert `blocked_by_partial_replay` and `blocked_by_retry_replay_conflict`, with no event mutation and no idempotency store write.

- [x] **Step 4: Add shape and not-found tests**

Cover missing session, unknown request id, persisted read across app instances, 422 request shape errors, and README contract text.

- [x] **Step 5: Run RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "idempotency_store_write_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Result before implementation: the ten new endpoint tests failed with `404 Not Found`; README gate passed.

### Task 3: Implement Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add route**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-card-lifecycle-append-idempotency-store-write-preflights")
```

and use the same missing-session behavior as PCWEB-074.

- [x] **Step 2: Add payload validator**

Validate only `mode`, `request_id`, and `candidate_response`; require `mode == "preflight_only"` without trimming; trim only `request_id`.

- [x] **Step 3: Add preflight helper**

Create `_llm_card_lifecycle_append_idempotency_store_write_preflight_from_record()` that calls PCWEB-074 helper and returns response-only idempotency store write preflight metadata.

- [x] **Step 4: Add per-check projection helper**

Create `_card_lifecycle_append_idempotency_store_write_preflight_check()` that maps transaction commit preflight statuses into idempotency-store write statuses while keeping all write flags false.

### Task 4: Verify and Review

**Files:**
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- docs changed in Task 1

- [x] **Step 1: Focused tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "idempotency_store_write_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Result after implementation and post-review provenance fix: `12 passed, 219 deselected, 2 warnings`.

- [x] **Step 2: Backend regression**

Run:

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Result after post-review provenance fix: `269 passed, 2 warnings`.

- [x] **Step 3: Quality gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Results after post-review provenance fix: `pc-web` passed with core `34 passed`, web backend `272 passed`, and browser smoke passed. `all-local --no-browser` passed with ASR runtime `65 passed`, ASR bakeoff `18 passed`, core `34 passed`, and web backend `272 passed`.

- [x] **Step 4: Independent review and hygiene**

Run an independent read-only review, fix any critical or important issues, remove pytest/python caches, confirm no test ports are listening, and run the sensitive marker scan excluding `configs/local/**`.

Result: independent review found no Critical issue and one Important provenance issue; fixed with a regression test and reran focused/backend/quality gates. Final hygiene completed.

## Self-Review

- Spec coverage: endpoint, request validation, fresh append, safe replay, partial replay, conflict, no mutation, no idempotency write, persistence, and documentation gates are covered.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: field names match the PCWEB-075 plan and PCWEB-071 through PCWEB-074 naming style.
