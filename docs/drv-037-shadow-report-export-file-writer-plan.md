# DRV-037 Shadow Report Export File Writer Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`shadow report ingestion/export/feedback readiness -> ignored artifact export files`  
> 边界：本计划只授权把 DRV-036 的 JSON/Markdown export preview 写入 ignored `artifacts/tmp/shadow_report_exports`。不授权访问麦克风、不读取真实用户音频、不读取 `.m4a`、不读取 `configs/local/`、不读取 `data/asr_eval/local_samples/`、不写或删除 audio chunk、不写仓库可提交导出文件、不下载公开音频、不运行 ASR provider、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

## 1. 背景

DRV-036 已经能把 DRV-033/035 report 转成 feedback analysis、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview，但它故意不写文件。用户要求按完整计划继续实现和自测，而不是继续做泛评测。因此下一步把 preview 落到 ignored artifact root，形成可复查的本地导出物。

这一步仍然不是正式产品导出，也不代表真实麦克风会议已经发生。它只把“报告预览”推进到“可追溯本地文件”，为后续 feedback ingestion API/UI 和真实 shadow test 做准备。

## 2. 目标

新增：

- `tools/shadow_report_export_file_writer.py`
- `tests/test_shadow_report_export_file_writer.py`

工具行为：

- 调用 DRV-036 ingestion/export/feedback readiness。
- 只在 DRV-036 返回 `shadow_report_ingested_for_export_feedback` 且 preview 存在时写文件。
- JSON 写为 `<session_id>.shadow-report.json`。
- Markdown 写为 `<session_id>.shadow-report.md`。
- 默认输出根为 `artifacts/tmp/shadow_report_exports`。
- 输出 `written_files`，包含 path、sha256 和 byte count。
- 已存在文件内容一致时返回 `idempotent_existing_files_match`。
- 已存在文件内容不一致时返回 `blocked_by_existing_export_conflict`，不覆盖。

## 3. 关键规则

### 3.1 Replay Draft 可以写预览，但不是 Go 证据

如果 DRV-036 的 `export_readiness_status=draft_export_preview_only`：

- 允许写 JSON/Markdown 预览。
- 输出 `go_evidence_status=not_go_evidence_replay_or_feedback_missing`。
- Markdown 保留 `Draft only; not real mic validation.`。
- 不把 replay draft 提升为真实产品价值证据。

### 3.2 真实反馈 Report 才能成为 Go 证据候选

如果 DRV-036 的 `export_readiness_status=ready_for_shadow_test_export` 且 `final_decision_readiness_status=go_supported_by_feedback`：

- 输出 `go_evidence_status=go_evidence_supported_by_real_feedback_report`。
- 写入 JSON/Markdown 导出预览文件。

### 3.3 输出根和文件名边界

允许输出：

- `artifacts/tmp/shadow_report_exports/**/*.json`
- `artifacts/tmp/shadow_report_exports/**/*.md`

读取或写入前阻断：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- 仓库外路径
- 不在 `artifacts/tmp/shadow_report_exports` 下的输出根

`session_id` 只能包含 ASCII 字母、数字、点、下划线和短横线，且不能包含路径分隔符。这样导出文件名不会被 `../` 或反斜杠绕过。

## 4. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_export_file_writer.py -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

原因：

- `tools/shadow_report_export_file_writer.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_export_file_writer.py -q -p no:cacheprovider
```

结果：

```text
7 passed, 1 warning
```

## 5. 验收边界

DRV-037 完成后，主线已从 export preview 推进到 ignored artifact export file writer。但它仍不代表：

- 真实麦克风会议已经发生。
- 真实 audio chunk 已采集或删除。
- replay draft 已有真实用户反馈。
- ASR 中文技术实体质量已经达标。
- 正式产品导出目录已经确定。
- 可以绕过 desktop runtime、worker/mic connector、feedback ingestion API/UI 或用户显式 start。

## 6. 下一步

DRV-037 后的主线候选：

- `feedback ingestion API/UI`：把真实会议后的卡片反馈接入 report，而不是只读已有 report。
- `Real Tauri no-op run`：验证 Tauri WebView no-op IPC。
- `worker/mic connector`：推进真实 worker/mic 连接设计，但采集仍需用户显式审批。
- `ASR quality decision exit`：只有在本地 FunASR 模型目录、DRV-019 审批、可选远程 ASR 对照或降级决策明确时推进。

默认不再继续公开音频/ASR 泛评测循环。
