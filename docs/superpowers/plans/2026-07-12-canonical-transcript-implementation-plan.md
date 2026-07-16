# Canonical Realtime Transcript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Workbench show one continuous, complete meeting transcript consisting of committed paragraphs plus one active tail, with safe FunASR cumulative reconciliation, in-place corrections, scroll-follow control and latest-session recovery.

**Architecture:** Add a backend canonical transcript projector that converts transcript events into an authoritative snapshot while retaining raw audit events. Fix FunASR presentation events so `text` and `normalized_text` describe the same incremental tail. Refactor the Workbench transcript renderer around canonical state and paragraph grouping; snapshots come from the backend projector while live WebSocket events update the same frontend reducer contract.

**Tech Stack:** FastAPI, Python 3.14, vanilla JavaScript, HTML/CSS, pytest, Node/Chrome CDP browser E2E, local FunASR realtime ASR.

## Execution Status - 2026-07-13

Implemented and verified:

- backend canonical transcript projector and events API snapshot;
- internally consistent FunASR incremental `text` / `normalized_text` fields;
- continuous committed document plus one `active_tail`;
- stable `projection_key` namespace separated from ASR `segment_id`;
- one render transaction for committed paragraphs plus active tail;
- 96px scroll-follow boundary and visible `↓ 有新内容` resume control;
- latest recoverable real-session restoration using wall-clock activity timestamps;
- honest interrupted real-microphone recovery state;
- canonical evidence clickback, in-place revision and original-text expansion;
- reconciled FunASR source snapshots remain correct after the active tail becomes final;
- provider errors preserve the complete visible transcript and only change status messaging;
- normal stream completion is recovered from the persisted evaluation summary;
- browser reload, scroll preservation, responsive layout and all visible button regression.

Fresh evidence:

```text
Python canonical / ASR / API / Workbench regression: 315 passed, 2 warnings
Python correction / suggestion / Workbench regression: 256 passed, 2 warnings
Real-microphone gate evaluator: 18 passed
Workbench all-buttons browser E2E: go_workbench_all_buttons_smoke
Browser screenshots: 25
Reconciled-final browser probe: exact canonical full text, active tail=0
Provider-error browser probe: canonical text and committed segments preserved
Real persisted session rec_mrh7w0eb:
  canonical segments=53
  active tail=1
  committed chars=2065
  full chars=2068
  duplicate visible 发言 label=0
Live page screenshot:
  artifacts/tmp/ui_screenshots/canonical-transcript-live-8767.png
```

Not claimed by this execution:

- a fresh controlled real-microphone acoustic run;
- production-grade Chinese ASR accuracy;
- recording-time formal AI suggestion production Go;
- 20-minute real meeting soak completion.

---

### Task 1: Add The Backend Canonical Transcript Projector

**Files:**
- Create: `code/web_mvp/backend/meeting_copilot_web_mvp/canonical_transcript.py`
- Create: `code/web_mvp/backend/tests/test_canonical_transcript.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [x] **Step 1: Write failing reducer tests**

Cover one active partial, final authority, revision replacement, unresolved revision supplement, ordering and `full_text` invariants. The primary cumulative case must assert:

```python
snapshot = project_canonical_transcript(
    session_id="rec_test",
    events=[
        transcript_final("seg_1", "第一段"),
        transcript_final("seg_2", "第二段"),
        transcript_partial("seg_3", "第三段正在说"),
    ],
)
assert snapshot["committed_text"] == "第一段第二段"
assert snapshot["full_text"] == "第一段第二段第三段正在说"
assert snapshot["active_tail"]["segment_id"] == "seg_3"
```

- [x] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_canonical_transcript.py -q
```

Expected: import failure because `canonical_transcript.py` does not exist.

- [x] **Step 3: Implement the projector**

Expose:

```python
SCHEMA_VERSION = "canonical-transcript.v1"

def project_canonical_transcript(*, session_id: str, events: list[dict]) -> dict:
    ...
```

The projector must:

- key normal transcript events by segment;
- apply authority `partial < final < revision`;
- keep only the newest unresolved partial as `active_tail`;
- preserve original text and evidence metadata;
- produce ordered `segments`, `committed_text`, `full_text` and character counts;
- never mutate input events.

- [x] **Step 4: Add canonical snapshot to the events API**

When returning `/live/asr/sessions/{session_id}/events`, append:

```python
body["canonical_transcript"] = project_canonical_transcript(
    session_id=session_id,
    events=body["events"],
)
```

