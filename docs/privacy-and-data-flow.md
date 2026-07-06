# 隐私、安全与数据流

> 日期：2026-06-18  
> 目的：会议录音和实时 AI 产品必须先建立信任边界。

## 1. 基本原则

- 不隐蔽录音。
- 不默认全天监听。
- 不自动加入会议。
- 用户手动开启，状态持续可见。
- 用户可暂停、停止、删除。
- 所有云端调用必须可解释。
- 删除应覆盖音频、转写、中间结果和导出物。

## 2. 数据分类

本地数据：

- 原始音频。
- 音频分轨。
- raw transcript。
- normalized transcript。
- 会议状态对象。
- 建议卡片。
- 会后纪要。
- 本地词库。
- 本地 provider config path reference、配置文件路径派生信息和 provider 配置草稿。

发送到 ASR provider 的数据：

- 音频片段或流式音频。
- 语言、热词、采样率等非敏感配置。

发送到 LLM provider 的数据：

- 稳定 transcript segment。
- 会议状态摘要。
- 技术词库必要片段。
- 不发送原始音频，除非用户明确启用多模态音频模型。

不应发送：

- 未使用的历史会议全文。
- 本地文件或代码库内容，除非用户显式导入。
- API key。
- 本地 provider config path、basename、parent directory、path-derived label、raw config 或 authorization header。
- 与当前任务无关的个人数据。

## 3. 会前告知

产品应提供可复制文案：

```text
本次会议我会使用 Meeting Copilot 进行录音、实时转写和会议辅助。音频/转写可能会发送到配置的 ASR/LLM 服务用于识别和总结。会议结束后可删除录音和转写数据。
```

企业版可替换为组织统一告知模板。

## 4. 删除语义

删除一次会议应删除：

- 原始音频。
- 临时音频 chunk。
- raw transcript。
- normalized transcript。
- LLM 中间输出。
- 建议卡片。
- 结构化状态。
- 导出文件。
- 本地缓存。

不能保证删除：

- 第三方 ASR/LLM provider 已按其策略处理的数据。

因此产品必须在 provider 配置页说明数据保留策略链接或企业配置要求。

PC-1 当前实现边界：

- 默认开发模式仍可使用内存 repository，不落盘。
- 设置 `MEETING_COPILOT_DATA_DIR` 或 app factory `data_dir` 后，session snapshot、状态、建议卡片反馈和 fixture metadata 会写入本地 JSON：`<data_dir>/sessions/{session_id}.json`。
- `DELETE /sessions/{session_id}` 会删除对应 session JSON 文件，删除后 API 无法再读取该 session。
- session id 只允许安全字符，避免通过 `../` 等路径写出数据目录。
- `DEC-210` 已补齐 ignored local artifact retention/delete 边界：`tools/local_artifact_retention.py` 只允许 `artifacts/tmp/audio_health/`、`artifacts/tmp/mainline_selftests/`、`artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/`、`artifacts/tmp/real_mic_shadow_tests/`、`artifacts/tmp/real_mic_shadow_reports/`、`artifacts/tmp/asr_events/` 和 `artifacts/tmp/asr_reports/` 下的本地 artifact 进入 retention manifest 或显式 `--delete`。
- 当前 retention/delete 工具不读取 artifact 内容，只记录路径、存在性、大小和 action；`configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/` 和 repo 外路径会在删除前阻断。
- PC-1 暂未接入生产用户数据目录、真实会议长期存储和 provider 侧删除能力；这些会在 Mac desktop shell 数据生命周期里继续实现。

## 5. 密钥与配置

