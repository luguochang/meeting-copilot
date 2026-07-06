# Current Mainline Index

> 日期：2026-07-04  
> 状态：Accepted as current mainline pointer  
> 目的：给后续开发一个短入口，避免在多个计划文档之间丢失主线。  
> 边界：本索引不授权访问麦克风、读取真实用户音频、读取任何 `.m4a`、读取 `configs/local/`、读取 `data/local_runtime/` 或 `outputs/`、下载公开音频大包、下载 FunASR/ModelScope 模型或调用远程 ASR/LLM。

2026-07-05 DEC-217 reset: 当前项目状态正式收敛为 `Local Shadow Preview / Engineering Demo ready`，不是 `Shadow Pilot ready`，也不是 `Production MVP ready`。完整复盘入口为 `docs/project-release-readiness-reset-2026-07-05.md`。下一阶段不得继续把 readiness/preflight/approval/preview/wrapper-only 工作算作主线进展，除非它直接改变 `quality_exit_status`、`real_mic_shadow_readiness_status`、`user_can_start_real_mic_shadow_test_now`、`normalized technical entity recall`、`formal card/report evidence status` 或真实会议反馈指标。

2026-07-05 DEC-218 implementation: Local Shadow Preview release path 已落到 Web 工作台、FastAPI API、browser smoke 和 mainline runner 顶层报告。当前允许声明的范围仍只有 `local synthetic/replay/artifact Copilot preview`；UI/runner 必须同时显示 `ASR quality not_exited`、`real mic blocked`、`LLM disabled_not_called`、`formal card/report not-Go` 和 microphone/remote/LLM safety flags=false。该实现只把当前真实状态可见化，不授权麦克风、系统音频、远程 ASR、LLM 或生产发布。

## 1. 当前主线

产品是中文技术会议实时 Copilot，不是普通转写工具。主线固定为：

```text
ASR final/revision
  -> EvidenceSpan
  -> meeting state / engineering gap candidate
  -> suggestion candidate/card
  -> feedback/export readiness
  -> desktop runtime
  -> 用户真实麦克风 shadow test
```

PC/桌面端优先。Local Web MVP 只是验证 core/API/UI/event 的本地切片；Mac/Tauri desktop shell、ASR worker handoff 和用户显式授权麦克风 adapter 才是当前产品形态主线。

