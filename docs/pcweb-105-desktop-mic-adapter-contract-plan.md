# PCWEB-105 Desktop Microphone Adapter Contract Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：定义真实麦克风 adapter 前置合同，但不访问麦克风、不请求权限、不写真实音频。  
> 边界：本计划不授权运行 Tauri/Cargo、不授权访问麦克风、不授权请求系统音频权限、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型。

## 1. 目标

PCWEB-105 把真实麦克风会议前的 adapter 语义固定为可测试合同：

```text
prepare
  -> status
  -> start
  -> pause
  -> resume
  -> stop
  -> delete_audio_chunks
```

它只回答“未来真实麦克风 adapter 应该如何被调用、状态怎么流转、音频 chunk 只能写到哪里、删除语义是什么、哪些操作仍未获批”。它不做真实采集，也不接 ASR worker。

## 2. 新增文件

- `code/desktop_tauri/mic-adapter-contract.policy.json`
- `tools/desktop_mic_adapter_contract.py`
- `tests/test_desktop_mic_adapter_contract.py`

更新：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`

## 3. 合同字段

Policy 固定：

- `pcweb_id=PCWEB-105`
- `contract_version=desktop_mic_adapter_contract.v1`
- `adapter_execution_status=not_bound_not_executed`
- `permission_request_status=not_requested`
- `user_start_boundary=explicit_user_start_required_before_capture`
- `approved_runtime_audio_root=artifacts/tmp/desktop_mic_adapter_runtime`
- `approved_audio_chunk_root=artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`
- `delete_semantics=delete_audio_chunks_before_session_discard`

命令目录：

- `mic_adapter.prepare`
- `mic_adapter.status`
- `mic_adapter.start`
- `mic_adapter.pause`
- `mic_adapter.resume`
- `mic_adapter.stop`
- `mic_adapter.delete_audio_chunks`

所有命令当前都只返回 `accepted=false` / `validated_not_executed` 预览。

## 4. 用户 start 边界

`mic_adapter.start` 必须带：

```text
user_consent_state=explicit_user_start_granted
```

没有该字段时，工具返回：

```text
blocked_by_mic_command_request_validation
```

即使字段存在，当前也仍不执行 start，不请求权限，不采集音频。

## 5. 路径边界

允许：

- `artifacts/tmp/desktop_mic_adapter_runtime`
- `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`

拒绝：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- 仓库外路径
- symlink 逃逸

`file` 和 `system_audio` source kind 仍必须另起审批，不能借 PCWEB-105 进入真实文件音频或系统音频采集。

## 6. Web Readiness 更新

`GET /desktop/asr-worker-handoff-dry-run-readiness` 已从：

```text
next_pcweb_id=PCWEB-104
```

推进为：

```text
next_pcweb_id=PCWEB-105
mic_adapter_contract_status=not_defined
desktop_asr_handoff_safe_to_request_audio_permission=false
```

这只是主线指针更新，不代表麦克风 adapter 已绑定或真实采集已获批。

## 7. TDD Evidence

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_contract.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
8 failed, 2 warnings
```

失败原因：

- `code/desktop_tauri/mic-adapter-contract.policy.json` 不存在。
- `tools/desktop_mic_adapter_contract.py` 不存在。
- Web readiness 仍返回 `next_pcweb_id=PCWEB-104`。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_contract.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
8 passed, 2 warnings
```

默认 CLI：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_mic_adapter_contract.py
```

结果：

- exit code：`0`
- `mic_adapter_contract_status=specified_not_executable`
- `adapter_execution_status=not_bound_not_executed`
- `permission_request_status=not_requested`
- `audio_capture_status=not_started`
- `audio_chunk_write_status=not_written`
- `audio_chunk_delete_status=not_executed`

## 8. Safety Flags

默认保持全部 false：

- `safe_to_bind_mic_adapter_now=false`
- `safe_to_accept_mic_command_now=false`
- `safe_to_execute_mic_command_now=false`
- `safe_to_select_input_device_now=false`
- `safe_to_request_audio_permission_now=false`
- `safe_to_capture_audio_now=false`
- `safe_to_start_recording_now=false`
- `safe_to_pause_recording_now=false`
- `safe_to_resume_recording_now=false`
- `safe_to_stop_recording_now=false`
- `safe_to_write_audio_chunk_now=false`
- `safe_to_read_audio_chunk_now=false`
- `safe_to_delete_audio_chunks_now=false`
- `safe_to_read_user_audio_now=false`
- `safe_to_read_configs_local_now=false`
- `safe_to_read_secret_now=false`
- `safe_to_call_remote_asr_now=false`
- `safe_to_call_llm_now=false`
- `safe_to_download_models_now=false`
- `safe_to_mutate_web_session_now=false`
- `safe_to_run_tauri_or_cargo_now=false`

## 9. 后续

PCWEB-105 完成后，后续仍不能进入真实麦克风会议。下一步只能在明确审批边界下选择：

- 真实 Tauri no-op run / IPC smoke。
- mic adapter no-op UI panel。
- FunASR 本地模型目录或 DRV-019 审批后的 synthetic smoke。
- worker implementation / event stream 与 mic adapter 的受控连接设计。