Keep all existing response fields unchanged.

- [x] **Step 5: Verify projector and API tests GREEN**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_canonical_transcript.py \
  code/web_mvp/backend/tests/test_app.py -q \
  -k 'canonical_transcript or live_asr_session_events'
```

Expected: PASS.

### Task 2: Make FunASR Incremental Events Internally Consistent

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`
- Modify: `code/web_mvp/backend/tests/test_asr_stream.py`

- [x] **Step 1: Extend the cumulative FunASR regression test**

Require both fields to contain only the new suffix:

```python
assert second_partial["text"] == second
assert second_partial["normalized_text"] == normalize(second)
assert second_partial["source_snapshot_text"] == first + second
```

Also test a small cumulative re-recognition where the newest snapshot changes the last few characters. The result must preserve all source text through reconciliation and must not silently return an empty tail.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_asr_stream.py -q \
  -k 'cumulative_funasr or cumulative_rerecognition'
```

Expected: FAIL because `normalized_text` still contains the cumulative snapshot and `source_snapshot_text` is absent.

- [x] **Step 3: Implement safe incremental presentation events**

For `funasr_realtime` partials:

```python
display_text = incremental_suffix(committed_source_snapshot, source_snapshot)
event["source_snapshot_text"] = source_snapshot
event["text"] = display_text
event["normalized_text"] = _normalize_text(display_text)
```

Use exact prefix removal first. For bounded re-recognition, use a conservative longest common prefix/overlap calculation and mark `projection_reconciled=True`. Never fabricate text or discard an unmatched source snapshot.

- [x] **Step 4: Verify persistence and end-of-stream behavior**

Assert that persisted finals reconstruct the complete transcript, the final FunASR cumulative event does not duplicate already committed text, and non-FunASR Provider event IDs remain unchanged.

- [x] **Step 5: Run the ASR stream suite**

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_asr_stream.py -q
```

Expected: PASS.

### Task 3: Replace Event-Row Rendering With A Canonical Document Renderer

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`
- Modify: `tests/test_workbench_productized_ui.py`
- Modify: `code/web_mvp/e2e/workbench_all_buttons_smoke.mjs`

- [x] **Step 1: Add failing canonical renderer contract tests**

Require these helpers:

```text
createCanonicalTranscriptState
applyCanonicalTranscriptEvent
replaceCanonicalTranscriptSnapshot
renderCommittedTranscriptDocument
upsertCanonicalActiveTail
```

Tests must reject direct append rendering of raw partial/final events. They must require one `#transcript-document`, one `#transcript-active-tail`, paragraph markup with segment spans, and no visible repeated `发言：` label on every endpoint.

- [x] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  tests/test_workbench_productized_ui.py -q \
  -k 'canonical or active_tail or transcript_document'
```

- [x] **Step 3: Add stable transcript containers**

Use:

```html
<div id="transcript-document" class="transcript-document"></div>
<div id="transcript-active-tail" class="transcript-active-tail" hidden></div>
<button id="btn-new-transcript-content" class="new-content-button" hidden>↓ 有新内容</button>
```

Keep `#transcript-stream` as the containing compatibility boundary so existing navigation and evidence tests continue to work.

- [x] **Step 4: Implement canonical frontend state**

State must store committed segments, one active tail and original/revision metadata. Partial updates only mutate the tail node. Final/revision updates rebuild committed paragraphs, then remove or replace the tail. Snapshot loading must prefer `body.canonical_transcript` and fall back to projecting legacy events.

- [x] **Step 5: Implement bounded paragraph grouping**

Group adjacent committed segments when gap is at most 3000ms and combined display text is at most 180 characters. Render each underlying segment as a span carrying `data-segment-id`, evidence attributes and correction state so evidence clickback remains precise.

- [x] **Step 6: Update browser projection probes**

The browser smoke must inject `A -> AB -> ABC` source behavior as canonical segments and prove:

```text
visible full text = ABC
committed paragraphs do not contain AAB or AABABC
active tail count <= 1
revision changes text without increasing segment count
```

- [x] **Step 7: Run Workbench and browser regression**

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  tests/test_workbench_productized_ui.py \
  tests/test_workbench_all_buttons_smoke.py -q

node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs
```

Expected: Python PASS and browser status `go_workbench_all_buttons_smoke`.

### Task 4: Add Scroll-Follow Control And Latest Real Session Recovery

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`
- Modify: `code/web_mvp/e2e/workbench_all_buttons_smoke.mjs`

