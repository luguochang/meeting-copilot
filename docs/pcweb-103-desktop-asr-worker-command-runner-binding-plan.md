# PCWEB-103 Desktop ASR Worker Command Runner Binding Plan

日期：2026-07-03

状态：Implemented

## 1. 目标

PCWEB-103 的目标是把 PCWEB-102 的 no-execution sidecar skeleton 推进到 desktop command runner binding 边界，但仍只允许静态预览和校验。

本阶段只证明未来绑定形态可审查：

```text
PCWEB-102 sidecar module boundary
  -> PCWEB-103 command runner binding preview
  -> Web readiness next pointer PCWEB-103
```

PCWEB-103 不实现 Rust command runner，不绑定 Tauri command，不 invoke IPC，不 spawn Python，不 dispatch worker command，不探测 health，不 collect event file，不读写 runtime audio 或 event file。

## 2. 产物

- `code/desktop_tauri/asr-worker-command-runner-binding.policy.json`
- `tools/desktop_asr_worker_command_runner_binding.py`
- `tests/test_desktop_asr_worker_command_runner_binding.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`

## 3. Policy

`asr-worker-command-runner-binding.policy.json` 固定：

- `pcweb_id=PCWEB-103`
- `required_previous_contracts=["PCWEB-102"]`
- `binding_mode=command_runner_binding_preview_only`
- `execution_mode=no_execution`
- `binding_version=desktop_asr_worker_command_runner_binding.v1`
- `command_runner_binding_status=specified_not_executable`
- `native_command_runner_status=path_reserved_not_bound`
- `sidecar_module_path=code/asr_runtime/scripts/asr_worker_sidecar.py`
- `future_native_command_runner_path=code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- `command_transport_preview=stdio_jsonl`
- `command_catalog=worker.prepare|worker.start|worker.health|worker.collect_events|worker.stop|worker.cleanup`
- `approved_event_output_root=artifacts/tmp/asr_events`
- `approved_runtime_root=artifacts/tmp/desktop_asr_worker_runtime`

当前只允许 `mock_streaming` 或 `sherpa_onnx_streaming` + `synthetic` 做 preview。`funasr_streaming` 仍需要本地模型目录或 DRV-019，`remote_asr` 和 `remote_llm_asr` forbidden，`mic|file|system_audio` 后续另行审批。

## 4. Binding Request

合法 binding request 必须提供：

- `binding_version`
- `binding_id`
- `sidecar_module_path`
- `native_command_runner_path`
- `command_transport`
- `command_catalog`
- `provider_mode`
- `source_kind`
- `event_output_root`
- `runtime_root`

校验通过时只返回：

- `command_runner_binding_status=ready_for_no_execution_binding_review`
- `future_native_command_preview.binding_status=validated_not_bound`
- `command_dispatch_status=not_dispatched`
- `tauri_ipc_status=not_invoked`
- `process_spawn_status=not_spawned`
- `health_probe_status=not_executed`
- `event_collection_status=not_executed`
- `worker_execution_status=not_executed`

这不是执行批准，也不是 Tauri 绑定批准。

## 5. Safety Flags

以下执行面必须保持 false：

- command runner bind/execute
- worker command accept/dispatch/execute
- process spawn/subprocess
- worker start/stop/health/collect events
- Tauri command bind / IPC invoke / Cargo/Tauri run
- audio capture / permission / user audio read
- configs/local / secret read
- provider import/execute / model import/download / ModelScope
- event file read/write / runtime audio write
- Web session mutation
- remote ASR/LLM

## 6. Path Boundary

允许路径：

- `code/asr_runtime/scripts/asr_worker_sidecar.py`
- `code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- `artifacts/tmp/asr_events`
- `artifacts/tmp/desktop_asr_worker_runtime`

禁止路径：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- 仓库外绝对路径
- symlink 逃逸

非法路径在 report 中显示为 `<redacted_invalid_path>`。

## 7. Web Readiness

`GET /desktop/asr-worker-handoff-dry-run-readiness` 的 next pointer 推进到：

```text
next_pcweb_id=PCWEB-103
define_desktop_asr_worker_command_runner_binding
```

同时新增 readiness 字段：

- `command_runner_binding_status=not_bound`
- `command_runner_execution_status=not_executed`
- `desktop_asr_handoff_safe_to_bind_command_runner=false`
- `desktop_asr_handoff_safe_to_dispatch_worker_command=false`
- `desktop_asr_handoff_safe_to_run_subprocess=false`
- `desktop_asr_handoff_safe_to_invoke_tauri_ipc=false`

blocker 新增 `command_runner_binding_not_approved`。

## 8. TDD 证据

红灯 1：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_command_runner_binding.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：9 failed, 2 warnings。原因是 PCWEB-103 policy/tool 不存在，Web readiness 仍指向 PCWEB-102。

红灯 2：

同一命令结果：4 failed, 5 passed, 2 warnings。原因是新增 runner/dispatch/Tauri IPC/subprocess/health/collect events safety fields 尚未实现。

绿灯：

同一命令结果：12 passed, 2 warnings。后续补充 `main(argv=None)` CLI 回归、no-request validation status、reserved Tauri command preview 字段，以及 `--policy` / `--binding-request` 输入文件读取前 forbidden path guard。

## 9. 后续边界

PCWEB-103 后仍不能直接进入真实 worker execution。下一步只能在新决策和 TDD 下选择：

- PCWEB-104 command runner implementation skeleton / no-dispatch boundary。
- Tauri no-op run approval / smoke。
- mic adapter contract。
- FunASR 本地模型目录或 DRV-019 后的 synthetic smoke。
- public audio no-download sample manifest。

任何真实 binding implementation、Tauri IPC invoke、subprocess runner、worker command dispatch、health probe、event file read/write、麦克风访问、模型下载、远程 ASR/LLM 或 Cargo/Tauri run 都必须另起审批。
