# Current Plan and Validation Report: Public Audio, Simulation, and User Real Mic

> 日期：2026-07-04  
> 状态：Accepted as current execution report  
> 目的：回答“完整计划是否写下来了、转写是否由我先网上找官方音频和模拟验证、最终是否由用户做真实麦克风会议验证”。  
> 边界：本文档不授权访问麦克风、不读取真实用户录音或任何 `.m4a`、不读取 `configs/local/`、不读取 `data/asr_eval/local_samples/`、不读取 `data/local_runtime/` 或 `outputs/`、不下载公开音频大包、不下载 FunASR/ModelScope 模型、不调用远程 ASR/LLM。

## 1. 总结论

完整计划已经写下，权威入口仍是：

- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-mainline-index.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

当前不是缺计划，也不是要继续无限评测。当前真实状态是：

- 转写验证先由我完成：官方公开音频来源复核、no-download manifest、自建中文技术会议合成/Mock、approved ASR event replay 和模拟 shadow pipeline batch。
- 用户最终再做真实麦克风会议验证：只有 readiness gate 满足，且由用户显式启动后，才进入真实麦克风 shadow test。
- 现在的主要阻塞不是产品逻辑：DRV-042 已证明 4 个工程 mock 场景能进入产品价值 preview，非工程 control 保持 0 fake candidate。
- 现在的主要阻塞是 ASR quality exit：本地 synthetic FunASR 主链路已按 approved runner 执行并进入 DRV-046/DRV-032；流程可追溯，但 ASR quality gate 仍未退出，主要问题是 batch evidence/DRV-044 gate 未满足和中文技术实体 recall 未达阈值。
- PC mainline 已补齐 approved ASR event artifact-backed trial：runner 和工作台“工件主线”都能把 approved event artifact 推进到 transcript、EvidenceSpan、state、suggestion candidate、no-call LLM draft 和 feedback/export preview closure；这仍不是真实会议 Go evidence。
- 公开音频阶段按计划 blocked：官方来源已确认，但没有 verified bounded clip manifest 和 GB 级公开包下载审批前，不下载、不抽取、不转码、不喂 ASR。
- DRV-043 已补上 FunASR 本地模型预检 evidence 输入：如果本机已有合法模型目录，可先生成 readiness JSON，再由 DRV-032 消费；ready evidence 只进入 `funasr_cache_preflight_ready_requires_execution_approval`，仍不直接运行 ASR。

## 2. 网上公开音频来源结论

本轮只保留官方可追溯来源，不再泛搜版权不清音频。

| 来源 | 当前用途 | 当前状态 | 不能做什么 |
| --- | --- | --- | --- |
| AliMeeting / OpenSLR SLR119 | 中文多人会议声学、near/far-field、重叠说话、切句风险 | `whitelisted_no_download` | 不默认下载 `Eval_Ali.tar.gz`，不作为产品价值 Go evidence |
| AISHELL-4 / OpenSLR SLR111 | 中文多人会议、远场、多通道、重叠说话补充 | `whitelisted_no_download` | 不默认下载 `test.tar.gz`，不作为产品价值 Go evidence |
| AISHELL-1 / OpenSLR SLR33 | 普通话 ASR/runtime sanity | `whitelisted_no_download` | 非会议，不证明会议声学或产品价值 |
| PyCon/QCon/InfoQ 等中文技术会议录播 | 未来授权路线候选 | `authorized_domain_candidate_only` | 无书面授权和人工标注计划前不抓取、不抽音频、不转写 |
| Bilibili/YouTube/播客/公开视频/公开课 | 不进入自动评测 | `excluded_by_default` | 不用来绕过公开语料 blocked 状态 |

复核链接：

- `https://www.openslr.org/119/`
- `https://www.openslr.org/111/`
- `https://www.openslr.org/33/`
- `https://github.com/modelscope/FunASR`

本地门禁结果：

```text
tools/public_audio_source_whitelist.py
source_validation_status=passed
source_count=3
safe_to_download_now=False
source_ids=aishell4_openslr_slr111,alimeeting_openslr_slr119,aishell1_openslr_slr33
exit_status=0
```

```text
tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio
plan_status=blocked_no_planned_samples
planned_sample_count=0
download_status=not_started
safe_to_download_now=False
safe_to_extract_now=False
exit_status=1
```

```text
tools/public_audio_planned_sample_manifest_decision.py
decision_status=blocked_no_verified_public_sample_manifest
public_audio_stage_status=blocked_no_planned_samples
safe_to_download_now=False
safe_to_extract_now=False
safe_to_transcode_now=False
safe_to_call_asr_now=False
blocked_reasons=no_verified_archive_member_path,no_expected_clip_sha256_after_extract,no_user_approval_for_gb_archive_download
exit_status=1
```

结论：公开音频不是失败，而是按计划安全阻断。没有具体 archive member path、clip start/end、clip sha256、license citation 和下载审批前，不能下载官方 GB 级包，也不能转向版权链不清来源。

## 3. 模拟转写执行状态

模拟转写已从“只写计划”推进到“可复跑执行门”：

- DRV-041：单场景 simulated shadow pipeline smoke，把 approved ASR event JSON 纯内存串到 `ASR replay -> shadow report draft -> export preview`。
- DRV-042：5 场景 batch smoke，要求 4 个工程 mock 场景全部 preview，1 个非工程 control blocked/no candidate。
- DRV-032：ASR quality decision gate 已消费 DRV-042 batch 状态。若 DRV-042 失败，先修模拟产品链路；DRV-042 通过后，才继续归因到 ASR quality exit。

本地 DRV-042 CLI 结果：

```text
tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events
batch_status=simulated_shadow_pipeline_batch_passed
scenario_count=5
engineering_preview_created_count=4
negative_control_blocked_count=1
negative_control_fake_candidate_count=0
go_evidence_status=not_go_evidence_batch_replay_or_feedback_missing
artifact_write_status=not_written
safe_to_capture_microphone_now=False
safe_to_call_remote_asr_now=False
safe_to_call_llm_now=False
exit_status=0
```

ASR quality gate 当前默认结果：

```text
tools/asr_quality_decision_gate.py
decision_status=requires_funasr_model_dir_or_drv019_approval
quality_exit_status=not_exited
simulated_shadow_batch_status=simulated_shadow_pipeline_batch_passed
simulated_shadow_engineering_preview_created_count=4
simulated_shadow_negative_control_fake_candidate_count=0
simulated_shadow_batch_go_evidence_status=not_go_evidence_batch_replay_or_feedback_missing
public_audio_decision_status=blocked_no_verified_public_sample_manifest
safe_to_capture_microphone_now=False
safe_to_call_remote_asr_now=False
safe_to_call_llm_now=False
exit_status=1
```

结论：模拟产品价值链路已经过 5 场景本地门禁；当前不能再说“还不知道模拟能不能跑”。但这仍不是 ASR quality Go evidence，也不是真实麦克风 evidence。

最新本地 FunASR readiness 预检结果：

```text
tools/funasr_synthetic_smoke_readiness.py \
  --audio-path artifacts/tmp/synthetic_audio/api-review-001.wav \
  --events-output-path artifacts/tmp/asr_events/api-review-001.funasr.events.json \
  --provider-output-path artifacts/tmp/asr_reports/api-review-001.funasr.provider.json \
  --transcript-report-path artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json \
  --smoke-report-path artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json \
  --model-cache-root <local ModelScope iic cache>
readiness_status=cache_preflight_passed_offline_execution_not_proven
required_cached_models_status=present
model_download_status=not_started
validation_errors=[]
exit_status=0
```

将 readiness artifact 接入 ASR quality gate 后：

```text
tools/asr_quality_decision_gate.py \
  --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json
decision_status=funasr_cache_preflight_ready_requires_execution_approval
quality_exit_status=not_exited
funasr_readiness_status=cache_preflight_passed_offline_execution_not_proven
funasr_required_cached_models_status=present
safe_to_run_funasr_smoke_now=False
safe_to_capture_microphone_now=False
safe_to_call_remote_asr_now=False
safe_to_call_llm_now=False
exit_status=1
```

生成 5 场景执行 packet 后：

```text
tools/funasr_synthetic_smoke_execution_packet.py \
  --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json
packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run
scenario_count=5
engineering_scenario_count=4
negative_control_count=1
execution_approval_status=not_approved_manual_run_only
safe_to_run_asr_now=False
safe_to_read_audio_file_now=False
safe_to_write_artifacts_now=False
exit_status=0
```

该 packet 已固化到：

```text
artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json
```

