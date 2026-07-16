# P0-P2 Progress Update: Real Mic And Mac Packaging

> 日期：2026-07-09
> 状态：active, not final completion
> 目的：记录本轮对 P0 真实麦克风主链路和 P2 Mac 打包的真实进展，避免后续把旧 no-go 证据、计划文档、或部分打包结果误解成最终完成。

## 1. 本轮新增结论

### 1.1 P0 真实麦克风主链路已经获得一条 GO 证据

本轮先复现了旧问题：

- `browser-live-mic-20260709-mainline`
  - headless Chrome
  - 真实浏览器麦克风输入
  - 采集到 40 个 chunk
  - `health_status=blocked_audio_too_quiet`
  - `asr_final_count=0`
  - 结论：no-go，不能算真实麦克风完成。

随后新增脚本能力并重跑：

- `browser-visible-live-mic-20260709-mainline-001`
  - visible Chrome
  - 真实浏览器麦克风输入
  - 采集到 67 个 chunk
  - `health_status=audio_capture_health_passed`
  - `asr_final_count=0`
  - 结论：采集已过，但 ASR 没 final，仍 no-go。

最终用 macOS `say` 播放中文技术会议语料，让笔记本麦克风真实收外放声，跑通同一套 Workbench 链路：

- 原始证据目录：
  - `artifacts/tmp/browser_live_mic/browser-visible-live-mic-tts-20260709-mainline-001/`
- 正式验收 bundle：
  - `artifacts/tmp/acceptance/browser-live-mic-tts-20260709-mainline-001/manifest.json`

正式 manifest 关键字段：

```text
verdict=go
audio_source=browser_live_mic
input_audio_path_kind=browser_get_user_media
counts_as_real_mic_go_evidence=true
browser_live_mic_go_evidence=true
ui_coverage=visible_chrome
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
asr_semantic_quality_status=passed
transcript_char_count=105
final_segment_count=1
llm_provider=real_gateway
llm_called=true
suggestion_card_count=3
approach_card_count=3
minutes_char_count=614
all_cards_have_evidence=true
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
delete_verified=true
degradation_reasons=[]
```

边界说明：

- 这是真实浏览器麦克风链路，不是上传 WAV，也不是 mock ASR。
- 输入语音由本机 `say` 外放后被麦克风收音，属于可控真实麦克风自测，不等于多人真实会议质量验收。
- 这证明主链路可跑通：真实麦克风采集 -> 实时 ASR -> 语义质量 gate -> LLM 建议卡 -> 方案卡 -> 纪要 -> UI 展示 -> 删除。
- 后续仍需要用户真实会议环境的手工验收，验证自然说话、多说话人、环境噪声和麦克风设备差异。

### 1.2 真实麦克风 E2E 脚本已增强

修改文件：

- `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`
- `code/web_mvp/backend/tests/test_workbench.py`

新增能力：

- 支持 `MEETING_COPILOT_BROWSER_MIC_HEADLESS=false` 运行可见 Chrome。
- 支持 `MEETING_COPILOT_BROWSER_MIC_FAKE_UI` 显式控制自动允许麦克风权限。
- 支持 `MEETING_COPILOT_BROWSER_MIC_AUDIO_FILE` 作为 Chrome fake audio 诊断输入。
- 新增 `browser_environment.json`，记录：
  - `input_mode`
  - `ui_coverage`
  - `chrome_headless`
  - `chrome_fake_ui_for_media_stream`
  - `chrome_fake_audio_file`
  - `record_seconds`
  - 端口信息
- `asr_probe.json` 现在导出：
  - `asr_semantic_quality`
  - `acceptance_eligible`
  - `acceptance_blockers`

已验证：

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/code/web_mvp/backend/tests/test_workbench.py \
  -k 'exports_asr_semantic_quality or fake_audio_file_diagnostic_mode or records_chrome_capture_mode'

