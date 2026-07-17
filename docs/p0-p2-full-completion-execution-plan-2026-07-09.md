# P0-P2 Full Completion Execution Plan

> 日期：2026-07-17 更新
> 状态：active execution plan / current implementation audited
> 目标：完成所有已经定好的 P0-P2 内容，不再把 no-op、计划或边界证明误称为生产完成。
> 当前结论：P0/P1 Web 主链路和 P2 桌面实现已大幅完成；P2 的真实 packaged React 页面完整会议操作仍待解锁 Mac 后执行，不能把 IPC prepare 或 backend smoke 拼接成完整 UI E2E。

## 1. 当前基线

已确认：

- P0/P1 Web MVP 主链路已有 Go 证据。
- Release summary 可生成 Go，但曾复用旧 P0 lane manifests。
- P2-1 当前只是 Mac dev shell + no-op IPC evidence。
- P2-2 当前只是 Windows compatibility plan。
- `docs/privacy-retention-and-delete-policy.md` 曾被多处引用但文件缺失，本轮已恢复。
- 桌面 worker protocol / command runner binding policy 曾缺失，本轮已恢复，focused gate 已过。

当前不可宣称：

```text
Production packaged desktop MVP complete
Mac native mic capture complete
ASR worker spawned by Tauri
real PCM audio chunk capture complete
.app/.dmg signed/notarized/install-smoked
Windows implementation complete
```

## 2. 完成定义

本目标完成时，必须同时满足：

### P0 Completion

- Browser live mic lane Go。
- Real-time auto suggestions Go。
- ASR semantic quality gate Go。
- Workbench productized UI Go。
- Release acceptance runner Go。
- Browser live mic evidence 必须带 semantic quality 字段，不能只用旧 `not_evaluated` bundle。

### P1 Completion

- Recording import/export Go。
- Provider health/cost policy Go。
- Privacy/retention/delete policy 文件存在，且 delete scope/evidence sanitizer 测试通过。
- Long meeting gate Go。
- 如果长会议仍是 synthetic gate，报告必须继续明确不等于真实 20-60 分钟生产 soak。

### P2 Completion

Mac-first desktop must move beyond no-op boundary:

- Tauri fresh WebView run evidence on current machine.
- `mic_adapter.start` no longer returns pure `noop_only`; it must expose a real executable dev path or a verified bridge to the existing browser/Web realtime path.
- Explicit user-start boundary remains true.
- Permission denied/granted behavior is documented and test-covered as much as can be automated locally.
- ASR worker lifecycle has executable local boundary: prepare/start/health/collect_events/stop/cleanup.
- Audio chunk lifecycle has executable local boundary: write/read/delete under ignored runtime root.
- Tauri/WebView can open the same Workbench and use the same P0 realtime suggestions/minutes/history/delete chain.
- `.app/.dmg` build command is reproducible with controlled artifact roots, or an explicit blocker is recorded with evidence.

Windows P2 remains compatibility implementation planning unless the goal is expanded to Windows machine execution. It must stay clearly labeled as plan-only.

### 2.1 2026-07-17 current audit override

本节优先于本文件早期的历史基线和旧命令输出：

- 原来写作“no-op IPC”的 P2 代码已经被替换为真实 native mic supervisor、Keychain/Credential Manager provider 配置、bundled backend/FunASR runtime、随机 loopback token 和 exact-port Tauri capability。
- `build.rs` 已生成 19 个 application command permissions；新的 packaged React WebView 已真实调用 `runtime_get_status`、`provider_config_status`、`mic_adapter_prepare`，证据为 `artifacts/tmp/packaged_tauri_ipc_smoke/phase3-packaged-tauri-ipc-20260717-r1/evidence.json`。
- 新 `.app` 的 local AI backend mainline 已通过 FunASR final、流式建议、correction、recording、end、minutes/approach/index，证据为 `artifacts/tmp/packaged_ai_mainline_smoke/phase3-packaged-ai-mainline-20260717-r2/evidence.json`。
- 当前剩余的 W5 不是“Rust command 不存在”，而是“用户在真实 packaged 页面显式开始录音后，同一场会议的 UI 文字、建议、录音、结束和复盘”尚未取得一次性证据。2026-07-17 尝试时 macOS 处于锁屏，自动化接口返回 `Mac is locked`；没有绕过锁屏。
- P2 的签名、公证、Gatekeeper、Windows 真机和模型/FFmpeg 再分发授权仍是发布门禁，不因本地 `.app` 能构建而自动通过。

