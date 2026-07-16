# Desktop Mac MVP Plan

> 日期：2026-07-08
> 状态：P2-1 completed as Mac dev shell + no-op IPC evidence
> 范围：Meeting Copilot PC 端 Mac-first 桌面形态。
> 结论：当前具备 Mac/Tauri 开发壳、Workbench WebView 配置、macOS 权限文案、历史真实 Tauri no-op WebView IPC evidence 和 focused static tests；不能宣称真实麦克风、真实 ASR worker、签名打包或 notarization 完成。

## 1. 当前架构选择

当前桌面形态选择 Tauri v2：

```text
shared web/backend/core
  -> local Web MVP backend on 127.0.0.1:8765
  -> Tauri WebView shell loads workbench.html
  -> desktop adapter / IPC boundary
  -> future mic adapter and ASR worker execution
```

选择原因：

- 复用当前 Workbench UI 和本地 backend，不重写两套业务代码。
- 平台差异收敛在 desktop adapter / IPC 层。
- Mac-first 能最快在当前用户机器上验证真实会议场景。

## 2. 已具备的 Mac 桌面能力

代码入口：

```text
code/desktop_tauri/
code/desktop_tauri/src-tauri/Cargo.toml
code/desktop_tauri/src-tauri/tauri.conf.json
code/desktop_tauri/src-tauri/src/lib.rs
code/desktop_tauri/src-tauri/Info.plist
```

Tauri 配置：

```text
productName=Meeting Copilot
identifier=com.meetingcopilot.desktop
devUrl=http://127.0.0.1:8765/
frontendDist=../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static
main window url=workbench.html
bundle targets=app,dmg
capabilities=core:default
```

当前 IPC command catalog：

```text
runtime.get_status
session.prepare
asr_worker.health
mic_adapter.prepare
mic_adapter.status
mic_adapter.start
mic_adapter.pause
mic_adapter.resume
mic_adapter.stop
mic_adapter.delete_audio_chunks
```

macOS 权限说明已在 `Info.plist` 中声明：

```text
NSMicrophoneUsageDescription
NSAudioCaptureUsageDescription
```

说明边界：

- 权限说明存在，不等于权限弹窗已触发。
- 权限说明存在，不等于用户已授权。
- 权限说明存在，不等于真实采集已发生。

## 3. 当前不可宣称的能力

不能宣称：

- 真实麦克风采集完成。
- 系统音频采集完成。
- `mic_adapter.start` 已真实录音；当前关键 start/pause/resume/stop/delete audio chunks 仍是 no-op/boundary command。
- ASR worker 已由 Tauri spawn 或管理。
- `worker.prepare(source_kind=mic)` 已批准执行。
- audio chunk 写入、读取、删除生命周期已完成。
- `.app` / `.dmg` 已签名、notarized、可交付安装。
- Mac App Store 或 Developer ID 分发已完成。

可以宣称：

- Tauri v2 Mac dev shell scaffold 存在。
- Workbench WebView 配置存在。
- 10 个 IPC command 已绑定。
- 历史 PCWEB-118 `cargo check` 曾通过。
- 历史 PCWEB-119 真实 Tauri no-op WebView IPC evidence 存在，且 10/10 command returned。
- 当前 P2 focused static tests 通过。

## 4. 开发版运行命令

先启动 Web MVP backend：

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
PYTHONPATH=.:../../core uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port 8765
```

历史受控 cargo check 命令：

```bash
CARGO_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/desktop_tauri_target \
/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

当前本机 `rustup` 默认 toolchain 未配置，所以本轮不把新鲜 `cargo check` 作为完成证据；继续使用历史 PCWEB-118 证据和当前 static tests 作为 P2-1 gate。

历史真实 Tauri no-op WebView run evidence：

```text
artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json

capture_status=captured_validated_tauri_noop_run
run_environment=tauri_webview
ipc_transport_status=tauri_ipc_available
validated_command_count=10
returned_command_count=10
safe_to_capture_audio_now=false
safe_to_call_remote_asr_now=false
safe_to_call_llm_now=false
```

## 5. P2-1 Focused Verification

当前验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py

python3 -m py_compile tools/desktop_worker_mic_source_from_tauri_evidence.py
```

当前结果：

```text
13 passed, 1 warning
py_compile passed
```

CLI evidence packet：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_worker_mic_source_from_tauri_evidence.py \
  --tauri-evidence-path artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json
```

关键输出：

```text
policy_validation_status=passed
tauri_evidence_validation_status=passed
worker_mic_source_approval_packet_status=ready_for_manual_review_not_executable
worker_mic_source_approval_status=not_approved
safe_to_capture_audio_now=false
safe_to_spawn_worker_now=false
safe_to_call_remote_asr_now=false
safe_to_call_llm_now=false
```

## 6. 后续真实 Mac MVP 任务

下一阶段若要从 dev shell 推进到真实 Mac MVP，必须新增独立目标：

- Tauri dev WebView fresh run：当前机器重新启动窗口并截图验证 Workbench。
- `mic_adapter.start` 从 no-op 变成显式用户 start 后的真实采集。
- 麦克风权限弹窗和拒绝/授权路径自测。
- ASR worker spawn / health / stop / cleanup。
- audio chunk lifecycle：write/read/delete。
- Workbench 在 Tauri WebView 中跑同一套真实麦克风全链路。
- `.app` / `.dmg` 构建、签名、notarization、安装后 smoke。

这些任务不属于当前 P2-1 完成范围。
