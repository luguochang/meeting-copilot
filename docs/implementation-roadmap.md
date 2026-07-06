# Meeting Copilot 实施路线图

> 日期：2026-06-18  
> 方法：SDD + TDD。先文档化边界和验收，再写最小可测代码；每一步有命令、有结果、有失败记录。

## 1. 当前工程原则

- 默认只把远程费用花在已配置的 LLM 中转站。
- ASR 优先本地/开源，不默认接付费云 ASR。
- 不污染系统 Python，不把模型依赖塞进桌面主进程。
- 每个新增能力先有测试或可重复 smoke 命令。
- 真实录音、API key、模型缓存不提交。
- 如果实时 ASR 质量不达标，及时降级，不硬做无价值产品。
- 需求、测试和结果文件必须能回到 `docs/requirements-traceability-matrix.md` 的需求 ID。

## 1.1 SDD/TDD Definition of Done

一个能力不能只因为“代码写了”就算完成，必须同时满足：

- 有需求 ID 或明确写入需求矩阵。
- 有失败用例、通过用例或可重复 smoke 命令。
- 有输出文件或日志记录关键指标。
- 对隐私、密钥、模型缓存、真实录音的存放路径有说明。
- 结论写回文档，尤其是失败、降级和不能承诺的部分。
- 涉及 LLM 的能力必须说明调用频率、失败行为和成本边界。

## 2. 阶段划分

### 阶段 A：本地 ASR runtime 可行性

目标：

- 建立独立 ASR runtime。
- 跑通本地私有录音 `<private-audio>.16k.wav` 的离线转写。
- 输出转写文本、segment、耗时、RTF、环境信息。

默认路线：

1. sherpa-onnx：Mac 端侧性能首测基线，适合证明轻量和实时可行性。
2. FunASR：中文质量候选和本地质量对照，依赖与模型体积更重。
3. SenseVoice 作为第三候选，不作为第一轮默认。

验收：

- 能输出中文文本。
- 转写耗时可记录。
- 至少输出多个 final segment 或明确记录 endpoint 切分失败。
- 依赖安装在 `meeting-copilot/code/asr_runtime/` 下。
- 不污染系统环境。

### 阶段 B：LLM 诊断模拟链路

目标：

- 用 ASR segment / EvidenceSpan 调用 `gpt-5.5` 中转站。
- 先判断 `meeting_context.is_engineering_meeting`。
- 输出会议摘要、行动项、风险、未闭环问题、建议卡片。
- JSON schema 可校验。

验收：

- 能稳定返回结构化 JSON。
- 非工程会议 `suggestion_cards` 必须为空。
- 建议卡片必须引用证据片段。
- 建议卡片类型必须在白名单内。
- 不允许无证据编造 owner、deadline、决策。
- LLM 分析必须支持增量 segment 输入，不能只依赖会后整段全文。
- 增量分析调度器位于 transcript stabilizer/state engine 和 LLM gateway 之间，输入是 `final`、`revision`、状态变化、时间窗口 tick、用户操作或会后任务，不是裸 `partial`。
- ASR `partial` 只允许更新低风险预览或候选信号，不允许直接触发强建议 LLM 调用。
- 每次 LLM request 必须记录触发原因、合并的 segment/event、provider/model/prompt version、token/等价用量和降级状态。

### 阶段 C：最小有价值 demo

目标：

```text
真实录音
  -> 本地 ASR
  -> transcript normalizer / stabilizer / segmenter
  -> engineering context gate
  -> incremental meeting state engine
  -> state diff / gap rules
  -> suggestion cards within timing window
  -> markdown/json report
```

最小有价值能力：

- 真实中文录音转文字。
- 技术实体/关键信息初步高亮。
- 结构化会议状态：决策、行动项、风险、未闭环问题。
- 低频建议卡片。
- 带证据的会后报告。
- 状态事件日志：created、updated、answered、confirmed、dismissed。

验收脚本：

- 详见 `docs/minimum-valuable-demo-script.md`。
- 只有转写和总结不算 demo 成功。
- 至少需要多个 EvidenceSpan、一个 ActionItem 或 DecisionCandidate、一张有证据的工程缺口卡片。
- 必须展示至少 5 条状态事件或状态 diff。
- 工程 demo 必须至少覆盖 DecisionCandidate、ActionItem、Risk、OpenQuestion 四类状态。
- 主视图必须以会议状态和建议卡片为核心，transcript 只作为可展开证据层。

### 阶段 D：实时链路验证

目标：

- 用本地音频 chunk 模拟实时输入。
- 验证 partial/final 延迟和稳定性。
- 决定本地 ASR 是否足以进入桌面端实时实现。
- 验证建议卡片是否能在相关 final segment 后 10-30 秒内出现。

