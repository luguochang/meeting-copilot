# Realtime Transcript Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved focused two-column Workbench so realtime partial text updates in place, revisions replace committed rows, formal AI advice stays visible, and bounded LLM correction becomes visible during recording.

**Architecture:** Keep the existing FastAPI/WebSocket/session repository architecture. Add a segment-keyed frontend transcript projection and a small backend realtime-correction policy module. Suggestion calls may return a correction for their evidence segment; finals without a suggestion are corrected only when the 30-second/240-character batch gate opens. All correction results remain `transcript_revision` events so history, evidence and export stay traceable.

**Tech Stack:** FastAPI, Python 3.14, vanilla JavaScript, HTML/CSS, Node `node:test`, pytest, Chrome CDP E2E, OpenAI-compatible LLM gateway.

---

### Task 1: Replace Partial Chunk Append With Segment Upsert

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`

- [ ] **Step 1: Replace the old append-contract tests with failing upsert-contract tests**

Add assertions that require:

```python
def test_workbench_partial_updates_one_active_row_per_segment():
    js = TestClient(create_app()).get("/static/workbench.js").text
    assert "function upsertLivePartial" in js
    helper = js[js.index("function upsertLivePartial"):]
    helper = helper[: helper.index("function removeLivePartialForSegment")]
    assert "querySelector" in helper
    assert "document.createElement" in helper
    assert "replaceChildren" not in helper
    assert "data-live-segment-id" in helper
    assert "partial-draft-index" not in js
    assert "appendPartialDraftUtterance" not in js


def test_workbench_snapshot_keeps_only_latest_unresolved_partial_per_segment():
    js = TestClient(create_app()).get("/static/workbench.js").text
    assert "function latestUnresolvedPartials" in js
    assert "latestUnresolvedPartials(events)" in js
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  -q -k 'partial_updates_one_active_row or snapshot_keeps_only_latest_unresolved_partial'
```

Expected: FAIL because the old code still uses `appendPartialDraftUtterance()` and chunk rows.

- [ ] **Step 3: Implement one mutable partial row per segment**

In `workbench.js`:

```js
function livePartialSelector(segmentId) {
  return `[data-live-segment-id="${CSS.escape(String(segmentId || ""))}"]`;
}

function upsertLivePartial(event = {}, payload = {}, text = "") {
  const segmentId = partialDraftKey(event, payload);
  if (!segmentId || !shouldDisplayPartial(event, payload, text)) return false;
  clearStreamEmptyState();
  const stream = $("transcript-stream");
  let row = stream.querySelector(livePartialSelector(segmentId));
  if (!row) {
    row = document.createElement("div");
    row.className = "utterance live-partial";
    row.dataset.liveSegmentId = segmentId;
    stream.appendChild(row);
  }
  row.innerHTML = livePartialMarkup(event, payload, text);
  return true;
}

function removeLivePartialForSegment(segmentId = "") {
  const key = String(segmentId || "").trim();
  if (!key) return;
  document.querySelectorAll(livePartialSelector(key)).forEach((row) => row.remove());
}
```

Replace the committed partial chunk map with a latest-text map used only for dedupe/metrics. Snapshot rendering must group unresolved partials by segment and render only the latest event for each segment. Final/revision branches remove the matching live row before committing.

- [ ] **Step 4: Run focused and surrounding Workbench tests**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py -q \
  -k 'partial or live_event or empty_final or snapshot_renderer'
```

Expected: PASS.

### Task 2: Replace Revisions In Place And Expose Original Text

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`

- [ ] **Step 1: Add failing revision replacement tests**

Add assertions requiring:

```python
def test_workbench_revision_replaces_target_row_in_place():
    js = TestClient(create_app()).get("/static/workbench.js").text
    assert "function upsertCommittedTranscript" in js
    assert "data-transcript-segment-id" in js
    revision_branch = js[js.index('e.event_type === "transcript_revision"'):]
    assert "upsertCommittedTranscript" in revision_branch
    assert "AI 已校正" in js


def test_workbench_corrected_row_can_reveal_original_asr_text():
    js = TestClient(create_app()).get("/static/workbench.js").text
    assert "查看原始识别" in js
    assert "original-asr-text" in js
