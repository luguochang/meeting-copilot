# PCWEB-116 Desktop Tauri No-op Run Result Collector Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把未来真实 Tauri WebView no-op run 的 10 个 IPC 返回结果，收集成 PCWEB-113 可摄入的 `desktop_tauri_noop_run_result.v1` 结构。  
> 边界：本计划不授权运行 Cargo/Tauri、不授权访问麦克风、不授权请求音频权限、不授权启动 worker、不授权读取或写入 audio chunk、不授权读写 worker event file、不授权读取 `configs/local/` 或 secret、不授权调用远程 ASR/LLM、不授权下载模型或公开音频。

## 1. 背景

PCWEB-113 已有 result intake，但此前真实 Tauri WebView no-op run 的 result JSON 仍需要人工构造。PCWEB-115 又把真实麦克风 shadow test 的 blocker 固定为：真实 Tauri no-op result 缺失、worker mic source 未批准、真实 mic adapter 未实现、ASR worker mic source 未实现、ASR quality 未退出。

本轮不运行 Tauri/Cargo，也不继续写同类 readiness wrapper；只把 Web 工作台补成未来 Tauri WebView 中可以自动收集 no-op result 的 UI surface。普通浏览器仍稳定显示 fallback。

## 2. 改动

修改：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 3. 行为

工作台的 `desktop-mic-adapter-contract-panel` 现在有三段：

1. PCWEB-105/106 mic adapter 合同 readiness。
2. PCWEB-109 7 个 mic adapter no-op invocation row。
3. PCWEB-116 10 个 Tauri no-op run result collector row。

10 个 collector command：

- `runtime.get_status` / `runtime_get_status`
- `session.prepare` / `session_prepare`
- `asr_worker.health` / `asr_worker_health`
- `mic_adapter.prepare` / `mic_adapter_prepare`
- `mic_adapter.status` / `mic_adapter_status`
- `mic_adapter.start` / `mic_adapter_start`
- `mic_adapter.pause` / `mic_adapter_pause`
- `mic_adapter.resume` / `mic_adapter_resume`
- `mic_adapter.stop` / `mic_adapter_stop`
- `mic_adapter.delete_audio_chunks` / `mic_adapter_delete_audio_chunks`

普通浏览器：

- `collector_status=collector_browser_fallback`
- `run_result_version=desktop_tauri_noop_run_result.v1`
- `run_environment=browser_fallback`
- `explicit_tauri_run_approval_recorded=false`
- `ipc_transport_status=not_available`
- 10 个 command result 均为 `invoke_status=not_invoked`
- `real_tauri_noop_result_ready=false`

未来 Tauri WebView：

- 通过 `window.__TAURI__.core.invoke` 或 `window.__TAURI__.tauri.invoke` 调用 10 个 no-op command。
- 在浏览器内存中生成 `window.__meetingCopilotTauriNoopRunResult`。
- 若 10 个 command 全部 returned，则 `real_tauri_noop_result_ready=true`。
- 该对象结构与 PCWEB-113 的 `run_result_version=desktop_tauri_noop_run_result.v1` 对齐。

## 4. 安全边界

PCWEB-116 仍是 no-op result collector，不是 Tauri run 授权：

- 不运行 Cargo/Tauri。
- 不请求麦克风权限。
- 不枚举设备。
- 不采集、读取、写入或删除 audio chunk。
- 不启动 ASR worker。
- 不读写 worker event file。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或公开音频。
- 不写本地文件。

UI 中的 safety flags 保持 false：

- `safe_to_request_audio_permission_now=false`
- `safe_to_capture_audio_now=false`
- `safe_to_start_asr_worker_now=false`
- `safe_to_read_audio_chunk_now=false`
- `safe_to_write_audio_chunk_now=false`
- `safe_to_read_worker_event_file_now=false`
- `safe_to_write_worker_event_file_now=false`
- `safe_to_call_remote_asr_now=false`
- `safe_to_call_llm_now=false`
- `safe_to_run_tauri_or_cargo_now=false`

## 5. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
1 failed, 2 warnings
```

失败原因：`loadDesktopTauriNoopRunResultCollector` 尚未实现。

浏览器红灯：

```bash
node e2e/browser_smoke.mjs
```

结果：

```text
Error: expected Tauri no-op result collector browser fallback
```

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
1 passed, 2 warnings
```

浏览器绿灯：

```bash
node e2e/browser_smoke.mjs
```

结果：

```json
{
  "status": "ok"
}
```

## 6. 结论

PCWEB-116 让下一次真实 Tauri no-op run 不再需要手工拼 result JSON。只要后续显式批准并实际运行 Tauri WebView，工作台就能在内存中收集 10 个 no-op IPC 的 result，并把形状对齐到 PCWEB-113。

当前真实麦克风 shadow test 仍 blocked。PCWEB-116 不代表 Tauri/Cargo 已运行，不代表 worker mic source 已批准，不代表真实 mic adapter 已实现，也不代表 ASR quality 已退出。