- [x] **Step 1: Add failing scroll-follow tests**

Require a 96px near-bottom threshold, no forced scroll while reading older content, a visible new-content button, click-to-resume and reduced-motion handling.

- [x] **Step 2: Add failing session recovery tests**

Require startup to load non-demo sessions, choose the newest session containing transcript or audio when no current session exists, fetch its events, apply its canonical snapshot and show `已恢复最近会议`.

- [x] **Step 3: Implement scroll-follow state**

Track whether the transcript center is following the bottom. Before each update record the prior position; after updates scroll only when following. Show `#btn-new-transcript-content` otherwise.

- [x] **Step 4: Implement latest-session recovery**

Add `restoreLatestRealSession()` after readiness loading and before the idle empty state is finalized. Do not restore mock/demo sessions. If the recovered session was interrupted, retain its transcript and show an honest disconnected status rather than `录音中`.

- [x] **Step 5: Add browser E2E coverage**

Test user-scroll-up preservation, new-content button behavior, page reload and restoration of the same canonical full text.

- [x] **Step 6: Verify focused and all-buttons tests**

Run the Workbench suites and browser smoke. Expected: PASS with no console/network errors.

### Task 5: Full Chain Verification And Traceable Documentation

**Files:**
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_gate.mjs`
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_gate.test.mjs`
- Modify: `docs/decision-log.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/real-mic-workbench-mainline-report-2026-07-10.md`

- [x] **Step 1: Add canonical transcript evidence fields**

Record:

```text
canonical_committed_char_count
canonical_full_char_count
canonical_segment_count
canonical_active_tail_count
canonical_duplicate_prefix_count
canonical_full_text_matches_export
latest_session_restore_status
scroll_follow_status
```

- [x] **Step 2: Run controlled no-cost audio verification**

Use a multi-section Chinese technical source with pauses. The fresh controlled lane produced continuous recording-time text, one active tail at a time, no cumulative duplication, complete export and a matching recording SHA. The lane used local FunASR and a local fake OpenAI-compatible gateway, so it is chain evidence rather than production LLM evidence.

- [x] **Step 3: Run bounded real-microphone verification**

The bounded runs were executed and classified fail-closed: one run passed the audio-health gate but produced no non-empty FunASR final, and another was blocked by the audio-health gate. Both preserved the recording and matching export SHA. This step is complete as an evidence exercise, not as a production Go gate.

Use the current local microphone path only after the deterministic gate passes. Do not repeat paid LLM calls unless the first run exposes a transient rather than deterministic failure.

- [x] **Step 4: Run full regression**

```bash
PYTHONPATH=code/web_mvp/backend python3 -m pytest \
  code/web_mvp/backend/tests/test_canonical_transcript.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_realtime_transcript_correction.py \
  code/web_mvp/backend/tests/test_realtime_corrections_api.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  tests/test_workbench_productized_ui.py \
  tests/test_workbench_all_buttons_smoke.py -q

node --test code/web_mvp/e2e/workbench_browser_live_mic_gate.test.mjs
node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
git diff --check
```

- [x] **Step 5: Record the decision and actual result**

Document the canonical transcript contract, cumulative reconciliation, complete-document UX, session restore behavior, artifacts and remaining No-Go items. Do not claim production completion unless full text, export equality, real microphone and recording-time AI gates all pass.

## Reconciled Result - 2026-07-13

The implementation and evidence steps in this plan are complete. The product release is not.

Fresh workspace verification:

- backend package regression: 642 passed, 2 warnings
- root regression: 351 passed, 2 warnings
- ASR runtime regression: 89 passed, 1 warning
- Node syntax checks: passed
- git diff --check: passed
- 8767 /health: status ok, service meeting-copilot-web-mvp

The deterministic correction fixture also passed the real backend API to canonical UI path:

- status: go_deterministic_correction_e2e
- revision_count: 1
- canonical target: det_corr_seg_1
- canonical source: det_corr_seg_1:rtc-v1
- original ASR disclosure: true
- original evidence clickback: true
- counts_as_production_llm_evidence: false
- remote_asr_called: false
- local_gateway_called: true

Remaining release gates are intentionally outside this plan: a newly rotated, explicitly authorized remote OpenAI-compatible gateway run; natural Chinese multi-speaker microphone quality; Chinese technical terminology and sentence-boundary quality; real wall-clock long-meeting soak; and Mac/Windows package acceptance. Any credential previously exposed in chat must be rotated and supplied only through an ignored environment/config mechanism before the remote gateway gate is run.
