# DRV-041 Simulated Shadow Pipeline Smoke Runner

> 日期：2026-07-04
> 状态：Implemented
> 目的：把“公开/合成转写事件是否能真正进入产品价值链路”从散点工具调用收束成一条可复跑 smoke。
> 边界：本工具不读取真实音频或 `.m4a`，不访问麦克风，不下载公开音频或模型，不调用远程 ASR/LLM，不运行 Cargo/Tauri，不写 audio chunk，不写导出文件，不读取 `configs/local/`、`data/local_runtime/` 或 `outputs/`。

## 背景

用户确认：完整计划需要写下来，转写验证先由我通过网上官方公开音频来源和本地模拟完成，最终真实麦克风会议由用户验证。

已有链路分别完成了：

```text
ASR event replay -> EvidenceSpan/state/candidate timeline
replay timeline -> DRV-033 shadow report draft
shadow report draft -> export/feedback readiness preview
```

但这些能力分散在多个工具中，容易让主线继续停留在“评估/报告/计划”。DRV-041 的目标是提供一个纯内存一键 smoke runner，把 approved ASR event JSON 直接串到 export preview，用来证明合成/公开样本事件进入产品价值链路的执行态。

## 决策

新增 `tools/simulated_shadow_pipeline_smoke.py`。

它只编排已有 builder，不重新实现 ASR replay、shadow report draft 或 export preview：

```text
artifacts/tmp/asr_events/*.events.json
  -> tools/asr_live_pipeline_replay.py
  -> tools/replay_shadow_report_draft_adapter.py
  -> tools/shadow_report_ingestion_export_feedback.py
  -> simulated_shadow_pipeline_preview_created
```

输入：

- `--events-path`：必须由 PCWEB-110/111 的 path guard 限制在 approved ASR event root。
- `--event-manifest-path`：可选，只允许 `asr_event_provenance.v1`，用于把输入标记为 `synthetic_audio`、`mock_streaming` 或未来 `public_audio_sample`。
- `--provider`：事件来源 provider 名称，例如 `mock_streaming`。
- `--session-id`：本次模拟会话 ID。

成功输出：

- `pipeline_status=simulated_shadow_pipeline_preview_created`
- `replay_status=asr_events_replayed_to_live_pipeline`
- `adapter_status=shadow_report_draft_created`
- `ingestion_status=shadow_report_ingested_for_export_feedback`
- `export_readiness_status=draft_export_preview_only`
- `go_evidence_status=not_go_evidence_replay_or_feedback_missing`
- `artifact_write_status=not_written`
- `audio_chunk_write_status=not_written`
- `public_audio_download_status=not_downloaded`
- `remote_asr_call_status=not_called`
- `llm_call_status=not_called`
- `real_mic_validation_status=not_started_user_final_validation_required`

阻断输出：

- `blocked_by_replay`：event path、manifest、event contract 或 JSON 形状不合法。
- `blocked_by_no_candidate_timeline`：非工程对照或没有工程 candidate，不伪造 shadow report。
- `blocked_by_shadow_report_draft`：replay 不能转换为 DRV-033 candidate report。
- `blocked_by_shadow_report_ingestion`：candidate report 不能进入 DRV-036 preview。

## 验收

TDD 覆盖：

- 工程 mock events 能生成 shadow export preview。
- 非工程 control 保持无 candidate、无 fake card、无 export preview。
- `public_audio_sample` provenance 只作为来源审计，不触发下载。
- forbidden `configs/local` path 在读取前 blocked。
- CLI 只有 preview created 时 exit 0，其它 blocked 状态 exit 1。

验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py -q -p no:cacheprovider
```

## 本地批量模拟结果

2026-07-04 用 existing approved mock events 做了一次只读批量 smoke，不写文件、不读音频、不调用远程：

| session | pipeline_status | short_local_simulated_input_status | transcript_segments | candidate_cards | go_evidence_status |
| --- | --- | --- | --- | --- | --- |
| `api-review-001` | `simulated_shadow_pipeline_preview_created` | `closed_to_candidate_timeline` | 3 | 1 | `not_go_evidence_replay_or_feedback_missing` |
| `architecture-review-001` | `simulated_shadow_pipeline_preview_created` | `closed_to_candidate_timeline` | 3 | 2 | `not_go_evidence_replay_or_feedback_missing` |
| `incident-review-001` | `simulated_shadow_pipeline_preview_created` | `closed_to_candidate_timeline` | 3 | 2 | `not_go_evidence_replay_or_feedback_missing` |
| `release-review-001` | `simulated_shadow_pipeline_preview_created` | `closed_to_candidate_timeline` | 3 | 2 | `not_go_evidence_replay_or_feedback_missing` |
| `non-engineering-control-001` | `blocked_by_no_candidate_timeline` | `no_engineering_candidate_detected` | n/a | n/a | `not_go_evidence_pipeline_blocked` |

结论：mock 转写事件能把工程会议闭合到产品价值 preview，且非工程对照不会被伪造成工程建议。这仍然不是 ASR quality Go evidence，也不是真实麦克风会议 evidence。

## 非目标

- 不从音频生成 ASR event。
- 不下载 AliMeeting/AISHELL-4/AISHELL-1。
- 不读取或转写用户真实录音。
- 不访问麦克风。
- 不启动 ASR worker。
- 不调用 OpenAI-compatible LLM 或远程 ASR。
- 不把 replay draft 写成 Go evidence。

## 后续

DRV-041 完成后，主线不应继续新增同类 smoke/readiness 包装器。下一步只保留两类真实推进：

1. ASR quality exit：提供 FunASR 本地模型目录、批准 DRV-019、显式远端 ASR 对照，或显式降级试点风险接受。
2. 真实麦克风 shadow test：在 PCWEB-115 readiness 满足后，由用户手动执行 20-30 分钟中文技术会议，并按 DRV-033/036/038/039 链路 ingest/export/feedback。
