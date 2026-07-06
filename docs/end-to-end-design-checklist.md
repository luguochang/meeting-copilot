# Meeting Copilot 全链路设计 Checklist

> 日期：2026-06-30  
> 用途：把产品定位、架构、PC Local Web MVP、ASR/LLM、EvidenceSpan、测试、隐私和后续桌面端计划统一到一张可执行检查表。  
> 当前阶段：PC-1 Local Web MVP。

## 1. 使用规则

每进入一个新阶段前，必须逐项检查本文档。状态含义：

- `[x]` 已有文档、代码或测试证据支撑。
- `[~]` 已有初步设计或最小骨架，但不够进入生产/桌面壳。
- `[ ]` 未完成，不应被当作已实现能力。

任何 `[ ]` 或关键 `[~]` 如果影响阶段目标，必须进入实施计划或风险文档。

## 2. 产品价值与边界

- `[x]` 产品定位明确：中文技术会议实时 Copilot，不是通用音频转文字。
- `[x]` 核心价值明确：会议状态、工程缺口、低频建议卡片、证据化报告。
- `[x]` 明确反目标：只做转写和会后总结不算 MVP 成功。
- `[x]` 明确目标用户：技术负责人、主持人/项目负责人、一线工程师/SRE。
- `[x]` 明确非工程会议门禁：工程建议卡片必须为 0。
- `[x]` 明确建议卡片语气：追问/确认，不做强裁判。
- `[~]` 建议卡片数量和频率有产品口径，但 PC-1 尚未接入真实增量 UI。
- `[ ]` 还缺真实用户会议中的可用性反馈和误报率数据。

证据：

- `docs/product-requirements.md`
- `docs/realtime-suggestion-cards.md`
- `docs/requirements-traceability-matrix.md`

## 3. 阶段路线

- `[x]` 已明确 Mac-first、Windows later。
- `[x]` 已明确核心智能层自研，底层 ASR/音频能力复用开源。
- `[x]` 已收束无限 ASR 泛评测，进入 PC Local Web MVP。
- `[x]` 已明确 PC Local Web MVP 先于 Mac desktop shell。
- `[~]` PC-1 已有 core/API/gate/demo fixture/Web 工作台、replay event stream、mock live event source skeleton、Web 工作台 Live Mock 切换、Live Mock SSE 订阅骨架、Live Mock 增量 UI、transcript evidence/state/card 自包含 live payload、本地 `live_asr_stream` skeleton、Live ASR 本地四类状态 lane skeleton、Live ASR no-LLM suggestion candidate queue、Live ASR no-LLM LLM request draft audit、Live ASR request draft query、Live ASR execution preview、Live ASR disabled executor boundary、Live ASR provider readiness、Live ASR provider config secret/display boundary、Live ASR provider masked status boundary、Live ASR provider config request-body validation、Live ASR provider config loader preflight、Live ASR card lifecycle preview dry-run、Live ASR card lifecycle append preflight dry-run、Live ASR card lifecycle append disabled run、Live ASR card lifecycle append repository dry-run、Live ASR card lifecycle append transaction disabled run、Live ASR card lifecycle append result audit preview、Live ASR card lifecycle retry/replay preflight、Live ASR card lifecycle append event serializer dry-run、Live ASR card lifecycle append mutation preflight、Live ASR card lifecycle append transaction commit preflight、Live ASR JSON audit persistence、Live ASR draft review 及 UI 展示、JSON session 持久化和脚本化浏览器 E2E；还没有桌面音频采集、真实 ASR provider endpoint final 质量验证、真实 LLM 实时事件源或真实 event append repository。
- `[ ]` Mac desktop shell 尚未开始。
- `[ ]` Windows adapter 尚未开始。
- `[ ]` 移动端只作为 future companion app，未进入计划。

证据：

- `docs/implementation-roadmap.md`
- `docs/decision-log.md`
- `docs/platform-packaging-and-store-compliance.md`

## 4. ASR 与转写稳定链路

