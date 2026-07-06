# DRV-038 Shadow Report Feedback Ingestion API/UI Plan

> 日期：2026-07-03  
> 状态：Implemented with focused TDD  
> 主线节点：`candidate/card/feedback -> Go/Pivot/Stop readiness -> pilot evidence`  
> 边界：本文档不授权访问麦克风、不读取真实用户音频或 `.m4a`、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不下载公开音频或模型、不运行 Cargo/Tauri。

## 1. 目的

DRV-033 固定了真实 shadow test report schema，DRV-036/037 已完成 report ingestion、readiness 和 ignored artifact export。DRV-038 补齐缺口：真实会议结束后，用户需要把每张建议卡的反馈标签写回 shadow report，并让系统自动更新反馈汇总和 Go/Pivot/Stop readiness。

该功能不是新一轮 ASR 评测，也不是普通转写功能。它服务产品价值验证：建议卡是否有用、是否太晚、是否错误，必须能进入报告链路。

## 2. 范围

新增：

- `tools/shadow_report_feedback_ingestion.py`
- `tests/test_shadow_report_feedback_ingestion.py`
- `POST /shadow-reports/feedback-ingestions`
- Web 工作台 `shadow-report-feedback-panel`

修改：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

## 3. 合同

输入：

```json
{
  "candidate_report": {},
  "candidate_report_path": "artifacts/tmp/real_mic_shadow_reports/example.json",
  "feedback_entries": [
    {"candidate_id": "cand-001", "label": "useful"}
  ]
}
```

规则：

- `candidate_report` 和 `candidate_report_path` 二选一。
- `candidate_report_path` 只允许 DRV-036 已允许的 `artifacts/tmp/real_mic_shadow_reports` 或 `artifacts/tmp/asr_reports`。
- `feedback_entries` 必须非空。
- 每个 entry 必须包含 `candidate_id` 和 `label`。
- `candidate_id` 必须引用 `candidate_card_timeline[*].candidate_id`。
- 每个 candidate 最多一个反馈标签。
- 允许标签只包括 `useful`、`would_have_asked`、`wrong`、`too_late`、`too_intrusive`、`dismissed`。

输出：

- `updated_candidate_report`
- `feedback_summary_delta`
- `readiness_report`，复用 DRV-036 readiness。
- `go_evidence_status`
- all-false safety flags。

## 4. 决策规则

真实 shadow report：

- `audio_chunk_write_status=written_by_user_approved_shadow_test`
- positive feedback `useful + would_have_asked >= 2`
- negative feedback `wrong + too_late + too_intrusive <= 1`

满足以上条件时，`final_decision.decision=go`，DRV-036 readiness 输出 `go_supported_by_feedback` 和 `ready_for_shadow_test_export`。

Replay draft 或无音频写入 report：

- 即使收到正反馈，也保持 `inconclusive_requires_more_shadow_tests`。
- 输出 `shadow_report_feedback_ingested_preview_only`。
- 输出 `go_evidence_status=not_go_evidence_replay_or_feedback_missing`。

负反馈占优：

- 真实 shadow report 中，若负反馈明显占优且没有正反馈，输出 `stop`。
- 其他反馈不足以 Go 的真实 report 输出 `pivot`。

## 5. UI

Web 工作台新增 `Shadow Feedback / 验收反馈` 面板：

- `Report JSON` textarea：手动输入 candidate report JSON。
- `Feedback JSON` textarea：手动输入 feedback entries JSON。
- `提交反馈`：调用 `POST /shadow-reports/feedback-ingestions`。
- `shadow-report-feedback-result`：展示 ingestion status、decision、export readiness、positive/negative/dismissed 计数或错误。

UI 不读取本地文件，不访问麦克风，不触发 ASR/LLM，不保存报告。

## 6. TDD 记录

工具红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_feedback_ingestion.py -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

原因：`tools/shadow_report_feedback_ingestion.py` 不存在。

工具绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_feedback_ingestion.py -q -p no:cacheprovider
```

结果：

```text
7 passed, 1 warning
```

API 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path \
  -q -p no:cacheprovider
```

结果：

```text
2 failed, 2 warnings
```

原因：`POST /shadow-reports/feedback-ingestions` 尚不存在，返回 404。

API 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path \
  -q -p no:cacheprovider
```

结果：

```text
2 passed, 2 warnings
```

UI 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
2 failed, 2 warnings
```

原因：HTML/JS/CSS 中还没有 `shadow-report-feedback-panel`、`bindShadowReportFeedbackForm` 和 API 调用。

UI 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
2 passed, 2 warnings
```

Focused 合并：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_feedback_ingestion.py \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
11 passed, 2 warnings
```

## 7. 验收边界

DRV-038 完成后，主线从“报告可导出”推进到“报告可接收用户反馈并更新 Go/Pivot/Stop readiness”。它仍不代表：

- 真实麦克风会议已经发生。
- 麦克风采集、start/pause/resume/stop/delete 已可执行。
- 原始 audio chunk 已采集、写入或删除。
- ASR 中文技术实体质量已经达标。
- replay draft 可以变成 Go 证据。
- 可以读取 `configs/local` 或调用远程 ASR/LLM。

## 8. 下一步

DRV-038 后，主线候选应继续收敛到：

- `Real Tauri no-op run`
- `worker/mic connector`
- `真实 shadow-test report ingestion/export pilot`
- `ASR quality decision exit`

默认不再回到公开音频泛搜或 ASR/provider 横评循环。
