# PCWEB-104 Desktop ASR Worker Command Runner Implementation Skeleton Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an inert Rust command runner implementation skeleton for the future desktop ASR worker, while preserving the no-dispatch/no-execution boundary.

**Architecture:** PCWEB-104 follows PCWEB-103. The Python policy/report tool validates a static Rust skeleton file, a policy JSON, and a caller-provided skeleton request, then returns a blocked command preview. The Rust file is intentionally not imported by `lib.rs` and not exposed through Tauri IPC.

**Tech Stack:** Python pytest/tooling, JSON policy files, Tauri Rust source tree, Web readiness endpoint tests.

---

## Scope

PCWEB-104 is a skeleton-only milestone. It creates and validates future implementation source shape, but does not bind, execute, spawn, read, write, capture, download, or call any provider.

Allowed artifacts:

- `code/desktop_tauri/asr-worker-command-runner-implementation-skeleton.policy.json`
- `tools/desktop_asr_worker_command_runner_implementation_skeleton.py`
- `tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py`
- `code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- Web readiness status update in `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Documentation updates in README, desktop README, RTM, current mainline index, and decision log

Forbidden in this milestone:

- Tauri command binding
- `mod asr_worker_command_runner;`
- `generate_handler!` entry
- subprocess or process spawn
- worker command dispatch
- health probe execution
- event file read/write
- microphone, system audio, file audio, or real user recording access
- remote ASR/LLM calls
- FunASR/ModelScope model download
- Cargo/Tauri execution

## Contract

Policy values:

- `pcweb_id=PCWEB-104`
- `required_previous_contracts=["PCWEB-103"]`
- `skeleton_mode=command_runner_implementation_skeleton_only`
- `execution_mode=no_dispatch_no_execution`
- `skeleton_version=desktop_asr_worker_command_runner_implementation_skeleton.v1`
- `runner_implementation_status=skeleton_not_bound`
- `native_command_runner_status=skeleton_file_not_bound`
- `native_command_runner_path=code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- `sidecar_module_path=code/asr_runtime/scripts/asr_worker_sidecar.py`
- `command_transport_preview=stdio_jsonl`
- `command_catalog=["worker.prepare","worker.start","worker.health","worker.collect_events","worker.stop","worker.cleanup"]`
- `approved_event_output_root=artifacts/tmp/asr_events`
- `approved_runtime_root=artifacts/tmp/desktop_asr_worker_runtime`

Provider/source boundaries:

- allowed preview providers: `mock_streaming`, `sherpa_onnx_streaming`
- later approval required: `funasr_streaming`
- forbidden: `remote_asr`, `remote_llm_asr`
- allowed preview source: `synthetic`
- later approval required: `mic`, `file`, `system_audio`

Expected valid report:

- `runner_implementation_status=ready_for_no_dispatch_skeleton_review`
- `ready_for_no_dispatch_skeleton_review=true`
- `future_native_command_runner_skeleton.implementation_status=skeleton_source_validated_not_bound`
- `future_native_command_runner_skeleton.binding_status=not_bound`
- `future_native_command_runner_skeleton.command_dispatch_status=not_dispatched`
- `future_native_command_runner_skeleton.tauri_ipc_status=not_invoked`
- `future_native_command_runner_skeleton.process_spawn_status=not_spawned`
- `future_native_command_runner_skeleton.worker_execution_status=not_executed`
- `blocked_command_preview.accepted=false`
- `blocked_command_preview.safe_to_execute_now=false`

## TDD Steps

- [x] **Step 1: Write failing tests**

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

Expected red result:

```text
8 failed, 2 warnings
```

Reason: policy/tool/Rust skeleton were missing and Web readiness still pointed to PCWEB-103.

- [x] **Step 2: Implement minimal no-dispatch skeleton**

Implementation artifacts:

- Add `code/desktop_tauri/asr-worker-command-runner-implementation-skeleton.policy.json`.
- Add `tools/desktop_asr_worker_command_runner_implementation_skeleton.py`.
- Add `code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`.
- Update Web readiness endpoint to report `next_pcweb_id=PCWEB-104`, `command_runner_implementation_skeleton_status=not_bound_no_dispatch`, `desktop_asr_handoff_safe_to_accept_worker_command=false`, and blockers including `command_runner_implementation_skeleton_not_approved`.

- [x] **Step 3: Verify green focused tests**

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

Green result:

```text
8 passed, 2 warnings
```

- [x] **Step 4: Document decision and traceability**

Documentation targets:

- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`
- `README.md`
- `code/desktop_tauri/README.md`

## Next Boundary

After PCWEB-104, the next execution ticket must choose one of:

- public planned samples no-download manifest
- ASR quality decision, including FunASR local model directory or model approval
- real Tauri no-op run, with explicit Cargo/Tauri approval
- mic adapter contract, still no microphone access until explicit user start semantics and runtime root policy are implemented

PCWEB-104 does not authorize real command dispatch, worker execution, event file IO, audio access, model download, remote calls, or Cargo/Tauri execution.