## 3. Required Work Items

### W1. Evidence Drift Fix

- [x] Restore `docs/privacy-retention-and-delete-policy.md`.
- [x] Restore missing desktop worker policy files:
  - `code/desktop_tauri/asr-worker-command-protocol.policy.json`
  - `code/desktop_tauri/asr-worker-command-runner-binding.policy.json`
- [x] Run P1/P2 focused regression after all changes. Current focused result: backend provider/LLM/correction/streaming `81 passed`, frontend `49 passed`, Rust normal `28 passed` plus Keychain integration `1 passed`, Tauri/smoke `23 passed`.

### W2. P2 Mac Desktop Executable Dev Path

- [x] Add tests proving `mic_adapter.start/stop/delete_audio_chunks` are no longer pure no-op for the new executable dev path.
- [x] Add a desktop runtime module that creates session-scoped audio chunk runtime state only under:

```text
artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/<session_id>/
```

- [x] Add delete logic that removes only the approved session chunk directory.
- [x] Add safety guards for unsafe session IDs and forbidden roots.
- [x] Keep remote ASR and secret/config reads disabled.

Current W2 evidence:

```text
tests/test_desktop_audio_chunk_runtime.py:
4 passed, 1 warning

desktop focused regression:
37 passed, 1 warning

controlled cargo check:
Finished `dev` profile target(s) successfully
```

W2 boundary:

- `mic_adapter.start/stop/delete_audio_chunks` now delegate to `desktop_audio_chunk_runtime` instead of returning pure `NoopBridgeResponse`.
- The runtime creates an approved repo-root session directory and `manifest.json`, and can delete that session directory.
- Runtime paths are resolved from `CARGO_MANIFEST_DIR` back to the repository root, so tests and Tauri execution do not write `code/desktop_tauri/src-tauri/artifacts`.
- This does not yet capture real PCM audio from macOS microphone.
- This does not yet spawn or manage the ASR worker.
- This does not yet prove a Tauri fresh WebView run on current code.

### W3. ASR Worker Lifecycle

- [x] Add tests for worker prepare/start/health/collect_events/stop/cleanup.
- [x] Implement a local executable worker lifecycle boundary for approved synthetic event output.
- [x] Keep the default provider local; remote ASR remains disabled.
- [x] Record lifecycle artifacts under ignored roots only.

Current W3 evidence:

```text
tests/test_desktop_asr_worker_lifecycle_runtime.py:
4 passed, 1 warning

desktop focused regression with W2 + W3:
41 passed, 1 warning

Rust lifecycle behavior tests:
3 passed

controlled cargo check:
Finished `dev` profile target(s) successfully
```

W3 boundary:

- `worker.prepare/start/health/collect_events/stop/cleanup` are now bound through Tauri command handlers.
- The executable local lifecycle writes state under repo-root `artifacts/tmp/desktop_asr_worker_runtime/<session_id>/`.
- `worker.start` writes an approved synthetic streaming ASR event file under `artifacts/tmp/asr_events/`.
- Rust unit tests prove `worker.start` is blocked until `prepare`, and `collect_events` is blocked until `running` or `stopped`.
- Runtime paths are resolved from `CARGO_MANIFEST_DIR` back to the repository root, and `code/desktop_tauri/src-tauri/artifacts` is not generated.
- PCWEB-099/PCWEB-103 no-execution policy/report tests remain as legacy safety guards and were not weakened.
- This does not yet prove production real-mic ASR evidence.
- This does not yet prove Web handoff/Workbench same-chain evidence; that remains W5.
- This does not spawn an external process, read user audio, call remote ASR, call LLM, download models, or read secrets/configs.

### W4. Tauri Fresh Run

- [x] Run controlled `cargo check` with:

```text
CARGO_HOME=artifacts/tmp/rust_toolchain/cargo
RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup
CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target
```

- [x] Start or verify Web backend on `127.0.0.1:8765`.
- [ ] Run Tauri dev shell current-machine WebView check.
- [x] Capture blocker/current-run evidence under `artifacts/tmp/desktop_tauri_current_run/`.

Current W4 evidence:

