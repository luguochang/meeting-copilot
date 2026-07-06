# DRV-036 Shadow Report Ingestion, Export, and Feedback Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`shadow report draft -> ingestion/export/feedback readiness`  
> 边界：本计划不授权访问麦克风、不读取真实用户音频、不读取 `.m4a`、不读取 `configs/local/`、不读取 `data/asr_eval/local_samples/`、不写 audio chunk、不下载公开音频、不运行 ASR provider、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

## 1. 背景

DRV-033 固定了真实麦克风 shadow-test report schema。DRV-035 已把 PCWEB-110/111 replay timeline 映射成 DRV-033 candidate report draft。下一步不能继续停留在“报告草稿已生成”，必须把报告接入一个可审计的 ingestion/export/feedback readiness gate。

这个 gate 的价值是把产品初心继续往前推：会议 Copilot 不是“转写工具”，而是要能在真实或模拟 shadow report 中汇总 candidate card、反馈、导出预览和 Go/Pivot/Stop readiness。

## 2. 目标

新增 `tools/shadow_report_ingestion_export_feedback.py` 和 `tests/test_shadow_report_ingestion_export_feedback.py`。

工具支持两类输入：

- 直接传入 DRV-033 `real_mic_shadow_test_report.v1` candidate report。
- 读取 `artifacts/tmp/real_mic_shadow_reports` 下的 candidate report JSON。
- 读取 `artifacts/tmp/asr_reports` 下的 DRV-035 adapter report JSON，并抽取其中的 `candidate_report`。

工具输出：

- schema validation 状态。
- timeline counts。
- feedback analysis。
- feedback collection status。
- final decision readiness status。
- export readiness status。
- JSON export preview。
- Markdown export preview。
- all false safety flags。

## 3. 关键规则

### 3.1 Replay Draft 不是 Go 证据

如果输入是 DRV-035 replay draft：

- `audio_chunk_write_status=not_written`。
- feedback 计数为 0。
- final decision 为 `inconclusive_requires_more_shadow_tests`。
- 输出 `export_readiness_status=draft_export_preview_only`。
- 输出 `feedback_collection_status=feedback_required_before_decision`。

它只能作为导出预览和报告结构验证，不能作为真实产品价值 Go 证据。

### 3.2 真实反馈 Report 才能进入 Go/Pivot/Stop Readiness

如果输入是真实 DRV-033 candidate report，并且 schema validation passed：

- 有真实反馈时输出 `feedback_collection_status=feedback_collected`。
- `go` 必须由 DRV-033 schema 保证至少 2 个 useful / would_have_asked 且 negative <= 1。
- 符合条件时输出 `final_decision_readiness_status=go_supported_by_feedback`。
- 如果 audio retention 表明来自用户批准 shadow test，输出 `export_readiness_status=ready_for_shadow_test_export`。

### 3.3 不写导出文件

DRV-036 只输出 `json_export_preview` 和 `markdown_export_preview`。它不写 export 文件、不创建目录、不上传、不读写音频。

后续如果要落地真实导出文件，必须另起 TDD 任务，并限定输出到 ignored artifact root。

## 4. 路径边界

允许读取：

- `artifacts/tmp/real_mic_shadow_reports/**/*.json`
- `artifacts/tmp/asr_reports/**/*.json`

读取前阻断：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- 仓库外路径
- 非 JSON 文件

## 5. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_ingestion_export_feedback.py -q -p no:cacheprovider
```

结果：

```text
6 failed, 1 warning
```

原因：

- `tools/shadow_report_ingestion_export_feedback.py` 不存在。

中间红灯：

```text
1 failed, 5 passed, 1 warning
```

原因：

- 一张卡可以同时被标 `useful` 和 `would_have_asked`，正向计数为 2，但 usefulness ratio 不应超过 1.0。

修复：

- `usefulness_ratio` 和 `negative_ratio` capped at 1.0。

最终 focused 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_ingestion_export_feedback.py -q -p no:cacheprovider
```

结果：

```text
6 passed, 1 warning
```

## 6. 验收边界

DRV-036 完成后，主线已从 report draft 推进到 report ingestion/export/feedback readiness。但它仍不代表：

- 真实麦克风会议已经发生。
- 真实 audio chunk 已采集或删除。
- 用户反馈已经存在于 replay draft。
- ASR 中文技术实体质量已经达标。
- 真实导出文件已经写入磁盘。
- 可以绕过 desktop runtime、worker/mic connector 或用户显式 start。

## 7. 下一步

DRV-036 后的主线候选：

- `shadow report export file writer`：把 preview 写入 ignored artifact root，仍不读音频。
- `feedback ingestion API/UI`：把真实会议后用户反馈接入 report，而不是只读已有 report。
- `Real Tauri no-op run`：验证 Tauri WebView no-op IPC。
- `worker/mic connector`：推进真实 worker/mic 连接设计，但采集仍需用户显式审批。

默认不再继续公开音频/ASR 泛评测循环。