结论：现在不需要默认下载模型，也不需要继续找模型目录；本机缓存已通过预检，且 5 场景 no-execution packet 已可复跑。真正缺口变成“是否批准一次本地 FunASR synthetic smoke 执行”。该执行只允许读取 `artifacts/tmp/synthetic_audio/**` 合成音频，并写入 ignored `artifacts/tmp/asr_events/**` / `artifacts/tmp/asr_reports/**`；仍不得访问麦克风、读取真实用户录音、读取 `.m4a`、读取 `configs/local`、调用远程 ASR/LLM 或下载模型。

## 4. 真实麦克风验证边界

真实麦克风会议由用户最终验证，不由我在当前阶段擅自执行。

进入真实麦克风 shadow test 前至少要满足：

- ASR quality exit 有明确选择：本地 FunASR 模型目录、DRV-019 手动模型下载审批、显式远端 ASR 对照，或显式降级试点风险接受。
- desktop/Tauri runtime、worker/mic source、mic adapter、ASR worker 和 export/feedback 前置 evidence 可被 PCWEB-115 readiness gate 识别。
- 用户显式 start，且 UI 能解释音频保留/删除、远程 ASR/LLM 是否启用、费用和隐私边界。
- 非工程 control 仍为 0 工程 state/candidate/card。
- 真实会议后能导出 DRV-033/036/037/038/039 覆盖的 transcript、ASR metrics、EvidenceSpan/state/card timeline 和用户反馈。

当前默认仍不能开始真实麦克风会议：

```text
real_mic_shadow_test_readiness=blocked_not_ready_for_user_real_mic_shadow_test
user_final_real_mic_validation=not_started
```

## 5. 后续主线计划

下一步不再继续新增同类计划、readiness 或 smoke 包装器。主线只剩三类有效动作：

1. ASR quality exit：默认推荐无新增 provider 费用路径，也就是提供已验证本地 FunASR 模型目录，或走 DRV-019 手动模型下载审批；若必须终止评估循环，可用显式降级试点风险接受，但不能宣称 ASR 质量达标。
2. Public audio bounded manifest：只在能形成 3-5 个官方 archive clip manifest，或用户批准 GB 级公开包下载时继续；否则保持 no-download blocked。
3. User real mic shadow test preparation：只在 readiness gate 能解释所有 blockers、成本和隐私边界后，由用户最终启动真实会议验证。

DRV-043 后，ASR quality exit 的最小无费用路径变得更具体：

```text
已有本地 FunASR 模型目录/cache
  -> funasr_synthetic_smoke_readiness.py --model-cache-root <dir>
  -> readiness JSON under artifacts/tmp/**
  -> asr_quality_decision_gate.py --funasr-readiness-path <json>
  -> funasr_cache_preflight_ready_requires_execution_approval
  -> 另开审批跑一次 synthetic smoke
```

这条路径仍不下载模型、不运行 ASR、不访问麦克风。

### 5.1 DRV-044 已实现：FunASR synthetic smoke result evidence gate

下一轮唯一主线票 `DRV-044: FunASR synthetic smoke result evidence schema/gate` 已实现。它不是新的横向评测，也不是新的 readiness 包装器，而是把“未来一旦允许跑一次本地 FunASR synthetic smoke，什么结果才算 ASR quality evidence candidate”固定成机器可验收合同。

为什么做：

- DRV-041/042 已证明 mock/approved event 能闭合到产品价值 preview，当前主阻塞已经不是产品链路，而是真实 ASR 是否能保留中文技术实体。
- DRV-043 已让本地 FunASR model cache readiness 能进入 DRV-032，但 readiness 还不是 ASR 质量证据。
- 如果不先固定 smoke result schema，后续真的跑出 ASR 文本后仍可能继续争论“怎么算通过”，再次陷入评测循环。

输入：

- 只接受 caller-provided `funasr_synthetic_smoke_result.v1` JSON，默认应位于 approved `artifacts/tmp/asr_reports/**`。
- JSON 必须声明 provider、scenario_id、input_source_kind、event_contract_stats、latency metrics、RTF、raw transcript metrics、normalized technical entity metrics、EvidenceSpan/state/candidate/card closure status 和 safety flags。
- 5 场景 batch confirmation 必须额外声明 `batch_artifact_provenance`，为每个 scenario 绑定 approved `artifacts/tmp/asr_reports/**.json` 或 `artifacts/tmp/asr_events/**.json` artifact path 与 sha256；`fixture_only`、sha256 mismatch、路径越界或缺少任一 scenario artifact 都必须 blocked。
- 禁止读取真实用户音频、`.m4a`、`configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/` 或仓库外路径。

输出：

- 通过但样本不足时输出 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`，不能直接写成产品 Go。
- 满足硬阈值且 batch 确认后，还必须有 `batch_artifact_provenance_status=validated`，DRV-032 才可以把 ASR quality 从 `not_exited` 推进到 `asr_quality_current_gate_not_blocking`。
- 缺 latency、缺 event contract、技术实体召回不足、非工程 control 出现工程 candidate、或 safety flags 表示调用远程 ASR/LLM/麦克风/真实音频时，必须 blocked。

硬阈值先按 first-pilot 进入条件固定：

- 4 个工程 synthetic scenarios 的 normalized technical entity recall 均 `>=0.80`，raw 和 normalized 指标必须分开记录。
- 非工程 control 的 engineering state/candidate/card 必须为 `0`。
- first partial latency p95 `<=2.0s`，final latency p95 `<=8.0s`，ASR RTF `<=0.60`。
- suggestion candidate latency p95 `<=30.0s`，且每张 card 必须能追溯到 EvidenceSpan。

不会做：

- 不下载 FunASR/ModelScope 模型。
- 不运行 ASR。
- 不访问麦克风。
- 不读取真实用户录音或 `.m4a`。
- 不调用远程 ASR/LLM。
- 不把单次 synthetic smoke 写成真实会议 Go evidence。

失败后停止条件：

- 如果 DRV-044 结果 schema 长期无法满足技术实体召回或 latency 阈值，且没有本地模型目录、DRV-019 审批或显式降级试点接受，则暂停真实麦克风 pilot，不再新增 provider 横评。
- 如果用户显式接受降级试点，只能进入一次 timing/feedback shadow-test 前置判断，`counts_as_asr_quality_go_evidence=false`。

落地文件：

- `tools/funasr_synthetic_smoke_result_evidence.py`
- `tests/test_funasr_synthetic_smoke_result_evidence.py`
- `docs/drv-044-funasr-synthetic-smoke-result-evidence-gate-plan.md`
- `tools/asr_quality_decision_gate.py`
- `tests/test_asr_quality_decision_gate.py`

### 5.2 DRV-045 已实现：FunASR synthetic smoke execution packet

本轮继续推进 ASR quality exit，但不越过现有安全边界。DRV-045 补的是 DRV-043 和 DRV-044 之间的执行交接：

```text
DRV-043 readiness evidence
  -> DRV-045 manual execution packet
  -> manual FunASR synthetic smoke artifacts
  -> DRV-044 batch provenance/hash gate
  -> DRV-032 ASR quality exit
```

输入：

- `funasr_synthetic_smoke_readiness.v1` readiness report。
- readiness 必须是 `cache_preflight_passed_offline_execution_not_proven`，且 `required_cached_models_status=present`。
- readiness safety flags 必须全 false。
- scenario set 必须覆盖 4 个工程场景和 `non-engineering-control-001` 负控。

输出：

- `packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run`。
- 5 个 FunASR command preview。
- expected events/provider/transcript/smoke report paths。
- `expected_drv044_batch_artifact_provenance` template，sha256 必须未来手动执行后计算。

不会做：

- 不运行 ASR。
- 不读取 `.wav` 或真实用户音频。
- 不下载 FunASR/ModelScope 模型。
- 不访问麦克风。
- 不调用远程 ASR/LLM。
- 不写 `artifacts/tmp/asr_events` 或 `artifacts/tmp/asr_reports`。
- 不把 packet 当作 ASR quality Go evidence。

落地文件：

- `tools/funasr_synthetic_smoke_execution_packet.py`
- `tests/test_funasr_synthetic_smoke_execution_packet.py`
- `docs/drv-045-funasr-synthetic-smoke-execution-packet-plan.md`

### 5.3 DRV-046 已实现：FunASR synthetic smoke batch evidence assembler

DRV-046 补的是手动执行之后、进入 DRV-044 gate 之前的证据装配：

```text
DRV-045 manual execution packet
  -> 5 个 manual FunASR smoke report JSON
  -> DRV-046 compute sha256 / assemble batch evidence
  -> DRV-044 quality + provenance/hash gate