```text
Backend health:
GET http://127.0.0.1:8765/health -> {"status":"ok","service":"meeting-copilot-web-mvp"}

Workbench:
GET http://127.0.0.1:8765/workbench -> HTML served

Controlled Rust/Tauri compile:
cargo check passed

Tauri dev CLI:
cargo tauri --version -> error: no such command: `tauri`

Evidence:
artifacts/tmp/desktop_tauri_current_run/w4-current-20260709/evidence.json
```

W4 boundary:

- Current backend and Workbench are reachable.
- Current Rust/Tauri crate compiles.
- A true Tauri dev WebView run is still not complete because the controlled toolchain does not include `cargo-tauri`.
- This is a documented blocker, not production desktop completion.

补充：当前受控工具链仍没有 `cargo-tauri` dev subcommand，但已经用新的 packaged `.app` 完成真实 WebView IPC smoke。该补充关闭了远程 origin ACL 和 command binding 阻塞，不等同于 `cargo tauri dev` 证据。

### W5. Same Chain Desktop Evidence

- [x] Verify the W3 desktop worker event output reaches the existing Web ASR live session handoff path as non-production evidence:

```text
desktop worker synthetic event file
-> POST /live/asr/local-event-files/sessions
-> GET /live/asr/sessions/{session_id}/events
-> production LLM endpoint blocks with local_event_file_not_real_input
-> DELETE session verified
```

- [ ] Verify the full desktop path reaches the same Workbench UI and backend APIs:

```text
mic/dev audio input -> realtime ASR -> auto suggestions -> cards -> approach -> minutes -> history -> delete
```

- [x] The W5 handoff evidence does not claim more than it proves.
- [ ] If true native mic permission cannot be automated, mark only that part manual and keep the goal active until a real route is implemented or verified.

Current W5 evidence:

```text
artifacts/tmp/desktop_tauri_current_run/w5-desktop-worker-handoff-20260709b/summary.json

handoff_status=go_non_production_handoff
create_http_status=201
ingest_mode=local_asr_event_file
acceptance_eligible=false
acceptance_blockers=["local_event_file_not_real_input","input_source_not_acceptance_lane"]
production_llm_http_status=409
production_llm_blocked=true
delete_verified=true
counts_as_production_real_mic_evidence=false
counts_as_desktop_worker_web_handoff_evidence=true
```

W5 boundary:

- This proves worker ASR event output can enter the existing Web ASR live session path.
- It also proves production LLM derivation remains correctly blocked for local-event-file input.
- It does not prove real desktop microphone capture.
- It does not prove realtime suggestions/cards/minutes from a native desktop mic route.
- The remaining W5 gap is the full desktop realtime chain:
  `desktop mic/dev audio input -> realtime ASR -> auto suggestions -> cards -> approach -> minutes -> history -> delete`.

2026-07-17 当前状态：`partial_packaged_ipc_go_ui_mainline_pending_unlock`。后端和录音链路已经有 packaged local smoke，实际页面安全 IPC 已通过，但锁屏使本轮无法点击“开始会议”并触发系统麦克风授权；该项保持未完成。

2026-07-17 DEC-406 follow-up：已在 `artifacts/tmp/controlled_rust_toolchain` 安装隔离 Rust `1.97.1` 与 `cargo-tauri 2.11.4`，并用当前提交重新生成 runtime bundle 和 `.app`。当前 packaged IPC、supervisor 和 local AI mainline 均有当前候选证据；W5 剩余范围收敛为真实打包页面点击后的 native microphone/UI 同场验证，不再把缺失 cargo-tauri 当作当前 blocker。

### W6. Release Refresh

- [x] Refresh browser live mic or equivalent current evidence so semantic quality fields are present.
- [x] Re-run release acceptance.
- [x] Write final P0-P2 completion report with Go/No-Go per item. See `docs/p0-p2-completion-report-2026-07-17.md`.

Current W6 evidence:

```text
tests/test_mainline_evidence_bundle_runner.py + tests/test_release_acceptance_runner.py:
25 passed, 2 warnings

release acceptance:
artifact_root=artifacts/tmp/release_acceptance/release-current-20260709-browser-mic-gate-and-ui-path-fixed
verdict=go
blockers=[]
file_lane=go
simulated_realtime=go
browser_live_mic=go
real_mic_recorded_realtime=no_go optional auxiliary lane
```

W6 boundary:

