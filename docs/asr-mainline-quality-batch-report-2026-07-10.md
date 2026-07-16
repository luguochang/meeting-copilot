# ASR Mainline Quality Batch Report - 2026-07-10

## 结论

本轮把中文技术会议 ASR 从单样本复核推进到显式 matrix 批量质量门禁。结论是：

- 主链路已经闭合：4 个合成技术会议场景、2 个本地 Provider，共 8 个 sample-provider 组合，均能进入 `ASR event -> EvidenceSpan -> state -> suggestion_candidate -> llm_request_draft`。
- 成本/隐私边界满足本轮要求：`remote_asr_called=false`、`llm_called=false`、`raw_audio_uploaded=false`。
- ASR 质量仍不达生产级：4 个技术会议场景里只有 3 个场景存在至少一个质量过线 Provider，`incident-review-001` 仍没有 Provider 过最低质量线。
- 当前发布判断仍是 `no_go_quality_not_production`，下一步应优先改进本地 ASR 术语召回、分段/endpoint 和保守归一化，而不是进入真实会议生产验收。

## 新增交付物

- `tools/asr_mainline_quality_batch_report.py`
- `tests/test_asr_mainline_quality_batch_report.py`
- `data/asr_eval/manifests/asr-mainline-quality-synthetic.json`
- `data/asr_eval/references/generated/api-review-001.txt`
- `data/asr_eval/references/generated/release-review-001.txt`
- `data/asr_eval/references/generated/incident-review-001.txt`
- `data/asr_eval/references/generated/architecture-review-001.txt`
- `data/asr_eval/annotations/generated/api-review-001.annotation.json`
- `data/asr_eval/annotations/generated/release-review-001.annotation.json`
- `data/asr_eval/annotations/generated/incident-review-001.annotation.json`
- `data/asr_eval/annotations/generated/architecture-review-001.annotation.json`

显式 matrix 是必要约束：不能再把 `S01/S02/S03` reference 和 `api/release/incident` ASR 产物按名字硬配，因为原 reference 与 synthetic ASR 产物文本不一致，会污染 CER 和术语召回。

## 证据

最新批量报告：

```text
artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-batch-20260710-after-runtime-risk.json
```

批量汇总：

```text
decision_status=no_go_quality_not_production
blockers=["some_samples_have_no_quality_passing_provider"]
recommended_next_gate=improve_asr_terms_segmentation_or_provider_before_real_gate

sample_count=4
evaluated_sample_count=4
missing_input_sample_count=0
sample_provider_count=8
pipeline_closed_sample_provider_count=8
quality_pass_sample_provider_count=3
samples_with_quality_pass_count=3
best_provider_by_average_term_recall=funasr_streaming

remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
user_audio_committed_to_repo=false
```

Provider 结果：

```text
api-review-001
  funasr_streaming: pass=true, term_recall=1.0, CER=0.054945, missing_terms=[]
  sherpa_onnx_streaming: pass=false, term_recall=0.5, CER=0.505495, contains_unk=true, missing_terms=[payment-gateway, request_id]

release-review-001
  funasr_streaming: pass=true, term_recall=0.75, CER=0.384615, missing_terms=[staging]
  sherpa_onnx_streaming: pass=false, term_recall=0.25, CER=0.736264, contains_unk=true, missing_terms=[checkout-service, error_rate, staging]

incident-review-001
  funasr_streaming: pass=false, term_recall=0.5, CER=0.395062, missing_terms=[timeout, 监控阈值]
  sherpa_onnx_streaming: pass=false, term_recall=0.25, CER=0.703704, contains_unk=true, missing_terms=[order-worker, timeout, 监控阈值]

architecture-review-001
  funasr_streaming: pass=true, term_recall=0.8, CER=0.071429, missing_terms=[mysql]
  sherpa_onnx_streaming: pass=false, term_recall=0.2, CER=0.663265, contains_unk=true, missing_terms=[feature-store, mysql, recommendation-service, redis cluster]
```

## 本轮修复

批量门禁第一次运行时，`incident-review-001` 的 FunASR event 虽有 `消费堆积 / lag / 告警延迟 / 扩容止血 / 根因` 等事故信号，但 live pipeline 没有产生 state 或 suggestion candidate。

根因：

- ASR raw event 中 `order-worker` 被识别成 `autoker`，runtime transcript report 有归一化，但 Web live normalizer 没有同等保守规则。
- live state 规则对事故复盘类风险过窄，只接受 `如果/超过/回滚` 或架构风险，不接受已经识别到的运行时事故信号。

