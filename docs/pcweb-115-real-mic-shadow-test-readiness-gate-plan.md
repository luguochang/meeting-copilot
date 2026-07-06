# PCWEB-115 Real Mic Shadow Test Readiness Gate Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把“现在能不能开始用户真实麦克风 shadow test”变成机器可测的静态前置门禁。  
> 边界：本计划不授权访问麦克风、不授权读取真实用户音频或 `.m4a`、不授权读取 `configs/local/`、不授权运行 Cargo/Tauri、不授权启动 worker、不授权调用远程 ASR/LLM、不授权下载公开音频或模型。

## 1. 背景

用户确认：完整计划必须写下来；转写验证先由我通过官方公开音频来源复核、合成音频、mock events 和模拟 replay 完成；最终真实麦克风会议由用户验证。

两个只读审查 Agent 的结论一致：计划层面已经完整，公开音频阶段正确停在 no-download blocked；真实麦克风 shadow test 的执行态缺口集中在：

- ASR 质量仍为 `requires_funasr_model_dir_or_drv019_approval`。
- 真实 Tauri WebView no-op run 尚未实际产生可摄入结果。
- worker mic source 仍为 `not_approved/not_executable`。
- 真实 mic adapter implementation 尚未开始。
- ASR worker 对真实 mic source 的实现和 smoke 尚未开始。

因此 PCWEB-115 不是继续扩大评测，而是把真实会议开始前的 go/no-go 判断收束成一个静态 gate。

## 2. 产物

- `code/desktop_tauri/real-mic-shadow-test-readiness.policy.json`
- `tools/real_mic_shadow_test_readiness_gate.py`
- `tests/test_real_mic_shadow_test_readiness_gate.py`

同步文档：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 3. 工具合同

`tools/real_mic_shadow_test_readiness_gate.py` 只组合已有 gate 和未来 caller-provided evidence：

- ASR quality decision gate：默认调用 DRV-032，当前必须保持 blocked。
- Tauri no-op evidence：未来真实 Tauri WebView no-op run 的观测结果，必须为 `validated_noop_ipc_observed`。
- Worker mic source approval：必须是 PCWEB-114 人工审批包之后的单次 shadow-test 手动批准证据。
- Mic adapter evidence：必须证明 `prepare/status/start/pause/resume/stop/delete_audio_chunks` 已实现并 smoke，且仍要求用户显式 start。
- ASR worker evidence：必须证明 `partial/final/revision/error/end_of_stream` event contract 和 Web handoff closure。
- Export/feedback evidence：默认复用 DRV-033 schema 和 DRV-036/037/038/039 链路，当前该项可视为 ready for future real report。

默认 CLI 输出：

```json
{
  "readiness_status": "blocked_not_ready_for_user_real_mic_shadow_test",
  "user_can_start_real_mic_shadow_test_now": false,
  "readiness_summary": {
    "asr_quality_ready": false,
    "real_tauri_noop_run_observed": false,
    "worker_mic_source_ready": false,
    "mic_adapter_ready": false,
    "asr_worker_ready": false,
    "export_feedback_ready": true
  },
  "blockers": [
    "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval",
    "real_tauri_noop_run_result_not_provided",
    "worker_mic_source_not_approved",
    "mic_adapter_real_implementation_not_available",
    "asr_worker_real_mic_source_not_available"
  ]
}
```

## 4. 安全边界

PCWEB-115 的 gate 自身永远不执行副作用。即使未来所有前置 evidence 满足，gate 也只会输出 `ready_for_user_manual_real_mic_shadow_test`，真实采集仍必须由用户在 UI 中显式 start。

所有 gate-side safety flags 保持 false：

- `safe_to_access_microphone_from_gate_now=false`
- `safe_to_enumerate_audio_devices_from_gate_now=false`
- `safe_to_request_audio_permission_from_gate_now=false`
- `safe_to_read_real_user_audio_from_gate_now=false`
- `safe_to_write_audio_chunk_from_gate_now=false`
- `safe_to_delete_audio_chunk_from_gate_now=false`
- `safe_to_spawn_worker_from_gate_now=false`
- `safe_to_run_tauri_or_cargo_from_gate_now=false`
- `safe_to_read_configs_local_from_gate_now=false`
- `safe_to_read_secret_from_gate_now=false`
- `safe_to_call_remote_asr_from_gate_now=false`
- `safe_to_call_llm_from_gate_now=false`
- `safe_to_download_models_from_gate_now=false`
- `safe_to_download_public_audio_from_gate_now=false`

`policy_path` 在读取前阻断：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- `.m4a`

2026-07-03 追加实现：CLI evidence input 已接入，真实 readiness 计算不再只能通过 Python 参数调用。`tools/real_mic_shadow_test_readiness_gate.py` 支持以下成对输入：

- `--asr-quality-report-json` / `--asr-quality-report-path`
- `--tauri-noop-evidence-json` / `--tauri-noop-evidence-path`
- `--worker-mic-source-approval-json` / `--worker-mic-source-approval-path`
- `--mic-adapter-evidence-json` / `--mic-adapter-evidence-path`
- `--asr-worker-evidence-json` / `--asr-worker-evidence-path`
- `--export-feedback-evidence-json` / `--export-feedback-evidence-path`

Evidence path 只允许 approved `artifacts/tmp/**` 下的 JSON 文件；在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。PCWEB-119 capture evidence shape 也已被 `_tauri_noop_ready()` 接受，但仍要求 validation passed、10 个 command returned、Tauri WebView run result 和所有 safety flags 安全。

## 5. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

失败原因：`code/desktop_tauri/real-mic-shadow-test-readiness.policy.json` 和 `tools/real_mic_shadow_test_readiness_gate.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider
```

结果：

```text
7 passed, 1 warning
```

默认 CLI：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_readiness_gate.py
```

结果：退出码 `1`，因为当前真实麦克风 shadow test 仍 blocked；输出 blocker 清单如第 3 节。

Evidence input 追加红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider
```

结果：

```text
3 failed, 9 passed, 1 warning
```

失败原因：PCWEB-115 CLI 尚不支持 evidence JSON/path 参数，且 `_tauri_noop_ready()` 尚不接受 PCWEB-119 capture evidence shape。

Evidence input 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider
```

结果：

```text
12 passed, 1 warning
```

## 6. 结论

完整计划已经写下，且 PCWEB-115 已把“何时能开始真实麦克风会议”变成可重复判断的 gate。

当前不能开始真实麦克风 shadow test。下一步不应继续新建同类 readiness/report-only 包装器，默认只在以下动作中选择：

- 真实 Tauri no-op run 的显式执行和结果摄入。
- ASR quality decision 的退出动作：提供 FunASR 本地模型目录、批准 DRV-019，或明确接受降级 pilot。
- 明确授权后的最小真实 mic adapter implementation boundary。
- 明确授权后的 real ASR worker mic source smoke。
