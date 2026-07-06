# PCWEB-097 Desktop ASR Worker Handoff Dry-run Readiness UI Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把 PCWEB-096 desktop ASR worker handoff local dry-run 状态接入 Web/Tauri no-op readiness UI/API。  
> 边界：不启动 ASR worker，不访问麦克风，不读取真实音频，不读取 `configs/local/`，不调用远程 ASR/LLM，不下载模型，不运行 Cargo/Tauri。

## 1. 目标

PCWEB-096 已把 desktop descriptor preflight 与 Web `/live/asr/local-event-files/sessions` API 串成受控 dry-run，但状态只存在于 CLI/工具侧。PCWEB-097 把该状态暴露给本地工作台，让 Web/Tauri no-op UI 能看到下一步 ASR worker handoff 的 readiness、blockers、allowed roots 和安全边界。

## 2. 实现

- 新增后端只读端点：`GET /desktop/asr-worker-handoff-dry-run-readiness`。
- 新增工作台面板：`desktop-asr-handoff-dry-run-panel`。
- 前端启动时 passive 读取该端点，渲染 `preview_only_ready`、`preview_ready_no_web_mutation`、`explicit_mode_only`、6 个 phase、blockers、next decisions 和 false safety flags。
- Browser smoke 首屏验证该面板，且 passive load 仍不写 `MEETING_COPILOT_DATA_DIR`。

## 3. 响应合同

端点返回：

- `pcweb_id=PCWEB-096`
- `next_pcweb_id=PCWEB-098`
- `desktop_asr_worker_handoff_dry_run_mode=readiness_only`
- `desktop_asr_worker_handoff_dry_run_status=preview_only_ready`
- `pcweb_096_default_dry_run_status=preview_ready_no_web_mutation`
- `synthetic_local_test_status=explicit_mode_only`
- `worker_execution_status=not_started`
- `event_file_read_status=not_read`
- `web_handoff_mutation_status=not_mutated`
- `handoff_api_endpoint=/live/asr/local-event-files/sessions`
- `approved_event_file_root=artifacts/tmp/asr_events`
- `approved_temp_web_data_dir_root=artifacts/tmp/desktop_handoff_dry_run`
- `desktop_asr_handoff_phase_count=6`
- `desktop_asr_handoff_phases`
- `desktop_asr_handoff_blockers`
- `desktop_asr_handoff_next_decisions`

所有 action safety flags 固定为 false：

- `desktop_asr_handoff_safe_to_start_worker`
- `desktop_asr_handoff_safe_to_capture_audio`
- `desktop_asr_handoff_safe_to_read_real_audio`
- `desktop_asr_handoff_safe_to_read_configs_local`
- `desktop_asr_handoff_safe_to_call_remote_asr`
- `desktop_asr_handoff_safe_to_call_llm`
- `desktop_asr_handoff_safe_to_download_models`
- `desktop_asr_handoff_safe_to_run_tauri_or_cargo`
- `desktop_asr_handoff_safe_to_mutate_web_session_now`

## 4. 非目标

PCWEB-097 不做：

- 不启动 ASR worker。
- 不读取 event file。
- 不调用 Web handoff mutation API。
- 不访问麦克风或系统音频。
- 不读取真实用户录音。
- 不读取 provider config、API key、keychain、环境密钥或 `configs/local/`。
- 不调用远程 ASR/LLM。
- 不下载 FunASR/ModelScope 模型。
- 不运行 Tauri、Cargo、package manager 或 shell command。

## 5. 验证

TDD 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_does_not_probe_audio_or_read_secrets \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_with_data_dir_does_not_create_local_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：5 failed。缺少 endpoint、panel 和 frontend loader。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_does_not_probe_audio_or_read_secrets \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_with_data_dir_does_not_create_local_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：5 passed, 2 warnings。

后续还需运行相邻 Web backend tests、browser smoke、all-local no-browser gate 和敏感信息扫描。