```

- [ ] **Step 2: Run and verify RED**

Run the two tests directly. Expected: FAIL because revisions currently append rows.

- [ ] **Step 3: Implement committed-row upsert**

Create a helper that:

```js
function upsertCommittedTranscript(event = {}, payload = {}, text = "", options = {}) {
  const targetSegmentId = String(
    options.targetSegmentId
    || payload.supersedes_segment_id
    || payload.revision_of
    || payload.segment_id
    || event.segment_id
    || ""
  ).trim();
  // Find/create one row by data-transcript-segment-id.
  // Preserve raw final as data/original details.
  // Replace display text for revisions instead of appending.
}
```

Revision markup must include a subdued `AI 已校正` badge and a `<details class="original-asr-text">` containing the escaped original final. Final markup must not show the badge. Snapshot rebuilding must choose the latest revision per target segment and produce one row.

- [ ] **Step 4: Verify revision, evidence and history regression**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py -q \
  -k 'revision or evidence or history or snapshot'
```

Expected: PASS.

### Task 3: Implement The Approved Two-Column Focused Workbench

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`
- Modify: `tests/test_workbench_productized_ui.py`
- Modify: `code/web_mvp/e2e/workbench_all_buttons_smoke.mjs`

- [ ] **Step 1: Add failing structural UI tests**

Require:

```python
def test_workbench_uses_focused_two_column_meeting_layout():
    html = TestClient(create_app()).get("/workbench").text
    assert 'grid-template-areas:"topbar topbar" "center right" "status status"' in html
    assert '<aside class="left"' not in html
    assert 'id="meeting-status-strip"' in html
    assert 'id="realtime-guidance-panel"' in html
    assert html.index('id="candidate-panel"') < html.index('id="suggestions-panel"')


def test_workbench_right_rail_names_local_reminders_and_formal_ai_separately():
    html = TestClient(create_app()).get("/workbench").text
    assert "实时提醒" in html
    assert "AI 建议" in html
    assert "实时建议" not in html
```

- [ ] **Step 2: Run UI tests and verify RED**

Expected: FAIL because the page still has a three-column cockpit.

- [ ] **Step 3: Restructure HTML/CSS without changing button IDs**

Use desktop grid:

```css
.app {
  grid-template-columns:minmax(0,1.9fr) minmax(320px,1fr);
  grid-template-areas:
    "topbar topbar"
    "center right"
    "status status";
}
```

Move meeting stage, transcript/reminder/card/audio/minutes counts into `#meeting-status-strip` in the topbar. Remove the visible left aside. In the right rail, keep recording-time sections ordered as status, realtime reminders, AI suggestions, then session actions. Put approach/minutes/history/privacy into a collapsed review/tools region or post-meeting tabs while preserving all existing IDs and button functionality.

On mobile, order must be topbar, center, right, status. Use no nested cards and keep panel headings compact.

- [ ] **Step 4: Update JS navigation to target the new status strip**

Existing overview navigation must continue to focus transcript/reminders/suggestions/audio/minutes. Replace selectors that assume `.left` with `#meeting-status-strip [data-overview-target]`.

- [ ] **Step 5: Update all-buttons E2E screenshots and run UI regression**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  tests/test_workbench_productized_ui.py \
  tests/test_workbench_all_buttons_smoke.py -q

node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

Expected: Python PASS; browser status `go_workbench_all_buttons_smoke`; all existing buttons and exports remain covered.

### Task 4: Add Bounded Realtime Final Correction

**Files:**
- Create: `code/web_mvp/backend/meeting_copilot_web_mvp/realtime_transcript_correction.py`
- Create: `code/web_mvp/backend/tests/test_realtime_transcript_correction.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/llm_service.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/auto_suggestion_orchestrator.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/tests/test_llm_service.py`
- Modify: `code/web_mvp/backend/tests/test_auto_suggestions.py`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`

- [ ] **Step 1: Add failing policy tests**

The new module must expose:

```python
POLICY_VERSION = "realtime-transcript-correction.v1"
MIN_BATCH_CHARS = 240
MIN_INTERVAL_MS = 30_000

