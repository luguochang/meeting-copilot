# PCWEB-129 Mainline Trial Feedback And Export Closure Plan

> Date: 2026-07-04  
> Status: Implemented / verified  
> Boundary: no microphone, no user audio, no `.m4a`, no `configs/local`, no remote ASR, no remote LLM, no model download, no public audio download, no extra provider cost.

## Purpose

DEC-203 proved the PC product mainline can run through transcript, EvidenceSpan, state, scheduler, suggestion candidate, no-call LLM request draft, and draft review. The next product-value step is not another ASR/provider bake-off. It is closing the product loop:

```text
mainline trial session
  -> choose suggestion candidates
  -> collect local deterministic feedback
  -> produce Markdown/JSON export preview
  -> keep replay/synthetic evidence marked as not-Go
```

## Product Behavior

Add a one-click workbench closure for the current mainline trial session:

- It reads an existing mainline Live ASR session.
- Supported sources are `mainline_asr_blocked_trial` and `mainline_asr_event_artifact_trial`.
- It derives a DRV-033-compatible candidate report from the Live ASR draft events.
- It selects the first two suggestion candidates by default.
- It applies deterministic local feedback:
  - first candidate: `useful`
  - second candidate: `would_have_asked`
- It reuses DRV-038 feedback ingestion and DRV-036 export preview logic.
- It returns JSON and Markdown previews.
- It must clearly report:
  - `export_readiness_status=draft_export_preview_only`
  - `go_evidence_status=not_go_evidence_replay_or_feedback_missing`
  - `final_decision=inconclusive_requires_more_shadow_tests`

## API

```text
POST /desktop/mainline-trial-feedback-export-closures
```

Request:

```json
{
  "session_id": "mainline_asr_blocked_trial_... or mainline_asr_event_artifact_trial_...",
  "feedback_entries": []
}
```

`feedback_entries` is optional. If omitted or empty, the API chooses the deterministic local feedback entries described above.

Response:

```json
{
  "pcweb_id": "PCWEB-129",
  "closure_id": "mainline_trial_feedback_export_closure",
  "closure_status": "mainline_trial_feedback_export_preview_created",
  "session_id": "...",
  "source_trial_id": "mainline_asr_blocked_trial | mainline_asr_event_artifact_trial",
  "source_event_artifact_status": "not_applicable | local_asr_event_file_handoff_created",
  "candidate_report_validation_status": "passed",
  "feedback_ingestion_status": "shadow_report_feedback_ingested_preview_only",
  "export_readiness_status": "draft_export_preview_only",
  "go_evidence_status": "not_go_evidence_replay_or_feedback_missing",
  "final_decision": {
    "decision": "inconclusive_requires_more_shadow_tests"
  },
  "json_export_preview": {},
  "markdown_export_preview": "# Shadow Test Report: ..."
}
```

## UI

Add:

- toolbar button: `闭环预览`
- main column panel: `主线闭环`

Clicking `闭环预览`:

- posts the current session id to `/desktop/mainline-trial-feedback-export-closures`
- accepts both the synthetic blocked trial and approved ASR event artifact trial
- renders closure status, feedback/export readiness, selected candidate ids, and not-Go evidence state
- renders artifact source status when the current source is `mainline_asr_event_artifact_trial`
- writes `markdown_export_preview` to the existing report panel

2026-07-04 DEC-214/215 follow-up:

- `POST /desktop/mainline-asr-event-artifact-trial/sessions` can create an approved ASR event artifact-backed mainline session.
- The workbench now includes `工件主线`, which loads `artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json` through the artifact-backed endpoint.
- Browser smoke covers the user-visible artifact trial and closure path.
- Valid but too-thin artifacts still report `blocked_by_candidate_report`; this is a content-quality boundary.

## Files

- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`
- Modify docs:
  - `docs/decision-log.md`
  - `docs/current-mainline-index.md`
  - `docs/current-plan-and-validation-report-2026-07-04.md`
  - `docs/requirements-traceability-matrix.md`

## TDD Plan

1. Add a backend API test that creates a mainline ASR-blocked trial session, posts closure request, and expects feedback/export preview status.
2. Run the focused test and confirm it fails with `404`.
3. Implement the endpoint and helper functions.
4. Run the focused backend test to green.
5. Add static asset assertions for button, panel, JS function, and CSS classes.
6. Run the static focused test to red/green.
7. Extend browser smoke to click `闭环预览` after `主线试运行`.
8. Run full backend tests and browser smoke.
9. Run sensitive scan.

## Safety

This increment must not:

- access or enumerate microphone devices
- request microphone permission
- read real user audio
- write audio chunks
- read `configs/local`
- call remote ASR
- call remote LLM
- write export files
- claim real meeting Go evidence

## Implementation Result

Implemented on 2026-07-04.

Backend:

- Added `POST /desktop/mainline-trial-feedback-export-closures`.
- The endpoint reads an existing `mainline_asr_blocked_trial` Live ASR session from the local ASR live repository.
- It derives a DRV-033-compatible candidate report from the Live ASR draft review.
- It applies deterministic default feedback to the first two selected suggestion candidates:
  - first candidate: `useful`
  - second candidate: `would_have_asked`
- It reuses DRV-038 feedback ingestion and DRV-036 export preview logic.
- It returns JSON/Markdown previews in the API response only; it does not write export files.

Frontend:

- Added toolbar button `闭环预览`.
- Added workbench panel `主线闭环`.
- Clicking the button posts the current mainline trial session id to `/desktop/mainline-trial-feedback-export-closures`.
- The panel renders closure/export/not-Go status, selected candidate ids, feedback counts, and false safety flags.
- The Markdown export preview is rendered into the existing `report-panel`.

Verified behavior:

```text
export_readiness_status=draft_export_preview_only
go_evidence_status=not_go_evidence_replay_or_feedback_missing
final_decision=inconclusive_requires_more_shadow_tests
```

Verification:

```text
Focused backend API red: 404 before endpoint existed
Focused backend API green: 1 passed, 2 warnings
Focused static UI red: missing mainline-feedback-export-closure-button
Focused static UI green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline trial feedback export closure"
```

Post-review hardening:

- Added missing-session 404 regression coverage.
- Added non-mainline-session 422 regression coverage.
- Added direct assertions for deterministic default feedback:
  - `useful=1`
  - `would_have_asked=1`
  - `negative_feedback_count=0`
  - selected candidate ids match the first two mainline suggestion candidates.
- Added a side-effect boundary regression that guards against LLM config/secret reads, native audio/process probes, outbound calls, and export file writes under `shadow_report_exports`.
- Added browser assertions for `inconclusive_requires_more_shadow_tests`, `positive=2`, `negative=0`, and selected candidate id visibility.

Final verification:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py -q -p no:cacheprovider
Result: 292 passed, 2 warnings

node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline trial feedback export closure"

Sensitive scan
Result: no matches
```

Boundary confirmed:

- no microphone access
- no audio device enumeration
- no real user audio or `.m4a` read
- no `configs/local` read
- no remote ASR call
- no remote LLM call
- no model download
- no export file write
- no real meeting Go evidence claim
