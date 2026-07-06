# PCWEB-120 Worker Mic Source From Tauri Evidence Plan

> 日期：2026-07-03  
> 状态：Accepted / Executed  
> 目的：把 PCWEB-119 真实 Tauri no-op WebView evidence 转成同 session worker mic source manual review packet，复用 PCWEB-114 的审批包规则，而不启动 worker、不访问麦克风、不批准真实采集。  
> 边界：本文档不授权访问麦克风、不请求音频权限、不启动 ASR worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk、不读取 `configs/local/`、不读取真实用户音频或 `.m4a`、不调用远程 ASR/LLM、不下载模型或公开音频。

## 1. 背景

PCWEB-119 已证明真实 Tauri WebView 可以调用 10 个 no-op IPC command，并把 validation passed evidence 写入 ignored artifact root。但 PCWEB-114 的 worker mic source approval packet 仍需要 caller 同时提供 connector request 和 Tauri run result。为了避免后续人工复制 JSON 或重新包装 readiness，本轮新增一个薄桥接：

```text
PCWEB-119 capture evidence
  -> validate capture / validation / run_result
  -> derive same-session PCWEB-112 connector request
  -> call PCWEB-114 worker mic source approval packet
  -> ready_for_manual_review_not_executable
```

## 2. 设计

新增：

- `code/desktop_tauri/worker-mic-source-from-tauri-evidence.policy.json`
- `tools/desktop_worker_mic_source_from_tauri_evidence.py`
- `tests/test_desktop_worker_mic_source_from_tauri_evidence.py`

工具只做三件事：

1. 校验 evidence path 只能位于 `artifacts/tmp/desktop_tauri_noop_run_results`，并在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和 `.m4a`。
2. 校验 PCWEB-119 evidence 必须是 `captured_validated_tauri_noop_run`，`run_environment=tauri_webview`，validation passed，10 个 command returned，并且所有 audio / worker / remote / secret safety flags 为 false。
3. 用 evidence `run_result.run_id` 派生同 session connector request，并调用 PCWEB-114 `build_worker_mic_source_approval_report`。

`connector_consent_scope` 固定为：

```text
tauri_noop_ipc_only_not_real_audio_capture
```

这表示 evidence 只证明 no-op IPC 运行过，不代表用户已经授权真实音频采集。

## 3. 自测结果

TDD red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py \
  -q -p no:cacheprovider
```

结果：

```text
5 failed, 1 warning
```

失败原因：PCWEB-120 policy/tool 尚不存在。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py \
  -q -p no:cacheprovider
```

结果：

```text
5 passed, 1 warning
```

真实 PCWEB-119 evidence CLI run：

```bash
latest=$(ls -t artifacts/tmp/desktop_tauri_noop_run_results/*.pcweb-119-tauri-noop-run-validation.json | head -n 1)
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_worker_mic_source_from_tauri_evidence.py \
  --tauri-evidence-path "$latest" \
  > artifacts/tmp/desktop_tauri_noop_run_results/pcweb-120-worker-mic-source-from-tauri-evidence.report.json
```

摘要：

```json
{
  "pcweb_id": "PCWEB-120",
  "policy_validation_status": "passed",
  "tauri_evidence_validation_status": "passed",
  "tauri_evidence_status": "captured_validated_tauri_noop_run",
  "derived_connector_request_status": "derived_same_session_connector_request",
  "worker_mic_source_approval_packet_status": "ready_for_manual_review_not_executable",
  "worker_mic_source_approval_status": "not_approved",
  "next_required_decision": "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked",
  "safe_to_capture_audio_now": false,
  "safe_to_start_worker_now": false
}
```

## 4. 已证明

- PCWEB-119 真实 Tauri evidence 可以被机器读取和校验。
- Evidence `run_id` 可以派生为同 session connector request。
- PCWEB-112 connector request 和 PCWEB-119 Tauri evidence 可以通过 PCWEB-114 合成 manual review packet。
- Packet 状态是 `ready_for_manual_review_not_executable`，不是执行授权。

## 5. 未证明

- 不代表 worker mic source 已批准。
- 不代表真实 `worker.prepare(source_kind=mic)` 可以执行。
- 不代表真实 mic adapter 已实现。
- 不代表麦克风权限已请求或可用。
- 不代表真实音频已采集、写入或删除。
- 不代表 ASR worker real mic source 已实现。
- 不代表 ASR 中文技术实体质量已达标。

## 6. 下一步

PCWEB-120 后，真实麦克风 readiness 的 worker mic source blocker 仍未消除，因为 approval status 仍是 `not_approved`。下一步只能在以下方向推进：

- 明确审批后的最小真实 mic adapter implementation boundary。
- ASR quality exit：FunASR 本地模型目录、DRV-019 审批、可选远程 ASR 对照或降级决策。
- 后续如要把 worker mic source 从 `not_approved` 变成单次 shadow-test approval，必须另起可审计 policy，并仍保持不访问麦克风、不启动 worker，直到真实 adapter 和 worker source smoke 均具备。
