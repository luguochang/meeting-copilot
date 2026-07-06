# PCWEB-112 Desktop Worker/Mic Connector Contract Plan

> 日期：2026-07-03  
> 状态：Implemented with focused TDD  
> 主线节点：`mic adapter -> worker handoff preflight -> real mic shadow-test prerequisites`  
> 边界：本文档不授权访问麦克风、不请求音频权限、不枚举设备、不采集/读取/写入/删除真实 audio chunk、不启动 worker、不执行 worker command、不读写 worker event file、不 mutate Web session、不读取 `configs/local`、不调用远程 ASR/LLM、不下载公开音频或模型、不运行 Cargo/Tauri。

## 1. 目的

PCWEB-099 已定义 ASR worker command protocol，PCWEB-105 已定义 mic adapter command contract，PCWEB-107/109 已把 mic adapter no-op IPC/UI surface 做出来。PCWEB-112 补的是两者之间的组合门禁：把 `mic_adapter.start` 的显式用户 start 预览，和 future `worker.prepare(source_kind=mic)` 的审批阻塞，放进同一份 connector report。

它不是新的 report-only 横向扩散，也不是真实采集。它只回答一个主线问题：在进入真实麦克风 shadow test 前，mic adapter 合同和 ASR worker 合同是否能以同一 session 对齐，并且是否仍清楚地阻断真实执行边界。

## 2. 范围

新增：

- `code/desktop_tauri/worker-mic-connector-contract.policy.json`
- `tools/desktop_worker_mic_connector_contract.py`
- `tests/test_desktop_worker_mic_connector_contract.py`

复用：

- PCWEB-099 `tools/desktop_asr_worker_command_protocol.py`
- PCWEB-105 `tools/desktop_mic_adapter_contract.py`
- PCWEB-107/109 mic adapter no-op IPC/UI surface 作为前置事实
- DRV-039 pilot bundle runner 作为真实会议后 feedback/export 后置闭环

## 3. 合同

输入 `connector_request`：

```json
{
  "connector_version": "desktop_worker_mic_connector_contract.v1",
  "connector_id": "worker_mic_connector_review",
  "session_id": "worker_mic_connector_review",
  "adapter_id": "worker_mic_connector_review",
  "worker_id": "worker_mic_connector_review",
  "mic_source_kind": "mic",
  "worker_source_kind": "mic",
  "mic_command_id": "mic_adapter.start",
  "worker_command_id": "worker.prepare",
  "mic_current_state": "prepared",
  "worker_current_state": "not_prepared",
  "user_consent_state": "explicit_user_start_granted",
  "runtime_audio_root": "artifacts/tmp/desktop_mic_adapter_runtime",
  "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
  "worker_runtime_root": "artifacts/tmp/desktop_asr_worker_runtime/worker-mic-connector-review",
  "worker_event_output_path": "artifacts/tmp/asr_events/worker-mic-connector-review.events.json"
}
```

规则：

- `session_id`、`adapter_id`、`worker_id` 必须一致。
- mic side 只允许 `mic_adapter.start`，并且 `user_consent_state` 必须是 `explicit_user_start_granted`。
- worker side 只允许 `worker.prepare`，但 `source_kind=mic` 仍由 PCWEB-099 返回 `source_kind requires later approval: mic`。
- connector 可以进入 `ready_for_worker_mic_connector_contract_review`，但执行状态仍是 `not_bound_not_executed`。
- `file` / `system_audio` source kinds 仍 blocked。
- 所有安全动作 flags 必须为 false。

## 4. 输出

成功的组合门禁输出：

- `worker_mic_connector_status=ready_for_worker_mic_connector_contract_review`
- `mic_command_request_preview.status=validated_not_executed`
- `worker_command_request_preview.source_kind=mic`
- `worker_command_blocker=source_kind requires later approval: mic`
- `connector_readiness_summary.next_required_decision=approve_worker_mic_source_after_real_tauri_noop_run`

默认无请求输出：

- `worker_mic_connector_status=specified_not_executable`
- mic/worker preview 均为 `null`

Blocked 输出：

- 无显式用户 start：`blocked_by_connector_request_validation`
- session/adapter/worker id 不一致：`blocked_by_connector_request_validation`
- forbidden roots：`blocked_by_connector_request_validation` 且路径 redacted
- policy 试图打开任何 side effect flag：`blocked_by_policy_validation`

## 5. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_connector_contract.py -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

原因：`code/desktop_tauri/worker-mic-connector-contract.policy.json` 和 `tools/desktop_worker_mic_connector_contract.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_connector_contract.py -q -p no:cacheprovider
```

结果：

```text
7 passed, 1 warning
```

## 6. 验收边界

PCWEB-112 完成后，项目更接近真实麦克风 shadow test 的前置闭环：mic adapter 和 worker command protocol 不再是完全分散的合同，而是可用同一 session 做组合校验。

它仍不代表：

- 真实 Tauri WebView 已运行。
- 麦克风权限已请求或可请求。
- 真实麦克风音频已采集。
- worker 已启动或可启动。
- worker 能读取 audio chunk 或写 event file。
- Web Live ASR session 已被真实 worker mutation。
- ASR 中文技术实体质量已达标。

## 7. 下一步

PCWEB-112 后不应继续做同类 no-execution connector wrapper。下一步应在以下路径中选择：

- `Real Tauri no-op run`：实际运行 Tauri WebView，验证已有 no-op IPC 可调用。
- `worker mic source approval packet`：在真实 Tauri no-op run 后，定义 worker `source_kind=mic` 的人工审批包。
- `ASR quality decision exit`：FunASR 本地模型目录、DRV-019 审批、可选远程 ASR 对照或降级取舍。
