# PCWEB-113 Desktop Tauri No-op Run Result Intake Plan

> 日期：2026-07-03  
> 状态：Implemented as result-intake boundary  
> 范围：给未来显式批准的真实 Tauri WebView no-op run 增加结构化结果验收入口。  
> 边界：本文档不授权运行 Cargo/Tauri，不授权访问麦克风，不授权请求权限，不授权启动 worker，不授权读写 audio chunk 或 worker event file，不授权读取 `configs/local`、真实用户音频或 `.m4a`，不授权调用远程 ASR/LLM，不授权下载模型或公开音频。

## 1. 目的

PCWEB-091 已能生成未来 Tauri no-op shell smoke 的手动 packet，PCWEB-107 已静态绑定 10 个 no-op IPC command，PCWEB-109 已在 Web 工作台中调用浏览器 fallback / Tauri no-op IPC，PCWEB-112 已把 mic adapter start 与 worker mic source approval blocker 合并为同一 session 的合同门禁。

PCWEB-113 补齐的是“运行后证据入口”：当未来用户或另行批准的 runner 在 Tauri WebView 中真实调用 10 个 no-op IPC 后，系统需要一个可复跑工具来验证这些结果是否仍然是无副作用 no-op，而不是靠口头描述。

## 2. 交付物

- `code/desktop_tauri/tauri-noop-run-result-intake.policy.json`
- `tools/desktop_tauri_noop_run_result_intake.py`
- `tests/test_desktop_tauri_noop_run_result_intake.py`

同步文档：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 3. 结果合同

合法 result 必须是 caller-provided JSON，版本为 `desktop_tauri_noop_run_result.v1`，并包含：

- `run_environment=tauri_webview`
- `explicit_tauri_run_approval_recorded=true`
- `web_app_url_status=local_dev_url_loaded`
- `ipc_transport_status=tauri_ipc_available`
- 10 个 command result，且 command id/name 精确匹配 PCWEB-107 catalog

10 个 command：

- `runtime_get_status -> runtime.get_status`
- `session_prepare -> session.prepare`
- `asr_worker_health -> asr_worker.health`
- `mic_adapter_prepare -> mic_adapter.prepare`
- `mic_adapter_status -> mic_adapter.status`
- `mic_adapter_start -> mic_adapter.start`
- `mic_adapter_pause -> mic_adapter.pause`
- `mic_adapter_resume -> mic_adapter.resume`
- `mic_adapter_stop -> mic_adapter.stop`
- `mic_adapter_delete_audio_chunks -> mic_adapter.delete_audio_chunks`

每个 command 的 `result` 必须保持：

- `command_status=noop_bound`
- `implementation_status=noop_only`
- `transport_status=tauri_ipc_bound`
- `side_effect_status=none`
- `safe_to_invoke_noop=true`
- `safe_to_execute_real_action=false`
- `captures_audio=false`
- `spawns_process=false`
- `calls_remote_provider=false`
- `writes_local_files=false`

## 4. 阻断条件

以下情况会 blocked：

- 缺失任一 no-op command。
- command id/name 不匹配。
- 任一 command `invoke_status` 不是 `returned`。
- 额外 command，例如 `audio.capture_start`。
- 任一 side-effect 字段漂移为 true。
- 顶层或 command result 出现 raw `stdout/stderr/path/cwd/env/api_key/authorization/bearer_token` 等字段。
- `result_path` 不在 `artifacts/tmp/desktop_tauri_noop_run_results` 下。
- 输入路径指向 `.m4a`、`configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 或仓库外路径。

Blocked report 不回显原始路径、secret-like 值或 raw output。

## 5. 验收

TDD 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_run_result_intake.py -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

失败原因：`code/desktop_tauri/tauri-noop-run-result-intake.policy.json` 和 `tools/desktop_tauri_noop_run_result_intake.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_run_result_intake.py -q -p no:cacheprovider
```

结果：

```text
7 passed, 1 warning
```

## 6. 完成后的意义

PCWEB-113 只证明未来真实 Tauri no-op run 的结果有了机器可验收入口。它不代表 Tauri 已经运行，不代表麦克风权限已请求，不代表真实音频已采集，不代表 ASR worker 已启动，也不代表 worker mic source 已获批。

如果未来 result intake 通过，下一步才是 `worker mic source approval packet`；该审批仍不得自动启动 worker、访问麦克风或读写 audio chunk。