验收：

- final 延迟 P95 <= 3.5s，或明确不达标。
- Provider 必须共用 `StreamingTranscriptEvent` 或等价契约，至少覆盖 `partial|final|revision`，包含 `segment_id/start_ms/end_ms/text/received_at_ms`，可选 `confidence/revision_of/raw`。
- final/revision 才能转为正式 `TranscriptSegment` 和 EvidenceSpan；partial 不能支撑强建议。
- 文件模式输出 1 条或少量 `segments` 只能证明 EvidenceSpan 文件链路可用，不等价于真实 streaming final segment 达标。
- ASR worker 内存稳定。
- 长音频不崩溃。
- 如果建议太晚，只能计为会后待确认项，不能算实时建议成功。

### 阶段 D0.5：本地 live_asr_stream 骨架

目标：

- 在进入 Mac desktop shell 前，先让 Web backend 能消费 ASR runtime 的统一 streaming event contract。
- 输出与 Live Mock 同形但来源不同的本地 ASR live envelope：`source=live_asr_stream`、`trace_kind=live_event`。
- 用 synthetic/file ASR event JSON 验证 `partial/final/revision/error/end_of_stream` 到 Web SSE 的转换。
- 用本地确定性规则验证 `final/revision` 可以触发带 EvidenceSpan 的 `DecisionCandidate` / `ActionItem` / `Risk` / `OpenQuestion` 状态候选和 no-LLM scheduler decision log，避免产品退化成实时转写展示。

验收：

