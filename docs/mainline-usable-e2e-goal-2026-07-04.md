# Mainline Usable E2E Goal

> Date: 2026-07-04  
> Decision: DEC-207  
> Status: Corrected execution target  
> Scope: PC desktop mainline usability, implementation gaps, repair, and full self-test

## 1. Corrected Goal

The goal is not only to run a self-test.

The goal is to make the current PC mainline as complete and usable as the current technical constraints allow:

```text
audio input readiness
  -> ASR event generation or approved replay
  -> Web Live ASR handoff
  -> transcript / EvidenceSpan
  -> meeting state extraction
  -> suggestion candidates
  -> LLM request draft or explicit no-call blocker
  -> feedback / export preview
  -> final self-test report
  -> gap list with fixes or explicit blockers
```

This means a test failure, missing endpoint, missing runner, missing report field, or broken UI path is not merely recorded. It must be fixed if it is inside the current local/no-paid/no-private-data boundary.

Only issues that require unapproved actions are allowed to remain blockers:

- real microphone capture without explicit start;
- Mac system audio capture before M2 implementation;
- remote ASR/LLM calls without explicit approval;
- private audio, old recordings, `.m4a`, or local secret reads;
- paid provider usage without a separate provider/cost decision;
- ASR production-quality proof that requires clean real meeting audio not yet available.

## 2. Completion Definition

A mainline pass requires all of the following:

1. A single local command can run the mainline self-test and write a JSON/Markdown report under approved ignored artifact roots.
2. M1 audio healthcheck is included in the report, either as a passing approved synthetic/local WAV sample or as a clear blocker.
3. The ASR event source path is exercised through an approved local event file, synthetic event source, or real local ASR artifact. If local ASR quality is insufficient, the report must say so explicitly.
4. Web Live ASR handoff creates a session and exposes transcript, EvidenceSpan, state events, scheduler events, suggestion candidates, and no-call LLM request drafts.
5. Feedback/export closure runs and produces a Markdown/JSON preview plus a clear Go / No-Go / inconclusive decision.
6. Browser smoke verifies that the workbench UI can expose the same mainline path.
7. The final report lists every gap as one of:
   - `fixed_in_this_run`;
   - `implemented_and_verified`;
   - `blocked_requires_explicit_user_approval`;
   - `blocked_requires_m2_system_audio_capture`;
   - `blocked_by_asr_quality`;
   - `deferred_not_required_for_current_mainline`.
8. Regression tests and sensitive scans run before claiming the goal is complete.

## 3. Current Known Mainline State

Already implemented:

- M1 audio capture healthcheck for approved WAV files.
- Web mainline ASR-blocked trial endpoint.
- Live ASR event stream to transcript / EvidenceSpan / state / scheduler / suggestion candidate / LLM request draft.
- Draft review for Live ASR sessions.
- Feedback and export preview closure for the mainline trial.
- Browser smoke path that covers the workbench UI.

Still missing or incomplete:

- One-command mainline self-test runner that combines M1 audio health, Web mainline session, events, draft, closure, browser smoke, and report output.
- M2 Mac system audio digital capture.
- Dual-track mic + system audio capture.
- Production ASR provider decision based on clean capture tracks.
- Real meeting Go evidence from user-approved microphone/system-audio sessions.

Current blocker:

```text
local zero-cost ASR quality is not yet production-sufficient for Chinese technical meetings
```

This blocker must be visible in the final report, but it should not stop us from fixing local product-chain gaps that do not require real audio or paid providers.

## 4. Execution Policy

Default allowed actions:

- run local tests;
- run local Web backend/browser smoke;
- generate synthetic WAV/event artifacts under approved ignored roots;
- run M1 healthcheck on approved local test WAV files;
- create JSON/Markdown self-test reports under approved ignored roots;
- fix local code, tests, schemas, docs, and UI gaps needed for the mainline.

Default blocked actions:

- read `configs/local/**`;
- read `data/asr_eval/local_samples/**`;
- read `data/local_runtime/**`;
- read `outputs/**`;
- read old user recordings or `.m4a`;
- upload audio;
- call remote ASR;
- call remote LLM;
- use paid providers;
- request microphone/system-audio permissions without explicit start.

## 5. Recommended Immediate Implementation

The next concrete implementation should be:

```text
M1.5 Mainline Usable E2E Runner
```

It should create:

```text
tools/mainline_usable_e2e_runner.py
tests/test_mainline_usable_e2e_runner.py
docs/mainline-usable-e2e-selftest-2026-07-04.md
```

The runner should:

- generate or use an approved local WAV fixture for M1 healthcheck;
- run `build_audio_capture_health_report`;
- create a Web mainline ASR-blocked trial session through the app factory or TestClient;
- fetch events and draft review;
- run mainline feedback/export closure;
- collect safety flags, event counts, candidate counts, closure status, and blockers;
- write one JSON report and one Markdown report under `artifacts/tmp/mainline_selftests/`;
- never call remote ASR/LLM or read private paths;
- return a non-zero exit code only for broken local product-chain behavior, not for expected ASR-quality blockers.

After M1.5 passes, proceed to:

```text
M2 Mac system audio digital capture
```

M2 should be the first implementation that moves beyond synthetic/local event proof toward production-grade capture.