def eligible_final_batch(record: dict, *, force: bool = False) -> dict: ...
def build_revision_event(*, session_id: str, final_event: dict, corrected_text: str, source: str, usage: dict) -> dict | None: ...
def apply_revision_events(record: dict, revisions: list[dict], *, status: dict) -> dict: ...
```

Tests must prove:

- partial events are never eligible;
- already revised segments are skipped;
- fewer than 240 chars before 30 seconds is deferred;
- 240 chars or 30 seconds opens the gate;
- `force=True` corrects remaining finals on stop;
- empty, identical, extreme-length or low-similarity corrections are rejected;
- a valid revision references the original segment/evidence and records non-secret usage metadata.

- [ ] **Step 2: Verify RED**

Run the new test file. Expected: import failure.

- [ ] **Step 3: Extend suggestion output schema with optional correction**

Change the suggestion system prompt to require parseable JSON:

```json
{
  "suggestion_text": "...",
  "confidence": 0.0,
  "trigger_reason": "...",
  "corrected_transcript": "same evidence text with ASR term errors corrected"
}
```

`execute_candidate()` must keep old fake/test responses compatible when `corrected_transcript` is absent. When present, return a `transcript_correction` object tied to the single evidence segment. Validate it through `build_revision_event()` before persistence.

- [ ] **Step 4: Persist combined suggestion corrections**

`auto_suggestion_orchestrator.run_once()` must append a valid revision to `record.events` in the same repository replacement that persists the suggestion card. The API response must expose `transcript_revisions` so Workbench can apply them immediately.

- [ ] **Step 5: Add a fallback batch-correction endpoint**

Add:

```text
POST /live/asr/sessions/{session_id}/realtime-corrections/run-once
body: { force: false }
```

The endpoint:

- uses only acceptance-eligible final events;
- returns without an LLM call when the 30-second/240-character gate is closed;
- calls existing `asr_correct.correct_transcript()` once for the selected batch;
- converts corrected output into per-segment revision events only when mapping remains safe;
- persists correction status, usage and revisions;
- never exposes credentials.

For the first implementation, a batch containing more than one final is corrected as one bounded text block and split only when the original segment boundaries can be preserved exactly through explicit indexed delimiters. If mapping fails, reject the revision rather than guessing.

- [ ] **Step 6: Trigger correction after a persisted final**

After `runAutoSuggestionsOnce({reason: "live_final"})`, call the fallback correction endpoint. Use a frontend single-flight + one pending trigger, matching the auto-suggestion pattern. Apply returned revisions through `appendLiveEvent()`/segment upsert. Do not call on partials.

- [ ] **Step 7: Run backend/frontend correction regression**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_realtime_transcript_correction.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_workbench.py -q
```

Expected: PASS with no real gateway calls in tests.

### Task 5: Full Mainline Verification And Documentation

**Files:**
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_gate.mjs`
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_gate.test.mjs`
- Modify: `docs/decision-log.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/real-mic-workbench-mainline-report-2026-07-10.md`

- [ ] **Step 1: Add browser evidence fields**

Recording samples and summary must include:

```text
active_live_partial_count
committed_transcript_row_count
corrected_transcript_row_count
max_rows_for_single_active_segment
first_correction_visible_latency_ms
realtime_transcript_compaction_status
```

The gate fails when one active segment creates more than one partial row or when revision increases the committed row count instead of replacing its target.

- [ ] **Step 2: Run no-cost browser regression**

Use the all-buttons file lane and a short real-microphone no-cost run. Verify partial compaction, layout, audio save, transcript, cards, minutes and delete without a paid call.

- [ ] **Step 3: Run one bounded production real-microphone verification**

Only after no-cost gates pass, use the existing configured 8765 provider and a controlled Chinese technical source. Require:

- recording-time text visible;
- at most one active partial row per segment;
- one evidence-backed formal AI card visible during recording;
- one `AI 已校正` row visible during recording;
- recording saved and SHA matched;
- no console/network error.

- [ ] **Step 4: Run full regression**

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_realtime_transcript_correction.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_live_events.py \
  tests/test_workbench_productized_ui.py \
  tests/test_workbench_all_buttons_smoke.py -q

node --test code/web_mvp/e2e/workbench_browser_live_mic_gate.test.mjs
node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
git diff --check
```

- [ ] **Step 5: Record the decision and actual result**

Document implemented behavior, tests, screenshots, artifact paths, token usage, and any remaining No-Go. Do not claim production completion unless the bounded production real-mic run passes both formal suggestion and correction visibility gates.