- Browser live mic is now the formal required real microphone release lane.
- Recorded real-mic WAV remains optional/auxiliary and does not block release when browser live mic is GO.
- This does not complete P2 packaged desktop release requirements.

### W7. Mac DMG Development Packaging

- [x] Produce a local development DMG without Finder AppleScript automation.
- [x] Mount smoke verifies the DMG contains `Meeting Copilot.app` and an `Applications` symlink.
- [x] Verify the mounted app's local ad-hoc bundle signature.
- [ ] Developer ID sign the app/DMG.
- [ ] Notarize and staple the DMG.
- [ ] Gatekeeper acceptance.

Current W7 evidence:

```text
tool=tools/package_macos_dmg_skip_finder.py
tests/test_package_macos_dmg_skip_finder.py=3 passed, 1 warning
artifact=artifacts/tmp/desktop_dmg_skip_finder_tool_20260709/evidence.json
dmg=artifacts/tmp/desktop_dmg_skip_finder_tool_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg
dmg_sha256=9610afbd51c7c98e5887e9c354642b590381270011aacb3a4652e55af3794d94
dmg_mount_smoke_exit_code=0
mounted_app_codesign_verify_exit_code=0
spctl_app_exit_code=3
spctl_dmg_exit_code=3
```

2026-07-09 refreshed development DMG after packaged WebView fixes:

```text
tests/test_package_macos_dmg_skip_finder.py=4 passed, 1 warning
artifact=artifacts/tmp/desktop_dmg_skip_finder_current_20260709/evidence.json
dmg=artifacts/tmp/desktop_dmg_skip_finder_current_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg
dmg_sha256=f5e047cdf7f1cdd13d9777bccc0ac39e648b549c40b1eede539bf74929dac435
dmg_create_exit_code=0
dmg_mount_smoke_exit_code=0
mounted_app_codesign_verify_exit_code=0
spctl_app_exit_code=3
spctl_dmg_exit_code=3
remaining_blockers=[
  developer_id_codesign_not_done,
  notarization_not_done,
  gatekeeper_rejects_unsigned_or_adhoc_artifacts,
  packaged_screenshot_evidence_still_missing
]
```

Note: signing, notarization, Gatekeeper, screenshot, and Windows boundaries remain.

W7 boundary:

- This is a development/validation DMG, not a public release package.
- Tauri's default DMG target still invokes Finder AppleScript; the committed workaround path is `tools/package_macos_dmg_skip_finder.py`.
- Public distribution remains blocked by Developer ID signing and notarization.

### W8. Packaged App Window Evidence

- [x] Re-run packaged `.app` after local ad-hoc signing.
- [x] Verify process and CoreGraphics window are observable.
- [x] Verify local ad-hoc app bundle signature integrity.
- [x] Capture DOM/runtime evidence from packaged WebView through Tauri-side probe.
- [ ] Capture screenshot evidence from packaged WebView.
- [x] Run packaged app same-chain meeting flow inside the packaged app with no-cost controlled derivation.

Current W8 evidence:

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-app-window-signed-dmg-20260709/evidence.json
process_observed=true
coregraphics_window_observed=true
app_codesign_verify_exit_code=0
spctl_app_exit_code=3
screenshot_status=blocked_by_macos_screen_capture_permission_or_window_capture_policy
counts_as_packaged_window_evidence=true
counts_as_packaged_dom_evidence=false
```

W8 boundary:

- Screen capture remains blocked by macOS permissions or capture policy in the current environment.
- System-level window evidence alone is not enough to claim packaged DOM/mainline completion.
- A Tauri-side internal runtime probe now proves packaged Workbench HTML, inline DOM, `workbench.js`, `runtime_get_status`, and API base wiring are live.
- A Tauri-side opt-in self-probe now proves the packaged app can run the same Workbench/backend chain through transcript, suggestion cards, approach cards, minutes, history, and delete using no-cost controlled demo derivation.

2026-07-09 packaged WebView runtime probe update:

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-webview-runtime-probe-20260709-backend-api/evidence.json
status=go_packaged_webview_runtime_probe
counts_as_packaged_runtime_probe_evidence=true
counts_as_packaged_dom_evidence=true
counts_as_packaged_backend_api_evidence=true
counts_as_packaged_screenshot_evidence=false
counts_as_packaged_mainline_evidence=false
packaged_workbench_loaded=true
packaged_inline_dom_selectors_present=true
packaged_workbench_runtime_connected=true
packaged_backend_api_connected=true
desktop_api_base_url=http://127.0.0.1:8765
desktop_status_text=桌面壳已连接
privacy_cost_flags.captures_audio=false
privacy_cost_flags.calls_remote_provider=false
```

