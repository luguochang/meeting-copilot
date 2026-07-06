# Mainline Goal Progress And Next Plan

> Date: 2026-07-04  
> Status: PC mainline artifact-backed closure complete, not production-complete  
> Scope: Meeting Copilot PC mainline usability, self-test, local fixes, and next production path

## 1. Current Conclusion

The project has not been stuck only doing evaluation.

The PC mainline product shape is now locally runnable and traceable:

```text
audio health sample
  -> Web mainline trial or approved ASR event artifact trial
  -> Live ASR events
  -> transcript / EvidenceSpan
  -> meeting state
  -> suggestion candidates
  -> LLM request drafts without calling LLM
  -> Copilot report preview
  -> feedback/export preview closure
  -> artifact retention
```

The product is still not production-ready because production ASR quality has not passed and real meeting audio has not been authorized or proven. The current blocker is no longer vague: existing local FunASR synthetic smoke evidence is available to the mainline, and it shows the ASR quality gate is still blocked.

## 2. What Was Completed

Implemented and verified:

- M1 audio capture healthcheck for approved local WAV artifacts.
- M2 Mac system-audio capture adapter boundary and explicit CLI entry, defaulting to safe preflight-only.
- Mainline usable E2E runner with JSON/Markdown artifacts.
- Web mainline trial, Live ASR event handoff, state extraction, suggestion candidates, LLM request draft creation, and feedback/export preview.
- Browser smoke path for the workbench.
- Local artifact retention/delete boundary for approved ignored runtime artifacts.
- Copilot report preview as a first-class mainline report section.
- ASR quality decision artifact ingestion from approved `artifacts/tmp/asr_reports/*.json`.
- Approved ASR event artifact handoff into the Web Live ASR path from the mainline runner.
- Approved ASR event artifact as the mainline trial source, including draft review and feedback/export preview closure.
- Structured closure blocker reporting for valid-but-too-thin ASR event artifacts.
- PC workbench `工件主线` entry for artifact-backed trial and closure.

Recent verification:

```text
focused artifact-backed closure regression: 15 passed, 2 warnings
adjacent regression: 36 passed, 2 warnings
syntax check: exit 0
sensitive scan: no matches
mainline with ASR quality artifact: exit 0
mainline with ASR quality artifact and browser smoke: exit 0
mainline with ASR quality artifact, ASR event artifact as main trial, feedback closure, and browser smoke: exit 0
browser smoke user-visible artifact mainline trial and closure: exit 0
```

## 3. What The Latest Mainline Proves

The latest mainline report can show product value, not only transcription:

```text
copilot_report_preview.preview_status=copilot_report_preview_created
copilot_report_preview.value_chain=[
  transcript,
  evidence_span,
  meeting_state,
  suggestion_candidate,
  llm_request_draft,
  feedback_export_preview
]
```

It also consumes real local ASR quality evidence:

```text
asr_quality.source_status=provided_asr_quality_decision_report
asr_quality.decision_status=blocked_by_funasr_smoke_assembly_input_guard
asr_quality.quality_exit_status=not_exited
production_asr_quality=blocked_by_asr_quality
gap_summary.implemented_and_verified=6
```

It now also proves approved ASR event artifact handoff:

```text
asr_event_handoff.handoff_status=local_asr_event_file_handoff_created
mainline_trial.trial_id=mainline_asr_event_artifact_trial
mainline_trial.trial_status=mainline_artifact_trial_session_created
live_asr.event_counts.transcript_final=4
live_asr.event_counts.suggestion_candidate_event=3
closure.source_trial_id=mainline_asr_event_artifact_trial
closure.closure_status=mainline_trial_feedback_export_preview_created
asr_event_artifact_handoff=implemented_and_verified
asr_event_artifact_closure=implemented_and_verified
gap_summary.implemented_and_verified=8
```

The meaningful conclusion is:

```text
The Copilot product chain works locally on synthetic/mock/replay evidence.
The ASR quality evidence does not yet support production or real-meeting Go.
Approved ASR event artifacts can now drive the mainline trial and feedback/export closure, not only an auxiliary handoff.
The PC workbench exposes this path through `工件主线`.
```

## 4. Why The Main Goal Is Not Complete Yet

The remaining blockers are real product blockers, not missing documents:

