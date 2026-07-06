# PCWEB-038 Live ASR State/Scheduler Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let synthetic `live_asr_stream` final/revision events create local deterministic meeting-state candidates and scheduler placeholder events, without calling ASR models, remote ASR, LLM, or loading real audio.

**Architecture:** Keep `asr_live_events.py` as the source-boundary adapter from ASR streaming contract to Web live envelope. Add a tiny deterministic state/scheduler skeleton after `final` and `revision` events so the UI can prove Live ASR is more than transcript rendering while still making the no-LLM boundary explicit.

**Tech Stack:** FastAPI, Pydantic, pytest, plain JavaScript EventSource UI, Chrome/CDP smoke script.

---

## Scope

This is a narrow PC Web MVP increment. It does not introduce real desktop audio capture, FunASR/sherpa model loading, remote ASR, remote LLM, persistent ASR session storage, or full report generation.

The deterministic heuristic is intentionally small:

- If a final/revision ASR text contains `灰度`, emit a `DecisionCandidate`.
- The `DecisionCandidate` references the active EvidenceSpan for that final/revision.
- Emit a `scheduler_event` placeholder with `scheduler_event_type=state_gap_detected`, `prompt_version=not-called`, and `model=not-called`.
- Do not emit `llm_schema_result`, `suggestion_card`, `suggestion_silenced`, or `suggestion_invalidated`.

## Files

- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
  - Add `state_event` / `scheduler_event` order.
  - Add deterministic state candidate and scheduler placeholder builders.
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
  - Add RED test for state/scheduler skeleton.
  - Update existing Live ASR event order expectations.
- Modify: `code/web_mvp/backend/tests/test_app.py`
  - Update Live ASR API/SSE test expectations.
  - Update static asset assertions for explicit no-LLM scheduler copy.
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`
  - Update Live ASR browser smoke to expect state board and scheduler trace, but still zero suggestion cards and no report.
- Modify: `code/web_mvp/README.md`
  - Document Live ASR now emits deterministic local state/scheduler placeholders only.
- Modify: `docs/requirements-traceability-matrix.md`
  - Add `PCWEB-038`.
- Modify: `docs/pc-local-web-mvp-acceptance.md`
  - Add `AC-PCWEB-031`.
- Modify: `docs/end-to-end-design-checklist.md`
  - Update P0/P1 status.
- Modify: `docs/project-structure.md`
  - Update Live ASR boundary summary.
- Modify: `docs/implementation-roadmap.md`
  - Update D0.5 status.
- Modify: `docs/decision-log.md`
  - Add `DEC-038`.
- Create: `docs/pcweb-038-live-asr-state-scheduler-plan.md`
  - Capture decision, boundaries, TDD evidence, and acceptance.

## Task 1: Backend RED Test

**Files:**
- Modify: `code/web_mvp/backend/tests/test_live_events.py`

- [ ] **Step 1: Write the failing test**

Add a test that builds Live ASR events from `_asr_stream_events()` and expects deterministic state/scheduler events after final and revision:

```python
def test_build_asr_live_events_emits_local_state_and_scheduler_skeleton():
    events = build_asr_live_events(
        session_id="local_asr_contract",
        provider="local_mock_asr",
        streaming_events=_asr_stream_events(),
    )

    event_types = [event["event_type"] for event in events]
    assert event_types == [
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "transcript_revision",
        "state_event",
        "scheduler_event",
        "provider_error",
        "evaluation_summary",
    ]
    assert "llm_schema_result" not in event_types
    assert "suggestion_card" not in event_types
    assert "suggestion_silenced" not in event_types

    state = next(event for event in events if event["id"] == "state:asr_state_event_asr_seg_001")
    assert state["payload"]["state_item"] == {
        "id": "asr_decision_asr_seg_001",
        "statement": "先灰度 10%。",
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }

    scheduler = next(event for event in events if event["id"] == "scheduler:asr_state_event_asr_seg_001")
    assert scheduler["payload"]["scheduler_event_type"] == "state_gap_detected"
    assert scheduler["payload"]["source_event_ids"] == ["asr_state_event_asr_seg_001"]
    assert scheduler["payload"]["prompt_version"] == "not-called"
    assert scheduler["payload"]["model"] == "not-called"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_emits_local_state_and_scheduler_skeleton -q