- API key 不写入评测 manifest。
- API key 不写入结果报告。
- 配置文件只保存 endpoint、model、region、timeout 等非敏感信息。
- 密钥读取优先使用系统 keychain、环境变量或企业 secret provider。
- 本地配置路径也按隐私数据处理：preflight 阶段只接受本地文件系统路径引用，拒绝 URL/file URL、NUL/control characters、POSIX `/../` 和 Windows `\..\` 路径穿越；响应、错误、日志、评测报告不得回显 raw path、basename、parent directory 或 path-derived label。
- `PCWEB-057` Secret storage policy 当前只返回模板：推荐 OS keychain、enterprise secret provider 或 development-only env var name reference；不得读取 keychain、环境变量密钥、本地配置或任何 secret adapter，也不得把 API key 写入 session JSON、Live ASR audit events、日志、报告、浏览器存储或仓库文件。
- `PCWEB-058` Provider config reader dry-run 当前只校验 future authorized reader 请求形状：允许提交本地 config path reference 和 secret reference，但必须保持 config read、secret read、LLM call 和 event mutation 全部关闭；不得读取配置文件、环境密钥、keychain 或 secret adapter，不得解析 secret reference id，不得检查文件存在性、可读性、大小、mtime、hash 或 fingerprint，也不得在响应、错误、日志、报告或浏览器存储中回显 raw path、basename、parent directory、path-derived label 或 secret reference id。
- `PCWEB-059` Provider masked status loader dry-run 当前只校验 future authorized masked status loader 请求形状：允许提交本地 config path reference、secret reference 和 requested display fields，但必须保持 config read、secret read、LLM call、event mutation 和 status value inference 全部关闭；display values 全部为 null，不得读取配置文件、环境密钥、keychain 或 secret adapter，不得解析 secret reference id，不得检查文件存在性、可读性、大小、mtime、hash 或 fingerprint，不得报告 API key value、masked key、presence、validity、length、hash、prefix、suffix 或 fingerprint，也不得在响应、错误、日志、报告或浏览器存储中回显 raw path、basename、parent directory、path-derived label 或 secret reference id。
- `PCWEB-060` OpenAI-compatible request body preview 当前只从 Live ASR no-LLM request drafts 派生 deterministic request body preview；不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得把 base URL、API key、Authorization header、bearer token、raw config、config path、API key presence/validity/length/hash/prefix/suffix/fingerprint 写入响应、错误、日志、报告、audit event、session JSON 或浏览器存储。
- `PCWEB-061` OpenAI request body preview redaction guard 当前只保护 preview 输出层：当 request draft 可反射字段含 API-key-like token、Bearer、Authorization、raw_config、api_key、base_url、config_path、`configs/local` 或 relay-domain marker 时，messages/metadata 中只返回 `[redacted:sensitive_draft_value]` 和 redaction audit；原始 `/events` 和 `/llm-request-drafts` 不被改写，以保留本地审计真实性。该 guard 不读取、验证、hash、mask、截取或解析真实 secret。
- `PCWEB-062` OpenAI request body schema outline preview 当前只在 `response_format.json_schema` 中返回本地 outline-only `SuggestionCardV1` 字段合同；它不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得生成 provider-specific 完整 JSON Schema、不得校验模型响应、不得创建正式建议卡或写入新的 audit event。
- `PCWEB-063` LLM schema validation dry-run 当前只校验 caller-provided `candidate_response`，并返回本地 validation status/error metadata；它不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得解析真实 chat-completions 响应、不得生成 `llm_schema_result`、不得创建正式建议卡、不得写入新的 audit event。
- `PCWEB-064` card creation policy dry-run 当前只校验 caller-provided `candidate_response` 与现有 request draft、evidence、state event、segment 和 timing 的本地策略关系；即使 policy 返回 allowed，也必须保持 `safe_to_create_card=false`，不得创建正式建议卡、不得生成 `llm_schema_result` 或 `suggestion_silenced`、不得写入新的 audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-065` card lifecycle preview dry-run 当前只预览 caller-provided `candidate_response` 在未来 enabled lifecycle 下会追加的 `llm_schema_result`、`suggestion_card` 或 `suggestion_silenced` 事件形状；preview events 只能出现在响应体中，必须保持 `safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得创建正式建议卡、不得生成真实 silenced record、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-066` card lifecycle append preflight dry-run 当前只预检 caller-provided `candidate_response` 的 future lifecycle events 是否能按 deterministic event id、幂等键和 would-append sequence 安全追加；append plan 只能出现在响应体中，必须保持 `safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得写入 idempotency store、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-067` card lifecycle append disabled run 当前只在 `mode=disabled` 下把 append preflight plan 映射成 skipped append run envelopes；append runs 只能出现在响应体中，必须保持 `event_append_status=not_appended`、`idempotency_store_status=not_written`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得写入 idempotency store、不得生成 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-068` card lifecycle append repository dry-run 当前只在 `mode=dry_run_only` 下把 skipped append runs 映射成 future repository result envelopes；repository results 只能出现在响应体中，必须保持 `repository_write_status=dry_run_only`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得开启 repository transaction、不得写入 Live ASR audit record、不得写入 idempotency store、不得生成 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-069` card lifecycle append transaction disabled run 当前只在 `mode=disabled` 下把 repository result envelopes 映射成 skipped transaction run envelopes；transaction runs 只能出现在响应体中，必须保持 `transaction_write_status=disabled`、`repository_transaction_status=disabled`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`safe_to_commit_transaction=false`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得开启或 commit repository transaction、不得写入 Live ASR audit record、不得写入 idempotency store、不得生成 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-070` card lifecycle append result audit preview 当前只在 `mode=preview_only` 下把 transaction run envelopes 映射成 response-only append result audit event previews；audit previews 只能出现在响应体中，必须保持 `append_result_audit_event_status=preview_only`、`audit_event_append_status=not_appended`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`safe_to_write_audit_events=false`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得开启或 commit repository transaction、不得写入 idempotency store、不得生成真实 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-071` card lifecycle retry/replay preflight 当前只在 `mode=preflight_only` 下把 append result audit preview items 映射成 response-only retry/replay conflict checks；安全重放必须匹配 request/draft/card identity，重复 append idempotency evidence 必须阻断。preflight checks 只能出现在响应体中，必须保持 `retry_replay_preflight_status=analyzed`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`safe_to_mutate_events=false`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得开启或 commit repository transaction、不得写入 idempotency store、不得生成真实 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-072` card lifecycle append event serializer dry-run 当前只在 `mode=dry_run_only` 下把 lifecycle preview events 和 append preflight plan 映射成 response-only canonical future lifecycle event objects；serialized events 只能出现在响应体中，必须保持 `append_event_serializer_status=serialized`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得开启或 commit repository transaction、不得写入 idempotency store、不得生成真实 lifecycle event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-073` card lifecycle append mutation preflight 当前只在 `mode=preflight_only` 下把 PCWEB-072 serialized events 映射成 response-only mutation preflight checks；checks 只能出现在响应体中，必须保持 `append_mutation_preflight_status=analyzed`、`repository_transaction_status=not_started`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`safe_to_mutate_events=false`、`safe_to_commit_transaction=false`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得开启或 commit repository transaction、不得写入 idempotency store、不得生成真实 lifecycle event 或 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-074` card lifecycle append transaction commit preflight 当前只在 `mode=preflight_only` 下把 PCWEB-073 mutation checks 和 PCWEB-071 retry/replay checks 映射成 response-only commit readiness checks；`safe_replay_existing_events` 只表示既有事件可解释为幂等重放，不表示允许新事务 commit。checks 只能出现在响应体中，必须保持 `append_transaction_commit_preflight_status=analyzed`、`repository_transaction_status=not_started`、`repository_transaction_commit_status=not_committed`、`repository_transaction_rollback_status=not_started`、`event_append_status=not_appended`、`audit_event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`safe_to_begin_transaction=false`、`safe_to_commit_transaction=false`、`safe_to_rollback_transaction=false`、`safe_to_mutate_events=false`、`safe_to_append_events=false`、`safe_to_write_idempotency_store=false`、`safe_to_write_audit_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得开启/commit/rollback repository transaction、不得写入 idempotency store、不得生成真实 lifecycle event 或 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-075` card lifecycle append idempotency store write preflight 当前只在 `mode=preflight_only` 下把 PCWEB-074 commit readiness checks 映射成 response-only idempotency store write checks；fresh append 只表示未来 enabled path 需要写 deterministic idempotency record，`safe_replay_existing_events` 表示不应再写新的 idempotency record。checks 只能出现在响应体中，必须保持 `idempotency_store_write_preflight_status=analyzed`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`repository_transaction_status=not_started`、`repository_transaction_commit_status=not_committed`、`repository_transaction_rollback_status=not_started`、`event_append_status=not_appended`、`audit_event_append_status=not_appended`、`safe_to_write_idempotency_store=false`、`safe_to_begin_transaction=false`、`safe_to_commit_transaction=false`、`safe_to_rollback_transaction=false`、`safe_to_mutate_events=false`、`safe_to_append_events=false`、`safe_to_write_audit_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、不得写入 idempotency store 或 marker、不得开启/commit/rollback repository transaction、不得生成真实 lifecycle event 或 append result audit event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-076` card lifecycle append result audit event persistence preflight endpoint `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights` 当前只在 `mode=preflight_only` 下把 PCWEB-075 idempotency store write checks 映射成 response-only append result audit event persistence checks；fresh append 只表示未来 enabled path 需要写 deterministic append result audit event，`safe_replay_existing_events` 表示不应再写新的 audit event。checks 只能出现在响应体中，必须携带 PCWEB-070 provenance（例如 `transaction_run_id`、`append_run_id`、`repository_result_id`、`audit_repository_transaction_status`），同时保持 `append_result_audit_event_persistence_preflight_status=analyzed`、`audit_event_append_status=not_appended`、`event_append_status=not_appended`、`idempotency_store_status=not_written`、`idempotency_store_write_status=not_written`、`repository_transaction_status=not_started`、`repository_transaction_commit_status=not_committed`、`repository_transaction_rollback_status=not_started`、`safe_to_persist_append_result_audit_event=false`、`safe_to_write_audit_events=false`、`safe_to_write_idempotency_store=false`、`safe_to_begin_transaction=false`、`safe_to_commit_transaction=false`、`safe_to_rollback_transaction=false`、`safe_to_mutate_events=false`、`safe_to_append_events=false` 和 `safe_to_create_card=false`，不得写入 Live ASR audit record、append result audit event、idempotency store 或 marker、不得开启/commit/rollback repository transaction、不得生成真实 lifecycle event、不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-077` card lifecycle readiness summary endpoint `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries` 当前只在 `mode=summary_only` 下复用 PCWEB-076 source preflight 并返回 response-only UI summary；summary 只能暴露 scoped source trace、phase status、block reasons、next decisions 和 `card_lifecycle_safe_to_* = false` flags，不得原样传播上游 true replay-oriented `safe_to_*` action signal。summary 不得写入 Live ASR audit record、append result audit event、idempotency store、marker、summary artifact 或 lifecycle event，不得开启/commit/rollback repository transaction，不得读取 provider config、base URL、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- `PCWEB-078` card lifecycle readiness UI 当前只在浏览器工作台中消费 PCWEB-077 response-only summary；`card-lifecycle-readiness-panel` 的 local contract probe 只来自当前 Live ASR stream 已展示并累计保存在浏览器内存中的 request draft、EvidenceSpan、state event 和 transcript timing，不读取本地配置、密钥、keychain、环境变量、真实音频或 `configs/local/`，不写入 browser storage、audit record、summary artifact、lifecycle event、idempotency store 或 report，不调用 LLM、远程 ASR 或中转站。该 probe 使用 core gate 已允许的 `owner_gap` 类型以通过现有本地验证；它不是正式建议卡，也不改变未来正式 card taxonomy。
- `PCWEB-079` desktop shell readiness boundary `GET /desktop/shell-readiness` 当前只返回 template-only readiness envelope 并由 `desktop-readiness-panel` 展示；响应中的 `desktop_safe_to_capture_audio=false`、`desktop_safe_to_request_permissions=false`、`desktop_safe_to_start_asr_worker=false`、`desktop_safe_to_call_remote_asr=false`、`desktop_safe_to_call_llm=false` 和 `desktop_safe_to_write_audio_chunks=false` 是明确禁用信号。该边界不得捕获 microphone/system audio、不得请求或探测 macOS/Windows 权限、不得访问 CoreAudio/ScreenCaptureKit/WASAPI/native API、不得加载 ASR 模型或启动 worker、不得读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写音频 chunk、本地数据目录、browser storage、安装包、签名、公证或上架产物，不得调用远程 ASR、LLM 或中转站。
- `PCWEB-080` desktop runtime decision boundary `GET /desktop/runtime-boundary` 当前只返回 template-only decision-preflight envelope 并由 `desktop-runtime-boundary-panel` 展示；响应中的 `recommended_desktop_runtime=tauri_first_electron_fallback` 和 `asr_worker_process_model=sidecar_worker_planned` 只是推荐/计划状态，`desktop_runtime_safe_to_create_shell=false`、`desktop_runtime_safe_to_start_native_bridge=false`、`desktop_runtime_safe_to_spawn_worker=false`、`desktop_runtime_safe_to_package_installer=false`、`desktop_runtime_safe_to_request_permissions=false`、`desktop_runtime_safe_to_capture_audio=false`、`desktop_runtime_safe_to_call_remote_asr=false` 和 `desktop_runtime_safe_to_call_llm=false` 是明确禁用信号。该边界不得创建 Tauri/Electron 项目、依赖锁文件、native bridge、worker process、安装包、签名、公证或上架产物；不得捕获 microphone/system audio、请求或探测权限、访问 native runtime/process/audio API、加载 ASR 模型、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写音频 chunk、本地数据目录或 browser storage，不得调用远程 ASR、LLM 或中转站。
- `PCWEB-081` desktop native bridge command contract `GET /desktop/native-bridge-contract` 当前只返回 response-only contract-preflight envelope 并由 `desktop-native-bridge-contract-panel` 展示；响应中的 `desktop_bridge_contract_status=specified_not_bound`、`desktop_bridge_command_count=8`、`desktop_bridge_phase_count=8`、error/resource contract、blockers、next decisions 和 `desktop_bridge_safe_to_create_native_bridge=false`、`desktop_bridge_safe_to_bind_ipc=false`、`desktop_bridge_safe_to_invoke_commands=false`、`desktop_bridge_safe_to_request_permissions=false`、`desktop_bridge_safe_to_enumerate_devices=false`、`desktop_bridge_safe_to_capture_audio=false`、`desktop_bridge_safe_to_spawn_worker=false`、`desktop_bridge_safe_to_write_local_files=false`、`desktop_bridge_safe_to_call_remote_asr=false`、`desktop_bridge_safe_to_call_llm=false` 是明确禁用信号。该边界不得创建 Tauri/Electron 项目、依赖锁文件、native bridge、IPC、localhost bridge、websocket bridge、command handler、worker process、安装包、签名、公证或上架产物；不得请求或探测权限、枚举音频设备、捕获 microphone/system audio、访问 native runtime/process/audio API、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写音频 chunk、本地数据目录、browser storage、bridge audit 文件或 installer/signing/notarization 产物，不得调用远程 ASR、LLM 或中转站。
- `PCWEB-082` Tauri shell scaffold spike 当前只创建静态源码 scaffold：`code/desktop_tauri/src-tauri/tauri.conf.json`、Cargo manifest、capability file 和 Rust no-op command handlers。`runtime_get_status`、`session_prepare` 和 `asr_worker_health` 只返回 `noop_bound/noop_only/tauri_ipc_bound` 响应，并且 `safe_to_execute_real_action=false`、`captures_audio=false`、`spawns_process=false`、`calls_remote_provider=false`、`writes_local_files=false`。该 scaffold 不运行 Tauri shell、不启动 native bridge、不安装或构建依赖、不生成 `Cargo.lock`、`package.json`、dependency lock files、`node_modules`、`target`、installer/signing/notarization artifacts；不得绑定 audio commands、请求或探测权限、枚举设备、捕获 microphone/system audio、访问 native audio API、启动 ASR worker、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写 runtime/session/audio/local data，也不得调用远程 ASR、LLM 或中转站。
- `PCWEB-083` desktop build readiness policy 当前只新增 `code/desktop_tauri/build-readiness.policy.json` 和 `tools/desktop_build_readiness.py`。默认报告只读取 policy，不探测工具链、不运行构建、不安装依赖；显式 probe 模式也仅为 `toolchain_version_probe_only`，只允许 `rustc --version` 和 `cargo --version`，并由工具层硬编码白名单强制执行，custom policy 不能扩大探针执行范围；同时保持 `safe_to_run_cargo_check_now=false`。该边界不得运行 cargo check/build、Tauri dev/build、npm/pnpm/yarn/npx package 或 Tauri launcher，不得生成 `Cargo.lock`、lock files、`node_modules`、`target`、dist、bundle、installer、签名或公证产物，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-084` desktop cargo check artifact policy 当前只新增 `code/desktop_tauri/cargo-check.policy.json` 和 `tools/desktop_cargo_check_policy.py`。默认报告只读取 policy 和检查预期 artifact path 是否存在，不运行 Cargo/Tauri/package manager、不安装 Rust、不联网抓依赖、不生成 `Cargo.lock` 或 target；未来首次获批 `cargo check` 必须使用 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`，首次获批后才允许生成并提交 `code/desktop_tauri/src-tauri/Cargo.lock`，repeat check 才使用 `--locked --offline`。该边界保持 `safe_to_run_cargo_check_now=false`，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-085` desktop Rust toolchain readiness policy 当前只新增 `code/desktop_tauri/rust-toolchain-readiness.policy.json` 和 `tools/desktop_rust_toolchain_readiness.py`。默认报告不执行外部命令；显式 probe 模式仅为 `local_version_and_platform_probe_only`，只允许 `rustc --version`、`cargo --version`、`rustup --version` 和 `xcode-select -p`，且 `xcode-select -p` 只返回 present/missing，不返回本机开发者工具路径。该边界保持 `safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false` 和 `safe_to_run_cargo_check_now=false`，不得安装 Rust、修改 shell profile、运行 cargo/Tauri/package manager、联网抓依赖、生成 `Cargo.lock` 或 target，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-086` desktop Rust toolchain installation decision policy 当前只新增 `code/desktop_tauri/rust-toolchain-installation.policy.json` 和 `tools/desktop_rust_toolchain_installation_decision.py`。默认报告不执行外部命令，仅为 `no_install_decision_report_only`，只读取 policy 并返回官方 `official_rustup` 推荐、显式 approval tokens、platform notes 和 post-install verification order。该边界保持 `safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false`、`safe_to_run_rustup_now=false` 和 `safe_to_run_cargo_check_now=false`，不得运行 curl、sh、rustup、cargo、brew、xcode-select --install、Visual Studio installer、apt/dnf/yum、winget/scoop/choco、npm/pnpm/yarn/npx 或 Tauri CLI，不得修改 shell profile、PATH、cargo home、rustup home、系统设置或 package-manager state，不得联网抓依赖、生成 `Cargo.lock` 或 target，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-087` desktop Rust toolchain install approval packet 当前只新增 `code/desktop_tauri/rust-toolchain-install-approval.policy.json` 和 `tools/desktop_rust_toolchain_install_approval_packet.py`。默认报告不执行外部命令，仅为 `manual_user_run_only`，只读取 policy 并返回官方 Rust/rustup/Tauri source URLs、macOS/Linux inert manual command text、Windows `rustup-init.exe` manual guidance、approval tokens、risk notes、rollback notes 和 post-install verification order。该边界保持 `safe_to_execute_install_now=false`、`safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false`、`safe_to_run_rustup_now=false` 和 `safe_to_run_cargo_check_now=false`，不得执行 manual text、运行 installer、curl、sh、rustup、cargo、brew、xcode-select --install、Visual Studio installer、WebView2 installer、apt/dnf/yum、winget/scoop/choco、npm/pnpm/yarn/npx 或 Tauri CLI，不得修改 shell profile、PATH、cargo home、rustup home、系统设置或 package-manager state，不得联网抓依赖、生成 `Cargo.lock` 或 target，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-088` desktop Rust post-install probe approval policy 当前只新增 `code/desktop_tauri/rust-post-install-probe-approval.policy.json` 和 `tools/desktop_rust_post_install_probe_approval.py`。默认报告不执行外部命令，仅为 `no_probe_execution_approval_packet_only`，只读取 policy 并返回未来 read-only probe allowlist、redaction requirements、approval tokens、expected result schema 和 cargo-check blockers。该边界保持 `safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`，不得运行 rustc、cargo、rustup、xcode-select、cargo check/build、Tauri CLI、package manager 或 shell command，不得读取 PATH、shell profile、cargo home、rustup home、dependency cache、`Cargo.lock`、target output、provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-089` desktop Rust post-install probe result intake 当前只新增 `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` 和 `tools/desktop_rust_post_install_probe_result_intake.py`。默认报告不执行外部命令，仅为 `manual_result_validation_only` 和 `caller_provided_json_only`，只校验 caller-provided bounded status fields，并拒绝 raw stdout/stderr、command、path、env、shell profile、cargo/rustup home、dependency cache、provider config、api_key、authorization 或 bearer token 字段。该边界保持 `safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false` 和 `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`，不得运行 rustc、cargo、rustup、xcode-select、cargo check/build、Tauri CLI、package manager 或 shell command，不得读取 PATH、shell profile、cargo home、rustup home、raw probe output、dependency cache、`Cargo.lock`、target output、provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- `PCWEB-090` desktop first cargo check execution boundary 当前只新增 `code/desktop_tauri/first-cargo-check-execution.policy.json` 和 `tools/desktop_first_cargo_check_execution_boundary.py`。默认报告不执行外部命令，仅为 `explicit_manual_execution_packet_only`，只读取 PCWEB-090 policy、PCWEB-084 artifact policy 和可选 PCWEB-089 bounded result，最多返回 `ready_for_explicit_user_approval` 手动执行包。该边界保持 `safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`，不得运行 cargo check/build、Tauri CLI、package manager 或 shell command，不得联网抓依赖、生成 `Cargo.lock`、target、dependency cache、installer、签名或公证产物，不得读取 PATH、shell profile、cargo home、rustup home、provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`，不得请求权限、枚举设备、捕获 microphone/system audio、启动 ASR worker、写 runtime/session/audio data 或调用远程 ASR、LLM、中转站。
- PCWEB-080 后，Web 工作台启动路径也必须保持被动：只读取 readiness、runtime boundary 和 fixture 列表，并额外读取 native bridge contract，不自动创建 demo session；这四个启动读取都必须是 no-write/no-capture GET 路径。只有用户显式加载 fixture、切换 Live Mock/Live ASR 或执行卡片反馈等动作时，才允许进入既有 session/audit 写入路径。
- Provider config loader 在授权、secret storage adapter、日志脱敏、错误脱敏和配置生命周期设计完成前不得读取配置文件或环境密钥。

## 6. 风险边界

禁止用途：

- 员工绩效评价。
- 隐蔽监控。
- 无告知录音。
- 伪造参会人共识。
- 输出法律、人事、合同裁决。
## 2026-07-02 Update: PCWEB-091 Tauri No-op Shell Smoke Boundary

`PCWEB-091` 新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。该阶段仍是 no-command privacy boundary：报告模式固定为 `readiness_report_only`，只静态验证 Tauri scaffold、Web MVP dev URL、frontend static path、minimal capability、no-op command catalog、generated artifact blockers 和 PCWEB-090 no-command boundary。

即使报告返回 `ready_for_explicit_tauri_run_approval`，也只是说明未来可以申请显式 Tauri no-op shell smoke；当前仍保持 `safe_to_run_tauri_dev_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_capture_audio_now=false`、`safe_to_read_configs_local_now=false`、`safe_to_read_secret_now=false` 和 `safe_to_call_remote_provider_now=false`。PCWEB-091 不读取真实音频、不请求麦克风或系统音频权限、不启动 ASR worker、不读取 provider config、不读取 `configs/local/`、不调用远程 ASR/LLM，也不生成 lock、target、installer、签名或公证产物。
