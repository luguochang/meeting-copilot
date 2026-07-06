# PCWEB-119 Real Tauri No-op WebView Run Evidence Plan

> 日期：2026-07-03  
> 状态：Accepted / Executed  
> 目的：在真实 Tauri WebView 中加载本地 Web 工作台，调用 PCWEB-107 的 10 个 no-op IPC command，并把 PCWEB-116 collector result 提交给 PCWEB-117 validation endpoint，形成 PCWEB-113 可接受的真实 no-op IPC evidence。  
> 边界：本计划不授权访问麦克风、不请求音频权限、不启动 ASR worker、不读写 audio chunk、不读取 `configs/local/`、不读取真实用户音频或 `.m4a`、不调用远程 ASR/LLM、不下载模型或公开音频。

## 1. 背景

PCWEB-118 已证明 Tauri Rust crate 可以通过受控 `cargo check`，但仍未证明真实 Tauri WebView 可以加载 Web MVP，也未证明 `window.__TAURI__` IPC collector 能产出可被后端验证的 result。

PCWEB-119 的目标是补齐这个执行态证据。它只验证 no-op IPC 和 validation capture，不进入麦克风采集或 worker 执行。

## 2. TDD / 修复点

真实 Tauri WebView run 前发现一个结果结构阻断：

- Rust `NoopBridgeResponse` 曾包含自由文本 `message` 字段。
- PCWEB-113 validator 为避免 raw path、secret、stdout/stderr 等文本泄漏，只接受严格白名单字段。
- 因此真实 WebView 中 10 个 IPC 即使 returned，也会因为 unsupported field 被 PCWEB-117 validation 拒绝。

本轮 TDD：

- Red：`tests/test_desktop_tauri_scaffold.py::test_noop_bridge_response_contract_declares_no_side_effects` 改为禁止 `pub message:`、`message:` 和旧自由文本。
- Green：从 `code/desktop_tauri/src-tauri/src/lib.rs` 删除 `message` 字段和赋值，只保留结构化 no-side-effect 字段。

## 3. 执行命令

Focused gate：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_tauri_noop_shell_run_smoke.py \
  tests/test_desktop_tauri_noop_run_result_intake.py \
  tests/test_desktop_tauri_noop_webview_run_capture.py \
  -q -p no:cacheprovider
```

结果：

```text
32 passed, 2 warnings
```

Controlled cargo check：

```bash
CARGO_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/desktop_tauri_target \
/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

结果：

```text
Finished `dev` profile
exit 0
```

Capture server：

```bash
python3 -m uvicorn desktop_tauri_noop_webview_run_capture_app:app \
  --app-dir tools --host 127.0.0.1 --port 8765
```

Real Tauri no-op run：

```bash
CARGO_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/desktop_tauri_target \
/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  run --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

Evidence file：

```text
artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json
```

Evidence summary：

```json
{
  "capture_status": "captured_validated_tauri_noop_run",
  "run_environment": "tauri_webview",
  "validation_status": "passed",
  "evidence_status": "ready_for_worker_mic_source_approval_review",
  "validated_command_count": 10,
  "returned_command_count": 10
}
```

Server log showed the real WebView loaded `/`, static assets, desktop readiness endpoints, and posted:

```text
POST /desktop/tauri-noop-run-results/validations 200 OK
```

## 4. 已证明

- 真实 Tauri WebView 可以加载本地 Web MVP dev URL `http://127.0.0.1:8765/`。
- `app.withGlobalTauri=true` 后，Web 工作台可以通过 `window.__TAURI__` 访问 Tauri v2 IPC。
- PCWEB-116 collector 在 Tauri WebView 中调用了 10 个 no-op IPC command。
- 10 个 command 均 returned，且返回值满足 PCWEB-113 strict no-side-effect contract。
- PCWEB-117 validation endpoint 返回 `result_validation_status=passed`。
- PCWEB-119 capture wrapper 只在 validation 通过后，把 evidence JSON 写入 ignored `artifacts/tmp/desktop_tauri_noop_run_results`。

## 5. 未证明

- 不代表麦克风权限已请求或可用。
- 不代表真实音频已采集、写入或删除。
- 不代表 ASR worker 已启动。
- 不代表 worker `source_kind=mic` 已批准。
- 不代表真实 mic adapter 已实现。
- 不代表 ASR worker real mic source 已实现。
- 不代表中文技术会议 ASR 质量已达标。
- 不代表可以进入用户真实麦克风 shadow test。

## 6. 下一步

PCWEB-119 消除了真实 Tauri no-op run result 缺失这一 blocker。后续主线只能在以下方向中推进：

- 同 session worker mic source approval packet，使用 PCWEB-119 evidence 与 PCWEB-112 connector request 形成人工 review packet。
- 明确审批后的最小真实 mic adapter implementation boundary，但仍必须先保持 start/pause/resume/stop/delete 和 ignored runtime root 边界。
- ASR quality exit：提供已验证 FunASR 本地模型目录、明确批准 DRV-019，选择可选远程 ASR 对照，或明确降级。

真实麦克风会议仍由用户最终显式启动，且必须等待 readiness gate 组合证据返回 ready。
