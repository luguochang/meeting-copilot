# PCWEB-100 Desktop ASR Worker Synthetic Lifecycle Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：在不启动真实 worker、不访问麦克风、不读写真实音频的前提下，把 PCWEB-099 command protocol 跑成一条可验证的 synthetic worker lifecycle，并在 `collect_events` 阶段复用 PCWEB-096 临时 Web handoff。  
> 边界：本计划不授权启动 worker、不授权访问麦克风或系统音频、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型、不授权运行 Cargo/Tauri、不授权写 runtime audio、不授权写 event file、不授权 mutate production Web session。

## 1. 目标

PCWEB-100 把 PCWEB-099 的静态 command envelope 往真实 worker 主线推进一步：在测试 harness 内按顺序应用 synthetic lifecycle。

执行顺序固定为：

```text
worker.prepare
  -> worker.start
  -> worker.collect_events
  -> worker.stop
  -> worker.cleanup
```

`collect_events` 阶段只允许读取测试放置在 `artifacts/tmp/asr_events` 下的 synthetic event JSON，并用 PCWEB-096 的 `synthetic_local_test` 路径写入 `artifacts/tmp/desktop_handoff_dry_run` 下的临时 Web data dir。

这一步不是 ASR worker 实现，也不是麦克风采集。

## 2. 产物

- `code/desktop_tauri/asr-worker-synthetic-lifecycle.policy.json`
- `tools/desktop_asr_worker_synthetic_lifecycle.py`
- `tests/test_desktop_asr_worker_synthetic_lifecycle.py`
- `docs/pcweb-100-desktop-asr-worker-synthetic-lifecycle-plan.md`

## 3. Harness 行为

合法输入：

- PCWEB-095 descriptor，且 `source_kind=synthetic`
- PCWEB-099 command requests，按固定五步顺序
- event file 位于 `artifacts/tmp/asr_events`
- temp Web data dir 位于 `artifacts/tmp/desktop_handoff_dry_run`

成功输出：

- `lifecycle_harness_status=synthetic_lifecycle_completed`
- `command_protocol_validation_status=passed`
- `synthetic_handoff_status=synthetic_web_handoff_passed`
- `final_worker_state=cleaned`
- `web_handoff_response_summary` 包含 transcript final count、suggestion card count 和 LLM 状态

阻断输出：

- mic/source kind 进入 `blocked_by_command_protocol`
- command 顺序错误进入 `blocked_by_lifecycle_sequence`
- missing/invalid event file 进入 `blocked_by_synthetic_handoff`
- policy drift 进入 `blocked_by_policy_validation`

## 4. Safety Flags

以下真实执行相关 flags 必须保持 false：

- `safe_to_spawn_worker_now`
- `safe_to_start_real_worker_now`
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
- `safe_to_mutate_production_web_session_now`
- `safe_to_run_tauri_or_cargo_now`

只有在 synthetic lifecycle 成功时，以下 narrow flags 可为 true：

- `safe_to_read_approved_asr_event_file_now`
- `safe_to_mutate_temp_web_session_now`

这两个 true 只表示测试 harness 读了 approved synthetic event file，并写入临时 Web data dir；不代表真实音频、真实 worker 或生产 session mutation。

## 5. TDD 验证

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_synthetic_lifecycle.py \
  -q -p no:cacheprovider
```

结果：

```text
8 failed
```

失败原因：

- 缺少 `asr-worker-synthetic-lifecycle.policy.json`
- 缺少 `desktop_asr_worker_synthetic_lifecycle.py`

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_synthetic_lifecycle.py \
  -q -p no:cacheprovider
```

结果：

```text
8 passed, 2 warnings
```

## 6. 后续

PCWEB-100 完成后仍不能直接进入真实麦克风。后续主线只能继续：

1. ASR worker implementation design/approval。
2. Tauri no-op run 的明确审批。
3. mic adapter start/pause/resume/stop/delete contract。
4. FunASR 本地模型目录或 DRV-019 模型下载审批后的 synthetic smoke。
5. 公开音频 planned samples no-download manifest。
