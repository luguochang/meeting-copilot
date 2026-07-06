# PCWEB-074 Live ASR Card Lifecycle Append Transaction Commit Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only, response-only transaction commit preflight after PCWEB-073 that inspects future lifecycle commit readiness without beginning, committing, rolling back, or mutating anything.

**Architecture:** The new endpoint validates a `preflight_only` request, loads the Live ASR session, reuses PCWEB-073 mutation preflight and PCWEB-071 retry/replay preflight as sources of truth, then projects both into transaction commit preflight checks. Retry/replay interpretation takes precedence for safe replay, partial replay, and conflicts; all write and commit flags remain disabled.

**Tech Stack:** FastAPI backend in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`, pytest contract tests in `code/web_mvp/backend/tests/test_app.py`, local docs under `docs/`.

---

### Task 1: Document PCWEB-074 Contract

**Files:**
- Create: `docs/pcweb-074-live-asr-card-lifecycle-append-transaction-commit-preflight-plan.md`
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
POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-commit-preflights
```

with request fields `mode`, `request_id`, and `candidate_response`; `mode` must equal exactly `preflight_only`.

- [x] **Step 2: Define readiness statuses**

Document top-level `transaction_commit_readiness_status` values:

```text
blocked_until_enabled
safe_replay_existing_events
blocked_by_partial_replay
blocked_by_retry_replay_conflict
blocked_by_mutation_preflight
```

- [x] **Step 3: Define write boundaries**

Document that PCWEB-074 must not mutate `/events`, write idempotency store, begin/commit/rollback repository transactions, write audit events, create cards, read secrets/config, estimate cost, or call remote ASR/LLM.

### Task 2: Add Failing Tests

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Add fresh append test**

Add a test that posts `mode=preflight_only` to the new endpoint for a fresh session and expects `transaction_commit_readiness_status=blocked_until_enabled`, two checks, all safe flags false, and unchanged `/events`.

- [x] **Step 2: Add safe replay test**

Seed both matching lifecycle events with `_append_persisted_lifecycle_event`, call the endpoint, and expect `transaction_commit_readiness_status=safe_replay_existing_events`, per-check `safe_replay_existing_event`, persisted bytes unchanged, and `safe_to_commit_transaction=false`.

- [x] **Step 3: Add partial replay and conflict tests**

Seed only one matching lifecycle event for partial replay and a mismatched event for conflict. Assert `blocked_by_partial_replay` and `blocked_by_retry_replay_conflict`, with no event mutation.

- [x] **Step 4: Add shape and not-found tests**

Cover missing session, unknown request id, persisted read across app instances, 422 request shape errors, and README contract text.

- [x] **Step 5: Run RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "transaction_commit_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

Result before implementation: the ten new endpoint tests failed with `404 Not Found`; README gate passed.

### Task 3: Implement Endpoint

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [x] **Step 1: Add route**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-commit-preflights")
```

and use the same missing-session behavior as PCWEB-073.

- [x] **Step 2: Add payload validator**

Validate only `mode`, `request_id`, and `candidate_response`; require `mode == "preflight_only"` without trimming; trim only `request_id`.

- [x] **Step 3: Add preflight helper**

Create `_llm_card_lifecycle_append_transaction_commit_preflight_from_record()` that calls PCWEB-073 and PCWEB-071 helpers, joins checks by `future_event_id`, and returns response-only commit preflight metadata.

- [x] **Step 4: Add per-check projection helper**

Create `_card_lifecycle_append_transaction_commit_preflight_check()` that maps retry/replay and mutation statuses into per-check transaction commit status while keeping all write flags false.

### Task 4: Verify and Review

**Files:**
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- docs changed in Task 1

- [x] **Step 1: Focused tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "transaction_commit_preflight or readme_documents_scripted_browser_e2e_gate" -q
```

- [x] **Step 2: Backend regression**

Run:

```bash
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

- [ ] **Step 3: Quality gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [ ] **Step 4: Independent review and hygiene**

Run an independent read-only review, fix any critical or important issues, remove pytest/python caches, confirm no test ports are listening, and run the sensitive marker scan excluding `configs/local/**`.

## Self-Review

- Spec coverage: endpoint, request validation, fresh append, safe replay, partial replay, conflict, no mutation, persistence, and documentation gates are covered.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: field names match the PCWEB-074 plan and PCWEB-069 through PCWEB-073 naming style.
