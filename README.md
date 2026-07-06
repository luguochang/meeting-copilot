# Meeting Copilot 工作区

> 中文技术会议实时 AI Copilot 需求设计、ASR 可行性验证和 PC/桌面端主线工作区。  
> 当前阶段：PC/desktop mainline；本地 Web MVP 是 core/API/UI/event 的验证切片，当前主线已经推进到 Tauri no-op shell、ASR worker handoff、mic adapter no-op invocation、worker/mic connector contract、Tauri no-op run result intake、worker mic source approval packet、real mic shadow-test readiness gate、Tauri no-op run result collector、Tauri no-op run result validation API/UI、first controlled Tauri `cargo check`、real Tauri no-op WebView IPC evidence、worker mic source from real Tauri evidence bridge、minimal mic adapter implementation boundary、ASR worker real mic source boundary、worker mic source single-shadow approval evidence、real mic shadow-test readiness API/UI、short local simulated input timeline report、ASR event provenance manifest、shadow report ingestion/export/feedback readiness、ignored artifact export file writer、shadow report feedback ingestion API/UI、shadow-test pilot bundle runner、simulated shadow pipeline smoke 和 simulated shadow pipeline batch 前置闭环。

## 目录

```text
meeting-copilot/
  docs/                    产品、需求、架构、评测文档
  code/                    可运行代码
    core/                  平台无关 Copilot core
    web_mvp/               本地 PC Web MVP
    desktop_tauri/         PCWEB-082 Tauri desktop shell scaffold
    asr_bakeoff/           中文技术会议 ASR 评测工具
    asr_runtime/           本地 ASR runtime、EvidenceSpan、demo gate
  tools/                   本地开发、验证和质量门禁脚本
  tests/                   跨模块工具脚本测试
  data/                    评测数据、样本、标注、reference
  configs/                 provider 配置、mock 输入
  results/                 bake-off 输出结果
  artifacts/               临时产物和导出物
```

## 当前核心结论