- `partial` 只更新预览，不生成正式 EvidenceSpan。
- `final` 和 `revision` 生成可点击 EvidenceSpan，并能被 Web 工作台从空 live view 增量渲染。
- `revision` 能复用 PCWEB-035/036 的 evidence lifecycle 和 card invalidation 契约。
- `final/revision` 可生成本地 `DecisionCandidate` / `ActionItem` / `Risk` / `OpenQuestion`、no-LLM scheduler decision log、no-LLM `suggestion_candidate_event` 和 no-LLM `llm_request_draft_event`，payload 必须明确 queued/skipped/cooldown/budget/not_called、`prompt_version/model=not-called`、候选 gap rule、candidate quality confidence/degradation metadata、request draft 的 `draft_only/not_generated` 状态和 `card_status=not_created`，不得生成正式建议卡或 LLM schema 结果；设置本地数据目录时，Live ASR JSON audit record 必须可跨 app 实例读回并可删除；audit record 可导出非正式 JSON/Markdown 复盘草稿，Web 工作台可在 Live ASR terminal summary 后展示该草稿，但不得混同正式 gated report；candidate queue 可通过只读 `/suggestion-candidates` 端点查询，request draft queue 可通过只读 `/llm-request-drafts` 端点查询，execution preview queue 可通过只读 `/llm-execution-previews` 端点查询，disabled executor run 可通过 `POST /llm-execution-runs` 查询 skipped run boundary，schema validation dry-run 可通过 `POST /llm-schema-validation-dry-runs` 校验 caller-provided candidate response，card creation policy dry-run 可通过 `POST /llm-card-creation-policy-dry-runs` 校验未来成卡资格，provider readiness 可通过只读 `/llm-provider-readiness` 查询 not-ready blocker，provider config boundary 可通过只读 `/llm-provider-config-boundary` 查询 template-only 字段分类和展示策略，provider masked status 可通过只读 `/llm-provider-masked-status` 查询 template-only status envelope，provider config validation 可通过 `POST /llm-provider-config-validation` 校验 request-body-only config draft，provider config loader preflight 可通过 `POST /llm-provider-config-loader-preflight` 校验 future loader request shape 和授权意图、拒绝 URL/file URL/NUL/control characters/路径穿越/重复字段，provider secret storage policy 可通过只读 `/llm-provider-secret-storage-policy` 查询推荐 secret reference 类型、禁止存储位置和 loader guards，但不得在本阶段排序、过滤、读取密钥、返回真实/脱敏配置值或路径派生值、报告 key presence/validity/length/hash/prefix/suffix/fingerprint、执行请求、估算 token、生成正式建议卡或调用 LLM；具体计划见 `docs/pcweb-038-live-asr-state-scheduler-plan.md`、`docs/pcweb-039-live-asr-scheduler-log-plan.md`、`docs/pcweb-040-live-asr-state-extraction-plan.md`、`docs/pcweb-041-live-asr-audit-persistence-plan.md`、`docs/pcweb-042-live-asr-draft-review-plan.md`、`docs/pcweb-043-live-asr-draft-ui-plan.md`、`docs/pcweb-044-live-asr-action-risk-state-plan.md`、`docs/pcweb-045-live-asr-suggestion-candidate-plan.md`、`docs/pcweb-046-live-asr-candidate-confidence-plan.md`、`docs/pcweb-047-live-asr-candidate-query-plan.md`、`docs/pcweb-048-live-asr-llm-request-draft-plan.md`、`docs/pcweb-049-live-asr-llm-request-draft-query-plan.md`、`docs/pcweb-050-live-asr-llm-execution-preview-plan.md`、`docs/pcweb-051-live-asr-llm-executor-disabled-run-plan.md`、`docs/pcweb-052-live-asr-llm-provider-readiness-plan.md`、`docs/pcweb-053-live-asr-llm-provider-config-boundary-plan.md`、`docs/pcweb-054-live-asr-llm-provider-masked-status-plan.md`、`docs/pcweb-055-live-asr-llm-provider-config-validation-plan.md`、`docs/pcweb-056-live-asr-llm-provider-config-loader-preflight-plan.md`、`docs/pcweb-057-live-asr-llm-provider-secret-storage-policy-plan.md`、`docs/pcweb-064-live-asr-card-creation-policy-dry-run-plan.md`。
- card lifecycle preview dry-run 可通过 `POST /llm-card-lifecycle-preview-dry-runs` 预览 future schema/card/silenced events；card lifecycle append preflight dry-run 可通过 `POST /llm-card-lifecycle-append-preflight-dry-runs` 预检 future event id、幂等键和追加顺序；card lifecycle append disabled run 可通过 `POST /llm-card-lifecycle-append-runs` 在 `mode=disabled` 下返回 skipped append run envelopes；card lifecycle append repository dry-run 可通过 `POST /llm-card-lifecycle-append-repository-dry-runs` 在 `mode=dry_run_only` 下返回 future repository result envelopes；card lifecycle append transaction disabled run 可通过 `POST /llm-card-lifecycle-append-transaction-runs` 在 `mode=disabled` 下返回 skipped transaction run envelopes；card lifecycle append result audit preview 可通过 `POST /llm-card-lifecycle-append-result-audit-previews` 在 `mode=preview_only` 下返回 response-only append result audit event previews；card lifecycle retry/replay preflight 可通过 `POST /llm-card-lifecycle-retry-replay-preflights` 在 `mode=preflight_only` 下区分 no-existing append、safe same-event replay、partial replay 和 blocked conflict；card lifecycle append event serializer dry-run 可通过 `POST /llm-card-lifecycle-append-event-serializer-dry-runs` 在 `mode=dry_run_only` 下返回 canonical future persisted event objects；card lifecycle append mutation preflight 可通过 `POST /llm-card-lifecycle-append-mutation-preflights` 在 `mode=preflight_only` 下返回 response-only mutation eligibility checks；card lifecycle append transaction commit preflight 可通过 `POST /llm-card-lifecycle-append-transaction-commit-preflights` 在 `mode=preflight_only` 下返回 response-only commit readiness checks；card lifecycle append idempotency store write preflight 可通过 `POST /llm-card-lifecycle-append-idempotency-store-write-preflights` 在 `mode=preflight_only` 下返回 response-only idempotency store write checks；`PCWEB-076` card lifecycle append result audit event persistence preflight 可通过 `POST /llm-card-lifecycle-append-result-audit-event-persistence-preflights` 在 `mode=preflight_only` 下返回 response-only audit event persistence checks；`PCWEB-077` card lifecycle readiness summary 可通过 `POST /llm-card-lifecycle-readiness-summaries` 在 `mode=summary_only` 下返回 response-only overall readiness、12 phase summaries、block reasons 和 next decisions；`PCWEB-078` Web 工作台在 Live ASR terminal summary 后把该 summary 渲染到 `card-lifecycle-readiness-panel`；`PCWEB-079` Web/API 通过 `GET /desktop/shell-readiness` 和 `desktop-readiness-panel` 展示进入 Mac-first desktop shell 前的 8 phase readiness、blockers、next decisions 和 `desktop_safe_to_capture_audio=false` 等禁用 flags；`PCWEB-080` Web/API 通过 `GET /desktop/runtime-boundary` 和 `desktop-runtime-boundary-panel` 展示进入真实 desktop shell 前的 Tauri-first/Electron-fallback、ASR sidecar worker、Mac-first process model、8 phase runtime boundary 和 `desktop_runtime_safe_to_create_shell=false` 等禁用 flags；`PCWEB-081` Web/API 通过 `GET /desktop/native-bridge-contract` 和 `desktop-native-bridge-contract-panel` 展示进入真实 Tauri shell scaffold 前的 8 command native bridge catalog、8 phase bridge contract、error/resource policy 和 `desktop_bridge_safe_to_create_native_bridge=false` 等禁用 flags，下一桌面增量必须进入 `create_tauri_shell_scaffold_against_bridge_contract`。上述能力仍不得在本阶段写入 audit record、append result audit event、begin/commit/rollback repository transaction、写入 idempotency store、读取密钥、执行请求、估算 token、生成正式建议卡、采集 microphone/system audio、请求系统权限、枚举音频设备、启动 ASR worker、绑定 IPC、启动 native bridge、创建 Tauri/Electron 项目或依赖、读取 provider config 或调用 LLM/远程 ASR；具体计划见 `docs/pcweb-065-live-asr-card-lifecycle-preview-dry-run-plan.md`、`docs/pcweb-066-live-asr-card-lifecycle-append-preflight-dry-run-plan.md`、`docs/pcweb-067-live-asr-card-lifecycle-append-disabled-run-plan.md`、`docs/pcweb-068-live-asr-card-lifecycle-append-repository-dry-run-plan.md`、`docs/pcweb-069-live-asr-card-lifecycle-append-transaction-disabled-run-plan.md`、`docs/pcweb-070-live-asr-card-lifecycle-append-result-audit-preview-plan.md`、`docs/pcweb-071-live-asr-card-lifecycle-retry-replay-preflight-plan.md`、`docs/pcweb-072-live-asr-card-lifecycle-append-event-serializer-dry-run-plan.md`、`docs/pcweb-073-live-asr-card-lifecycle-append-mutation-preflight-plan.md`、`docs/pcweb-074-live-asr-card-lifecycle-append-transaction-commit-preflight-plan.md`、`docs/pcweb-075-live-asr-card-lifecycle-append-idempotency-store-write-preflight-plan.md`、`docs/pcweb-076-live-asr-card-lifecycle-append-result-audit-event-persistence-preflight-plan.md`、`docs/pcweb-077-live-asr-card-lifecycle-readiness-summary-plan.md`、`docs/pcweb-078-live-asr-card-lifecycle-readiness-ui-plan.md`、`docs/pcweb-079-desktop-shell-readiness-boundary-plan.md`、`docs/pcweb-080-desktop-runtime-boundary-plan.md` 和 `docs/pcweb-081-desktop-native-bridge-contract-plan.md`。
- `PCWEB-082` 当前新增 `code/desktop_tauri/src-tauri` 静态 Tauri shell scaffold、`tauri.conf.json`、最小 capability 和 `runtime_get_status` / `session_prepare` / `asr_worker_health` 三个 no-op command，均返回 `noop_bound` 且 `safe_to_execute_real_action=false`。这只允许源码 scaffold，不允许运行/构建/打包 Tauri，不允许创建 `Cargo.lock`、`package.json`、dependency lock files、`node_modules`、`target` 或 installer/signing/notarization artifacts，不允许绑定 audio commands、请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、读取 `configs/local` 或密钥、写 runtime/session/audio 数据或调用远程 ASR/LLM；具体计划见 `docs/pcweb-082-tauri-shell-scaffold-spike-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-082-tauri-shell-scaffold-spike.md`。
- `PCWEB-083` 当前新增 `code/desktop_tauri/build-readiness.policy.json` 和 `tools/desktop_build_readiness.py`，用于输出 no-build readiness report 和显式 `toolchain_version_probe_only`。该阶段保持 `safe_to_run_cargo_check_now=false`，只允许版本探测 `rustc --version` 与 `cargo --version`，且 custom policy 不能扩大工具层执行白名单；不允许运行 cargo check/build、Tauri dev/build、package manager 或 npm/pnpm/yarn/npx launcher，不允许创建 lock/build artifacts，不允许接音频/权限/worker/密钥/远程 provider；未来 cargo check 必须等 user approval、Cargo.lock policy、target dir policy、network dependency fetch policy、cache cleanup policy 和 no-audio/no-secret/no-remote boundary 复核后才可启用。
- `PCWEB-084` 当前新增 `code/desktop_tauri/cargo-check.policy.json` 和 `tools/desktop_cargo_check_policy.py`，用于输出 no-run cargo-check artifact policy report。该阶段决定未来首次获批 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 可生成并提交 `Cargo.lock`，Cargo output 必须使用 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`，repeat check 使用 `--locked --offline`，但当前仍保持 `safe_to_run_cargo_check_now=false`，不安装 Rust、不联网抓依赖、不运行 cargo/Tauri/package manager、不生成 lock/target/build artifacts、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-084-desktop-cargo-check-artifact-policy-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-084-desktop-cargo-check-artifact-policy.md`。
- `PCWEB-085` 当前新增 `code/desktop_tauri/rust-toolchain-readiness.policy.json` 和 `tools/desktop_rust_toolchain_readiness.py`，用于输出 no-install Rust toolchain readiness report 和显式 `local_version_and_platform_probe_only`。该阶段默认不执行命令，显式 probe 只允许 `rustc --version`、`cargo --version`、`rustup --version` 和 `xcode-select -p`，并对 `xcode-select -p` 做 presence-only 脱敏；当前仍保持 `safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false` 和 `safe_to_run_cargo_check_now=false`，不安装 Rust、不改 shell profile、不运行 cargo/Tauri/package manager、不生成 artifacts、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-085-desktop-rust-toolchain-readiness-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-085-desktop-rust-toolchain-readiness.md`。
- `PCWEB-086` 当前新增 `code/desktop_tauri/rust-toolchain-installation.policy.json` 和 `tools/desktop_rust_toolchain_installation_decision.py`，用于输出 no-install Rust toolchain installation decision report 和 `no_install_decision_report_only`。该阶段只记录 `recommended_install_provider=official_rustup`、显式 approval tokens、platform notes 和 post-install verification order；当前仍保持 `safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false`、`safe_to_run_rustup_now=false` 和 `safe_to_run_cargo_check_now=false`，不运行 curl/sh/rustup/cargo/package manager、不改 shell profile、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-086-desktop-rust-toolchain-installation-decision-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-086-desktop-rust-toolchain-installation-decision.md`。
- `PCWEB-087` 当前新增 `code/desktop_tauri/rust-toolchain-install-approval.policy.json` 和 `tools/desktop_rust_toolchain_install_approval_packet.py`，用于输出 Rust toolchain install approval packet 和 `manual_user_run_only`。该阶段只记录官方 Rust/rustup/Tauri source URLs、macOS/Linux inert manual command text、Windows `rustup-init.exe` manual guidance、approval tokens、risk notes、rollback notes 和 post-install verification order；当前仍保持 `safe_to_execute_install_now=false`、`safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false`、`safe_to_run_rustup_now=false` 和 `safe_to_run_cargo_check_now=false`，不执行 manual text、不运行 installer/curl/sh/rustup/cargo/package manager、不改 shell profile/PATH、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-087-desktop-rust-toolchain-install-approval-packet.md`。
- `PCWEB-088` 当前新增 `code/desktop_tauri/rust-post-install-probe-approval.policy.json` 和 `tools/desktop_rust_post_install_probe_approval.py`，用于输出 Rust post-install probe approval packet 和 `no_probe_execution_approval_packet_only`。该阶段只记录未来 read-only probe allowlist、redaction requirements、approval tokens、expected result schema 和 cargo-check blockers；当前仍保持 `safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`，不运行 rustc/cargo/rustup/xcode-select/cargo check/Tauri/package manager、不读 PATH/shell profile/cargo home/rustup home、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-088-desktop-rust-post-install-probe-approval-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-088-desktop-rust-post-install-probe-approval.md`。
- `PCWEB-089` 当前新增 `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` 和 `tools/desktop_rust_post_install_probe_result_intake.py`，用于输出 Rust post-install probe result intake report 和 `manual_result_validation_only` / `caller_provided_json_only`。该阶段只校验 caller-provided bounded JSON status，拒绝 raw stdout/stderr、command、path、env、home/cache、provider config、api_key、authorization、bearer token 等字段；当前仍保持 `safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false` 和 `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`，不运行 rustc/cargo/rustup/xcode-select/cargo check/Tauri/package manager、不读 PATH/shell profile/cargo home/rustup home、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-089-desktop-rust-post-install-probe-result-intake.md`。
- `PCWEB-090` 当前新增 `code/desktop_tauri/first-cargo-check-execution.policy.json` 和 `tools/desktop_first_cargo_check_execution_boundary.py`，用于输出 first cargo check execution boundary report 和 `explicit_manual_execution_packet_only`。该阶段把 PCWEB-084 的 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`、`CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`、Cargo.lock/target artifact policy 与 PCWEB-089 bounded toolchain result 合并成手动执行包；即使 toolchain result 可用，也只返回 `ready_for_explicit_user_approval`，保持 `safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`，不运行 cargo/Tauri/package manager、不抓依赖、不生成 `Cargo.lock`/target、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-090-desktop-first-cargo-check-execution-boundary.md`。
- `PCWEB-091` 当前新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`，用于输出 Tauri no-op shell local run smoke readiness report 和 `readiness_report_only`。该阶段只静态验证 PCWEB-082 scaffold、`devUrl=http://127.0.0.1:8765/`、`frontendDist`、minimal capability、exact no-op command catalog、generated artifact blockers 和 PCWEB-090 no-command boundary；即使全部通过，也只返回 `ready_for_explicit_tauri_run_approval`，保持 `safe_to_run_tauri_dev_now=false`、`safe_to_run_cargo_check_now=false` 和 `safe_to_capture_audio_now=false`，不运行 Tauri/Cargo/package manager、不抓依赖、不生成 lock/target/installer、不接音频/权限/worker/密钥/远程 provider；具体计划见 `docs/pcweb-091-tauri-noop-shell-local-run-smoke-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke.md`。
- 本阶段不接麦克风、系统音频、远程 ASR 或真实 LLM；具体计划见 `docs/pcweb-037-live-asr-stream-plan.md`。

