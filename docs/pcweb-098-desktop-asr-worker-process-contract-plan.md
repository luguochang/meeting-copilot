# PCWEB-098 Desktop ASR Worker Process Contract Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：定义未来 desktop ASR worker sidecar 的进程合同、命令目录、资源限制、事件输出合同和安全边界。  
> 边界：本计划不授权启动 worker、不授权访问麦克风或系统音频、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型、不授权运行 Cargo/Tauri。

## 1. 目标

PCWEB-098 把 PCWEB-097 的 readiness UI/API 往真实桌面 ASR worker 主线推进一步：先把 worker 进程应该长什么样、能输出什么、哪些命令未来存在、哪些根目录被允许、哪些动作当前仍被禁止写成 machine-checkable 合同。

这一步仍然是 `process_contract_only`，不是 worker 实现。

## 2. 产物

- `code/desktop_tauri/asr-worker-process-contract.policy.json`
- `tools/desktop_asr_worker_process_contract.py`
- `tests/test_desktop_asr_worker_process_contract.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

## 3. 合同内容

worker process contract 固定以下主字段：

- `pcweb_id=PCWEB-098`
- `policy_status=desktop_asr_worker_process_contract_policy_only`
- `contract_mode=process_contract_only`
- `worker_process_status=not_spawned`
- `worker_lifecycle_status=specified_not_started`
- `worker_health_status=not_checked`
- `worker_command_transport_status=not_bound`
- `worker_output_contract_status=event_file_or_stream_specified`
- `approved_event_output_root=artifacts/tmp/asr_events`
- `approved_runtime_root=artifacts/tmp/desktop_asr_worker_runtime`
- `handoff_api_endpoint=/live/asr/local-event-files/sessions`

统一 ASR event contract：

```text
partial
final
revision
error
end_of_stream
```

future command catalog：

```text
worker.prepare
worker.start
worker.stop
worker.health
worker.collect_events
worker.cleanup
```

所有 command 当前 `safe_to_execute_now=false`。

## 4. 路径边界

允许：

- event output：`artifacts/tmp/asr_events`
- runtime root：`artifacts/tmp/desktop_asr_worker_runtime`

禁止：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- 仓库外绝对路径
- symlink 逃逸

无效路径必须脱敏为 `<redacted_invalid_path>`。

## 5. Safety Flags

以下 flags 必须保持 false：

- `safe_to_run_subprocess_now`
- `safe_to_spawn_worker_now`
- `safe_to_start_worker_now`
- `safe_to_stop_worker_now`
- `safe_to_check_worker_health_now`
- `safe_to_bind_worker_command_transport_now`
- `safe_to_capture_audio_now`
- `safe_to_request_audio_permission_now`
- `safe_to_read_user_audio_now`
- `safe_to_read_configs_local_now`
- `safe_to_read_secret_now`
- `safe_to_call_remote_asr_now`
- `safe_to_call_llm_now`
- `safe_to_download_models_now`
- `safe_to_write_runtime_audio_now`
- `safe_to_write_event_file_now`
- `safe_to_read_event_file_now`
- `safe_to_mutate_web_session_now`
- `safe_to_run_tauri_or_cargo_now`

## 6. Readiness Pointer 修正

PCWEB-097 已完成，因此 `/desktop/asr-worker-handoff-dry-run-readiness` 的下一步指针必须从：

```text
next_pcweb_id=PCWEB-097
expose_pcweb_096_status_in_web_tauri_noop_ui
```

修正为：

```text
next_pcweb_id=PCWEB-098
define_desktop_asr_worker_process_contract
```

## 7. TDD 验证

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_process_contract.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
9 failed
```

失败原因：

- 缺少 `asr-worker-process-contract.policy.json`
- 缺少 `desktop_asr_worker_process_contract.py`
- readiness endpoint 仍返回 `next_pcweb_id=PCWEB-097`

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_process_contract.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
9 passed, 2 warnings
```

## 8. 后续

PCWEB-098 完成后，下一步不应直接进入真实麦克风。后续主线仍按以下顺序推进：

1. ASR worker implementation approval/design。
2. synthetic/local event file handoff 继续作为无音频桥。
3. Tauri no-op run 需要工具链和明确审批边界。
4. mic adapter start/pause/resume/stop/delete 单独设计和测试。
5. 用户最终真实麦克风 shadow test。