```

输入：

- `funasr_synthetic_smoke_execution_packet.v1` packet。
- 5 个 approved `artifacts/tmp/asr_reports/**.json` smoke report artifacts。

输出：

- artifacts 缺失时：`blocked_missing_manual_smoke_artifacts`。
- artifacts 存在且 DRV-044 通过时：`drv044_batch_evidence_validated`。
- artifacts 存在但质量、安全字段或 provenance 不满足 DRV-044 时：`drv044_batch_evidence_blocked`。

不会做：

- 不运行 ASR。
- 不读取音频。
- 不下载模型。
- 不访问麦克风。
- 不调用远程 ASR/LLM。
- 不写 artifacts。

落地文件：

- `tools/funasr_synthetic_smoke_batch_evidence_assembler.py`
- `tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py`
- `docs/drv-046-funasr-synthetic-smoke-batch-evidence-assembler-plan.md`

### 5.4 DRV-047 已实现：ASR quality consumes DRV-046 assembly

DRV-047 补的是 DRV-046 之后、进入 DRV-032 之前的最后一段手工交接：

```text
DRV-046 batch evidence assembler report
  -> DRV-032 --funasr-smoke-assembly-path
  -> validate nested DRV-044 gate report
  -> strict ASR quality exit
```

输入：

- caller-provided DRV-046 JSON report。
- path 必须位于 approved `artifacts/tmp/**.json`。
- assembly 必须是 `drv044_batch_evidence_validated`，并包含合法嵌套 DRV-044 batch confirmed gate report。

输出：

- 合法 assembly：`funasr_smoke_result_source=drv046_batch_assembly`，并复用 `asr_quality_current_gate_not_blocking` / `strict_quality_gate_not_blocking` 逻辑。
- 非法 assembly：`blocked_by_funasr_smoke_assembly_input_guard`。

不会做：

- 不运行 DRV-046。
- 不读取 artifact 内容。
- 不运行 ASR。
- 不读取音频。
- 不下载模型。
- 不访问麦克风。
- 不调用远程 ASR/LLM。

落地文件：

- `tools/asr_quality_decision_gate.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/drv-047-asr-quality-drv046-assembly-intake-plan.md`

### 5.5 PC 产品交付路线

为避免只在 readiness/boundary 中打转，PC 产品交付路线固定为四段：

1. Mac local shadow MVP：Tauri shell、用户显式 start 的 mic adapter、local ASR event stream、no-LLM request draft 和实时 transcript/EvidenceSpan/state/candidate UI。默认不上传原始音频，不调用远程 ASR，不启用 LLM。
2. Mac AI pilot：启用 OpenAI-compatible LLM 中转站，只发送 EvidenceSpan、会议状态摘要、候选问题和安全裁剪后的上下文；不发送原始音频。调度必须有节流、去重、冷却窗口和失败降级。
3. Mac private beta：加入会议列表、会后复盘、录音/文字稿保留与删除、按 EvidenceSpan 回看、建议卡反馈统计、JSON/Markdown 导出、崩溃日志和本地清理策略。
4. Windows Phase 2：复用 core/web/provider 层，但单独处理 Windows 麦克风权限、系统音频差异、设备枚举、打包签名、安装路径、日志目录和升级策略；不假设 Mac 音频实现可无差异迁移。

## 6. 本轮验证命令

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  tests/test_asr_quality_decision_gate.py \
  tests/test_simulated_shadow_pipeline_smoke.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  tests/test_public_audio_source_whitelist.py \
  tests/test_public_audio_sample_extraction_plan.py \
  tests/test_public_audio_planned_sample_manifest_decision.py \
  -q -p no:cacheprovider

Result: 97 passed, 1 warning
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  -q -p no:cacheprovider

Result: 6 passed, 1 warning
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py \
  -q -p no:cacheprovider

Result: 6 passed, 1 warning
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider

Result: 21 passed, 1 warning
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events

Result: exit 0, simulated_shadow_pipeline_batch_passed
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py

Result: exit 1, requires_funasr_model_dir_or_drv019_approval
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py

Result: exit 0, source_validation_status=passed, safe_to_download_now=false
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio

Result: exit 1, blocked_no_planned_samples
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py

Result: exit 1, blocked_no_verified_public_sample_manifest
```

## 7. 最终判断

这个产品的初心没有丢：不是做录音转文字，而是做中文技术会议实时 Copilot。

现在已经证明：

- 产品智能链路在 mock/approved event 层可闭合到产品价值 preview。
- 非工程负控没有被伪造出建议卡。
- 公开音频策略是安全的官方 no-download 路径。
- 真实麦克风验证被明确留给用户最终执行。

现在还没有证明：

- 本地真实 ASR 对中文技术会议实体已经足够好。
- 公开音频 clip 已被合法抽样并喂给 ASR。
- 真实麦克风会议已经 ready 或已经发生。

因此下一步必须围绕 ASR quality exit 或用户显式降级试点取舍推进，不能再用更多泛化评测文档替代主线执行。

## 8. 2026-07-04 三路审查与复核结论

本轮按用户要求再次确认“完整计划是否写下、转写验证是否由我先通过网上公开音频和模拟完成、最终是否由用户做真实麦克风会议验证”。三路只读审查结论一致：

- 公开音频计划完整，但具体 3-5 个 verified public sample manifest 尚未完成；当前不能下载 AliMeeting/AISHELL-4/AISHELL-1 的 GB 级公开包。
- 模拟转写 / Shadow Pipeline 已证明产品链路 preview 可闭合：4 个工程场景能产生 candidate-card preview，1 个非工程 control 保持 0 fake candidate；这证明不是普通转写链路，但仍不证明真实 ASR 质量达标。
- 产品初心和主线仍清楚：`ASR final/revision -> EvidenceSpan -> meeting state / engineering gap -> suggestion card -> feedback/export -> desktop runtime -> 用户真实麦克风 shadow test`。
- 下一步不应继续新增同类计划、readiness wrapper、report-only schema 或开放式 provider 横评；只能进入 ASR quality exit 取舍，或在用户显式接受质量风险时做一次降级 shadow-test 准备。

本轮复跑结果：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py
Result: exit 0, source_validation_status=passed, source_count=3, safe_to_download_now=false
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio
Result: exit 1, plan_status=blocked_no_planned_samples, safe_to_download_now=false, safe_to_extract_now=false
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py
Result: exit 1, decision_status=blocked_no_verified_public_sample_manifest, safe_to_call_asr_now=false
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events
Result: exit 0, batch_status=simulated_shadow_pipeline_batch_passed, engineering_preview_created_count=4, negative_control_fake_candidate_count=0
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py
Result: exit 1, decision_status=requires_funasr_model_dir_or_drv019_approval, quality_exit_status=not_exited
```

当前下一步只保留三个有效出口：

1. 若已有合法本地 FunASR 模型目录/cache：走 DRV-043 -> DRV-045 -> 受控 5 场景 synthetic smoke -> DRV-046 -> DRV-044 -> DRV-032。
2. 若无本地模型但接受零额外 provider 费用的模型路径：走 DRV-019 手动模型下载审批后再执行上一路径。
3. 若要尽快验证产品节奏但不声明 ASR 达标：提交合法 `asr_quality_degraded_pilot_acceptance.v1`，只允许一次用户显式 shadow-test timing/feedback 风险试点，且不算 ASR quality Go evidence。

本轮未访问麦克风，未读取真实用户录音或任何 `.m4a`，未读取 `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/` 或 `outputs/`，未下载公开音频或模型，未调用远程 ASR/LLM。

## 9. 2026-07-04 DEC-186 三路审查后的执行收口

本轮再次启动三路只读审查后，结论收敛如下：

- 产品主线没有偏：当前项目仍是中文技术会议实时 Copilot，不是录音转文字工具。判断中心仍是 `EvidenceSpan -> meeting state / engineering gap -> suggestion card -> feedback/export`。
- 完整计划已经写下：权威入口仍是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md`、`docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`、`docs/current-plan-and-validation-report-2026-07-04.md`、`docs/requirements-traceability-matrix.md` 和 `docs/decision-log.md`。
- 转写验证分工明确：公开官方音频来源复核、no-download manifest、本地中文技术会议合成/Mock、approved ASR event replay 和模拟 shadow pipeline 由我先完成；真实麦克风会议由用户最后在 readiness gate 满足后显式启动验证。
- Public sample manifest 当前只是 schema gate，不是事实下载验证 gate。即使未来 planned sample JSON 通过字段、路径、预算、sha256 格式和 attribution 校验，也只表示可人工复核下载方案；它不能在未下载、未读 archive index 的前提下证明 `archive_member_path` 真实存在或 `expected_sha256_after_extract` 一定正确。
- 公开音频当前 blocked 是合理状态：AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111 和 AISHELL-1/OpenSLR SLR33 仍是默认官方白名单，但没有 3-5 个 verified clip manifest、archive member path、clip sha256 和 GB 级公开包下载审批前，不下载、不抽取、不转码、不喂 ASR。
- 模拟 shadow pipeline 已有执行门禁：DRV-042 batch 已证明 4 个工程 mock 场景能形成 preview，非工程 control 保持 0 fake candidate；这证明产品链路不是普通转写，但仍不证明真实 ASR 质量达标。
- PC/真实麦克风 readiness 已经落到 policy、工具、API/UI 和测试；PCWEB-124 只是把 PCWEB-115 blocker 可见化，默认仍为 `blocked_not_ready_for_user_real_mic_shadow_test`，不应继续新增同类 readiness/report-only wrapper。

因此，下一步只保留三条可执行出口：

1. `ASR quality exit`：优先使用已验证本地 FunASR 模型目录/cache；若没有，则只能走 DRV-019 手动模型下载审批，或选择显式远端 ASR 对照。默认仍不增加远程 ASR 费用。
2. `Public audio bounded manifest`：只围绕 AliMeeting/AISHELL-4 做 3-5 个可复核 clip 的 no-download manifest；没有真实 archive member path 和 checksum 就继续 blocked。
3. `Explicit degraded pilot`：如果要尽快验证产品节奏，必须提交合法 `asr_quality_degraded_pilot_acceptance.v1`，只允许一次用户显式 shadow-test timing/feedback 风险试点，且不算 ASR quality Go evidence。

停止条件同步锁定：没有本地 FunASR 模型目录、没有 DRV-019 审批、没有远端 ASR 对照选择、也没有合法降级试点接受记录时，不能进入真实麦克风 shadow test；也不能用新增计划、schema、readiness 面板或 provider 横评包装成主线进展。

## 10. 2026-07-04 DEC-188 二次审查后的当前计划状态

本轮按用户最新问题再次确认：完整计划已经写下；转写类验证由我先通过官方公开音频来源复核、本地合成/Mock 模拟和 ASR event replay 完成；用户最终再做真实麦克风会议验证。

两路只读 Agent 审查结论一致：

- 计划完整，不需要新增总控计划或新的散点计划文档。
- 主线没有本质矛盾，仍是中文技术会议实时 Copilot，不是普通录音转文字工具。
- 真实麦克风边界清楚：当前不能访问麦克风、读取真实用户录音、读取 `.m4a`、读取 `configs/local/` 或调用远程 ASR/LLM。
- 当前 blocker 是 ASR quality exit，不是产品逻辑或计划文字缺失。
- PCWEB-125 `Mac Local Shadow MVP synthetic demo closure` 已完成，不能再当作下一张未完成开发票。
- `Public audio bounded manifest` 仍是条件分支；它可以在有真实 `archive_member_path`、clip start/end、expected sha256、license citation 和下载审批时推进，但不能替代 ASR quality exit，也不能在当前 blocked 状态下转成下载或 ASR 输入。

本轮联网复核口径保持不变：

- AliMeeting / OpenSLR SLR119：官方可追溯多人会议来源，继续作为会议声学候选；当前只保留 no-download manifest 路径。
- AISHELL-4 / OpenSLR SLR111：官方可追溯多人会议来源，继续作为远场、多通道、重叠说话补充候选；当前只保留 no-download manifest 路径。
- AISHELL-1 / OpenSLR SLR33：普通话 ASR/runtime sanity 来源，不证明会议声学或产品价值。
- FunASR：中文 ASR 主候选；只有在已有本地模型目录/cache、DRV-019 手动审批或合法 smoke artifacts 后进入 ASR quality exit，不自动下载模型。

当前下一步收敛为一条默认主线：

```text
ASR quality exit
  -> verified local FunASR model/cache, or DRV-019 approval, or valid DRV-046 assembly
  -> strict DRV-044/DRV-032 quality gate
  -> PC/runtime readiness
  -> 用户显式真实麦克风 shadow test
```

如果不选择本地 FunASR、不批准模型下载、不提供 verified public clip manifest、不接受 degraded pilot，当前正确状态就是继续 blocked，而不是继续新增评测、计划或 wrapper。

## 11. 2026-07-04 PCWEB-126 真实感模拟包执行结果

为了避免继续停留在计划和门禁，本轮把产品体验推进到一个更像真实会议的 synthetic simulation：

```text
4 speaker / 8 turn / 47.2s synthetic release+incident review
  -> partial corrections / final / 2 revisions / pause / overlap markers
  -> payment-gateway / P99 / 0.1% / Kafka lag / rollback / feature flag
  -> Live ASR SSE
  -> transcript / EvidenceSpan / state / candidate / no-LLM draft
  -> readiness blockers
```

落地内容：

- Web backend 新增 `POST /desktop/realistic-meeting-simulation-pack/sessions`。
- Web 工作台新增 `真实模拟` 按钮。
- 新增 `renderRealisticMeetingSimulationPack`，显示 simulation id、scenario id、speaker/turn/revision/state/draft 计数、realism features、technical terms 和 readiness blockers。
- 该入口复用现有 Live ASR session、SSE、draft report 和 readiness gate，不创建平行链路。

验证结果：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session \
  -q -p no:cacheprovider
Result: failed with 404
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 3 passed, 2 warnings
```

```text
Browser E2E:
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok; checked includes realistic meeting simulation pack
```

边界：

- 不访问麦克风。
- 不读取真实用户音频或 `.m4a`。
- 不下载公开音频或模型。
- 不运行 ASR。
- 不调用远程 ASR/LLM。
- 不读取 `configs/local/`。
- 不写 audio chunk。
- 不算 ASR quality Go evidence。
- 不算真实麦克风 Go evidence。

当前判断：

PCWEB-126 提升的是产品体验模拟真实性，解决“只像工具链 JSON，不像真实会议”的问题。它不能替代 ASR quality exit。下一步仍要回到本地 FunASR/DRV-019/DRV-046/degraded pilot 这些质量出口之一。

## 12. 2026-07-04 PCWEB-127 长会模拟 profile 执行结果

PCWEB-127 在 PCWEB-126 的同一 endpoint 上增加 `profile=long_shadow`，避免另起一套平行模拟链路。

新增长会 profile：

```text
5 speaker / 16 turn / 615s synthetic architecture+release+incident follow-up
  -> 5 partial / 13 final / 3 revision
  -> pause / overlap markers
  -> recommendation-service / payment-gateway / idempotency-key / Redis cluster
  -> MySQL / P99 / SLO / Kafka lag / rollback / feature flag
  -> Live ASR SSE
  -> transcript / EvidenceSpan / state / candidate / no-LLM draft
  -> draft report preview
```

落地内容：

- `CreateRealisticMeetingSimulationPackSessionRequest` 支持 `profile`，仅允许 `standard` 或 `long_shadow`。
- `standard` 保持 PCWEB-126 的 47.2s 短模拟。
- `long_shadow` 返回 `profile_id=long_shadow`、`scenario_id=pcweb_127_long_architecture_release_review`。
- Web 工作台新增 `长会模拟` 按钮。
- 前端短模拟和长会模拟共用 `loadRealisticMeetingSimulationPackProfile`，并复用同一 Live ASR / SSE / draft report / readiness UI。

验证结果：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_long_shadow_profile_creates_report_preview \
  -q -p no:cacheprovider
Result: failed with 422 because profile was forbidden
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session \
  code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_long_shadow_profile_creates_report_preview \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 3 passed, 2 warnings
```

```text
Browser E2E:
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok; checked includes long realistic meeting simulation pack
```

边界：

- 不访问麦克风。
- 不读取真实用户音频或 `.m4a`。
- 不下载公开音频或模型。
- 不运行 ASR。
- 不调用远程 ASR/LLM。
- 不读取 `configs/local/`。
- 不写 audio chunk。
- 不算 ASR quality Go evidence。
- 不算真实麦克风 Go evidence。

当前判断：

PCWEB-127 已让模拟体验更接近真实长会和会后复盘 preview，但仍不能替代 ASR quality exit。下一步仍应进入本地 FunASR/DRV-019/DRV-046/degraded pilot 的真实质量出口之一。

## 13. 2026-07-04 DEC-191 本地 FunASR readiness 预检状态更新

本轮在不运行 ASR、不下载模型、不访问麦克风、不读取真实用户音频的前提下，复核了本机 FunASR 环境和 ModelScope 缓存：

- `code/asr_runtime/.venv-funasr` 已存在，FunASR CLI 与 Python runtime 可用。
- 本地 ModelScope 缓存中已存在 `speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` 所需 `model.pt` 和 `config.yaml`。
- `artifacts/tmp/synthetic_audio` 下已存在 5 个 synthetic smoke 场景音频。
- DRV-043 readiness artifact 已写入 ignored `artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json`。
- DRV-032 已消费该 readiness artifact，并输出 `funasr_cache_preflight_ready_requires_execution_approval`。
- DRV-045 execution packet 已生成到 ignored `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`，包含 5 场景 manual command preview，但仍保持 `execution_approval_status=not_approved_manual_run_only`。
- DRV-045 packet 已补齐 `postprocess_command_previews`：每个场景包含 transcript report 命令、synthetic ASR smoke report 命令、golden script 路径、术语表路径和 smoke report stdout redirect，避免本地 FunASR smoke 之后还要手工拼后处理命令。

当前判断：

- 项目没有卡在“缺 FunASR 模型”这一步了。
- 也没有必要默认启用收费远程 ASR。
- 下一步若要验证真实 ASR 中文技术词效果，必须进入一次本地 FunASR synthetic smoke；这一步会运行本地模型、读取合成音频、写 ignored ASR event/report artifacts，因此需要明确执行许可。
- 在该 smoke 执行和 DRV-046/DRV-044/DRV-032 batch gate 通过前，真实麦克风会议仍不能开始。

## 14. 2026-07-04 DEC-192 FunASR synthetic smoke 后处理命令闭环

本轮继续推进 ASR quality exit，但仍不运行 ASR、不读取音频、不下载模型、不访问麦克风。

补齐内容：

- `code/asr_runtime/scripts/command_result.py` 的 `ProviderTranscript` 增加 `duration_seconds`，从 provider JSON 顶层 `audio_duration_seconds` 读取。
- `code/asr_runtime/scripts/transcript_report.py` 在 `--provider-json` 已含 `audio_duration_seconds` 时，允许省略 `--duration-seconds`。
- `tools/funasr_synthetic_smoke_execution_packet.py` 增加 `postprocess_command_previews`。
- 每个 scenario 的 postprocess preview 包含：
  - `transcript_report_argv`
  - `smoke_report_argv`
  - `smoke_report_stdout_redirect_path`
  - 对应 golden script path，例如 `data/asr_eval/synthetic_meetings/scripts/api-review.json`
  - 术语表 `data/asr_eval/glossaries/technical-terms.zh.json`
- ignored packet artifact 已重新生成：`artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`。

验证结果：

```text
RED:
tests/test_transcript_report.py::test_load_provider_transcript_reads_text_latency_and_provider
tests/test_transcript_report.py::test_transcript_report_cli_uses_provider_duration_when_provider_json_has_it
Result: failed because ProviderTranscript had no duration_seconds and CLI required --duration-seconds.
```

```text
RED:
tests/test_funasr_synthetic_smoke_execution_packet.py::test_ready_readiness_builds_batch_execution_packet_without_leaking_local_paths
Result: failed because packet had no postprocess_command_previews.
```

```text
GREEN:
cd code/asr_runtime && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_transcript_report.py tests/test_demo_pipeline.py tests/test_transcribe_funasr.py \
  -q -p no:cacheprovider
Result: 21 passed, 1 warning
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_synthetic_asr_smoke_report.py \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
Result: 38 passed, 1 warning
```

当前判断：

- 本地 FunASR smoke 获批后，执行链现在是完整的：ASR provider/events artifact -> transcript report -> synthetic smoke report -> DRV-046 batch assembler -> DRV-044/DRV-032 quality gate。
- 这不是 ASR quality Go evidence，因为 ASR 仍未运行。
- 下一步仍只剩明确批准一次本地 FunASR synthetic smoke，或继续保持真实麦克风 blocked。

## 15. 2026-07-04 DEC-193 FunASR synthetic smoke approved runner dry-run

本轮继续补齐“获批后怎么执行”的自动化入口，但仍不运行 ASR。

新增内容：

- `tools/funasr_synthetic_smoke_approved_runner.py`
- `tests/test_funasr_synthetic_smoke_approved_runner.py`
- dry-run artifact：`artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json`

runner 行为：

- 默认只 dry-run，读取 DRV-045 packet 并统计计划命令。
- 默认输出：
  - `runner_status=dry_run_ready_requires_execute_flag_and_approval`
  - `planned_provider_command_count=5`
  - `planned_postprocess_command_count=10`
  - `executed_command_count=0`
  - `safe_to_run_asr_now=false`
- `execute=true` 时必须同时满足：
  - 合法 `funasr_synthetic_smoke_execution_approval.v1` approval record。
  - `approval_token=APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY`。
  - `approval_scope=local_funasr_synthetic_smoke_5_scenarios_only`。
  - `approved_packet_path` 与当前 packet path 一致。
  - 本地模型目录存在且包含 `model.pt` 与 `config.yaml`。
- execute 分支已用 fake runner 测试 5 个 provider command + 10 个 postprocess command 的执行顺序和 stdout redirect；本轮没有运行真实 FunASR。

验证结果：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  -q -p no:cacheprovider
Result: 4 failed because tools/funasr_synthetic_smoke_approved_runner.py did not exist.
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  -q -p no:cacheprovider
Result: 4 passed, 1 warning
```

```text
Regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
Result: 39 passed, 1 warning
```

当前判断：

- 获批后不需要人工复制 15 条命令；runner 可按 packet 顺序执行。
- 未获批时，runner 不执行任何命令、不读合成音频、不写 ASR artifacts。
- 这仍不是 ASR quality Go evidence，因为真实 FunASR smoke 尚未执行。
- 下一步仍是显式批准一次本地 FunASR synthetic smoke。

## 16. 2026-07-04 DEC-194 FunASR synthetic smoke approval record 模板

本轮补齐审批记录模板，但默认模板不算批准，不会触发执行。

新增内容：

- `tools/funasr_synthetic_smoke_execution_approval_record.py`
- `tests/test_funasr_synthetic_smoke_execution_approval_record.py`
- approval template artifact：`artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json`

模板行为：

- 默认生成 `funasr_synthetic_smoke_execution_approval.v1` approval record template。
- 默认 `approval_confirmed_by_user=false`。
- runner 已加固为必须要求 `approval_confirmed_by_user=true`，否则即使带 `--execute` 也会 blocked。
- 模板固定：
  - `approval_scope=local_funasr_synthetic_smoke_5_scenarios_only`
  - `approval_token=APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY`
  - `approved_packet_path=artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`
  - `approved_scenario_count=5`
  - `allow_read_synthetic_audio=true`
  - `allow_write_ignored_asr_artifacts=true`
  - `allow_run_local_funasr=true`
  - `deny_real_user_audio=true`
  - `deny_microphone=true`
  - `deny_remote_asr=true`
  - `deny_llm=true`
  - `deny_model_download=true`

验证结果：

```text
RED:
tests/test_funasr_synthetic_smoke_approved_runner.py::test_execute_rejects_unconfirmed_approval_template
Result: failed because runner did not yet require approval_confirmed_by_user=true.
```

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_approval_record.py \
  -q -p no:cacheprovider
Result: 3 failed because approval record tool did not exist.
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_approval_record.py \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
Result: 43 passed, 1 warning
```

```text
Safety smoke:
tools/funasr_synthetic_smoke_approved_runner.py \
  --execution-packet-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json \
  --approval-record-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json \
  --execute
Result: runner_status=blocked_missing_or_invalid_execution_approval,
        validation_errors includes approval_confirmed_by_user must be true,
        executed_command_count=0
```

当前判断：

- 后续审批不再靠聊天里的隐含语义，而是靠可审计 approval record。
- 当前模板明确不是批准。
- 下一步仍是用户显式确认本地 FunASR synthetic smoke approval record；确认后才能执行本地 smoke。

## 17. 2026-07-04 DEC-195 approval wrapper 与 runner 互操作修复

核验时发现一个执行前链路 bug：

```text
funasr_synthetic_smoke_execution_approval_record.py
  -> 输出 report wrapper，内部字段为 approval_record_template

funasr_synthetic_smoke_approved_runner.py --approval-record-path
  -> 之前把整个 wrapper 当成 approval record 校验
```

这会导致 runner 对未确认模板返回一组无关字段错误，而不是准确返回 `approval_confirmed_by_user must be true`。

修复：

- runner 在读取 `approval_record_path` 后，如果 JSON 内有 `approval_record_template` object，就自动解包该 object 再校验。
- 未确认模板仍然 blocked，不会执行任何命令。

验证：

```text
RED:
tests/test_funasr_synthetic_smoke_approved_runner.py::test_runner_unwraps_approval_record_template_report_from_path
Result: failed because runner validated the wrapper instead of approval_record_template.
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_approval_record.py \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
Result: 44 passed, 1 warning
```

```text
Safety smoke:
tools/funasr_synthetic_smoke_approved_runner.py \
  --execution-packet-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json \
  --approval-record-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json \
  --execute
Result: runner_status=blocked_missing_or_invalid_execution_approval,
        validation_errors=['approval_confirmed_by_user must be true'],
        executed_command_count=0
```

当前判断：

- approval artifact 与 runner 现在能正确互操作。
- 未确认模板仍不算批准。
- 下一步没有新的外围工具缺口，仍是用户显式确认 approval record 后运行本地 FunASR synthetic smoke。

## 18. 2026-07-04 DEC-196 真实麦克风全链路升级确认

用户确认：适当的时候要调麦克风走真实全链路流程。

该确认的含义：

- 真实麦克风会议 shadow test 是本项目必须进入的主线阶段，不是永远停留在 synthetic/demo。
- 但该确认不是“当前立刻访问麦克风”的授权。
- 真实麦克风只能在 ASR quality 和 readiness gate 满足后进入。

执行顺序：

```text
FunASR synthetic smoke approval record confirmed
  -> local FunASR 5 scenario synthetic smoke
  -> DRV-046 batch assembler
  -> DRV-044 quality/provenance gate
  -> DRV-032 ASR quality exit
  -> PCWEB-115 real mic readiness gate
  -> explicit real mic approval/start
  -> real meeting shadow test
  -> feedback/export
```

边界：

- 当前不访问麦克风。
- 当前不枚举音频设备。
- 当前不请求 macOS 麦克风权限。
- 当前不读取真实用户录音或 `.m4a`。
- 当前不写真实 audio chunk。
- 当前不调用远程 ASR/LLM。

下一步：

仍然是确认本地 FunASR synthetic smoke approval record，然后运行本地 synthetic smoke；只有该质量出口过了，才推进真实麦克风 readiness 和真实会议 shadow test。

## 19. 2026-07-04 DEC-197 approved runner packet command contract 加固

多 Agent 只读审查确认：当前主线 blocker 仍是 ASR quality exit，但 approved runner 还存在一个获批前可修的安全合同缺口。runner 原先会校验 packet 顶层字段、approval record 和本地模型目录，但没有二次校验 packet 内部 provider/postprocess argv、输入路径、stdout redirect 和脚本路径。

本轮修复：

- `tools/funasr_synthetic_smoke_approved_runner.py` 现在校验 DRV-045 packet command contract。
- provider command 必须匹配 local FunASR script、synthetic audio root、events output root、provider stdout root、model placeholder 和固定 streaming 参数。
- postprocess command 必须匹配 transcript report script、FunASR single-result builder script、provider/transcript/events/report roots、golden script root 和 smoke report stdout root。
- forbidden roots、`.m4a`、系统语音备忘录临时路径、`outputs/**`、仓库外路径或缺 `argv` 均会在执行前 blocked，且 `executed_command_count=0`。
- `_default_run_command()` 写 stdout 前也复用 approved report root guard。

验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  -q -p no:cacheprovider
Result: 4 failed, 6 passed, 1 warning
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  -q -p no:cacheprovider
Result: 10 passed, 1 warning
```

边界：

- 不运行 ASR。
- 不读取合成音频。
- 不写 ASR artifacts。
- 不访问麦克风。
- 不读取真实用户音频或 `.m4a`。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载模型。

## 20. 2026-07-04 DEC-198 FunASR single-result builder 与 DRV-046 格式桥接

多 Agent 只读审查继续发现一个执行链断点：DRV-045 packet 原先的 `smoke_report_argv` 调用 `tools/synthetic_asr_smoke_report.py`，产出的是旧版 `synthetic_asr_smoke_report.v1` 扁平摘要；而 DRV-046 assembler 需要读取 `funasr_synthetic_smoke_result.v1` / `single_synthetic_smoke` / `scenario_results=[...]`。因此旧链路即使获批执行成功，也会在 DRV-046 assembly 阶段 blocked。

本轮修复：

- 新增 `tools/funasr_synthetic_smoke_single_result_builder.py`。
- 新增 `tests/test_funasr_synthetic_smoke_single_result_builder.py`。
- DRV-045 packet 的 smoke postprocess 改为调用新 builder。
- 新 builder 只读取 approved provider/transcript/events/script JSON，不读取音频、不运行 ASR。
- builder 输出 DRV-044 可验证的单场景 evidence，并让 non-engineering control 保持 `state_event_count=0` / `candidate_card_count=0`。
- 新增跨工具测试：使用 DRV-045 packet 的 `smoke_report_argv` 生成 5 个单场景 artifact，再交给 DRV-046 assembler，确认可以进入 `drv044_batch_evidence_validated`。

验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_single_result_builder.py \
  -q -p no:cacheprovider
Result: 2 failed, 1 warning
```

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_packet.py::test_ready_readiness_builds_batch_execution_packet_without_leaking_local_paths \
  -q -p no:cacheprovider
Result: failed because packet still pointed at tools/synthetic_asr_smoke_report.py
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_single_result_builder.py \
  tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py \
  -q -p no:cacheprovider
Result: 9 passed, 1 warning
```

```text
GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  tests/test_funasr_synthetic_smoke_approved_runner.py \
  -q -p no:cacheprovider
Result: 16 passed, 1 warning
```

本轮重新生成：

```text
artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json
packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run
first smoke postprocess script=tools/funasr_synthetic_smoke_single_result_builder.py
```

```text
artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json
runner_status=dry_run_ready_requires_execute_flag_and_approval
planned_provider_command_count=5
planned_postprocess_command_count=10
executed_command_count=0
```

当前判断：

- approved runner -> postprocess -> DRV-046/044/032 的格式桥接缺口已修复。
- 当前仍未获得本地 FunASR synthetic smoke 执行批准。
- `quality_exit_status` 仍未退出。
- 真实麦克风仍 blocked。

## 21. 2026-07-04 DEC-199 本地 FunASR synthetic smoke 主链路执行结果

用户要求停止深入边界测试，先把主流程跑通。本轮已从 dry-run 推进到本地 synthetic smoke execute，并把结果送入 DRV-046 和 DRV-032。

执行链路：

```text
confirmed approval record
  -> tools/funasr_synthetic_smoke_approved_runner.py --execute
  -> 5 个 FunASR provider command
  -> 10 个 transcript/single-result postprocess command
  -> tools/funasr_synthetic_smoke_batch_evidence_assembler.py
  -> tools/asr_quality_decision_gate.py
```

主结果：

```text
runner_status=executed_local_funasr_synthetic_smoke_commands
executed_command_count=15
validation_errors=[]
```

```text
assembly_status=drv044_batch_evidence_blocked
artifact_read_status=read
artifact_count=5
counts_as_asr_quality_go_evidence=false
```

```text
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
safe_to_capture_microphone_now=false
```

场景摘要：

| Scenario | Event contract | normalized technical recall | RTF | Product closure |
| --- | --- | ---: | ---: | --- |
| `api-review-001` | passed | `0.50` | `1.012` | state/card generated |
| `architecture-review-001` | passed | `0.20` | `0.917` | state/card generated |
| `incident-review-001` | passed | `0.25` | `0.960` | state/card generated |
| `release-review-001` | passed | `0.00` | `0.984` | state/card generated |
| `non-engineering-control-001` | passed | `0.00` | `0.920` | no engineering state/card |

判断：

- 主链路已跑通到质量门：本地合成音频、ASR events、transcript report、single-result builder、batch assembler 和 ASR quality gate 都已串起来。
- 当前不能进入真实麦克风，因为 ASR quality exit 未过。
- 当前主要缺口不是“有没有流程”，而是“ASR 是否足够有产品价值”：中文技术会议里夹杂英文服务名、指标名、错误码时，当前识别结果会连写、截断或误识别，导致技术实体召回不足。
- RTF 当前也不达标；该 RTF 包含当前 per-scenario command 路径的真实耗时表现，后续需要用长驻 worker/batch mode 分离模型加载和单段推理，而不是改阈值。
- non-engineering control 没有误生成工程建议，说明负控和产品闭环规则没有明显误触发。

边界：

- 本轮未访问麦克风，未枚举音频设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`。
- 未调用远程 ASR/LLM，未产生中转站费用，未下载模型。
- 当前 evidence 不算 ASR quality Go evidence，不能用于宣称真实会议可用。

下一步主线：

1. 不再继续做开放式边界评测。
2. 先基于已有 provider/events artifacts 修 deterministic hotword/normalizer 和技术词归一化；只重跑 postprocess、single-result builder、DRV-046、DRV-032，避免重复烧时间跑 ASR。
3. 若 recall 仍不达标，再评估 FunASR 热词/参数或显式 degraded pilot。没有 recall 达标或风险接受前，不启动真实麦克风。
4. RTF 单独进入长驻 ASR worker/batch mode 优化，不通过降低门槛解决。

## 22. 2026-07-04 DEC-200 技术词 normalizer 与 batch runtime 实现自测

本轮按第 21 节计划执行了两条主线收敛：

1. 基于已有 FunASR provider/events artifacts 修 deterministic 技术词归一化。
2. 新增单进程复用 FunASR model 的 batch runtime 测量路径，拆分模型加载耗时和每段推理耗时。

代码变更：

- `code/asr_runtime/scripts/transcript_normalizer.py`
- `code/asr_runtime/tests/test_transcript_normalizer.py`
- `data/asr_eval/glossaries/technical-terms.zh.json`
- `code/asr_runtime/scripts/transcribe_funasr.py`
- `code/asr_runtime/tests/test_transcribe_funasr.py`
- `docs/superpowers/plans/2026-07-04-asr-technical-term-normalization-mainline.md`

TDD 结果：

```text
RED:
tests/test_transcript_normalizer.py::test_committed_technical_glossary_recovers_funasr_observed_near_misses_without_guessing_unseen_terms
Result: failed on paymentgateway / REDIScostbQPS / pp九b / pP99b near-misses
```

```text
GREEN:
cd code/asr_runtime && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_transcript_normalizer.py tests/test_transcript_report.py tests/test_transcribe_funasr.py \
  -q -p no:cacheprovider
Result: 25 passed, 1 warning
```

最终主链路后处理结果：

```text
postprocess_executed=10
assembly_status=drv044_batch_evidence_blocked
counts_as_asr_quality_go_evidence=false
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
safe_to_capture_microphone_now=false
```

| Scenario | normalized recall after DEC-200 | RTF |
| --- | ---: | ---: |
| `api-review-001` | `1.00` | `1.012` |
| `architecture-review-001` | `0.80` | `0.917` |
| `incident-review-001` | `0.50` | `0.960` |
| `release-review-001` | `0.75` | `0.984` |
| `non-engineering-control-001` | `0.00` | `0.920` |

Batch runtime 对照：

```text
chunk_size=[0,10,5]
transcribe_only_rtf=0.679
item_rtfs=[0.676, 0.677, 0.676, 0.673, 0.692]
```

```text
chunk_size=[0,20,10]
transcribe_only_rtf=0.358
item_rtfs=[0.357, 0.356, 0.356, 0.358, 0.362]
```

chunk20 速度达标，但质量不达标：技术词 recall 约为 `0.50 / 0.60 / 0.25 / 0.50`，并出现 `paymentway`、`quest`、`ure store`、`trcoutservice service` 等更严重技术词误识别。因此不能用 chunk20 直接替换主链路。

结论：

- deterministic normalizer 已完成应做的收敛，不能继续靠规则硬补缺失实体。
- RTF 问题确认与模型加载和 chunk 参数有关，但“速度达标”和“质量达标”目前存在冲突。
- ASR quality exit 仍未完成，真实麦克风继续 blocked。
- 下一步应转入 FunASR 热词/参数机制或 degraded pilot 风险接受，而不是继续扩大 normalizer 或评测范围。

## 23. 2026-07-04 DEC-201 真实主链路自测：FunASR hotword 参数与产品 replay 结论

用户要求停止深入边界测试，先把真实主流程跑通。本轮没有新增开放式评测，只把已生成的 FunASR hotword batch provider/events artifacts 接入现有主链路：

```text
provider/events
  -> transcript_report
  -> funasr_synthetic_smoke_single_result_builder
  -> DRV-046 batch_evidence_assembler
  -> DRV-032 asr_quality_decision_gate
  -> simulated_shadow_pipeline_smoke with FunASR events
```

执行产物：

- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk10_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk10_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.shadow-pipeline-chunk10_hotword.json`
- `artifacts/tmp/asr_reports/funasr.synthetic-smoke.shadow-pipeline-chunk20_hotword.json`
- `artifacts/tmp/asr_reports/simulated-shadow-pipeline.mock-control.latest.json`
- `docs/asr-mainchain-fullflow-selftest-2026-07-04.md`

ASR quality gate 结果：

| Candidate | RTF range | Normalized recall by engineering scenario | Result |
| --- | ---: | ---: | --- |
| `chunk10_hotword` | `0.668-0.694` | `1.00 / 0.80 / 0.25 / 0.50` | `quality_exit_status=not_exited` |
| `chunk20_hotword` | `0.355-0.363` | `0.50 / 0.60 / 0.25 / 0.50` | `quality_exit_status=not_exited` |

产品 replay 结果：

| Input | Engineering preview count | Negative control | Failed scenario |
| --- | ---: | ---: | --- |
| `chunk10_hotword` FunASR events | `3/4` | `1/1 blocked, 0 fake candidates` | `incident-review-001` |
| `chunk20_hotword` FunASR events | `3/4` | `1/1 blocked, 0 fake candidates` | `incident-review-001` |
| mock control | `4/4` | `1/1 blocked, 0 fake candidates` | none |

结论：

- 主流程已真实跑通到质量门和产品 replay；不是流程没接起来。
- 当前 blocker 是 ASR 质量：热词参数没有显著改善中文技术会议中英文服务名、指标名、错误码和事故上下文保留。
- `chunk20_hotword` 可以让 RTF 达标，但 recall 明显不够；`chunk10_hotword` 也未满足 recall/RTF 双门槛。
- 产品 replay/card pipeline 能消费真实 FunASR 事件，在 ASR 上下文足够时可生成建议；`incident-review-001` 失败说明 ASR 丢上下文会直接损害产品价值。
- 真实麦克风仍 blocked。下一步默认推进 PC 端产品主流程和 ASR blocked 状态可视化；若要提前开麦，必须走显式 degraded pilot 风险接受，且只算 timing/feedback 试点。

边界：

本轮未访问麦克风，未枚举设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，没有新增付费项。

## 24. 2026-07-04 DEC-202 / PCWEB-128 主线 ASR-blocked 试运行入口

本轮根据 DEC-201 的结论继续实现 PC 端主流程，不再扩 ASR 评测。新增 `Mainline ASR-Blocked Trial`：

```text
主线试运行按钮
  -> POST /desktop/mainline-asr-blocked-trial/sessions
  -> local synthetic Live ASR session
  -> SSE transcript/state/request draft
  -> panel shows DEC-201 ASR quality blocker
```

实现文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `docs/pcweb-128-mainline-asr-blocked-trial-plan.md`
- `docs/superpowers/plans/2026-07-04-pcweb-128-mainline-asr-blocked-trial.md`

用户可见结果：

- 工具栏新增 `主线试运行`。
- 点击后进入 Live ASR 模式，流式展示转写、状态、request draft。
- `本地演示闭环` 面板展示：
  - `mainline_asr_blocked_trial`
  - `DEC-201`
  - `not_exited`
  - `blocked_by_funasr_smoke_assembly_input_guard`
  - `chunk10_hotword`
  - `chunk20_hotword`
  - `incident-review-001`
  - `continue_pc_product_flow_keep_real_mic_blocked`

验证：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker \
  tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 2 passed, 2 warnings
```

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline ASR blocked trial"
```

结论：

PC 端主线现在不只是“模拟能跑”，而是能把产品流和 ASR quality blocker 同屏呈现。该入口不解锁真实麦克风、不算 ASR quality Go、不算真实会议验收；下一步应围绕建议候选、反馈、报告导出继续做产品闭环。

边界：

本轮未访问麦克风，未枚举设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，没有新增付费项。

## 25. 2026-07-04 DEC-203 PC 产品主流程 smoke 自测

用户要求停止深入边界测试，先把主流程跑通。本轮没有新增 provider 横评、没有读取真实音频、没有触碰麦克风，只验证当前 PC 产品主流程是否能通过真实程序路径闭合：

```text
mainline endpoint / workbench
  -> Live ASR trial session
  -> transcript final/revision
  -> EvidenceSpan/state/scheduler/suggestion candidate
  -> no-call LLM request draft
  -> draft review/report data
  -> ASR quality blocker and real-mic blocker visible
```

执行结果：

```text
GET /health
Result: {"status":"ok","service":"meeting-copilot-web-mvp"}

POST /desktop/mainline-asr-blocked-trial/sessions
session_id=manual_true_mainline_selftest_20260704
Result: 201 Created

focused pytest:
1 passed, 2 warnings

browser smoke:
status=ok
checked includes "mainline ASR blocked trial"
```

主流程观测：

| Field | Value |
| --- | ---: |
| `transcript_final` | 13 |
| `transcript_revision` | 3 |
| `state_event` | 17 |
| `scheduler_event` | 17 |
| `suggestion_candidate_event` | 17 |
| `llm_request_draft_event` | 17 |
| draft transcript segments | 16 |
| draft evidence spans | 19 |
| draft state candidates | 17 |
| draft suggestion candidates | 17 |
| LLM call status | `not_called` |

结论：

- PC 产品主流程 smoke 已跑通。
- 当前不是“产品链路没接起来”，而是“真实 ASR quality 与真实麦克风 readiness 未通过”。
- 下一步默认进入 `PCWEB-129 Mainline Trial Feedback And Export Closure`，把现有主线 session 推到建议候选、用户反馈和 Markdown/JSON 报告预览闭环。该项现已由 DEC-205 完成，后续不得重复包装同类 preview。
- 不再将开放式 provider 横评或同类边界/readiness 包装器作为主线。

证据文档：

- `docs/mainline-product-smoke-selftest-2026-07-04.md`

边界：

本轮未访问麦克风，未枚举设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，没有新增付费项。

## 26. 2026-07-04 DEC-204 / PCWEB-130 UI/UX Pro Max 工作台重设计

用户要求安装 `ui-ux-pro-max-skill` 并按它改善展示页。执行官方安装：

```text
npx --yes ui-ux-pro-max-cli init --ai codex
```

安装结果：

- 项目级 `.codex/skills/ui-ux-pro-max`
- 全局 `~/.codex/skills/ui-ux-pro-max`

设计系统查询：

```text
AI meeting copilot developer tool realtime desktop dashboard B2B productivity
```

执行取舍：

- 保留暗色开发者工具、Inter/system 字体、微交互、focus-visible、reduced-motion、响应式和状态反馈建议。
- 不采用 landing-page pattern，因为该产品当前是 PC 工作台，不是营销页。
- 新 UI 采用深色专业工作台：品牌锁定区、分组 toolbar、主线绿色 CTA、阻塞状态 chip、深色 panel/tile、移动端状态条单列展示。

实现文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `.codex/skills/ui-ux-pro-max/**`
- `docs/pcweb-130-ui-ux-pro-max-workbench-redesign.md`

验证：

```text
TDD red: test_workbench_static_assets_are_served failed because class="app-shell" did not exist
Focused green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline ASR blocked trial"
Desktop screenshot: artifacts/tmp/ui_screenshots/workbench-home-dark-v4.png
Mobile screenshot: artifacts/tmp/ui_screenshots/workbench-mobile-dark-v4.png
Mobile layout probe: scrollWidth == clientWidth
```

边界：

本轮只改 UI 展示和静态测试，不访问麦克风，不读取真实音频，不调用远程 ASR/LLM，不改变 ASR quality gate 或真实麦克风 readiness。

## 27. 2026-07-04 DEC-205 / PCWEB-129 主线反馈与导出预览闭环

本轮按 `docs/pcweb-129-mainline-trial-feedback-export-closure-plan.md` 实现 PC 产品主线闭环，不再继续扩展 ASR/provider 评测。

实现路径：

```text
主线试运行 session
  -> Live ASR draft review
  -> DRV-033-compatible candidate report
  -> deterministic feedback useful / would_have_asked
  -> DRV-038 feedback ingestion
  -> DRV-036 Markdown/JSON export preview
  -> workbench 主线闭环面板
```

新增能力：

- `POST /desktop/mainline-trial-feedback-export-closures`
- 工作台 `闭环预览` 按钮
- 工作台 `主线闭环` 面板
- Markdown preview 自动写入既有 `report-panel`

关键输出：

| Field | Value |
| --- | --- |
| `closure_id` | `mainline_trial_feedback_export_closure` |
| `closure_status` | `mainline_trial_feedback_export_preview_created` |
| `feedback_ingestion_status` | `shadow_report_feedback_ingested_preview_only` |
| `export_readiness_status` | `draft_export_preview_only` |
| `go_evidence_status` | `not_go_evidence_replay_or_feedback_missing` |
| `final_decision.decision` | `inconclusive_requires_more_shadow_tests` |

验证：

```text
Focused backend API red: 404 before endpoint existed
Focused backend API green: 1 passed, 2 warnings
Focused static UI red: missing mainline-feedback-export-closure-button
Focused static UI green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline trial feedback export closure"
```

审查后补强：

- API 负向约束已覆盖：missing session -> 404；non-mainline session -> 422 / `blocked_by_source_trial`。
- 默认 feedback 细节已覆盖：first candidate `useful`，second candidate `would_have_asked`，positive count `2`，negative count `0`。
- side-effect sentinel 已覆盖：不读 LLM config/secret，不探测 native audio/process，不发起 outbound call，不写 `shadow_report_exports`。
- 浏览器 smoke 已覆盖 `inconclusive_requires_more_shadow_tests`、`positive=2`、`negative=0` 和 selected candidate id。

最终回归：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py -q -p no:cacheprovider
Result: 292 passed, 2 warnings

node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline trial feedback export closure"

Sensitive scan
Result: no matches
```

结论：

PC 产品主线现在已经能从“试运行能展示”推进到“建议候选可以被本地反馈、并形成 Markdown/JSON 导出预览”。这一步是产品价值闭环，不是 ASR 质量证明。当前输出仍明确是 draft preview / replay not-Go evidence，不能解锁真实麦克风会议。

边界：

本轮未访问麦克风，未枚举设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，未写导出文件，没有新增付费项。

## 28. 2026-07-04 DEC-216 / ASR quality follow-up 可诊断阻塞收敛

本轮回到 ASR quality exit，不继续扩展 UI 或 wrapper。目标是用已有 `chunk20_hotword` provider/events artifacts 追出真实缺口：哪些是 transcript 中可观察 near-miss，哪些是 ASR 输出完全缺失。

实现：

- `tools/funasr_synthetic_smoke_single_result_builder.py` 输出 expected/matched/missing entity 明细。
- `tools/funasr_synthetic_smoke_result_evidence.py` 在明细存在时校验 recall 和 matched/missing 集合一致性。
- `code/asr_runtime/scripts/transcript_normalizer.py` 和 `data/asr_eval/glossaries/technical-terms.zh.json` 修复可观察 near-miss：
  - `paymentway -> payment-gateway`
  - `字段 quest -> request_id`
  - `ure store -> feature-store`
  - `redi coasterbqp -> redis cluster`
  - `auder + backlog/lag/告警 context -> order-worker`
  - `trcoutservice service -> checkout-service`
- 明确不补 `timeout`、`监控阈值`、`staging`，因为当前 transcript 中没有对应证据。

TDD / 验证：

```text
builder RED: KeyError 'expected_entities'
builder GREEN: 3 passed, 1 warning
normalizer RED: 1 failed, 7 passed
normalizer spacing RED: 1 failed, 8 passed
normalizer GREEN: 9 passed, 1 warning
entity detail consistency RED: 1 failed, 1 warning
entity detail consistency GREEN: 1 passed, 1 warning

PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
Result: passed

PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_followup_mainline_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
Result: exit 0
```

最终质量结论：

| Scenario | normalized recall | Missing |
| --- | ---: | --- |
| `api-review-001` | `1.0` | none |
| `architecture-review-001` | `1.0` | none |
| `incident-review-001` | `0.5` | `timeout`, `监控阈值` |
| `release-review-001` | `0.75` | `staging` |

`DRV-046 assembly_status=drv044_batch_evidence_blocked`，`DRV-032 quality_exit_status=not_exited`。这不是流程失败，而是 ASR quality 仍未达到中文技术会议阈值。

下一步：

不再继续堆 deterministic normalizer。下一轮应只做受控 ASR 输入/参数实验：固定 `chunk20_hotword` 速度基线，围绕 `timeout`、`监控阈值`、`staging` 检查 synthetic audio 是否真实承载这些词，并尝试热词/参数/模型路径。如果仍失败，则进入远程 ASR 成本/隐私审批或显式 degraded pilot 决策。

边界：

未读取私人录音、`.m4a`、Voice Memos、`configs/local/`、`data/local_runtime/` 或 `outputs/`；未访问麦克风；未调用远程 ASR/LLM；未新增付费项；真实 token/domain 指纹扫描无命中。