- `[x]` 本地 ASR 默认，远程 ASR 非默认。
- `[x]` FunASR 是中文质量主候选，sherpa-onnx 是轻量 fallback。
- `[x]` 已有统一 streaming event contract。
- `[x]` partial 不生成正式 EvidenceSpan，不触发强 LLM 建议。
- `[x]` final/revision 才能进入正式 transcript segment。
- `[x]` raw transcript 和 normalized transcript 同时保留。
- `[~]` FunASR streaming 文件回放可用，但 final 是 fixed window，不是 provider endpoint final。
- `[~]` 技术词 normalizer 已有最小规则，但真实会议术语覆盖不足。
- `[ ]` 还没有 macOS 实时音频采集输入到 ASR worker。
- `[ ]` 还没有长会议 30-60 分钟稳定性测试。
- `[ ]` 还没有真实会议环境 partial/final/revision 延迟 P95。

证据：

- `docs/asr-provider-strategy.md`
- `docs/chinese-technical-language.md`
- `code/asr_runtime/scripts/streaming_contract.py`
- `code/asr_runtime/scripts/transcribe_funasr.py`
- `code/asr_runtime/tests/test_streaming_contract.py`
- `code/asr_runtime/tests/test_transcribe_funasr.py`

## 5. LLM、调度与成本

- `[x]` LLM provider 使用 OpenAI-compatible 中转站。
- `[x]` API key 只允许存在 `configs/local/`，不得写入公开文档。
- `[x]` 已有 LLM smoke。
- `[x]` 已有 usage sidecar，记录 provider/model/prompt version/token。
- `[x]` 已有 incremental scheduler，partial 不触发 LLM。
- `[x]` final/revision 和状态变化受冷却窗口/预算控制。
- `[x]` PC-1 快照、建议卡片和 replay event stream 已暴露 LLM trigger/cost trace 字段，包括 replay-derived `llm_scheduled` / `llm_schema_result`。
- `[x]` LLM 失败/超时/schema invalid 的 Web MVP replay 降级界面已有第一版：失败结果进入 `suggestion_silenced`，不作为强建议展示。
- `[~]` 会中每次 LLM 调用的 replay trace UI 已有第一版；Live ASR 已有 no-LLM scheduler/candidate/request-draft 审计骨架、disabled executor boundary、provider readiness、provider config secret/display boundary、provider masked status boundary、provider config request-body validation、provider config loader preflight、card lifecycle preview dry-run、card lifecycle append preflight dry-run、card lifecycle append disabled run、card lifecycle append repository dry-run、card lifecycle append transaction disabled run、card lifecycle append result audit preview、card lifecycle retry/replay preflight、card lifecycle append event serializer dry-run、card lifecycle append mutation preflight 和 card lifecycle append transaction commit preflight；真实 scheduler event log、真实 LLM request execution、真实 provider config loader、secret manager、真实 event append repository 和 live trigger trace UI 尚未完成。

证据：

- `docs/llm-quality-evaluation.md`
- `docs/requirements-traceability-matrix.md`
- `code/asr_runtime/scripts/incremental_scheduler.py`
- `code/asr_runtime/scripts/meeting_analysis.py`

## 6. EvidenceSpan 与可信输出

- `[x]` DecisionCandidate、ActionItem、Risk、OpenQuestion 必须引用 EvidenceSpan。
- `[x]` SuggestionCard 必须引用 EvidenceSpan。
- `[x]` 会后报告正式内容必须引用证据。
- `[x]` core 快照会拒绝缺证据建议卡片。
- `[x]` core 快照会拒绝非工程会议携带工程建议。
- `[x]` core 快照会拒绝没有真实 state_ref、完整 MeetingStateEvent、`gap_rule_id`、`trigger_reason` 的正式建议卡片。
- `[x]` core 快照会拒绝伪造 segment/event 时间或超过 30 秒窗口且未降级的强实时建议卡片。
- `[x]` core 快照会拒绝降级状态下仍输出强建议卡片。
- `[~]` Markdown 报告已有最小证据输出。
- `[x]` Web MVP 已支持 UI 侧点击证据回跳 EvidenceSpan 和 transcript 片段。
- `[~]` 已有 EvidenceSpan 在 revision 后的最小 lifecycle 字段、强卡拦截、stale 可视化，以及 Live Mock `transcript_revision` 对旧 EvidenceSpan 的 superseded 增量应用；引用 stale/superseded evidence 的建议卡会通过 `suggestion_invalidated` 审计事件和 UI 层降级为 muted；真实 revision event 触发状态重算、LLM 重跑和 repository 永久撤销尚未接入。

