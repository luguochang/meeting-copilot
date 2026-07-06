# PCWEB-117 Desktop Tauri No-op Run Result Validation API/UI Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把 PCWEB-116 在未来 Tauri WebView 中收集到的 `desktop_tauri_noop_run_result.v1`，通过 Web backend 交给 PCWEB-113 validator 校验，并在工作台展示 validation 状态。  
> 边界：本计划不授权运行 Cargo/Tauri、不授权访问麦克风、不授权请求音频权限、不授权启动 worker、不授权读取或写入 audio chunk、不授权读写 worker event file、不授权读取 `configs/local/` 或 secret、不授权调用远程 ASR/LLM、不授权下载模型或公开音频、不授权写入本地 result 文件。

## 1. 背景

PCWEB-116 已能在 Web 工作台中生成 no-op run result collector surface。普通浏览器只显示 `collector_browser_fallback`；未来 Tauri WebView 中才会通过 `window.__TAURI__` 调用 PCWEB-107 的 10 个 no-op IPC，并把 result 放到 `window.__meetingCopilotTauriNoopRunResult`。

PCWEB-113 已有独立 CLI/tool intake，但如果未来 Tauri WebView 运行后还需要人工复制 JSON 再跑 CLI，真实 Tauri no-op run 到 worker mic source approval 的链路仍然不顺。因此 PCWEB-117 补齐一个后端 validation endpoint 和 UI validation summary，让 collector result 可以在工作台内直接进入 PCWEB-113 校验逻辑。

## 2. 改动

修改：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
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

新增 Web backend endpoint：

```text
POST /desktop/tauri-noop-run-results/validations
```

请求体：

```json
{
  "run_result": {
    "run_result_version": "desktop_tauri_noop_run_result.v1"
  }
}
```

后端行为：

- 动态加载 `tools/desktop_tauri_noop_run_result_intake.py`。
- 调用 `build_tauri_noop_run_result_intake_report(run_result=...)`。
- 当 PCWEB-113 report 的 `result_validation_status=passed` 时返回 report。
- 当 result 是 browser fallback、side-effect flag 漂移、command 缺失、extra command、raw path/secret/stdout 字段或其他 PCWEB-113 validation error 时，返回 HTTP 422，`detail` 为 PCWEB-113 report。
- `data_dir` 模式下不创建 `sessions`、`live_asr_sessions` 或 `desktop_tauri_noop_run_results` 存储目录。

工作台行为：

- 普通浏览器仍不提交 validation request，显示：
  - `pcweb_117_validation_status=not_submitted`
  - `validation_status=validation_browser_fallback`
  - `result_validation_status=not_submitted`
  - `real_tauri_noop_run_evidence_status=not_available`
- 未来 Tauri WebView 中，如果 PCWEB-116 collector 的 10 个 command 全部 returned，则自动调用 `/desktop/tauri-noop-run-results/validations`。
- PCWEB-113 validation 通过后，UI 显示：
  - `pcweb_117_validation_status=validated_by_pcweb_113`
  - `validation_status=validated_noop_ipc_observed`
  - `validated_command_count=10`
  - `returned_command_count=10`
- PCWEB-113 validation 阻断后，UI 显示：
  - `pcweb_117_validation_status=blocked_by_pcweb_113_validation`
  - `validation_status=blocked_by_result_validation`

## 4. 安全边界

PCWEB-117 只做 collector result 的 response-time validation，不新增任何执行权限：

- 不运行 Cargo/Tauri。
- 不请求麦克风权限。
- 不枚举设备。
- 不采集、读取、写入或删除 audio chunk。
- 不启动 ASR worker。
- 不读写 worker event file。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或公开音频。
- 不写本地 result 文件。
- 不批准 worker mic source。
- 不改变 PCWEB-115 的真实麦克风 shadow-test blocker。

UI validation summary 继续展示 all-false safety flags：

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
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_accepts_collector_result_without_running_tauri \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_rejects_browser_fallback_or_side_effects \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_with_data_dir_does_not_create_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
4 failed, 2 warnings
```

失败原因：

- `/desktop/tauri-noop-run-results/validations` 尚不存在，API tests 返回 404。
- `validateDesktopTauriNoopRunResult` 和 validation UI 标记尚不存在，static asset test 失败。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_accepts_collector_result_without_running_tauri \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_rejects_browser_fallback_or_side_effects \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_with_data_dir_does_not_create_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
4 passed, 2 warnings
```

浏览器绿灯：

```bash
cd code/web_mvp
MEETING_COPILOT_E2E_VERBOSE=1 \
MEETING_COPILOT_E2E_PORT=8773 \
MEETING_COPILOT_E2E_CHROME_PORT=9333 \
node e2e/browser_smoke.mjs
```

结果：

```json
{
  "status": "ok"
}
```

## 6. 结论

PCWEB-117 把“未来真实 Tauri no-op run result”从 UI 内存 collector 接到了 PCWEB-113 validator。它让下一步真实 Tauri no-op run 的 evidence 可以在工作台内完成初步校验，再进入 PCWEB-114 worker mic source approval packet。

当前真实麦克风 shadow test 仍 blocked。PCWEB-117 不代表 Tauri/Cargo 已运行，不代表真实 Tauri result 已产生，不代表 worker mic source 已批准，不代表真实 mic adapter 已实现，也不代表 ASR quality 已退出。