### 阶段 D0：PC Local Web MVP

目标：

- 在本机启动一个本地 Web/API 原型。
- 复用 `code/core` 和现有 `asr_runtime` 输出。
- 展示 transcript、EvidenceSpan、meeting state、suggestion cards、state events、report。
- 验证产品价值不是“转写 + 总结”，而是实时/准实时 Copilot 状态和建议。

验收：

- `/health` 本地接口可用。
- 可以创建、读取、删除本地会议会话。
- 会话快照包含 transcript、states、suggestion_cards、state_events、quality。
- 正式状态和建议卡片必须带 EvidenceSpan。
- 非工程会议 suggestion_cards 为 0。
- 建议卡片支持保留、忽略、标记错误。
- 支持 JSON/Markdown 报告导出。
- core 不依赖 Tauri、Electron、macOS 或 Windows API。

进入条件：

- ASR runtime、EvidenceSpan、meeting analysis、scheduler 已有最小可测链路。
- ASR 泛评测阶段已收束，下一步需要验证产品价值。

退出条件：

- 至少 2 个中文技术会议样本跑通。
- 工程会议能覆盖 DecisionCandidate、ActionItem、Risk、OpenQuestion。
- 至少 1 张建议卡片能回溯 EvidenceSpan。
- 非工程会议工程建议卡片为 0。
- 本地 API 和 core 测试通过。