证据：

- `docs/meeting-state-model.md`
- `code/core/meeting_copilot_core/session_snapshot.py`
- `code/core/tests/test_session_snapshot.py`
- `code/core/tests/test_contracts.py`
- `code/core/tests/test_gates.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_app.py`

## 7. PC Local Web MVP

- `[x]` 已有 PC-1 需求文档。
- `[x]` 已有 PC-1 验收清单。
- `[x]` 已有 PC-1 实施计划。
- `[x]` 已有平台无关 core 最小骨架。
- `[x]` 已有本地 API 最小骨架。
- `[x]` API 覆盖 health、create/read/delete session、card status、Markdown report。
- `[x]` API 覆盖 demo fixture 列表和从 fixture 创建 gated session。
- `[x]` repository create/status update 已改为通过 core gate 后再写入，并拒绝未知 card_id，避免坏数据污染内存记录。
- `[x]` 已有本地 Web 工作台第一版，主视图展示状态板、建议卡片、质量指标，证据/转写/报告在侧栏。
- `[x]` Web 工作台展示 fixture evaluation summary，能看到工程语境、有效卡片、gap 覆盖和失败原因。
- `[x]` repository 支持默认 in-memory 和显式 `MEETING_COPILOT_DATA_DIR` JSON 本地持久化。
- `[x]` 已有 demo fixture endpoint 和一键加载样本。
- `[x]` 已有 2 个工程正例 fixture，且每个至少覆盖 2 个 gap rule。
- `[x]` 已有 3 个非工程/边界负例 fixture，工程建议卡片必须为 0。
- `[~]` 已有 replay snapshot event stream 和 SSE 格式输出；已有 mock live JSON/SSE event source skeleton 和本地 `live_asr_stream` JSON/SSE skeleton，Web 工作台可通过 EventSource 订阅并增量应用 Live Mock / Live ASR envelope，transcript final/revision、state/card 事件已带自包含展示字段，Live ASR 可本地抽取 `DecisionCandidate`、`ActionItem`、`Risk`、`OpenQuestion` 四类 PC-1 state lane，并输出 no-LLM suggestion candidate 与 LLM request draft 审计，terminal summary 后可展示非正式 draft review，revision 可更新旧 EvidenceSpan lifecycle，并通过 `suggestion_invalidated` 审计事件降级相关卡片，但还没有桌面音频采集、真实 ASR provider endpoint final 质量验证、真实语义 state engine 或真实 LLM live source。
- `[x]` 已有本地 JSON session/status 持久化；SQLite 暂不引入。
- `[x]` 已有脚本化浏览器端 E2E smoke，覆盖 workbench 加载、evidence click-back、replay event stream、schema 降级 UI 和 report。

证据：

