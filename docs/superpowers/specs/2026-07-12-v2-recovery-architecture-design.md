# Meeting Copilot V2 Recovery Architecture Design

## Status

This document is the authoritative recovery design for the 2026-07-12 V2 refactor.
Root-level documents that claim all stages are complete are historical drafts until the
acceptance gates in this document pass on the current worktree.

## Product Goal

Deliver a local-first PC meeting copilot that can record or import Chinese technical
meetings, show one continuous canonical transcript during the meeting, surface timely
AI-backed advice, retain audio and history, and produce evidence-linked post-meeting
artifacts without adding a paid ASR dependency.

## Architecture Boundaries

### 1. Persistence Boundary

All persistent repositories accept a data directory, not a database-file path. The
single SQLite database is always `<data_dir>/meeting_copilot.db`. JSON migration is an
explicit idempotent startup operation that runs before repository construction.

Required invariants:

- configuring `MEETING_COPILOT_DATA_DIR` must start successfully;
- two app instances using the same directory must see the same sessions;
- migration must not create `meeting_copilot.db/meeting_copilot.db`;
- deleting a session must remove its database record and owned audio atomically or
  report the remaining delete scope honestly;
- production startup must configure a persistent data directory.

### 2. Realtime Meeting Boundary

The browser captures audio and sends ordered PCM frames to one resumable meeting
session. The server owns ASR, canonical transcript projection, audio persistence and
meeting activity timestamps.

Required invariants:

- visible text is committed canonical segments plus at most one active tail;
- WebSocket reconnect retains unsent audio in a bounded queue and flushes it in order;
- reconnecting to the same session never creates a second logical meeting;
- pause stops audio frame production without ending the session;
- provider failure never clears committed transcript text;
- normal end, interrupted end and no-audio end remain distinguishable after reload.

### 3. AI Derivation Boundary

Local deterministic reminders and remote formal LLM artifacts are separate. Remote
calls use the configured OpenAI-compatible gateway only after acceptance, budget,
degradation, cooldown and deduplication gates pass.

Required invariants:

- no formal AI artifact is labelled real when the provider is mock or acceptance is
  blocked;
- recording-time formal suggestions must be visible during recording in production
  mode, not generated only after stop;
- suggestion, approach and minutes artifacts carry evidence spans that resolve to the
  canonical transcript;
- usage accounting records provider/model, prompt tokens, completion tokens and total
  tokens; estimated cost must be labelled as an estimate;
- budget exhaustion blocks new paid calls but preserves recording and local ASR.

### 4. Client Interaction Boundary

`workbench.js` owns meeting state and the main workflow. Optional settings code may use
public client APIs only and must load after the main script. Duplicate modal markup and
patch scripts that depend on private variables are not allowed.

Required invariants:

- every HTML id is unique and all modal markup is inside `<body>`;
- browser and Tauri use valid static asset URLs;
- settings save only non-secret preferences; API credentials remain environment or OS
  credential-store configuration and are never persisted in localStorage;
- the history modal consumes the actual `{sessions: [...]}` API contract;
- all visible buttons have one business meaning and a browser E2E path.

### 5. Degradation And Release Boundary

Degradation is enforced at service boundaries, not merely exposed by a status API.
The release state is fail-closed.

Levels:

- Level 0: recording, local ASR, local reminders and paid AI enabled;
- Level 1: recording and ASR enabled; paid AI uses stricter confidence/budget gates;
- Level 2: recording and local ASR enabled; paid AI disabled;
- Level 3: recording enabled; ASR and paid AI disabled, with an explicit recording-only
  UI state;
- Level 4: recording unavailable; meeting start is blocked with an actionable reason.

Production completion requires all of the following on the current code:

- Python regression green;
- JavaScript syntax and contract tests green;
- all-buttons browser E2E green with screenshots and no runtime/5xx errors;
- persistent restart and migration test green;
- reconnect audio continuity test green;
- fresh controlled real-microphone run green;
- recording-time formal AI suggestion gate green in production mode;
- long-meeting soak and audio export SHA gate green;
- Mac package produced and smoke-tested; Windows remains an explicit separate gate.

## Data Flow

```text
Mic or imported media
  -> local audio preprocessing
  -> local realtime/batch ASR
  -> persisted raw events + canonical transcript + audio asset
  -> local reminder candidates
  -> acceptance/budget/degradation gate
  -> OpenAI-compatible formal suggestions / approaches / minutes
  -> evidence-linked Workbench and exports
```

## Error Handling

- Startup and migration errors fail startup with a specific message; they are never
  swallowed.
- Paid AI failures degrade AI only and retain transcript/audio.
- ASR failures escalate degradation and retain audio when recording-only mode is
  available.
- Client fetch and WebSocket failures preserve the current readable meeting state.
- Documentation reports the exact failing gate and never converts a skipped check into
  a pass.

## Documentation Authority

The active order is:

1. this architecture design;
2. `docs/superpowers/plans/2026-07-12-v2-recovery-implementation-plan.md`;
3. current automated and browser evidence;
4. `docs/current-mainline-index.md` and `docs/decision-log.md`.

Root-level completion reports are not release evidence until reconciled against these
sources.