3 passed, 58 deselected, 2 warnings
```

### 1.3 Browser live mic bundle 接受 visible Chrome 作为真实用户麦克风 UI 证据

修改文件：

- `tools/mainline_evidence_bundle_runner.py`
- `tests/test_mainline_evidence_bundle_runner.py`

原因：

- 真实麦克风权限、系统输入设备和外放收音更接近可见 Chrome 的用户路径。
- 旧 gate 只接受 `headless_chrome`，会把真实可见浏览器验收误判为 `ui_not_verified_in_browser`。

已验证：

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/tests/test_mainline_evidence_bundle_runner.py \
  -k 'browser_live_mic_lane'

4 passed, 11 deselected, 2 warnings
```

## 2. P2 Mac 打包进展

### 2.1 已证明

受控 Tauri toolchain 存在：

```text
tauri-cli 2.11.4
stable-aarch64-apple-darwin active default
```

完整构建命令：

```text
CARGO_HOME=artifacts/tmp/rust_toolchain/cargo
RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup
CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target
cargo tauri build
```

构建结果：

```text
Finished release profile
Built application at: artifacts/tmp/desktop_tauri_target/release/meeting-copilot-desktop
Bundling Meeting Copilot.app
Bundling Meeting Copilot_0.1.0_aarch64.dmg
Error failed to bundle project: error running bundle_dmg.sh
```

已产物：

```text
artifacts/tmp/desktop_tauri_target/release/meeting-copilot-desktop
artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app
```

`.app` smoke：

- 用 `open -n artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app` 启动。
- 观察到进程：

```text
Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop
```

2026-07-09 后续更新：

- 已修复 packaged Workbench API base 边界：Tauri `runtime_get_status` 返回 `desktop_api_base_url=http://127.0.0.1:8765`。
- Workbench 在 Tauri runtime 下会使用该 base 解析 HTTP API 和 WebSocket：
  - HTTP: `apiUrl(...)`
  - WebSocket: `apiWsUrl(...)`
- 浏览器模式保持原来的相对路径。
- `cargo tauri build --bundles app` 已复现成功：

```text
Finished release profile
Built application at: artifacts/tmp/desktop_tauri_target/release/meeting-copilot-desktop
Bundling Meeting Copilot.app
Finished 1 bundle at:
  artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app
```

- 重新启动 `.app` 后观察到 packaged 进程：

```text
Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop
```

边界：

- 这只能证明 `.app` 能启动进程。
- 还不能证明 packaged app 内 Workbench DOM 完整可用。
- 还不能证明 packaged app 能跑同一套实时会议主链路。
- 还没有签名、公证、安装路径 smoke。

### 2.2 未完成 / Blocker

- `.dmg` 未产出，失败在 Tauri 生成的 `bundle_dmg.sh`。
- 已用 `bash -x bundle_dmg.sh ...` 定位到更具体的卡点：脚本挂在 Finder AppleScript 美化 DMG 窗口阶段。
- 相关日志片段：

```text
Running AppleScript to make Finder stuff pretty:
/usr/bin/osascript ... createdmg.tmp ... dmg.DLq0dd
```

- 该问题属于 DMG packaging/Finder automation blocker，不影响 `.app` 本体构建。
- 已清理手动诊断遗留挂载盘：`hdiutil detach /dev/disk5` -> `"disk5" ejected.`
- 打包目录普通 `sed/find/du/plutil` 读取多次出现 `Interrupted system call` 或卡住，和此前记录的工作树 I/O 异常一致。
- 需要后续单独修复或绕过 `.dmg` 阶段，例如：
  - 先把 `.app` 作为开发验收交付物；
  - 再排查 `bundle_dmg.sh` / `hdiutil` / DMG 背景资源 / 文件系统锁；
  - 最后再做签名、公证和安装 smoke。

## 3. 当前 P0-P2 状态

### P0

- 公开测试音频模拟实时主链路：GO。
- 真实麦克风主链路：GO，基于 visible Chrome + getUserMedia + 本机外放 TTS 被真实麦克风收音。
- 前端产品化：已有默认隐藏 demo、核心入口、按钮 smoke；仍建议继续做逐按钮完整回归。
- 自动建议链路：真实麦克风 GO run 中已证明 3 张建议卡、3 张方案卡、纪要、证据引用和删除。

### P1

