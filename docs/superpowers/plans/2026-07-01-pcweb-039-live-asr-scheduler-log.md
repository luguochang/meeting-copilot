# PCWEB-039 Live ASR Scheduler Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Live ASR local scheduler events from a single placeholder into an auditable local scheduler decision log without calling LLM or generating suggestion cards.

**Architecture:** Keep `asr_live_events.py` as the Live ASR boundary adapter. Add a small local scheduler decision model inside the Web MVP backend so `final/revision` state candidates produce `llm_candidate_queued` or `llm_candidate_skipped` events with cooldown/budget metadata and explicit `not-called` LLM status.

**Tech Stack:** FastAPI, pytest, plain JavaScript EventSource UI, Chrome/CDP smoke script.

---

## Scope

This plan implements `PCWEB-039` only. It does not call remote LLM/ASR, does not load ASR models, does not read audio files, does not persist scheduler logs, and does not generate suggestion cards.

## Files

- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
  - Add local scheduler config/state/decision helpers.
  - Replace `state_gap_detected` placeholder payload with queued/skipped audit payload.
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
  - Add scheduler log tests for queued and cooldown-skipped decisions.
  - Update existing PCWEB-038 scheduler payload expectations.
- Modify: `code/web_mvp/backend/tests/test_app.py`
  - Update API/SSE expectations for scheduler decision metadata.
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`
  - Assert Live ASR timeline displays `llm_candidate_queued`, `llm_candidate_skipped`, `cooldown`, and `not_called`.
- Modify: `code/web_mvp/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`

## Task 1: Backend RED Tests

- [ ] Add a test proving the first Live ASR state candidate emits:

```python
{
    "scheduler_event_type": "llm_candidate_queued",
    "decision_reason": "state_change",
    "would_call_llm": True,
    "llm_call_status": "not_called",
    "cooldown_remaining_ms": 0,
    "call_count_last_hour": 1,
    "budget_remaining": 79,
}
```

- [ ] Add a test proving a dense revision emits:

```python
{
    "scheduler_event_type": "llm_candidate_skipped",
    "decision_reason": "cooldown",
    "would_call_llm": False,
    "llm_call_status": "not_called",
    "cooldown_remaining_ms": 9999,
}
```

- [ ] Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_emits_scheduler_decision_log_for_state_candidates -q
```

Expected: FAIL because the payload still uses `state_gap_detected`.

## Task 2: Backend GREEN

- [ ] Add local scheduler config/state/decision helpers in `asr_live_events.py`.
- [ ] Track scheduler state across streaming events inside `build_asr_live_events`.
- [ ] Use state-change cooldown for state candidates.
- [ ] Keep source/trace/event order stable.
- [ ] Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py -q
```

Expected: all live event tests pass.

## Task 3: API/SSE and Browser Smoke

- [ ] Update `test_create_asr_live_session_events_json_and_sse_use_asr_boundary` to assert scheduler payload metadata.
- [ ] Update browser smoke to assert `llm_candidate_queued`, `llm_candidate_skipped`, `cooldown`, `not_called`, zero forbidden event types, and no report fetch.
- [ ] Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_create_asr_live_session_events_json_and_sse_use_asr_boundary -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
```

Expected: both pass.

## Task 4: Documentation

- [ ] Add `PCWEB-039` to requirements traceability.
- [ ] Add `AC-PCWEB-032` to acceptance.
- [ ] Add `DEC-039` to decision log.
- [ ] Update README, project structure, roadmap, and checklist so the P0 gap becomes: local scheduler audit exists; real scheduler event log and real LLM still missing.

## Task 5: Gates and Cleanup

- [ ] Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [ ] Cleanup:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find code tests tools -path '*/.venv-*' -prune -o -type d \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} + && rm -rf .pytest_cache
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
```

- [ ] Run the local sensitive-string scan from the safety checklist without writing real secrets or private paths into this plan.