```

Expected: FAIL because no `state_event` or `scheduler_event` exists in Live ASR events yet.

## Task 2: Backend GREEN Implementation

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- Modify: `code/web_mvp/backend/tests/test_live_events.py`

- [ ] **Step 1: Implement event ordering and skeleton helpers**

Add `state_event` and `scheduler_event` to `EVENT_ORDER`, then append state/scheduler events after final/revision events when the text contains `灰度`.

Use this payload shape:

```python
{
    "id": f"state:asr_state_event_{segment_id}",
    "event_type": "state_event",
    "at_ms": at_ms + 1,
    "payload": {
        "event_id": f"asr_state_event_{segment_id}",
        "target_type": "DecisionCandidate",
        "target_id": f"asr_decision_{segment_id}",
        "state_event_type": "created",
        "evidence_span_ids": [evidence_id],
        "state_item": {
            "id": f"asr_decision_{segment_id}",
            "statement": text,
            "evidence_span_ids": [evidence_id],
            "source": "live_asr_stream",
            "state_origin": "local_deterministic_asr_skeleton",
        },
    },
}
```

Use this scheduler payload shape:

```python
{
    "scheduler_event_type": "state_gap_detected",
    "card_id": "",
    "gap_rule_id": "asr.state_candidate.review",
    "trigger_source": "live_asr_state_skeleton",
    "trigger_reason": "ASR final/revision produced a deterministic state candidate; LLM disabled in PCWEB-038",
    "segment_batch": [segment_id],
    "source_event_ids": [state_event_id],
    "prompt_version": "not-called",
    "model": "not-called",
}
```

- [ ] **Step 2: Update existing expected event order**

The existing Live ASR mapping test should expect state/scheduler events between final/revision and terminal events. It should keep the evidence lifecycle assertions unchanged.

- [ ] **Step 3: Run focused backend tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py -q
```

Expected: all `test_live_events.py` tests pass.

## Task 3: API and Frontend Browser Expectations

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Update API/SSE expectations**

In `test_create_asr_live_session_events_json_and_sse_use_asr_boundary`, expect:

```python
[
    "transcript_partial",
    "transcript_final",
    "state_event",
    "scheduler_event",
    "transcript_revision",
    "state_event",
    "scheduler_event",
    "evaluation_summary",
]
```

Also assert SSE contains:

```python
assert "event: state_event" in sse_response.text
assert "event: scheduler_event" in sse_response.text
assert "not-called" in sse_response.text
```

- [ ] **Step 2: Update browser smoke Live ASR section**

After switching to Live ASR, wait for `.event-item.scheduler_event` and `.state-item`, then assert:

```js
assert(liveAsrReview.eventText.includes("scheduler_event"), "expected live ASR scheduler placeholder");
assert(liveAsrReview.eventText.includes("not-called"), "expected live ASR no-LLM scheduler marker");
assert(liveAsrReview.stateText.includes("先灰度 5%"), "expected live ASR state candidate from revision");
assert(liveAsrReview.cardCount === 0, "expected live ASR skeleton not to create suggestion cards");
assert(liveAsrReview.stateCount >= 1, "expected live ASR skeleton to create local state candidates");
```

- [ ] **Step 3: Run focused API tests**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_create_asr_live_session_events_json_and_sse_use_asr_boundary tests/test_app.py::test_workbench_static_assets_are_served -q
```

Expected: both tests pass.

## Task 4: Documentation

**Files:**
- Modify: `code/web_mvp/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Create: `docs/pcweb-038-live-asr-state-scheduler-plan.md`

- [ ] **Step 1: Add PCWEB-038/AC-PCWEB-031**

State that Live ASR now emits local deterministic state/scheduler skeleton events for final/revision, with no LLM/schema/card/report.

- [ ] **Step 2: Add DEC-038**

Record:

- Accepted decision to create deterministic local state/scheduler placeholders.
- Reason: prove value path beyond transcript without paying for LLM or claiming true intelligence is complete.
- Boundary: no real state engine, scheduler, LLM, report, desktop audio, or provider quality claim.
- Verification commands and browser smoke evidence.

- [ ] **Step 3: Update roadmap/checklist**

Move the gap from "Live ASR does not create state/scheduler" to "Live ASR has deterministic local state/scheduler skeleton; true state engine/scheduler/LLM still missing."

## Task 5: Full Verification and Cleanup

**Files:**
- No planned source edits.

- [ ] **Step 1: Run focused backend tests**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_live_events.py tests/test_app.py -q
```

- [ ] **Step 2: Run browser smoke**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
```

- [ ] **Step 3: Run quality gates**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

- [ ] **Step 4: Cleanup local test caches**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
find code tests tools -path '*/.venv-*' -prune -o -type d \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} + && rm -rf .pytest_cache
```

- [ ] **Step 5: Check ports and sensitive strings**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
# Run the project sensitive-string scan from the local safety checklist.
# Do not write real keys, real recording paths, or relay hostnames into this plan.
```

Expected: no port listeners and no sensitive-string matches.
