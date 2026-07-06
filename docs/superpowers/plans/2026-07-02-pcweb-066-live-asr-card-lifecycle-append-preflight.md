# PCWEB-066 Live ASR Card Lifecycle Append Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dry-run-only endpoint that derives deterministic future event ids, idempotency keys, and append sequence placement for PCWEB-065 lifecycle preview events without mutating `/events`.

**Architecture:** Reuse the existing schema-validation payload parser and PCWEB-065 lifecycle preview helper, then layer a response-only append preflight plan over the preview events. The endpoint stays inside the current FastAPI local adapter and does not introduce a repository append API, idempotency store, provider config reader, secret reader, worker, or remote call.

**Tech Stack:** Python 3, FastAPI, pytest, existing Live ASR JSON audit repository.

---

## File Structure

- Modify `code/web_mvp/backend/tests/test_app.py` for PCWEB-066 TDD coverage and README gate update.
- Modify `code/web_mvp/backend/meeting_copilot_web_mvp/app.py` for the new endpoint and helper functions.
- Modify `docs/pcweb-066-live-asr-card-lifecycle-append-preflight-dry-run-plan.md` after implementation to record verification.
- Modify `docs/decision-log.md`, `docs/requirements-traceability-matrix.md`, `docs/pc-local-web-mvp-acceptance.md`, `docs/end-to-end-design-checklist.md`, `docs/project-structure.md`, `docs/implementation-roadmap.md`, `docs/privacy-and-data-flow.md`, and `code/web_mvp/README.md` to keep decisions and contracts aligned.

## Task 1: Failing Endpoint Tests

- [x] **Step 1: Add focused tests**

Add tests in `code/web_mvp/backend/tests/test_app.py` after the PCWEB-065 tests:

```python
def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_future_append_plan_without_mutating_events():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_schema_invalid_silenced_plan():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_policy_blocked_silenced_plan():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_blocks_existing_event_or_idempotency_conflicts():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_returns_404_for_unknown_request_id():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_reads_persisted_record_across_app_instances():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_returns_404_for_missing_session():
    ...

def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_rejects_request_shape_errors():
    ...
```

- [x] **Step 2: Run RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_future_append_plan_without_mutating_events tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_schema_invalid_silenced_plan tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_policy_blocked_silenced_plan tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_blocks_existing_event_or_idempotency_conflicts tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_returns_404_for_unknown_request_id tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_returns_404_for_missing_session tests/test_app.py::test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_rejects_request_shape_errors -q
```

Expected before implementation: failures with endpoint `404 Not Found`.

## Task 2: Minimal Endpoint Implementation

- [x] **Step 1: Add route**

Add:

```python
@app.post("/live/asr/sessions/{session_id}/llm-card-lifecycle-append-preflight-dry-runs")
def create_asr_live_session_llm_card_lifecycle_append_preflight_dry_runs(...):
    validated_payload = _validate_llm_schema_validation_dry_run_payload(payload)
    record = asr_live_repo.get(session_id)
    return _llm_card_lifecycle_append_preflight_dry_run_from_record(record, validated_payload)
```

- [x] **Step 2: Add helper**

Implement `_llm_card_lifecycle_append_preflight_dry_run_from_record()` by reusing `_llm_card_lifecycle_preview_dry_run_from_record()` and deriving `append_plan`.

- [x] **Step 3: Add deterministic conflict checks**

Check existing top-level `id`, top-level `idempotency_key`, and `payload.idempotency_key` against each future plan item.

- [x] **Step 4: Run GREEN focused tests**

Run the same focused command from Task 1. Expected: `8 passed`.

## Task 3: Documentation And Gates

- [x] **Step 1: Update docs and README**

Record PCWEB-066 as implemented once tests pass.

- [x] **Step 2: Run regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

- [x] **Step 3: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [x] **Step 4: Cleanup and sensitive scan**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find . \( -path '*/.venv-*' -o -path '*/.venv' \) -prune -o \( -type d -name '.pytest_cache' -o -type d -name '__pycache__' \) -exec rm -rf {} +
```

Run the standard project sensitive-value scan from the active handoff notes or release checklist. Do not write real local secret fragments, private recording paths, or relay hostnames into this plan file.

Expected: no sensitive matches outside ignored local config.
