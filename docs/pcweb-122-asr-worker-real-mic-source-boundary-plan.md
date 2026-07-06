# PCWEB-122 ASR Worker Real Mic Source Boundary Plan

> 日期：2026-07-03  
> 状态：Implemented with TDD  
> 主线节点：desktop runtime / ASR worker mic source boundary  
> 边界：本计划不授权访问麦克风、不请求音频权限、不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk、不读写 event file、不读取真实用户音频、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载模型或公开音频、不运行 Tauri dev/build。

## 1. 目的

PCWEB-115 readiness gate 已经要求一份 ASR worker real mic source evidence，字段包括：

- `implementation_status=implemented_and_smoke_tested`
- `event_contract_status=partial_final_revision_error_end_of_stream_supported`
- `worker_output_root=artifacts/tmp/asr_events`
- `web_handoff_status=closed_to_evidence_state_gap`
- 上游安全 flags 全部不能为 true

PCWEB-122 的目标是补齐这份 evidence 的静态实现边界，证明桌面侧已经有可编译、可校验、可追踪的 ASR worker mic source boundary。它不证明真实 worker 可以启动，也不证明 ASR 质量已经达标。

## 2. 实现内容

新增文件：

- `tests/test_desktop_asr_worker_real_mic_source_boundary.py`
- `tools/desktop_asr_worker_real_mic_source_boundary.py`
- `code/desktop_tauri/asr-worker-real-mic-source-boundary.policy.json`
- `code/desktop_tauri/src-tauri/src/asr_worker_mic_source_runtime.rs`

修改文件：

- `code/desktop_tauri/src-tauri/src/lib.rs`

实现约束：

- Rust module 只定义 inert `AsrWorkerMicSourceBoundaryEvidence` 和 `boundary_evidence()`。
- `lib.rs` 只暴露 `pub mod asr_worker_mic_source_runtime;`，不新增 Tauri command。
- Python tool 只读取 policy 和 Rust source，不执行外部命令，不启动 worker，不访问麦克风。
- 输出 evidence 与 `tools/real_mic_shadow_test_readiness_gate.py` 的 `_asr_worker_ready()` 兼容。

## 3. TDD 验证

Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_real_mic_source_boundary.py \
  -q -p no:cacheprovider
```

结果：

```text
6 failed, 1 warning
```

失败原因：PCWEB-122 policy、Rust module 和 tool 不存在。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_real_mic_source_boundary.py \
  -q -p no:cacheprovider
```

结果：

```text
6 passed, 1 warning
```

审查加固 Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_real_mic_source_boundary.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  -q -p no:cacheprovider
```

结果：

```text
4 failed, 13 passed, 1 warning
```

失败原因：

- CLI 不接受 `--rust-module-path` / `--lib-rs-path`，无法验证 Rust boundary 失败时的退出码。
- Tool 的 Rust validator 没有阻断 `cpal` 等 forbidden snippets。
- Policy path guard 没有显式阻断 Voice Memos 风格路径。
- PCWEB-115 `_asr_worker_ready()` 只校验最小字段，未要求完整 PCWEB-122 evidence shape。

审查加固 Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_real_mic_source_boundary.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  -q -p no:cacheprovider
```

结果：

```text
17 passed, 1 warning
```

Focused integration：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_real_mic_source_boundary.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  tests/test_desktop_mic_adapter_implementation_boundary.py \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py \
  -q -p no:cacheprovider
```

结果：

```text
28 passed, 1 warning
```

## 4. Readiness 影响

PCWEB-122 evidence 能让：

```python
real_mic_shadow_test_readiness_gate._asr_worker_ready(evidence) is True
```

并在向 PCWEB-115 提供该 evidence 时移除：

```text
asr_worker_real_mic_source_not_available
```

仍然保留的真实会议阻塞项：

- `asr_quality_decision_requires_funasr_model_dir_or_drv019_approval`
- `worker_mic_source_not_approved`

PCWEB-122 不代表真实麦克风 shadow test 已经 ready。

## 5. 后续主线

PCWEB-122 后不得继续新增同类 ASR worker boundary/readiness wrapper。下一步只允许在以下方向推进：

1. ASR quality exit：提供 FunASR 本地模型目录、批准 DRV-019 模型下载、选择可选远程 ASR 对照，或明确接受降级 pilot。
2. Worker mic source approval：只在用户显式批准单次 shadow test 后，将 PCWEB-114/120 的 `not_approved` 推进到单次人工批准。
3. 用户最终真实麦克风 shadow test：只能在 PCWEB-115 readiness gate 全部满足后，由用户在 UI 中显式启动。
