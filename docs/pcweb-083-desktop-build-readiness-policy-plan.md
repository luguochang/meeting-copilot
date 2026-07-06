# PCWEB-083 Desktop Build Readiness Policy Plan

## Goal

PCWEB-083 defines the first build-readiness boundary after the static Tauri scaffold. It allows a no-build readiness report and an optional toolchain version probe, while keeping `cargo check`, Tauri dev/build, dependency installation, lockfile generation, target generation, audio capture, worker spawn, secret reads, and remote calls blocked.

## Position In The PC Path

```text
PCWEB-081
  native bridge command contract boundary

PCWEB-082
  create_tauri_shell_scaffold_against_bridge_contract

PCWEB-083
  desktop build readiness policy and toolchain_version_probe_only report
```

PCWEB-083 moves the desktop path forward without pretending the Tauri scaffold has compiled or run. The value is to make the next step explicit: a future `cargo check` can only be enabled after artifact, lockfile, dependency fetch, cleanup, and no-audio/no-secret/no-remote boundaries are approved.

## Artifacts

PCWEB-083 adds:

- `code/desktop_tauri/build-readiness.policy.json`
- `tools/desktop_build_readiness.py`
- `tests/test_desktop_build_readiness_policy.py`

The policy file records:

- `policy_status=build_readiness_policy_only`
- `toolchain_probe_mode=toolchain_version_probe_only`
- `safe_to_run_cargo_check_now=false`
- `safe_to_run_tauri_dev_now=false`
- `safe_to_run_tauri_build_now=false`
- `safe_to_install_dependencies_now=false`
- `safe_to_generate_lockfiles_now=false`
- `safe_to_generate_build_artifacts_now=false`
- allowed probe commands: `rustc --version` and `cargo --version`
- executable probe allowlist is enforced in `tools/desktop_build_readiness.py`; custom policy files cannot expand execution beyond `rustc --version` and `cargo --version`
- forbidden commands: `cargo check`, `cargo build`, `cargo tauri dev`, `cargo tauri build`, `npm install`, `npm ci`, `pnpm install`, `yarn install`, `npm run tauri dev/build`, `pnpm run tauri dev/build`, `yarn tauri dev/build`, and `npx tauri dev/build`

## Official Source Notes

The policy references official Tauri v2 pages as the source set for future build/toolchain work:

- [Tauri v2 prerequisites](https://v2.tauri.app/start/prerequisites/)
- [Tauri v2 CLI reference](https://v2.tauri.app/reference/cli/)
- [Tauri v2 configuration files](https://v2.tauri.app/develop/configuration-files/)

PCWEB-083 does not rely on those pages to run a build. They are recorded so the future build increment has primary-source anchors before changing dependency or artifact policy.

## Explicit Non-Goals

PCWEB-083 does not:

- Run `cargo check`.
- Run `cargo build`.
- Run `cargo tauri dev` or `cargo tauri build`.
- Run `npm install`, `pnpm install`, `yarn install`, or any frontend package manager.
- Generate `Cargo.lock`, npm/pnpm/yarn lock files, `node_modules`, `target`, `dist`, bundles, installers, signing files, or notarization files.
- Bind audio commands.
- Request permissions.
- Enumerate devices.
- Capture microphone or system audio.
- Spawn ASR workers.
- Read provider config, API keys, keychain, environment secrets, or `configs/local/`.
- Write runtime/session/audio data.
- Call remote ASR, LLM, relay, or paid providers.

## Future Preconditions

`cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` remains blocked until all of these are true:

- `explicit_user_approval_for_build_artifacts`
- `cargo_lock_policy_decided`
- `target_dir_policy_decided`
- `network_dependency_fetch_policy_decided`
- `cache_cleanup_policy_decided`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

## Acceptance

- Root tests prove `build-readiness.policy.json` exists and keeps build/dependency/Tauri CLI execution blocked by default.
- Root tests prove `desktop_build_readiness.py` is static by default and does not run external commands unless `probe_toolchain=True`.
- Root tests prove optional toolchain probing only runs `rustc --version` and `cargo --version`.
- Root tests prove a custom policy cannot expand the executable probe allowlist; blocked commands are reported with `returncode=126` and are not passed to the runner.
- Root tests prove forbidden side effects remain present in both the policy and readiness report.
- Quality gate includes the PCWEB-083 root tests through `root-pytest`.
- Documentation gate records PCWEB-083 in README, Web README, traceability, acceptance, privacy/data-flow, project structure, roadmap, decision log, this plan, and the implementation plan.

## Implementation Status

Status: completed for the build-readiness policy boundary.

Verification:

- RED was confirmed before policy/tool creation through `tests/test_desktop_build_readiness_policy.py`.
- Post-review RED was confirmed for custom policy allowlist expansion and missing forbidden package-manager/Tauri launcher variants; the implementation now enforces a code-level probe allowlist and expanded forbidden command coverage.
- Focused GREEN passed with `python3 -m pytest tests/test_desktop_build_readiness_policy.py tests/test_quality_gate.py -q`.
- Docs gate passed with `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`.
- `python3 tools/run_quality_gate.py --profile pc-web` passed with 20 root tests, 34 core tests, 300 Web backend tests, and browser smoke.
- `python3 tools/run_quality_gate.py --profile all-local --no-browser` passed with ASR runtime, ASR bake-off, root, core, and Web backend tests.
- `python3 tools/desktop_build_readiness.py --probe-toolchain` returned `returncode=127` for `rustc` and `cargo` on this machine, meaning the toolchain is not currently available in PATH; it returned a readiness report without traceback and did not run a build.
- The policy and tool remain no-build by default; the only optional probe mode is `toolchain_version_probe_only`.
