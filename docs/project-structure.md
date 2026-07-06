# 项目工作区结构

> 日期：2026-06-18

当前产品工作区为 `meeting-copilot/`，和历史面试材料、抓取输出隔离。

## 目录

```text
meeting-copilot/
  docs/                    产品、需求、架构、评测文档
  code/                    可运行代码
    core/                  平台无关 Copilot core
    web_mvp/               本地 PC Web MVP
    desktop_tauri/         PCWEB-082 静态 Tauri desktop shell scaffold
    asr_bakeoff/           中文技术会议 ASR 评测工具
    asr_runtime/           本地 ASR runtime、EvidenceSpan、demo gate
  data/                    评测数据、样本、标注、reference
    asr_eval/
      samples/             音频样本
      manifests/           样本清单
      references/          人工参考转写
      annotations/         人工结构化标注
      glossaries/          技术术语和 ASR/normalizer 词库
    web_mvp/
      fixtures/            PC Local Web MVP demo/replay 样本，不包含密钥或真实本地录音路径
  configs/                 provider 配置、mock 输入
  results/                 bake-off 输出结果
  artifacts/               临时产物、导出物、后续可清理文件
  tools/                   本地开发、验证和质量门禁脚本
  tests/                   跨模块工具脚本测试
```

## 分类原则

- `docs/` 只放设计、需求、评审和说明。
- `code/` 只放可执行代码和测试。
- `data/` 放输入数据，真实音频不提交公共仓库。
- `configs/` 放 provider 和运行配置，真实 API key 不提交。
- `results/` 放可复现评测结果。
- `artifacts/` 放临时人工产物或导出物。
- `tools/` 放不属于单一业务包的本地工程脚本，例如统一质量门禁。
- `tests/` 放跨模块工程脚本测试；业务模块测试仍放在各自 `code/*/tests` 下。

## 当前代码模块

`code/core` 是平台无关 Copilot core。

目标职责：

- 会议快照聚合。
- EvidenceSpan 引用检查。
- Transcript/Evidence/StreamingEvent/SuggestionCard/StateEvent/Degradation 的 versioned contract。
- 状态驱动建议卡片 gate。
- 实时卡片 30 秒窗口 gate。
- LLM 调度与成本追踪字段 gate。
- 降级状态下不得输出强建议的 gate。
- 建议卡片状态。
- 报告导出模型。
- 后续被 Web MVP、Mac desktop shell、Windows adapter 复用。

`code/web_mvp` 是本地 PC Web MVP。

目标职责：

- 本地 API 和 Web UI。
- 静态 Web 工作台：状态板、建议卡片、质量指标、证据面板、转写面板、Markdown 报告。
- 会话创建、读取、卡片操作、删除。
- demo fixture 列表与一键创建 session。
- demo fixture evaluation summary，验证工程正例多 gap 覆盖和非工程负例 0 卡片。
- 展示 replay snapshot timeline，并通过 EventSource 消费 Live Mock / Live ASR 本地事件源。
- 不直接耦合 macOS/Windows 平台能力。

当前边界：