Implementation notes:

- Fixed `desktop_frontend_probe_runtime::repo_root()` so packaged probes write under repo-root `artifacts/tmp/desktop_frontend_probe_runtime`, not `code/artifacts/tmp/...`.
- Split frontend probe artifacts into `latest-page-load.json`, `latest-inline-dom.json`, and `latest-workbench-runtime.json` so Rust page-load and JS runtime probes no longer overwrite each other.
- Fixed packaged Workbench script loading: Tauri uses relative `workbench.js`, Web service mode uses `/static/workbench.js`.
- Fixed Workbench bootstrap order so `initDesktopRuntimeProbe()` sets `apiBaseUrl` before `/audio/check` and session history calls.

Verification:

```text
Backend CORS for packaged Tauri origin:
OPTIONS /health with Origin tauri://localhost -> access-control-allow-origin: tauri://localhost

Packaged backend API probe:
latest-backend-api.health_ok=true
latest-backend-api.sessions_loaded=true
latest-backend-api.errors=[]

tests/test_packaged_frontend_probe_evidence.py + tests/test_workbench_desktop_runtime_probe.py + tests/test_desktop_tauri_scaffold.py:
16 passed, 1 warning

code/web_mvp/backend/tests/test_workbench.py + tests/test_workbench_desktop_runtime_probe.py + tests/test_desktop_tauri_scaffold.py:
75 passed, 2 warnings

focused no-cost regression:
187 passed, 2 warnings

cargo test desktop_frontend_probe_runtime:
3 passed

cargo check:
Finished `dev` profile successfully

cargo tauri build --bundles app:
Finished 1 bundle at artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app

codesign --verify --deep --strict:
valid on disk; satisfies its Designated Requirement
```