- 这个产品应优先做桌面端，不是纯 Web。
- 第一技术闸门是中文技术会议 ASR，不是 UI 或 LLM。
- 默认不增加远程 ASR 付费项：MVP 优先本地/开源 ASR，远程 ASR 只作评测对照或可选高质量模式。
- 架构基座决策：不 fork 某个会议助手开源项目做二开；采用自建薄主线，复用 Tauri、FastAPI、本地 ASR provider、FunASR/sherpa 和 OpenAI-compatible LLM 协议这些成熟组件。若未来发现 500+ star 项目有可复用模块，只能作为 provider/adapter，不替代 EvidenceSpan -> gap/card -> feedback 的产品主线。
- mock bake-off 只证明评测管线可跑，不证明真实 ASR 效果。
- 实时价值来自缺口雷达、建议卡片、会议状态机和证据链。
- PC-1 已进入本地 Web MVP 垂直切片：core gate、本地 API、静态工作台、demo fixture、replay event stream、JSON session 持久化和浏览器 E2E gate。
- PCWEB-082 已进入第一个真实桌面壳静态 scaffold：`code/desktop_tauri/src-tauri` 包含 `tauri.conf.json`、Rust no-op bridge handlers 和最小 Tauri capability，但仍不运行 Tauri、不安装依赖、不捕获音频、不启动 worker。
- 2026-07-02 纠偏后，主线切到 desktop runtime validation；2026-07-03 PCWEB-118 已用 ignored `artifacts/tmp/rust_toolchain` 临时工具链完成第一次受控 Tauri Rust `cargo check`，`Cargo.lock` 已生成并应保留，Cargo target 保持在 ignored `artifacts/tmp/desktop_tauri_target`。这仍不代表 `cargo tauri dev`、Tauri 窗口、WebView IPC、麦克风或 worker 已完成。
- 2026-07-03 已按公开/合成音频计划实现 DRV-003 到 DRV-008：公开音频 bounded sample extraction plan、5 个中文合成技术会议脚本 gate、本地/offline-only 合成音频计划、本地 ASR event generation plan、本地 synthetic TTS smoke、synthetic ASR smoke report。当前仍不下载公开音频、不调用远程 ASR/LLM、不读取真实用户音频或 `configs/local/`。
- 最新 synthetic/sherpa 结论：链路和速度可跑，16.83s 合成音频 latency 516ms、RTF 0.030667；但中文技术实体质量未达标，normalized recall 0.25。DRV-027 后 `architecture-review-001` perfect/mock lane 已转 ready，`incident-review-001` mock lane 也转 ready；当前 5 场景 batch 已从 `blocked_by_product_logic` 推进到 `blocked_by_asr_quality`，下一步进入 FunASR/normalizer/hotword 的受控 ASR 质量路径。
- 最新 FunASR / ASR quality 结论：本地 synthetic FunASR 主链路已经按 approved runner 执行并进入 DRV-046/DRV-032；流程可追溯，但 ASR quality gate 仍未退出，主要问题是 batch evidence/DRV-044 gate 未满足和中文技术实体 recall 未达阈值。`transcribe_funasr.py --streaming` 仍要求显式 `--local-model-dir`，缺失或目录不完整时返回 blocked，不构造可能自动下载的 `AutoModel`。
- 最新 FunASR 模型准备结论：已新增 DRV-019 模型下载审批包 `code/asr_runtime/funasr-model-download-approval.policy.json` 和 `tools/funasr_model_download_approval_packet.py`；它只生成 `manual_user_run_only` 静态报告，记录 ModelScope/iic 模型、约 840MB 磁盘风险、手动下载说明、清理策略和 post-download 验证顺序，当前仍不下载模型、不执行命令、不运行 FunASR smoke。
- 最新 FunASR synthetic smoke 执行包结论：DRV-045 已新增 `tools/funasr_synthetic_smoke_execution_packet.py`，只在 DRV-043 readiness 合法时生成 5 场景 manual command preview、expected output paths 和 DRV-044 batch provenance template；默认 `safe_to_execute_now=false`，不运行 ASR、不读取音频、不写 artifacts、不下载模型、不调用远程 provider。它让未来本地模型就绪后的 smoke 执行路径可重复、可审计，但不算 ASR quality evidence。
- 最新 ASR quality assembly intake 结论：DRV-047 已让 `tools/asr_quality_decision_gate.py --funasr-smoke-assembly-path` 直接消费 DRV-046 assembly report，校验 `drv044_batch_evidence_validated`、all-false safety flags 和嵌套 DRV-044 batch confirmed gate report 后复用 strict quality exit；默认仍不运行 DRV-046、不读取 artifact 内容、不运行 ASR、不读取音频、不下载模型、不调用远程 provider。
- 最新 synthetic batch smoke 结论：5 个中文合成会议脚本已用本机 `say` + `afconvert` 生成 16kHz mono wav，并用本地 sherpa 跑完 baseline；RTF 约 0.029-0.035，速度稳定。确定性 normalizer 增量后，`api-review-001` normalized technical entity recall 从 0.25 提升到 0.5，但工程脚本整体仍只有 0-0.5，未达 first-pilot，详见 `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`。
- 最新 synthetic product value gate 结论：`tools/synthetic_product_value_gate.py` 已把 ASR 事件完整性、技术实体召回、expected gaps/cards 和非工程 control 合并判定；4 个工程脚本均为 `needs_asr_quality_work`，非工程 control 为 `negative_control_passed`。当前不能进入真实麦克风 pilot。
- 最新 Live ASR replay 结论：已新增 `tools/asr_live_pipeline_replay.py`，可把 `artifacts/tmp/asr_events/*.events.json` 接入 Web Live ASR pipeline，验证 transcript final -> EvidenceSpan -> state/scheduler/suggestion candidate/request draft 的无 LLM 链路。真实 sherpa artifacts 复测显示 api/release 可产生 1 个 evidence-backed candidate，非工程 control 已收敛为 0 state / 0 scheduler / 0 candidate。
- 最新 worker handoff API 结论：Web backend 已新增 `POST /live/asr/local-event-files/sessions`，只允许从 `artifacts/tmp/asr_events` 读取本地 ASR event JSON 并创建 Live ASR session；该入口阻止 `configs/local`、真实音频样本、local runtime、outputs 和 symlink 绕过，仍不访问麦克风、不跑 ASR 模型、不调用远程 ASR/LLM。
- 最新 PC mainline 结论：approved ASR event artifact 已从辅助 handoff 升级为 `mainline_asr_event_artifact_trial`，可通过 runner 和工作台“工件主线”进入 transcript -> EvidenceSpan -> state -> suggestion candidate -> no-call LLM draft -> feedback/export preview closure。该链路已由 browser smoke 覆盖；仍不代表 ASR quality 通过，也不是真实会议 Go evidence。
- 最新公开音频计划结论：完整转写验证计划已落档，不再泛搜版权不清音频；公开来源优先 AliMeeting / OpenSLR SLR119，其次 AISHELL-4 / OpenSLR SLR111。`public_audio_sample_extraction_plan` 已支持 planned sample schema 校验，但校验通过仍只代表 `schema_validated_no_download`，不下载、不解压、不转码；没有具体 planned samples 时返回 `blocked_no_planned_samples`。DRV-031 新增 `tools/public_audio_planned_sample_manifest_decision.py`，默认结论为 `blocked_no_verified_public_sample_manifest`：当前缺真实 archive member path、clip sha256 和 GB 级公开包下载审批，因此公开音频阶段继续保持 no-download blocked，不转向版权不清来源。
- 最新 ASR quality decision 结论：DRV-032 `tools/asr_quality_decision_gate.py` 只组合 product value batch、FunASR readiness、DRV-019 approval packet 和 DRV-031 public audio decision，不运行 ASR、不下载模型、不访问麦克风、不调用远程 ASR/LLM。当前默认结论为 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`：perfect/mock 5/5 ready、real sherpa ASR 4 个工程场景仍 blocked、非工程 candidate=0、FunASR runtime cache/local model dir 缺失。Gate 现在明确四个出口：已验证本地 FunASR 模型目录、DRV-019 手动模型下载、显式远端 ASR 对照、显式降级试点；默认推荐无新增 provider 费用路径。显式降级试点只允许一次 shadow-test timing/feedback 验证，`counts_as_asr_quality_go_evidence=false`。
- 最新 DRV-032 CLI input 结论：`tools/asr_quality_decision_gate.py` 已支持 `--degraded-pilot-acceptance-json` 和 `--degraded-pilot-acceptance-path`。Path 只允许 approved `artifacts/tmp/**` JSON；读取前会阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。默认仍是 `acceptance_input_status=not_requested` / `quality_exit_status=not_exited`；合法降级接受只让 PCWEB-115 继续检查其它前置条件，不算 ASR quality Go evidence。
- 2026-07-03 追加检索到 MagicHub Web Meeting 小体量候选，但页面标注 CC BY-NC-ND 4.0，当前只记录为 observed-but-not-whitelisted：不自动下载、不抽样、不转码、不进入产品价值 gate。
- 权威总控入口见 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`；公开/合成音频与真实麦克风验收细节见 `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`。用户最终再做真实麦克风会议验证。
- 2026-07-04 最新确认：完整计划已经写下；转写验证由我先通过官方公开音频来源复核、no-download manifest、合成音频、mock streaming events 和 approved replay 自测；最终真实麦克风会议由用户验证。PCWEB-124 已把 PCWEB-115 readiness 显示到 Web 工作台，默认仍是 `blocked_not_ready_for_user_real_mic_shadow_test`，不访问麦克风、不读取真实音频、不下载公开音频大包、不调用远程 ASR/LLM。
- 当前验证结构固定为：合成中文技术会议验证工程语义和卡片价值，公开授权音频验证声学和事件链路，真实麦克风会议由用户最终验收；实际执行顺序以 `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md` 为准。
- 2026-07-03 计划确认和多 Agent 只读审查结论：完整计划已经写下，当前不是缺计划，而是缺可用中文技术实体 ASR 质量和可运行桌面端闭环。公开音频由我先做来源审查与 no-download sample manifest，合成音频和本地 ASR 由我自测；真实麦克风会议最终由用户执行 shadow test。
- 2026-07-03 二次公开音频检索结论：MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 只记录为 observed-but-not-whitelisted 候选；WenetSpeech 因平台音频版权链风险明确排除。默认自动白名单仍只保留 AliMeeting、AISHELL-4 和 AISHELL-1；任何公开音频都先做 no-download manifest，不抓 B 站/YouTube/播客/公开视频。
- 2026-07-03 多 Agent 对抗审查后的执行锁：后续主线压成 6 个里程碑，分别是 ASR 质量一次性决策、真实 Tauri no-op run、worker handoff 闭环、mic adapter no-op UI invocation、短时本地模拟输入、真实麦克风 shadow-test report schema；不再把新 readiness/report-only 文档算作主线进展。
- 最新真实麦克风验收 schema 结论：DRV-033 已新增 `tools/real_mic_shadow_test_report_schema.py` 和 `tests/test_real_mic_shadow_test_report_schema.py`，固定真实 shadow test 必须导出的 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、feedback labels、privacy/cost flags、audio retention/delete status 和 Go/Pivot/Stop 决策；该工具只校验 schema，不访问麦克风、不读取真实录音、不调用远程 ASR/LLM。
- 最新 mic adapter no-op UI invocation 结论：PCWEB-109 已把 PCWEB-105 合同 readiness 和 PCWEB-107 七个 no-op IPC command 的 invocation status 合并展示到 Web 工作台；普通浏览器显示 `mic_adapter_browser_fallback` / `not_invoked`，Tauri WebView 才允许调用 no-op IPC。该阶段仍不请求权限、不枚举设备、不采集或写入音频、不运行 Cargo/Tauri。
- 最新 short local simulated input timeline 结论：PCWEB-110 已把 M5 收束为 replay report；`tools/asr_live_pipeline_replay.py` 现在输出 `short_local_simulated_input_status`、ASR metrics、EvidenceSpan timeline、state timeline 和 candidate/card timeline。工程 mock sample 可闭合到 `closed_to_candidate_timeline`，非工程 control 保持 0 state / 0 candidate；本阶段不生成正式 suggestion card、不产生真实 feedback、不执行 export、不读取 `.m4a` 或真实音频、不访问麦克风、不调用远程 ASR/LLM。
- 最新 ASR event provenance 结论：PCWEB-111 已让 `tools/asr_live_pipeline_replay.py` 支持可选 `--event-manifest-path`；`asr_event_provenance.v1` manifest 必须位于 `artifacts/tmp/asr_events`，绑定 replay events path，声明 `input_source_kind`，并保持 no-LLM/no-remote/no-mic/no-user-audio/no-public-download flags 全 false。合法 manifest 可把 report 标成 `public_audio_sample` 等来源；blocked manifest 不读取 events、不生成 timeline。复审后已加固 provenance id fields，`/Users/...`、`configs/local/...`、`data/asr_eval/local_samples/...`、反斜杠路径和 `.m4a` 等路径文本会在读取 events 前阻断，当前 replay gate 为 `13 passed, 1 warning`。
- 最新 public audio post-extraction evidence 结论：DRV-034 已新增 `tools/public_audio_post_extraction_evidence_schema.py` 和 `tests/test_public_audio_post_extraction_evidence_schema.py`，只验证未来人工批准抽样后的 evidence JSON，包括 planned sample、source attribution、archive member、clip window、expected/observed sha256、observed duration、sample rate、channel count、license citation 和 cleanup status；不下载、不解压、不转码、不读取音频、不运行 ASR、不调用远程 ASR/LLM，focused gate 为 `10 passed, 1 warning`。
- 最新 replay shadow report draft 结论：DRV-035 已新增 `tools/replay_shadow_report_draft_adapter.py` 和 `tests/test_replay_shadow_report_draft_adapter.py`，把 PCWEB-110/111 replay timeline 映射成 DRV-033 `real_mic_shadow_test_report.v1` 草稿；草稿包含 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、0 feedback、`inconclusive_requires_more_shadow_tests` 和 `audio_chunk_write_status=not_written`。复审后已要求输入 replay 的远程 ASR、真实音频、`configs/local`、麦克风和 LLM safety flags 全部明确为 false，且 `validation_errors` 为空，避免把危险来源洗成干净草稿；focused gate 为 `6 passed, 1 warning`。这只证明模拟 replay 可对齐未来真实验收报告结构，不代表真实麦克风会议已经发生。
- 最新 shadow report ingestion/export/feedback 结论：DRV-036 已新增 `tools/shadow_report_ingestion_export_feedback.py` 和 `tests/test_shadow_report_ingestion_export_feedback.py`，可 ingest DRV-033 candidate report 或 DRV-035 adapter report，输出 schema validation、timeline counts、feedback analysis、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview。Replay draft 只会得到 `draft_export_preview_only` / `feedback_required_before_decision`，不能作为 Go 证据；真实反馈 report 才能 `ready_for_shadow_test_export`。Focused gate 为 `6 passed, 1 warning`，该阶段不访问麦克风、不读取真实音频、不写真实导出文件、不调用远程 ASR/LLM。
- 最新 shadow report export file writer 结论：DRV-037 已新增 `tools/shadow_report_export_file_writer.py` 和 `tests/test_shadow_report_export_file_writer.py`，把 DRV-036 JSON/Markdown preview 写入 ignored `artifacts/tmp/shadow_report_exports`，并记录 path、sha256 和 byte count。Replay draft 可写预览但仍标记 `not_go_evidence_replay_or_feedback_missing`；真实反馈 report 才能标记 `go_evidence_supported_by_real_feedback_report`。Focused gate 为 `7 passed, 1 warning`，该阶段不访问麦克风、不读取真实音频、不写 audio chunk、不写仓库可提交导出文件、不调用远程 ASR/LLM。
- 最新 shadow report feedback ingestion API/UI 结论：DRV-038 已新增 `tools/shadow_report_feedback_ingestion.py` 和 `tests/test_shadow_report_feedback_ingestion.py`，Web backend 新增 `POST /shadow-reports/feedback-ingestions`，工作台新增 `shadow-report-feedback-panel`。真实 audio-written report 在 positive>=2 且 negative<=1 时可更新为 Go readiness；replay draft 即使有正反馈也保持 `not_go_evidence_replay_or_feedback_missing`。Focused 合并 gate 为 `11 passed, 2 warnings`，该阶段不访问麦克风、不读取真实音频、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM。
- 最新 shadow-test pilot bundle runner 结论：DRV-039 已新增 `tools/shadow_test_pilot_bundle_runner.py` 和 `tests/test_shadow_test_pilot_bundle_runner.py`，把 DRV-038 feedback ingestion 和 DRV-037 ignored export writer 串成一个 pilot bundle。真实 audio-written report 且正反馈达标时输出 `pilot_bundle_written` 和 JSON/Markdown bundle；replay draft 只输出 `pilot_bundle_preview_written_not_go_evidence`。Focused gate 为 `5 passed, 1 warning`，该阶段不访问麦克风、不读取真实音频、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不下载模型或公开音频。
- 最新 worker/mic connector contract 结论：PCWEB-112 已新增 `tools/desktop_worker_mic_connector_contract.py`、`code/desktop_tauri/worker-mic-connector-contract.policy.json` 和 `tests/test_desktop_worker_mic_connector_contract.py`，把 PCWEB-105 `mic_adapter.start` 合同预览和 PCWEB-099 `worker.prepare(source_kind=mic)` 审批阻塞合并成同一 session 的组合门禁。Focused gate 为 `7 passed, 1 warning`；该阶段不访问麦克风、不请求权限、不启动 worker、不读写 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- 最新 Tauri no-op run result intake 结论：PCWEB-113 已新增 `tools/desktop_tauri_noop_run_result_intake.py`、`code/desktop_tauri/tauri-noop-run-result-intake.policy.json` 和 `tests/test_desktop_tauri_noop_run_result_intake.py`，为未来显式批准的真实 Tauri WebView no-op run 增加结构化结果验收入口。合法结果必须覆盖 PCWEB-107 的 10 个 no-op IPC command，且每个返回都保持 `noop_bound/noop_only/tauri_ipc_bound` 和 no-side-effect flags；通过后只进入 `ready_for_worker_mic_source_approval_review`，不自动批准 worker/mic source。Focused gate 为 `7 passed, 1 warning`；该阶段不运行 Cargo/Tauri、不访问麦克风、不启动 worker、不读写 audio chunk/event file、不调用远程 ASR/LLM。
- 最新 worker mic source approval packet 结论：PCWEB-114 已新增 `tools/desktop_worker_mic_source_approval.py`、`code/desktop_tauri/worker-mic-source-approval.policy.json` 和 `tests/test_desktop_worker_mic_source_approval.py`，把 PCWEB-112 connector request 与 PCWEB-113 Tauri no-op result 合成同一 session 的人工审批包，并在读取前阻断 forbidden `policy_path`。合法输入只输出 `ready_for_manual_review_not_executable` / `not_approved`，下一步为 `manual_approve_worker_prepare_source_kind_mic_or_keep_blocked`；Focused gate 为 `8 passed, 1 warning`。该阶段不批准真实 worker/mic source、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk/event file、不运行 Cargo/Tauri、不调用远程 ASR/LLM。
- 最新 real mic shadow-test readiness gate 结论：PCWEB-115 已新增 `tools/real_mic_shadow_test_readiness_gate.py`、`code/desktop_tauri/real-mic-shadow-test-readiness.policy.json` 和 `tests/test_real_mic_shadow_test_readiness_gate.py`。默认输出 `blocked_not_ready_for_user_real_mic_shadow_test`；PCWEB-119/120/121/122/123 已分别补齐真实 Tauri no-op evidence、同 session review packet、mic adapter boundary、ASR worker mic source boundary 和单次 worker mic source approval evidence 机制。PCWEB-115 现在能消费 DRV-032 的 `degraded_pilot_accepted_with_quality_risk`，但只把它当作单次试点风险接受，不当作 ASR 质量 Go 证据。PCWEB-115 CLI 也已支持从 inline JSON 或 approved `artifacts/tmp/**` JSON 证据文件加载 ASR quality、Tauri no-op、worker approval、mic adapter、ASR worker 和 export/feedback evidence，并在读取前阻断 `configs/local`、真实/样本音频、`data/local_runtime`、`outputs`、仓库外路径和非 JSON。该工具只做静态前置判断，不访问麦克风、不读取真实音频、不运行 Cargo/Tauri、不启动 worker、不调用远程 ASR/LLM。
- 最新 real mic shadow-test readiness API/UI 结论：PCWEB-124 已新增 `GET /desktop/real-mic-shadow-test-readiness` 和 Web `desktop-real-mic-shadow-readiness-panel`。工作台现在能直接展示 PCWEB-115 默认 readiness、blockers、ASR quality exit、Tauri/worker/mic/export 状态、pilot protocol 和 all-false safety flags；focused gate 为 `5 passed, 2 warnings`。这只是可见化已有 blocker，不读取证据文件、不创建真实存储、不访问麦克风、不启动 worker、不调用远程 provider。
- 最新 simulated shadow pipeline smoke 结论：DRV-041 已新增 `tools/simulated_shadow_pipeline_smoke.py` 和 `tests/test_simulated_shadow_pipeline_smoke.py`，把 approved ASR event JSON 纯内存串到 `ASR replay -> DRV-035 shadow draft -> DRV-036 export preview`。工程 mock events 可输出 `simulated_shadow_pipeline_preview_created` 和 `draft_export_preview_only`，非工程 control 返回 `blocked_by_no_candidate_timeline` 且不伪造卡片；focused gate 为 `5 passed, 1 warning`。这只证明模拟转写事件能闭合到产品价值 preview，不代表 ASR quality 达标、不访问麦克风、不读取真实音频、不下载公开音频或模型、不调用远程 ASR/LLM。
- 最新 simulated shadow pipeline batch 结论：DRV-042 已把 DRV-041 扩展成默认 5 场景批量 smoke，CLI `--batch-default-mock-events` 要求 4 个工程 mock 场景全部 preview、非工程 control 保持 `blocked_by_no_candidate_timeline`。Focused gate 更新为 `9 passed, 1 warning`；该 batch 仍输出 `not_go_evidence_batch_replay_or_feedback_missing`，不写文件、不访问麦克风、不调用远程 provider。
- 最新 ASR quality gate / DRV-042 联动结论：DRV-032 现在会消费 DRV-042 batch status。若 batch 失败，先返回 `fix_simulated_shadow_pipeline_first`；当前 batch passed 后，默认仍正确停在 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。本轮相关门禁为 `56 passed, 1 warning`，模拟 batch CLI exit 0，ASR quality CLI exit 1；这说明产品 mock 链路已过，不代表真实 ASR 质量或真实麦克风 ready。
- 最新 FunASR local readiness evidence 结论：DRV-043 已让 `tools/funasr_synthetic_smoke_readiness.py` 支持显式 `--model-cache-root`，只预检必需模型文件且不回显本机绝对路径；`tools/asr_quality_decision_gate.py` 支持 `--funasr-readiness-path` 从 approved `artifacts/tmp/**` JSON 读取预检 evidence。若 evidence ready，DRV-032 只推进到 `funasr_cache_preflight_ready_requires_execution_approval`，仍不运行 ASR、不下载模型、不访问麦克风。
- 最新 FunASR smoke result evidence 结论：DRV-044 已新增 `tools/funasr_synthetic_smoke_result_evidence.py` 和 `tests/test_funasr_synthetic_smoke_result_evidence.py`，并在 DEC-177 加固 batch artifact provenance/hash gate。该工具只验证 caller-provided `funasr_synthetic_smoke_result.v1` JSON，不运行 ASR、不读音频、不下载模型；单场景通过只算 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`，5 场景 batch confirmation 必须绑定 approved JSON artifact path 和 sha256，并输出 `batch_artifact_provenance_status=validated` 后，才能让 DRV-032 输出 `asr_quality_current_gate_not_blocking`。Focused gate 为 `27 passed, 1 warning`，集成 gate 为 `81 passed, 1 warning`。
- 最新 FunASR batch evidence assembly 结论：DRV-046 已新增 `tools/funasr_synthetic_smoke_batch_evidence_assembler.py`，只消费 DRV-045 packet 和 approved `artifacts/tmp/asr_reports/**.json` smoke reports，计算 sha256、组装 `batch_synthetic_confirmation` 并调用 DRV-044 gate；默认没有真实 smoke artifacts 时 blocked，不运行 ASR、不读取音频、不写 artifacts、不下载模型、不调用远程 provider。
- 最新 Tauri no-op run result collector 结论：PCWEB-116 已在 Web 工作台 `desktop-mic-adapter-contract-panel` 中新增 10-command result collector。普通浏览器显示 `collector_browser_fallback`、`desktop_tauri_noop_run_result.v1`、10 个 `not_invoked` row 和 `real_tauri_noop_result_ready=false`；未来 Tauri WebView 中会通过 `window.__TAURI__` 调 10 个 no-op IPC，并把结果放到 `window.__meetingCopilotTauriNoopRunResult`。Focused static asset gate 为 `1 passed, 2 warnings`，browser smoke 为 `status=ok`。该阶段不运行 Cargo/Tauri、不访问麦克风、不启动 worker、不写本地文件、不调用远程 ASR/LLM。
- 最新 Tauri no-op run result validation API/UI 结论：PCWEB-117 已新增 `POST /desktop/tauri-noop-run-results/validations`，把 PCWEB-116 collector result 交给 PCWEB-113 validator；工作台普通浏览器显示 `validation_browser_fallback` / `pcweb_117_validation_status=not_submitted`，未来 Tauri WebView 中 10 个 no-op command 全部 returned 后才自动提交 validation。Focused gate 为 `4 passed, 2 warnings`，browser smoke 为 `status=ok`。该阶段不运行 Cargo/Tauri、不访问麦克风、不启动 worker、不写本地 result 文件、不调用远程 ASR/LLM。
- 最新音频来源与模拟分工复审结论：官方网页复核和两个只读审查 Agent 一致确认，完整计划已经写下，不需要继续泛搜音频；AliMeeting/OpenSLR SLR119 与 AISHELL-4/OpenSLR SLR111 只作为 no-download 会议声学来源，AISHELL-1/OpenSLR SLR33 只做普通话 sanity，FunASR 仍需本地模型目录或 DRV-019 审批。真实麦克风会议仍由用户最终执行。
- 最新 first controlled Tauri `cargo check` 结论：PCWEB-118 已修复两类编译阻塞：`generate_context!()` 需要默认 `src-tauri/icons/icon.png`，以及 `#[tauri::command] pub fn` 会触发 Tauri helper macro duplicate reexport；当前 10 个 no-op command 改为 private `fn`，新增最小 PNG icon，受控 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 通过。该阶段只证明 Rust crate 可编译，不代表 Tauri 窗口/WebView 已运行、不代表真实 no-op IPC result 已产生、不访问麦克风、不启动 worker、不调用远程 ASR/LLM。
- 最新 real Tauri no-op WebView IPC evidence 结论：PCWEB-119 已真实启动本地 capture server 和 Tauri WebView，Web 工作台通过 `window.__TAURI__` 调用 10 个 no-op IPC command，并自动 `POST /desktop/tauri-noop-run-results/validations`；验证返回 passed，ignored evidence 写入 `artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json`，摘要为 `captured_validated_tauri_noop_run`、`validated_command_count=10`、`returned_command_count=10`、`ready_for_worker_mic_source_approval_review`。该阶段只证明 Tauri WebView/no-op IPC/validation capture 已运行，不代表麦克风、worker、真实 mic adapter 或 ASR 质量已完成。
- 最新 worker mic source from real Tauri evidence 结论：PCWEB-120 已新增 `tools/desktop_worker_mic_source_from_tauri_evidence.py`，读取 PCWEB-119 ignored evidence，校验 capture/validation/run_result 后派生同 session PCWEB-112 connector request，并复用 PCWEB-114 生成 `ready_for_manual_review_not_executable` packet；真实 evidence CLI run 输出 `worker_mic_source_approval_status=not_approved`、`safe_to_capture_audio_now=false`、`safe_to_start_worker_now=false`。该阶段不批准 worker mic source，不访问麦克风，不启动 worker。
- 最新 minimal mic adapter implementation boundary 结论：PCWEB-121 已新增 `code/desktop_tauri/src-tauri/src/mic_adapter_runtime.rs`、`tools/desktop_mic_adapter_implementation_boundary.py`、`code/desktop_tauri/mic-adapter-implementation-boundary.policy.json` 和 `tests/test_desktop_mic_adapter_implementation_boundary.py`。该 evidence 能让 readiness gate 的 `mic_adapter_ready` 变为 true 并移除 `mic_adapter_real_implementation_not_available` blocker；但它仍不请求权限、不访问麦克风、不写 audio chunk、不启动 worker、不调用远程 ASR/LLM。
- 最新 ASR worker real mic source boundary 结论：PCWEB-122 已新增 `code/desktop_tauri/src-tauri/src/asr_worker_mic_source_runtime.rs`、`tools/desktop_asr_worker_real_mic_source_boundary.py`、`code/desktop_tauri/asr-worker-real-mic-source-boundary.policy.json` 和 `tests/test_desktop_asr_worker_real_mic_source_boundary.py`。该 evidence 能让 readiness gate 的 `asr_worker_ready` 变为 true 并移除 `asr_worker_real_mic_source_not_available` blocker；但它仍不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 event/audio 文件、不访问麦克风、不调用远程 ASR/LLM。
- 最新 worker mic source 单次批准机制结论：PCWEB-123 已新增 `tools/desktop_worker_mic_source_single_shadow_approval.py`、`code/desktop_tauri/worker-mic-source-single-shadow-approval.policy.json` 和 `tests/test_desktop_worker_mic_source_single_shadow_approval.py`。合法 approval evidence 能让 readiness gate 的 `worker_mic_source_ready` 变为 true 并移除 `worker_mic_source_not_approved` blocker；默认无 approval record 时仍是 blocked/not approved，且任何情况下都不启动 worker、不采集音频。
- 最新执行锁结论：转写验证先由我通过官方公开来源审查、no-download manifest、自建中文技术会议合成音频、mock streaming events 和 approved synthetic event replay 完成；用户最终再做真实麦克风会议 shadow test。当前不是缺计划，缺的是执行态：ASR quality 默认仍卡在 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`，公开音频仍卡在 `blocked_no_verified_public_sample_manifest`；PCWEB-121/122/123 只补齐 mic adapter、ASR worker mic source 和 worker mic source approval evidence 机制，不代表真实采集或真实 worker 已可开始。下一步不再写泛化评测计划，只能按 DRV-032 四个 quality exit 出口推进；显式降级试点可以结束评估循环进入一次用户手动 shadow-test 前置判断，但不能宣称 ASR 质量达标。
- 如果 ASR 不达标，桌面 MVP 应降级为会后结构化纪要，不做强实时 Copilot。

## 重点文档

- `docs/current-mainline-index.md`：当前主线短索引，集中列出主线结论、禁止事项、6 个收敛里程碑和验证命令。
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`：完整未来计划总控入口，集中回答公开音频、模拟转写、ASR handoff、桌面 runtime 和最终真实麦克风验收。
- `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`：计划确认、责任边界、多 Agent 审查结论和下一步锁定。
- `docs/product-requirements.md`：产品需求主文档。
- `docs/project-current-status-and-forward-plan-2026-07-03.md`：当前进展、已得结论、公开音频模拟边界和未来开发计划。
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`：当前主线执行计划，明确产品价值 tri-lane gate、公开音频 no-download 边界、桌面 runtime、worker handoff 和用户真实麦克风 shadow test。
- `docs/copilot-product-value-gate.md`：DRV-025 tri-lane product value gate，使用 perfect transcript / mock ASR / real ASR 三路拆分产品逻辑、streaming contract 和 ASR 质量瓶颈。
- `docs/copilot-product-value-batch-result-2026-07-03.md`：DRV-026 五场景 batch summary，汇总 API、架构、事故、发布和非工程负控的 tri-lane 结果。
- `docs/asr-quality-decision-gate-2026-07-03.md`：DRV-032 ASR quality decision gate，把当前 ASR 阻塞收束为本地 FunASR 模型目录、DRV-019 审批、可选远端对照或显式降级试点四个出口。
- `docs/pcweb-110-short-local-simulated-input-timeline-report-plan.md`：PCWEB-110 M5 短时本地模拟输入 timeline report，证明 approved synthetic/mock event file 可闭合到 evidence/state/candidate timeline，但不创建正式卡片、不产生真实 feedback/export。
- `docs/pcweb-111-asr-event-provenance-manifest-plan.md`：PCWEB-111 ASR event provenance manifest，证明 replay report 可审计事件来源，但不下载公开音频、不运行 ASR、不读取真实音频。
- `docs/pcweb-112-desktop-worker-mic-connector-contract-plan.md`：PCWEB-112 worker/mic connector contract，把 mic adapter start 合同和 worker mic source 审批阻塞合并成同一 session 的 no-side-effect 前置门禁。
- `docs/pcweb-113-desktop-tauri-noop-run-result-intake-plan.md`：PCWEB-113 Tauri no-op run result intake，给未来显式批准的真实 Tauri WebView no-op run 增加机器可验收结果入口。
- `docs/pcweb-114-desktop-worker-mic-source-approval-plan.md`：PCWEB-114 worker mic source approval packet，把 connector request 和 Tauri no-op result 合成同一 session 的人工审批包，但仍不批准执行。
- `docs/pcweb-120-worker-mic-source-from-tauri-evidence-plan.md`：PCWEB-120 从 PCWEB-119 真实 Tauri evidence 派生同 session worker mic source manual review packet，仍不批准执行。
- `docs/pcweb-121-minimal-real-mic-adapter-implementation-boundary-plan.md`：PCWEB-121 最小 mic adapter implementation boundary，提供 readiness gate 可识别的 mic adapter evidence，但不访问麦克风、不写 audio chunk。
- `docs/pcweb-122-asr-worker-real-mic-source-boundary-plan.md`：PCWEB-122 ASR worker real mic source boundary，提供 readiness gate 可识别的 ASR worker evidence，但不启动 worker、不访问麦克风、不读写 event/audio 文件。
- `docs/pcweb-123-worker-mic-source-single-shadow-approval-plan.md`：PCWEB-123 worker mic source 单次 shadow-test approval evidence，提供 readiness gate 可识别的单次批准 evidence，但不启动 worker、不访问麦克风。
- `docs/pcweb-124-real-mic-shadow-test-readiness-api-ui-plan.md`：PCWEB-124 real mic shadow-test readiness API/UI，把 PCWEB-115 默认 go/no-go、blockers、pilot protocol 和 safety flags 展示到 Web 工作台。
- `docs/drv-041-simulated-shadow-pipeline-smoke-plan.md`：DRV-041 simulated shadow pipeline smoke runner，把 approved ASR event JSON 纯内存串到 replay、shadow draft 和 export preview。
- `docs/drv-042-simulated-shadow-pipeline-batch-smoke-plan.md`：DRV-042 simulated shadow pipeline batch smoke，把 4 个工程 mock 场景和 1 个非工程负控固定成一键自测门。
- `docs/current-plan-and-validation-report-2026-07-04.md`：当前完整计划与执行验证报告，集中回答公开音频、模拟转写、ASR quality blocker 和用户最终真实麦克风验证边界。
- `docs/drv-043-funasr-local-readiness-evidence-input-plan.md`：DRV-043 FunASR 本地模型预检 evidence 输入，把本地模型目录/cache readiness 接入 ASR quality gate，但不运行 ASR、不下载模型。
- `docs/drv-044-funasr-synthetic-smoke-result-evidence-gate-plan.md`：DRV-044 FunASR synthetic smoke result evidence gate，把未来 smoke result 的 schema、硬阈值、单场景候选和 batch confirmation 出口接入 ASR quality gate。
- `docs/drv-045-funasr-synthetic-smoke-execution-packet-plan.md`：DRV-045 FunASR synthetic smoke execution packet，把 DRV-043 readiness 到 DRV-044 provenance/hash gate 的人工执行交接固定成机器可验收 packet。
- `docs/drv-046-funasr-synthetic-smoke-batch-evidence-assembler-plan.md`：DRV-046 FunASR synthetic smoke batch evidence assembler，把手动 smoke artifacts 装配成 DRV-044 batch evidence 并复用 DRV-044 gate。
- `docs/drv-047-asr-quality-drv046-assembly-intake-plan.md`：DRV-047 ASR quality DRV-046 assembly intake，让 DRV-032 直接消费 DRV-046 assembly report/path，减少人工复制嵌套 DRV-044 gate report。
- `docs/pcweb-115-real-mic-shadow-test-readiness-gate-plan.md`：PCWEB-115 real mic shadow-test readiness gate，把真实麦克风会议是否可开始收束成 blocker 清单和 go/no-go 判断。
- `docs/pcweb-118-desktop-first-controlled-cargo-check-plan.md`：PCWEB-118 第一次受控 Tauri Rust `cargo check` 计划、root cause、修复和边界。
- `docs/pcweb-116-desktop-tauri-noop-run-result-collector-plan.md`：PCWEB-116 Tauri no-op run result collector，让未来真实 Tauri WebView no-op run 可在内存中产出 PCWEB-113 可摄入 result JSON。
- `docs/pcweb-117-desktop-tauri-noop-run-result-validation-api-ui-plan.md`：PCWEB-117 Tauri no-op run result validation API/UI，把 collector result 接到 PCWEB-113 validator。
- `docs/drv-035-replay-shadow-report-draft-adapter-plan.md`：DRV-035 replay -> DRV-033 shadow report draft adapter，证明模拟 replay 可对齐未来真实 shadow-test 报告结构，但不访问麦克风、不读取真实音频、不生成真实用户反馈。
- `docs/drv-036-shadow-report-ingestion-export-feedback-plan.md`：DRV-036 shadow report ingestion/export/feedback readiness，证明 report 可进入反馈统计、Go/Pivot/Stop readiness 和导出预览，但不写真实导出文件、不生成真实用户反馈。
- `docs/drv-037-shadow-report-export-file-writer-plan.md`：DRV-037 shadow report export file writer，把 DRV-036 导出预览写入 ignored artifact root，并保留路径、sha256 和 byte count 审计。
- `docs/drv-038-shadow-report-feedback-ingestion-api-ui-plan.md`：DRV-038 shadow report feedback ingestion API/UI，把用户卡片反馈接入 report、Go/Pivot/Stop readiness 和 Web 工作台。
- `docs/drv-039-shadow-test-pilot-bundle-runner-plan.md`：DRV-039 shadow-test pilot bundle runner，把真实验收反馈写回和 ignored JSON/Markdown 导出打成一个可复跑 bundle。
- `docs/project-stage-status-and-next-work-2026-07-02.md`：当前阶段状态、已完成内容、风险和后续工作快照。
- `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`：纠偏后的 desktop runtime validation 与公开/合成音频模拟计划。
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`：公开授权音频、合成中文技术会议 ASR 和用户真实麦克风验收主计划。
- `docs/project-progress-report-2026-07-02.md`：当前阶段进度、红灯、风险和下一步计划。
- `docs/mac-mvp-requirements-and-technical-plan.md`：Mac 优先 MVP 需求与技术方案。
- `docs/minimum-valuable-demo-script.md`：最小有价值 demo 验收脚本。
- `docs/feature-map.md`：功能地图与阶段划分。
- `docs/meeting-scenarios.md`：中文技术会议场景模板。
- `docs/realtime-suggestion-cards.md`：实时建议卡片规范。
- `docs/meeting-state-model.md`：会议状态模型。
- `docs/chinese-technical-language.md`：中文技术语义与术语规范。
- `docs/asr-provider-strategy.md`：ASR provider 策略、成本边界和实现路线。
- `docs/asr-bakeoff-guide.md`：ASR bake-off 使用说明。
- `docs/asr-evaluation-dataset.md`：ASR 评测集规范。
- `docs/llm-quality-evaluation.md`：LLM 质量评测。
- `docs/privacy-and-data-flow.md`：隐私、安全与数据流。
- `docs/failure-and-degradation.md`：失败与降级策略。
- `docs/project-structure.md`：工作区结构说明。
- `docs/local-run-notes.md`：本机配置和自测记录，不包含完整密钥。
- `docs/implementation-roadmap.md`：SDD/TDD 实施路线图。
- `docs/requirements-traceability-matrix.md`：需求 ID、验收和测试追踪矩阵。

## 运行测试

推荐入口：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

`pc-web` profile 会运行当前 PC Web MVP 的本地免费质量门禁：

- 根目录 `root-pytest`，包含 PCWEB-082 root scaffold contract tests。
- `code/core` 单元测试。
- `code/web_mvp/backend` API/fixture/repository 测试。
- `code/web_mvp/e2e/browser_smoke.mjs` 真实 Chrome/CDP 浏览器 smoke。

如需把本地 ASR runtime 和 bake-off 工具测试也纳入门禁：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile all-local
```

以上两个 profile 都不会调用 LLM 中转站、远程 ASR 或 `configs/local/`。LLM smoke 和真实 provider 质量评测仍按专项文档手动运行，避免默认验证产生额外费用。

PCWEB-082 的 `root scaffold contract tests` 只做静态合同检查：`code/desktop_tauri/src-tauri/tauri.conf.json` 指向现有 Web MVP，`runtime_get_status`、`session_prepare`、`asr_worker_health` 只返回 `noop_bound` 响应且 `safe_to_execute_real_action=false`。PCWEB-118 后 `src-tauri/Cargo.lock` 已成为桌面 app 的可提交 reproducibility artifact，不再按 generated blocker 处理；这些测试仍禁止 `package.json`、npm/pnpm/yarn lock files、`node_modules`、`target`、installer/signing/notarization artifacts，也不会运行 `cargo build`、`cargo tauri dev`、`npm install` 或真实 Tauri shell。

PCWEB-083 增加桌面 build readiness policy：`code/desktop_tauri/build-readiness.policy.json` 和 `tools/desktop_build_readiness.py`。默认报告只读 policy，不执行外部命令；显式 probe 模式也仅是 `toolchain_version_probe_only`，只允许 `rustc --version` 和 `cargo --version`，且工具层硬编码执行白名单，custom policy 不能扩大探针执行范围；同时保持 `safe_to_run_cargo_check_now=false`、不运行 Tauri dev/build、不安装依赖、不生成 lock/build artifacts、不接音频/worker/密钥/远程 provider。

PCWEB-084 增加桌面 cargo check artifact policy：`code/desktop_tauri/cargo-check.policy.json` 和 `tools/desktop_cargo_check_policy.py`。默认报告是 `cargo_check_policy_static_report`，只读 policy 和 artifact path existence，不运行 Cargo/Tauri/package manager；PCWEB-118 已完成第一次受控 `cargo check`，因此 `code/desktop_tauri/src-tauri/Cargo.lock` 现在应保留，target 放在 ignored `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`，重复检查优先使用 `--locked --offline`。该 policy tool 自身仍保持 `safe_to_run_cargo_check_now=false`，不安装 Rust、不执行 Cargo、不接音频/worker/密钥/远程 provider。

PCWEB-085 增加桌面 Rust toolchain readiness policy：`code/desktop_tauri/rust-toolchain-readiness.policy.json` 和 `tools/desktop_rust_toolchain_readiness.py`。默认报告不执行命令；显式 `local_version_and_platform_probe_only` 只允许 `rustc --version`、`cargo --version`、`rustup --version` 和 `xcode-select -p`，其中 `xcode-select -p` 只返回 present/missing，不回显本机路径。当前保持 `safe_to_install_toolchain_now=false`、`safe_to_run_cargo_check_now=false`，不安装 Rust、不改 shell profile、不运行 cargo/Tauri/package manager、不生成 artifacts、不接音频/worker/密钥/远程 provider。

PCWEB-086 增加桌面 Rust toolchain installation decision policy：`code/desktop_tauri/rust-toolchain-installation.policy.json` 和 `tools/desktop_rust_toolchain_installation_decision.py`。默认报告是 `no_install_decision_report_only`，只记录官方 `official_rustup` 推荐、显式批准 tokens、macOS/Windows/Linux platform notes 和 future post-install verification order；当前保持 `safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false` 和 `safe_to_run_cargo_check_now=false`，不运行 curl/sh/rustup/cargo/package manager、不改 shell profile、不联网抓依赖、不生成 artifacts、不接音频/worker/密钥/远程 provider。

PCWEB-087 增加桌面 Rust toolchain install approval packet：`code/desktop_tauri/rust-toolchain-install-approval.policy.json` 和 `tools/desktop_rust_toolchain_install_approval_packet.py`。默认报告是 `manual_user_run_only`，只把官方 Rust/rustup/Tauri 来源、macOS/Linux 手动命令文本、Windows `rustup-init.exe` 手动说明、approval tokens、risk notes、rollback notes 和 post-install verification order 渲染为人审审批包；当前保持 `safe_to_execute_install_now=false`、`safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false` 和 `safe_to_run_cargo_check_now=false`，不执行命令文本、不运行安装器/package manager/cargo/Tauri、不改 shell profile/PATH、不抓依赖、不生成 artifacts、不接音频/worker/密钥/远程 provider。

PCWEB-088 增加桌面 Rust post-install probe approval packet：`code/desktop_tauri/rust-post-install-probe-approval.policy.json` 和 `tools/desktop_rust_post_install_probe_approval.py`。默认报告是 `no_probe_execution_approval_packet_only`，只记录未来人工安装 Rust 后可申请的只读 probe allowlist（`rustc --version`、`cargo --version`、`rustup --version`、macOS `xcode-select -p`）、脱敏规则、approval tokens、expected result schema 和 cargo-check blockers；当前保持 `safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false` 和 `safe_to_generate_target_dir_now=false`，不运行 probe、不读 PATH/shell profile/cargo home/rustup home、不运行 cargo/Tauri、不生成 artifacts、不接音频/worker/密钥/远程 provider。

PCWEB-089 增加桌面 Rust post-install probe result intake：`code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` 和 `tools/desktop_rust_post_install_probe_result_intake.py`。默认报告是 `manual_result_validation_only` 和 `caller_provided_json_only`，只校验调用方提供的 bounded JSON status，拒绝 raw stdout/stderr、command、path、env、cargo/rustup home、provider config、api_key、authorization 或 bearer token 字段；当前保持 `safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false` 和 `safe_to_run_cargo_check_now=false`，并固定 `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`，不运行 rustc/cargo/rustup/xcode-select/cargo check/Tauri/package manager、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。

PCWEB-090 增加桌面 first cargo check execution boundary：`code/desktop_tauri/first-cargo-check-execution.policy.json` 和 `tools/desktop_first_cargo_check_execution_boundary.py`。默认报告是 `explicit_manual_execution_packet_only`，把 PCWEB-084 的 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`、`CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target` 和 artifact policy 与 PCWEB-089 的 bounded toolchain result 合并成手动执行包，满足前置条件时只返回 `execution_packet_status=ready_for_explicit_user_approval`；PCWEB-118 已完成这条受控执行路径的一次实际 `cargo check`，但 PCWEB-090 工具本身仍只生成 packet，不执行命令、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。

PCWEB-091 增加桌面 Tauri no-op shell local run smoke readiness boundary：`code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。默认报告是 `readiness_report_only`，只静态验证 PCWEB-082 Tauri scaffold、`devUrl=http://127.0.0.1:8765/`、`frontendDist`、minimal capability、10 个 no-op command catalog、generated artifact blockers 和 PCWEB-090 boundary；PCWEB-118 后 `Cargo.lock` 已从 generated blocker 中移除并成为预期文件。该 smoke tool 仍只返回 `smoke_packet_status=ready_for_explicit_tauri_run_approval`，保持 `safe_to_run_tauri_dev_now=false`，不运行 Tauri/package manager、不生成 source-tree target/installer、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。

PCWEB-095 增加 desktop-side ASR worker handoff preflight：`code/desktop_tauri/asr-worker-handoff-preflight.policy.json` 和 `tools/desktop_asr_worker_handoff_preflight.py`。它只校验 caller-provided worker descriptor，包括 `session_id`、`provider`、`event_file_path`、`source_kind` 和 `chunk_lifecycle`，并生成 future `/live/asr/local-event-files/sessions` request preview；当前只允许 `source_kind=preflight_only|synthetic`，`mic|file` 需要后续审批；event file path 只允许 `artifacts/tmp/asr_events`。该工具仍不启动 worker、不访问麦克风、不读写 event file、不写 runtime audio、不调用 Web API、不调用远程 ASR/LLM、不下载模型。

PCWEB-096 增加 desktop ASR worker handoff local dry-run bridge：`code/desktop_tauri/asr-worker-handoff-local-dry-run.policy.json` 和 `tools/desktop_asr_worker_handoff_local_dry_run.py`。默认 `preview_only` 只复用 PCWEB-095 生成 Web handoff request preview，不读取 event file、不调用 Web API、不写 session；显式 `synthetic_local_test` 只允许读取 `artifacts/tmp/asr_events` 下的 synthetic event file，并用 FastAPI TestClient 写入 `artifacts/tmp/desktop_handoff_dry_run` 下的临时 Web data dir，验证 `/live/asr/local-event-files/sessions` 合同。该工具仍不启动 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型。

PCWEB-097 增加 desktop ASR worker handoff dry-run readiness UI/API：Web backend 新增 `GET /desktop/asr-worker-handoff-dry-run-readiness`，Web 工作台新增 `desktop-asr-handoff-dry-run-panel`，只展示 PCWEB-096 `preview_only`/显式 `synthetic_local_test` readiness、allowed roots、blockers、next decisions 和 false safety flags。该面板仍不启动 worker、不读取 event file、不调用 Web handoff mutation API、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

以下 PCWEB-098/099/100/101/102/103/104/105 是已完成的历史增量记录；当前下一步以 `docs/current-mainline-index.md` 和 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 为准，不再回到 PCWEB-098、PCWEB-101、PCWEB-104 或 PCWEB-105 的旧 next pointer。

PCWEB-098 已实现 desktop ASR worker process contract：新增 `code/desktop_tauri/asr-worker-process-contract.policy.json`、`tools/desktop_asr_worker_process_contract.py` 和 `tests/test_desktop_asr_worker_process_contract.py`，定义 future worker lifecycle、command catalog、resource limits、event output contract、approved roots 和 no-spawn/no-audio/no-remote safety flags；同时把 PCWEB-097 readiness endpoint 的下一步指针改为 `next_pcweb_id=PCWEB-098`。该阶段仍不启动 worker、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

PCWEB-099 已实现 desktop ASR worker command protocol：新增 `code/desktop_tauri/asr-worker-command-protocol.policy.json`、`tools/desktop_asr_worker_command_protocol.py` 和 `tests/test_desktop_asr_worker_command_protocol.py`，定义 future `worker.prepare/start/stop/health/collect_events/cleanup` 的 request/response envelope、lifecycle transition preview、blocked response、approved roots 和 no-execution/no-audio/no-remote safety flags；同时把 PCWEB-097 readiness endpoint 的下一步指针改为 `next_pcweb_id=PCWEB-099`。该阶段仍不启动 worker、不访问麦克风、不读写 event file、不写 runtime audio、不调用 Web mutation、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

PCWEB-100 已实现 desktop ASR worker synthetic lifecycle harness：新增 `code/desktop_tauri/asr-worker-synthetic-lifecycle.policy.json`、`tools/desktop_asr_worker_synthetic_lifecycle.py` 和 `tests/test_desktop_asr_worker_synthetic_lifecycle.py`，用 PCWEB-099 command requests 跑 `prepare -> start -> collect_events -> stop -> cleanup`，并在 `collect_events` 阶段复用 PCWEB-096 `synthetic_local_test` 把 approved synthetic event file 导入临时 Web data dir。该阶段仍不启动真实 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri、不写 runtime audio、不写 event file、不 mutate production Web session。

PCWEB-101 已实现 desktop ASR worker implementation approval packet：新增 `code/desktop_tauri/asr-worker-implementation-approval.policy.json`、`tools/desktop_asr_worker_implementation_approval.py` 和 `tests/test_desktop_asr_worker_implementation_approval.py`，把未来真实 worker implementation 的 entrypoint、command runner、event output root、runtime root、资源预算、provider/source mode 和 approval tokens 固定为人工 review packet。即使 packet 校验通过，也只返回 `ready_for_manual_review_not_executable`，继续保持 `approved_to_implement_now=false`、`approved_to_execute_now=false`，不实现/启动 worker、不访问麦克风、不读写 event file、不下载模型、不运行 Cargo/Tauri、不调用远程 ASR/LLM。

PCWEB-102 已实现 desktop ASR worker no-execution skeleton：新增 `code/asr_runtime/scripts/asr_worker_sidecar.py`、`code/desktop_tauri/asr-worker-no-execution-skeleton.policy.json`、`tools/desktop_asr_worker_no_execution_skeleton.py` 和 `tests/test_desktop_asr_worker_no_execution_skeleton.py`，把 PCWEB-101 的 approval packet 推进到可导入的 sidecar module boundary。它只返回 worker identity、command envelope、lifecycle state、event writer、provider adapter、health/status 和 cleanup plan preview；`mic|file|system_audio`、`remote_asr`、`remote_llm_asr` 和 `funasr_streaming` 未审批路径都会 blocked。该阶段仍不启动 worker、不访问麦克风、不读写 event file、不写 runtime audio、不下载模型、不运行 Cargo/Tauri、不调用远程 ASR/LLM。

PCWEB-103 已实现 desktop ASR worker command runner binding preview：新增 `code/desktop_tauri/asr-worker-command-runner-binding.policy.json`、`tools/desktop_asr_worker_command_runner_binding.py`、`tests/test_desktop_asr_worker_command_runner_binding.py` 和 `docs/pcweb-103-desktop-asr-worker-command-runner-binding-plan.md`，把 PCWEB-102 sidecar module boundary 推进到 future native command runner path 的静态绑定预览。合法 binding request 也只返回 `ready_for_no_execution_binding_review` 和 `validated_not_bound`，继续保持 command dispatch、Tauri IPC、subprocess、health probe、event collection、worker execution、event file IO、音频、模型、远程 ASR/LLM 和 Cargo/Tauri flags 全部 false。

PCWEB-104 已实现 desktop ASR worker command runner implementation skeleton / no-dispatch boundary：新增 `code/desktop_tauri/asr-worker-command-runner-implementation-skeleton.policy.json`、`tools/desktop_asr_worker_command_runner_implementation_skeleton.py`、`tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py`、`code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs` 和 `docs/pcweb-104-desktop-asr-worker-command-runner-implementation-skeleton-plan.md`。Rust skeleton 只定义 inert preview structs/functions，未在 `lib.rs` 中 `mod` 或 `generate_handler!` 绑定；合法 skeleton request 也只返回 `ready_for_no_dispatch_skeleton_review`、`skeleton_source_validated_not_bound` 和 blocked command preview，继续保持 command accept/dispatch、Tauri IPC、subprocess/process spawn、event file IO、音频、模型、远程 ASR/LLM 和 Cargo/Tauri flags 全部 false。Web readiness endpoint 当前指向 `next_pcweb_id=PCWEB-104` 只是 no-dispatch boundary 状态标记，不代表 PCWEB-104 仍是未来任务；下一张执行票只能从公开音频 planned samples、ASR quality decision、真实 Tauri no-op run 或 mic adapter contract 中选择。

PCWEB-105 已实现 desktop microphone adapter contract：新增 `code/desktop_tauri/mic-adapter-contract.policy.json`、`tools/desktop_mic_adapter_contract.py`、`tests/test_desktop_mic_adapter_contract.py` 和 `docs/pcweb-105-desktop-mic-adapter-contract-plan.md`。它只定义 `mic_adapter.prepare/status/start/pause/resume/stop/delete_audio_chunks` 命令合同、显式用户 start 边界、ignored runtime audio root、audio chunk root 和删除语义；`mic_adapter.start` 即使带 `user_consent_state=explicit_user_start_granted` 也只返回 `validated_not_executed`。Web readiness endpoint 已推进到 `next_pcweb_id=PCWEB-105`，继续保持权限请求、音频采集、audio chunk 读写/删除、远程 ASR/LLM、模型下载和 Cargo/Tauri flags 全部 false。

PCWEB-106 已实现 desktop microphone adapter contract readiness UI/API：新增 `GET /desktop/mic-adapter-contract-readiness` 和 `desktop-mic-adapter-contract-panel`，把 PCWEB-105 合同展示到 Web/Tauri no-op 工作台。端点返回 7 个 mic adapter command、approved runtime/audio chunk roots、delete semantics 和完整 all-false safety flags；ASR handoff readiness endpoint 现指向 `next_pcweb_id=PCWEB-106`。该阶段仍不请求权限、不枚举设备、不访问麦克风、不写或删除 audio chunk、不读取真实用户音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

PCWEB-107 已实现 desktop mic adapter no-op Tauri IPC binding：`code/desktop_tauri/src-tauri/src/lib.rs` 静态绑定 10 个 no-op command，其中 7 个是 `mic_adapter.prepare/status/start/pause/resume/stop/delete_audio_chunks`；`tools/desktop_tauri_noop_shell_run_smoke.py` 现在校验 PCWEB-107 catalog、bridge IDs、`generate_handler!`、function mapping 和 no-side-effect response fields。focused tests 结果为 `20 passed, 1 warning`。该阶段仍不运行 Cargo/Tauri、不请求权限、不枚举设备、不访问麦克风、不写或删除 audio chunk、不启动 worker、不调用远程 ASR/LLM。

PCWEB-108 已实现 worker output -> Web Live ASR session closure gate：`tools/desktop_asr_worker_handoff_local_dry_run.py` 的 `synthetic_local_test` 现在必须从临时 Web Live ASR session response 汇总 transcript final、EvidenceSpan、state event、scheduler event、suggestion candidate 和 LLM request draft；技术会议 synthetic input 返回 `closed_to_evidence_state_gap`，非工程 input 即使能写入 transcript 也会因没有 state/gap candidate 返回 `blocked_by_live_session_closure`。该阶段仍不启动真实 worker、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

PCWEB-109 已实现 mic adapter no-op UI invocation：`code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js` 在 `desktop-mic-adapter-contract-panel` 中同时展示 PCWEB-105 合同 readiness 和 PCWEB-107 七个 no-op command 的 invocation status；普通浏览器 fallback 固定为 `mic_adapter_browser_fallback` / `not_invoked`，Tauri WebView 才通过 `window.__TAURI__` 调用 `mic_adapter_prepare/status/start/pause/resume/stop/delete_audio_chunks`。该阶段仍不请求权限、不枚举设备、不访问麦克风、不写或删除 audio chunk、不启动 worker、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

DRV-033 已实现 real mic shadow test report schema：新增 `tools/real_mic_shadow_test_report_schema.py`、`tests/test_real_mic_shadow_test_report_schema.py` 和 `docs/drv-033-real-mic-shadow-test-report-schema-plan.md`。无 candidate report 时输出 schema contract；有 candidate report 时只校验 JSON 结构和安全字段，valid report 返回 `schema_validated_no_audio_access`；复审后已加固 EvidenceSpan/state/card timeline 固定字段、交叉引用、feedback aggregate、`go` 决策最低反馈、audio retention enum 和全 forbidden-root path guard。该阶段仍不访问麦克风、不读取真实用户音频、不写或删除 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

DRV-024 加固公开音频计划工具边界：`tools/public_audio_source_whitelist.py` 和 `tools/public_audio_sample_extraction_plan.py` 现在会在读取 `--source-path` 或 `--planned-samples-file` 前拒绝 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外绝对路径和 symlink 逃逸；没有具体 planned samples 时返回 `blocked_no_planned_samples`，不再显示为可下载评审 ready；planned samples 必须绑定 selected `source_id/source_url/source_license`。该阶段仍不下载公开音频、不抽取、不转码、不调用 ASR/LLM。

DRV-031 完成公开音频 planned sample manifest decision：新增 `tools/public_audio_planned_sample_manifest_decision.py`、`tests/test_public_audio_planned_sample_manifest_decision.py` 和 `docs/public-audio-planned-sample-manifest-decision-2026-07-03.md`。默认只评审 AliMeeting Eval 和 AISHELL-4 test 两个白名单会议声学候选，输出 `blocked_no_verified_public_sample_manifest`，原因是当前没有真实 `archive_member_path`、没有 `expected_sha256_after_extract`、也没有用户批准 GB 级公开包下载；工具仍不下载、不抽取、不转码、不调用 ASR/LLM、不读取真实用户音频或 `configs/local`。如果未来提供合法 planned samples 文件，工具只返回 `schema_validated_no_download` 和 `ready_for_manual_download_review`，仍不生成下载/解压/转码命令。

DRV-032 完成 ASR quality decision gate，并在本轮扩展为 ASR quality exit contract：`tools/asr_quality_decision_gate.py`、`tests/test_asr_quality_decision_gate.py` 和 `docs/asr-quality-decision-gate-2026-07-03.md`。默认组合 product value batch、FunASR synthetic smoke readiness、DRV-019 approval packet 和 DRV-031 public audio decision，输出 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`；同时保持 `safe_to_run_funasr_smoke_now=false`、`safe_to_download_models_now=false`、`safe_to_download_public_audio_now=false`、`safe_to_capture_microphone_now=false`、`safe_to_call_remote_asr_now=false` 和 `safe_to_call_llm_now=false`。合法 `asr_quality_degraded_pilot_acceptance.v1` 可输出 `degraded_pilot_accepted_with_quality_risk` 并让 PCWEB-115 继续检查其它前置条件，但 `counts_as_asr_quality_go_evidence=false`。

DRV-025 增加 Copilot product value tri-lane gate：`tools/copilot_product_value_tri_lane_gate.py` 和 `docs/copilot-product-value-gate.md` 把同一 synthetic scenario 拆成 `perfect_transcript`、`mock_asr`、`real_asr` 三路，统一复用 Web Live ASR builder 生成 EvidenceSpan/state/scheduler/suggestion_candidate/llm_request_draft，并输出 `overall_decision`。当前 API review smoke 显示 perfect 和 mock lane 为 `product_logic_ready`，real ASR lane 因 sherpa normalized technical entity recall 0.5 返回 `blocked_by_asr_quality`；该工具仍不调用 LLM/远程 ASR、不访问麦克风、不读取真实用户音频、不下载模型。

DRV-026 增加 Copilot product value batch gate：`tools/copilot_product_value_batch_gate.py` 把 DRV-025 扩到 5 个 synthetic scripts。DRV-027 前的 batch smoke 暴露了 `architecture-review-001` perfect lane 产品逻辑缺口和 `incident-review-001` mock lane 缺口，因此后续优先修产品逻辑，而不是继续 ASR 横评。

DRV-027 增加 architecture review product logic coverage：`asr_live_events.py` 现在能从 `QPS/缓存穿透/mysql` 识别架构风险，并从 `压测 owner 还没安排` 识别未闭环问题；新增 tri-lane 和 Live ASR 回归测试。真实仓库 batch smoke 现在显示 perfect lane ready 5/5、mock lane ready 5/5、real ASR blocked 4/5、非工程负控 candidate=0，整体阻塞转为 `blocked_by_asr_quality`。

DRV-001 增加桌面 Native Runtime 前端面板：Web 工作台新增 `desktop-native-runtime-panel`。普通浏览器上下文显示 `browser_fallback/not_available`，不报错、不写本地、不采集音频；Tauri 上下文会通过 `window.__TAURI__.core.invoke` 调用 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个 no-op command 并展示返回安全字段。PCWEB-118 已完成一次受控 Tauri Rust `cargo check`，PCWEB-119 已完成真实 Tauri WebView no-op IPC 验证。
PCWEB-118 已完成 first controlled Tauri `cargo check`：使用 ignored `artifacts/tmp/rust_toolchain` 和 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`，修复 icon 缺失和 Tauri command macro duplicate reexport 后，`cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 通过。PCWEB-119 已完成显式 real Tauri no-op run，PCWEB-120 已完成同 session worker mic source manual review packet bridge，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；下一步应是 ASR quality exit，而不是访问麦克风或启动真实 worker。

DRV-002 增加公开音频 source whitelist：`data/asr_eval/public_sources.json` 和 `tools/public_audio_source_whitelist.py` 只生成白名单报告，不下载音频、不读取真实用户音频、不读取 `configs/local`。白名单当前包含 AISHELL-4、AliMeeting、AISHELL-1 的 OpenSLR 来源和 license 边界；公开原始数据只能放到 ignored 的 `data/asr_eval/public_raw/` 或 `artifacts/tmp/public_audio/`。

DRV-003 到 DRV-006 增加公开/合成音频 no-execute validation gates：

- `tools/public_audio_sample_extraction_plan.py`：生成 bounded public sample extraction plan，默认 `safe_to_download_now=false`、`safe_to_extract_now=false`。
- `tools/synthetic_meeting_script_report.py`：校验 5 个中文合成技术会议脚本，覆盖 API、release、incident、architecture、non-engineering control，并要求 expected state/gap/card annotations。
- `tools/synthetic_audio_generation_plan.py`：生成本地/offline-only 合成音频计划，默认 `safe_to_generate_audio_now=false`。
- `tools/asr_event_generation_plan.py`：生成本地 ASR event plan，默认 `safe_to_run_asr_now=false`，并要求后续记录 RTF、latency、raw/normalized CER、技术实体 precision/recall、CPU 和内存。

DRV-019 增加 FunASR 模型下载审批包：`code/asr_runtime/funasr-model-download-approval.policy.json` 和 `tools/funasr_model_download_approval_packet.py`。默认报告是 `funasr_model_download_approval_packet_static_report`，只记录 ModelScope/iic `speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`、约 840MB 观察到的模型下载风险、手动下载文本、approval tokens、cleanup notes 和 post-download verification order；当前保持 `safe_to_execute_download_now=false`、`safe_to_download_models_now=false`、`safe_to_run_modelscope_now=false` 和 `safe_to_run_funasr_smoke_now=false`，不执行 `modelscope`/Python 命令、不读 `configs/local`、不读真实用户音频、不调用远程 ASR/LLM。

DRV-020 增加 ASR Live Pipeline Replay gate：`tools/asr_live_pipeline_replay.py` 只读取 allowed `artifacts/tmp/asr_events` 事件 JSON，复用 Web Live ASR builder 输出 replay report，检查 EvidenceSpan、state_event、scheduler_event、suggestion_candidate_event、llm_request_draft_event 和 no-LLM 状态；同时收紧 OpenQuestion/ActionItem 本地抽取规则，要求工程上下文，避免非工程会议因“是否方便”或“名单整理”误触发工程候选。

DRV-021 增加本地 ASR event file handoff API：`POST /live/asr/local-event-files/sessions` 接受 `session_id`、`provider` 和 `events_path`，仅从 `artifacts/tmp/asr_events` 读取 worker/smoke 产出的 JSON event file，创建可通过现有 `/live/asr/sessions/{id}/events` 和 `/events.sse` 消费的 Live ASR session；该 API 默认 `safe_to_call_llm_now=false`、`safe_to_call_remote_asr_now=false`、`safe_to_read_user_audio_now=false`、`safe_to_read_configs_local_now=false`、`safe_to_capture_microphone_now=false`。

DRV-023 加固本地 ASR event file handoff API：坏 JSON、非 list、非 object item、缺失文件进入 `blocked_by_invalid_events_file`；未知 event type、缺 `segment_id`、空 final/revision、非法时间戳或 confidence、revision 缺 `revision_of` 进入 `blocked_by_event_contract`；重复 `session_id` 进入 `blocked_by_duplicate_session` 且不覆盖已有 Live ASR record；repo 内 allowed 绝对路径显示为 repo-relative，repo 外绝对路径 redacted。该入口仍不读真实音频、不访问麦克风、不调用远程 ASR/LLM、不下载模型。

只查看将执行的命令：

```bash
python3 tools/run_quality_gate.py --profile pc-web --dry-run
```

分模块命令：

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/asr_bakeoff
python3 -m pytest tests -q

cd /Users/chase/Documents/面试/meeting-copilot/code/asr_runtime
pytest -q

cd /Users/chase/Documents/面试/meeting-copilot/code/core
pytest -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
pytest -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
```

当前验证：

```text
asr_bakeoff: 18 passed, 1 warning
asr_runtime: 35 passed, 1 warning
core: 34 passed, 1 warning
web_mvp backend: 40 passed, 2 warnings
web_mvp browser smoke: status ok
```

## 运行单样本 smoke

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/asr_bakeoff
python3 -m asr_bakeoff.cli \
  --manifest ../../data/asr_eval/manifests/smoke.json \
  --provider mock \
  --mock-transcripts ../../configs/asr_providers/mock-transcripts.json \
  --output ../../results/asr_bakeoff/smoke-mock.json
```

## 运行多场景 smoke

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/asr_bakeoff
python3 -m asr_bakeoff.cli \
  --manifest ../../data/asr_eval/manifests/smoke-multiscenario.json \
  --provider mock \
  --mock-transcripts ../../configs/asr_providers/mock-transcripts.json \
  --output ../../results/asr_bakeoff/smoke-multiscenario-mock.json
```

当前多场景 smoke 输出：

```json
{
  "sample_count": 4,
  "failed_sample_count": 0,
  "scored_cer_sample_count": 4,
  "scored_entity_sample_count": 4,
  "avg_cer": 0.0,
  "avg_entity_f1": 1.0
}
```

## command provider

`command` provider 可接入任意外部 ASR 程序。外部命令向 stdout 输出 JSON：

```json
{
  "text": "接口新增 trace_id 字段，需要兼容调用方。",
  "latency_ms": 1234,
  "segments": [
    {"start_ms": 0, "end_ms": 3000, "text": "接口新增 trace_id 字段，需要兼容调用方。"}
  ],
  "entities": ["trace_id", "兼容", "调用方"],
  "raw": {}
}
```

示例：

```bash
python3 -m asr_bakeoff.cli \
  --manifest ../../data/asr_eval/manifests/smoke.json \
  --provider command \
  --provider-name local-funasr \
  --command "python ../asr_runtime/scripts/transcribe_funasr.py {audio_path} --model paraformer-zh --device cpu --no-punc" \
  --output ../../results/asr_bakeoff/funasr-smoke.json
```

## 下一步

当前下一步不再继续扩散 ASR 横评，而是按 `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md` 和 `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md` 执行：

1. 进入 DRV-032 ASR quality exit：优先使用已就绪 FunASR 本地模型目录，或走 DRV-019 模型下载审批包；没有批准前不下载模型。显式远端 ASR 对照和显式降级试点都是可选路径，默认不增加 provider 费用。
2. 做 bounded normalizer/hotword 工作：只恢复 ASR 文本中已有线索，不从 `<unk>` 或缺失文本猜实体。
3. 重新跑 5 场景 batch gate：严格质量目标是至少一个工程脚本 real ASR normalized recall 接近 0.8，同时保持 perfect/mock ready 和非工程 control 0 candidate；若选择显式降级试点，只能验证 timing/feedback，不算 ASR quality Go evidence。
4. 公开音频只保留合法授权小样本治理：DRV-031 已确认 AliMeeting/AISHELL-4 当前缺真实 archive member path、clip sha256 和下载审批，公开音频阶段保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`，不下载 GB 级大包，不转向版权不清来源。
5. 合成/公开样本事件的产品链路 smoke 已由 DRV-041/042 收束：approved ASR event JSON 可以纯内存闭合到 shadow export preview，默认 5 场景 batch 要求 4 个工程 mock 场景 preview、非工程 control 无候选；后续不要继续增加同类 wrapper，只在 ASR quality exit 或真实 shadow-test 前置上推进。
6. 推进桌面 no-op runtime / IPC 和 pilot 前置闭环：PCWEB-098 到 PCWEB-124 已完成 worker process contract、command protocol、synthetic lifecycle harness、implementation approval packet、no-execution sidecar skeleton、command runner binding preview、implementation skeleton/no-dispatch、mic adapter contract、mic adapter readiness UI/API、mic adapter no-op Tauri IPC binding、worker output -> Web Live ASR session closure、mic adapter no-op UI invocation、short local simulated input timeline report、ASR event provenance manifest、真实 Tauri no-op WebView evidence、worker mic source bridge、mic adapter boundary、ASR worker mic source boundary、单次 worker mic source approval evidence 机制和 real mic readiness UI；PCWEB-115 已能消费显式降级试点风险接受。下一步不再做同类 wrapper，只按 DRV-032 quality exit 出口推进，不接麦克风、不启动真实 worker、不运行 Tauri dev/build。
7. 在 no-LLM candidate 稳定后，再受控启用 OpenAI-compatible 中转站做低频建议卡，发送 EvidenceSpan 和结构化状态摘要，不发送原始音频。
8. 用户最终执行真实麦克风 20-30 分钟中文技术会议 shadow test，按 DRV-033 schema 导出 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、反馈标签、privacy/cost flags、audio retention/delete status 和 Go/Pivot/Stop。