- demo fixture endpoint 只证明结构链路和 core gate，不证明真实桌面 ASR、系统音频采集或真实 provider endpoint final 质量。
- 当前 Web 工作台已能用浏览器 EventSource 消费有限本地 SSE：Live Mock fixture stream 和 synthetic Live ASR stream；它仍不是桌面真实音频采集或真实模型实时流。
- Web backend 已有 mock live event source skeleton：`/live/mock/fixtures/{fixture_id}/sessions` 和 `/live/sessions/{session_id}/events(.sse)` 输出 `source=live_mock_stream`、`trace_kind=live_event` 的 live envelope。它只证明 API 契约和 SSE 形状，不证明真实 ASR、真实 scheduler 或桌面音频采集。
- Web backend 已有本地 ASR event source skeleton：`/live/asr/mock/sessions` 和 `/live/asr/sessions/{session_id}/events(.sse)` 输出 `source=live_asr_stream`、`trace_kind=live_event` 的 live envelope。它只接受 synthetic streaming event JSON，不读取音频文件、不加载模型、不调用远程 provider；当前可用本地确定性规则从 `final/revision` 生成 `DecisionCandidate`、`ActionItem`、`Risk` 和 `OpenQuestion`，并输出 no-LLM scheduler decision log（queued/skipped/cooldown/budget/not_called）、no-LLM `suggestion_candidate_event`（gap rule、prompt、evidence、candidate quality confidence/degradation metadata、`card_status=not_created`）和 no-LLM `llm_request_draft_event`（future request linkage、`draft_only`、`not_generated`、`not_called`）。设置本地 `data_dir` 时，Live ASR JSON audit record 会写入 `live_asr_sessions/` 并可跨 app 实例读回、随 `DELETE /sessions/{id}` 删除；`/live/asr/sessions/{id}/suggestion-candidates` 可只读查询候选队列；`/live/asr/sessions/{id}/llm-request-drafts` 可只读查询 future LLM request draft 队列；`PCWEB-060` `/live/asr/sessions/{id}/llm-openai-request-body-previews` 可只读查询从 request drafts 派生的 OpenAI-compatible chat-completions body preview，包含 messages、response_format schema target、metadata 和幂等键，但仍不读取 provider config、base URL、API key、Authorization header、环境密钥、keychain 或 `configs/local/`，不调用中转站、不估算 token、不生成正式 schema/card；`PCWEB-061` 在该 preview 输出层应用 `local_sensitive_draft_value_guard.v1`，将 draft payload 中高风险 marker 替换为 `[redacted:sensitive_draft_value]` 并返回 redaction audit，同时保持原始 `/events` 和 `/llm-request-drafts` 不变；`PCWEB-062` 在同一 preview 的 `response_format.json_schema` 中返回 outline-only `SuggestionCardV1` 字段合同，供后续 enabled executor、schema validator 和 card lifecycle 对齐，但仍不生成完整 JSON Schema、不做 schema validation、不创建正式建议卡；`PCWEB-063` `POST /live/asr/sessions/{id}/llm-schema-validation-dry-runs` 可对 caller-provided candidate response 做本地 schema validation dry-run，返回 passed/failed 和 validation errors，但仍不生成真实 `llm_schema_result`、不创建卡片、不改变 audit record、不调用中转站；`PCWEB-064` `POST /live/asr/sessions/{id}/llm-card-creation-policy-dry-runs` 可对 schema-shaped candidate response 做本地成卡资格策略校验，返回 allowed/blocked 和 policy errors，但仍不生成真实 `llm_schema_result`、不创建 `suggestion_card`/`suggestion_silenced`、不改变 audit record、不调用中转站；`/live/asr/sessions/{id}/llm-execution-previews` 可只读查询 future LLM execution preview 队列；`POST /live/asr/sessions/{id}/llm-execution-runs` 当前仅支持 `mode=disabled` 并返回 skipped run envelopes，不读取密钥、不执行请求、不改变 audit record；`/live/asr/sessions/{id}/llm-card-lifecycle-append-runs` 当前仅支持 `mode=disabled`，把 append preflight plan 映射成 skipped append run envelopes，不读取密钥、不执行请求、不写 idempotency store、不改变 audit record；`/live/asr/sessions/{id}/llm-card-lifecycle-append-repository-dry-runs` 当前仅支持 `mode=dry_run_only`，把 skipped append runs 映射成 repository result envelopes，不读取密钥、不执行请求、不写 repository transaction、不写 idempotency store、不改变 audit record；`/live/asr/sessions/{id}/llm-card-lifecycle-append-transaction-runs` 当前仅支持 `mode=disabled`，把 repository result envelopes 映射成 skipped transaction run envelopes，不读取密钥、不执行请求、不 commit repository transaction、不写 idempotency store、不改变 audit record；`/live/asr/sessions/{id}/llm-provider-readiness` 可只读报告 future OpenAI-compatible provider readiness 当前 `not_ready`，但不读取本地配置、环境 secret 或 API key；`/live/asr/sessions/{id}/llm-provider-config-boundary` 可只读报告 future provider config 字段分类和展示策略，允许 `api_key` 作为字段名元数据出现但不得返回真实值、masked value、presence/validity/length/hash/prefix/suffix/fingerprint 或 raw config；`/live/asr/sessions/{id}/llm-provider-masked-status` 可只读报告 future masked provider status envelope，display values 全部为 null，仍不得读取或返回真实 provider config；`POST /live/asr/sessions/{id}/llm-provider-config-validation` 只校验 caller-provided provider config draft，返回 base URL origin、model、timeout、CA basename 等 safe derived display values，并保持 `api_key=null`、`safe_to_execute=false`，不读取本地配置/环境 secret、不改变 audit record、不调用 LLM；`POST /live/asr/sessions/{id}/llm-provider-config-loader-preflight` 只校验 future config loader request shape 和授权意图，拒绝 URL/file URL、NUL/control characters、路径穿越和重复字段，且不返回 raw path、basename、parent directory 或 path-derived label；`PCWEB-057` `/live/asr/sessions/{id}/llm-provider-secret-storage-policy` 可只读报告 future secret storage policy，推荐 secret reference 类型并列出禁止存储位置，仍不读取 keychain、环境密钥或本地配置；`PCWEB-058` `POST /live/asr/sessions/{id}/llm-provider-config-reader-dry-run` 可校验 future authorized config reader request shape，要求 secret reference 和 dry-run 授权，但仍不读取本地配置、环境 secret、keychain 或 secret adapter，不返回 raw path、basename、parent directory、path-derived label、secret reference id 或文件状态侧信道；`PCWEB-059` `POST /live/asr/sessions/{id}/llm-provider-masked-status-loader-dry-run` 可校验 future authorized masked status loader request shape、requested display fields、secret reference 和 dry-run 授权，但仍不读取本地配置、环境 secret、keychain 或 secret adapter，不推断 status value，不返回路径/secret reference/文件状态侧信道或 API key 状态侧信道；`/live/asr/sessions/{id}/draft(.md)` 可从该 audit record 生成包含 LLM request drafts 的本地复盘草稿，Web 工作台会在 Live ASR terminal summary 后展示该草稿并避免 formal report path，但这不是正式 state engine、正式报告、真实 scheduler、真实 LLM executor、真实 provider config loader、真实 secret storage adapter、真实 event append repository、正式建议卡、录音或音频 chunk 生命周期。
- `PCWEB-065`、`PCWEB-066`、`PCWEB-067`、`PCWEB-068`、`PCWEB-069`、`PCWEB-070`、`PCWEB-071`、`PCWEB-072`、`PCWEB-073`、`PCWEB-074`、`PCWEB-075`、`PCWEB-076` 和 `PCWEB-077` 把 Live ASR LLM card lifecycle 继续保持在本地禁用边界：PCWEB-065 预览 future `llm_schema_result`、`suggestion_card` 或 `suggestion_silenced` 事件形状，PCWEB-066 预检 future event id、幂等键和 would-append sequence，PCWEB-067 把 append plan 映射成 skipped append runs，PCWEB-068 把 skipped runs 映射成 repository result envelopes，PCWEB-069 把 repository results 映射成 skipped transaction run envelopes，PCWEB-070 把 transaction runs 映射成 response-only append result audit event previews，PCWEB-071 把 audit preview items 映射成 response-only retry/replay conflict checks，并要求安全重放匹配 request/draft/card identity、重复 append idempotency evidence 必须阻断，PCWEB-072 把 preview/preflight 映射成 canonical future persisted event objects，PCWEB-073 把 serialized events 映射成 response-only mutation preflight checks，PCWEB-074 把 mutation/retry checks 映射成 response-only transaction commit readiness checks，PCWEB-075 把 commit readiness checks 映射成 response-only idempotency store write checks，PCWEB-076 把 idempotency store write checks 映射成 response-only append result audit event persistence checks，PCWEB-077 通过 `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries` 把 PCWEB-065..076 的 source status 压缩成 response-only card lifecycle readiness summary。十三者都不得写入 Live ASR audit record、不得 begin/commit/rollback repository transaction、不得写入 append result audit event 或 idempotency store、不得读取 provider config/secret、不得调用中转站。
- `PCWEB-078` 是前端 consumption 层：`frontend_static/index.html` 增加 `card-lifecycle-readiness-panel`，`frontend_static/app.js` 在 Live ASR terminal summary 后用 local contract probe 调用 PCWEB-077 并渲染 12 phase readiness；它不新增 backend write path、不创建正式建议卡、不读取配置/密钥、不调用中转站。
- `PCWEB-079` 是桌面壳准备度边界：`app.py` 暴露 `GET /desktop/shell-readiness`，`frontend_static/index.html` 增加 `desktop-readiness-panel`，`frontend_static/app.js` 在工作台启动时渲染 8 phase desktop readiness、blockers、next decisions 和 `desktop_safe_to_capture_audio=false` 等禁用 flags；它不接入麦克风/系统音频、不请求权限、不访问 native audio API、不启动 ASR worker、不读配置/密钥、不写本地或浏览器存储、不调用远程 ASR/LLM。
- `PCWEB-080` 是桌面运行时决策边界：`app.py` 暴露 `GET /desktop/runtime-boundary`，`frontend_static/index.html` 增加 `desktop-runtime-boundary-panel`，`frontend_static/app.js` 在工作台启动和 reset 时渲染 8 phase desktop runtime boundary、`recommended_desktop_runtime=tauri_first_electron_fallback`、`asr_worker_process_model=sidecar_worker_planned`、blockers、next decisions 和 `desktop_runtime_safe_to_create_shell=false` 等禁用 flags；它不创建 Tauri/Electron 项目或依赖、不启动 native bridge、不 spawn worker、不接入麦克风/系统音频、不请求权限、不访问 native runtime/process/audio API、不读配置/密钥、不写本地或浏览器存储、不调用远程 ASR/LLM。
- `PCWEB-081` 是桌面原生桥命令契约边界：`app.py` 暴露 `GET /desktop/native-bridge-contract`，`frontend_static/index.html` 增加 `desktop-native-bridge-contract-panel`，`frontend_static/app.js` 在工作台启动和 reset 时渲染 8 command bridge catalog、8 phase bridge contract、error/resource policy、blockers、next decisions 和 `desktop_bridge_safe_to_create_native_bridge=false` 等禁用 flags；`docs/pcweb-081-desktop-native-bridge-contract-plan.md` 和 `docs/superpowers/plans/2026-07-02-pcweb-081-desktop-native-bridge-contract.md` 记录该 contract。它不创建 `src-tauri`、`Cargo.toml`、`package.json`、lock files 或 desktop scaffold，不绑定 IPC/native bridge、不 spawn worker、不枚举设备、不捕获音频、不读配置/密钥、不写本地或浏览器存储、不调用远程 ASR/LLM。PCWEB-081 后下一桌面增量必须是 `create_tauri_shell_scaffold_against_bridge_contract`。
- `PCWEB-082` 是第一个桌面 Tauri shell scaffold spike：`code/desktop_tauri/src-tauri` 包含 `Cargo.toml`、`build.rs`、`tauri.conf.json`、`capabilities/default.json`、`src/main.rs` 和 `src/lib.rs`。该 scaffold 指向现有 Web MVP static assets 和手动启动的 local backend，绑定 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个 no-op command，并返回 `noop_bound` 与 `safe_to_execute_real_action=false`。它仍不创建 `Cargo.lock`、`package.json`、dependency lock files、`node_modules`、`target`、installer/signing/notarization artifacts，不运行 cargo/npm/Tauri CLI，不绑定音频命令、不请求权限、不枚举设备、不捕获音频、不启动 worker、不读取 `configs/local` 或密钥、不写 runtime/session/audio 数据、不调用远程 ASR/LLM。
- `PCWEB-083` 是桌面 build readiness policy 边界：`code/desktop_tauri/build-readiness.policy.json` 记录 `toolchain_version_probe_only`、`safe_to_run_cargo_check_now=false`、允许/禁止命令、禁止默认产物和未来 cargo check preconditions；`tools/desktop_build_readiness.py` 默认只读 policy，显式 probe 也只运行 `rustc --version` 与 `cargo --version`，且 custom policy 不能扩大工具层白名单。它仍不运行 cargo check/build、Tauri dev/build、package manager 或 npm/pnpm/yarn/npx launcher，不生成 lock/build artifacts，不接音频/权限/worker/密钥/远程调用。
- `PCWEB-084` 是桌面 cargo check artifact policy 边界：`code/desktop_tauri/cargo-check.policy.json` 记录未来 first approved `cargo check`、repeat `--locked --offline` check、`CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`、Cargo.lock generation/commit policy、network fetch policy、cleanup policy 和 schema validation；`tools/desktop_cargo_check_policy.py` 默认只读 policy 和 artifact path existence，并保持 `safe_to_run_cargo_check_now=false`。它仍不安装 Rust、不运行 cargo check/build、Tauri dev/build、package manager 或 npm/pnpm/yarn/npx launcher，不生成 lock/target/build artifacts，不接音频/权限/worker/密钥/远程调用。
- `PCWEB-085` 是桌面 Rust toolchain readiness policy 边界：`code/desktop_tauri/rust-toolchain-readiness.policy.json` 记录 `local_version_and_platform_probe_only`、`safe_to_install_toolchain_now=false`、允许探针、禁止安装/构建命令、xcode path presence-only 脱敏和 first cargo-check blockers；`tools/desktop_rust_toolchain_readiness.py` 默认不执行命令，显式 probe 只运行硬白名单并保持 `safe_to_run_cargo_check_now=false`。它仍不安装 Rust、不改 shell profile、不运行 cargo/Tauri/package manager、不生成 artifacts、不接音频/权限/worker/密钥/远程调用。
- `PCWEB-086` 是桌面 Rust toolchain installation decision policy 边界：`code/desktop_tauri/rust-toolchain-installation.policy.json` 记录 `no_install_decision_report_only`、`recommended_install_provider=official_rustup`、approval tokens、platform notes、post-install verification order 和 `safe_to_install_toolchain_now=false`；`tools/desktop_rust_toolchain_installation_decision.py` 默认只读 policy，不包含 command execution path。它仍不运行 curl/sh/rustup/cargo/package manager、不改 shell profile、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程调用。
- `PCWEB-087` 是桌面 Rust toolchain install approval packet 边界：`code/desktop_tauri/rust-toolchain-install-approval.policy.json` 记录 `manual_user_run_only`、官方 Rust/rustup/Tauri source URLs、inert manual instruction text、approval tokens、risk notes、rollback notes、post-install verification order 和 `safe_to_execute_install_now=false`；`tools/desktop_rust_toolchain_install_approval_packet.py` 默认只读 policy，不包含 command execution path。它仍不执行 manual text、不运行 installer/curl/sh/rustup/cargo/package manager、不改 shell profile/PATH、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程调用。
- `PCWEB-088` 是桌面 Rust post-install probe approval 边界：`code/desktop_tauri/rust-post-install-probe-approval.policy.json` 记录 `no_probe_execution_approval_packet_only`、future probe allowlist、redaction requirements、approval tokens、expected result schema、cargo-check blockers 和 `safe_to_run_post_install_probe_now=false`；`tools/desktop_rust_post_install_probe_approval.py` 默认只读 policy，不包含 command execution path。它仍不运行 rustc/cargo/rustup/xcode-select/cargo check/Tauri/package manager、不读 PATH/shell profile/cargo home/rustup home、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程调用。
- `PCWEB-089` 是桌面 Rust post-install probe result intake 边界：`code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` 记录 `manual_result_validation_only`、`caller_provided_json_only`、bounded result fields/status enums、forbidden raw result fields 和 `safe_to_accept_raw_probe_output_now=false`；`tools/desktop_rust_post_install_probe_result_intake.py` 默认只读 policy 和可选 caller-provided JSON，不包含 command execution path。它仍不运行 rustc/cargo/rustup/xcode-select/cargo check/Tauri/package manager、不接受 raw probe output、不读 PATH/shell profile/cargo home/rustup home、不抓依赖、不生成 artifacts、不接音频/权限/worker/密钥/远程调用，并保持 `safe_to_run_cargo_check_now=false` 与 `blocked_until_pcweb_084_and_user_approval`。
- `PCWEB-090` 是桌面 first cargo check execution boundary：`code/desktop_tauri/first-cargo-check-execution.policy.json` 记录 `explicit_manual_execution_packet_only`、PCWEB-084 first cargo check command/env/artifacts、PCWEB-089 bounded result source、required preconditions 和 false safety flags；`tools/desktop_first_cargo_check_execution_boundary.py` 默认只读 policy、PCWEB-084 policy 和可选 PCWEB-089 bounded result，不包含 command execution path。它最多生成 `ready_for_explicit_user_approval` 手动执行包，仍不运行 cargo check/build、Tauri/package manager、不抓依赖、不生成 `Cargo.lock`/target、不接音频/权限/worker/密钥/远程调用。
- repository 默认是 in-memory；显式设置 `MEETING_COPILOT_DATA_DIR` 或 app factory `data_dir` 时使用 JSON session repository，覆盖 PC-1 session/status 持久化和删除语义。
- fixture evaluation 只用于 PC-1 replay/value gate，不替代真实会议人工质量评审。