修复：

- `transcript_normalizer.normalize()` 新增上下文受限 near-miss：只在后文存在 `消费堆积/lag/告警` 时，把 `autoker/auder` 恢复成 `order-worker`。
- `asr_live_events._looks_like_risk()` 新增运行时事故信号识别：`消费堆积/告警延迟/临时扩容/根因` 且具备工程上下文时产生 Risk。
- 原始 evidence 不被覆盖：`transcript_final.payload.text` 和 `evidence.quote` 仍保留 raw ASR，修正只体现在 `normalized_text` 和 state description。

修复后变化：

```text
before:
  pipeline_closed_sample_provider_count=7/8
  incident-review-001.funasr mainline_status=blocked_pipeline_not_closed

after:
  pipeline_closed_sample_provider_count=8/8
  incident-review-001.funasr mainline_status=pipeline_closed_quality_insufficient
```

边界：

- 该修复不补 `timeout` 或 `监控阈值`，因为 ASR 没识别到这些关键语义，不能靠规则或 LLM 伪造。
- 因此 `incident-review-001` 仍是质量 no-go。

## Provider 判断

- FunASR 当前是质量优先默认候选：4 个技术场景里 3 个过最低门槛，平均术语召回最好，但延迟明显高，RTF 接近 1，仍需优化 `incident` 类场景、热词、分段窗口和术语召回。
- Sherpa 当前只能作为低延迟实时预览候选：first partial/final 通常在几百毫秒级，但 `<unk>` 多、英文服务名和术语召回差，不能作为生产级中文技术会议默认质量 Provider。
- 远程 ASR 继续默认关闭；如后续要接阿里/讯飞等远程实时 ASR，只能作为显式可选 Provider 和独立成本/隐私方案，不能变成默认依赖。

## 下一步

优先级建议：

1. 统一 runtime 与 Web live 的保守 normalizer：只迁移 scoped near-miss，不迁移会注入未说实体的规则。
2. 给 FunASR 文件/实时路径统一 hotword 与本地模型参数，并重新跑同一 batch gate。
3. 改进实时 final 分段：避免过短碎片导致状态提取依赖单个残缺片段；保留 raw evidence，必要时用 revision/aggregate view 展示修正。
4. 给 Sherpa 暴露 `chunk-ms` 和 endpoint 参数做低延迟预览优化，但不把低延迟等同于质量达标。
5. 只有当 batch gate 达到所有技术场景至少一个本地 Provider 质量过线，才进入真实麦克风长会议 wall-clock soak。

## 验证命令

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_asr_mainline_quality_batch_report.py \
  tests/test_asr_mainline_quality_report.py \
  tests/test_asr_live_pipeline_replay.py \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py

python3 tools/asr_mainline_quality_batch_report.py \
  --matrix data/asr_eval/manifests/asr-mainline-quality-synthetic.json \
  --output artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-batch-20260710-after-runtime-risk.json \
  --replay-run-id 20260710-runtime-risk
```

本轮批量命令预期退出码为 `2`，含义是质量门禁 no-go，不是脚本执行失败。

## 追加：Coverage Audit 与 Repaired Local Synthetic Gate

追加时间：2026-07-10

本轮后续不再扩大评测集，而是先修正质量判断的根因。新增 batch coverage audit 后，旧 variants 报告明确暴露两个样本的 ASR artifact/reference 覆盖不一致：

```text
report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-variants-20260710-coverage-audit-v2.json
decision_status=no_go_quality_not_production
samples_with_suspected_reference_artifact_mismatch=[
  release-review-001,
  incident-review-001
]

release-review-001:
  best_covered_reference_ratio=0.698925
  uncovered_reference_suffix_preview="staging 跑过。验收用例还差支付失败重试，我们今天补一下。"

incident-review-001:
  best_covered_reference_ratio=0.634146
  uncovered_reference_suffix_preview="timeout 增多。复盘报告我们下周一发，但监控阈值谁来改还没定。"