### 阶段 E：真实桌面采集 MVP

目标：

- 接入 macOS 麦克风和系统音频采集。
- 将真实音频流送入 ASR worker。
- UI 展示会议状态、建议卡片、质量状态和证据面板。

验收：

- 手动开始、暂停、恢复、停止。
- 音频质量异常可见。
- ASR/LLM provider 状态可见。
- 会议数据可删除。
- 不默认启用远程 ASR。

## 3. 代码结构规划

```text
meeting-copilot/code/
  core/                     平台无关 Copilot core
    meeting_copilot_core/
      session_snapshot.py
    tests/

  web_mvp/                  PC Local Web MVP
    backend/
      meeting_copilot_web_mvp/
        app.py
        repository.py
      tests/

  asr_bakeoff/              已有：评测工具、LLM smoke、provider 接口
  desktop_tauri/            PCWEB-082 静态 Tauri shell scaffold，不运行/构建/打包
    README.md
    src-tauri/
      Cargo.toml
      build.rs
      tauri.conf.json
      capabilities/default.json
      src/main.rs
      src/lib.rs
  asr_runtime/              新增：本地 ASR runtime，不污染主工具
    README.md
    .gitignore
    scripts/
      inspect_audio.py
      transcribe_sherpa_onnx.py
      transcribe_command.py
    tests/
      test_audio_inspection.py
      test_transcript_report.py
    outputs/                ignored
    models/                 ignored
    .venv/                  ignored
```

