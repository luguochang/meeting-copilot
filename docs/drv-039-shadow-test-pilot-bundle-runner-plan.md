# DRV-039 Shadow Test Pilot Bundle Runner Plan

> 日期：2026-07-03  
> 状态：Implemented with focused TDD  
> 主线节点：`真实 shadow-test report feedback -> ignored export bundle -> Go/Pivot/Stop evidence package`  
> 边界：本文档不授权访问麦克风、不读取真实用户音频或 `.m4a`、不写或删除 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不下载公开音频或模型、不运行 Cargo/Tauri、不启动 worker。

## 1. 目的

DRV-038 已经能把真实会议后的卡片反馈写回 shadow report，DRV-037 已经能把报告导出为 ignored JSON/Markdown 文件。DRV-039 把这两步串成一个 pilot bundle runner：输入一份 DRV-033/038 兼容的 candidate report 和 feedback entries，输出反馈后的 readiness、导出文件记录和 Go/Pivot/Stop 结论。

这一步服务真实麦克风会议后的验收闭环，不是继续 ASR 评测。它不证明真实会议已经发生，也不把 replay draft 变成 Go 证据。

## 2. 范围

新增：

- `tools/shadow_test_pilot_bundle_runner.py`
- `tests/test_shadow_test_pilot_bundle_runner.py`

复用：

- DRV-038 `tools/shadow_report_feedback_ingestion.py`
- DRV-037 `tools/shadow_report_export_file_writer.py`
- DRV-036 `tools/shadow_report_ingestion_export_feedback.py`
- DRV-033 `tools/real_mic_shadow_test_report_schema.py`

## 3. 合同

输入：

```json
{
  "candidate_report_path": "artifacts/tmp/real_mic_shadow_reports/example.json",
  "feedback_entries": [
    {"candidate_id": "cand-001", "label": "useful"},
    {"candidate_id": "cand-002", "label": "would_have_asked"}
  ],
  "output_root": "artifacts/tmp/shadow_report_exports"
}
```

规则：

- `output_root` 必须先通过 DRV-037 guard，且必须在 `artifacts/tmp/shadow_report_exports` 下。
- 输出根 guard 必须在读取 candidate report 前执行。
- feedback ingestion 失败时不得写导出文件。
- feedback ingestion 成功后，把 `updated_candidate_report` 传给 DRV-037 export writer。
- 真实 audio-written report 且 feedback 达 Go 阈值时，输出 `pilot_bundle_written`。
- replay draft 或无真实音频写入的 report 只能输出 `pilot_bundle_preview_written_not_go_evidence`。

输出：

- `pilot_bundle_status`
- `feedback_ingestion_status`
- `export_file_write_status`
- `go_evidence_status`
- `final_decision`
- `bundle_artifacts`
- `written_file_count`
- all-false safety flags

## 4. 决策规则

真实会议 report：

- `audio_chunk_write_status=written_by_user_approved_shadow_test`
- `useful + would_have_asked >= 2`
- `wrong + too_late + too_intrusive <= 1`

满足时：

- `feedback_ingestion_status=shadow_report_feedback_ingested`
- `export_file_write_status=written_to_ignored_artifact_root`
- `go_evidence_status=go_evidence_supported_by_real_feedback_report`
- `final_decision=go`
- `pilot_bundle_status=pilot_bundle_written`

Replay draft：

- 即使有正反馈，也必须保持 `final_decision=inconclusive_requires_more_shadow_tests`。
- 输出 `pilot_bundle_preview_written_not_go_evidence`。
- 输出 `go_evidence_status=not_go_evidence_replay_or_feedback_missing`。

Blocked：

- unsafe output root：`blocked_by_output_root_guard`，不读取 report。
- bad feedback：`blocked_by_feedback_ingestion`，不写导出文件。
- export writer blocked：`blocked_by_export_file_writer`。

## 5. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_test_pilot_bundle_runner.py -q -p no:cacheprovider
```

结果：

```text
5 failed, 1 warning
```

原因：`tools/shadow_test_pilot_bundle_runner.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_test_pilot_bundle_runner.py -q -p no:cacheprovider
```

结果：

```text
5 passed, 1 warning
```

源码边界扫描：

```bash
rg -n "subprocess|os\\.system|Popen|check_call|check_output|ffmpeg|afconvert|sounddevice|pyaudio|wave\\.open|requests\\.|urllib\\.request|modelscope|AutoModel|getUserMedia|MediaRecorder" tools/shadow_test_pilot_bundle_runner.py
```

结果：无命中。

## 6. 验收边界

DRV-039 完成后，真实 shadow-test report 的反馈和导出可以一键打包为 ignored artifact bundle。但它仍不代表：

- 真实麦克风会议已经发生。
- 麦克风采集、worker、ASR 或 Tauri runtime 已经可执行。
- 公开音频已经下载、抽样或转写。
- replay draft 可以成为 Go 证据。
- 可以读取 `configs/local`、调用远程 ASR/LLM、下载模型或访问麦克风。

## 7. 下一步

DRV-039 后，主线不再继续做 shadow report preview/export/feedback 的同类包装。下一步默认只应进入：

- `Real Tauri no-op run`
- `worker/mic connector`
- `ASR quality decision exit`
- 用户真实麦克风 shadow test 前置清单
