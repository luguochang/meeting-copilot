# PCWEB-091 Tauri No-op Shell Local Run Smoke Design

> Date: 2026-07-02  
> Scope: Meeting Copilot desktop path after PCWEB-090.  
> Status: Approved by standing user instruction to continue autonomously with the best recommendation.  
> Boundary: This design does not authorize running Cargo, Tauri, package managers, audio capture, ASR workers, provider config reads, secrets, or remote providers.

## Context

PCWEB-082 created a static Tauri v2 scaffold under `code/desktop_tauri/src-tauri`. PCWEB-083 through PCWEB-090 then added no-command build, Rust, probe, result-intake, and first-cargo-check execution boundaries. PCWEB-090 can produce a manual `cargo check` packet, but it still keeps all run/fetch/artifact flags false.

The next useful desktop step is PCWEB-091: define the local Tauri no-op shell run smoke boundary. This must move the project closer to a real desktop client without violating the current no-command constraint.

## Recommended Approach

Use the existing desktop policy/report pattern:

```text
JSON policy
  -> Python report tool
  -> pytest contract tests
  -> docs and traceability updates
```

PCWEB-091 is a readiness/report boundary, not an execution step. It validates that the no-op shell is structurally ready for a future local smoke run:

- Tauri config still points to `http://127.0.0.1:8765/`.
- `frontendDist` still resolves to the existing Web MVP static assets.
- Bundle remains inactive.
- Capability remains minimal.
- The no-op command catalog remains exactly `runtime_get_status`, `session_prepare`, `asr_worker_health`.
- No generated artifacts such as `Cargo.lock`, `target`, `node_modules`, installer, signing, or notarization files exist in `code/desktop_tauri`.
- PCWEB-090 still exists and remains a no-command first-cargo-check boundary.

## Rejected Approaches

### Run `cargo tauri dev` now

Rejected for this increment. It would execute Cargo/Tauri and may fetch dependencies or create artifacts before the project has an explicit run approval and artifact cleanup path.

### Extend ASR or LLM dry-runs instead

Rejected as the mainline. The project already has enough fixture/synthetic dry-run surface. PCWEB-091 should advance the desktop client path.

### Add audio commands to the shell

Rejected for this increment. Audio capture belongs to later Mac adapter work after the no-op shell and IPC boundary are proven.

## Report Contract

The report should expose:

- `pcweb_id=PCWEB-091`
- `policy_name=Desktop Tauri No-op Shell Local Run Smoke`
- `policy_status=tauri_noop_shell_local_run_smoke_policy_only`
- `smoke_boundary_mode=readiness_report_only`
- `accepted_desktop_scaffold_source=pcweb_082_tauri_shell_scaffold`
- `accepted_cargo_check_boundary_source=pcweb_090_first_cargo_check_execution_boundary`
- `tauri_shell_run_status=not_run`
- `external_command_execution_status=not_run`
- `approval_status=explicit_tauri_run_approval_not_recorded`
- `dev_url=http://127.0.0.1:8765/`
- `frontend_dist=../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static`
- `expected_noop_commands=["runtime_get_status","session_prepare","asr_worker_health"]`
- `expected_bridge_command_ids=["runtime.get_status","session.prepare","asr_worker.health"]`
- `bundle_active=false`
- a future manual smoke packet that stays `not_generated` until all validations pass

Even if validations pass, the report may only return `smoke_packet_status=ready_for_explicit_tauri_run_approval`. It must not execute anything.

## Safety Flags

All safety flags remain false:

- `safe_to_run_tauri_dev_now`
- `safe_to_run_tauri_build_now`
- `safe_to_run_cargo_check_now`
- `safe_to_run_cargo_build_now`
- `safe_to_spawn_process_now`
- `safe_to_fetch_dependencies_now`
- `safe_to_generate_cargo_lock_now`
- `safe_to_generate_target_dir_now`
- `safe_to_generate_installer_now`
- `safe_to_request_audio_permission_now`
- `safe_to_capture_audio_now`
- `safe_to_start_asr_worker_now`
- `safe_to_read_provider_config_now`
- `safe_to_read_secret_now`
- `safe_to_read_configs_local_now`
- `safe_to_call_remote_provider_now`

## Data Flow

```text
PCWEB-091 tool
  reads PCWEB-091 policy
  reads src-tauri/tauri.conf.json
  reads src-tauri/capabilities/default.json
  reads src-tauri/src/lib.rs
  reads PCWEB-090 policy
  validates forbidden generated artifacts under code/desktop_tauri
  emits readiness report
```

No shell command, process spawn, network fetch, provider call, audio read, or secret read is part of the flow.

## Error Handling

The tool must block the readiness packet when:

- policy identity or required fields drift
- Tauri config drifts away from the existing Web MVP URL/static assets
- bundle becomes active
- capability gains non-minimal permissions
- command catalog is missing, extra, or renamed
- PCWEB-090 no-command boundary is missing or relaxed
- generated artifacts are present
- any configured input path points at forbidden local/sensitive roots

Forbidden roots:

- `configs/local`
- `data/local_runtime`
- `outputs`
- `artifacts/tmp`
- `data/asr_eval/samples`

## Tests

The first tests must be RED before implementation:

- policy file existence and false safety flags
- source text has no command execution entrypoints
- report blocks when PCWEB-090 validation is not available or drifted
- valid static scaffold yields `ready_for_explicit_tauri_run_approval` but still `not_run`
- Tauri config drift blocks the packet
- capability drift blocks the packet
- extra or missing no-op command blocks the packet
- generated artifact presence blocks the packet
- forbidden paths are blocked before file reads

## Done Criteria

PCWEB-091 is done only when:

- RED was observed for the new test file before implementation.
- The policy JSON and report tool exist.
- Focused PCWEB-091 tests pass.
- Adjacent desktop boundary tests pass.
- Docs gate passes.
- `pc-web` quality gate passes.
- `all-local --no-browser` quality gate passes.
- Docs and traceability are updated.
- No Cargo/Tauri/package-manager command was executed as part of PCWEB-091.