## 4. 第一轮执行任务

1. 新建 `code/asr_runtime` 目录和忽略规则。
2. 写音频检查脚本测试，读取 wav 时长、采样率、声道。
3. 实现音频检查脚本。
4. 建立 ASR runtime 文档。
5. 检查 Python 版本和可用包管理器。
6. 尝试建立独立 venv。
7. 优先安装 sherpa-onnx。
8. 下载或选用中文模型。
9. 跑真实录音 chunked endpoint 转写。
10. 生成 `outputs/<private-audio>.sherpa.json`。
11. 生成 `outputs/<private-audio>.report.json`。
12. 调 LLM 中转站生成结构化分析。
13. 用非工程真实录音验证工程语境门禁。
14. 用合成中文技术会议短样本验证工程卡片链路。
15. 写自测结论到 `docs/local-run-notes.md`。

## 4.1 当前执行任务：PC-1 Local Web MVP

1. 建立 `docs/pc-local-web-mvp-requirements.md`。
2. 建立 `docs/pc-local-web-mvp-acceptance.md`。
3. 建立 `docs/pc-local-web-mvp-plan.md`。
4. 新建 `code/core`，实现平台无关会议快照聚合。
5. 新建 `code/web_mvp/backend`，实现本地 API。
6. 用 TDD 覆盖快照聚合、非工程门禁、卡片状态、删除会话。
7. 跑 core、web_mvp、asr_runtime、asr_bakeoff 测试。
8. 更新决策日志、需求追踪矩阵和项目结构。

