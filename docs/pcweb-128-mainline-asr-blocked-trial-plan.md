# PCWEB-128 Mainline ASR-Blocked Trial Plan

> Date: 2026-07-04  
> Status: Implemented  
> Boundary: no microphone, no user audio, no `.m4a`, no `configs/local`, no remote ASR, no remote LLM, no model download, no public audio download, no extra provider cost.

## Purpose

DEC-201 proved the main ASR chain runs but FunASR hotword candidates do not pass quality exit. The product should not stall in repeated ASR evaluation, and the PC workbench should make the current truth visible:

```text
product flow can run on synthetic/live events
ASR quality is still blocked
real microphone is still blocked
```

PCWEB-128 adds a workbench entry for that state.

## Implementation

Backend:

- `POST /desktop/mainline-asr-blocked-trial/sessions`
- Creates a local Live ASR session using existing synthetic long technical meeting events.
- Stores it in the existing Live ASR session repository.
- Returns DEC-201 quality metadata:
  - `asr_quality_exit_status=not_exited`
  - `asr_quality_decision_status=blocked_by_funasr_smoke_assembly_input_guard`
  - `recommended_next_action=continue_pc_product_flow_keep_real_mic_blocked`
  - blocked candidates `chunk10_hotword` and `chunk20_hotword`
  - failed FunASR product replay scenario `incident-review-001`

Frontend:

- Toolbar button: `主线试运行`
- Function: `loadMainlineAsrBlockedTrial`
- Renderer: `renderMainlineAsrBlockedTrial`
- Reuses existing Live ASR SSE mode and the existing Shadow MVP panel.

## Verification

Focused backend/API and static asset test:

```text
2 passed, 2 warnings
```

Browser E2E:

```text
node code/web_mvp/e2e/browser_smoke.mjs
status=ok
checked includes "mainline ASR blocked trial"
```

## Decision

PCWEB-128 advances PC product flow visibility. It does not unlock ASR quality, real microphone capture, or real meeting validation. The next useful product work should build on this PC workbench mainline state rather than restart open-ended ASR provider evaluation.