- ASR quality is below the strict Chinese technical-meeting gate. Existing evidence includes normalized recall failures and non-validated batch evidence.
- Default no-argument runs still use synthetic/mock events for safety, but approved ASR event artifacts can now be selected as the mainline trial source.
- Valid but too-thin ASR event artifacts can be read and handed off, yet still fail feedback/export closure when they do not produce enough suggestion candidates. This is a content-quality boundary, not a runner crash.
- Real Mac system audio or microphone capture requires explicit user approval, device route selection, and privacy boundary confirmation.
- LLM execution is intentionally disabled; current product only creates request drafts and previews.

These should not be hidden or papered over. If local ASR cannot reach the threshold, the product must either improve the ASR path, switch to a remote provider with explicit cost/privacy approval, or accept a degraded pilot with clear risk.

## 5. Next Plan

Priority 1: ASR quality exit

```text
Goal: make DRV-046/044/032 pass, or produce a clear Pivot/Stop quality conclusion.
Action: inspect current FunASR synthetic smoke failures, identify whether failures are caused by configuration, hotwords, chunking, transcript normalization, or model quality.
Boundary: no private audio, no microphone, no remote ASR, no paid provider.
```

Priority 2: user-authorized real capture

```text
Goal: run a short Mac virtual-system-audio or microphone health capture only after explicit start.
Action: select device index/route, run short capture, pass M1 health gate, retain/delete artifacts through the existing local retention boundary.
Boundary: no background capture, no private old recordings, no upload.
```

Priority 3: LLM execution decision

```text
Goal: move from LLM request draft to optional real suggestion-card generation.
Action: add OpenAI-compatible provider dry-run/disabled-by-default execution path, then only call the configured relay after explicit provider/cost approval.
Boundary: no secrets in repo, no default paid calls.
```

Priority 4: UI refinement

```text
Goal: improve scanability of artifact-backed closure state, candidate insufficiency, ASR quality blockers, and real-capture readiness without reading private data.
Action: refine the already-visible `工件主线` and closure panels after the ASR/real-capture gates are clearer.
Boundary: no remote ASR/LLM calls and no audio capture from UI without explicit approval.
```

## 6. Current Product Value Assessment

The product still has value if it is built as a meeting Copilot rather than an ASR app:

- It extracts meeting state and engineering gaps while the meeting is still active.
- It creates suggestion candidates and LLM request drafts with EvidenceSpan traceability.
- It can produce a review/export preview and preserve artifacts for later replay.
- It now exposes the ASR quality blocker directly in the mainline, which prevents overclaiming.

The product should stop or pivot if ASR quality cannot support Chinese technical meetings with acceptable latency and recall, and if no approved remote ASR path is allowed. The next phase must answer that with evidence, not more wrapper plans.

## 7. 2026-07-04 ASR Quality Follow-up Result

本轮 ASR quality 出口没有通过，但已经从“只知道 recall 不够”推进到“知道哪些实体缺失、哪些可以正当修复”。

实现与证据：

```text
single-result builder now reports:
  expected_entities
  raw_matched_entities / raw_missing_entities
  normalized_matched_entities / normalized_missing_entities

DRV-044 now validates entity detail consistency when those fields are present.

chunk20_hotword final:
  api-review-001:          normalized_recall=1.0, missing=[]
  architecture-review-001: normalized_recall=1.0, missing=[]
  incident-review-001:     normalized_recall=0.5, missing=[timeout, 监控阈值]
  release-review-001:      normalized_recall=0.75, missing=[staging]
```

本地 deterministic normalizer 只修复了 transcript 中可观察的 near-miss：`paymentway`、`字段 quest`、`ure store`、`REDi coasterBQP`、`auder` backlog 上下文、`trcoutservice service`。没有把 `timeout`、`监控阈值`、`staging` 从脚本答案反填到 transcript。

最终 gate：

```text
DRV-046 assembly_status=drv044_batch_evidence_blocked
DRV-032 decision_status=blocked_by_funasr_smoke_assembly_input_guard
DRV-032 quality_exit_status=not_exited
```

主线回归：

```text
all-local no-browser gate: passed
mainline artifact-backed runner with browser smoke: exit 0
browser_smoke.browser_smoke_status=passed
overall_status=mainline_product_chain_exercised_with_expected_blockers
```

下一步：

不再继续堆 normalizer。只围绕 `timeout`、`监控阈值`、`staging` 做受控 ASR 输入/参数实验：先确认 synthetic script/audio 是否真实承载这些词，再尝试热词/参数/模型路径。若仍不能达到阈值，则进入远程 ASR 成本/隐私审批或显式 degraded pilot 决策。