## 5. Go / No-Go

Go：

- 本地 ASR 能输出可读中文。
- LLM 能基于 transcript 输出结构化分析。
- 建议卡片有证据，不编造。
- 非工程会议不输出工程缺口卡片。
- 工程会议样本能输出有证据的缺口卡片。
- 成本仍只有 LLM 中转站。
- raw ASR 指标和 normalized 指标都被记录，不能只看修正后的好结果。

No-Go / 降级：

- 本地 ASR 无法安装或严重不兼容 Mac。
- 本地 ASR 转写中文不可读。
- 352 秒音频耗时过长，无法支撑实时。
- LLM 对低质量 transcript 产生大量幻觉。
- 技术词、服务名、指标长期无法通过热词或 provider bake-off 达标。
- 建议卡片无法在会议中及时出现，只能做会后待确认。

## 6. 2026-06-19 当前实测结论

已完成的验证：

- `asr_runtime` 现在可直接 `pytest -q`，不需要手动 `PYTHONPATH`。
- 已有纯本地 `demo_pipeline`，可用 fake ASR JSON 和 fake analysis JSON 验证最小闭环。
- FunASR 文件模式已经支持统一 `segments` 输出，能进入 EvidenceSpan 链路。
- `transcript_report` 保留 raw transcript，并新增 `normalized_text` 和 `normalization_changes`。
- `demo_eval` 同时输出 raw 技术实体召回和 normalized 技术实体召回。
- 已新增增量分析调度器，证明 ASR `partial` 不触发 LLM，`final/revision` 受冷却窗口和小时预算控制，状态变化可走更短触发窗口。
- 已新增 mock streaming contract，把 `partial/final/revision` 转为统一 provider transcript；unknown event type 会失败，避免 provider 适配错误静默丢段。
- 已新增 `realtime_simulation.py`，可用可控事件流验证 provider transcript、EvidenceSpan 输入和 LLM 调度次数。
- 已新增 FunASR streaming 文件回放适配器，能按 `paraformer-zh-streaming` chunk 参数输出统一 streaming events。
- `PCWEB-058` 已新增 Live ASR provider config reader dry-run：只校验 future authorized config reader request、secret reference 和 dry-run 授权，不读取本地配置、环境密钥、keychain 或 secret adapter，不返回路径/secret reference/文件状态侧信道，不调用中转站。
- `PCWEB-059` 已新增 Live ASR provider masked status loader dry-run：只校验 future authorized masked status loader request、requested display fields、secret reference 和 dry-run 授权，不读取本地配置、环境密钥、keychain 或 secret adapter，不推断 status value，不返回路径/secret reference/文件状态侧信道或 API key 状态侧信道，不调用中转站。
- `PCWEB-060` 已新增 Live ASR OpenAI-compatible request body preview：从 no-LLM request drafts 派生 deterministic chat-completions messages、response_format schema target、metadata 和幂等键，但仍不读取 provider config、base URL、API key、Authorization header、环境密钥、keychain 或 `configs/local/`，不调用中转站、不估算 token、不生成 schema/card。
- `PCWEB-061` 已新增 Live ASR OpenAI request body preview redaction guard：preview 输出层应用 `local_sensitive_draft_value_guard.v1`，阻断 draft payload 中 API-key-like token、Bearer/Authorization、provider config marker、`configs/local` 或 relay-domain marker 被反射进 messages/metadata，同时保持原始 request drafts/events 不变。
- `PCWEB-062` 已新增 Live ASR OpenAI request body schema outline preview：在 request body preview 的 `response_format.json_schema` 中返回 outline-only `SuggestionCardV1` 字段合同，供后续 enabled executor、schema validator 和 card lifecycle 对齐；仍不生成完整 JSON Schema、不做 schema validation、不创建卡片、不调用中转站。
- `PCWEB-063` 已新增 Live ASR LLM schema validation dry-run：对 caller-provided `candidate_response` 做本地 `SuggestionCardV1` 和 dry-run gate 子集校验，返回 passed/failed 与 deterministic validation errors；仍不解析真实模型响应、不生成 `llm_schema_result`、不创建卡片、不调用中转站。
- `PCWEB-064` 已新增 Live ASR card creation policy dry-run：对 schema-shaped candidate response 做本地 evidence/state/segment/request linkage 和 timing policy 校验，返回 allowed/blocked 与 deterministic policy errors；仍不生成 `llm_schema_result`、不创建卡片、不调用中转站。
- `PCWEB-065` 已新增 Live ASR card lifecycle preview dry-run：对 schema/policy dry-run 后的 candidate 预览未来 enabled lifecycle 会追加的 `llm_schema_result` + `suggestion_card` 或 `llm_schema_result` + `suggestion_silenced`，但仍不生成真实事件、不改变 `/events`、不读取配置/密钥、不调用中转站。
- `PCWEB-066` 本轮新增 Live ASR card lifecycle append preflight dry-run：对 PCWEB-065 preview events 派生 future event id、幂等键和 would-append sequence，并预检已有 event/idempotency conflict；仍不写入 audit record、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `PCWEB-067` 本轮新增 Live ASR card lifecycle append disabled run：在 `mode=disabled` 下把 PCWEB-066 append plan 映射为 skipped append runs，并保留 preflight conflict；仍不写入 audit record、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `PCWEB-068` 本轮新增 Live ASR card lifecycle append repository dry-run：在 `mode=dry_run_only` 下把 PCWEB-067 skipped append runs 映射为 future repository result envelopes，并保留 preflight conflict；仍不写入 audit record、不写 repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `PCWEB-069` 本轮新增 Live ASR card lifecycle append transaction disabled run：在 `mode=disabled` 下把 PCWEB-068 repository results 映射为 skipped transaction runs，并保留 preflight conflict；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `PCWEB-070` 本轮新增 Live ASR card lifecycle append result audit preview：在 `mode=preview_only` 下把 PCWEB-069 transaction runs 映射为 response-only append result audit event previews，并保留 preflight conflict；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `PCWEB-071` 已新增 Live ASR card lifecycle retry/replay preflight：在 `mode=preflight_only` 下把 PCWEB-070 audit preview items 映射为 response-only retry/replay checks，区分 no-existing append、safe same-event replay、partial replay 和 blocked conflict；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `PCWEB-072` 本轮新增 Live ASR card lifecycle append event serializer dry-run：在 `mode=dry_run_only` 下把 PCWEB-065/066 preview/preflight 映射为 canonical future persisted event objects，并保留 preflight conflict；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。

