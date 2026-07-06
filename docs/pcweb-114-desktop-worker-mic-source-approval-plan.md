# PCWEB-114 Desktop Worker Mic Source Approval Packet Plan

> 日期：2026-07-03  
> 状态：Implemented as manual approval packet boundary  
> 范围：把 PCWEB-112 worker/mic connector contract 和 PCWEB-113 Tauri no-op run result intake 合成一个人工审批包。  
> 边界：本文档不授权访问麦克风，不授权请求权限，不授权启动 worker，不授权执行 `worker.prepare(source_kind=mic)`，不授权读写或删除 audio chunk，不授权读写 worker event file，不授权运行 Cargo/Tauri，不授权读取 `configs/local`、真实用户音频或 `.m4a`，不授权调用远程 ASR/LLM，不授权下载模型或公开音频。

## 1. 目的

PCWEB-112 已证明同一 session 下的 `mic_adapter.start` preview 和 `worker.prepare(source_kind=mic)` blocker 可以被组合校验。PCWEB-113 已证明未来显式批准的真实 Tauri WebView no-op run 结果可以被机器验收。

PCWEB-114 补齐的是“人工审批前的证据包”：只有当 connector request 通过、worker 侧仍明确保留 `source_kind requires later approval: mic` blocker、Tauri no-op result 通过、且二者属于同一 session/run id 时，工具才输出 `ready_for_manual_review_not_executable`。该输出仍不是执行许可，只是把后续是否允许 worker 接受 `source_kind=mic` 的人工决策材料归档为结构化 JSON。

## 2. 交付物

- `code/desktop_tauri/worker-mic-source-approval.policy.json`
- `tools/desktop_worker_mic_source_approval.py`
- `tests/test_desktop_worker_mic_source_approval.py`

同步文档：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 3. 输入合同

合法输入必须同时满足：

- PCWEB-112 connector request 通过：
  - `connector_version=desktop_worker_mic_connector_contract.v1`
  - `session_id=adapter_id=worker_id`
  - `mic_source_kind=mic`
  - `worker_source_kind=mic`
  - `mic_command_id=mic_adapter.start`
  - `worker_command_id=worker.prepare`
  - `user_consent_state=explicit_user_start_granted`
  - mic runtime/audio chunk root 和 worker runtime/event root 均在 approved roots 下
- PCWEB-112 worker side 仍保留 blocker：
  - `source_kind requires later approval: mic`
- PCWEB-113 Tauri no-op result 通过：
  - `run_result_version=desktop_tauri_noop_run_result.v1`
  - `run_environment=tauri_webview`
  - `explicit_tauri_run_approval_recorded=true`
  - `web_app_url_status=local_dev_url_loaded`
  - `ipc_transport_status=tauri_ipc_available`
  - PCWEB-107 的 10 个 no-op IPC command 全部 returned，且 side-effect flags 全 false
- `tauri_run_result.run_id` 必须等于 connector `session_id`，防止把不同 session 的 Tauri 证据和 worker/mic request 混用。

## 4. 输出合同

合法输入输出：

- `worker_mic_source_approval_packet_status=ready_for_manual_review_not_executable`
- `worker_mic_source_approval_status=not_approved`
- `approval_scope=allow_worker_prepare_source_kind_mic_after_manual_approval`
- `approved_to_execute_now=false`
- `safe_to_accept_worker_mic_source_now=false`
- `manual_review_packet.next_required_decision=manual_approve_worker_prepare_source_kind_mic_or_keep_blocked`

阻断条件：

- 缺少 connector request 或 Tauri no-op result。
- connector request 未通过，包括没有显式 user start、session/adapter/worker 不一致、source 不是 `mic`、路径指向 forbidden root。
- Tauri no-op result 未通过，包括 command 缺失、失败、额外 command、side-effect drift、raw output/path/secret 字段。
- Tauri `run_id` 与 connector `session_id` 不一致。
- policy 中任何执行/音频/worker/网络/模型/Cargo/Tauri safety flag 被改成 true。
- `policy_path` 指向 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 或音频文件路径。

Blocked report 不回显完整 forbidden path、secret-like 值或 raw command output。

## 5. 验收

TDD 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_approval.py -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

失败原因：`code/desktop_tauri/worker-mic-source-approval.policy.json` 和 `tools/desktop_worker_mic_source_approval.py` 不存在。

路径守卫追加红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_approval.py::test_policy_path_rejects_forbidden_roots_before_reading -q -p no:cacheprovider
```

结果：

```text
1 failed, 1 warning
```

失败原因：`policy_path` 指向 `configs/local` 时，旧实现会尝试读取文件，而不是在读取前返回 blocked report。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_approval.py -q -p no:cacheprovider
```

结果：

```text
8 passed, 1 warning
```

## 6. 完成后的意义

PCWEB-114 只把 worker mic source 的人工审批证据包变成可复跑、可审计、可阻断的结构化工具。它不代表真实 Tauri WebView 已被本轮运行，不代表麦克风权限已请求，不代表真实音频已采集，不代表 worker 已启动，也不代表 `source_kind=mic` 已经被实际批准或执行。

下一步必须在以下动作中继续收敛，而不是回到泛化评测：

- 明确授权后执行真实 Tauri no-op run，并把 result 喂给 PCWEB-113/114。
- 做真实麦克风 shadow-test 前置清单。
- 处理 ASR quality decision 的退出动作。
- 如果明确进入真实采集实现，另起审批和 TDD，先实现最小用户显式 start/pause/resume/stop/delete 链路。