2026-07-09 packaged same-chain no-cost self-probe update:

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-same-chain-no-cost-20260709/evidence.json
status=go_packaged_webview_runtime_probe
blockers=[]
counts_as_packaged_runtime_probe_evidence=true
counts_as_packaged_dom_evidence=true
counts_as_packaged_backend_api_evidence=true
counts_as_packaged_same_chain_no_cost_evidence=true
counts_as_packaged_mainline_evidence=true
counts_as_production_real_llm_evidence=false
counts_as_production_real_mic_evidence=false
packaged_same_chain_flow_complete=true
packaged_same_chain_session_id=packaged_probe_mrd3b9gk
packaged_same_chain_suggestion_card_count=3
packaged_same_chain_approach_card_count=1
same_chain_blockers=[]
remaining_blockers=[
  developer_id_signing_not_done,
  notarization_not_done,
  gatekeeper_acceptance_not_done,
  windows_real_machine_not_verified
]
```

Implementation notes:

- Added demo-only `deterministic_demo` derivation mode for `/live/asr/demo/sessions/{id}/...`.
- Production `/live/asr/sessions/{id}/...` endpoints reject `deterministic_demo`.
- Packaged Workbench self-probe only runs when `MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE=1` makes Tauri `runtime_get_status.packaged_same_chain_probe_enabled=true`.
- Self-probe creates a mock ASR session, renders transcript in packaged WebView, generates deterministic no-cost suggestion cards / approach cards / minutes, verifies history, deletes the session, verifies history removal, and writes `latest-same-chain.json`.
- Backend self-test was started with `PYTHON_DOTENV_DISABLED=1` and without LLM gateway env vars; no remote LLM/ASR or paid provider was called.

Boundary:

- This removes `packaged_same_chain_realtime_meeting_flow_not_verified` from the current P2 remaining blockers.
- This is not production real LLM evidence.
- This is not production real microphone evidence.
- Screenshot evidence remains blocked by macOS capture permission/policy.
- Public release remains blocked by Developer ID signing, notarization, Gatekeeper, and Windows real-machine verification.

### W9. Release External Preflight

- [x] Add a machine-readable public release preflight for external blockers.
- [x] Verify packaged same-chain evidence remains accepted.
- [x] Verify development DMG is not counted as public release package.
- [x] Verify the tool does not read secrets, submit notarization, or call remote services.
- [ ] Developer ID signing available and applied.
- [ ] Notarization profile configured and notarization/staple completed.
- [ ] Gatekeeper acceptance passes.
- [ ] Windows real-machine verification evidence exists.
- [x] Packaged visual/screenshot evidence is explicitly waived in favor of internal DOM/runtime/same-chain probe for release preflight.

Current W9 evidence:

```text
tool=tools/macos_release_external_preflight.py
tests/test_macos_release_external_preflight.py=5 passed, 1 warning
artifact=artifacts/tmp/release_external_preflight_20260709/evidence.json
status=blocked_external_release_requirements
counts_as_packaged_same_chain_no_cost_evidence=true
counts_as_public_release_package=false
macos_public_release.ready=false
notarization.tooling_ready=true
notarization.notarization_completed=false
developer_id_application_count=0
developer_id_installer_count=0
notarytool_available=true
notary_profile_provided=true
spctl_app_exit_code=3
spctl_dmg_exit_code=3
windows_real_machine_verified=false
remaining_blockers=[
  developer_id_signing_not_done,
  notarization_not_done,
  gatekeeper_acceptance_not_done,
  windows_real_machine_not_verified
]
visual_evidence.packaged_screenshot_requirement_status=waived_by_internal_dom_runtime_probe
```

W9 boundary:

- This preflight freezes the current external release blockers as evidence; it does not complete them.
- It does not read API keys, `.env`, `configs/local/`, keychain passwords, or notarization credentials.
- It does not submit notarization and does not call Apple remote services.
- It confirms the development DMG remains a validation artifact, not a public release package.
- Packaged screenshot is tracked as a visual QA gap waived by internal DOM/runtime/same-chain evidence, not as a public release external blocker.
- `notarization.tooling_ready=true` only means `notarytool` and profile are available; `notarization.notarization_completed=false` remains until Mac public release runner is GO.

### W10. Mac Public Release Runner

- [x] Add an executable runner for Developer ID signing, notarization, stapling, and Gatekeeper checks.
- [x] Verify missing Developer ID stops before any mutating command.
- [x] Verify ready-path command order by TDD using a fake command runner.
- [x] Run the runner on the current machine and write blocked evidence.
- [ ] Install/configure Developer ID Application identity.
- [ ] Re-run runner to produce signed/notarized/stapled DMG.
- [ ] Gatekeeper app and DMG acceptance passes.

Current W10 evidence:

```text
tool=tools/macos_public_release_runner.py
tests/test_macos_public_release_runner.py=3 passed, 1 warning
artifact=artifacts/tmp/macos_public_release_20260709/evidence.json
status=blocked_public_release_execution_requirements
remaining_blockers=[
  developer_id_signing_not_done
]
developer_id_application_count=0
notarytool_available=true
notary_profile_provided=true
counts_as_public_release_package=false
executed_mutating_command_count=0
notarization_submitted=false
remote_service_called=false
```

W10 boundary:

- This proves the release execution path exists and is guarded.
- It does not sign, notarize, staple, or pass Gatekeeper on the current machine because no Developer ID Application identity is available.
- It does not read secret values or keychain passwords.

### W11. Windows Real-Machine Verification Intake

- [x] Add a validator for caller-provided Windows real-machine observation.
- [x] Require validator GO evidence before external preflight clears Windows blocker.
- [x] Reject bare `windows_real_machine_verified=true` JSON that does not come from the validator schema.
- [x] Generate current blocked Windows evidence on this Mac.
- [ ] Run Tauri/Workbench/audio/import/delete/installer smoke on a real Windows machine.
- [ ] Feed that observation into the validator and refresh external preflight.

Current W11 evidence:

```text
tool=tools/windows_real_machine_verification.py
tests/test_windows_real_machine_verification.py=3 passed, 1 warning
artifact=artifacts/tmp/windows_real_machine_verification_20260709/evidence.json
status=blocked_windows_real_machine_verification
windows_real_machine_verified=false
remaining_blockers=[
  windows_real_machine_not_observed,
  windows_host_os_not_observed,
  windows_tauri_webview_not_verified,
  windows_backend_health_not_verified,
  windows_workbench_not_verified,
  windows_provider_health_not_verified,
  windows_microphone_permission_path_not_verified,
  windows_realtime_mainline_not_verified,
  windows_file_import_export_not_verified,
  windows_installer_or_portable_launch_not_verified,
  windows_delete_not_verified,
  windows_secret_redaction_not_verified
]
```

W11 boundary:

- This is an intake/validator, not a Windows run.
- It does not execute Windows commands, access WASAPI, read user audio, or fake Windows success.
- It makes the remaining Windows blocker auditable and harder to accidentally waive.

## 4. Stop Rules

Do not count these as progress toward full P0-P2 completion:

- More readiness/preflight wrappers without executable behavior.
- Reusing old browser bundles to satisfy new semantic-quality evidence.
- Calling no-op IPC a completed desktop product.
- Claiming Windows implemented when only compatibility plan exists.
- Running paid remote ASR by default.
- Reading `configs/local/`, `.env`, secrets, or unauthorized private audio.

## 5. Current Verification Snapshot

Already run in this execution:

```text
P2 desktop focused gate before policy restore:
17 failed / 16 passed
root cause: missing PCWEB-099 and PCWEB-103 policy JSON