`tools/run_quality_gate.py` 是跨模块本地质量门禁入口。

当前支持：

- `--profile pc-web`：运行根目录 scaffold contract tests、core 测试、Web backend 测试和浏览器 E2E smoke。
- `--profile all-local`：在 `pc-web` 基础上增加 ASR runtime 和 ASR bake-off 工具测试。
- `--dry-run`：打印命令，不执行。
- `--no-browser`：跳过 Chrome/CDP 浏览器 smoke，用于无 Chrome 环境的临时诊断。

边界：

- 默认不调用远程 ASR、LLM 中转站或 `configs/local/`。
- 这是本地验证编排脚本，不替代后续 CI、截图回归或真实 live event source 验收。

`code/asr_bakeoff` 是中文技术会议 ASR 评测工作台。

当前支持：

- 样本 manifest 校验。
- reference/annotation 加载。
- 中文 CER 计算。
- 技术实体 precision/recall/F1。
- 延迟 P50/P95/max。
- mock provider 端到端评测。
- command provider 外部命令适配。
- 样本级 provider 失败隔离。
- 缺 reference/annotation 时标记不可评测，不计入平均分。

后续扩展：

- 阿里 Paraformer provider。
- 讯飞实时转写 provider。
- 腾讯实时语音识别 provider。
- 百度实时语音识别 provider。
- OpenAI Realtime transcription provider。
- FunASR/SenseVoice 自托管 provider。
- sherpa-onnx 本地 provider。