- 录音导入/会后转写、Provider 成本策略、删除策略、长会议 synthetic gate 此前已有实现和部分证据。
- 本轮未重新跑完整 P1 focused regression。

### P2

- Tauri dev / compile / `.app` 构建和启动进程：部分 GO。
- `.dmg`：NO-GO，bundle script 失败。
- packaged app DOM/UI/mainline：未完成。
- Rust 原生 PCM 麦克风采集：未完成；当前真实麦克风主线走 WebView/browser getUserMedia。
- Windows：仍是 plan-only。

## 4. 下一步执行顺序

1. 生成并保存 P0-P2 最新 release summary，引用新的真实麦克风 GO manifest。
2. 继续 P2：
   - 修 `.dmg` 失败或正式记录为 blocker；
   - 做 packaged `.app` DOM/截图/Workbench runtime evidence；
   - 验证 packaged app 与后端/API 的连接边界。
3. 继续前端逐按钮回归：
   - 开始会议；
   - 结束会议；
   - 导入录音；
   - 历史记录；
   - 生成建议；
   - 方案分析；
   - 纪要；
   - 导出文字稿/纪要；
   - 删除。
4. 最后重新跑 release acceptance runner；如果 runner 因当前 I/O 异常无法运行，要把异常和缺口记录为 release blocker，不能宣称全完成。

## 6. 2026-07-09 Packaged App Window And Release Gate Update

### 6.1 Packaged `.app` window evidence

新增证据：

```text
artifacts/tmp/desktop_tauri_current_run/packaged-app-window-20260709/evidence.json
```

关键结论：

```text
process_observed=true
webkit_processes_observed=true
coregraphics_window_observed=true
window_owner=Meeting Copilot
window_number=6612
window_bounds={x:95,y:33,width:1280,height:849}
counts_as_packaged_window_evidence=true
counts_as_packaged_dom_evidence=false
```

边界：

- CoreGraphics 能看到 packaged `.app` 的真实窗口。
- System Events/Accessibility 没有暴露窗口：`system_events_window_count=0`。
- `screencapture -l <window_id>` 失败：`could not create image from window`。
- 全屏 `screencapture` 也失败：`could not create image from display`。
- 因此当前机器无法自动拿到 packaged `.app` 的截图或 DOM 级证据。
- 这不是 `.app` 没启动，而是 macOS 窗口/屏幕捕获权限或策略 blocker。

### 6.2 Backend mainline regression

Release runner 先暴露两个旧测试断言问题：

- `test_workbench_route_serves_html` 写死 `20260708` 版本号。
- `test_workbench_browser_live_mic_ws_marks_browser_source_not_generic_replay` 仍要求直接 `new WebSocket('/live/asr/...')`，与 packaged desktop `apiWsUrl(...)` 修复冲突。

已修复测试断言，并完成 fresh regression：

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  tests/test_mainline_evidence_bundle_runner.py \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_real_llm_path.py \
  code/web_mvp/backend/tests/test_metrics.py \
  code/web_mvp/backend/tests/test_g3_g4_g5.py \
  code/web_mvp/backend/tests/test_e2e_mainline.py \
  code/web_mvp/backend/tests/test_shadow_trial.py

214 passed, 2 warnings
```

### 6.3 Release acceptance state

修正 `--browser-live-mic-bundle` 输入为 bundle 目录后，release acceptance 汇总显示：

```text
file_lane=go
simulated_realtime=go
browser_live_mic=go
backend_mainline previously failed, now separately verified fixed with 214 passed
release verdict still no_go because real_mic_recorded_realtime inputs are missing
```

当前不立刻重跑完整 release acceptance，原因是每次会再次调用真实 LLM gateway，增加中转站费用。按最新证据，剩余 release blocker 应收敛为：

- `real_mic_recorded_realtime` lane 仍缺录制 WAV + health report；不过 browser live mic 已经作为真实麦克风 GO evidence。
- packaged `.app` 仍缺 DOM/截图级证据。
- `.dmg`、签名、公证未完成。

### 6.4 Release gate and UI verifier update

2026-07-09 后续修复并重跑 release acceptance：

- 修复 `tools/release_acceptance_runner.py`：`browser_live_mic` 作为正式真实麦克风必需 lane；`real_mic_recorded_realtime` 改为辅助 lane。录制 WAV 输入缺失仍写入 lane manifest，但不再覆盖 browser live mic 的真实麦克风 GO 证据。
- 修复 `tools/mainline_evidence_bundle_runner.py`：Workbench UI verifier 启动独立 uvicorn 时，`MEETING_COPILOT_DATA_DIR` 和 `MEETING_COPILOT_ARTIFACT_ROOT` 统一传绝对路径，避免子进程 cwd 从仓库根目录变成 `code/web_mvp/backend` 后读到错误数据目录。
- 新增/更新测试：

```text
tests/test_release_acceptance_runner.py:
  test_release_acceptance_runner_treats_recorded_real_mic_as_optional_when_browser_live_mic_is_go

