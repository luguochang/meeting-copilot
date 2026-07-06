# PCWEB-101 Desktop ASR Worker Implementation Approval Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：在 PCWEB-098/099/100 之后，定义真实 desktop ASR worker implementation 的人工审批包、模块边界、provider/source 边界、资源预算和 no-execution safety flags。  
> 边界：本计划不授权实现真实 worker、不授权启动 worker、不授权访问麦克风或系统音频、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型、不授权运行 Cargo/Tauri、不授权写 runtime audio、不授权读写 event file、不授权 mutate Web session。

## 1. 目标

PCWEB-100 已证明 synthetic command lifecycle 可以把 approved synthetic event file 导入临时 Web data dir，但它仍不是真实 worker。

PCWEB-101 的目标是把下一步真实 worker implementation 前的审批边界变成可测试合同：

```text
PCWEB-098 process contract
  + PCWEB-099 command protocol
  + PCWEB-100 synthetic lifecycle harness
  -> PCWEB-101 implementation approval packet
  -> later explicit worker implementation plan
```

这一步只回答：

- 未来 worker entrypoint 和 command runner 应落在哪些代码根。
- 未来 event writer 只能写到哪个 approved root。
- runtime root、资源预算、cleanup 和 provider mode 怎么声明。
- 哪些 provider/source 现在只能 preview，哪些必须另行审批。
- 即使 caller 传齐 approval tokens，也仍不能执行。

## 2. 产物

- `code/desktop_tauri/asr-worker-implementation-approval.policy.json`
- `tools/desktop_asr_worker_implementation_approval.py`
- `tests/test_desktop_asr_worker_implementation_approval.py`
- `docs/pcweb-101-desktop-asr-worker-implementation-approval-plan.md`

同时更新：

- Web readiness endpoint：`next_pcweb_id=PCWEB-101`
- `README.md`
- `code/desktop_tauri/README.md`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 3. Approval Packet 行为

默认报告：

- `implementation_approval_status=implementation_approval_required`
- `implementation_packet_status=not_ready`
- `implementation_status=not_implemented`
- `worker_execution_status=not_executed`
- `next_action=submit_bounded_worker_implementation_approval_packet`

合法 approval packet 只会变成人工 review preview：

- `approval_packet_validation_status=passed`
- `implementation_approval_status=ready_for_manual_review_not_executable`
- `implementation_packet_status=preview_ready`
- `approved_to_implement_now=false`
- `approved_to_execute_now=false`

允许 preview 的 provider/source：

- `provider_mode=mock_streaming`
- `provider_mode=sherpa_onnx_streaming`
- `source_kind=synthetic`

必须另行审批或阻断：

- `provider_mode=funasr_streaming`：需要本地模型目录或 DRV-019。
- `provider_mode=remote_asr|remote_llm_asr`：默认 forbidden。
- `source_kind=mic|file|system_audio`：需要后续 mic/file/system audio adapter 审批。

## 4. Safety Flags

以下 flags 必须保持 false：

- `safe_to_implement_worker_now`
- `safe_to_execute_worker_now`
- `safe_to_spawn_worker_now`
- `safe_to_start_worker_now`
- `safe_to_capture_audio_now`
- `safe_to_request_audio_permission_now`
- `safe_to_read_user_audio_now`
- `safe_to_read_configs_local_now`
- `safe_to_read_secret_now`
- `safe_to_call_remote_asr_now`
- `safe_to_call_llm_now`
- `safe_to_download_models_now`
- `safe_to_run_modelscope_now`
- `safe_to_write_runtime_audio_now`
- `safe_to_write_event_file_now`
- `safe_to_read_event_file_now`
- `safe_to_mutate_web_session_now`
- `safe_to_run_tauri_or_cargo_now`

## 5. TDD 验证

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_implementation_approval.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
10 failed, 2 warnings
```

失败原因：

- 缺少 `asr-worker-implementation-approval.policy.json`
- 缺少 `desktop_asr_worker_implementation_approval.py`
- readiness endpoint 仍返回 `next_pcweb_id=PCWEB-099`

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_implementation_approval.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：

```text
10 passed, 2 warnings
```

## 6. 后续

PCWEB-101 完成后，仍不能直接访问麦克风、运行真实 worker 或下载模型。后续必须另起计划和 TDD，只能在以下方向中选择：

1. PCWEB-102 no-execution worker skeleton / module boundary。
2. Tauri no-op local run 的明确审批与执行边界。
3. mic adapter start/pause/resume/stop/delete contract。
4. FunASR 本地模型目录或 DRV-019 审批后的 synthetic smoke。
5. 公开音频 planned samples no-download manifest。