`code/asr_runtime` 是本地运行和最小 demo 验证工作台。

当前支持：

- WAV 音频元数据检查。
- sherpa-onnx 文件/伪实时转写。
- FunASR 文件转写。
- Provider JSON -> TranscriptReport。
- raw transcript + normalized transcript。
- EvidenceSpan。
- LLM meeting analysis。
- MeetingStateEvent。
- Demo pipeline。
- Demo evaluation gate。

## 文档模块

当前产品文档分为：

- `product-requirements.md`：产品定位、MVP、用户旅程、成功指标。
- `mac-mvp-requirements-and-technical-plan.md`：Mac 优先 MVP 需求与技术方案。
- `feature-map.md`：功能地图与阶段划分。
- `meeting-scenarios.md`：中文技术会议场景模板。
- `realtime-suggestion-cards.md`：实时建议卡片规范。
- `meeting-state-model.md`：会议状态模型。
- `chinese-technical-language.md`：中文技术语义与术语规范。
- `asr-provider-strategy.md`：ASR provider 策略、成本边界和实现路线。
- `asr-bakeoff-guide.md`：ASR bake-off 使用说明。
- `asr-evaluation-dataset.md`：ASR 评测集规范。
- `llm-quality-evaluation.md`：LLM 抽取、建议、纪要评测。
- `privacy-and-data-flow.md`：隐私、安全和数据流。
- `failure-and-degradation.md`：失败与降级策略。
- `implementation-roadmap.md`：SDD/TDD 实施路线图。
- `requirements-traceability-matrix.md`：需求 ID、验收、测试和结果文件追踪矩阵。
- `decision-log.md`：重要产品、技术、成本、隐私和阶段决策记录。
- `platform-packaging-and-store-compliance.md`：PC 跨平台客户端、打包签名、安装分发、iOS/Android 上架费用与合规风险规划。
- `pc-local-web-mvp-requirements.md`：PC-1 本地 Web MVP 需求。
- `pc-local-web-mvp-acceptance.md`：PC-1 验收清单。
- `pc-local-web-mvp-plan.md`：PC-1 实施计划。
- `pcweb-037-live-asr-stream-plan.md`：本地 `live_asr_stream` 骨架计划，用于把 Web live envelope 从 fixture mock 推进到可接 ASR worker 的本地事件源。
- `pcweb-038-live-asr-state-scheduler-plan.md`：Live ASR 本地状态/调度骨架计划，用于证明 ASR final/revision 能触发证据化状态候选和 no-LLM scheduler placeholder。
- `pcweb-039-live-asr-scheduler-log-plan.md`：Live ASR 本地 scheduler decision log 计划，用于证明 queued/skipped/cooldown/budget/no-call 调度审计契约。
- `pcweb-040-live-asr-state-extraction-plan.md`：Live ASR 本地状态抽取契约计划，用于证明 ASR final/revision 能触发 `DecisionCandidate` 和 `OpenQuestion` 两类证据化状态。
- `pcweb-041-live-asr-audit-persistence-plan.md`：Live ASR JSON audit record 持久化计划，用于证明本地 ASR live event stream 可跨 app 实例读回并随会话删除。
- `pcweb-042-live-asr-draft-review-plan.md`：Live ASR draft review 计划，用于从 audit record 生成非正式 JSON/Markdown 复盘草稿。
- `pcweb-043-live-asr-draft-ui-plan.md`：Live ASR draft review UI 计划，用于把非正式复盘草稿展示到 Web 工作台 report panel，同时保持 formal report path 关闭。
- `pcweb-044-live-asr-action-risk-state-plan.md`：Live ASR Action/Risk state skeleton 计划，用于让本地 ASR live 状态抽取覆盖四类 PC-1 状态 lane。
- `pcweb-045-live-asr-suggestion-candidate-plan.md`：Live ASR no-LLM suggestion candidate queue 计划，用于把本地状态候选推进到可审计的未来建议候选，但不生成正式建议卡或调用 LLM。
- `pcweb-046-live-asr-candidate-confidence-plan.md`：Live ASR candidate quality metadata 计划，用于给 no-LLM suggestion candidate 增加本地 deterministic confidence/degradation 审计字段，但不生成正式建议卡或调用 LLM。
- `pcweb-047-live-asr-candidate-query-plan.md`：Live ASR candidate query 计划，用于只读查询 no-LLM suggestion candidate 队列，供后续 scheduler/card engine 使用，但不排序、过滤、生成正式建议卡或调用 LLM。
- `pcweb-048-live-asr-llm-request-draft-plan.md`：Live ASR LLM request draft 计划，用于在 no-LLM suggestion candidate 后追加本地 request draft 审计，说明未来 LLM 请求将携带的上下文，但不调用中转站、不生成 schema 或正式建议卡。
- `pcweb-049-live-asr-llm-request-draft-query-plan.md`：Live ASR LLM request draft query 计划，用于只读查询 no-LLM request draft 队列，供后续 LLM executor 使用，但不执行请求、不估算 token、不生成 schema 或正式建议卡。
- `pcweb-050-live-asr-llm-execution-preview-plan.md`：Live ASR LLM execution preview 计划，用于从 request draft 派生只读执行预检 envelope，供后续真实 LLM executor 使用，但不读取密钥、不调用中转站、不估算 token、不生成 schema 或正式建议卡。
- `pcweb-058-live-asr-llm-provider-config-reader-dry-run-plan.md`：Live ASR LLM provider config reader dry-run 计划，用于校验 future authorized config reader 请求、secret reference 和显式 dry-run 授权，但不读取配置文件、密钥、keychain 或 secret adapter，不返回路径/secret reference/文件状态侧信道。
- `pcweb-059-live-asr-llm-provider-masked-status-loader-dry-run-plan.md`：Live ASR LLM provider masked status loader dry-run 计划，用于校验 future authorized masked status loader 请求、requested display fields、secret reference 和显式 dry-run 授权，但不读取配置文件、密钥、keychain 或 secret adapter，不推断 status value，不返回路径/secret reference/文件状态侧信道或 API key 状态侧信道。
- `pcweb-060-live-asr-openai-request-body-preview-plan.md`：Live ASR OpenAI-compatible request body preview 计划，用于从 no-LLM request draft 派生 deterministic chat-completions messages、response_format schema target、metadata 和幂等键，但不读取 provider config、base URL、API key、Authorization header、环境密钥、keychain 或 `configs/local/`，不调用中转站。
- `pcweb-061-live-asr-openai-request-body-preview-redaction-guard-plan.md`：Live ASR OpenAI request body preview redaction guard 计划，用于阻断 request draft payload 中高风险 marker 被反射进 preview messages/metadata，并保持原始 request drafts/events 不变。
- `pcweb-062-live-asr-openai-request-body-schema-outline-preview-plan.md`：Live ASR OpenAI request body schema outline preview 计划，用于在 request body preview 的 `response_format.json_schema` 中暴露 outline-only `SuggestionCardV1` 字段合同，但不生成完整 JSON Schema、不校验模型响应、不调用中转站。
- `pcweb-063-live-asr-llm-schema-validation-dry-run-plan.md`：Live ASR LLM schema validation dry-run 计划，用于校验 caller-provided candidate response 的本地 `SuggestionCardV1` 合同和 dry-run gate 子集，但不生成真实 schema result、不创建卡片、不调用中转站。
- `pcweb-065-live-asr-card-lifecycle-preview-dry-run-plan.md`：Live ASR card lifecycle preview dry-run 计划，用于在 schema/policy dry-run 后预览 future `llm_schema_result`、`suggestion_card` 或 `suggestion_silenced` 生命周期事件，但不写入 audit record、不创建真实卡片、不调用中转站。
- `pcweb-066-live-asr-card-lifecycle-append-preflight-dry-run-plan.md`：Live ASR card lifecycle append preflight dry-run 计划，用于预检 future lifecycle event id、幂等键和 would-append sequence，但不写入 audit record、不写入 idempotency store、不调用中转站。
- `pcweb-067-live-asr-card-lifecycle-append-disabled-run-plan.md`：Live ASR card lifecycle append disabled run 计划，用于把 append preflight plan 映射成 skipped append run envelopes，但不写入 audit record、不写入 idempotency store、不调用中转站。
- `pcweb-068-live-asr-card-lifecycle-append-repository-dry-run-plan.md`：Live ASR card lifecycle append repository dry-run 计划，用于把 skipped append runs 映射成 future repository result envelopes，但不写入 audit record、不写 repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-069-live-asr-card-lifecycle-append-transaction-disabled-run-plan.md`：Live ASR card lifecycle append transaction disabled run 计划，用于把 repository result envelopes 映射成 skipped transaction run envelopes，但不写入 audit record、不 commit repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-070-live-asr-card-lifecycle-append-result-audit-preview-plan.md`：Live ASR card lifecycle append result audit preview 计划，用于把 skipped transaction run envelopes 映射成 response-only append result audit event previews，但不写入 audit record、不 commit repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-071-live-asr-card-lifecycle-retry-replay-preflight-plan.md`：Live ASR card lifecycle retry/replay preflight 计划，用于把 append result audit preview items 映射成 response-only retry/replay conflict checks，但不写入 audit record、不 commit repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-072-live-asr-card-lifecycle-append-event-serializer-dry-run-plan.md`：Live ASR card lifecycle append event serializer dry-run 计划，用于把 lifecycle preview events 和 append preflight plan 映射成 canonical future persisted event objects，但不写入 audit record、不 commit repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-073-live-asr-card-lifecycle-append-mutation-preflight-plan.md`：Live ASR card lifecycle append mutation preflight 计划，用于把 PCWEB-072 canonical serialized events 映射成 response-only mutation eligibility checks，但不写入 audit record、不开启或 commit repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-074-live-asr-card-lifecycle-append-transaction-commit-preflight-plan.md`：Live ASR card lifecycle append transaction commit preflight 计划，用于把 PCWEB-073 mutation checks 和 PCWEB-071 retry/replay checks 映射成 response-only commit readiness checks，但不写入 audit record、不开启/commit/rollback repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-075-live-asr-card-lifecycle-append-idempotency-store-write-preflight-plan.md`：Live ASR card lifecycle append idempotency store write preflight 计划，用于把 PCWEB-074 commit readiness checks 映射成 response-only idempotency-store write checks，但不写入 audit record、不开启/commit/rollback repository transaction、不写入 idempotency store、不调用中转站。
- `pcweb-076-live-asr-card-lifecycle-append-result-audit-event-persistence-preflight-plan.md`：Live ASR card lifecycle append result audit event persistence preflight 计划，用于把 PCWEB-075 idempotency-store checks 映射成 response-only append result audit event persistence checks，但不写入 audit record、append result audit event、idempotency store 或 repository transaction。
- `pcweb-077-live-asr-card-lifecycle-readiness-summary-plan.md`：Live ASR card lifecycle readiness summary 计划，用于把 PCWEB-065..076 source status 压缩成 UI 可消费的 readiness/blocker/phase/next-decision summary，但不写入 audit record、不创建卡片、不读取配置/密钥、不调用中转站。
- `pcweb-078-live-asr-card-lifecycle-readiness-ui-plan.md`：Live ASR card lifecycle readiness UI 计划，用于把 PCWEB-077 response-only summary 渲染到 Web 工作台 `card-lifecycle-readiness-panel`，但不创建正式建议卡、不写入 audit record、不读取配置/密钥、不调用中转站。
- `pcweb-079-desktop-shell-readiness-boundary-plan.md`：Desktop shell readiness boundary 计划，用于在进入 Mac-first 桌面壳前通过 `/desktop/shell-readiness` 和 `desktop-readiness-panel` 展示 no-capture/no-permission/no-worker/no-paid-call 的 readiness 边界。
- `pcweb-080-desktop-runtime-boundary-plan.md`：Desktop runtime decision boundary 计划，用于在创建真实桌面壳前通过 `/desktop/runtime-boundary` 和 `desktop-runtime-boundary-panel` 展示 Tauri-first/Electron-fallback、ASR sidecar worker、Mac-first process model 和 no-dependency/no-runtime/no-capture/no-paid-call 的决策边界。
- `pcweb-081-desktop-native-bridge-contract-plan.md`：Desktop native bridge command contract 计划，用于在创建真实 Tauri shell scaffold 前通过 `/desktop/native-bridge-contract` 和 `desktop-native-bridge-contract-panel` 展示 8 command bridge catalog、8 phase bridge contract、error/resource policy 和 no-bridge/no-IPC/no-worker/no-capture/no-paid-call 的契约边界；下一桌面增量必须是 `create_tauri_shell_scaffold_against_bridge_contract`。
- `pcweb-082-tauri-shell-scaffold-spike-plan.md`：Tauri shell scaffold spike 计划，用于创建 `code/desktop_tauri/src-tauri` 静态 scaffold、`tauri.conf.json`、最小 capability 和 `runtime_get_status` / `session_prepare` / `asr_worker_health` 三个 no-op command；仍不运行/构建/打包、不创建 `Cargo.lock` 或 `package.json`、不接音频/权限/worker/密钥/付费调用。
- `pcweb-083-desktop-build-readiness-policy-plan.md`：Desktop build readiness policy 计划，用于通过 `build-readiness.policy.json` 和 `desktop_build_readiness.py` 定义 no-build readiness report、`toolchain_version_probe_only` 和 `safe_to_run_cargo_check_now=false`；未来 cargo check 仍需先确认 lockfile、target dir、network fetch、cleanup 和 no-audio/no-secret/no-remote preconditions。
- `pcweb-084-desktop-cargo-check-artifact-policy-plan.md`：Desktop cargo check artifact policy 计划，用于通过 `cargo-check.policy.json` 和 `desktop_cargo_check_policy.py` 定义未来首次 cargo check 的 Cargo.lock、`CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`、network fetch、cleanup、schema validation 和 no-run report 边界，保持 `safe_to_run_cargo_check_now=false`。
- `pcweb-085-desktop-rust-toolchain-readiness-plan.md`：Desktop Rust toolchain readiness 计划，用于通过 `rust-toolchain-readiness.policy.json` 和 `desktop_rust_toolchain_readiness.py` 定义 no-install/no-build toolchain readiness、`local_version_and_platform_probe_only`、xcode path 脱敏和 first cargo-check blockers，保持 `safe_to_install_toolchain_now=false`。
- `pcweb-086-desktop-rust-toolchain-installation-decision-plan.md`：Desktop Rust toolchain installation decision 计划，用于通过 `rust-toolchain-installation.policy.json` 和 `desktop_rust_toolchain_installation_decision.py` 定义 no-install/no-command install decision、`official_rustup` 推荐、approval tokens、platform notes 和 post-install verification order，保持 `safe_to_install_toolchain_now=false`。
- `pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md`：Desktop Rust toolchain install approval packet 计划，用于通过 `rust-toolchain-install-approval.policy.json` 和 `desktop_rust_toolchain_install_approval_packet.py` 定义 `manual_user_run_only` approval packet、inert manual instruction text、official source URLs、risk/rollback notes 和 post-install verification order，保持 `safe_to_execute_install_now=false`。
- `pcweb-088-desktop-rust-post-install-probe-approval-plan.md`：Desktop Rust post-install probe approval 计划，用于通过 `rust-post-install-probe-approval.policy.json` 和 `desktop_rust_post_install_probe_approval.py` 定义 `no_probe_execution_approval_packet_only`、future probe allowlist、redaction requirements、expected result schema 和 cargo-check blockers，保持 `safe_to_run_post_install_probe_now=false` 和 `safe_to_run_cargo_check_now=false`。
- `pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`：Desktop Rust post-install probe result intake 计划，用于通过 `rust-post-install-probe-result-intake.policy.json` 和 `desktop_rust_post_install_probe_result_intake.py` 定义 `manual_result_validation_only`、`caller_provided_json_only`、bounded result fields/status enums、forbidden raw result fields 和 cargo-check blockers，保持 `safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false` 和 `blocked_until_pcweb_084_and_user_approval`。
- `pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`：Desktop first cargo check execution boundary 计划，用于通过 `first-cargo-check-execution.policy.json` 和 `desktop_first_cargo_check_execution_boundary.py` 定义 `explicit_manual_execution_packet_only`、PCWEB-084 command/env/artifact policy、PCWEB-089 bounded result input 和 manual execution packet，保持 `safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。
- `pcweb-091-tauri-noop-shell-local-run-smoke-plan.md`：`PCWEB-091` Desktop Tauri no-op shell local run smoke 计划，用于通过 `tauri-noop-shell-run-smoke.policy.json` 和 `desktop_tauri_noop_shell_run_smoke.py` 定义 `readiness_report_only`、PCWEB-082 scaffold validation、PCWEB-090 no-command boundary validation 和 future manual smoke packet，最多返回 `ready_for_explicit_tauri_run_approval`，保持 `safe_to_run_tauri_dev_now=false`、`safe_to_run_cargo_check_now=false` 和 `safe_to_capture_audio_now=false`。
- `end-to-end-design-checklist.md`：全链路设计、阶段 gate、风险和下一阶段检查表。
- `multi-agent-design-review-2026-06-30.md`：多 Agent 对产品价值、技术架构、SDD/TDD 验收追踪的评审报告。