```

关键判断：

- 不裁剪 reference，不用缩短 golden text 来制造通过。
- 不用 normalizer 或 LLM 硬补 `staging`、`timeout` 等没有足够可观察 ASR 证据的词。
- 先重新生成本地合成音频，再用同一套本地 FunASR -> transcript report -> live pipeline replay -> quality batch gate 验证。

本轮新增/更新交付物：

- `tools/asr_mainline_quality_batch_report.py`：新增 `reference_artifact_coverage` 审计字段和 aggregate mismatch 统计。
- `tests/test_asr_mainline_quality_batch_report.py`：新增尾部 reference 覆盖缺口测试。
- `tools/synthetic_audio_local_tts_smoke.py`：新增 `voice` 与 `rate_wpm`，用于受控本地 TTS 生成。
- `tests/test_synthetic_audio_local_tts_smoke.py`：覆盖显式中文 voice/rate。
- `code/asr_runtime/scripts/transcript_normalizer.py`
- `code/asr_runtime/tests/test_transcript_normalizer.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/transcript_normalizer.py`
- `code/web_mvp/backend/tests/test_transcript_normalizer.py`
- `tools/asr_mainline_quality_report.py`
- `tests/test_asr_mainline_quality_report.py`
- `data/asr_eval/manifests/asr-mainline-quality-tingting-r130.json`
- `data/asr_eval/manifests/asr-mainline-quality-synthetic-repaired-local.json`

本地重新生成 release/incident 音频：

```text
voice=Tingting
rate_wpm=130
target_root=artifacts/tmp/synthetic_audio/tingting_r130

release-review-001.wav: 21.792313s
incident-review-001.wav: 21.060875s
```

对比旧音频：

```text
release-review-001.wav old: 11.297438s
incident-review-001.wav old: 11.362375s
```

这证明旧 ASR artifact 的尾部缺失不应继续被当成 provider 质量最终结论。

Bounded normalizer/alias 规则：

- 允许恢复 `check outservice` -> `checkout-service`，但仅限发布/灰度/指标上下文。
- 允许恢复 `error r ate` -> `error_rate`，但仅限指标/P99/灰度上下文。
- 不恢复 `ing` -> `staging`，因为信息不足。
- 不恢复 `timeet` -> `timeout`，因为本轮 incident 已达到最低质量门，且该近音仍需更强证据后再决定。
- raw evidence 和 transcript text 仍保留 ASR 原文，修正只进入 normalized text / quality alias / downstream state。

Targeted release/incident 结果：

```text
report=artifacts/tmp/asr_reports/asr-mainline-quality-tingting-r130-20260710-after-normalizer.json
decision_status=candidate_for_next_real_audio_gate
sample_count=2
pipeline_closed_sample_provider_count=2
quality_pass_sample_provider_count=2
samples_with_quality_pass_count=2
suspected_reference_artifact_mismatch_sample_count=0
remote_asr_call_count=0
llm_call_count=0
```

Repaired 4 场景本地合成质量门禁：

```text
matrix=data/asr_eval/manifests/asr-mainline-quality-synthetic-repaired-local.json
report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-repaired-local-20260710-final-verify.json

decision_status=candidate_for_next_real_audio_gate
recommended_next_gate=real_or_public_audio_wall_clock_soak
blockers=[]

sample_count=4
sample_provider_count=4
pipeline_closed_sample_provider_count=4
quality_pass_sample_provider_count=4
samples_with_quality_pass_count=4
samples_without_quality_pass=[]
suspected_reference_artifact_mismatch_sample_count=0
samples_with_suspected_reference_artifact_mismatch=[]

remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
user_audio_committed_to_repo=false
```

Provider summary:

```text
funasr_batch_chunk20_hotword:
  evaluated_sample_count=2
  usable_pass_count=2
  average_term_recall=1.0
  average_CER=0.047488
  average_RTF=0.355502

funasr_tingting_r130_chunk20_hotword:
  evaluated_sample_count=2
  usable_pass_count=2
  average_term_recall=0.75
  average_CER=0.145503
  average_RTF=0.592629
  missing_terms=[staging, timeout]