当前总控计划入口是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`。若旧阶段报告、`docs/superpowers/plans/**` 或 2026-07-02 文档与该总控计划冲突，以 2026-07-03 P0/P1 文档为准。

2026-07-03 计划确认入口是 `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`：我负责公开授权音频来源审查、合成音频模拟、本地 ASR 自测和指标报告；用户最终负责真实麦克风会议 shadow test。

2026-07-03 最新用户确认：完整计划必须继续按“网上公开音频来源复核 + 合成音频/模拟转写自测 + 用户最终真实麦克风会议验证”执行。当前结论是计划已写下，不再新建散点计划；后续只更新本索引、总控计划、RTM 和 decision-log。公开音频只能推进到官方来源复核和 no-download sample manifest，除非后续出现可复核 clip manifest 或用户明确批准 GB 级公开包下载。

2026-07-03 本轮执行确认：`转写类验证` 的责任边界保持为我先完成官方公开来源复核、no-download manifest、合成中文技术会议、mock streaming events 和 approved synthetic event replay；`最终真实麦克风会议验证` 仍由用户在 readiness gate 满足后手动执行。当前不得把“再找公开视频/音频”扩展成抓取 Bilibili、YouTube、播客、公开课、技术大会录播或版权链不清材料；也不得为了补公开音频证据而默认下载 AliMeeting/AISHELL-4/AISHELL-1 的 GB 级包。

2026-07-04 用户最新确认：完整计划已经写下，且转写类验证仍由我先通过网上官方公开音频来源复核、本地合成/Mock 模拟和 ASR event replay 完成；用户最终再做真实麦克风会议验证。本轮只允许把已有 readiness 状态可见化和继续执行 bounded 模拟/ASR quality 出口，不允许访问麦克风、读取用户录音或 `.m4a`、下载 GB 级公开音频包、调用远程 ASR/LLM 或读取 `configs/local/`。

2026-07-04 DEC-175/176/177 执行锁：`DRV-044 FunASR synthetic smoke result evidence schema/gate` 已实现并完成 provenance/hash 加固。它只验证未来本地 FunASR synthetic smoke result 的 evidence schema、硬阈值、artifact provenance 和 gate 结果，不运行 ASR、不下载模型、不访问麦克风、不调用远程 ASR/LLM。单场景 smoke 只算候选；5 场景 batch confirmation 必须带 `batch_artifact_provenance_status=validated`，才能让 DRV-032 严格退出 ASR quality blocker；该 evidence 仍不是真实麦克风 Go evidence。开源基座决策同步锁定为：不 fork 会议助手项目做二开，采用自建薄主线并复用 Tauri、FastAPI、FunASR/sherpa 和 OpenAI-compatible LLM 协议；500+ star 项目如有可用能力，只能作为 provider/adapter，不替代 EvidenceSpan -> gap/card -> feedback 主线。

2026-07-04 DEC-178 执行确认：新增 `docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md` 作为短入口，明确回答“完整计划已写下，转写由我先通过网上官方公开音频和本地模拟完成，用户最终再做真实麦克风会议验证”。本轮联网复核仍只保留 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 作为默认白名单；MagicData-RAMC 等 NC/ND 或非会议来源只观察不进入自动评测。当前不下载公开音频大包，不抓公开视频，不读取真实录音，不访问麦克风，不调用远程 ASR/LLM。

2026-07-04 DEC-179/DRV-045 执行确认：新增 `tools/funasr_synthetic_smoke_execution_packet.py` 和 `tests/test_funasr_synthetic_smoke_execution_packet.py`，把 DRV-043 readiness evidence 到 DRV-044 batch provenance/hash gate 之间的手动执行交接固定下来。合法 readiness 会生成 5 场景 FunASR synthetic smoke command previews、expected output paths 和 `expected_drv044_batch_artifact_provenance` template；默认仍 `safe_to_execute_now=false`、`execution_approval_status=not_approved_manual_run_only`，不运行 ASR、不读取音频、不写 artifacts、不下载模型、不调用远程 provider。Focused TDD gate 为 `6 passed, 1 warning`。

2026-07-04 DEC-180/DRV-046 执行确认：新增 `tools/funasr_synthetic_smoke_batch_evidence_assembler.py` 和 `tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py`，把 DRV-045 manual packet 产出的 5 个 smoke report JSON 装配成 DRV-044 batch evidence。工具只读取 approved `artifacts/tmp/asr_reports/**.json` artifact bytes 计算 sha256、合并 scenario_results、生成 `batch_synthetic_confirmation`，然后调用 DRV-044 gate；缺 artifact、unsafe path 或 DRV-044 quality blocker 都会 blocked。它不运行 ASR、不读取音频、不写 artifacts、不下载模型、不调用远程 provider。Focused TDD gate 为 `6 passed, 1 warning`。

2026-07-04 DEC-186 收口确认：三路只读审查和本地门禁复跑一致确认，完整计划已经写下，转写类验证由我先通过官方公开音频 no-download 路线、本地中文技术会议合成/Mock 和 approved ASR event replay 完成，真实麦克风会议由用户最终验证。公开音频当前合理 blocked，模拟 shadow pipeline batch 已证明产品链路 preview 可闭合但不算 ASR quality Go evidence。下一张主线票不得继续 readiness/report-only wrapper；默认应选择 `ASR quality exit` 的实际出口，或进入 `Mac Local Shadow MVP: Tauri/WebView synthetic event demo closure`，用 approved synthetic/mock streaming events 演示 Mac 端 Copilot 工作流，同时明确展示真实麦克风仍被 ASR quality 阻断。

2026-07-04 DEC-187/PCWEB-125 执行确认：`Mac Local Shadow MVP synthetic demo closure` 已实现。Web backend 新增 `POST /desktop/mac-local-shadow-mvp-demo/sessions`，使用内置 synthetic streaming events 创建 Live ASR session，并闭合到 transcript partial/final/revision、EvidenceSpan、state_event、scheduler_event、suggestion_candidate_event、no-LLM request draft 和真实麦克风 readiness blocker；Web 工作台新增 `Shadow MVP` 按钮和 `mac-local-shadow-mvp-panel`，点击后进入现有 Live ASR SSE 渲染路径。Focused TDD gate 为 `3 passed, 2 warnings`，`test_app.py` 全量为 `285 passed, 2 warnings`，`test_live_events.py` 为 `40 passed, 1 warning`；浏览器 E2E `node code/web_mvp/e2e/browser_smoke.mjs` 返回 `status=ok`，checked list 包含 `Mac Local Shadow MVP synthetic demo closure`。该切片不访问麦克风、不读取真实音频、不运行 ASR、不调用远程 ASR/LLM、不下载模型或公开音频，且不算 ASR quality Go evidence。

2026-07-04 DEC-188 执行收口确认：本轮针对“完整计划是否写下、转写是否由我先找公开音频和模拟、用户是否最后真实麦克风验证”再次启动两路只读 Agent 审查。审查结论一致：计划完整，主线无本质矛盾，真实麦克风边界清楚，当前 blocker 是 ASR quality exit 而不是计划缺失。PCWEB-125 已完成，不能再把 Mac synthetic demo closure 当作下一张未完成开发票；public audio bounded manifest 仍只是条件分支，不是当前默认主线，也不能替代 ASR quality exit。下一步只允许进入 ASR quality exit 的实际出口，或在用户显式接受风险时走一次 degraded pilot；不得继续新增总控计划、同类 readiness/report-only wrapper、开放式 provider 横评、版权链不清音频搜索或默认下载 GB 级公开音频包。

2026-07-04 DEC-189/PCWEB-126 执行确认：`Realistic Meeting Simulation Pack` 已实现，用更接近真实中文技术会议的 synthetic turn stream 推进产品体验验证。Web backend 新增 `POST /desktop/realistic-meeting-simulation-pack/sessions`，生成 4 speaker / 8 turn / 47.2s 的 release+incident review synthetic stream，覆盖 partial corrections、2 次 revision、pause/overlap markers、payment-gateway、P99、0.1%、Kafka lag、rollback、feature flag 等技术词，并复用 Live ASR SSE 渲染到 transcript、EvidenceSpan、state、scheduler、suggestion candidate 和 no-LLM request draft。Web 工作台新增 `真实模拟` 按钮和 `renderRealisticMeetingSimulationPack`，点击后展示 scenario、speaker/turn/revision/state/draft、realism features、technical terms 和 readiness blockers。TDD 红灯为新 endpoint 404，focused 绿灯为 `3 passed, 2 warnings`；浏览器 E2E `node code/web_mvp/e2e/browser_smoke.mjs` 返回 `status=ok`，checked list 包含 `realistic meeting simulation pack`。该切片不访问麦克风、不读取真实音频、不下载公开音频/模型、不运行 ASR、不调用远程 ASR/LLM、不写 audio chunk，且不算 ASR quality Go evidence 或真实麦克风 Go evidence。

2026-07-04 DEC-190/PCWEB-127 执行确认：`Long Realistic Meeting Simulation Profile` 已实现。`POST /desktop/realistic-meeting-simulation-pack/sessions` 现在支持 `profile=long_shadow`，生成 5 speaker / 16 turn / 615s 的 architecture+release+incident follow-up synthetic timeline，覆盖 5 partial、13 final、3 revision、pause/overlap、recommendation-service、payment-gateway、idempotency-key、Redis cluster、MySQL、P99、SLO、Kafka lag、rollback、feature flag 等技术词。Web 工作台新增 `长会模拟` 按钮，复用同一 Live ASR SSE 和 `renderRealisticMeetingSimulationPack`，可在 UI 里看到 `pcweb_127_long_architecture_release_review`、long timeline features、technical terms、draft report preview 和 readiness blockers。TDD 红灯为 `profile` 被请求模型拒绝并返回 422；focused 绿灯为 `3 passed, 2 warnings`；浏览器 E2E `node code/web_mvp/e2e/browser_smoke.mjs` 返回 `status=ok`，checked list 包含 `long realistic meeting simulation pack`。该 profile 仍是 synthetic-only，不访问麦克风、不读真实音频、不下载公开音频/模型、不运行 ASR、不调用远程 ASR/LLM、不写 audio chunk，不算 ASR quality Go evidence 或真实麦克风 Go evidence。

2026-07-04 DEC-191/DRV-043-045 执行确认：本机已有可用 FunASR/ModelScope 本地缓存和 synthetic audio，`tools/funasr_synthetic_smoke_readiness.py` 对 `api-review-001` 返回 `readiness_status=cache_preflight_passed_offline_execution_not_proven`、`required_cached_models_status=present`、`model_download_status=not_started`、`validation_errors=[]`。`tools/asr_quality_decision_gate.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 已把 ASR quality 状态从 `requires_funasr_model_dir_or_drv019_approval` 推进到 `funasr_cache_preflight_ready_requires_execution_approval`；`tools/funasr_synthetic_smoke_execution_packet.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 返回 `packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run`，覆盖 5 个 synthetic 场景。no-execution packet 已固化到 ignored artifact `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`，当前不再需要模型下载审批作为默认下一步；真正的下一步是是否批准一次本地 FunASR synthetic smoke 执行。该批准会读取 `artifacts/tmp/synthetic_audio/**` 合成音频并写 ignored `artifacts/tmp/asr_events/**` / `artifacts/tmp/asr_reports/**`，但仍不访问麦克风、不读真实用户音频、不读 `.m4a`、不读 `configs/local`、不调用远程 ASR/LLM、不下载模型。未批准前 `safe_to_run_asr_now=false`，真实麦克风 shadow test 仍 blocked。

2026-07-04 DEC-192/DRV-045 后处理闭环加固：`funasr_synthetic_smoke_execution_packet` 已补齐 `postprocess_command_previews`，每个 scenario 都有 transcript report 命令和 synthetic ASR smoke report 命令，并显式绑定 `data/asr_eval/synthetic_meetings/scripts/*.json` golden script、`data/asr_eval/glossaries/technical-terms.zh.json` 术语表、events/provider/transcript/smoke report 路径和 smoke report stdout redirect。`code/asr_runtime/scripts/transcript_report.py` 在 provider JSON 含 `audio_duration_seconds` 时不再要求人工传 `--duration-seconds`，减少手工执行断点。该变更不运行 ASR、不读取音频、不写 smoke 结果，只让一次获批本地 FunASR smoke 后可以从 provider/events artifact 直接进入 transcript report -> smoke report -> DRV-046/044/032。

2026-07-04 DEC-193/DRV-048 执行前入口加固：新增 `tools/funasr_synthetic_smoke_approved_runner.py` 和 `tests/test_funasr_synthetic_smoke_approved_runner.py`。该 runner 默认只 dry-run，读取 DRV-045 packet 后输出 `runner_status=dry_run_ready_requires_execute_flag_and_approval`、`planned_provider_command_count=5`、`planned_postprocess_command_count=10`、`executed_command_count=0`，并保持 `safe_to_run_asr_now=false`。只有同时提供 `--execute`、合法 `funasr_synthetic_smoke_execution_approval.v1` approval record 和通过校验的本地模型目录时，才会执行 5 个 provider command 和 10 个后处理 command；本轮只用 fake runner 测试 execute 分支，没有运行真实 FunASR。dry-run artifact 已写入 `artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json`。下一步仍是显式批准一次本地 FunASR synthetic smoke；未批准前真实麦克风 shadow test 继续 blocked。

2026-07-04 DEC-194/DRV-049 approval record 模板：新增 `tools/funasr_synthetic_smoke_execution_approval_record.py` 和 `tests/test_funasr_synthetic_smoke_execution_approval_record.py`，生成 `funasr_synthetic_smoke_execution_approval.v1` 模板。默认模板 artifact 为 `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json`，其 `approval_confirmed_by_user=false`，runner 在 `--execute` 下会返回 `blocked_missing_or_invalid_execution_approval`，不会执行任何命令。只有模板被显式确认为 `approval_confirmed_by_user=true`，且 token/scope/packet path/场景数/拒绝真实音频和远程调用等字段全部匹配，runner 才允许进入执行分支。该阶段仍不运行 ASR、不读取合成音频、不写 ASR artifacts。

2026-07-04 DEC-195 approval wrapper 互操作修复：核验发现 `funasr_synthetic_smoke_execution_approval_record.py` 输出的是 report wrapper，而 `funasr_synthetic_smoke_approved_runner.py --approval-record-path` 之前会把整个 wrapper 当作 approval record 校验，导致模板路径模式报出一组无关字段错误。已修复 runner：当 approval JSON 包含 `approval_record_template` object 时自动解包再校验。回归确认未确认模板现在只返回 `validation_errors=['approval_confirmed_by_user must be true']` 且 `executed_command_count=0`；相关测试为 `44 passed, 1 warning`。该修复不运行 ASR、不读取音频、不写 ASR artifacts。

2026-07-04 DEC-196 真实麦克风全链路升级确认：用户确认“适当的时候要调麦克风走真实全链路流程”。该方向进入主线，但不代表当前已经授权访问麦克风。执行顺序固定为：先完成本地 FunASR synthetic smoke -> DRV-046/044/032 ASR quality exit 或显式 degraded pilot -> PCWEB-115 readiness gate -> 真实麦克风 approval/start -> 真实会议 shadow test -> feedback/export。未满足 ASR quality exit 或合法降级试点前，不访问麦克风、不枚举设备、不请求权限、不采集或写真实 audio chunk。

2026-07-04 DEC-197/DRV-048 执行前安全加固：多 Agent 只读审查确认主线 blocker 仍是 ASR quality exit，但发现 approved runner 在获批执行前应二次校验 packet 内部命令和路径。已加固 `tools/funasr_synthetic_smoke_approved_runner.py`：provider/postprocess argv 必须匹配 approved DRV-045 command contract，synthetic audio/events/report/script roots 必须精确匹配，forbidden roots、`.m4a`、系统语音备忘录临时路径、`outputs/**`、仓库外路径或缺 `argv` 均在执行前 blocked，`executed_command_count=0`。TDD 红灯为 `4 failed, 6 passed, 1 warning`；focused 绿灯为 `10 passed, 1 warning`。该阶段不运行 ASR、不读音频、不写 ASR artifacts、不访问麦克风、不下载模型、不调用远程 ASR/LLM。

2026-07-04 DEC-198/DRV-050 格式桥接修复：多 Agent 审查发现原 DRV-045 `smoke_report_argv` 调用旧 `synthetic_asr_smoke_report.py` 会产出 `synthetic_asr_smoke_report.v1` 扁平摘要，无法被 DRV-046 assembler 消费。已新增 `tools/funasr_synthetic_smoke_single_result_builder.py`，并把 DRV-045 packet 的 smoke postprocess 改为输出 DRV-044 可验证的 `funasr_synthetic_smoke_result.v1` / `single_synthetic_smoke` / `scenario_results=[...]`。新增跨工具测试确认：用 DRV-045 packet 的 `smoke_report_argv` 生成 5 个单场景 artifact 后，DRV-046 可 assemble 成 `drv044_batch_evidence_validated`，DRV-044 返回 `funasr_synthetic_smoke_quality_batch_confirmed`。已重新生成 ignored no-execution packet 和 dry-run artifact；当前仍未批准真实本地 FunASR smoke，因此 ASR quality 未退出，真实麦克风仍 blocked。

2026-07-03 DEC-138 复核结论：两个只读审查 Agent 均确认计划已经完整，公开音频阶段当前正确停在 no-download blocked，真实麦克风会议最终由用户验证；旧 decision-log 中 PCWEB-097/098 这类历史 next pointer 已由 PCWEB-107、DRV-033、DEC-136 和 DEC-138 supersede。当前下一步只以第 4 节的 6 个收敛里程碑为准。

2026-07-03 PCWEB-109 执行结论：M4 已从“合同可见 / Rust no-op 静态绑定”推进到 Web UI invocation surface。普通浏览器中 `desktop-mic-adapter-contract-panel` 会显示 7 个 mic adapter no-op invocation row，状态为 `mic_adapter_browser_fallback` / `not_invoked`；未来 Tauri WebView 中才通过 `window.__TAURI__` 调用 PCWEB-107 已绑定的 no-op command。该阶段仍不请求麦克风权限、不枚举设备、不采集或写入 audio chunk、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 PCWEB-110 执行结论：M5 已从“待做短时本地模拟输入”推进到可审计 timeline report。`tools/asr_live_pipeline_replay.py` 现在会把 approved synthetic/mock event file replay 成 `short_local_simulated_input_status`、`asr_metrics`、`evidence_span_timeline`、`state_timeline` 和 `candidate_card_timeline`；工程样本可闭合到 candidate timeline，非工程 control 保持 0 state / 0 candidate。该阶段仍不读取 `.m4a`、不读取真实用户录音、不访问麦克风、不调用远程 ASR/LLM、不下载模型、不下载公开音频、不运行 Cargo/Tauri。

2026-07-03 PCWEB-111 执行结论：下一轮最小闭环第一步 `ASR event manifest / provenance` 已完成。`tools/asr_live_pipeline_replay.py` 支持可选 `--event-manifest-path`，manifest 必须位于 `artifacts/tmp/asr_events`，绑定 replay events path，声明 `input_source_kind`，并保持 no-LLM/no-remote/no-mic/no-user-audio/no-public-download flags 全 false。该阶段只增加 event provenance 审计，不读取音频、不下载公开音频、不运行 ASR、不调用远程 ASR/LLM。

2026-07-03 PCWEB-111 复审加固结论：manifest provenance id fields 现在会阻断绝对路径、相对 forbidden-root 文本、反斜杠路径文本和音频路径文本，包括 `/Users/...`、`configs/local/...`、`data/asr_eval/local_samples/...`、`data\asr_eval\local_samples\...` 和 `.m4a`；blocked manifest 不读取 events，也不会把这些文本写入 report。`tests/test_asr_live_pipeline_replay.py` 当前为 `13 passed, 1 warning`。

2026-07-03 DRV-034 执行结论：公开音频 post-extraction evidence schema 已完成。`tools/public_audio_post_extraction_evidence_schema.py` 只接收未来人工批准抽样后的 evidence JSON，验证 planned sample、source attribution、archive member、clip window、expected/observed sha256、observed duration、sample rate、channel count、license citation 和 cleanup status；仍不下载、不解压、不转码、不读取音频、不运行 ASR、不调用远程 ASR/LLM。focused gate 为 `10 passed, 1 warning`。

2026-07-03 DRV-034 复审同步结论：DRV-034 后当时的明确下一步是 `replay -> DRV-033 shadow report draft adapter`，现已由 DRV-035 完成。也就是把 PCWEB-110/111 replay timeline 映射成真实 shadow-test report 草稿；模拟输入可填 transcript、ASR metrics、EvidenceSpan/state/candidate timeline 和 event provenance，但 `audio_chunk_write_status` 必须保持 `not_written`，仍不访问麦克风、不读取真实音频、不生成真实反馈。

2026-07-03 DRV-035 执行结论：`replay -> DRV-033 shadow report draft adapter` 已完成。`tools/replay_shadow_report_draft_adapter.py` 会把 replay report 映射成 `real_mic_shadow_test_report.v1` candidate report draft，并调用 DRV-033 schema 校验；成功草稿包含 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、0 feedback、`inconclusive_requires_more_shadow_tests` 和 `audio_chunk_write_status=not_written`。复审后已要求输入 replay 的远程 ASR、真实音频、`configs/local`、麦克风和 LLM safety flags 全部明确为 false，且 `validation_errors` 为空。非 candidate replay 会 blocked，不伪造产品价值。focused gate 为 `6 passed, 1 warning`。

2026-07-03 DRV-036 执行结论：`shadow report ingestion/export/feedback` 已完成。`tools/shadow_report_ingestion_export_feedback.py` 可直接 ingest DRV-033 candidate report，或读取 `artifacts/tmp/real_mic_shadow_reports` 下的 report JSON，或读取 `artifacts/tmp/asr_reports` 下的 DRV-035 adapter report 并抽取 candidate report；输出 schema validation、timeline counts、feedback analysis、feedback collection status、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview。Replay draft 只会得到 `draft_export_preview_only` / `feedback_required_before_decision`，不能作为 Go 证据；真实反馈 report 才能输出 `ready_for_shadow_test_export`。focused gate 为 `6 passed, 1 warning`，仍不访问麦克风、不读取真实音频、不写导出文件、不调用远程 ASR/LLM。

2026-07-03 DRV-037 执行结论：`shadow report export file writer` 已完成。`tools/shadow_report_export_file_writer.py` 会调用 DRV-036，把 JSON/Markdown export preview 写入 ignored `artifacts/tmp/shadow_report_exports`；输出文件名来自安全 `session_id`，写入前阻断 unsafe output root、unsafe session id 和既有文件内容冲突；内容一致重复执行返回 `idempotent_existing_files_match`。Replay draft 可写预览但标记 `not_go_evidence_replay_or_feedback_missing`，真实反馈 report 才会标记 `go_evidence_supported_by_real_feedback_report`。focused gate 为 `7 passed, 1 warning`，仍不访问麦克风、不读取真实音频、不写 audio chunk、不写仓库可提交导出文件、不调用远程 ASR/LLM。

2026-07-03 DRV-038 执行结论：`shadow report feedback ingestion API/UI` 已完成。`tools/shadow_report_feedback_ingestion.py` 可接收 DRV-033 candidate report 或 DRV-035 adapter report path，并把 `useful/would_have_asked/wrong/too_late/too_intrusive/dismissed` 写回 candidate card timeline 和 feedback summary；真实 audio-written report 在 positive>=2 且 negative<=1 时更新为 `go` 并通过 DRV-036 readiness，replay draft 即使有正反馈也保持 `inconclusive_requires_more_shadow_tests` / `not_go_evidence_replay_or_feedback_missing`。Web backend 新增 `POST /shadow-reports/feedback-ingestions`，工作台新增 `shadow-report-feedback-panel`。focused gate 为 `11 passed, 2 warnings`，仍不访问麦克风、不读取真实音频、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 DRV-039 执行结论：`shadow-test pilot bundle runner` 已完成。`tools/shadow_test_pilot_bundle_runner.py` 会先调用 DRV-038 写回 feedback entries，再调用 DRV-037 把更新后的报告写入 ignored `artifacts/tmp/shadow_report_exports` JSON/Markdown bundle；unsafe output root 会在读取 candidate report 前 blocked，bad feedback 不会写导出文件。真实 audio-written report 且 feedback 达标时输出 `pilot_bundle_written` / `go_evidence_supported_by_real_feedback_report`，replay draft 只输出 `pilot_bundle_preview_written_not_go_evidence`。focused gate 为 `5 passed, 1 warning`，仍不访问麦克风、不读取真实音频、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不下载公开音频或模型、不运行 Cargo/Tauri。

2026-07-03 PCWEB-112 执行结论：`Desktop Worker/Mic Connector Contract` 已完成。`tools/desktop_worker_mic_connector_contract.py` 会复用 PCWEB-105 mic adapter contract 和 PCWEB-099 worker command protocol，把 `mic_adapter.start` 的显式用户 start preview 与 `worker.prepare(source_kind=mic)` 的 future approval blocker 放进同一 session 的组合门禁。合法 request 输出 `ready_for_worker_mic_connector_contract_review`，但 worker 侧仍保留 `source_kind requires later approval: mic`，下一步 decision 为 `approve_worker_mic_source_after_real_tauri_noop_run`。focused gate 为 `7 passed, 1 warning`，仍不访问麦克风、不请求权限、不读写 audio chunk、不启动 worker、不读写 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 PCWEB-113 执行结论：`Desktop Tauri No-op Run Result Intake` 已完成。`tools/desktop_tauri_noop_run_result_intake.py` 只接收未来显式批准真实 Tauri WebView no-op run 后的 caller-provided JSON，验证 PCWEB-107 的 10 个 no-op IPC command 全部 returned，且每个返回仍为 `noop_bound/noop_only/tauri_ipc_bound`、`side_effect_status=none`、`captures_audio=false`、`spawns_process=false`、`calls_remote_provider=false`、`writes_local_files=false`。合法 result 只输出 `validated_noop_ipc_observed` / `ready_for_worker_mic_source_approval_review`，下一步为 `review_worker_mic_source_approval_packet`；该阶段仍不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 event file、不调用远程 ASR/LLM。

2026-07-03 PCWEB-114 执行结论：`Desktop Worker Mic Source Approval Packet` 已完成。`tools/desktop_worker_mic_source_approval.py` 复用 PCWEB-112 connector request 和 PCWEB-113 Tauri no-op result intake，只有二者均通过且 `tauri_run_result.run_id == connector.session_id` 时，才输出 `ready_for_manual_review_not_executable`，并在读取前阻断 forbidden `policy_path`。合法 report 仍保持 `worker_mic_source_approval_status=not_approved`、`approved_to_execute_now=false`、`safe_to_accept_worker_mic_source_now=false`，下一步为 `manual_approve_worker_prepare_source_kind_mic_or_keep_blocked`；focused gate 为 `8 passed, 1 warning`，仍不批准真实 worker/mic source、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 PCWEB-115 执行结论：`Real Mic Shadow Test Readiness Gate` 已完成。`tools/real_mic_shadow_test_readiness_gate.py` 组合 DRV-032 ASR quality decision、PCWEB-114/120 worker mic source approval packet、真实 Tauri no-op evidence、真实 mic adapter evidence、ASR worker mic source evidence 和 DRV-033/036/037/038/039 export/feedback readiness，默认输出 `blocked_not_ready_for_user_real_mic_shadow_test`。PCWEB-119 已补齐真实 Tauri no-op evidence，PCWEB-120 已补齐同 session manual review packet，PCWEB-121 已提供 mic adapter implementation-boundary evidence，PCWEB-122 已提供 ASR worker real mic source boundary evidence，PCWEB-123 已提供单次 worker mic source approval evidence 机制；默认无 approval record 时仍 not approved，提供合法 PCWEB-123 evidence 后默认仍被 ASR quality 阻断。PCWEB-115 现在可消费 DRV-032 的 `degraded_pilot_accepted_with_quality_risk`，但仅作为一次用户显式 shadow-test 风险接受，`counts_as_asr_quality_go_evidence=false`。PCWEB-115 CLI 已补齐 evidence input：可从 inline JSON 或 approved `artifacts/tmp/**` JSON 证据文件加载所有前置 evidence，且在读取前阻断 forbidden roots、`.m4a`、仓库外路径和非 JSON。该工具只做静态 preflight，不访问麦克风、不读取真实音频、不运行 Cargo/Tauri、不启动 worker、不调用远程 ASR/LLM、不下载模型或公开音频。

2026-07-03 PCWEB-116 执行结论：`Desktop Tauri No-op Run Result Collector` 已完成。Web 工作台的 `desktop-mic-adapter-contract-panel` 现在额外展示 10-command collector，普通浏览器输出 `collector_browser_fallback`、`desktop_tauri_noop_run_result.v1`、10 个 `not_invoked` row 和 `real_tauri_noop_result_ready=false`；未来 Tauri WebView 中会通过 `window.__TAURI__` 调用 PCWEB-107 的 10 个 no-op IPC，并把 result 放到 `window.__meetingCopilotTauriNoopRunResult`，形状对齐 PCWEB-113。Focused static asset gate 为 `1 passed, 2 warnings`，browser smoke 为 `status=ok`。该阶段不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 worker event file、不写本地文件、不调用远程 ASR/LLM。

2026-07-03 PCWEB-117 执行结论：`Desktop Tauri No-op Run Result Validation API/UI` 已完成。Web backend 新增 `POST /desktop/tauri-noop-run-results/validations`，复用 PCWEB-113 `desktop_tauri_noop_run_result_intake` validator；工作台普通浏览器显示 `validation_browser_fallback` / `pcweb_117_validation_status=not_submitted`，未来 Tauri WebView 中 PCWEB-116 collector 的 10 个 command 全部 returned 后才自动提交 validation。Focused gate 为 `4 passed, 2 warnings`，browser smoke 为 `status=ok`。该阶段不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 worker event file、不写本地 result 文件、不调用远程 ASR/LLM。

2026-07-03 PCWEB-118 执行结论：`Desktop First Controlled Cargo Check` 已完成。第一次受控 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 暴露两类真实编译阻塞：Tauri `generate_context!()` 默认需要 `src-tauri/icons/icon.png`，以及 `#[tauri::command] pub fn` 会触发 Tauri helper macro duplicate reexport。当前修复为新增最小 PNG icon、将 10 个 no-op command 改为 private `fn`、保留 `src-tauri/Cargo.lock`，并把 target 放到 ignored `artifacts/tmp/desktop_tauri_target`。Focused Cargo/Tauri policy/scaffold gate 为 `40 passed, 1 warning`，真实 `cargo check` 为 exit 0。PCWEB-118 只证明 Rust crate 可编译，不代表 `cargo tauri dev`、Tauri WebView、PCWEB-116 collector result、PCWEB-117 validation result、麦克风、worker 或 ASR quality 已完成。

2026-07-03 音频来源与模拟分工复审结论：两个只读审查 Agent 和官方网页复核一致确认，完整计划已经写下，公开音频、合成/Mock 转写和用户最终真实麦克风验证已经分开；AliMeeting/OpenSLR SLR119 与 AISHELL-4/OpenSLR SLR111 只作为 no-download 会议声学来源，AISHELL-1/OpenSLR SLR33 只做普通话 sanity，FunASR 仍需本地模型目录或 DRV-019 审批。下一步不再泛搜音频，不下载公开包，不把公开音频或 replay draft 当作产品价值 Go 证据。

2026-07-03 最新执行复核：用户再次确认“转写由我先通过网上公开音频和模拟完成，最终真实麦克风会议由用户验证”。本轮已复核 OpenSLR 官方来源并重跑 no-download 工具链：`tools/public_audio_source_whitelist.py` 通过，3 个白名单来源保持 `safe_to_download_now=false`；`tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio` 返回 `blocked_no_planned_samples`；`tools/public_audio_planned_sample_manifest_decision.py` 返回 `blocked_no_verified_public_sample_manifest`，blocked reasons 为 `no_verified_archive_member_path`、`no_expected_clip_sha256_after_extract`、`no_user_approval_for_gb_archive_download`。公开音频阶段因此继续停在官方来源复核和 manifest 模拟，不下载、不抽取、不喂 ASR。

2026-07-03 二次收敛确认：完整计划已写下且经只读 Agent 复审确认，不再新建散点计划。转写验证由我先通过官方授权公开来源审查、no-download manifest、合成音频、mock events 和 approved synthetic event replay 完成；真实麦克风会议由用户在前置链路满足后显式启动。当前 no-download 公开音频测试为 `25 passed, 1 warning`，短时模拟/ASR event 链路测试为 `24 passed, 1 warning`；ASR 质量 gate 默认仍是 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。PCWEB-119 已完成 `Real Tauri no-op run`，PCWEB-120 已把真实 Tauri evidence 接到同 session worker mic source manual review packet，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；下一步默认转向 ASR quality decision 退出动作，不继续泛搜公开视频、播客或版权链不清音频。

2026-07-03 最新用户确认后的执行锁：完整计划已经写下，本轮只做了官方来源/本地工具/只读 Agent 复核，没有访问麦克风、没有读取真实用户音频、没有下载公开音频大包、没有调用远程 ASR/LLM。公开音频阶段当前正确停在 `blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`，因为缺真实 archive member path、clip sha256 和 GB 级公开包下载审批；ASR 质量阶段默认正确停在 `requires_funasr_model_dir_or_drv019_approval`，因为 sherpa 中文技术实体召回不达标且 FunASR 本地模型目录缺失。本轮 DRV-032 已补齐 machine-readable quality exit contract：本地 FunASR、DRV-019、可选远端 ASR 对照、显式降级试点四条出口；显式降级试点只能结束评估循环并进入一次 shadow-test timing/feedback 验证，不能当作 ASR quality Go evidence。PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence，PCWEB-120 已完成同 session worker mic source manual review packet bridge，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；无 approval record 时仍 not approved，有合法 evidence 时仍不执行。下一轮不得继续新增泛化 plan/schema/readiness 来替代进展；优先顺序固定为：ASR quality 出口选择，最后才由用户执行真实麦克风会议 shadow test。

## 2. 当前已证明的结论

- Web Live ASR pipeline 已能从 `final/revision` 生成 EvidenceSpan、state、scheduler、suggestion candidate 和 LLM request draft。
- 本地 ASR event file handoff API 已完成边界加固，只允许 `artifacts/tmp/asr_events`。
- desktop-side ASR worker handoff preflight 已完成 descriptor 合同，但仍不启动 worker、不访问麦克风。
- PCWEB-096 已新增 desktop ASR worker handoff local dry-run bridge：默认只生成 Web handoff preview，显式 synthetic local test 可用临时 data dir 调 Web handoff API；仍不启动 worker、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM。
- PCWEB-097 已把 PCWEB-096 dry-run readiness/status 接入 Web/Tauri no-op UI/API：`GET /desktop/asr-worker-handoff-dry-run-readiness` 和 `desktop-asr-handoff-dry-run-panel` 只展示 readiness、allowed roots、blockers 和 false safety flags，仍不读 event file、不写 session、不启动 worker、不访问音频。
- DRV-025/026 tri-lane + batch gate 已完成；DRV-027 后当前 5 场景结论推进为 `overall_decision=blocked_by_asr_quality`。
- 5 个场景 perfect/mock lane 均已 ready；4 个工程场景 real ASR 因 sherpa 技术实体 recall 不达标而阻断。
- DRV-028 已完成一轮 bounded normalizer：`incident-review-001` normalized recall 从 0.0 到 0.25，`architecture-review-001` 从 0.0 到 0.2；剩余缺失实体没有足够 ASR 文本线索，不能继续用规则猜。
- 非工程 control 当前保持 0 candidate，是必须保护的安全边界。
- sherpa 速度可用，但中文技术实体质量不足；FunASR/Paraformer 仍需本地模型目录或明确模型下载审批。
- 计划确认和多 Agent 只读审查结论一致：当前不是缺计划，而是缺可用中文技术实体 ASR 质量和可运行桌面端闭环。
- PCWEB-098 已实现 desktop ASR worker process contract，定义 future worker lifecycle、command catalog、resource limits、event output contract、approved roots 和 no-spawn/no-audio/no-remote safety flags。
- PCWEB-099 已实现 desktop ASR worker command protocol，定义 `worker.prepare/start/stop/health/collect_events/cleanup` 的 request/response envelope、lifecycle transition preview、blocked response、approved roots 和 safety flags；readiness endpoint 下一步指针已从 PCWEB-098 改为 PCWEB-099；仍不启动 worker、不访问麦克风、不读写音频、不调用 Web mutation、不运行 Cargo/Tauri。
- PCWEB-100 已实现 desktop ASR worker synthetic lifecycle harness，按 `prepare -> start -> collect_events -> stop -> cleanup` 跑通 synthetic command sequence，并在 `collect_events` 阶段复用 PCWEB-096 临时 Web handoff；仍不启动真实 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri、不 mutate production Web session。
- PCWEB-101 已实现 desktop ASR worker implementation approval packet，定义未来 worker implementation 的 entrypoint、command runner、provider/source、event/runtime roots、资源预算和 approval tokens；合法 packet 也只会返回人工 review preview，不批准实现或执行。
- PCWEB-102 已实现 desktop ASR worker no-execution skeleton，新增 `code/asr_runtime/scripts/asr_worker_sidecar.py`、`tools/desktop_asr_worker_no_execution_skeleton.py` 和 `code/desktop_tauri/asr-worker-no-execution-skeleton.policy.json`；它只定义 module boundary 和 preview report，仍不启动 worker、不访问麦克风、不读写 event file、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-103 已实现 desktop ASR worker command runner binding preview，新增 `tools/desktop_asr_worker_command_runner_binding.py` 和 `code/desktop_tauri/asr-worker-command-runner-binding.policy.json`；它只验证 sidecar module path、future native command runner path、command catalog、provider/source 和 root 边界，合法 request 也保持 command dispatch、Tauri IPC、subprocess、health probe、event collection 和 worker execution 全部 not executed。
- PCWEB-104 已实现 desktop ASR worker command runner implementation skeleton / no-dispatch boundary，新增 `code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`、`tools/desktop_asr_worker_command_runner_implementation_skeleton.py` 和 `code/desktop_tauri/asr-worker-command-runner-implementation-skeleton.policy.json`；Rust skeleton 未在 `lib.rs` 中绑定，合法 request 也只返回 blocked command preview，仍不 accept/dispatch worker command、不 invoke Tauri IPC、不 spawn subprocess、不读写 event file、不访问麦克风、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri；PCWEB-104 不代表真实 command runner 或 worker execution 已获批。
- PCWEB-105 已实现 desktop microphone adapter contract，新增 `code/desktop_tauri/mic-adapter-contract.policy.json` 和 `tools/desktop_mic_adapter_contract.py`；它只定义 `prepare/status/start/pause/resume/stop/delete_audio_chunks` 合同、显式用户 start 边界、ignored runtime audio root、delete 语义和 all-false safety flags；仍不访问麦克风、不请求权限、不写 audio chunk、不读取真实用户音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-106 已实现 desktop microphone adapter contract readiness UI/API，新增 `GET /desktop/mic-adapter-contract-readiness` 和 `desktop-mic-adapter-contract-panel`；端点复用 PCWEB-105 静态 contract report，显示 `mic_adapter_contract_status=specified_not_executable`、7 个 mic adapter command、approved runtime/audio chunk roots、delete semantics 和 all-false safety flags；ASR handoff readiness endpoint 现指向 `next_pcweb_id=PCWEB-106`，仍不访问麦克风、不请求权限、不枚举设备、不写或删除 audio chunk、不读取真实用户音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-107 已实现 desktop mic adapter no-op Tauri IPC binding：`code/desktop_tauri/src-tauri/src/lib.rs` 现在静态绑定 10 个 no-op command，其中 7 个是 `mic_adapter.*`；`tools/desktop_tauri_noop_shell_run_smoke.py` 会校验 exact command set、bridge id set、`generate_handler!` set、function-to-command mapping 和 no-side-effect fields；focused TDD gate 为 `20 passed, 1 warning`。PCWEB-107 仍不运行 Cargo/Tauri、不请求权限、不访问麦克风、不写或删除 audio chunk、不启动 worker、不调用远程 ASR/LLM。
- PCWEB-108 已实现 worker output -> Web Live ASR session closure gate：`tools/desktop_asr_worker_handoff_local_dry_run.py` 的 `synthetic_local_test` 不再只证明 Web handoff API 接收成功，而是必须在临时 Web session response 中汇总 transcript final、EvidenceSpan、state event、scheduler event、suggestion candidate 和 LLM request draft；技术会议输入返回 `closed_to_evidence_state_gap`，非工程输入即使有 transcript/EvidenceSpan 也会因没有 state/gap candidate 返回 `blocked_by_live_session_closure`。该阶段仍不启动真实 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-109 已实现 mic adapter no-op UI invocation：Web 工作台在同一个 `desktop-mic-adapter-contract-panel` 中展示 PCWEB-105 合同 readiness 和 PCWEB-107 七个 no-op command 的 invocation status；普通浏览器固定显示 `mic_adapter_browser_fallback`、7 个 `not_invoked` row 和 no-permission/no-audio/no-remote/no-LLM/no-Cargo safety flags。该阶段仍不访问麦克风、不请求权限、不枚举设备、不写或删除 audio chunk、不启动 worker、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- PCWEB-112 已实现 worker/mic connector contract：同一 session 下的 `mic_adapter.start` preview 能被 PCWEB-105 校验为 `validated_not_executed`，`worker.prepare(source_kind=mic)` 能被 PCWEB-099 明确阻断为 `source_kind requires later approval: mic`，组合 report 输出下一步审批决策；仍不访问麦克风、不请求权限、不读写 audio chunk、不启动 worker、不读写 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- PCWEB-113 已实现 Tauri no-op run result intake：未来显式批准的真实 Tauri WebView no-op run 结果可以作为 caller-provided JSON 进入机器校验；缺失、失败、额外 command、side-effect flag 漂移或 raw path/secret/stdout 字段都会 blocked；合法结果只进入 worker mic source approval review，不自动批准真实 worker/mic source。
- PCWEB-114 已实现 worker mic source approval packet：PCWEB-112 connector 和 PCWEB-113 Tauri no-op result 必须同 session 才能形成人工 review packet；合法 packet 仍是 `not_approved` / `not_executable`，不移除 `source_kind=mic` 的真实执行边界。
- PCWEB-118 已实现 first controlled Tauri Rust `cargo check`：Tauri scaffold 当前可编译，`Cargo.lock` 已生成并应保留，target 位于 ignored `artifacts/tmp/desktop_tauri_target`；这只消除了 Rust crate compile blocker，仍未证明 Tauri WebView/no-op IPC runtime。
- PCWEB-110 已实现 short local simulated input timeline report：`tools/asr_live_pipeline_replay.py` 会在 replay report 中输出 approved synthetic event source、timeline window、ASR metrics、EvidenceSpan timeline、state timeline 和 candidate/card timeline；工程 mock sample 返回 `closed_to_candidate_timeline`，非工程 control 返回 `no_engineering_candidate_detected` 且 `candidate_card_timeline=[]`。该阶段仍不读取 `.m4a`、本地私有短音频、`data/asr_eval/local_samples` 或任意用户录音，不访问麦克风、不调用远程 ASR/LLM。
- PCWEB-111 已实现 ASR event provenance manifest：replay report 现在可从 `asr_event_provenance.v1` manifest 输出 `event_manifest_status`、repo-relative `event_manifest_path`、`input_source_kind` 和 sanitized `event_provenance`，用于区分 `synthetic_audio`、`mock_streaming` 和未来 `public_audio_sample`。Manifest path、events path 绑定、schema、side-effect flags 和 provenance id path-text 都会在读取 events 前校验；仍不读取音频、不下载公开包、不调用 ASR/LLM。
- DRV-034 已实现 public audio post-extraction evidence schema：只验证未来人工批准抽样后的 evidence JSON，不读取音频、不下载/抽取/转码、不调用 ASR/LLM。它把 DRV-031 的 planned sample manifest 与未来 ASR event provenance 之间的证据交接补齐。
- DRV-033 已实现 real mic shadow test report schema：新增 `tools/real_mic_shadow_test_report_schema.py` 和 `tests/test_real_mic_shadow_test_report_schema.py`，固定 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、feedback labels、Go/Pivot/Stop、privacy/cost flags、audio retention/delete status 和 known limitations；复审后已加固 nested timeline 字段、transcript/evidence/card 交叉引用、feedback aggregate、`go` 决策最低反馈、audio retention enum 和全 forbidden-root 预读阻断；默认只输出 schema contract，valid candidate report 只做 `schema_validated_no_audio_access` 校验；仍不访问麦克风、不读取真实录音、不写或删除 audio chunk、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- DRV-030 已完成 public/audio simulation guard hardening：`synthetic_asr_smoke_report` 现在会在读取前阻止 allowed root 内 symlink 指向 `configs/local` 等 forbidden roots；`public_audio_sample_extraction_plan` 会按 repo root 打开已校验的 planned samples 文件；`copilot_product_value_batch_gate` 的默认 mock lane 已改为 `.mock.events.json`，不再和 real sherpa lane 共用同一事件文件。
- DRV-032 已实现 ASR quality decision gate，并扩展为 ASR quality exit contract：`tools/asr_quality_decision_gate.py` 只组合 product value batch、FunASR readiness、DRV-019 approval packet 和 DRV-031 public audio decision，不运行 ASR、不下载模型、不下载公开音频、不访问麦克风、不调用远程 ASR/LLM；当前默认输出 `decision_status=requires_funasr_model_dir_or_drv019_approval`、`quality_exit_status=not_exited`、`recommended_quality_exit_path_id=local_funasr_model_dir_if_available_else_explicit_degraded_pilot_decision`，确认 perfect/mock 5/5 ready、real sherpa blocked 4/5、非工程 candidate=0、FunASR runtime cache/local model dir missing。合法 `asr_quality_degraded_pilot_acceptance.v1` 可输出 `degraded_pilot_accepted_with_quality_risk`，只解锁一次 shadow-test 风险试点前置判断，不算 ASR quality Go evidence。
- DRV-032 现在消费 DRV-042 simulated shadow pipeline batch status：若 batch 未通过，先返回 `fix_simulated_shadow_pipeline_first`，避免把产品链路问题误判成 ASR provider 问题；当前 DRV-042 batch passed 后，ASR quality 默认仍正确停在 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。
- DRV-043 已补齐 FunASR 本地模型预检 evidence 输入：`tools/funasr_synthetic_smoke_readiness.py --model-cache-root` 只检查必需模型文件且不回显绝对路径；`tools/asr_quality_decision_gate.py --funasr-readiness-path artifacts/tmp/**.json` 可消费该 evidence，并在 ready 时输出 `funasr_cache_preflight_ready_requires_execution_approval`，仍不运行 ASR、不下载模型、不访问麦克风。
- DRV-044 已补齐 FunASR synthetic smoke result evidence gate：`tools/funasr_synthetic_smoke_result_evidence.py` 只验证 approved `artifacts/tmp/asr_reports/**` 下的 caller-provided result JSON；单场景通过输出 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`，batch 4 工程 + 1 非工程 control 通过才输出 `funasr_synthetic_smoke_quality_batch_confirmed`。`tools/asr_quality_decision_gate.py --funasr-smoke-result-path` 可消费该 evidence；默认仍不运行 ASR、不访问麦克风、不下载模型。
- DRV-045 已补齐 FunASR synthetic smoke execution packet：`tools/funasr_synthetic_smoke_execution_packet.py` 消费 DRV-043 readiness evidence，生成 5 场景 manual command preview、expected outputs 和 DRV-044 batch provenance template；默认仍不执行命令、不读取音频、不写产物、不下载模型。它让未来本地模型就绪后的执行路径可重复、可审计，但不算 ASR quality evidence。
- DRV-046 已补齐 FunASR synthetic smoke batch evidence assembler：`tools/funasr_synthetic_smoke_batch_evidence_assembler.py` 消费 DRV-045 packet 和 5 个 approved smoke report JSON，计算 sha256 后组装 DRV-044 batch evidence 并调用 DRV-044 gate；默认没有 artifacts 时 blocked，不运行 ASR、不读音频、不写产物、不下载模型。
- DRV-047 已补齐 ASR quality 对 DRV-046 assembly report 的 intake：`tools/asr_quality_decision_gate.py --funasr-smoke-assembly-path` 可直接消费 DRV-046 `drv044_batch_evidence_validated` report，校验嵌套 DRV-044 gate report 后复用 strict quality exit；默认仍不运行 DRV-046、不读取 artifacts、不运行 ASR、不读音频、不下载模型。
- DRV-032 CLI acceptance input 已补齐：降级试点记录可通过 inline JSON 或 approved `artifacts/tmp/**` JSON path 提供；默认没有 acceptance record，仍输出 `not_requested` / `not_exited`。合法降级 path 只允许 PCWEB-115 继续检查其它真实会议前置条件，不能写成 ASR quality Go；CLI 会在读取前阻断 forbidden roots、`.m4a`、仓库外路径和非 JSON。

## 3. 当前不做什么

- 不继续泛化 ASR/provider 横评。
- 不抓取 Bilibili、YouTube、播客、公开课、直播回放或版权链不清音频。
- 不下载 AliMeeting/AISHELL-4/AISHELL-1 的 GB 级公开包，除非具体 sample manifest 和人工审批就绪。
- 不把 MagicHub Web Meeting 等 observed-but-not-whitelisted 候选绕过白名单用于自动下载、抽样、转码或产品价值 gate。
- 不读取真实用户录音或 `data/asr_eval/local_samples/`。
- 不访问麦克风。
- 不读取 `configs/local/` 或真实 API key。
- 不调用远程 ASR/LLM。
- 不自动下载 FunASR/ModelScope 模型。
- 不把 PCWEB-118 的 `cargo check` 通过解读成 Tauri 窗口已经运行、IPC 已观测、麦克风可访问或 worker 可启动。

## 4. 6 个收敛里程碑状态与退出条件

已完成项不得重复包装成下一步。后续只把直接推进 ASR quality、真实 Tauri evidence、worker/mic source、真实 mic adapter 或用户 pilot 的工作算作主线进展。

1. ASR quality decision 一次性决策：当前默认结论为 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`；只有提供已验证 FunASR 本地模型目录、明确批准 DRV-019、选择可选远程 ASR 对照，或提交合法 `asr_quality_degraded_pilot_acceptance.v1` 显式降级试点记录，才算从评估循环退出。降级试点只允许一次用户手动 shadow-test timing/feedback 验证，`counts_as_asr_quality_go_evidence=false`。不能继续扩 provider 横评，不能从 `<unk>` 或缺失文本猜实体。
2. Real Tauri no-op run：PCWEB-118 已证明 Tauri Rust crate 可编译；PCWEB-119 已在真实 Tauri WebView 中验证 10 个 no-op IPC returned，并把 validation passed evidence 写入 ignored `artifacts/tmp/desktop_tauri_noop_run_results`。这只消除了真实 Tauri no-op result 缺口，仍不访问麦克风、不启动 worker、不读取 secret、不调用远程 provider。
3. Worker output -> Web Live ASR session 闭环：PCWEB-108 已证明 approved synthetic event output 可经 `/live/asr/local-event-files/sessions` 写入临时 Web Live ASR session，并形成 transcript/EvidenceSpan/state/gap/candidate/request draft closure；仍不启动真实 worker、不读真实音频。后续若继续 M3，只能把同一 closure summary 接到 UI/报告，不扩大到真实 worker 或麦克风。
4. Mic adapter no-op UI invocation：PCWEB-109 已在 Web UI 中展示 `prepare/status/start/pause/resume/stop/delete_audio_chunks` 7 个 no-op invocation row；普通浏览器为 `mic_adapter_browser_fallback` / `not_invoked`，Tauri WebView 才允许调用 PCWEB-107 no-op IPC；`start` 仍只能 no-op/validated-not-executed，直到真实采集审批。
5. Short local simulated input：PCWEB-110 已完成 M5 timeline report；只能用合成生成音频、mock events 或 `artifacts/tmp/asr_events` 下 approved synthetic event file 跑 EvidenceSpan -> gap/state -> candidate/card，保持非工程 control 0 candidate，并输出 ASR metrics、EvidenceSpan timeline、state timeline、candidate/card timeline；不得读取 `.m4a`、本地私有短音频、`data/asr_eval/local_samples` 或任意用户录音。
6. Real mic shadow test report schema：DRV-033 已固定真实验收报告结构，包括 transcript、ASR metrics、EvidenceSpan/state/candidate/card timeline、feedback labels、privacy/cost flags 和 Go/Pivot/Stop；之后仍必须等 desktop runtime/worker/mic adapter/export 具备，再由用户最终执行 20-30 分钟真实中文技术会议。

DRV-036 已完成 `shadow report ingestion/export/feedback`，DRV-037 已完成 `shadow report export file writer`，DRV-038 已完成 `feedback ingestion API/UI`，DRV-039 已完成 `shadow-test pilot bundle runner`，DRV-041 已完成 `simulated shadow pipeline smoke runner`，DRV-042 已完成 `simulated shadow pipeline batch smoke`，PCWEB-112 已完成 `worker/mic connector contract`，PCWEB-113 已完成 `Tauri no-op run result intake`，PCWEB-114 已完成 `worker mic source approval packet`，PCWEB-115 已完成 `real mic shadow-test readiness gate`，PCWEB-116 已完成 `Tauri no-op run result collector`，PCWEB-117 已完成 `Tauri no-op run result validation API/UI`，PCWEB-119 已完成 `real Tauri no-op WebView IPC evidence`，PCWEB-120 已完成 `worker mic source from real Tauri evidence bridge`，PCWEB-121 已完成 `minimal mic adapter implementation boundary`，PCWEB-122 已完成 `ASR worker real mic source boundary`，PCWEB-123 已完成 `single-shadow worker mic source approval evidence`，PCWEB-124 已完成 `real mic shadow-test readiness API/UI visibility`。下一张主线票不得继续评测循环或继续做同类 readiness/report-only 包装器，默认应在 ASR quality decision 的退出动作中选择；不得再把公开音频 post-extraction evidence schema、shadow report draft adapter、report ingestion/export/feedback preview、export file writer、feedback ingestion API/UI、pilot bundle runner、simulated shadow pipeline smoke/batch、worker/mic connector wrapper、Tauri result-intake wrapper、Tauri result-validation wrapper、worker mic source approval wrapper、真实 Tauri no-op run wrapper、Tauri evidence bridge、mic adapter boundary、ASR worker mic source boundary、single-shadow approval evidence、真实麦克风 readiness gate 或 readiness UI 当作待做项。

PCWEB-118 已完成 first controlled Tauri Rust `cargo check` 并消除了当前 scaffold 的编译阻塞。PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence。PCWEB-120 已完成从真实 Tauri evidence 到同 session worker mic source manual review packet 的桥接。PCWEB-121 已完成最小 mic adapter implementation boundary，并让 readiness gate 可在提供该 evidence 时移除 mic adapter blocker。PCWEB-122 已完成 ASR worker real mic source boundary，并让 readiness gate 可在提供该 evidence 时移除 `asr_worker_real_mic_source_not_available` blocker。PCWEB-123 已完成单次 worker mic source approval evidence，并让 readiness gate 可在提供合法 evidence 时移除 `worker_mic_source_not_approved` blocker。PCWEB-124 已把 PCWEB-115 默认 readiness 暴露到 Web 工作台，便于直接看到仍被 ASR quality 和缺省 evidence 阻断。PCWEB-125 已把 synthetic Live ASR 产品链路接成可点击 Mac local shadow MVP 演示入口，并已通过 browser E2E 点击验证。DRV-032 已补齐 ASR quality exit contract，PCWEB-115 已能消费显式降级试点风险接受。下一张主线票现在应选择 ASR quality 出口，而不是继续新增 compile/readiness/boundary/approval 文档。真实麦克风会议仍必须等 ASR quality exit 或合法降级试点接受后，由用户显式启动。

2026-07-03 PCWEB-120 执行结论：`Desktop Worker Mic Source From Tauri Evidence` 已完成。`tools/desktop_worker_mic_source_from_tauri_evidence.py` 读取 PCWEB-119 capture evidence，校验 `captured_validated_tauri_noop_run`、validation passed、10 个 command returned 和 all-false safety flags，派生同 session PCWEB-112 connector request，再调用 PCWEB-114 生成 manual review packet。真实 evidence CLI run 输出 `ready_for_manual_review_not_executable`、`worker_mic_source_approval_status=not_approved`、`safe_to_capture_audio_now=false`、`safe_to_start_worker_now=false`；focused gate 为 `5 passed, 1 warning`。该阶段仍不批准 worker mic source、不访问麦克风、不请求权限、不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk、不调用远程 ASR/LLM。

2026-07-03 PCWEB-121 执行结论：`Desktop Mic Adapter Implementation Boundary` 已完成。`code/desktop_tauri/src-tauri/src/mic_adapter_runtime.rs` 定义 inert mic adapter runtime boundary evidence，`tools/desktop_mic_adapter_implementation_boundary.py` 校验 policy/Rust boundary 并输出 readiness gate 兼容 evidence；focused TDD gate 为 `6 passed, 1 warning`。该 evidence 能让 `real_mic_shadow_test_readiness_gate._mic_adapter_ready()` 返回 true，并从 readiness blockers 中移除 `mic_adapter_real_implementation_not_available`；但仍不请求权限、不访问麦克风、不采集或写入 audio chunk、不启动 worker、不调用远程 ASR/LLM。

2026-07-03 PCWEB-122 执行结论：`ASR Worker Real Mic Source Boundary` 已完成。`code/desktop_tauri/src-tauri/src/asr_worker_mic_source_runtime.rs` 定义 inert ASR worker mic source boundary evidence，`tools/desktop_asr_worker_real_mic_source_boundary.py` 校验 policy/Rust boundary 并输出 readiness gate 兼容 evidence；focused TDD gate 为 `6 passed, 1 warning`。该 evidence 能让 `real_mic_shadow_test_readiness_gate._asr_worker_ready()` 返回 true，并从 readiness blockers 中移除 `asr_worker_real_mic_source_not_available`；但仍不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 event/audio 文件、不访问麦克风、不调用远程 ASR/LLM。

2026-07-03 PCWEB-123 执行结论：`Worker Mic Source Single Shadow Approval` 已完成。`tools/desktop_worker_mic_source_single_shadow_approval.py` 只接收 caller-provided PCWEB-114 manual review packet report 和显式 approval record，校验 approval token、session id、scope 和 all-false safety flags；focused TDD gate 为 `6 passed, 1 warning`，集成 gate 为 `36 passed, 1 warning`。合法 evidence 能让 `real_mic_shadow_test_readiness_gate._worker_mic_source_ready()` 返回 true，并从 readiness blockers 中移除 `worker_mic_source_not_approved`；默认无 approval record 时仍 blocked/not approved，且任何路径仍不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 event/audio 文件、不访问麦克风、不调用远程 ASR/LLM。

2026-07-04 PCWEB-124 执行结论：`Real Mic Shadow-Test Readiness API/UI` 已完成。Web backend 新增 `GET /desktop/real-mic-shadow-test-readiness`，只调用 PCWEB-115 默认静态报告；Web 工作台新增 `desktop-real-mic-shadow-readiness-panel`，展示 readiness status、blockers、ASR quality exit、Tauri/worker/mic/export 前置状态、pilot protocol 和全部 safety flags。TDD 红灯为 `5 failed, 2 warnings`，实现后 focused gate 为 `5 passed, 2 warnings`。该面板只是把已有 go/no-go 可见化，不是新 gate，不访问麦克风、不读取真实音频、不读取证据文件或 `configs/local`、不创建真实存储、不启动 worker、不运行 Cargo/Tauri、不调用远程 ASR/LLM、不下载模型或公开音频。默认仍显示 `blocked_not_ready_for_user_real_mic_shadow_test`，下一步仍应按 DRV-032 ASR quality exit 或 bounded 公共/合成模拟路径推进。

2026-07-04 DRV-041 执行结论：`Simulated Shadow Pipeline Smoke Runner` 已完成。`tools/simulated_shadow_pipeline_smoke.py` 只编排已有 `asr_live_pipeline_replay`、`replay_shadow_report_draft_adapter` 和 `shadow_report_ingestion_export_feedback`，把 approved ASR event JSON 纯内存串到 `simulated_shadow_pipeline_preview_created`。工程 mock events 产出 `draft_export_preview_only`，非工程 control 返回 `blocked_by_no_candidate_timeline` 且不伪造 shadow report；可选 public audio provenance 只作为 `public_audio_sample` 来源审计，仍 `public_audio_download_status=not_downloaded`。TDD 红灯为 `5 failed, 1 warning`，原因是工具不存在；实现后 focused gate 为 `5 passed, 1 warning`。该工具不访问麦克风、不读取真实音频或 `.m4a`、不下载公开音频或模型、不调用远程 ASR/LLM、不写导出文件、不运行 Cargo/Tauri；它只证明模拟转写事件能闭合到产品价值 preview，不代表 ASR quality 达标或真实会议 ready。

2026-07-04 DRV-042 执行结论：`Simulated Shadow Pipeline Batch Smoke` 已完成。`tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events` 会运行 5 个默认 mock 场景，要求 `api/architecture/incident/release` 4 个工程场景全部 `simulated_shadow_pipeline_preview_created`，且 `non-engineering-control-001` 必须 `blocked_by_no_candidate_timeline` / `candidate_cards=0`。TDD 红灯为 `4 failed, 5 passed, 1 warning`，原因是 batch builder 和 CLI flag 不存在；实现后 focused gate 为 `9 passed, 1 warning`。真实本地 artifacts batch 输出 `scenario_count=5`、`engineering_preview_created_count=4`、`negative_control_blocked_count=1`、`negative_control_fake_candidate_count=0`，并保持 `not_go_evidence_batch_replay_or_feedback_missing`。该 batch 仍不访问麦克风、不读取真实音频、不下载模型/公开音频、不调用远程 ASR/LLM、不写导出文件。

每个下一步仍按 SDD/TDD 执行：先落需求/决策 ID 和失败用例或 smoke，再实现最小变更，最后把红灯/绿灯命令、结果、隐私边界和成本边界写回 RTM/decision-log/对应计划文档。

## 4.1 2026-07-04 DEC-205 / PCWEB-129 主线反馈与导出预览闭环

`PCWEB-129 Mainline Trial Feedback And Export Closure` 已完成。当前 PC 工作台已经支持：

```text
主线试运行
  -> suggestion candidates
  -> deterministic local feedback
  -> Markdown/JSON export preview
  -> explicit replay/synthetic not-Go evidence
```

新增入口：

- `POST /desktop/mainline-trial-feedback-export-closures`
- 工作台按钮 `闭环预览`
- 工作台面板 `主线闭环`

验证结果：

```text
Focused backend API red: 404 before endpoint existed
Focused backend API green: 1 passed, 2 warnings
Focused static UI red: missing closure button
Focused static UI green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline trial feedback export closure"
```

主线状态：

- 产品闭环已经从“试运行可见”推进到“反馈/导出预览可见”。
- 该闭环仍为 synthetic/replay draft preview，不是真实麦克风 Go evidence。
- 当前继续显示：
  - `export_readiness_status=draft_export_preview_only`
  - `go_evidence_status=not_go_evidence_replay_or_feedback_missing`
  - `final_decision=inconclusive_requires_more_shadow_tests`

后续不得再把同类 `mainline trial -> feedback/export preview` 包装成下一张主线票。下一步应在以下两类中选择：

- 继续推进真实麦克风前置缺口：ASR quality exit / explicit degraded pilot / real mic readiness evidence。
- 在现有 PC 工作台上推进真实桌面 runtime/mic adapter 的受控主流程，但仍必须先满足审批和 safety gate。

## 4.2 转写模拟与真实麦克风分工锁

后续不再把“转写验证”理解成马上开麦克风或读取真实录音。执行顺序固定为：

```text
官方公开音频来源复核
  -> no-download planned sample manifest
  -> 自建中文技术会议合成音频 / mock streaming events
  -> 本地 ASR events / tri-lane product gate
  -> desktop worker / IPC / mic adapter contract
  -> 用户最终真实麦克风 shadow test
```

当前可执行动作：

- 继续使用 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111 和 AISHELL-1 / OpenSLR SLR33 作为自动白名单来源；AliMeeting/AISHELL-4 用于会议声学，AISHELL-1 只做普通话 sanity check。
- MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 只做候选观察，不进入自动下载、抽样、转码或产品价值 gate。
- MISP-Meeting 只做 non-commercial observed candidate；PyCon China、QCon/InfoQ 只做 future authorized-only domain candidate。无书面授权、人工标注计划和许可审查前，不抓取、不抽音频、不转写、不进入产品价值 gate。
- WenetSpeech 明确排除，因为其音频依赖 YouTube/podcast 等平台来源。
- 没有具体 `planned_samples` 时，公开音频阶段必须保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`，不能绕去 Bilibili、YouTube、播客、公开课、技术大会录播或其他版权链不清音频。
- 真实麦克风会议只能在 desktop runtime、worker handoff、mic adapter start/pause/resume/stop/delete 和导出反馈链路具备后，由用户显式启动。

本轮短入口：`docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`。该文档是给后续执行看的压缩版结论，不替代本文档和总控计划；若口径冲突，以 P0/P1 文档和 decision-log 最新 accepted 决策为准。

## 5. 验证命令

Focused:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_contract.py \
  tests/test_asr_quality_decision_gate.py \
  tests/test_public_audio_source_whitelist.py \
  tests/test_public_audio_sample_extraction_plan.py \
  tests/test_copilot_product_value_tri_lane_gate.py \
  tests/test_copilot_product_value_batch_gate.py \
  -q -p no:cacheprovider
```

Batch gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_batch_gate.py \
  --scripts-root data/asr_eval/synthetic_meetings/scripts \
  --mock-events-pattern 'artifacts/tmp/asr_events/{script_id}.mock.events.json' \
  --real-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' \
  --real-smoke-report-pattern 'artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json' \
  --real-provider sherpa_onnx_streaming
```

ASR quality decision:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py
```

Mic adapter contract:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_mic_adapter_contract.py
```

Short local simulated input timeline:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py \
  -q -p no:cacheprovider
```

ASR event provenance manifest:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py \
  -q -p no:cacheprovider
```

Desktop first controlled cargo check:

```bash
CARGO_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/desktop_tauri_target \
/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

Public audio post-extraction evidence schema:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_post_extraction_evidence_schema.py \
  -q -p no:cacheprovider
```

Shadow report ingestion/export/feedback readiness:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_ingestion_export_feedback.py \
  -q -p no:cacheprovider
```

Shadow report export file writer:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_export_file_writer.py \
  -q -p no:cacheprovider
```

Shadow report feedback ingestion API/UI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_feedback_ingestion.py \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

Shadow-test pilot bundle runner:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_test_pilot_bundle_runner.py \
  -q -p no:cacheprovider
```

Worker/mic connector contract:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_connector_contract.py \
  -q -p no:cacheprovider
```

Tauri no-op run result intake:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_tauri_noop_run_result_intake.py \
  -q -p no:cacheprovider
```

Worker mic source approval packet:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_approval.py \
  -q -p no:cacheprovider
```

Real mic shadow-test readiness gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  -q -p no:cacheprovider

PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_readiness_gate.py
```

Tauri no-op run result collector browser smoke:

```bash
cd code/web_mvp
node e2e/browser_smoke.mjs
```

Tauri no-op run result validation API/UI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_accepts_collector_result_without_running_tauri \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_rejects_browser_fallback_or_side_effects \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_with_data_dir_does_not_create_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

Real mic shadow-test schema:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_report_schema.py
```

Full local gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

## 5.1 2026-07-03 直接检查点：计划、公开音频、模拟转写、真实麦克风

用户最新确认的问题是：完整计划是否已经写下；转写类验证是否由我先通过网上官方公开音频和模拟完成；最终真实麦克风会议是否由用户验证。

本轮结论：

- 完整计划已经写下，不缺新的计划文档。权威入口仍是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、本文档、`docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`、RTM 和 `docs/decision-log.md`。
- 两个只读审查 Agent 结论一致：当前缺的是执行态证据，不是计划文字。公开/合成音频由我先做模拟和自测；真实麦克风会议最终由用户在前置链路满足后显式启动。
- 官方网页复核确认 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 仍是当前可追溯公开来源池；FunASR 仍是中文质量主候选。
- OpenSLR 官方页显示 AliMeeting 是 Mandarin multi-channel meeting speech corpus，License 为 `CC BY-SA 4.0`，`Eval_Ali.tar.gz` 为 3.42G；本项目只保留 no-download manifest 路径，不默认下载。
- OpenSLR 官方页显示 AISHELL-4 是 Mandarin multi-channel meeting speech corpus，License 为 `CC BY-SA 4.0`，`test.tar.gz` 为 5.2G；本项目只保留 no-download manifest 路径，不默认下载。
- OpenSLR 官方页显示 AISHELL-1 License 为 `Apache License v.2.0`，`data_aishell.tgz` 为 15G；它只能做普通话 ASR sanity，不证明会议声学或产品价值。
- FunASR 官方 README 当前仍说明其支持 streaming 等 ASR 能力；本项目只在已有本地模型目录或 DRV-019 明确审批后验证，不静默下载模型。

当前状态：

- 公开音频：`blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`，原因是缺真实 `archive_member_path`、clip sha256 和 GB 级公开包下载审批。
- 合成/模拟：已有合成中文技术会议脚本、mock streaming events、approved synthetic event replay、synthetic ASR smoke report 和 product value gate；这些可继续由我自测，但不能替代真实会议。
- ASR 质量：默认 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`，因为 sherpa 中文技术实体召回不达标，FunASR 本地模型目录/cache 缺失；可选出口只有本地 FunASR、DRV-019、显式远端 ASR 对照或合法显式降级试点。
- 真实麦克风：`blocked_not_ready_for_user_real_mic_shadow_test`，因为 ASR quality 仍未全绿；PCWEB-121/122/123 已提供 mic adapter、ASR worker mic source 和单次 worker mic source approval evidence 机制，但不代表真实采集或真实 worker 已可执行。
- PCWEB-115 CLI：已能加载 inline JSON 或 approved `artifacts/tmp/**` JSON evidence，并基于 DRV-032/PCWEB-119/121/122/123/DRV-033/036/037/038/039 evidence 计算 readiness；默认 CLI 仍 blocked，不读取麦克风、真实音频、`configs/local` 或任何 forbidden root。

下一步默认执行顺序：

1. 不再新增泛化计划或同类 readiness/report-only wrapper。
2. 若要继续 ASR 质量：必须提供已验证 FunASR 本地模型目录、明确批准 DRV-019、显式选择远端 ASR 对照，或提交合法 `asr_quality_degraded_pilot_acceptance.v1` 降级试点记录。
3. 若要继续公开音频：只做 AliMeeting/AISHELL-4 的 3-5 个 official clip manifest；没有真实 archive member path 和 checksum 就保持 blocked。
4. PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence，PCWEB-120 已完成同 session worker mic source manual review packet bridge，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；不得再把这些当作未完成主线。
5. 下一步只在 DRV-032 quality exit 出口之间推进；显式降级试点只验证 timing/feedback，不算 ASR quality Go evidence；仍不访问麦克风、不启动真实 worker、不写真实 audio chunk。
6. 前置 gate 全绿后，才由用户执行 20-30 分钟中文技术会议真实麦克风 shadow test。

## 5.2 2026-07-04 DEC-199 本地 FunASR synthetic smoke 主链路执行结果

用户要求停止深入边界测试，先跑主流程。本轮已执行本地合成中文技术会议 FunASR 主链路：

```text
confirmed approval record
  -> approved runner --execute
  -> 5 provider commands
  -> 10 postprocess commands
  -> DRV-046 batch assembler
  -> DRV-032 ASR quality decision gate
```

执行证据：

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

结论：

- 主链路结构已跑通：本地合成音频 -> FunASR streaming artifacts -> transcript report -> single-result smoke report -> DRV-046 batch assembly -> DRV-032 quality gate。
- 质量门未过：工程场景 normalized technical entity recall 约为 `0.50 / 0.20 / 0.25 / 0.00`，低于 `>=0.80`；RTF 约为 `0.92-1.01`，高于 `<=0.60`。
- 失败点不是执行器或格式桥接，而是当前本地 FunASR route 对中文技术会议中的英文服务名、指标名、错误码和混合中英词保存不稳定。
- non-engineering control 没有生成工程 state/candidate，负控链路表现正常。
- 真实麦克风仍不能启动；不能把当前结果包装成 ASR quality Go evidence。

下一步只做收敛动作：

1. 基于已有 provider/events artifacts 修 deterministic hotword/normalizer 和技术词归一化，重跑 postprocess/DRV-046/DRV-032。
2. 若 recall 仍不足，再进入 FunASR 参数/热词或显式 degraded pilot 取舍。
3. RTF 后续要通过长驻 ASR worker 或 batch mode 剥离模型加载重新测，不能放宽阈值。

## 5.3 2026-07-04 DEC-200 技术词 normalizer 与 FunASR batch runtime 收敛结果

本轮已按 DEC-199 后续计划完成实现和自测：

- 增加 deterministic 技术词 near-miss normalizer，不从 `<unk>` 或缺失文本中补实体。
- 重新执行 10 条 postprocess command，没有重跑原 provider/FunASR 命令。
- 重新执行 DRV-046 和 DRV-032。
- 新增 `transcribe_streaming_batch()`，验证单进程复用 FunASR model 的 runtime 表现。

主链路最终结果：

```text
postprocess_executed=10
assembly_status=drv044_batch_evidence_blocked
counts_as_asr_quality_go_evidence=false
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
safe_to_capture_microphone_now=false
```

recall 改善：

| Scenario | Before | After |
| --- | ---: | ---: |
| `api-review-001` | `0.50` | `1.00` |
| `architecture-review-001` | `0.20` | `0.80` |
| `incident-review-001` | `0.25` | `0.50` |
| `release-review-001` | `0.00` | `0.75` |

RTF 对照：

| Runtime | RTF | 判断 |
| --- | ---: | --- |
| original per-file runner | `0.92-1.01` | 不达标 |
| batch `chunk_size=[0,10,5]` | `0.679` | 仍不达标 |
| batch `chunk_size=[0,20,10]` | `0.358` | 速度达标但文本质量下降 |

当前判断：

- normalizer 已把可恢复的 near-miss 拉起来，但不能解决 ASR 原文缺失实体。
- `chunk20` 可显著降低 RTF，但技术词 recall 更差，不能直接替换主链路。
- ASR quality exit 仍未退出，真实麦克风仍 blocked。
- 下一步如果继续主线，只能做受控 FunASR 参数/热词机制，或者用户显式接受 degraded pilot 风险。

## 5.4 2026-07-04 DEC-201 真实主链路自测与热词参数结论

用户要求不要继续深入边界测试，先跑真实主流程。本轮把 FunASR hotword runtime 的实际 artifacts 接入完整主链路：

```text
FunASR batch hotword provider/events
  -> transcript report
  -> single-result smoke evidence
  -> DRV-046 batch assembly
  -> DRV-032 ASR quality gate
  -> product-side replay/shadow pipeline
```

候选结果：

| Candidate | RTF | Engineering normalized recall | Gate |
| --- | ---: | ---: | --- |
| `chunk10_hotword` | `0.668-0.694` | `1.00 / 0.80 / 0.25 / 0.50` | blocked |
| `chunk20_hotword` | `0.355-0.363` | `0.50 / 0.60 / 0.25 / 0.50` | blocked |

产品 replay 结果：

- FunASR hotword events 可驱动 `api`、`architecture`、`release` 三个工程场景生成 preview/candidate。
- `incident-review-001` 两组都失败为 `blocked_by_no_candidate_timeline`，根因是 ASR 文本丢失足够多事故上下文。
- mock control 仍为 `simulated_shadow_pipeline_batch_passed`，`engineering_preview_created_count=4`，`negative_control_blocked_count=1`，说明产品 replay/card pipeline 本身不是当前主 blocker。

当前判断：

- 热词 manifest 支持保留为 provider 扩展口，但本轮不能解锁 ASR quality exit。
- `chunk20_hotword` 速度达标但质量不达标；`chunk10_hotword` 质量也未达标且速度不达标。
- 真实麦克风仍 blocked；下一步默认推进 PC 端产品主流程和 ASR-blocked 可见状态。若要提前开麦，只能走显式 degraded pilot 风险接受。

## 5.5 2026-07-04 DEC-202 / PCWEB-128 主线 ASR-blocked 试运行入口

本轮按 DEC-201 的下一步建议推进 PC 端产品主流程，而不是继续 ASR 边界评测。

新增能力：

- `POST /desktop/mainline-asr-blocked-trial/sessions`
- Web 工作台按钮：`主线试运行`
- Live ASR SSE 复用现有长会 synthetic timeline。
- Shadow MVP panel 展示 DEC-201 质量阻断摘要。

面板必须可见：

```text
mainline_asr_blocked_trial
DEC-201
not_exited
blocked_by_funasr_smoke_assembly_input_guard
chunk10_hotword
chunk20_hotword
incident-review-001
continue_pc_product_flow_keep_real_mic_blocked
```

验证：

```text
focused pytest: 2 passed, 2 warnings
browser smoke: status=ok, checked includes "mainline ASR blocked trial"
```

当前判断：

- PC 端已有一个主线入口展示“产品链路可运行，但真实麦克风因 ASR quality blocked 不能启动”。
- 这不是 ASR quality Go evidence，不是真实麦克风 Go evidence，也没有调用远程 ASR/LLM。
- 下一步继续产品可用闭环，而不是重启 provider 横评。

## 5.6 2026-07-04 DEC-203 PC 产品主流程 smoke 自测

用户要求不要继续深入边界测试，先把主流程跑通。本轮复用本地 Web MVP 服务和 PCWEB-128 主线入口，执行产品链路 smoke：

```text
POST /desktop/mainline-asr-blocked-trial/sessions
  -> Live ASR session manual_true_mainline_selftest_20260704
  -> /live/asr/sessions/{session_id}/draft
  -> /live/asr/sessions/{session_id}/llm-request-drafts
  -> browser smoke verifies workbench UI
```

自测结果：

```text
health: ok
focused pytest: 1 passed, 2 warnings
browser smoke: status=ok, checked includes "mainline ASR blocked trial"
```

主流程产物摘要：

- `transcript_final=13`
- `transcript_revision=3`
- `state_event=17`
- `scheduler_event=17`
- `suggestion_candidate_event=17`
- `llm_request_draft_event=17`
- draft review 中 `transcript_segments=16`、`evidence_spans=19`、`state_candidates=17`、`suggestion_candidates=17`
- LLM 仍为 `not_called`
- 真实麦克风 readiness 仍为 `blocked_not_ready_for_user_real_mic_shadow_test`

结论：

PC 产品主流程 smoke 已跑通，当前主 blocker 不是产品链路未连通，而是真实 ASR quality 与真实麦克风 readiness 未通过。当时的下一步 `PCWEB-129 Mainline Trial Feedback And Export Closure` 现已由 DEC-205 完成；后续不再围绕同类 preview 包装，继续回到 ASR quality exit、真实麦克风 readiness 或受控桌面 runtime 主线。

证据文档：

- `docs/mainline-product-smoke-selftest-2026-07-04.md`

## 5.7 2026-07-04 DEC-204 / PCWEB-130 UI/UX Pro Max 工作台重设计

用户要求安装 `ui-ux-pro-max-skill` 并改善当前展示页。已执行：

```text
npx --yes ui-ux-pro-max-cli init --ai codex
```

安装结果：

- 项目级：`.codex/skills/ui-ux-pro-max`
- 全局：`~/.codex/skills/ui-ux-pro-max`

设计取舍：

- 采用 skill 生成的暗色开发者工具、微交互、状态反馈、响应式与 reduced-motion 建议。
- 不采用其误判出的 landing-page pattern，因为当前目标是 PC 工作台，不是营销页。
- 页面改为深色专业工作台：品牌锁定区、分组 toolbar、绿色主线 CTA、阻塞状态 chip、深色 panel/tile、移动端单列状态条。

验证：

```text
TDD red: static asset test failed on missing app-shell
Focused green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline ASR blocked trial"
Visual:
  artifacts/tmp/ui_screenshots/workbench-home-dark-v4.png
  artifacts/tmp/ui_screenshots/workbench-mobile-dark-v4.png
Mobile layout: scrollWidth == clientWidth
```

证据文档：

- `docs/pcweb-130-ui-ux-pro-max-workbench-redesign.md`

## 5.8 2026-07-04 DEC-209 / M2 Mac 系统音频采集适配层

M2 已从“固定写未实现 blocker”推进为可执行的本地 adapter + mainline gap 接入。新增：

```text
tools/mac_system_audio_capture_adapter.py
tests/test_mac_system_audio_capture_adapter.py
docs/mac-system-audio-capture-m2-plan-2026-07-04.md
```

核心决策：

- 默认 no-capture preflight，不请求权限、不录音、不枚举或读取真实用户音频。
- 显式录制才通过 `ffmpeg avfoundation` 从指定 audio input index 采 16k mono s16 WAV。
- 要做 Mac 系统音频数字采集，当前推荐先走虚拟系统音频输入设备；ScreenCaptureKit 作为未来 native path，不在本 M2 实现内。
- 采集输出必须先进 M1 `audio_capture_healthcheck`，不能绕过健康门。
- 即使 M2 health pass，也只移除 `mac_system_audio_capture` blocker；不移除 `production_asr_quality` 和 `real_meeting_go_evidence` blocker。
- 不新增付费 ASR 项目，不调用远程 ASR/LLM，不读取 `configs/local`。

主链路 runner 已新增 `system_audio_capture` report 字段。默认仍显示：

```text
capture_adapter_status=preflight_only_not_capturing
mac_system_audio_capture=blocked_requires_m2_system_audio_capture
```

当注入或未来真实显式 M2 capture 的 `audio_health.health_status=audio_capture_health_passed` 时：

```text
mac_system_audio_capture=implemented_and_verified
real_meeting_go_evidence=blocked_requires_explicit_user_approval
```

M2.1 已把显式系统音频 capture 接进 mainline CLI：

```text
--system-audio-record-seconds
--system-audio-device-index
--system-audio-output-path
```

只有 `--system-audio-record-seconds > 0` 才调用 M2 adapter。默认主链路仍不录制、不请求权限。

已完成验证：

```text
M2 adapter: 8 passed, 1 warning
mainline integration: 9 passed, 2 warnings
adjacent regression: 28 passed, 2 warnings
mainline CLI + browser smoke: exit 0, browser_smoke_status=passed
sensitive scan: no matches
```

后续仍需用户明确授权后的真实 Mac virtual-system-audio health capture。

## 5.9 2026-07-04 DEC-210 / 本地 Artifact Retention And Delete Boundary

本地主链路自测产物现在有 retention/delete 边界。新增：

```text
tools/local_artifact_retention.py
tests/test_local_artifact_retention.py
docs/local-artifact-retention-delete-2026-07-04.md
```

主链路 runner 已新增：

```text
artifact_retention
```

默认策略：

```text
retention_status=local_artifacts_retained
retention_policy=local_artifacts_retained_until_explicit_delete
```

显式删除只允许 approved ignored artifact roots，不读取 artifact 内容，只记录 path/existence/size/action，并在删除前阻断 `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/` 和 repo 外路径。

已完成验证：

```text
local artifact retention: 4 passed, 1 warning
mainline integration: 13 passed, 2 warnings
adjacent regression: 32 passed, 2 warnings
syntax: exit 0
```

## 5.10 2026-07-04 DEC-211 / 主链路 Copilot Report Preview

主链路 runner 现在不再只输出 `draft_review_created`，而是把结构化 Live ASR draft 转成一等 `copilot_report_preview` 字段。新增能力：

```text
transcript
  -> evidence_span
  -> meeting_state
  -> suggestion_candidate
  -> llm_request_draft
  -> feedback_export_preview
```

报告字段：

```text
draft_review.formal_report_status=formal_report_preview_created
copilot_report_preview.preview_status=copilot_report_preview_created
copilot_report_preview.is_formal_go_evidence=false
copilot_report_preview.value_chain=[transcript,evidence_span,meeting_state,suggestion_candidate,llm_request_draft,feedback_export_preview]
```

Markdown 报告新增 `Copilot Report Preview` 章节，展示 top state items、top suggestion candidates、closure decision 和 quality blockers。该 preview 不调用 LLM、不调用远程 ASR、不读取真实音频，不是真实会议 Go evidence。

验证：

```text
RED: formal_report_status was not_created
GREEN: tests/test_mainline_usable_e2e_runner.py::test_runner_executes_mainline_and_writes_traceable_reports passed
```

## 5.11 2026-07-04 DEC-212 / ASR Quality Decision Evidence Into Mainline

主链路 runner 现在可以读取 approved ASR quality decision artifact：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_evidence_mainline_20260704 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json
```

新增报告字段：

```text
asr_quality.source_status
asr_quality.source_path
asr_quality.decision_status
asr_quality.quality_exit_status
asr_quality.blocked_reasons
```

边界：

- 只读 `artifacts/tmp/asr_reports/*.json`。
- 在读取前阻断 `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/`、repo 外路径、`.m4a` 和非 JSON。
- 不运行 ASR、不读取音频、不下载模型、不调用远程 ASR/LLM。

真实已有 artifact 的主链路结论：

```text
asr_quality.source_status=provided_asr_quality_decision_report
asr_quality.decision_status=blocked_by_funasr_smoke_assembly_input_guard
asr_quality.quality_exit_status=not_exited
production_asr_quality=blocked_by_asr_quality
gap_summary.implemented_and_verified=6
```

这说明 ASR quality blocker 已经从“没接入主链路”推进到“主链路可消费真实本地质量证据，且证据显示当前未达生产门槛”。后续不要继续新增同类 readiness/report-only wrapper；默认优先级为：

```text
1. 修复或优化本地 FunASR synthetic smoke 质量，争取 DRV-046/044/032 通过
2. 让 approved ASR event artifact 直接进入 Web Live ASR handoff，降低 hardcoded mock 占比
3. 用户明确授权后再跑真实 Mac system audio / mic health capture
```

最终验证：

```text
mainline runner: 13 passed, 2 warnings
adjacent regression: 36 passed, 2 warnings
syntax check: exit 0
final browser mainline smoke with ASR quality artifact: exit 0, browser_smoke_status=passed
sensitive scan: no matches
```

## 5.12 2026-07-04 DEC-213 / Approved ASR Event Artifact Handoff Into Mainline Runner

主链路 runner 现在可以把 approved ASR event artifact 交给现有 Web handoff API：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_handoff_verified_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/api-review-001.mock.events.json \
  --asr-events-provider local_mock_asr_artifact
```

新增报告字段：

```text
asr_event_handoff.handoff_status
asr_event_handoff.events_path
asr_event_handoff.event_source
asr_event_handoff.input_event_counts
asr_event_handoff.live_event_counts
```

总控 smoke 结论：

```text
asr_event_handoff.handoff_status=local_asr_event_file_handoff_created
asr_event_handoff.event_source.is_mock=false
asr_event_handoff.live_event_counts.transcript_final=3
asr_event_handoff.live_event_counts.state_event=1
asr_event_handoff.live_event_counts.suggestion_candidate_event=1
asr_event_handoff.live_event_counts.llm_request_draft_event=1
asr_event_artifact_handoff=implemented_and_verified
gap_summary.implemented_and_verified=7
```

这把“ASR artifact 能进入 Web Live ASR handoff”从历史工具/endpoint 能力提升到了当前总控 runner 的可验证分支。它仍不代表 ASR quality 通过，也不是真实会议 Go evidence。

验证：

```text
RED: run_mainline_usable_e2e_selftest did not accept asr_events_path
GREEN: 1 passed, 2 warnings
mainline runner: 13 passed, 2 warnings
final browser smoke with ASR quality + ASR event artifact: exit 0, browser_smoke_status=passed
```

## 5.13 2026-07-04 DEC-214 / ASR Event Artifact As Mainline Trial And Feedback Closure Source

主链路 runner 现在不再只把 approved ASR event artifact 当作辅助 handoff。只要传入 `--asr-events-path`，主 session 就会创建为 artifact-backed trial，并且 draft review、Copilot report preview、feedback/export closure 都围绕同一个 artifact-backed session 执行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_mainline_closure_green_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
```

新增/关键报告字段：

```text
mainline_trial.trial_id
mainline_trial.ingest_mode
mainline_trial.events_path
mainline_trial.source_event_artifact_status
closure.source_trial_id
closure.source_event_artifact_status
asr_event_artifact_closure
```

总控 smoke 结论：

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
mainline_trial.trial_id=mainline_asr_event_artifact_trial
mainline_trial.trial_status=mainline_artifact_trial_session_created
live_asr.event_counts.transcript_final=4
live_asr.event_counts.suggestion_candidate_event=3
closure.source_trial_id=mainline_asr_event_artifact_trial
closure.source_event_artifact_status=local_asr_event_file_handoff_created
closure.closure_status=mainline_trial_feedback_export_preview_created
browser_smoke_status=passed
gap_summary.implemented_and_verified=8
```

边界结论：

```text
api-review-001.mock.events.json -> only 1 suggestion candidate -> closure reports blocked_by_candidate_report
m15_runner_artifact_mainline.events.json -> 3 suggestion candidates -> closure preview created
```

这说明主链路现在可以区分两件事：

- 工件可读、可 handoff；
- 工件内容是否足以支撑 meeting-copilot feedback/export closure。

验证：

```text
RED: artifact-backed closure endpoint/test missing, then source tool loader used mutable REPO_ROOT
GREEN: code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_event_artifact_trial_closes_feedback_export_preview passed
RED: runner still used blocked trial as main source
GREEN: tests/test_mainline_usable_e2e_runner.py::test_runner_uses_asr_event_artifact_as_mainline_trial_source passed
focused regression: 15 passed, 2 warnings
final artifact-backed browser smoke: exit 0
```

## 5.14 2026-07-04 DEC-215 / Artifact-Backed Mainline Is Visible In PC Workbench

PC workbench 现在新增 `工件主线` 按钮。该入口调用：

```text
POST /desktop/mainline-asr-event-artifact-trial/sessions
events_path=artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json
provider=local_artifact_asr
```

用户可见链路：

```text
工件主线
  -> mainline_asr_event_artifact_trial
  -> Live ASR SSE
  -> transcript / EvidenceSpan / state / suggestion candidate / LLM draft
  -> 闭环预览
  -> source_event_artifact_status=local_asr_event_file_handoff_created
```

Browser smoke 已覆盖：

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: exit 0
Checked:
  - mainline ASR event artifact trial
  - mainline ASR event artifact feedback export closure
```

前端边界：

- 不读取音频。
- 不请求麦克风权限。
- 不调用远程 ASR/LLM。
- 不读取 `configs/local`。
- artifact preview 仍标记为 not-Go evidence。

剩余主线方向更新：

```text
1. ASR quality exit: FunASR synthetic smoke 仍未通过 DRV-046/044/032。
2. Real capture: Mac system audio / mic 仍需要用户明确授权和设备路由。
3. LLM execution: 当前仍是 request draft，不默认调用远程模型。
4. UI refinement: 继续改善状态密度、候选不足原因和 ASR quality blocker 的展示，但主入口已可用。
```

## 6. 关键文档

- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/copilot-product-value-batch-result-2026-07-03.md`
- `docs/asr-quality-decision-gate-2026-07-03.md`
- `docs/asr-mainchain-fullflow-selftest-2026-07-04.md`
- `docs/mainline-product-smoke-selftest-2026-07-04.md`
- `docs/pcweb-130-ui-ux-pro-max-workbench-redesign.md`
- `docs/mac-system-audio-capture-m2-plan-2026-07-04.md`
- `docs/local-artifact-retention-delete-2026-07-04.md`
- `docs/mainline-goal-progress-and-next-plan-2026-07-04.md`
- `docs/pcweb-105-desktop-mic-adapter-contract-plan.md`
- `docs/pcweb-106-desktop-mic-adapter-contract-readiness-ui-plan.md`
- `docs/pcweb-107-desktop-mic-adapter-noop-tauri-ipc-binding-plan.md`
- `docs/pcweb-110-short-local-simulated-input-timeline-report-plan.md`
- `docs/pcweb-111-asr-event-provenance-manifest-plan.md`
- `docs/pcweb-112-desktop-worker-mic-connector-contract-plan.md`
- `docs/pcweb-113-desktop-tauri-noop-run-result-intake-plan.md`
- `docs/pcweb-114-desktop-worker-mic-source-approval-plan.md`
- `docs/pcweb-115-real-mic-shadow-test-readiness-gate-plan.md`
- `docs/pcweb-116-desktop-tauri-noop-run-result-collector-plan.md`
- `docs/drv-033-real-mic-shadow-test-report-schema-plan.md`
- `docs/drv-034-public-audio-post-extraction-evidence-schema-plan.md`
- `docs/drv-035-replay-shadow-report-draft-adapter-plan.md`
- `docs/drv-036-shadow-report-ingestion-export-feedback-plan.md`
- `docs/drv-037-shadow-report-export-file-writer-plan.md`
- `docs/drv-038-shadow-report-feedback-ingestion-api-ui-plan.md`
- `docs/drv-039-shadow-test-pilot-bundle-runner-plan.md`
- `docs/drv-045-funasr-synthetic-smoke-execution-packet-plan.md`
- `docs/drv-046-funasr-synthetic-smoke-batch-evidence-assembler-plan.md`
- `docs/drv-047-asr-quality-drv046-assembly-intake-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 4.2 2026-07-04 DEC-216 / ASR quality follow-up 结论

本轮 ASR quality follow-up 已完成一轮收敛，但没有通过质量出口。

关键变化：

- 单场景 FunASR smoke report 现在输出 expected/matched/missing entity 明细。
- DRV-044 gate 会在这些明细存在时校验 recall 数值和 matched/missing 集合一致性。
- normalizer/glossary 只修当前 transcript 中可观察的 near-miss：`paymentway`、`字段 quest`、`ure store`、`REDi coasterBQP`、`auder` backlog 上下文、`trcoutservice service`。
- 没有反填 `timeout`、`监控阈值`、`staging`。

最新 `chunk20_hotword` 结果：

```text
api-review-001:          normalized_recall=1.0, missing=[]
architecture-review-001: normalized_recall=1.0, missing=[]
incident-review-001:     normalized_recall=0.5, missing=[timeout, 监控阈值]
release-review-001:      normalized_recall=0.75, missing=[staging]

DRV-046 assembly_status=drv044_batch_evidence_blocked
DRV-032 quality_exit_status=not_exited
```

主线回归仍通过 artifact-backed runner + browser smoke，但真实麦克风继续 blocked。下一步不再堆 normalizer，只做受控 ASR 输入/参数实验，或进入远程 ASR 成本/隐私审批、显式 degraded pilot 决策。