tests/test_mainline_evidence_bundle_runner.py:
  test_workbench_session_verifier_passes_absolute_runtime_paths
```

验证结果：

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/tests/test_mainline_evidence_bundle_runner.py \
  /tmp/meeting-copilot-short/tests/test_release_acceptance_runner.py

25 passed, 2 warnings
```

刷新后的 release acceptance：

```text
artifact_root=artifacts/tmp/release_acceptance/release-current-20260709-browser-mic-gate-and-ui-path-fixed
verdict=go
blockers=[]
pytest_backend_mainline=passed
workbench_smoke=passed
git_diff_check=passed
health_endpoint=passed
workbench_js_version=passed
file_lane=go
simulated_realtime=go
browser_live_mic=go
real_mic_recorded_realtime=no_go optional auxiliary lane
llm_call_count=10
llm_usage_total_tokens=48084
privacy_cost_flags.raw_audio_uploaded=false
privacy_cost_flags.remote_asr_called=false
privacy_cost_flags.configs_local_read=false
```

边界：

- 该 GO 证明 P0/P1 Web 主发布验收 gate 已通过当前 runner。
- 该 GO 不等于完整 P0-P2 目标完成；P2 packaged `.app` DOM/截图级证据、`.dmg`、签名、公证、Windows 实机仍未完成。
- `real_mic_recorded_realtime` 仍可作为后续辅助录制 WAV 验收 lane，但当前真实麦克风正式依据是 `browser_live_mic` 的 visible Chrome + getUserMedia + 本机外放 TTS 收音 GO bundle。

### 6.5 DMG skip-Finder packaging update

Tauri 默认 `cargo tauri build` 的 DMG 阶段仍卡在 Finder AppleScript 美化窗口步骤。已新增项目级可复现工具：

```text
tools/package_macos_dmg_skip_finder.py
tests/test_package_macos_dmg_skip_finder.py
```

该工具使用 Tauri 生成的 `bundle_dmg.sh` 的 `--skip-jenkins` 选项绕过 Finder AppleScript，产出本地开发验收 DMG：

```text
artifact=artifacts/tmp/desktop_dmg_skip_finder_tool_20260709/evidence.json
dmg=artifacts/tmp/desktop_dmg_skip_finder_tool_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg
dmg_sha256=9610afbd51c7c98e5887e9c354642b590381270011aacb3a4652e55af3794d94
packaging_method=tauri_generated_create_dmg_script_with_skip_jenkins
finder_applescript_skipped=true
```

验证结果：

```text
tests/test_package_macos_dmg_skip_finder.py: 3 passed, 1 warning
app_adhoc_codesign_exit_code=0
app_codesign_verify_exit_code=0
dmg_create_exit_code=0
dmg_mount_smoke_exit_code=0
dmg_contains_app=true
dmg_contains_applications_symlink=true
mounted_app_codesign_verify_exit_code=0
spctl_app_exit_code=3
spctl_dmg_exit_code=3
```

边界：

- 该 DMG 可以作为本机开发/验收打包证据。
- 该 DMG 不是公开发布安装包：当前只是 ad-hoc 本地签名，没有 Developer ID，没有 notarization，Gatekeeper 仍拒绝。
- Tauri 默认 DMG target 仍会尝试 Finder AppleScript；当前可复现发布工程路径是项目级 `tools/package_macos_dmg_skip_finder.py`。

