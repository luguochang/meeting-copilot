# PCWEB-108 Worker Output to Web Live ASR Session Closure Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`worker handoff`、`EvidenceSpan/state/gap`  
> 边界：本计划不授权启动真实 worker、不访问麦克风、不读取真实用户音频、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

## 1. 目标

PCWEB-096/100 已能把 approved synthetic ASR event file 通过 `/live/asr/local-event-files/sessions` 写入临时 Web data dir，但报告只证明 Web handoff API 接收成功。PCWEB-108 将该 dry-run 提升为 M3 closure gate：必须证明 worker-like event output 进入 Web Live ASR session 后，确实形成 transcript final、EvidenceSpan、state event、scheduler event、suggestion candidate 和 LLM request draft。

这一步仍然不是普通转写成功判定。非工程文本即使能生成 transcript final 和 EvidenceSpan，只要没有 state/gap candidate，就必须返回 blocked closure。

## 2. 范围

修改：

- `tools/desktop_asr_worker_handoff_local_dry_run.py`
- `tests/test_desktop_asr_worker_handoff_local_dry_run.py`
- `tests/test_desktop_asr_worker_synthetic_lifecycle.py`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `README.md`

不修改：

- Web handoff API 行为。
- Tauri/Rust runtime。
- ASR provider/model runtime。
- 麦克风 adapter 执行逻辑。
- 任何真实音频、密钥或本地私有配置。

## 3. 验收

新增/加固测试必须证明：

- `synthetic_local_test` 成功时，`web_handoff_response_summary` 包含：
  - `transcript_final_count`
  - `evidence_span_count`
  - `state_event_count`
  - `scheduler_event_count`
  - `suggestion_candidate_count`
  - `llm_request_draft_count`
  - `suggestion_card_count`
  - `all_llm_statuses`
  - `worker_to_web_live_session_closure_status`
- 技术会议输入必须返回 `worker_to_web_live_session_closure_status=closed_to_evidence_state_gap`。
- 非工程输入必须返回 `dry_run_status=blocked_by_live_session_closure`，并暴露 `blocked_no_state_or_gap_candidate`。
- blocked closure 仍不得调用 LLM、远程 ASR、麦克风、模型下载、真实 worker 或 Cargo/Tauri。
- PCWEB-100 synthetic lifecycle harness 必须继承同一个 closure summary。

## 4. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_calls_web_handoff_api_in_temp_data_dir \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_blocks_when_handoff_does_not_create_evidence_state_gap \
  -q -p no:cacheprovider
```

结果：`2 failed, 2 warnings`。失败原因是现有 summary 缺少 EvidenceSpan/state/gap closure 字段，非工程输入仍返回 `synthetic_web_handoff_passed`。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_calls_web_handoff_api_in_temp_data_dir \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_blocks_when_handoff_does_not_create_evidence_state_gap \
  tests/test_desktop_asr_worker_synthetic_lifecycle.py::test_synthetic_lifecycle_runs_command_sequence_and_temp_web_handoff \
  -q -p no:cacheprovider
```

结果：`3 passed, 2 warnings`。

相关门禁：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py \
  tests/test_desktop_asr_worker_synthetic_lifecycle.py \
  tests/test_asr_live_pipeline_replay.py \
  -q -p no:cacheprovider
```

结果：`24 passed, 2 warnings`。

复审加固：

- 只读代码审查指出：初版 closure status 虽然展示了 `scheduler_event_count` 和 `llm_request_draft_count`，但 closed 判定只检查 transcript final、EvidenceSpan、state 和 suggestion candidate。若未来 response 中 candidate 仍存在而 scheduler/draft 缺失，会误判为 closed。
- 追加红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_closure_summary_blocks_when_scheduler_or_llm_request_draft_is_missing \
  -q -p no:cacheprovider
```

结果：`1 failed, 1 warning`。失败原因为缺 scheduler 时仍返回 `closed_to_evidence_state_gap`。

- 加固内容：
  - `_live_session_closure_status()` 纳入 `scheduler_event_count` 和 `llm_request_draft_count`。
  - 缺 scheduler 返回 `blocked_no_scheduler_event`。
  - 缺 LLM request draft 返回 `blocked_no_llm_request_draft`。
- 加固绿灯：
  - 同一 focused command。
  - Result: `1 passed, 1 warning`。
- 加固后相关门禁：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py tests/test_desktop_asr_worker_synthetic_lifecycle.py tests/test_asr_live_pipeline_replay.py -q -p no:cacheprovider`
  - Result: `25 passed, 2 warnings`。

相邻保护网：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_quality_decision_gate.py \
  tests/test_real_mic_shadow_test_report_schema.py \
  tests/test_desktop_tauri_noop_shell_run_smoke.py \
  tests/test_desktop_tauri_scaffold.py \
  -q -p no:cacheprovider
```

结果：`38 passed, 1 warning`。

总门禁：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile pc-web
```

结果：

- root-pytest：`285 passed, 2 warnings`
- core：`34 passed, 1 warning`
- Web backend：`316 passed, 2 warnings`
- browser smoke：`status=ok`
- quality gate：`profile=pc-web passed`

Review hardening 后重跑结果：

- root-pytest：`286 passed, 2 warnings`
- core：`34 passed, 1 warning`
- Web backend：`316 passed, 2 warnings`
- browser smoke：`status=ok`
- quality gate：`profile=pc-web passed`

## 5. 后续

PCWEB-108 完成后，M3 的 synthetic temp Web session closure 已可审计。下一步仍只能在既有 6 个里程碑内选择：

- M2 Real Tauri no-op run，前提是明确允许运行 Tauri/Cargo。
- M4 Mic adapter no-op UI invocation，不请求权限、不枚举设备、不写 chunk。
- M5 Short local simulated input，将 closure summary 接入 state/candidate/card timeline 报告。

真实麦克风会议仍未开始。
