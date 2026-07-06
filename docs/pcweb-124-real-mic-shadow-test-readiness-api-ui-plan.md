# PCWEB-124 Real Mic Shadow-Test Readiness API/UI Plan

> 日期：2026-07-04  
> 状态：Implemented  
> 目的：把 PCWEB-115 真实麦克风 shadow-test readiness 的默认静态报告展示到本地 Web 工作台，避免后续误以为真实会议已经 ready。  
> 边界：本文档和本实现不授权访问麦克风、不请求音频权限、不读取真实用户音频或 `.m4a`、不读取 evidence 文件或 `configs/local/`、不创建真实本地存储、不启动 worker、不运行 Cargo/Tauri、不调用远程 ASR/LLM、不下载模型或公开音频。

## 1. 背景

用户确认完整计划已经按“网上官方公开音频来源复核 + 本地合成/Mock 模拟转写 + 用户最终真实麦克风会议验证”执行。当前 PCWEB-115 已有真实麦克风 shadow-test readiness gate，但 Web 工作台没有直接展示该 gate 的默认 blockers，容易让后续开发在计划、评估和 readiness wrapper 之间循环。

PCWEB-124 的目标不是创建新 gate，而是把已有 gate 显示到产品工作台：

```text
GET /desktop/real-mic-shadow-test-readiness
  -> PCWEB-115 default static preflight report
  -> Web readiness panel
  -> blockers / safety flags / pilot protocol visible
```

## 2. 需求

- Web backend 必须新增 `GET /desktop/real-mic-shadow-test-readiness`。
- Endpoint 只能调用 `tools/real_mic_shadow_test_readiness_gate.py` 的 default static report。
- Endpoint 默认必须返回 `blocked_not_ready_for_user_real_mic_shadow_test`。
- Endpoint 必须显示：
  - `pcweb_id=PCWEB-115`
  - `readiness_mode=static_preflight_report_only`
  - `user_can_start_real_mic_shadow_test_now=false`
  - `asr_quality_exit_status=not_exited`
  - `worker_mic_source_approval_status=not_approved`
  - `tauri_noop_evidence_status=not_provided`
  - `mic_adapter_implementation_status=not_provided`
  - `asr_worker_implementation_status=not_provided`
  - `export_feedback_status=ready_for_real_report_after_user_shadow_test`
  - blockers、pilot protocol 和全部 false safety flags。
- Web 工作台必须新增 `desktop-real-mic-shadow-readiness-panel`。
- `renderEmpty()` 后必须重新加载 readiness，防止删除会话后桌面状态面板变空。
- data_dir 模式不得创建 `sessions`、`live_asr_sessions` 或 `real_mic_shadow_reports`。

## 3. 不做范围

- 不读取 `artifacts/tmp/**` evidence 文件；PCWEB-124 只展示默认报告。
- 不触发 PCWEB-115 CLI path input。
- 不接入真实麦克风 start/pause/resume/stop/delete。
- 不启动 ASR worker。
- 不运行 Tauri/Cargo。
- 不下载 AliMeeting、AISHELL-4、AISHELL-1 或 FunASR/ModelScope 模型。
- 不调用 OpenAI-compatible LLM 中转站或远程 ASR。

## 4. TDD 记录

Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_reports_static_gate_without_audio_access \
  code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_does_not_probe_audio_or_read_secrets \
  code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_with_data_dir_does_not_create_local_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

Result：`5 failed, 2 warnings`。

失败原因：

- `/desktop/real-mic-shadow-test-readiness` 返回 404。
- HTML 缺 `desktop-real-mic-shadow-readiness-panel`。
- JS 缺 `loadDesktopRealMicShadowTestReadiness` 和 endpoint 字符串。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_reports_static_gate_without_audio_access \
  code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_does_not_probe_audio_or_read_secrets \
  code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_with_data_dir_does_not_create_local_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

Result：`5 passed, 2 warnings`。

## 5. 实现文件

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`

## 6. 后续主线

PCWEB-124 完成后，默认状态仍是 blocked。下一步不得继续新增同类 readiness/report-only wrapper，应回到：

- DRV-032 ASR quality exit：本地 FunASR 模型目录、DRV-019 手动模型下载审批、显式远端 ASR 对照，或合法 `asr_quality_degraded_pilot_acceptance.v1`。
- 公开音频 bounded manifest：只有官方来源、archive member path、clip window、expected sha256、license citation 和下载审批齐备后，才允许进入人工下载复核。
- 用户最终真实麦克风 shadow test：只在 readiness gate 变为 ready 后，由用户显式启动。