### 6.6 Packaged app window recheck after local signing

本地 ad-hoc 重签和开发 DMG 产出后，重新启动 packaged `.app` 并刷新窗口证据：

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-app-window-signed-dmg-20260709/evidence.json
process_observed=true
coregraphics_window_observed=true
screenshot_status=blocked_by_macos_screen_capture_permission_or_window_capture_policy
app_codesign_verify_exit_code=0
spctl_app_exit_code=3
counts_as_packaged_window_evidence=true
counts_as_packaged_dom_evidence=false
```

边界：

- `.app` 本体经本地 ad-hoc 重签后，`codesign --verify --deep --strict` 通过。
- `spctl` 仍拒绝，因为没有 Developer ID 签名和 notarization。
- macOS 当前仍阻止 `screencapture`：`could not create image from display`。
- 仍不能证明 packaged `.app` 内 DOM 或同一套实时会议主链路。

### 6.7 Packaged WebView runtime probe

为绕过当前 macOS 屏幕录制/辅助功能权限导致的截图阻塞，本轮新增并验证了 Tauri 内部 packaged WebView runtime probe。

修复内容：

- 修复 `code/desktop_tauri/src-tauri/src/desktop_frontend_probe_runtime.rs` 的仓库根目录解析：
  - 旧路径会从 `code/desktop_tauri/src-tauri` 向上爬两层，错误写入 `code/artifacts/tmp/...`。
  - 新路径向上查找包含 `docs/` 和 `code/desktop_tauri/` 的仓库根，正确写入 `artifacts/tmp/...`。
- 将 probe artifact 拆成分类文件，避免 Rust page-load probe 覆盖前端 DOM/runtime probe：
  - `latest-page-load.json`
  - `latest-inline-dom.json`
  - `latest-workbench-runtime.json`
  - `latest.json`
- 修复 packaged Workbench 脚本加载：
  - Tauri packaged 环境使用相对 `workbench.js?v=20260709-desktop-probe`。
  - Web 服务环境使用 `/static/workbench.js?v=20260709-desktop-probe`。
- 修复 Workbench 启动顺序：
  - 先 `await initDesktopRuntimeProbe()` 获取 `desktop_api_base_url=http://127.0.0.1:8765`。
  - 再请求 `/audio/check` 和历史记录，避免 packaged 环境先访问 `tauri://localhost/...`。
- 新增 `tools/packaged_frontend_probe_evidence.py` 汇总验证 packaged frontend probe。

正式证据：

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-webview-runtime-probe-20260709-backend-api/evidence.json
status=go_packaged_webview_runtime_probe
blockers=[]
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
```

关键 probe 文件：

```text
artifacts/tmp/desktop_frontend_probe_runtime/latest-page-load.json
artifacts/tmp/desktop_frontend_probe_runtime/latest-inline-dom.json
artifacts/tmp/desktop_frontend_probe_runtime/latest-workbench-runtime.json
```

`latest-workbench-runtime.json` 证明：

```text
ready_state=complete
runtime_status.command_status=ok
runtime_status.desktop_api_base_url=http://127.0.0.1:8765
desktop_status_text=桌面壳已连接
selectors.history-list/session-meta/transcript-stream/suggestions-panel/approach-panel/minutes-panel/s-desktop=true
latest-backend-api.health_ok=true
latest-backend-api.sessions_loaded=true
latest-backend-api.errors=[]
```

验证结果：

```text
code/web_mvp/backend/tests/test_app.py::test_backend_allows_tauri_packaged_origin_for_local_api_probe:
passed

真实 HTTP CORS preflight:
OPTIONS /health
Origin: tauri://localhost
access-control-allow-origin: tauri://localhost

packaged backend API probe:
latest-backend-api.health_ok=true
latest-backend-api.sessions_loaded=true
latest-backend-api.errors=[]

tests/test_packaged_frontend_probe_evidence.py + tests/test_workbench_desktop_runtime_probe.py + tests/test_desktop_tauri_scaffold.py:
16 passed, 1 warning

