# PCWEB-096 Desktop ASR Worker Handoff Local Dry Run Plan

> 日期：2026-07-03  
> 状态：Accepted as implemented local dry-run boundary  
> 范围：把 PCWEB-095 desktop worker descriptor preflight 与 Web `/live/asr/local-event-files/sessions` handoff API 串成可本地自测的 dry-run。  
> 边界：不启动 worker、不访问麦克风、不请求权限、不读取真实用户音频、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载模型、不写 runtime audio。

## 1. 目标

PCWEB-095 已能生成 future Web handoff request preview，但还没有证明该 preview 可以真实进入 Web Live ASR handoff API。PCWEB-096 增加一个窄工具：

```text
desktop ASR worker descriptor
  -> PCWEB-095 preflight
  -> future_web_handoff_request_preview
  -> optional synthetic_local_test against Web TestClient
  -> temp Live ASR session summary
```

默认模式仍是 `preview_only`，只生成预览，不读取事件文件、不调用 Web API、不写 session。只有显式 `mode=synthetic_local_test` 且 descriptor/source_kind/data_dir 均在白名单内，才允许 Web TestClient 读取 `artifacts/tmp/asr_events` 下的 synthetic ASR event file，并把结果写入 `artifacts/tmp/desktop_handoff_dry_run` 下的临时 data dir。

## 2. 新增文件

- `code/desktop_tauri/asr-worker-handoff-local-dry-run.policy.json`
- `tools/desktop_asr_worker_handoff_local_dry_run.py`
- `tests/test_desktop_asr_worker_handoff_local_dry_run.py`

## 3. 模式

### `preview_only`

- 运行 PCWEB-095 descriptor preflight。
- 返回 `future_web_handoff_request_preview`。
- `event_file_read_status=not_read`。
- `web_handoff_mutation_status=not_mutated`。
- `safe_to_read_approved_asr_event_file_now=false`。
- `safe_to_mutate_temp_web_session_now=false`。

### `synthetic_local_test`

- 只接受 `source_kind=synthetic` 或 `preflight_only` 的 descriptor。
- 只允许 event file path 位于 `artifacts/tmp/asr_events`。
- 只允许 data dir 位于 `artifacts/tmp/desktop_handoff_dry_run`。
- 用 FastAPI `TestClient` 调用现有 Web handoff API。
- 成功时只返回摘要：session id、ingest mode、provider、`transcript_final` count、`suggestion_card` count、LLM status。
- 如果 Web handoff 返回 422，dry-run 必须返回 `blocked_by_web_handoff_response`，并保持读取/临时 session mutation 成功 flags 为 false。
- 如果自定义 policy 将任何 worker/audio/remote/model false flag 改为 true，dry-run 必须返回 `blocked_by_policy_validation`。
- CLI 遇到任何 `blocked_*` dry-run status 必须返回非 0，避免自动化脚本误判成功。

## 4. 明确不做

- 不启动 desktop ASR worker。
- 不访问麦克风或系统音频。
- 不请求音频权限。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local`、provider config、API key、环境密钥或 keychain。
- 不调用远程 ASR、LLM 或中转站。
- 不下载 FunASR/ModelScope 模型。
- 不写 runtime audio。
- 不运行 Tauri、Cargo、package manager 或 shell command。

## 5. 验收

Focused:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_handoff_preflight.py \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py \
  -q -p no:cacheprovider
```

Full local:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

## 6. 下一步

PCWEB-096 让桌面 worker handoff 主线前进到“descriptor preview 可以被 Web API 消费”的本地证据。下一步仍不是麦克风；下一步应在 Tauri no-op shell 或 Web UI 中展示/触发该 dry-run 状态，或者在获得 FunASR 本地模型目录/审批后替换 synthetic event file 来源。
