# Complete Forward Plan: Audio Simulation, ASR Handoff, and Real Mic Validation

> 日期：2026-07-03  
> 状态：Accepted as master execution pointer  
> 目的：把“完整计划是否已经写下、转写如何先用公开音频和模拟验证、真实麦克风会议何时由用户最终验证”收束到一个总控入口。  
> 边界：本文档不授权访问麦克风、不授权读取真实用户录音、不授权读取任何 `.m4a`、不授权读取 `configs/local/`、不授权读取 `data/asr_eval/local_samples/`、不授权读取 `data/local_runtime/` 或 `outputs/`、不授权下载公开音频大包、不授权自动下载 FunASR/ModelScope 模型、不授权调用远程 ASR/LLM。

## 1. 结论

完整计划已经写下。后续执行主线固定为：

```text
自建中文技术会议脚本/合成音频
  + 公开授权中文会议音频 sample manifest
  -> 本地 ASR partial/final/revision/error/end_of_stream
  -> EvidenceSpan
  -> meeting state / engineering gap candidate
  -> suggestion candidate/card
  -> feedback/export readiness
  -> desktop runtime / ASR worker handoff
  -> 用户最终真实麦克风会议 shadow test
```

产品初心没有变：它不是普通音频转文字工具，也不是单纯会后总结工具。只有当系统能在会议中及时发现工程讨论缺口，并把建议追溯到 EvidenceSpan，才有继续投入的价值。

2026-07-03 最新复核锁：用户确认转写验证先由我通过网上官方授权公开音频来源和本地模拟完成，真实麦克风会议最后由用户验证。本轮官方来源复核和两个只读审查 Agent 结论一致：计划层面已经完整，执行态缺口是 ASR quality；PCWEB-119 已完成真实 Tauri no-op run，PCWEB-120 已完成从真实 Tauri evidence 到同 session worker mic source manual review packet 的桥接，PCWEB-121 已完成 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制。公开音频只保留 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 的 no-download 路径；没有 verified clip manifest 和下载审批前不下载、不抽取、不转码、不喂 ASR。后续不能把 `plan/schema/readiness` 当作 `execution/quality passed`。

2026-07-03 本轮执行确认：完整计划已经写下，且当前执行方式固定为 `我先用公开官方来源复核 + 本地模拟转写自测`，`用户最后用真实麦克风会议验收`。公开来源的下一步只允许在白名单内做 no-download manifest 或人工审批后的 bounded clip；如果没有具体 archive member path、clip sha256 和下载审批，就保持 blocked，并继续推进合成/Mock replay、ASR quality exit 和 PC desktop 主线。

2026-07-04 最新执行确认：本计划仍是完整计划入口，不再新建散点总控。转写类验证由我负责：继续使用 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 这类官方可追溯来源做 no-download manifest 和未来 bounded sample 计划，同时用自建中文技术会议合成音频、mock streaming events 和 approved replay 做本地模拟；真实麦克风会议由用户最后手动验证。本轮 PCWEB-124 只把 PCWEB-115 readiness 暴露到 Web 工作台，显示当前仍 blocked，不授权访问麦克风、读取真实录音、下载公开音频大包、调用远程 ASR/LLM 或读取 `configs/local/`。

2026-07-04 DRV-041 执行确认：转写模拟不再只停留在 replay/draft/export 三个分散工具上，已新增 `tools/simulated_shadow_pipeline_smoke.py` 把 approved ASR event JSON 纯内存串成 `ASR replay -> shadow report draft -> export preview`。这一步回答“模拟转写出来之后，是否能进入产品价值链路”：工程 mock events 能形成 draft export preview，非工程 control 不伪造候选卡。它仍不代表真实 ASR 质量达标，不访问麦克风，不读取真实音频，不下载公开音频或模型，不调用远程 ASR/LLM。

2026-07-04 DRV-042 执行确认：模拟链路已经从单场景 smoke 扩展成默认 5 场景 batch。`--batch-default-mock-events` 要求 4 个工程 mock 场景全部 preview，非工程 control 保持 blocked/no candidate。真实本地 artifacts 自测输出 4/4 工程 preview、1/1 负控 blocked、0 fake candidate；仍不产生 Go evidence，不写文件，不访问麦克风或远程 provider。

2026-07-04 DRV-032/DRV-042 联动确认：ASR quality decision gate 现在会消费 DRV-042 batch status。若模拟 batch 失败，先返回 `fix_simulated_shadow_pipeline_first`，避免把产品链路问题误判给 ASR provider；当前 batch passed 后，默认仍停在 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。这说明产品 mock/approved event 链路已经过门禁，剩余主阻塞是 ASR quality exit 或 public audio verified clip manifest，不是继续写计划。

2026-07-04 DRV-043 执行确认：FunASR 本地模型预检 evidence 已接入 ASR quality exit。`tools/funasr_synthetic_smoke_readiness.py --model-cache-root` 只检查必需模型文件，不回显本机绝对路径；`tools/asr_quality_decision_gate.py --funasr-readiness-path` 只从 approved `artifacts/tmp/**` JSON 读取 evidence。ready evidence 只让 DRV-032 进入 `funasr_cache_preflight_ready_requires_execution_approval`，仍不运行 ASR、不下载模型、不访问麦克风。