code/web_mvp/backend/tests/test_workbench.py + tests/test_workbench_desktop_runtime_probe.py + tests/test_desktop_tauri_scaffold.py:
75 passed, 2 warnings

focused no-cost regression:
code/web_mvp/backend/tests/test_app.py
code/web_mvp/backend/tests/test_workbench.py
tests/test_workbench_desktop_runtime_probe.py
tests/test_packaged_frontend_probe_evidence.py
tests/test_package_macos_dmg_skip_finder.py
tests/test_mainline_evidence_bundle_runner.py
tests/test_release_acceptance_runner.py
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

边界：

- packaged DOM/runtime evidence 已从 No-Go 变为 Go。
- 该 6.7 runtime probe 本身仍不是 packaged app 内同一套实时会议主链路证据；后续 6.9 same-chain self-probe 已补齐该缺口。
- 截图证据仍未完成，原因是 macOS 屏幕捕获权限/策略。
- Developer ID 签名、公证、Gatekeeper、Windows 实机仍未完成。

### 6.8 Refreshed development DMG after packaged WebView fixes

重新构建 packaged `.app` 并完成 packaged WebView runtime probe 后，已刷新本地开发 DMG：

```text
tests/test_package_macos_dmg_skip_finder.py:
4 passed, 1 warning

artifact=artifacts/tmp/desktop_dmg_skip_finder_current_20260709/evidence.json
status=go_development_dmg_not_public_release
dmg=artifacts/tmp/desktop_dmg_skip_finder_current_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg
dmg_sha256=f5e047cdf7f1cdd13d9777bccc0ac39e648b549c40b1eede539bf74929dac435
dmg_create_exit_code=0
dmg_mount_smoke_exit_code=0
dmg_contains_app=true
dmg_contains_applications_symlink=true
mounted_app_codesign_verify_exit_code=0
spctl_app_exit_code=3
spctl_dmg_exit_code=3
counts_as_public_release_package=false
```

剩余 blocker 已同步收窄为：

```text
developer_id_codesign_not_done
notarization_not_done
gatekeeper_rejects_unsigned_or_adhoc_artifacts
packaged_screenshot_evidence_still_missing
```

边界：

- 该 DMG 已包含当前 packaged WebView 修复后的 `.app`。
- 该 DMG 仍是本地开发/内测包，不是公开分发包。
- Gatekeeper 仍拒绝，原因是缺 Developer ID 签名和 notarization。

### 6.9 Packaged same-chain no-cost self-probe

本轮新增 packaged `.app` 内部同链路自测，目标是消除 `packaged_same_chain_realtime_meeting_flow_not_verified`，且不增加中转站费用。

实现边界：

- 后端新增 demo-only `deterministic_demo` derivation mode。
- 仅 `/live/asr/demo/sessions/{id}/llm-execution-runs`、`/approach-cards`、`/minutes` 接受该模式。
- 生产 `/live/asr/sessions/{id}/...` endpoints 继续拒绝该模式。
- 该模式生成建议卡、方案卡和纪要，但标记为：

```text
execution_boundary=demo_no_cost_execution
llm_provider.provider=deterministic_demo
llm_call_status=not_called
cost_status=no_cost
llm_usage.total_tokens=0
```

packaged Workbench self-probe 仅在以下环境变量开启时运行：

```text
MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE=1
```

实际 packaged self-probe 证据：

```text
artifacts/tmp/desktop_frontend_probe_runtime/latest-same-chain.json
```

关键字段：

```text
session_created=true
events_ingested=true
events_visible_in_api=true
events_visible_in_workbench=true
same_session_id_observed=true
transcript_visible=true
suggestion_card_count=3
approach_card_count=1
minutes_visible=true
history_visible=true
delete_verified=true
history_removed_after_delete=true
remote_asr_called=false
remote_llm_called=false
paid_provider_called=false
errors=[]
```