关键数值：

```text
sherpa-onnx:
  venv: ~141MB
  model: ~26MB
  17.9 秒合成技术样本 latency: ~461ms
  优势: 非常快、端侧轻
  风险: payment-gateway 丢失，灰度/P99 等技术词错误

FunASR paraformer-zh, CPU, no punctuation:
  venv: ~1.2GB
  ASR model cache: ~954MB
  VAD model cache: ~3.9MB
  17.9 秒合成技术样本 latency: ~6.3s，RTF ~0.35
  peak memory: ~3.5GB
  优势: 保留更多中文技术语义，带 timestamp
  风险: 字间空格、payment-gateway/P99 等仍需归一化

FunASR paraformer-zh, CPU, punctuation:
  punctuation model cache: ~1.1GB
  17.9 秒合成技术样本 latency: ~17.0s，RTF ~0.95
  peak memory: ~5.8GB
  结论: 可读性更好，但不适合作为默认轻量实时链路

FunASR paraformer-zh-streaming, CPU, file-replayed streaming:
  online streaming model download: ~840MB
  17.9 秒合成技术样本 warm latency: ~18.8s，RTF ~1.05
  events: 30 partial / 6 window-final / 1 end_of_stream
  finalization_strategy: fixed_window_from_partial_hypotheses，不是 provider endpoint final
  scheduler: 只触发 1 次 LLM，30 个 partial 全部忽略
  优势: 多条窗口化 final segment 已能锻炼 EvidenceSpan 和增量调度链路
  风险: payment-gateway/P99/0.1% 等技术实体仍有明显错误，且 RTF 接近实时边界
```

最重要的产品判断：

- 只靠本地 ASR 不足以稳定支撑中文技术会议 Copilot。
- 只靠 LLM 也不能掩盖 ASR 质量问题，否则证据链会不可信。
- MVP 默认链路必须包含 transcript normalizer/stabilizer，并且同时保留 raw ASR 指标。
- 当前 normalized FunASR 样本 demo gate 通过，但 raw 技术实体召回为 0，说明下一步必须做 streaming/热词/术语表 bake-off，而不是直接做 UI。
- 当前 FunASR streaming 文件回放已证明多条窗口化 final segment、scheduler 节流和 provider contract smoke 可用，但仍不能证明 provider endpoint final、桌面实时音频采集或中文技术词质量达标。
- 下一步真实链路必须优先做热词/术语表/normalizer bake-off、chunk 参数调优、partial/final latency 统计、长会议稳定性，再进入桌面采集 UI。