2026-07-04 DRV-044/DEC-177 执行确认：FunASR synthetic smoke result evidence gate 已实现并完成 provenance/hash 加固。`tools/funasr_synthetic_smoke_result_evidence.py` 只验证 caller-provided `funasr_synthetic_smoke_result.v1` JSON，不运行 ASR、不读取音频、不下载模型；单场景通过只输出 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`，5 场景 batch confirmation 必须绑定 approved JSON artifact path 和 sha256，并输出 `batch_artifact_provenance_status=validated` 后，才允许 DRV-032 输出 `asr_quality_current_gate_not_blocking`。该 evidence 仍不是真实麦克风 Go evidence。

2026-07-04 DEC-178 执行确认：新增短入口 `docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`。本轮再次确认完整计划已经写下；转写类验证由我先完成网上官方公开音频来源复核、no-download manifest、本地中文技术会议合成/Mock 模拟和 approved ASR event replay；用户最终再做真实麦克风会议 shadow test。联网复核后默认公开音频白名单仍只保留 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111 和 AISHELL-1/OpenSLR SLR33；MagicData-RAMC 等 NC/ND 或非会议来源只观察，不进入默认自动评测。公开音频阶段没有 verified bounded clip manifest 前继续 blocked，不下载、不抽取、不转码、不喂 ASR。

2026-07-04 DRV-045/DEC-179 执行确认：FunASR synthetic smoke execution packet 已实现。`tools/funasr_synthetic_smoke_execution_packet.py` 只消费 DRV-043 readiness evidence，若 readiness 合法则输出 5 场景 manual command previews、expected output paths 和 DRV-044 batch artifact provenance template；若 readiness 缺失或包含 side-effect flags，则 blocked。该工具不运行 ASR、不读音频、不写 artifacts、不下载模型、不调用远程 provider，默认 `safe_to_execute_now=false`。它的价值是让未来模型目录就绪后，从 readiness 到 smoke artifacts 再到 DRV-044 hash gate 的交接不再靠口头约定。

2026-07-04 DRV-046/DEC-180 执行确认：FunASR synthetic smoke batch evidence assembler 已实现。`tools/funasr_synthetic_smoke_batch_evidence_assembler.py` 只消费 DRV-045 packet 和 5 个 approved smoke report JSON；它读取 JSON artifact bytes 计算 sha256，组装 `batch_synthetic_confirmation`，并调用 DRV-044 gate。默认无 packet 或缺 smoke artifacts 时 blocked；artifact 存在但质量/安全字段未达 DRV-044 阈值时也 blocked。该工具不运行 ASR、不读音频、不写 artifacts、不下载模型、不调用远程 provider。

2026-07-04 DRV-047/DEC-181 执行确认：ASR quality decision gate 已能直接消费 DRV-046 batch evidence assembler report。`tools/asr_quality_decision_gate.py --funasr-smoke-assembly-path` 只读取 approved JSON，校验 `assembly_status=drv044_batch_evidence_validated`、all-false safety flags 和嵌套 DRV-044 batch confirmed gate report；通过后输出 `funasr_smoke_result_source=drv046_batch_assembly` 并复用 strict quality exit。该 intake 不运行 DRV-046、不读取 artifact 内容、不运行 ASR、不读取音频、不下载模型、不调用远程 provider。

## 1.1 文档权威顺序

为避免后续被旧计划带偏，当前文档优先级固定如下：

| 层级 | 文档 | 用法 |
| --- | --- | --- |
| P0 | `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` | 完整未来计划总控入口，回答公开音频、模拟转写、桌面主线和真实麦克风验证 |
| P0 | `docs/current-mainline-index.md` | 当前主线短入口，后续开发默认从这里进入 |
| P0 | `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md` | 计划确认、责任边界、多 Agent 审查结论和下一步锁定 |
| P0 | `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md` | 产品价值、ASR 验证、桌面 runtime 和真实麦克风主执行计划 |
| P0 | `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md` | 公开音频、模拟转写和真实麦克风的计划锁 |
| P1 | `docs/project-current-status-and-forward-plan-2026-07-03.md` | 当前状态报告和阶段计划 |
| P1 | `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md` | 公开/合成音频与真实麦克风验证细节 |
| P1 | `docs/current-plan-and-validation-report-2026-07-04.md` | 当前执行报告，集中回答公开音频、模拟转写、ASR quality blocker 和用户最终真实麦克风验证 |
| P1 | `docs/copilot-product-value-batch-result-2026-07-03.md` | 当前产品价值 gate 结果 |
| P1 | `docs/drv-041-simulated-shadow-pipeline-smoke-plan.md`、`docs/drv-042-simulated-shadow-pipeline-batch-smoke-plan.md` | 模拟转写执行态 smoke/batch 细节；不是 ASR quality Go evidence，也不是真实麦克风 evidence |
| P1 | `docs/requirements-traceability-matrix.md`、`docs/decision-log.md` | 可追溯需求与决策记录 |
| P2 | `docs/superpowers/plans/**`、2026-07-02 阶段报告 | 历史执行记录；若与 2026-07-03 P0/P1 冲突，以 P0/P1 为准 |

已 superseded 的口径：

- 空 public audio sample plan 不再是 `ready_for_manual_download_review`，而是 `blocked_no_planned_samples`。
- 不再继续宽泛 ASR/provider 横评。
- 不再把新 readiness/report-only 文档当作主线进展，除非它直接推进桌面 runtime、ASR worker handoff、麦克风 adapter 或产品价值 gate。
- `PCWEB-092` 这类旧 desktop next step 只作历史记录；PCWEB-097 到 PCWEB-124、DRV-034、DRV-035、DRV-036、DRV-037、DRV-038 和 DRV-039 已完成。下一步不直接进入真实麦克风，只能在明确审批边界下继续 ASR 质量受控路径、真实麦克风 shadow-test 前置清单或明确降级取舍。

## 2. 当前已经证明什么

- `final/revision -> EvidenceSpan -> state/scheduler -> suggestion candidate -> LLM request draft` 的无 LLM 链路已跑通。
- 5 个中文合成技术会议脚本已经建立，并覆盖 API review、release review、incident review、architecture review 和 non-engineering control。
- perfect transcript lane 和 mock ASR lane 当前 5/5 ready，说明 Copilot brain 的基础产品逻辑已经不再是当前最大阻塞。
- non-engineering control 当前保持 0 工程 candidate，这是必须继续保护的误报边界。
- sherpa-onnx 速度可用，RTF 约 0.029-0.035，但中文技术实体质量不足，只能作为性能基线。
- real ASR lane 当前 batch 结论仍是 `blocked_by_asr_quality`，4 个工程场景被 sherpa 技术实体召回阻断。
- FunASR/Paraformer 是中文质量主候选，但当前缺本地 runtime model dir；没有用户明确批准前不能自动下载约 840MB 模型。
- Web local ASR event file handoff API 和 PCWEB-096 desktop ASR worker handoff local dry-run 已经把未来 worker 输出接到 Web Live ASR session 的合同打通，但仍不启动 worker、不访问麦克风。
- PCWEB-110 已把短时本地模拟输入闭合为 timeline report：approved synthetic/mock event file 可以输出 `short_local_simulated_input_status`、`asr_metrics`、`evidence_span_timeline`、`state_timeline` 和 `candidate_card_timeline`；工程输入能闭合到 candidate timeline，非工程 control 保持 0 state / 0 candidate。
- PCWEB-111 已把 replay 输入来源从固定 `approved_synthetic_event_file` 推进到可选 ASR event provenance manifest：manifest 可声明 `synthetic_audio`、`mock_streaming` 或未来 `public_audio_sample`，但必须绑定 events path，并保持 no-LLM/no-remote/no-mic/no-user-audio/no-public-download flags 全 false；复审后已加固 provenance id fields，阻断 `/Users/...`、`configs/local/...`、`data/asr_eval/local_samples/...`、反斜杠路径和 `.m4a` 等路径文本进入 report。
- DRV-034 已把公开音频 post-extraction evidence schema 落地：未来只有在人工批准并完成公开样本抽取后，才允许提交 evidence JSON；该工具只校验 metadata/checksum/cleanup/source attribution，不读取音频、不运行外部命令、不调用 ASR/LLM。
- DRV-035 已把 PCWEB-110/111 replay timeline 映射到 DRV-033 shadow-test report draft schema：草稿包含 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、0 feedback、`inconclusive_requires_more_shadow_tests` 和 `audio_chunk_write_status=not_written`；它只证明模拟 replay 可对齐未来真实验收报告结构，不代表真实麦克风会议已经发生。
- DRV-036 已把 DRV-033/035 report 接入 ingestion/export/feedback readiness：report 可输出 timeline counts、feedback analysis、feedback collection status、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview；replay draft 只允许 `draft_export_preview_only`，真实反馈 report 才能 `ready_for_shadow_test_export`。
- DRV-037 已把 DRV-036 export preview 写入 ignored artifact root：`tools/shadow_report_export_file_writer.py` 只写 `artifacts/tmp/shadow_report_exports` 下的 JSON/Markdown，带 path/sha256/byte count 审计；replay draft 仍不是 Go 证据，真实反馈 report 才能标记为 Go evidence candidate。
- DRV-038 已把真实会议后的用户反馈接入 shadow report：`tools/shadow_report_feedback_ingestion.py`、`POST /shadow-reports/feedback-ingestions` 和 Web `shadow-report-feedback-panel` 可把卡片反馈标签写回 report，并复用 DRV-036 输出 Go/Pivot/Stop readiness；replay draft 即使有正反馈也不会变成 Go 证据。
- DRV-039 已把真实验收反馈写回和 ignored 导出打成 pilot bundle：`tools/shadow_test_pilot_bundle_runner.py` 先调用 DRV-038，再调用 DRV-037；真实 audio-written report 且 feedback 达标时输出 `pilot_bundle_written`，replay draft 只输出 `pilot_bundle_preview_written_not_go_evidence`。
- PCWEB-112 已把 mic adapter contract 和 ASR worker command protocol 做成同一 session 的 connector 门禁：`tools/desktop_worker_mic_connector_contract.py` 复用 PCWEB-105 和 PCWEB-099，合法 request 仍保持 no-execution，并明确 worker `source_kind=mic` 需要后续审批。
- PCWEB-118 已完成第一次受控 Tauri Rust `cargo check`：修复 `generate_context!()` 缺默认 icon 和 Tauri command macro duplicate reexport 两个真实编译阻塞，`Cargo.lock` 已生成并保留，target 输出在 ignored `artifacts/tmp/desktop_tauri_target`。这只证明 Rust crate 可编译，不证明 Tauri WebView、no-op IPC collector/validation、麦克风或 worker 已完成。

## 3. 网上公开音频怎么处理

联网复核后的默认白名单仍只保留授权链清楚的公开语料。公开音频的作用是验证真实会议声学、多人、远场、重叠说话、切句和 ASR event contract；它不证明工程建议卡片的产品价值。

| 来源 | 状态 | 授权 | 允许用途 | 禁止用途 / 当前动作 |
| --- | --- | --- | --- | --- |
| AliMeeting / OpenSLR SLR119 | `whitelisted_no_download` | CC BY-SA 4.0 | 会议声学、near/far-field、重叠说话、切句、ASR event contract | `Eval_Ali.tar.gz` 约 3.42G；只做 bounded sample manifest，不默认下载，不进产品价值 gate |
| AISHELL-4 / OpenSLR SLR111 | `whitelisted_no_download` | CC BY-SA 4.0 | 多人会议、远场、多通道、重叠说话补充 | `test.tar.gz` 约 5.2G；只做 bounded sample manifest，不默认下载，不进产品价值 gate |
| AISHELL-1 / OpenSLR SLR33 | `whitelisted_no_download` | Apache License v2.0 | 普通话 ASR/runtime sanity check | 非会议，不证明会议声学或产品价值 |
| THCHS-30 / OpenSLR SLR18 | `observed_low_priority_not_executable` | Apache License v2.0 | 低优先级中文朗读/噪声补充观察 | 非会议，不在当前工具白名单，不作为默认 public audio 主线 |
| MagicHub Web Meeting | `observed_but_not_whitelisted` | CC BY-NC-ND 4.0 | 小体量 web meeting 候选观察 | NC/ND 边界更敏感；不自动下载、不抽样、不切分/转码、不进入产品价值 gate |
| MagicData-RAMC / OpenSLR SLR123 | `observed_but_not_whitelisted` | CC BY-NC-ND 4.0 | 自发对话/话题多样性候选观察 | 非会议主集且 NC/ND；不自动下载、不抽样、不进入产品价值 gate |
| MISP-Meeting | `observed_but_not_whitelisted` | Non-commercial research license | 真实多人会议、多通道、噪声/房间差异候选观察 | 非商业许可；不自动下载、不抽样、不进入商业 MVP gate |
| Mozilla Common Voice zh-CN | `observed_but_not_whitelisted` | CC0-1.0 | 普通话短句/口音 baseline 候选观察 | 非会议且包体大；不自动下载、不作为会议模拟或产品价值证据 |
| PyCon China / QCon / InfoQ | `authorized_domain_candidate_only` | Authorization required | 真实中文技术词和工程实践域内验证候选 | 公开可观看不等于可抽音频/转写/商用评测；无书面授权和人工标注计划前不进入自动评测 |
| WenetSpeech / OpenSLR SLR121 | `excluded` | CC BY 4.0 metadata with platform-audio provenance caveat | 不采用 | 依赖 YouTube/podcast 平台音频版权链；不下载、不抓取、不进入评测 |

明确不进入自动评测：

- Bilibili、YouTube、播客、直播回放、公开课、技术大会录播。
- 未明确允许下载、切分、转码和本地衍生处理的公开视频。
- 第三方重打包但无法确认原始授权链的数据。
- 需要登录、限制商用、禁止改编或授权不清的数据。
- 依赖 YouTube、podcast 或其他平台音频 URL 的语料，即使页面有元数据许可，也不进入本项目自动评测。

联网复核链接：

- AliMeeting / OpenSLR SLR119：`https://www.openslr.org/119/`
- AISHELL-4 / OpenSLR SLR111：`https://www.openslr.org/111/`
- AISHELL-1 / OpenSLR SLR33：`https://www.openslr.org/33/`
- MagicData-RAMC / OpenSLR SLR123：`https://www.openslr.org/123/`
- MISP-Meeting：`https://challenge.xfyun.cn/misp_dataset`
- Mozilla Common Voice：`https://commonvoice.mozilla.org/`
- PyCon China：`https://cn.pycon.org/2024/`
- QCon / InfoQ：`https://time.geekbang.org/course/intro/101031501`
- FunASR：`https://github.com/modelscope/FunASR`

### 3.1 本轮复核和执行状态

2026-07-03 最新用户确认后，本轮不再扩大音频来源池，只复核官方来源和现有 no-download 工具链：

- OpenSLR SLR119 官方页仍把 AliMeeting 标为 Mandarin multi-channel meeting speech corpus，license 为 CC BY-SA 4.0；本项目只使用其作为会议声学候选，不默认下载 `Eval_Ali.tar.gz`。
- OpenSLR SLR111 官方页仍把 AISHELL-4 标为 conference/meeting scenario Mandarin corpus，license 为 CC BY-SA 4.0；本项目只使用其作为多人、远场、重叠说话补充候选，不默认下载 `test.tar.gz`。
- OpenSLR SLR33 官方页仍把 AISHELL-1 标为 Mandarin speech corpus，license 为 Apache License v2.0；它只能用于普通话 ASR sanity，不进入会议声学或产品价值证据。
- MISP-Meeting、PyCon China、QCon/InfoQ 已作为更贴近真实会议/真实技术词的候选记录，但它们不改变默认白名单：MISP 是 non-commercial research license，技术大会录播需要书面授权和人工标注计划；无授权前不下载、不抽取、不转写、不进入产品价值 gate。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py` 返回 `source_validation_status=passed`，3 个白名单来源全部保持 `download_status=not_started`、`default_download_enabled=false`、`safe_to_download_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio` 返回 `plan_status=blocked_no_planned_samples`，说明还没有 3-5 个具体 clip 的 `archive_member_path`、起止时间、sha256 和 attribution。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py` 返回 `decision_status=blocked_no_verified_public_sample_manifest`，blocked reasons 为 `no_verified_archive_member_path`、`no_expected_clip_sha256_after_extract`、`no_user_approval_for_gb_archive_download`。
- 对应测试 `tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py` 为 `25 passed, 1 warning`。

因此，公开音频阶段当前结论不是“失败”，而是“按计划阻断”：已经找到了合法官方来源，但没有 verified bounded sample manifest 和 GB 级公开包下载审批前，不下载、不抽取、不转码、不喂 ASR。转写模拟继续依赖自建中文技术会议脚本、合成音频、mock streaming events 和本地 ASR event replay；最终真实麦克风会议仍由用户在桌面前置链路满足后执行。

下一步不是继续泛搜更多网站，而是把 AliMeeting 或 AISHELL-4 细化成 3-5 个可复核 clip 的 sample manifest。DRV-031 已把这件事实现为机器可测决策：当前没有真实 archive member path、clip sha256 或 GB 级公开包下载审批，因此保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`，不绕去版权不清来源。

## 4. 模拟转写怎么做

模拟转写分三层，每层回答不同问题。

### 4.1 Perfect Transcript Lane

用标准文字稿验证产品智能层：

- 是否能发现 owner、deadline、rollback、test、metric、monitoring、risk、open question 等工程缺口。
- 是否在 10-30 秒窗口内形成 EvidenceSpan 和 candidate。
- 非工程会议是否仍然 0 工程 candidate。

如果这一层失败，停止 ASR/provider 横评，先修产品逻辑。

### 4.2 Mock ASR Lane

用 mock streaming events 验证事件合同：

- partial 不触发正式 EvidenceSpan 或 LLM。
- final/revision 触发 EvidenceSpan、state/gap 和 request draft。
- revision 能处理 stale/superseded evidence。
- SSE/JSON session 可以被 Web UI 消费。

这一层只证明 pipeline 可跑，不证明真实 ASR 质量。

### 4.3 Real ASR Lane

用合成音频和未来公开授权小样本验证真实转写质量：

- 记录 RTF、first partial latency、final latency、raw/normalized technical entity precision/recall。
- 记录 final/revision 是否能稳定生成 EvidenceSpan。
- 记录 ASR 事件是否满足 `partial/final/revision/error/end_of_stream` 合同。

当前 real ASR lane 的最大风险是中文技术实体保留，而不是速度。

## 5. ASR Provider 决策

默认策略：

1. `mock_streaming`：验证 pipeline，不代表 ASR。
2. `sherpa_onnx_streaming`：本地性能基线，继续保留，但不再作为中文质量主线。
3. `funasr_streaming`：中文质量主候选；官方仓库为 https://github.com/modelscope/FunASR。
4. remote ASR：非默认，只能作为显式可选高质量对照，不作为 MVP 默认成本项。

FunASR 当前边界：

- 只能在用户提供已验证的本地模型目录，或明确批准 DRV-019 模型下载审批包后运行。
- `transcribe_funasr.py --streaming` 必须传入 `--local-model-dir`。
- 目录不完整时返回 blocked，不构造可能自动下载的 `AutoModel`。

## 5.1 下一步决策树

后续不再“走一步问一步”。默认决策如下：

| 条件 | 默认动作 | 不做什么 |
| --- | --- | --- |
| 用户提供已验证 FunASR 本地模型目录，或明确批准 DRV-019 模型下载审批包 | 跑一次 FunASR synthetic smoke，并复跑 tri-lane/batch gate | 不扩大 provider 横评，不调用远程 ASR |
| FunASR 模型目录缺失且未批准下载 | 继续 PC desktop mainline；PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence，PCWEB-120 已完成同 session worker mic source manual review packet bridge，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制，PCWEB-124 已把 readiness blocker 展示到 Web 工作台；当前只推进 ASR quality exit，或提交显式降级试点风险接受后进入 PCWEB-115 其它前置检查 | 不继续给 sherpa 硬编码 normalizer，不从 `<unk>` 猜实体，不再新增只证明 no-execution 的横向 readiness 文档，不重复做已完成的 Tauri no-op run、mic adapter boundary、ASR worker mic source boundary、worker approval evidence 或 readiness UI；降级试点不算 ASR quality Go evidence |
| 公开音频没有 3-5 个 clip manifest | 保持 `blocked_no_planned_samples` | 不下载 GB 级大包，不抓版权不清音频 |
| perfect/mock lane 退化 | 先修产品逻辑 | 不把失败归因给 ASR provider |
| real ASR lane 继续 blocked | 进入模型审批、可选远程 ASR 对照或显式降级试点决策 | 不继续堆 report-only 评测；不把降级试点写成 ASR 质量通过 |
| desktop shell/IPC/worker/mic adapter 未就绪 | 不进入真实麦克风会议 | 不访问麦克风，不读取真实录音 |

## 6. 真实麦克风会议何时开始

真实麦克风会议由用户最终验证。当前我不会擅自访问麦克风，也不会读取用户真实录音。

进入条件：

- desktop app 或 Tauri shell 可运行。
- native IPC no-op 路径已验证。
- 麦克风 adapter 支持用户显式 start/pause/resume/stop/delete。
- 本地 ASR worker 能输出统一事件。
- Web/desktop UI 能展示 transcript、EvidenceSpan、state/gap 和 candidate/card。
- 非工程 control 仍为 0 工程 candidate。
- ASR 技术实体质量至少有可解释的达标路径，或用户明确接受降级试用。

First pilot 协议：

- 1 场 20-30 分钟中文技术会议 shadow test。
- 用户点击 start 后才采集。
- 默认不上传原始音频。
- 默认不调用远程 ASR。
- 如果启用 LLM，只发送 EvidenceSpan 和结构化状态摘要，不发送原始音频。
- 导出 transcript、ASR metrics、state timeline、candidate/card timeline 和用户反馈。

Go evidence：

- 至少 2 个真实中文技术会议场景。
- useful / would_have_asked >= 40%。
- wrong / too_late / too_intrusive <= 20-25%。

### 6.1 First Pilot Checklist

真实麦克风 shadow test 前必须逐项满足：

- 用户已明确同意本次会议 shadow test。
- UI 显示当前输入源、采集状态、duration、chunk count 和错误状态。
- start/pause/resume/stop/delete 都已通过本地 smoke。
- 原始 audio chunk 只写入 ignored runtime root，不写入仓库可提交路径。
- 默认不上传原始音频。
- 默认不调用远程 ASR。
- LLM 若启用，只发送 EvidenceSpan 和结构化状态摘要，不发送原始音频。
- 导出 transcript、ASR metrics、state timeline、candidate/card timeline、feedback summary。
- 每张卡都能标注 `useful`、`would_have_asked`、`wrong`、`too_late`、`too_intrusive` 或 `dismissed`。
- 会议后必须形成 Go / Pivot / Stop 结论。

## 7. 后续执行顺序

### Phase 1: 停止扩大评测，锁定当前结论

状态：进行中。

动作：

- 以 `docs/current-mainline-index.md` 和本文档作为主入口。
- 不再新增宽泛 ASR 横评。
- 不再新增只证明 `safe_to_execute=false` 的横向 readiness 文档。

退出条件：

- 每轮进展必须回答技术实体、EvidenceSpan/gap、非工程 0 candidate 三个问题。

### Phase 2: ASR 质量受控路径

动作：

- DRV-032 已把 ASR 质量路径收束为机器可测 decision gate，并扩展为 ASR quality exit contract：默认只组合 product value batch、FunASR readiness、DRV-019 approval packet 和 DRV-031 public audio decision，不运行 ASR、不下载模型、不访问麦克风、不调用远程 ASR/LLM。当前输出 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`，并稳定列出本地 FunASR、DRV-019、可选远端 ASR 对照和显式降级试点四个出口。
- FunASR 本地模型目录就绪后，只跑一次 synthetic smoke。
- 如果模型目录不就绪，停在 DRV-019 Need model approval，不静默下载。
- bounded normalizer/hotword 只恢复 ASR 文本里已有线索，不从 `<unk>` 或缺失文本猜实体。

退出条件：

- 严格质量退出：FunASR 或其它本地 ASR quality evidence 必须覆盖 4 个工程 synthetic scenarios，且 normalized technical entity recall 均 `>=0.80`；raw recall 和 normalized recall 必须分开记录，不能用 normalizer 掩盖原始 ASR 质量。
- 负控退出：non-engineering control 的 engineering state、candidate 和 card 必须全部为 `0`；任何假工程 candidate 都先修产品逻辑，不进入真实麦克风。
- 实时性退出：first partial latency p95 `<=2.0s`，final latency p95 `<=8.0s`，ASR RTF `<=0.60`，suggestion candidate latency p95 `<=30.0s`。
- 证据链退出：每张 suggestion candidate/card 必须能追溯到 EvidenceSpan；缺 EvidenceSpan、缺 final/revision 事件、缺 latency/RTF 或 safety flags 不干净时，不能作为 ASR quality Go evidence。
- DRV-044 出口：`DRV-044 FunASR synthetic smoke result evidence schema/gate` 已实现，先固定 future smoke result 的机器可验收合同，再决定是否执行一次本地 FunASR smoke。单次 smoke 只能输出 quality candidate，仍需 batch confirmation。
- DRV-045 出口：`DRV-045 FunASR synthetic smoke execution packet` 已实现，先从 DRV-043 readiness 生成 5 场景 manual command preview、expected outputs 和 DRV-044 provenance template；它不执行 ASR，只把未来本地 smoke 的人工执行和证据交接固定下来。
- DRV-046 出口：`DRV-046 FunASR synthetic smoke batch evidence assembler` 已实现，未来手动 smoke report artifacts 生成后，用它计算 sha256、组装 batch evidence 并交给 DRV-044；它不执行 ASR、不写产物，只把 artifacts 到 quality gate 的交接固定下来。
- DRV-047 出口：`DRV-047 ASR quality DRV-046 assembly intake` 已实现，DRV-032 可直接读取 DRV-046 assembly report/path，校验嵌套 DRV-044 gate report 后进入 strict quality exit；它不运行 DRV-046、不读取 artifact 内容、不运行 ASR。
- 降级试点退出：合法 `asr_quality_degraded_pilot_acceptance.v1` 可输出 `degraded_pilot_accepted_with_quality_risk`，只允许一次用户手动 shadow-test timing/feedback 验证，`counts_as_asr_quality_go_evidence=false`。
- 当前默认决策是 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。如果 FunASR 本地模型目录不可用且不批准 DRV-019，也不接受显式降级试点，则暂停真实麦克风 pilot，不继续新增 provider 横评或 report-only readiness。

### Phase 3: 公开音频 sample manifest

动作：

- 只针对 AliMeeting Eval 或 AISHELL-4 test 形成 3-5 个 sample manifest。
- manifest 必须包含 archive、archive member path、clip start/end、sha256、license citation、cleanup policy。
- 校验通过也只表示 `schema_validated_no_download`。
- DRV-031 当前默认输出 `blocked_no_verified_public_sample_manifest`，原因是缺少真实 archive member path、缺少 expected clip sha256、且没有用户批准 GB 级公开包下载。

退出条件：

- 已明确 `blocked: no bounded public sample manifest without large archive download`；后续除非用户提供合法 planned samples 文件或明确批准公开包下载，否则公开音频阶段不再作为下一主线。

### Phase 4: Desktop no-op runtime / IPC

动作：

- PCWEB-097 已完成：PCWEB-096 dry-run 状态已展示到 Web/Tauri no-op readiness UI/API。
- PCWEB-098 已完成：定义 desktop ASR worker process contract，包括 future worker lifecycle、command catalog、resource limits、event output contract、approved roots 和 no-spawn/no-audio/no-remote safety flags。
- PCWEB-099 已完成：定义 worker command request/response protocol、lifecycle transition preview 和 blocked response envelope；该阶段仍然只做协议和校验，不启动 worker、不读写音频、不调用 Web mutation、不运行 Cargo/Tauri。
- PCWEB-100 已完成：用 PCWEB-099 command requests 跑通 synthetic lifecycle harness，并在 `collect_events` 阶段复用 PCWEB-096 临时 Web handoff；仍不启动真实 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- PCWEB-101 已完成：定义 worker implementation approval packet，记录 future entrypoint、command runner、event/runtime roots、provider/source mode、资源预算和 approval tokens；合法 packet 也只返回人工 review preview，不批准实现或执行 worker。
- PCWEB-102 已完成：定义 no-execution worker sidecar skeleton 和静态 report，包含 worker identity、command envelope、lifecycle state、event writer、provider adapter、health/status 和 cleanup preview；仍不启动 worker、不访问麦克风、不读写 event file、不写 runtime audio、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-103 已完成：定义 desktop command runner binding preview 和静态 report，验证 sidecar module path、future native command runner path、command catalog、provider/source 和 root 边界；合法 request 也只返回 `ready_for_no_execution_binding_review`，仍不绑定 Tauri command、不 invoke IPC、不 spawn subprocess、不 dispatch worker command、不 health probe、不 collect events、不读写 event file、不访问麦克风、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-104 已完成：创建 inert Rust command runner skeleton 和 no-dispatch 静态 report；Rust 文件未在 `lib.rs` 中绑定，合法 request 也只返回 `ready_for_no_dispatch_skeleton_review` 和 blocked command preview，仍不 accept/dispatch worker command、不 invoke Tauri IPC、不 spawn subprocess、不读写 event file、不访问麦克风、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-108 已完成：把 PCWEB-096/100 的 synthetic worker-like event file handoff 从“Web API 接收成功”提升为 closure gate；`synthetic_local_test` 必须从临时 Web Live ASR session response 中证明 transcript final、EvidenceSpan、state event、scheduler event、suggestion candidate 和 LLM request draft 已生成；非工程输入即使有 transcript/EvidenceSpan 也会因没有 state/gap candidate 返回 `blocked_by_live_session_closure`。
- PCWEB-118 已完成：使用 ignored 临时 Rust/Cargo home 和 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target` 跑通第一次受控 `cargo check`；`src-tauri/Cargo.lock` 已成为桌面 app reproducibility artifact。后续不再把“缺少 Rust/Cargo”作为桌面主线 blocker，但仍不能把编译通过等同于 Tauri WebView run。
- PCWEB-119 已完成：真实 Tauri WebView 加载本地 Web MVP，通过 `window.__TAURI__` 调用 10 个 no-op IPC，并自动提交 PCWEB-117 validation；validation passed evidence 已写入 ignored `artifacts/tmp/desktop_tauri_noop_run_results`。该 evidence 只证明 no-op IPC runtime 和 validation capture，不代表麦克风、worker、真实 mic adapter 或 ASR quality 已完成。

退出条件：

- PCWEB-119 已满足：Tauri WebView 内能调用 10 个 no-op IPC，且 validation passed evidence 已捕获。

### Phase 4.1: PC 产品交付路线

动作：

- Mac local shadow MVP：以 Tauri shell 为主形态，支持用户显式 start/pause/resume/stop/delete 的 mic adapter、local ASR event stream、实时 transcript、EvidenceSpan、meeting state、gap candidate 和 no-LLM request draft。默认不上传原始音频，不调用远程 ASR，不启用 LLM。
- Mac AI pilot：在 ASR quality exit 或显式降级试点接受后，启用 OpenAI-compatible LLM 中转站；只发送 EvidenceSpan、结构化会议状态摘要、候选问题和必要上下文，不发送原始音频。LLM 调度必须有 10-30 秒窗口、去重、节流、冷却、失败降级和成本记录。
- Mac private beta：补齐会议列表、会后复盘、录音和文字稿保留/删除策略、按 EvidenceSpan 回看、建议卡反馈统计、JSON/Markdown 导出、崩溃日志、本地清理和升级策略。
- Windows Phase 2：复用 core/web/provider/LLM schema 层；Windows 单独实现和验证麦克风权限、系统音频差异、设备枚举、安装路径、日志目录、打包签名和升级策略。不承诺 Mac 音频实现可无差异迁移。

退出条件：

- Mac local shadow MVP 能在不调用远程 ASR/LLM 的情况下完成 transcript -> EvidenceSpan -> state/gap -> candidate preview。
- Mac AI pilot 能在中转站成本可控、隐私边界可解释、失败可降级的前提下生成实时建议和阶段总结。
- Private beta 必须能导出真实会议 report、feedback summary 和删除/保留审计。
- Windows 只有在 Mac MVP 形成真实会议证据后进入，不与 Mac MVP 并行抢主线。

当前下一步候选必须另起计划和 TDD，只保留三类：

```text
ASR quality exit / explicitly approved minimal real mic adapter implementation boundary
  without spawning worker, reading/writing real audio, mutating production Web sessions,
  calling remote providers, downloading models, or running Cargo/Tauri without explicit approval
```

验收边界：

- 只定义 future worker command envelope、response envelope、allowed lifecycle transition preview、blocked reasons、approved event/runtime roots 和 safety flags。
- 不启动 worker。
- 不访问麦克风。
- 不读取或写入 runtime audio。
- 不读取或写入 event file。
- 不调用 Web handoff mutation。
- 不读取真实音频。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不运行 Cargo/Tauri，除非另有明确授权。

### Phase 5: ASR worker handoff

动作：

- 从 synthetic/local event file handoff 推进到 worker 进程合同和 command protocol。
- worker 只输出 event file/stream，不直接驱动 LLM。
- Web backend 继续只接受 allowed event roots。

退出条件：

- desktop descriptor -> worker output -> Web Live ASR session 可重复跑通。

### Phase 6: Mic adapter and real shadow test

动作：

- PCWEB-105 已完成麦克风 adapter 合同和删除语义：定义 `prepare/status/start/pause/resume/stop/delete_audio_chunks`、显式用户 start 边界、ignored runtime audio root、audio chunk root 和 all-false safety flags；仍不访问麦克风、不请求权限、不写真实音频。
- PCWEB-106 已完成麦克风 adapter contract readiness UI/API：新增 `GET /desktop/mic-adapter-contract-readiness` 和 `desktop-mic-adapter-contract-panel`，复用 PCWEB-105 静态合同报告展示 `specified_not_executable`、7 个 mic adapter command、approved roots、delete semantics 和 all-false safety flags；ASR handoff readiness 现指向 `next_pcweb_id=PCWEB-106`；仍不请求权限、不枚举设备、不采集或写入真实音频、不删除真实音频、不运行 Cargo/Tauri。
- PCWEB-107 已完成 mic adapter no-op Tauri IPC 静态绑定：Tauri scaffold 绑定 10 个 no-op command，其中 7 个是 `mic_adapter.*`；static smoke tool 已校验 exact catalog、handler set、mapping 和 no-side-effect fields；仍不运行 Cargo/Tauri、不请求权限、不枚举设备、不采集或写入真实音频、不删除真实音频。
- PCWEB-109 已完成 mic adapter no-op UI invocation：Web 工作台在 `desktop-mic-adapter-contract-panel` 中追加 7 个 no-op invocation row；普通浏览器显示 `mic_adapter_browser_fallback` / `not_invoked`，未来 Tauri WebView 才调用 PCWEB-107 no-op IPC；仍不请求权限、不枚举设备、不采集或写入真实音频、不删除真实音频、不运行 Cargo/Tauri。
- PCWEB-112 已完成 worker/mic connector contract：`tools/desktop_worker_mic_connector_contract.py` 把 PCWEB-105 `mic_adapter.start` preview 和 PCWEB-099 `worker.prepare(source_kind=mic)` 阻塞结果合并为同一 session 的组合门禁；合法 request 输出 `ready_for_worker_mic_connector_contract_review`，但 worker mic source 仍需未来审批。该阶段仍不访问麦克风、不请求权限、不读写 audio chunk、不启动 worker、不读写 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- PCWEB-113 已完成 Tauri no-op run result intake：`tools/desktop_tauri_noop_run_result_intake.py` 接收未来显式批准真实 Tauri WebView no-op run 后的 caller-provided JSON，验证 PCWEB-107 的 10 个 no-op IPC command 全部 returned，且每个返回保持 `noop_bound/noop_only/tauri_ipc_bound` 和 no-side-effect flags。合法 result 只表示 `validated_noop_ipc_observed`，并把下一步推进到 `review_worker_mic_source_approval_packet`；仍不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 event file、不调用远程 ASR/LLM。
- PCWEB-114 已完成 worker mic source approval packet：`tools/desktop_worker_mic_source_approval.py` 把 PCWEB-112 connector request 和 PCWEB-113 Tauri no-op result 合成同一 session 的人工审批包；合法输入只输出 `ready_for_manual_review_not_executable`、`worker_mic_source_approval_status=not_approved` 和 `manual_approve_worker_prepare_source_kind_mic_or_keep_blocked`，不移除真实执行边界。该阶段仍不访问麦克风、不请求权限、不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk 或 event file、不运行 Cargo/Tauri、不调用远程 ASR/LLM。
- PCWEB-115 已完成 real mic shadow-test readiness gate：`tools/real_mic_shadow_test_readiness_gate.py` 把 ASR 质量、真实 Tauri no-op evidence、worker mic source approval、真实 mic adapter、ASR worker mic source 和 export/feedback readiness 组合成真实会议 go/no-go。PCWEB-119 已补齐真实 Tauri no-op result，PCWEB-120 已补齐同 session manual review packet，PCWEB-121 已补齐最小 mic adapter implementation-boundary evidence，PCWEB-122 已补齐 ASR worker real mic source boundary evidence，PCWEB-123 已补齐单次 worker mic source approval evidence 机制；默认提供合法 PCWEB-123 evidence 后真实会议仍被 ASR quality 阻断。PCWEB-115 现在能消费 DRV-032 的显式降级试点风险接受，但该路径只解锁一次用户手动 shadow-test 前置判断，不证明 ASR 质量达标。PCWEB-115 CLI 已支持 inline JSON 和 approved `artifacts/tmp/**` JSON evidence path 输入，使 readiness 可以从已有证据文件执行计算；路径守卫会在读取前阻断 forbidden roots、音频文件、仓库外路径和非 JSON。该阶段仍不访问麦克风、不读取真实音频、不运行 Cargo/Tauri、不启动 worker、不调用远程 ASR/LLM、不下载模型或公开音频。
- PCWEB-116 已完成 Tauri no-op run result collector：Web 工作台在普通浏览器展示 `collector_browser_fallback`、`desktop_tauri_noop_run_result.v1`、10 个 `not_invoked` row 和 `real_tauri_noop_result_ready=false`；未来 Tauri WebView 中可通过 `window.__TAURI__` 调 10 个 no-op IPC，并把 result 放到 `window.__meetingCopilotTauriNoopRunResult` 供 PCWEB-113 摄入。该阶段仍不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不写本地文件、不调用远程 ASR/LLM。
- PCWEB-117 已完成 Tauri no-op run result validation API/UI：Web backend 新增 `POST /desktop/tauri-noop-run-results/validations`，把 PCWEB-116 collector result 交给 PCWEB-113 validator；普通浏览器只显示 `validation_browser_fallback` / `pcweb_117_validation_status=not_submitted`，未来 Tauri WebView 中 10 个 no-op IPC 全部 returned 后才自动提交 validation。该阶段仍不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不写本地 result 文件、不调用远程 ASR/LLM。
- PCWEB-118 已完成 first controlled Tauri `cargo check`：Tauri Rust scaffold 已能编译，PCWEB-116/117 不再卡在 Rust crate compile blocker；PCWEB-119 已完成真实 Tauri WebView no-op run evidence，PCWEB-120 已完成同 session worker mic source bridge，PCWEB-121/122 已完成 mic adapter 与 ASR worker mic source implementation boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制。该事实不代表麦克风、worker execution 或 ASR quality 已完成。
- PCWEB-119 已完成 real Tauri no-op WebView IPC evidence：真实 Tauri WebView 加载 `http://127.0.0.1:8765/`，Web collector 调用 10 个 no-op IPC 并提交 validation；capture evidence 为 `captured_validated_tauri_noop_run`、`validated_command_count=10`、`returned_command_count=10` 和 `ready_for_worker_mic_source_approval_review`。该阶段仍不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM。
- PCWEB-120 已完成 worker mic source from real Tauri evidence bridge：`tools/desktop_worker_mic_source_from_tauri_evidence.py` 读取 PCWEB-119 ignored evidence，校验 capture/validation/run_result 后派生同 session PCWEB-112 connector request，并调用 PCWEB-114 输出 `ready_for_manual_review_not_executable` packet；真实 evidence CLI run 仍为 `worker_mic_source_approval_status=not_approved`。该阶段仍不批准 worker mic source、不访问麦克风、不请求权限、不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk、不调用远程 ASR/LLM。
- PCWEB-121 已完成 minimal mic adapter implementation boundary：`code/desktop_tauri/src-tauri/src/mic_adapter_runtime.rs` 只定义 inert boundary evidence，`tools/desktop_mic_adapter_implementation_boundary.py` 只做静态 policy/Rust 校验并输出 readiness gate 兼容 evidence。它移除的是 `mic_adapter_real_implementation_not_available` 前置缺口，不代表真实权限请求、麦克风采集、audio chunk 写入/删除或 worker mic source 已获批。
- PCWEB-122 已完成 ASR worker real mic source boundary：`code/desktop_tauri/src-tauri/src/asr_worker_mic_source_runtime.rs` 只定义 inert boundary evidence，`tools/desktop_asr_worker_real_mic_source_boundary.py` 只做静态 policy/Rust 校验并输出 readiness gate 兼容 evidence。它移除的是 `asr_worker_real_mic_source_not_available` 前置缺口，不代表真实 worker 已启动、`worker.prepare(source_kind=mic)` 已执行、event/audio 文件已读写、麦克风已访问或 ASR quality 已达标。
- PCWEB-123 已完成 worker mic source single-shadow approval evidence：`tools/desktop_worker_mic_source_single_shadow_approval.py` 只接收 caller-provided manual review packet report 和 approval record，校验 token、session、scope 和 all-false safety flags，并输出 readiness gate 兼容 evidence。它移除的是 `worker_mic_source_not_approved` 前置缺口；默认无 approval record 时仍 blocked/not approved，不代表真实 worker 已启动、`worker.prepare(source_kind=mic)` 已执行、event/audio 文件已读写、麦克风已访问或 ASR quality 已达标。
- PCWEB-124 已完成 real mic shadow-test readiness API/UI visibility：`GET /desktop/real-mic-shadow-test-readiness` 和 Web `desktop-real-mic-shadow-readiness-panel` 只展示 PCWEB-115 默认静态 readiness、blockers、pilot protocol 和 safety flags，不读取 evidence 文件、不创建真实存储、不访问麦克风、不启动 worker、不调用远程 provider。默认仍显示 `blocked_not_ready_for_user_real_mic_shadow_test`，它的作用是让当前阻塞可见，而不是宣布真实会议 ready。
- DRV-041 已完成 simulated shadow pipeline smoke runner：`tools/simulated_shadow_pipeline_smoke.py` 只消费 approved ASR event JSON 和可选 provenance manifest，复用 replay、shadow draft 和 export preview builder，在内存中输出 `simulated_shadow_pipeline_preview_created`。工程 mock events 可生成 `draft_export_preview_only`，非工程 control 会 `blocked_by_no_candidate_timeline`；该工具不写导出文件、不访问麦克风、不读取真实音频、不下载公开音频或模型、不调用远程 ASR/LLM。
- DRV-042 已完成 simulated shadow pipeline batch smoke：默认 5 个 mock 场景形成批量门，4 个工程场景必须 preview，非工程 control 必须 no candidate；该 batch 只做内存汇总，不写 runtime artifact，不访问麦克风、不读取真实音频、不下载公开音频或模型、不调用远程 ASR/LLM。
- PCWEB-110 已完成短时本地模拟输入 timeline report：`tools/asr_live_pipeline_replay.py` 在 replay report 中输出 approved synthetic event source、timeline window、ASR metrics、EvidenceSpan timeline、state timeline 和 candidate/card timeline；工程 mock sample 返回 `closed_to_candidate_timeline`，非工程 control 返回 `no_engineering_candidate_detected` 且 0 candidate。该输入只能来自合成生成音频、mock events 或 approved synthetic event file；不得读取 `.m4a`、本地私有短音频、`data/asr_eval/local_samples` 或任意用户录音。
- PCWEB-111 已完成 ASR event provenance manifest：`tools/asr_live_pipeline_replay.py` 支持 `--event-manifest-path`，manifest 必须位于 `artifacts/tmp/asr_events`、绑定 replay events path、声明 approved `input_source_kind`，并保持 side-effect flags 全 false；provenance id fields 不得包含绝对路径、相对 forbidden-root 文本、反斜杠路径或 `.m4a`；blocked manifest 不读取 events、不生成 timeline。该阶段仍不读取音频、不下载公开包、不运行 ASR、不调用远程 ASR/LLM。
- DRV-034 已完成公开音频 post-extraction evidence schema：`tools/public_audio_post_extraction_evidence_schema.py` 只验证未来人工批准抽样后的 evidence JSON；合法 evidence 需要 source attribution、archive member、clip window、expected/observed sha256、observed duration、sample rate、channel count、license citation 和 cleanup status。该阶段仍不下载、不抽取、不转码、不读取音频、不运行 ASR、不调用远程 ASR/LLM。
- DRV-035 已完成 replay -> shadow report draft adapter：`tools/replay_shadow_report_draft_adapter.py` 把 replay report 映射成 DRV-033 candidate report draft，并通过 schema validation；非 candidate replay blocked，不伪造真实 feedback 或产品价值。该阶段仍不访问麦克风、不读取真实音频、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- DRV-036 已完成 shadow report ingestion/export/feedback readiness：`tools/shadow_report_ingestion_export_feedback.py` 可 ingest DRV-033 candidate report 或 DRV-035 adapter report，输出 schema validation、timeline counts、feedback analysis、Go/Pivot/Stop readiness 和 export preview；该阶段仍不写真实导出文件、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM。
- DRV-037 已完成 shadow report export file writer：`tools/shadow_report_export_file_writer.py` 把 DRV-036 preview 写入 ignored `artifacts/tmp/shadow_report_exports`，并记录导出文件 path、sha256 和 byte count；unsafe output root、unsafe session id 和既有文件内容冲突会 blocked。该阶段仍不写仓库可提交导出文件、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM。
- DRV-038 已完成 shadow report feedback ingestion API/UI：`tools/shadow_report_feedback_ingestion.py` 接收 feedback entries 并更新 candidate card timeline、feedback summary 和 final decision preview；Web backend 暴露 `POST /shadow-reports/feedback-ingestions`，Web 工作台显示 `shadow-report-feedback-panel`。该阶段仍不写 candidate report 文件、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM。
- DRV-039 已完成 shadow-test pilot bundle runner：`tools/shadow_test_pilot_bundle_runner.py` 串联 DRV-038 和 DRV-037，真实 audio-written report 且 feedback 达标时写出 ignored JSON/Markdown bundle，并输出 Go evidence status；replay draft 只能写 preview bundle，不得成为 Go 证据。该阶段仍不访问麦克风、不读取真实音频、不写 audio chunk、不调用远程 ASR/LLM。
- DRV-033 已完成真实麦克风 shadow-test report schema：固定 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、feedback labels、privacy/cost flags、audio retention/delete status 和 Go/Pivot/Stop 结构；只做 schema validation，不访问麦克风、不读取真实录音、不写或删除 audio chunk。
- 下一步不再是“继续证明 M5”“继续做 shadow draft”“继续做 ingestion preview”“继续做 export writer”“继续做 feedback ingestion API/UI”“继续做 pilot bundle 包装”“继续做 simulated shadow pipeline smoke/batch”“继续做 worker/mic connector wrapper”“继续做 Tauri result-intake wrapper”“继续做 worker mic source approval wrapper”“继续做 Tauri evidence bridge”“继续做 mic adapter boundary”“继续做 ASR worker mic source boundary”“继续做 single-shadow approval evidence”“继续做真实麦克风 readiness wrapper”或“继续做 readiness UI”；应转向 ASR 质量决策退出动作。公开音频若没有具体 manifest 和审批，继续保持 no-download blocked。
- 最后由用户执行真实会议 shadow test。

退出条件：

- shadow test 产出 transcript、metrics、timeline、card feedback，并形成 Go / Pivot / Stop 决策。

## 8. 后续 6 个里程碑锁

多 Agent 只读审查后的收敛规则：

| 里程碑 | 退出条件 | 不做什么 |
| --- | --- | --- |
| M1 ASR 质量路径一次性决策 | FunASR 本地模型目录、DRV-019 审批、可选远程 ASR 对照或降级取舍不再悬空 | 不新增 provider 横评，不继续猜测型 normalizer |
| M2 Real Tauri no-op run | PCWEB-118 已证明 Tauri Rust crate 可编译；PCWEB-119 已证明真实 Tauri WebView no-op run 结果通过 10 个 no-op IPC returned + no-side-effect 校验，并写入 ignored evidence；PCWEB-120 已把该 evidence 用于同 session worker mic source manual review packet，但 approval status 仍是 `not_approved` | 不访问麦克风，不启动 worker，不读 secret |
| M3 Worker output -> Web Live ASR session | PCWEB-108 已证明 approved synthetic event output 可创建临时 Web Live ASR session，并生成 transcript/EvidenceSpan/state/gap/candidate/request draft closure；PCWEB-112/114 已证明 mic start contract、worker mic source approval blocker 和人工审批包可在同一 session 组合校验 | 不启动真实 worker，不读真实音频，不把非工程 transcript 当产品价值 |
| M4 Mic adapter no-op UI invocation | PCWEB-109 已展示 7 个 no-op invocation row；浏览器 fallback 为 `not_invoked`，Tauri WebView 才调用 PCWEB-107 no-op IPC；`start` 仍 validated-not-executed | 不请求权限，不枚举设备，不写 chunk，不运行 Cargo/Tauri |
| M5 Short local simulated input | PCWEB-110 已证明 approved synthetic/mock event file 可生成 ASR metrics、EvidenceSpan timeline、state timeline 和 candidate/card timeline；工程输入闭合到 candidate timeline，非工程 control 0 candidate | 不把公开音频当产品价值证明，不读取 `.m4a`、本地私有短音频或用户录音 |
| M6 Real mic shadow test report schema | DRV-033 已固定 transcript、ASR metrics、state timeline、candidate/card timeline、feedback labels、privacy/cost flags、audio retention/delete status 和 Go/Pivot/Stop schema；真实会议仍待用户最终执行 | 不直接开麦克风，不读取真实录音 |

任何新 PCWEB/DRV 主线项必须标注它直接推进的链路节点：`ASR quality`、`EvidenceSpan/state/gap`、`candidate/card/feedback`、`desktop runtime`、`worker handoff`、`mic adapter` 或 `pilot`。否则只能作为辅助记录，不算主线进展。

## 8.1 下一轮最小可行闭环

PCWEB-110 后，下一轮不再继续做泛化评测，也不直接做自动公开音频下载器或真实麦克风采集。最小可行闭环固定为：

```text
ASR event manifest / provenance (PCWEB-111 done)
  -> public audio post-extraction evidence schema (DRV-034 done)
  -> replay timeline report
  -> shadow report draft schema adapter (DRV-035 done)
  -> shadow report ingestion/export/feedback readiness (DRV-036 done)
  -> shadow report export file writer (DRV-037 done)
  -> shadow report feedback ingestion API/UI (DRV-038 done)
  -> shadow-test pilot bundle runner (DRV-039 done)
  -> 用户最终真实麦克风 shadow test report ingestion
```

推荐顺序：

1. `event manifest / provenance`：PCWEB-111 已完成。`artifacts/tmp/asr_events` 下可选 `asr_event_provenance.v1` manifest 可区分 `synthetic_audio`、`mock_streaming` 和未来 `public_audio_sample`，记录 `script_id/source_id/sample_id/provider/event_contract_version/generated_by`，并保持 no-remote/no-mic/no-user-audio/no-public-download flags 全 false。
2. `public audio post-extraction evidence schema`：DRV-034 已完成。公开音频阶段不先做自动下载器；如果未来用户明确批准并手工下载/抽样，只接收一个 post-extraction evidence JSON，验证 `planned_sample_id/source_id/archive_member_path/observed_sha256/observed_duration/sample_rate/channel_count/license_citation/cleanup_status`，仍不读取真实音频、不运行外部命令、不调用 ASR。
3. `replay -> shadow report draft`：DRV-035 已完成。PCWEB-110/111 replay timeline shape 已可映射到 DRV-033 shadow-test report draft schema；模拟输入可以填 transcript、ASR metrics、EvidenceSpan/state/candidate timeline 和 event provenance，但 `audio_chunk_write_status` 保持 `not_written`，真实 feedback/export 仍待 pilot。
4. `shadow report ingestion/export/feedback readiness`：DRV-036 已完成。DRV-033/035 report 可进入 ingestion gate 并产出 feedback analysis、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview；但 DRV-036 不写导出文件、不生成真实用户反馈。
5. `shadow report export file writer`：DRV-037 已完成。DRV-036 preview 可写入 ignored `artifacts/tmp/shadow_report_exports` 下的 JSON/Markdown 文件，重复内容一致时 idempotent，内容冲突时 blocked；仍不写仓库可提交导出文件、不生成真实用户反馈。
6. `shadow report feedback ingestion API/UI`：DRV-038 已完成。真实会议后的卡片反馈可通过工具、API 和 Web 面板进入 report，并更新 Go/Pivot/Stop readiness；replay draft 仍不能成为 Go 证据。
7. `shadow-test pilot bundle runner`：DRV-039 已完成。真实会议后的 report 和 feedback entries 可被打包成 ignored JSON/Markdown bundle，并输出 Go/Pivot/Stop 证据状态；replay draft 只允许 preview bundle，不允许成为 Go 证据。
8. `真实麦克风验收 ingestion`：desktop runtime、worker/mic adapter、start/pause/resume/stop/delete、导出和反馈链路具备后，由用户执行 20-30 分钟真实中文技术会议，再把结果按 DRV-033 schema ingest；我不主动读取麦克风或真实录音。

这条闭环的价值是把主线从“计划和评测”推进到“可追溯本地 replay + 可接真实验收报告”，同时继续守住不下载公开大包、不读取真实音频、不调用远程 ASR/LLM、不下载模型和不运行 Tauri dev/build 的默认边界。Cargo 只允许像 PCWEB-118 这种受控编译检查，且不得被解读成麦克风、worker 或 WebView runtime 已可用。

## 9. 停止条件

出现以下情况时，不再继续堆评测，应进入产品取舍：

- 本地 ASR 长期无法保留关键中文技术实体。
- FunASR 质量验证长期卡在模型目录/审批，且用户不批准模型下载。
- 公开音频只能通过 GB 级整包下载才能抽样，且用户不批准。
- 真实会议中 useful / would_have_asked 低于 40%。
- wrong / too_late / too_intrusive 高于 20-25%。

取舍选项：

- 批准一次 FunASR 模型下载，并建立缓存/清理策略。
- 引入远程 ASR 作为可选高质量模式，而非默认模式。
- 降级为会后结构化纪要，不做强实时 Copilot。

## 9.1 每轮主线报告模板

后续每轮主线报告必须先回答这 5 个问题：

1. 技术实体 normalized recall 是否改善，或为什么仍 blocked。
2. final/revision 是否能在 10-30 秒窗口内形成 EvidenceSpan 和工程 gap candidate。
3. 非工程 control 是否仍为 0 工程 state/candidate/card。
4. 本轮是否推进了 desktop runtime、ASR worker handoff、mic adapter 或真实 pilot 前置条件。
5. 本轮是否引入任何额外费用、远程调用、模型下载、真实音频读取或麦克风权限；若有，必须说明审批依据。

如果报告无法回答前 4 个问题，则这轮不算推进主线。

## 10. 权威关联文档

- `docs/current-mainline-index.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/copilot-product-value-batch-result-2026-07-03.md`
- `docs/asr-quality-decision-gate-2026-07-03.md`
- `docs/pcweb-110-short-local-simulated-input-timeline-report-plan.md`
- `docs/pcweb-111-asr-event-provenance-manifest-plan.md`
- `docs/pcweb-112-desktop-worker-mic-connector-contract-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/drv-035-replay-shadow-report-draft-adapter-plan.md`
- `docs/drv-036-shadow-report-ingestion-export-feedback-plan.md`
- `docs/drv-037-shadow-report-export-file-writer-plan.md`
- `docs/drv-038-shadow-report-feedback-ingestion-api-ui-plan.md`
- `docs/drv-039-shadow-test-pilot-bundle-runner-plan.md`
- `docs/drv-041-simulated-shadow-pipeline-smoke-plan.md`
- `docs/drv-042-simulated-shadow-pipeline-batch-smoke-plan.md`
- `docs/drv-043-funasr-local-readiness-evidence-input-plan.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