- `docs/pc-local-web-mvp-requirements.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `code/core`
- `code/web_mvp/backend`

## 8. 桌面端计划

- `[x]` 已明确共享 core/UI + platform adapter + 分平台打包。
- `[x]` 已明确 Mac MVP 初期 App Store 外分发。
- `[x]` 已明确 Windows 第二阶段 direct download 优先。
- `[~]` Tauri 优先预研、Electron 备选，但最终桌面壳未锁定。
- `[ ]` 还没有 macOS audio_capture adapter。
- `[ ]` 还没有 Windows audio_capture adapter。
- `[ ]` 还没有模型下载/cache/cleanup 的桌面实现。
- `[ ]` 还没有自动更新策略。
- `[ ]` 还没有签名、公证、安装/卸载 smoke。

证据：

- `docs/platform-packaging-and-store-compliance.md`
- `docs/mac-mvp-requirements-and-technical-plan.md`
- `docs/decision-log.md`

## 9. 隐私、安全与成本

- `[x]` 默认远程成本只允许 LLM 中转站。
- `[x]` 远程 ASR 不默认启用。
- `[x]` 真实录音、API key、模型缓存、运行输出不得入仓库。
- `[x]` 已有 `.gitignore` 覆盖 local config、模型、输出、venv、Python cache。
- `[x]` 文档明确录音/转写/AI 服务披露要求。
- `[~]` PC-1 已有显式本地数据目录和 session JSON 删除；音频/chunk/export/model cache 删除待桌面数据生命周期接入。
- `[ ]` 还没有会议数据删除的桌面端全链路实现。
- `[ ]` 还没有隐私政策/录音告知文案最终稿。
- `[ ]` 还没有模型许可证和再分发清单。

证据：

- `docs/privacy-and-data-flow.md`
- `docs/platform-packaging-and-store-compliance.md`
- `.gitignore`

## 10. SDD/TDD 与验证

- `[x]` 需求追踪矩阵存在。
- `[x]` 重要决策已进入 decision log。
- `[x]` core 采用 TDD 建立测试。
- `[x]` web_mvp backend 采用 TDD 建立测试。
- `[x]` asr_runtime 和 asr_bakeoff 有自动测试。
- `[x]` PCWEB 已有后端测试和脚本化浏览器 E2E smoke。
- `[x]` 已有统一本地脚本化质量门禁入口：`python3 tools/run_quality_gate.py --profile pc-web`。
- `[ ]` 还没有 CI。
- `[ ]` 还没有前端可视化回归或截图验收。

当前验证命令：

```bash
cd code/core && pytest -q
cd code/web_mvp/backend && pytest -q
cd code/asr_runtime && pytest -q
cd code/asr_bakeoff && python3 -m pytest tests -q
python3 tools/run_quality_gate.py --profile pc-web
```

## 11. 进入下一阶段前 Gate

进入 PC-1 前端工作台前必须满足：

- `[x]` core snapshot 可输出 transcript/state/card/evidence/quality。
- `[x]` API 可创建/读取/删除 session。
- `[x]` API 可更新 card status。
- `[x]` Markdown report 可导出。
- `[x]` 准备 2-3 个 demo fixture endpoint 或本地样本加载路径。
- `[x]` Web 工作台可加载 demo fixture 并展示状态、建议、证据、转写、质量和报告。

进入 Mac desktop shell 前必须满足：

- `[x]` PC Web 工作台可实际展示 transcript/state/card/evidence/report。
- `[x]` 至少 2 个中文技术会议样本可一键加载并通过 core demo gate。
- `[x]` 非工程会议样本工程建议卡片为 0。
- `[x]` 建议卡片有 evidence click-back，可从卡片/状态证据 chip 定位 EvidenceSpan 和 transcript segment。
- `[x]` LLM trigger trace、schema 降级和 usage 在建议卡片、quality/evaluation 面板和 replay event stream 可见；真实 scheduler event log 尚未接入。
- `[~]` 已明确 PC-1 本地数据目录和 session JSON 删除语义；模型缓存策略仍需专项清单。

进入 Windows adapter 前必须满足：

- `[ ]` Mac desktop shell 证明 core/UI/platform adapter 分层可行。
- `[ ]` Windows audio capture、installer、signing、SmartScreen 风险完成专项设计。

## 12. 当前最高优先级缺口

P0：

- `[~]` PC Web 工作台已有第一版、replay event stream、mock live source skeleton、Live Mock UI 切换、Live Mock SSE 订阅骨架、Live Mock 增量 UI、本地 `live_asr_stream` skeleton、Live ASR 本地状态/调度/候选/request-draft 审计、transcript evidence/state/card 自包含 live payload、JSON 持久化和浏览器 E2E 自动化；仍缺桌面音频采集、真实 ASR provider endpoint final 质量验证、持久化真实 scheduler event log 和真实 LLM live source。
- `[x]` `PCWEB-037` 本地 `live_asr_stream` 骨架已实现：从 synthetic ASR runtime streaming event contract 生成 Web live envelope，并通过 SSE/浏览器 smoke 证明前端可消费本地 ASR 事件源；仍不接桌面音频采集、远程 ASR 或真实 LLM。
- `[x]` `PCWEB-038` Live ASR 本地状态/调度骨架已实现：`final/revision` 可生成带 EvidenceSpan 的本地 `DecisionCandidate` 和 no-LLM scheduler trace；仍不是正式 state engine、真实 scheduler 或真实 LLM。
- `[x]` `PCWEB-039` Live ASR 本地 scheduler decision log 已实现：可展示 `llm_candidate_queued`、`llm_candidate_skipped`、`cooldown`、预算和 `not_called`；仍不是持久化真实 scheduler log 或真实 LLM。
- `[x]` `PCWEB-040` Live ASR 本地状态抽取契约已实现：`final/revision` 可生成带 EvidenceSpan 的本地 `DecisionCandidate` 和 `OpenQuestion`，同一段多状态时保持 `state_event -> scheduler_event` 成对相邻；仍不是正式语义 state engine。
- `[x]` `PCWEB-041` Live ASR JSON audit persistence 已实现：设置本地数据目录时，`live_asr_stream` JSON event record 可跨 app 实例读回并随 `DELETE /sessions/{id}` 删除；仍不持久化 raw audio、audio chunks、真实 scheduler log 或 LLM 输出。
- `[x]` `PCWEB-042` Live ASR draft review 已实现：可从 audit record 导出 JSON/Markdown 复盘草稿，明确 `is_formal_report=false`、`llm_call_status=not_called`；仍不是正式 gated report 或 LLM 分析。
- `[x]` `PCWEB-043` Live ASR draft review UI 已实现：Web 工作台在 Live ASR terminal summary 后展示 `/draft.md` 非正式复盘草稿，并保持 0 次 formal report 请求；仍不是正式 gated report、建议卡或 LLM 分析。
- `[x]` `PCWEB-044` Live ASR Action/Risk state skeleton 已实现：本地规则可生成 `ActionItem` 和 `Risk`，使 Live ASR 四类状态 lane 都能被 evidence-backed state event 驱动；仍不是正式语义 state engine。
- `[x]` `PCWEB-045` Live ASR suggestion candidate queue 已实现：每个本地状态候选在 no-LLM scheduler 后生成 `suggestion_candidate_event`，Web timeline 和 draft review 可见候选 gap rule，但仍不生成正式建议卡、LLM schema 或 silenced event。
- `[x]` `PCWEB-046` Live ASR candidate quality metadata 已实现：no-LLM `suggestion_candidate_event` 增加本地 deterministic confidence/degradation 审计字段，Web timeline 和 draft review 可见 quality metadata，但仍不生成正式建议卡、LLM schema、silenced event 或 formal report 请求。
- `[x]` `PCWEB-047` Live ASR candidate queue query 已实现：API 可只读查询 no-LLM suggestion candidate queue，保留原始事件顺序和 payload，支持 JSON persistence 跨实例读取，但仍不排序、过滤、生成正式建议卡或调用 LLM。
- `[x]` `PCWEB-048` Live ASR LLM request draft audit 已实现：每个 no-LLM suggestion candidate 后追加本地 `llm_request_draft_event`，Web timeline 和 draft review 可见 request draft、candidate linkage、`draft_only/not_generated/not_created` 状态，但仍不调用中转站、不生成 schema、正式建议卡或 silenced event。
- `[x]` `PCWEB-049` Live ASR LLM request draft query 已实现：API 可只读查询 no-LLM `llm_request_draft_event` 队列，保留规范化事件顺序、event metadata 和 payload，支持 JSON persistence 跨实例读取，但仍不执行请求、不估算 token、不生成 schema、正式建议卡或 silenced event。
- `[x]` `PCWEB-050` Live ASR LLM execution preview 已实现：API 可只读查询从 request draft 派生的本地 execution preview queue，包含幂等键、schema target、request/candidate/evidence/segment linkage 和 `preview_only/not_called/not_generated/not_created/not_estimated` 状态，但仍不读取密钥、不调用中转站、不执行请求、不生成 schema、正式建议卡或 silenced event。
- `[x]` `PCWEB-051` Live ASR LLM executor disabled run 已实现：API 已有显式 executor action boundary，`mode=disabled` 时从 execution preview 派生 skipped run queue，包含 run/request/evidence linkage 和 `llm_executor_disabled/not_called/not_generated/not_created/not_estimated` 状态，但仍不读取密钥、不调用中转站、不估算 token、不生成 schema、正式建议卡或 silenced event。
- `[x]` `PCWEB-052` Live ASR LLM provider readiness 已实现：API 可只读报告 future OpenAI-compatible provider contract 当前 `not_ready/disabled/blocked`，展示队列计数、block reasons 和 next decisions；即使存在环境配置路径也不读取配置或密钥，仍不调用中转站、不估算 token、不生成 schema、正式建议卡或 silenced event。
- `[x]` `PCWEB-053` Live ASR LLM provider config boundary 已实现：API 可只读报告 future provider config 字段分类和展示策略，`api_key` 仅作为字段名元数据出现并标记 `secret/never_display/never_return_value`；即使存在环境配置路径也不读取配置或密钥，仍不返回真实/脱敏配置值、不报告 key presence/validity/length/hash/prefix/suffix/fingerprint、不调用中转站。
- `[x]` `PCWEB-054` Live ASR LLM provider masked status 已实现：API 可只读报告 future masked provider status envelope，display values 全部为 null，`api_key` 仅作为 placeholder/policy/forbidden signal 名称出现；即使存在环境配置路径也不读取配置或密钥，仍不返回真实/脱敏配置值、不报告 key presence/validity/length/hash/prefix/suffix/fingerprint、不调用中转站。
- `[x]` `PCWEB-055` Live ASR LLM provider config validation 已实现：API 可校验 caller-provided OpenAI-compatible config draft，返回 safe derived display values 和 `safe_to_execute=false`；请求体可包含 `api_key` 但响应/422 错误不得回显、mask、hash、计数或报告 key presence/validity/prefix/suffix/fingerprint；仍不读取本地配置/环境密钥、不改变 `/events`、不调用中转站。
- `[x]` `PCWEB-056` Live ASR LLM provider config loader preflight 已实现：API 可校验 future config loader request shape 和授权意图，返回 metadata-only 和 `safe_to_load_config=false`；请求体可包含本地 config path reference，但会拒绝 URL/file URL、NUL/control characters、路径穿越和重复 `requested_fields`，且响应/422 错误不得回显 raw path、basename、parent directory、path-derived label、config existence/readable/size/mtime/hash/fingerprint、密钥或授权值；仍不读取本地配置/环境密钥、不改变 `/events`、不调用中转站。
- `[x]` `PCWEB-057` Live ASR LLM provider secret storage policy 已实现：API 可只读报告 template-only secret storage policy，推荐 OS keychain/enterprise secret provider/development-only env var name reference，列出禁止存储位置和 loader guards；仍不读取 keychain、环境密钥或本地配置，不返回真实/脱敏/哈希/前后缀/长度/fingerprint/presence/validity secret signal，不改变 `/events`、不调用中转站。
- `[x]` `PCWEB-058` Live ASR LLM provider config reader dry-run 已实现：API 可校验 future authorized config reader request shape，要求 `dry_run_only`、secret reference 和显式授权 flags，并保持 config read、secret read、LLM call、event mutation 全部关闭；响应/422/404 错误不得回显 raw path、basename、parent directory、path-derived label、secret reference id、config existence/readable/size/mtime/hash/fingerprint、密钥或授权值；仍不读取 keychain、环境密钥、本地配置、secret adapter，不改变 `/events`、不调用中转站。
- `[x]` `PCWEB-059` Live ASR LLM provider masked status loader dry-run 已实现：API 可校验 future authorized masked status loader request shape，要求 `masked_status_dry_run_only`、secret reference、requested display fields 和显式授权 flags，并保持 config read、secret read、LLM call、event mutation、status value inference 全部关闭；display values 全部为 null，响应/422/404 错误不得回显 raw path、basename、parent directory、path-derived label、secret reference id、config existence/readable/size/mtime/hash/fingerprint、API key/masked key/presence/validity/length/hash/prefix/suffix/fingerprint 或授权值；仍不读取 keychain、环境密钥、本地配置、secret adapter，不改变 `/events`、不调用中转站。
- `[x]` `PCWEB-060` Live ASR OpenAI-compatible request body preview 已实现：API 可从 no-LLM request drafts 派生 deterministic chat-completions request body preview，包含 messages、response_format schema target、metadata、幂等键和 request/candidate/evidence linkage；仍保持 `body_preview_only/not_called/not_read/not_generated/not_created/not_estimated/safe_to_execute=false`，不读取 provider config、base URL、API key、Authorization header、环境密钥、keychain 或 `configs/local/`，不改变 `/events`、不调用中转站。
- `[x]` `PCWEB-061` Live ASR OpenAI request body preview redaction guard 已实现：preview 输出层应用 `local_sensitive_draft_value_guard.v1`，当 draft payload 可反射字段含 API-key-like token、Bearer、Authorization、raw_config、api_key、base_url、config_path、`configs/local` 或 relay-domain marker 时，以 `[redacted:sensitive_draft_value]` 替换并返回 redaction audit；原始 `/events` 和 `/llm-request-drafts` 不被改写，仍不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-062` Live ASR OpenAI request body schema outline preview 已实现：preview 的 `response_format.json_schema` 现在返回 outline-only `SuggestionCardV1` 字段合同（required/optional/type hints/additional_properties_status），让未来 enabled executor、schema validator 和 card lifecycle 对齐；仍不生成完整 JSON Schema、不做 schema validation、不创建卡片、不改变 `/events`、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-063` Live ASR LLM schema validation dry-run 已实现并经审查加固：API 可对 caller-provided `candidate_response` 做本地 dry-run schema validation，返回 passed/failed、request linkage 和 deterministic validation errors；`mode/request_id` 保持严格 string，token/timing 字段拒绝 float、boolean 和 numeric string；仍不生成真实 `llm_schema_result`、不创建卡片、不改变 `/events`、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-064` Live ASR card creation policy dry-run 已实现：API 可对 schema-shaped `candidate_response` 做本地卡片创建策略 dry-run，校验 request draft linkage、evidence/state/segment/time/schema/candidate quality，返回 allowed/blocked 和 deterministic policy errors；仍不生成真实 `llm_schema_result`、不创建卡片、不改变 `/events`、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-065` Live ASR card lifecycle preview dry-run 已实现：API 可对 schema/policy dry-run 后的 candidate 预览未来 enabled lifecycle 会追加的 `llm_schema_result` + `suggestion_card` 或 `llm_schema_result` + `suggestion_silenced`，但仍不生成真实事件、不改变 `/events`、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-066` Live ASR card lifecycle append preflight dry-run 已实现：API 可对 future lifecycle events 派生 event id、幂等键和 would-append sequence，并预检已有 event/idempotency conflict；仍不写入 audit record、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-067` Live ASR card lifecycle append disabled run 已实现：API 可把 append preflight plan 映射为 skipped append runs，并保留 preflight conflict；仍不写入 audit record、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-068` Live ASR card lifecycle append repository dry-run 已实现：API 可把 skipped append runs 映射为 future repository result envelopes，并保留 preflight conflict；仍不写入 audit record、不写 repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-069` Live ASR card lifecycle append transaction disabled run 已实现：API 可把 repository results 映射为 skipped transaction runs，并保留 preflight conflict；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-070` Live ASR card lifecycle append result audit preview 已实现：API 可把 transaction runs 映射为 response-only append result audit event previews，并保留 preflight conflict；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-071` Live ASR card lifecycle retry/replay preflight 已实现：API 可把 audit preview items 映射为 retry/replay checks，区分安全重放、部分重放和冲突；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-072` Live ASR card lifecycle append event serializer dry-run 已实现：API 可把 preview/preflight 映射为 canonical future persisted event objects；仍不写入 audit record、不 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-073` Live ASR card lifecycle append mutation preflight 已实现：API 可把 canonical serialized events 映射为 response-only mutation eligibility checks；仍不写入 audit record、不开启或 commit repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-074` Live ASR card lifecycle append transaction commit preflight 已实现：API 可把 mutation checks 与 retry/replay checks 映射为 response-only commit readiness checks；仍不写入 audit record、不开启/commit/rollback repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-075` Live ASR card lifecycle append idempotency store write preflight 已实现：API 可把 commit readiness checks 映射为 response-only idempotency store write checks；fresh append 只暴露 future idempotency record，safe replay 不再写，partial/conflict/mutation blocked 一律阻断；仍不写入 audit record、不开启/commit/rollback repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-076` Live ASR card lifecycle append result audit event persistence preflight 已实现：API 可把 idempotency store write checks 映射为 response-only append result audit event persistence checks；fresh append 只暴露 future audit event identity，safe replay 不再写 audit event，partial/conflict/upstream blocked 一律阻断；每条 check 保留 PCWEB-070 `transaction_run_id`、`append_run_id`、`repository_result_id`、`audit_repository_transaction_status` 等 provenance，且 `safe_to_mutate_events=false`、`safe_to_append_events=false`；仍不写入 audit record、不开启/commit/rollback repository transaction、不写 idempotency store、不读取配置/密钥、不调用中转站。
- `[x]` `PCWEB-077` Live ASR card lifecycle readiness summary 已实现：API 可把 PCWEB-065..076 的低层 lifecycle/preflight chain 压缩成 response-only UI summary，输出 overall readiness、12 phase summaries、block reasons、next decisions 和 scoped safe flags；safe replay 只作为 no-new-write replay evidence，不暴露上游 true action `safe_to_*`；仍不写入 audit record、不开启/commit/rollback repository transaction、不写 idempotency store 或 audit event、不读取配置/密钥、不调用中转站。
- `[~]` PC-1 尚未把持久化真实 scheduler event log 接进 API 快照；当前强制卡片携带并交叉校验 trigger/cost trace 字段，可从 snapshot 生成含 LLM 调度、结构化结果和静默降级的 replay timeline，并已有 Live ASR no-LLM scheduler decision log 用于 UI 链路验证。
- `[~]` core schema/contract 已迁入 PC-1 关键契约，但 ASR runtime 仍保留 provider/CLI 层自由 dict adapter。
- `[~]` PC Web 前端工作台已实现静态第一版、fixture evaluation summary、Live Mock EventSource 消费骨架、Live Mock 增量 UI、Live ASR EventSource skeleton、Live ASR 本地四类状态抽取/调度/候选/request-draft 审计、Live ASR candidate/request-draft/execution-preview 查询端点、Live ASR draft review UI、transcript evidence/state/card 自包含 live payload 和脚本化浏览器 E2E；真实桌面音频输入和真实模型实时流仍未完成。

