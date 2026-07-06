# PCWEB-099 Desktop ASR Worker Command Protocol Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：定义未来 desktop ASR worker sidecar 的命令 request/response envelope、生命周期 transition preview、blocked response 和安全边界。  
> 边界：本计划不授权启动 worker、不授权访问麦克风或系统音频、不授权读写 event file、不授权写 runtime audio、不授权调用 Web mutation、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型、不授权运行 Cargo/Tauri。

## 1. 目标

PCWEB-099 接在 PCWEB-098 process contract 之后。PCWEB-098 定义 worker 进程应该长什么样；PCWEB-099 定义未来桌面端如何用统一 command envelope 表达 `prepare/start/stop/health/collect_events/cleanup`，以及每个命令在当前阶段如何被 machine-checkable 地验证、阻断和报告。

这一步仍然是 `command_envelope_contract_only`，不是 worker 实现。

## 2. 产物

- `code/desktop_tauri/asr-worker-command-protocol.policy.json`
- `tools/desktop_asr_worker_command_protocol.py`
- `tests/test_desktop_asr_worker_command_protocol.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

## 3. Command Protocol 内容

固定主字段：

- `pcweb_id=PCWEB-099`
- `required_previous_contract=PCWEB-098`
- `policy_status=desktop_asr_worker_command_protocol_policy_only`
- `protocol_mode=command_envelope_contract_only`
- `protocol_version=desktop_asr_worker_command_protocol.v1`
- `worker_command_execution_status=not_executed`
- `transition_preview_status=specified_not_executed`
- `approved_event_output_root=artifacts/tmp/asr_events`
- `approved_runtime_root=artifacts/tmp/desktop_asr_worker_runtime`

future command catalog：

| Command | Allowed current state | Requested state after |
| --- | --- | --- |
| `worker.prepare` | `not_prepared` | `prepared` |
| `worker.start` | `prepared` | `running` |
| `worker.stop` | `running` | `stopped` |
| `worker.health` | `not_prepared/prepared/running/stopped/cleaned` | `unchanged` |
| `worker.collect_events` | `running/stopped` | `unchanged` |
| `worker.cleanup` | `stopped` | `cleaned` |

所有 command 当前 `safe_to_execute_now=false`。

## 4. Request / Response Envelope

caller-provided command request 必须包含：

- `protocol_version`
- `command_id`
- `request_id`
- `session_id`
- `worker_id`
- `source_kind`
- `current_state`
- `requested_state_after`
- `event_output_path`
- `runtime_root`

合法 request 只会得到 response preview：

- `accepted=false`
- `status=validated_not_executed`
- `worker_lifecycle_status=unchanged_not_executed`
- `errors=[]`

这表示协议形状可审查，但当前没有接受、执行或状态迁移。

## 5. 路径边界

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

## 6. Safety Flags

以下 flags 必须保持 false：

- `safe_to_execute_worker_command_now`
- `safe_to_accept_command_now`
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

## 7. Readiness Pointer 修正

PCWEB-098 已完成，因此 `/desktop/asr-worker-handoff-dry-run-readiness` 的下一步指针从：

```text
next_pcweb_id=PCWEB-098
define_desktop_asr_worker_process_contract
```

修正为：

```text
next_pcweb_id=PCWEB-099
define_desktop_asr_worker_command_protocol
```

## 8. TDD 验证

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_command_protocol.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
10 failed
```

失败原因：

- 缺少 `asr-worker-command-protocol.policy.json`
- 缺少 `desktop_asr_worker_command_protocol.py`
- readiness endpoint 仍返回 `next_pcweb_id=PCWEB-098`

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_command_protocol.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
10 passed, 2 warnings
```

## 9. 后续

PCWEB-099 完成后仍不能直接进入真实麦克风。后续主线继续按三条可停止工作推进：

1. 公开音频 AliMeeting/AISHELL-4 planned samples no-download manifest。
2. FunASR 本地模型目录或 DRV-019 模型下载审批后的一次 synthetic smoke。
3. ASR worker implementation design/approval、Tauri no-op run 或 mic adapter contract，均必须另起测试和决策。