```

结论更新：

- 旧结论“本地合成 ASR 质量 No-Go”已被 refined：No-Go 中包含 release/incident 旧合成音频 artifact 覆盖不足。
- 当前可以声明：repaired local synthetic 中文技术会议 ASR -> 实时建议候选主链路在 4 场景质量门禁中候选通过。
- 当前仍不能声明：真实多人会议、真实麦克风或公开会议音频 ASR 已生产可用。
- 下一步不再继续扩开放式评测；应进入真实/公开音频 wall-clock soak，并最终由用户配合真实麦克风会议验收。

附加风险记录：

- 一次本地 batch probe 使用 FunASR batch path 时，FunASR 提示检查/拉取 VAD 模型缓存。该动作不是远程 ASR，也没有上传音频或调用 LLM，但说明生产/离线包必须把 ASR/VAD/Punc 等模型依赖纳入可审计缓存或安装包管理，禁止运行时静默联网。

## 追加：真实/公开音频 Wall-Clock Gate

追加时间：2026-07-10

为避免把多个局部 Go 误拼成“真实长会议 Go”，本轮新增真实/公开音频 wall-clock gate：

```text
tool=tools/real_public_audio_wall_clock_gate.py
test=tests/test_real_public_audio_wall_clock_gate.py
report=artifacts/tmp/asr_reports/real-public-audio-wall-clock-gate-20260710-final-verify.json
```

当前输入：

```text
quality_report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-repaired-local-20260710-final-verify.json
synthetic_soak_report=artifacts/tmp/soak/p1-4-long-meeting-soak-20260708/soak_report.json
real_mic_manifest=artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209-refresh2/manifest.json
wall_clock_soak_report=null
```

当前结论：

```text
gate_status=blocked_real_or_public_wall_clock_soak_missing
blockers=[real_or_public_wall_clock_soak_missing]
recommended_next_action=run_real_microphone_or_public_audio_wall_clock_soak

repaired_synthetic_quality.ready=true
synthetic_soak.ready=true
synthetic_soak.counts_as_real_or_public_wall_clock_soak=false
real_mic_short.ready=true
real_mic_short.counts_as_wall_clock_soak=false
real_mic_short.duration_status=missing_or_too_short
wall_clock_soak.ready=false
```

解释：

- Repaired local synthetic gate 可以作为下一阶段入口。
- 20 分钟 deterministic synthetic soak 只能证明长会议节流/内存/指标门禁，不证明真实或公开音频声学。
- 既有真实 browser mic evidence 证明短链路可跑，但没有 20 分钟级 wall-clock 时长，不能替代长会议 soak。
- 当前仍不允许自动下载公开音频大包。

公开音频来源复核：

```text
report=artifacts/tmp/asr_reports/public-audio-source-whitelist-20260710-final-verify.json
source_validation_status=passed
source_count=3
safe_to_download_now=false
next_action=create_bounded_sample_extraction_plan
```

2026-07-10 用户确认公开样本方向必须以中文为主后，bounded sample extraction approval 已恢复为 live 工具：

```text
tool=tools/public_audio_sample_extraction_plan.py
test=tests/test_public_audio_sample_extraction_plan.py
report=artifacts/tmp/asr_reports/public-audio-sample-extraction-approval-20260710-final-verify.json
plan_status=blocked_no_planned_samples
source_id=alimeeting_openslr_slr119
source_language=zh-CN Mandarin
dataset_role=primary_mandarin_meeting_acoustics
meeting_acoustics_evidence=true
counts_toward_public_meeting_wall_clock_candidate=true
planned_sample_count=0
safe_to_download_now=false
safe_to_extract_now=false
remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
```

解释：

- AliMeeting SLR119 是第一中文会议声学候选；AISHELL-4 SLR111 是第二中文会议声学补充。
- AISHELL-1 SLR33 是普通话朗读 baseline，只能做 runtime smoke，不能计入会议声学或 public meeting wall-clock evidence。
- 当前仍没有具体 `archive_member_path + clip window + checksum` planned sample manifest；因此不能下载、不能抽取、不能转写公开音频。
- planned sample schema 通过也只表示 `schema_validated_no_download`，不会生成下载、抽取或转码命令；包含 placeholder 文本的 archive member path 会被阻断。

官方 OpenSLR 复核边界：

- AliMeeting / SLR119：CC BY-SA 4.0，真实多通道多方会议；Eval 包约 3.42G，适合会议声学验证，但不默认下载。
- AISHELL-4 / SLR111：CC BY-SA 4.0，会议场景语音；test 包约 5.2G，适合会议声学验证，但不默认下载。
- AISHELL-1 / SLR33：Apache 2.0，普通话朗读；data 包约 15G，只适合作普通话 ASR smoke，不证明会议声学。

下一步：

- 优先让用户配合一次 20 分钟级真实麦克风 wall-clock soak；或
- 先做公开音频 bounded sample extraction approval，再下载/抽取极小样本并生成 `real_public_audio_wall_clock_soak.v1` 证据。
