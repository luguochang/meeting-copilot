# PCWEB-041 Live ASR Audit Persistence Plan

> Date: 2026-07-01  
> Scope: PC Web MVP, local Live ASR event audit records only.

## Goal

Persist local Live ASR event streams as JSON audit records when the Web MVP runs with a local data directory, so `live_asr_stream` sessions can be read after an app instance restart and deleted through the existing session deletion lifecycle.

## Why This Matters

`PCWEB-037` through `PCWEB-040` proved that local ASR streaming JSON can produce transcript, EvidenceSpan, state events, and no-LLM scheduler audit events. The current storage is still an in-process dictionary inside `create_app()`. That is not enough for a PC client where the user expects a meeting run to be traceable, inspectable, and deletable.

This increment turns the local Live ASR stream into a minimal persisted audit artifact without expanding cost or model scope.

## Requirements

- When `create_app(data_dir=...)` is used, `POST /live/asr/mock/sessions` writes a JSON record under the configured local data directory.
- The persisted record must include:
  - `session_id`
  - `provider`
  - `source=live_asr_stream`
  - `trace_kind=live_event`
  - `events`
- A new app instance using the same `data_dir` must serve the same Live ASR JSON and SSE events.
- `DELETE /sessions/{session_id}` must delete the matching Live ASR audit record as well as any normal session record.
- ASR live session ids must use the same safe id policy as JSON session records.
- In-memory mode must keep existing behavior.
- Unknown Live ASR sessions still return 404.
- Live ASR audit persistence must not call LLM, read real audio, read `configs/local`, or write API keys.

## Boundaries

This is not a production recording data lifecycle. It persists only JSON event audit records generated from synthetic/request-provided ASR streaming events.

Out of scope:

- raw audio persistence
- audio chunk persistence
- transcript report generation
- formal session snapshot generation from Live ASR
- real ASR provider storage
- real scheduler event log storage outside this JSON audit record
- LLM calls or suggestion-card generation

## Design

Add a small repository module for Live ASR audit records:

- `InMemoryAsrLiveSessionRepository`
- `JsonFileAsrLiveSessionRepository`

The repository API is intentionally narrow:

- `create(record) -> record`
- `get(session_id) -> record`
- `delete(session_id) -> bool`

`create_app()` will create the ASR live repository alongside the existing session repository. If `data_dir` is provided, the repository writes under `data_dir/live_asr_sessions/{session_id}.json`; otherwise it uses memory.

The existing `/live/asr/sessions/{id}/events` and `.sse` endpoints read through this repository instead of a local dict. The existing `DELETE /sessions/{id}` endpoint also calls the ASR live repository delete path.

## Test Plan

Follow TDD:

1. Add a failing test proving Live ASR JSON/SSE can be read from a new app instance with the same `data_dir`.
2. Add a failing test proving `DELETE /sessions/{id}` removes the Live ASR audit record and follow-up reads return 404.
3. Add a failing test proving unsafe ASR live `session_id` is rejected when JSON persistence is enabled.
4. Implement the repository and app integration.
5. Run focused backend tests.
6. Run PC Web and all-local quality gates.