P1：

- `[~]` PC-1 session/status 已可写入本地 JSON 并删除文件；完整音频/chunk/export/model cache 删除语义未完成。
- `[~]` EvidenceSpan click-back、revision 最小失效策略、Live Mock revision evidence lifecycle、`suggestion_invalidated` 审计事件和 UI 层卡片降级已在 Web MVP/core 实现；真实 revision event 自动重算状态/卡片未完成。
- `[ ]` ASR 技术词质量仍依赖 normalizer，真实会议可用性未证明。
- `[~]` MVP 缺口类型口径暂不新增 `compatibility_gap`，API 兼容性用 `test_verification_gap` 和 `metric_monitoring_gap` 表达；后续如要新增必须更新 core 白名单。
- `[x]` 工程语境门禁已有纯商务/行政、泛产品、混合技术词 3 类边界负例。
- `[ ]` 用户反馈质量资产未实现，`too_late`、`too_intrusive` 尚未进入回归样本。

P2：

- `[x]` 统一本地测试脚本已建立：`tools/run_quality_gate.py` 覆盖 PC Web MVP core/backend/browser smoke；CI 尚未建立。
- `[ ]` 模型许可证、缓存和下载策略需要后续专项清单。
- `[ ]` 桌面自动更新和签名策略后置。
- `[ ]` traceability gate 未建立，测试名/marker 尚未强绑定需求 ID。
