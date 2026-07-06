# PCWEB-043 Live ASR Draft UI Plan

> Date: 2026-07-01  
> Scope: PC Web MVP, frontend surfacing of local Live ASR draft review.

## Goal

When a Live ASR stream reaches `evaluation_summary`, the Web workbench must show the existing non-formal Live ASR draft review in the report panel. This keeps the user-facing review path useful without upgrading the synthetic ASR skeleton into a formal gated report.

## Product Boundary

Live ASR draft UI is a review surface for the local audit record only:

- It fetches `/live/asr/sessions/{session_id}/draft.md`.
- It displays the markdown text in the existing report panel.
- It must clearly include the draft warning: `Draft only; not a formal gated meeting report.`
- It must not fetch `/sessions/{session_id}/report.md` in Live ASR mode.
- It must not create suggestion cards, LLM schema results, or silenced suggestion events.
- It must not call remote ASR, remote LLM, or read real audio.

## Implementation

1. Add frontend tests that require Live ASR mode to reference `/draft.md` and gate it by `currentEventMode === "live_asr"`.
2. Extend browser smoke to count Live ASR `draft.md` requests and assert the report panel contains the draft warning and state candidate section.
3. Add `loadLiveAsrDraft()` to `frontend_static/app.js`.
4. In `connectLiveEventStream()`, call `loadLiveAsrDraft()` after terminal `evaluation_summary` only when `currentEventMode === "live_asr"`.
5. Guard async report/draft responses with captured session id and mode so stale responses cannot overwrite a newer session.
6. Guard async session creation with a session load token so stale Live ASR creation responses cannot overwrite a newer replay/live session.
7. Guard async event stream loads with captured session id and mode so stale JSON event responses cannot overwrite the current timeline.
8. Clear the active session while Live ASR session creation is pending so manual report refresh cannot request a draft for the previous session.
9. Apply the same terminal draft/report behavior when `EventSource` is unavailable and the UI falls back to JSON event loading.
10. Update requirements, acceptance, roadmap, checklist, project structure, README, and decision log.

## Tests

- `python3 -m pytest tests/test_app.py::test_workbench_static_assets_are_served -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

## Non-Goals

- No formal report generation from Live ASR skeleton.
- No LLM call.
- No suggestion card generation.
- No real desktop audio capture.
- No ASR model quality claim.