正式聚合证据：

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-same-chain-no-cost-20260709/evidence.json
status=go_packaged_webview_runtime_probe
blockers=[]
counts_as_packaged_same_chain_no_cost_evidence=true
counts_as_packaged_mainline_evidence=true
packaged_same_chain_flow_complete=true
same_chain_blockers=[]
remaining_blockers=[
  developer_id_signing_not_done,
  notarization_not_done,
  gatekeeper_acceptance_not_done,
  windows_real_machine_not_verified
]
```

验证边界：

- 这证明 packaged WebView 内同一套 Workbench/backend/session/history/delete 链路可跑通。
- 这不是生产真实 LLM 证据。
- 这不是生产真实麦克风证据。
- 这不是签名/公证/Gatekeeper 公开发布证据。

## 5. Release External Preflight 更新

2026-07-09 新增外部发布预检证据：

```text
tool=tools/macos_release_external_preflight.py
test=tests/test_macos_release_external_preflight.py
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
notary_profile_provided=false
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

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q tests/test_macos_release_external_preflight.py
3 passed, 1 warning

python3 tools/macos_release_external_preflight.py \
  --run-id release-external-preflight-20260709 \
  --output-root artifacts/tmp/release_external_preflight_20260709
status=blocked_external_release_requirements
```

边界：

- 该预检不读取 `.env`、`configs/local/`、API key、notary password 或 keychain password。
- 该预检不提交 notarization，不调用 Apple 远程服务。
- 该预检不回退 packaged same-chain no-cost 主链路证据。
- packaged screenshot 缺失已重分类为 visual QA gap，并由 packaged DOM/runtime/same-chain probe 豁免 public release blocker。
- external preflight 已接入 Mac public release runner evidence；`notarization.tooling_ready=true` 不等于公证完成，`notarization.notarization_completed=false` 时仍保留 `notarization_not_done`。
- 该预检证明当前 public release 仍 blocked；完整 P0-P2 目标不能标记完成。

## 6. Mac Public Release Runner 更新

2026-07-09 新增可执行 Mac public release runner：

```text
tool=tools/macos_public_release_runner.py
test=tests/test_macos_public_release_runner.py
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
privacy_safety.secret_values_read=false
privacy_safety.keychain_password_read=false
privacy_safety.notarization_submitted=false
privacy_safety.remote_service_called=false
```

该 runner 已具备完整执行路径：

```text
ditto stage app
-> codesign app with Developer ID Application
-> verify app
-> create DMG via bundle_dmg.sh --skip-jenkins
-> codesign DMG
-> xcrun notarytool submit --wait
-> xcrun stapler staple
-> spctl app
-> spctl DMG
```

边界：

- 当前机器没有 Developer ID Application identity，因此 runner 在任何 mutating command 前停止。
- 这不是 signed/notarized/Gatekeeper GO evidence。
- 证书可用后应重跑同一 runner，而不是另写人工流程。

## 7. Windows Real-Machine Validator 更新

2026-07-09 新增 Windows 真机 evidence intake/validator：

```text
tool=tools/windows_real_machine_verification.py
test=tests/test_windows_real_machine_verification.py
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

External preflight 已接入该 evidence：

```text
windows.evidence_path=artifacts/tmp/windows_real_machine_verification_20260709/evidence.json
windows.validator_status=blocked_windows_real_machine_verification
windows.windows_real_machine_verified=false
remaining_blockers includes windows_real_machine_not_verified
```

边界：

- 这不是 Windows 真机通过证据。
- 当前 Mac 环境不执行 Windows 命令、不访问 WASAPI、不读取用户音频。
- 后续 Windows 真机 observation 必须放在 `artifacts/tmp/**`，由 validator 校验后才能清除 blocker。

## 8. 仍不能宣称完成的事项

- 不能宣称完整生产级 PC 桌面端已完成。
- 不能宣称 `.dmg` 是可公开分发的签名公证安装包。
- 不能宣称签名/公证完成。
- 不能宣称 Windows 完成。
- 不能宣称 Rust 原生麦克风 PCM 采集完成。
- 不能用本机 TTS 收音替代最终用户真实会议验收。
- 不能用 packaged no-cost self-probe 替代生产真实 LLM 或真实麦克风会议验收。