P2 desktop focused gate after policy restore:
33 passed, 1 warning

Workbench focused gate:
71 passed, 2 warnings

W2 audio chunk runtime focused gate:
4 passed, 1 warning

Desktop focused regression after W2 runtime transition:
37 passed, 1 warning

Controlled Rust/Tauri compile check after W2 runtime transition:
cargo check passed with controlled CARGO_HOME/RUSTUP_HOME/CARGO_TARGET_DIR

W3 ASR worker lifecycle runtime focused gate:
4 passed, 1 warning

W3 Rust lifecycle behavior test:
3 passed

Desktop focused regression after W3 lifecycle transition:
41 passed, 1 warning

Controlled Rust/Tauri compile check after W3 lifecycle transition:
cargo check passed with controlled CARGO_HOME/RUSTUP_HOME/CARGO_TARGET_DIR

W4 backend/Workbench current check:
backend health ok; Workbench HTML served

W4 Tauri fresh WebView:
blocked_missing_cargo_tauri_cli

W5 desktop worker to Web ASR handoff:
go_non_production_handoff; production LLM correctly blocked by local_event_file_not_real_input
```

## 6. Next Immediate Step

Continue P2 release hardening and external blocker closure:

1. Provide or configure Apple Developer ID signing material if public Mac distribution must be completed in this goal; without Developer ID credentials this remains an external blocker recorded in `artifacts/tmp/release_external_preflight_20260709/evidence.json`.
2. Keep v1 mic input route as WebView/browser `getUserMedia`; Rust native PCM mic capture remains P2+/P3 unless explicitly promoted.
3. If Developer ID material becomes available, rerun app/DMG signing, notarization, staple, Gatekeeper acceptance, and the release external preflight.
4. Keep Windows as plan-only until a Windows machine is available for real Tauri/WebView/audio/installer smoke.
5. Do not refresh paid release acceptance unless explicit budget is approved.

## 7. 2026-07-17 V2 文件导入主线决策

审计发现旧版 `/live/asr/transcribe-file/sessions` 没有接入 V2 React 首页、V2 history 或 canonical transcript，因此早期 P1-1 勾选不能视为 V2 产品功能完成。本轮不继续做横向 ASR 评测，直接补齐真实业务闭环：

```text
V2 首页导入录音
-> 本地 FunASR batch
-> 原始音频 + 标准化 WAV 本地保存
-> V2 canonical transcript / audio persistence
-> durable correction + suggestion + minutes + approach + index
-> V2 review / history / audio playback
```

实现约束：

- 不新增远程 ASR 费用，默认使用本地 FunASR；LLM 仍只在用户配置的 OpenAI-compatible Provider 下执行。
- 阻塞性文件转写在线程池执行，不能占用 async event loop。
- 导入结果必须落 V2 persistence，不能只返回 legacy session ID。
- 失败时执行 deletion fence，不能留下只存在文件系统或只存在旧仓库的半套会话。
- 导入是会后文件链路，不替代实时 microphone/partial 目标。
- 当前 batch wrapper 仍以 whole-file segment 兼容；timestamped segments 作为后续明确 backlog，不在本轮扩展为新的评测循环。

详细接口与证据见 `docs/v2-import-audio-contract-2026-07-17.md`。
