# PCWEB-040 Live ASR State Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the local Live ASR state extraction skeleton so evidence-backed `final/revision` events can emit both `DecisionCandidate` and `OpenQuestion` state events.

**Architecture:** Keep extraction local and deterministic inside `asr_live_events.py`. Convert transcript event text into one or more small state candidate dictionaries, then reuse the existing no-LLM scheduler decision log for each emitted state event. The frontend continues to consume self-contained `state_event` payloads and does not fetch reports or display suggestion cards in Live ASR mode.

**Tech Stack:** Python 3, FastAPI test client, pytest, browser smoke through Node.js/Chrome CDP, plain JavaScript frontend.

---

### Task 1: Backend OpenQuestion Contract

**Files:**
- Modify: `code/web_mvp/backend/tests/test_live_events.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`

- [ ] **Step 1: Write the failing test**

Add this test to `code/web_mvp/backend/tests/test_live_events.py` near the ASR live tests:

```python
def test_build_asr_live_events_extracts_open_question_state_candidate():
    streaming_events = [
        {
            "event_type": "final",
            "segment_id": "asr_seg_question_001",
            "text": "谁负责回滚？",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 2600,
            "end_ms": 2600,
            "received_at_ms": 2600,
        },
    ]

    events = build_asr_live_events(
        session_id="local_asr_question_contract",
        provider="local_mock_asr",
        streaming_events=streaming_events,
    )

    state = next(event for event in events if event["event_type"] == "state_event")
    assert state["payload"]["target_type"] == "OpenQuestion"
    assert state["payload"]["target_id"] == "asr_question_asr_seg_question_001"
    assert state["payload"]["state_item"] == {
        "id": "asr_question_asr_seg_question_001",
        "question": "谁负责回滚？",
        "evidence_span_ids": ["asr_ev_asr_seg_question_001"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_extracts_open_question_state_candidate -q
```

Expected: fail because no `state_event` is emitted for a pure question segment.

- [ ] **Step 3: Implement minimal extraction contract**

In `asr_live_events.py`, replace the single `"灰度"` branch with helper functions that produce state specs:

```python
QUESTION_MARKERS = ("谁", "吗", "怎么", "是否", "有没有", "还没有确认", "?", "？")


def _extract_local_state_specs(text: str, segment_id: str, evidence_id: str) -> list[dict[str, Any]]:
    specs = []
    if "灰度" in text:
        specs.append(
            {
                "state_event_id": f"asr_state_event_{segment_id}",
                "target_type": "DecisionCandidate",
                "target_id": f"asr_decision_{segment_id}",
                "state_item": {
                    "id": f"asr_decision_{segment_id}",
                    "statement": text,
                    "evidence_span_ids": [evidence_id],
                    "source": ASR_LIVE_SOURCE,
                    "state_origin": "local_deterministic_asr_skeleton",
                },
            }
        )
    if _looks_like_open_question(text):
        specs.append(
            {
                "state_event_id": f"asr_question_event_{segment_id}",
                "target_type": "OpenQuestion",
                "target_id": f"asr_question_{segment_id}",
                "state_item": {
                    "id": f"asr_question_{segment_id}",
                    "question": text,
                    "evidence_span_ids": [evidence_id],
                    "source": ASR_LIVE_SOURCE,
                    "state_origin": "local_deterministic_asr_skeleton",
                },
            }
        )
    return specs
```

Use each spec to build a `state_event` and matching `scheduler_event`.

- [ ] **Step 4: Run focused test to verify it passes**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_live_events.py::test_build_asr_live_events_extracts_open_question_state_candidate -q
```

Expected: pass.

### Task 2: API/SSE and Frontend Live ASR Sample

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [ ] **Step 1: Write failing API/SSE expectations**

Extend `_asr_live_payload()` in `test_app.py` with a final event:

```python
{
    "event_type": "final",
    "segment_id": "asr_seg_002",
    "text": "谁负责回滚？",
    "start_ms": 3400,
    "end_ms": 6100,
    "received_at_ms": 7000,
    "confidence": 0.9,
}
```

Update expected event sequence to include `transcript_final`, `state_event`, `scheduler_event` before `evaluation_summary`. Assert that one state event has `target_type == "OpenQuestion"` and `state_item.question == "谁负责回滚？"`.

- [ ] **Step 2: Run API test to verify it fails**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_create_asr_live_session_events_json_and_sse_use_asr_boundary -q
```

Expected: fail until the backend extraction and expected sequence agree.

- [ ] **Step 3: Update frontend sample and browser assertion**

Add the same question final event to `localAsrStreamingEvents()` in `app.js`. Update `browser_smoke.mjs` Live ASR assertions so `stateText` includes `谁负责回滚？`.

- [ ] **Step 4: Run focused backend tests**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_live_events.py tests/test_app.py -q
```

Expected: pass.

- [ ] **Step 5: Add public API ordering contract test**

Add `test_create_asr_live_session_keeps_multi_state_scheduler_pairs_at_api_boundary` to `test_app.py` with one final segment:

```python
"text": "先灰度 10%，谁负责回滚？"
```

Expected event order from `/live/asr/sessions/{session_id}/events` and `.events.sse`:

```text
transcript_final
state_event
scheduler_event
state_event
scheduler_event
evaluation_summary
```

### Task 3: Documentation and Quality Gates

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `code/web_mvp/README.md`

- [ ] **Step 1: Update tracking docs**

Add `PCWEB-040` to the requirements matrix, `AC-PCWEB-033` to the acceptance checklist, and `DEC-040` to the decision log. Wording must say this is a local deterministic extraction contract, not the production state engine.

- [ ] **Step 2: Run browser smoke**

Run:

```bash
cd code/web_mvp
node e2e/browser_smoke.mjs
```

Expected: JSON output with `"status": "ok"`.

- [ ] **Step 3: Run quality gates**

Run:

```bash
cd ../..
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: both commands exit 0.

- [ ] **Step 4: Cleanup and safety checks**

Run:

```bash
find code tests tools -path '*/.venv-*' -prune -o -type d \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} + && rm -rf .pytest_cache
lsof -nP -iTCP:8767 -sTCP:LISTEN || true
lsof -nP -iTCP:9223 -sTCP:LISTEN || true
```

Expected: no lingering listeners on 8767 or 9223.
