# Meeting Copilot 当前状态与 P0 执行计划 - 2026-07-08

> 状态：当前 P0 执行入口
> 来源：用户反馈、三路多 Agent 只读审查、主线程代码/服务/浏览器复核
> 产品初心：中文技术会议实时 AI Copilot，不是普通音频转文字工具。
> 当前结论：`Demo/mock: Go for demo only`、`File lane: Go`、`Simulated realtime wav: Go`、`Real mic: No-Go deferred`、`Production MVP: No-Go`。

## 1. 一屏结论

项目不是没有做，也不是已经能生产交付。

已经能证明的部分：

- Workbench 页面能打开，当前 8765 服务已重新加载新版 `workbench.html`，`#transcript-stream`、`#suggestions-panel`、`#approach-panel` 均存在。
- 示例按钮可渲染 4 条演示转写和 4 条候选提醒，但它是 `local_mock_asr`，只算 demo。
- 文件导入 lane 已有可信子链路：`uploaded_wav -> local_funasr_batch -> real_gateway -> suggestion cards -> approach cards -> minutes -> Workbench same session -> delete`。
- 后端已有 live ASR session、LLM OpenAI-compatible 调用、方案卡、复盘、历史和删除雏形。

不能证明的部分：

- 真实麦克风能稳定采到有效中文会议输入。
- 真实麦克风能产生非 fake ASR final。
- 真实麦克风同一 session 能自动或准自动进入建议、方案、复盘、历史、删除和 evidence bundle。
- 当前页面已经是用户可放心使用的生产级产品页。
- 当前后端已经有生产级边界、并发、审计和完整删除语义。

最新可声明状态：

| Lane | 状态 | 允许怎么说 | 禁止怎么说 |
|---|---:|---|---|
| Demo/mock | Go for demo only | 可演示 UI 和候选提醒 | 不能说真实会议跑通 |
| File upload | Go | 文件导入 Copilot 子链路已跑通 | 不能外推为实时麦克风可用 |
| Simulated realtime WAV | Go | 用合成/公开授权 WAV 验证实时协议和业务链路 | 不能算真实麦克风 Go |
| Real mic browser | No-Go | 有入口，有历史降级记录 | 不能说全链路真实自测通过 |
| Real mic command-line | No-Go | 有工具和静音证据 | 不能说 ASR/LLM 主链路通过 |
| Production MVP | No-Go | 只能算本地技术预览 | 不能发布或对外宣称生产可用 |

## 2. 这次新发现

### 2.1 页面“文字没了”的直接运行态原因之一

`app.py` 在模块加载时读取：

```python
WORKBENCH_HTML = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")
```

旧 8765 进程启动于 2026-07-08 11:51:34，持有旧版 HTML 常量。源码里已经有 `#transcript-stream/#suggestions-panel/#approach-panel`，但旧进程返回的 `/workbench` 仍包含 `id="stream"`。

本轮已重启 8765，重新验证：

```text
GET /health -> {"status":"ok","service":"meeting-copilot-web-mvp"}
GET /audio/check -> mic_available=true, realtime_asr_available=true, llm_configured=true
GET /workbench -> has transcript-stream / suggestions-panel / approach-panel, no old id="stream"
```

这说明此前用户在页面看到旧结构，不一定是源码没改，而是运行态没有重启。

### 2.2 当前浏览器页面状态

应用内浏览器中存在一个历史错误标签：

```text
ERR_CONNECTION_REFUSED
```

这是旧服务未启动时留下的错误页，不代表当前服务状态。当前有效页面是：

```text
http://127.0.0.1:8765/workbench
title=会议助手
hasOldStream=false
hasTranscriptStream=true
hasSuggestionsPanel=true
hasApproachPanel=true
```

页面当前还能看到一个降级历史会话：

```text
rec_mrbjm4gr
0 条文字
未识别到有效语音
```

这是 No-Go 证据，不是成功证据。

### 2.3 示例按钮可用，但只算 demo

点击 `试用示例` 后页面状态：

```text
sessionMeta=演示会议 · workbench_mrbkq43f · 21 条记录 · 示例内容，不计入真实验收。
utteranceCount=4
suggestionLikeCount=4
asr=演示
```

这说明 demo UI 能渲染，但也暴露产品问题：

- 候选提醒仍混在 `#transcript-stream` 实时文字流里。
- 右侧正式 `#suggestions-panel` 仍为空。
- 用户会看到“可能遗漏”，但不会清楚它是候选，不是正式 AI 建议卡。

## 3. 原始 P0 契约

P0 只认这条主链路：

```text
真实或用户授权音频
  -> 非 fake ASR
  -> ASR final / EvidenceSpan
  -> 会议状态与工程缺口候选
  -> 低频有证据建议
  -> 方案分析
  -> 会后复盘
  -> 历史恢复
  -> 删除
  -> evidence bundle
```

不可变验收：

- 只有实时文字，没有建议卡：No-Go。
- 只有会后总结，没有会中或准实时建议：No-Go。
- 建议卡没有 EvidenceSpan：No-Go。
- mock/fake/demo 被包装成真实能力：No-Go。
- file lane Go 被外推成 real mic Go：No-Go。
- real mic 没有有效输入还继续跑 LLM 质量评估：No-Go。
- 删除不能覆盖本地会话、派生产物和音频保留状态：No-Go。

## 4. 当前实现矩阵

| 功能 | 当前状态 | 说明 |
|---|---|---|
| 本地 Web UI | 部分实现 | Workbench 能打开，但仍偏研发验证台 |
| 真实麦克风入口 | 部分实现 | `getUserMedia -> WebSocket` 存在，但 Gate A/B/C 未通 |
| 文件导入转写 | 已通过子链路 | `local_funasr_batch` 可用，是当前最可信 lane |
| ASR live session | 部分实现 | JSON/in-memory repo 可存取，但 schema/provenance 不够硬 |
| EvidenceSpan | 部分实现 | event 结构存在，但 UI 回跳和正式约束不足 |
| 会中候选提醒 | 部分实现 | 主要是本地关键词/规则候选，不是真正实时 LLM 自动建议 |
| 正式建议卡 | 部分实现 | 需要手动调用 `/llm-execution-runs` |
| 方案分析 | 部分实现 | 手动调用 `/approach-cards` |
| 会后复盘 | 部分实现 | 手动调用 `/minutes`，UI 仍是 Markdown `<pre>` 风格 |
| 历史记录 | 部分实现 | 可列 session，但标题/来源/选中态/验收语义不足 |
| 删除 | 部分实现 | 能删 live session JSON，但不能证明完整音频/export/evidence 删除 |
| 生产级后端 | 未完成 | `app.py` 过胖，mock/demo/real/local-event/legacy 路由混在一起 |
| 桌面安装包 | 未完成 | Tauri 仍主要是 scaffold/no-op/boundary |
| 系统音频 | 未完成 | P0 先不扩展，避免偏离 real mic 主线 |

## 5. 三路 Agent 复审结论

### 5.1 前端/产品可用性

- Workbench 不是完全没功能，真实入口、导入入口、建议/方案/复盘、历史、删除都存在。
- 但当前页面像九个按钮的研发控制台，不像用户开会时能放心使用的产品。
- `试用示例` 与真实会议入口同级，会混淆 demo 与真实能力。
- 概念过多：`可能遗漏`、`实时建议`、`会议建议`、`提醒`、`方案分析` 没有统一层级。
- 候选提醒和实时文字混在同一流里，正式建议在右侧，用户很难理解“当前 AI 到底做了什么”。

### 5.2 后端/架构边界

- 当前真实产品更接近 `/live/asr/sessions` 族 API，旧 `/sessions` 是 legacy/core snapshot path。
- fake/mock/demo/local-event/file/mic 都能进入系统，其中 local-event-file 可被标为非 mock，provenance 不够强。
- `get_recognizer()` 在 sidecar 不可用时会 fallback 到 `FakeStreamRecognizer`。虽然现在会标 mock/degraded，但生产真实会议模式应该 hard fail，而不是生成可被误解的 session。
- 真实 LLM 卡不是实时自动生成，而是用户点击后调用 `/llm-execution-runs`。
- `app.py` 同时承载 health、audio check、WebSocket、file upload、demo、mock、local event、LLM、minutes、shadow feedback 等，P0 边界不清。

### 5.3 文档/需求/交付差距

- 当前文档不是太少，而是太多，曾经大量 readiness/preflight/approval wrapper 稀释主线。
- 最新可信主线必须收束为：`file lane Go / real mic No-Go / Production MVP No-Go`。
- 过去陷入评测循环的根因是把“边界证明/包装器”当成“产品主线进展”。
- 后续只能按 real mic Gate A/B/C 推进，不能再扩 ASR provider 横评、安装包、移动端、系统音频、自动建单等。

## 6. 当前阻断问题

### P0 阻断

- Real mic Gate A 未通过：历史证据显示近静音或无有效语音。
- Real mic Gate B 未通过：未证明非 fake ASR final >= 1。
- Real mic Gate C 未通过：未证明同一真实麦克风 session 进入建议、方案、复盘、历史、删除、evidence。
- mock/degraded session 仍可手动进入 LLM endpoints，容易出现“假 ASR + 真 LLM”的混合记录。
- local-event-file 可构造非 mock session，验收 eligibility 不够硬。
- 测试命名存在误导，多个 `real/mainline` 测试实际使用 fake recognizer、mock ASR、mock LLM 或 fake verifier。

### P1/P2 后置

- app.py router 拆分。
- JSON repo 并发和版本控制。
- AudioWorklet 替换 ScriptProcessor。
- Tauri 桌面壳、Windows、系统音频、移动端。
- Jira/Linear/GitHub 自动建单。

## 7. Stop Rules

以下工作不能再算 P0 主线进展：

- 新增 readiness/preflight/approval wrapper。
- 扩 ASR provider 横评。
- 用 demo smoke 证明真实会议。
- 用 file lane Go 证明 real mic Go。
- real mic 没有 ASR final 前做 LLM 质量评测。
- P0 未通前推进安装包、移动端、系统音频、多人识别、自动建单。
- 把 mock/degraded/local-event-file bundle 写成 Production Go。
- 把轻量 live draft card 继续混称为正式 `SuggestionCardV1`。

## 8. P0 Gate

### Gate A：真实输入有效

必须证明：

- 浏览器或命令行采集到非静音输入。
- evidence 包含设备、音量、电平、WAV、volumedetect 或等效音频健康数据。
- 输入来自真实麦克风或用户明确授权音频。

未通过时：

- 停止 ASR/LLM 下游。
- 输出 Gate A No-Go。
- 不生成建议/方案/复盘。

### Gate B：非 fake ASR final

必须证明：

- `provider_mode=real`
- `is_mock=false`
- `asr_fallback_used=false`
- `final_count>=1`
- ASR text 非空

未通过时：

- 不允许进入 Production Go。
- 若 fallback 到 fake，只能标 `degraded/mock`，不能进入真实验收。

### Gate C：同 session Copilot 闭环

必须证明同一 session 完成：

- transcript visible
- suggestion cards
- approach cards
- minutes
- history restore
- delete
- evidence bundle
- `go_no_go.md` 列出的证据文件真实存在

只有 Gate C 通过，才允许讨论 Production MVP Go。

## 8.1 Release-Decisive 状态

后续不再把 readiness、preflight、wrapper、synthetic、demo smoke 或重复 file lane 回归当作 P0 主线进展。P0 主线只承认以下 7 个能改变发布判断的状态：

| 状态键 | 当前状态 | 能改变它的证据 |
|---|---:|---|
| `backend_acceptance_enforcement` | 部分 Go | mock/demo/local-event/fallback/degraded/空 ASR 默认不能进入正式 LLM；demo/test 绕过不进入生产端点 |
| `workbench_real_demo_separation` | 部分 Go | UI 明确区分真实麦克风、导入录音、演示、降级；候选提醒不计入正式建议；真实麦克风失败时保留上一场可读会议 |
| `formal_card_evidence` | 部分 Go / real mic No-Go | 正式建议卡已可携带 quote/timestamp/segment 并回跳；仍缺同 real mic session 证据 |
| `delete_evidence` | No-Go for production | 删除结果逐项覆盖 session、events、cards、minutes、exports、evidence bundle、音频或音频保留状态 |
| `real_mic_gate_a` | No-Go | 真实麦克风或用户授权音频有非静音 input health evidence |
| `real_mic_gate_b` | No-Go | 同 session `provider_mode=real/is_mock=false/asr_fallback_used=false/final_count>=1/non_empty_transcript=true` |
| `real_mic_gate_c` | No-Go | 同 session 完成 transcript、正式建议、方案、复盘、历史恢复、删除和 evidence bundle |

任何新任务若不能改变以上状态之一，只能记为 supporting work，不能写成 P0 主线完成。

## 8.2 本轮多 Agent 再审查后的最新缺口

四路 Agent 对前端、后端、真实链路、文档追踪的最新审查结论一致：

- Workbench 不是空壳，但仍有产品语义问题：开始麦克风时前端会在服务端确认前显示“麦克风/真实”，候选提醒与正式建议计数仍混在 `#s-cards`，`整理会议` 只是三个按钮 `.click()` 拼装。
- 后端已补齐非空 ASR final gate 和删除范围精确化；本轮 DEC-236 已将 demo/test 绕过从生产端点请求 schema 隔离到 `/live/asr/demo/sessions/*` 专用边界。
- 真实链路不是没有工具，而是 Gate A/B/C 证据不足。`workbench_smoke.mjs` 只证明 demo UI；file lane Go 只证明导入录音子链路；real mic 仍 No-Go。
- 文档不是太少，而是活文档太多。当前唯一活计划仍是本文档，旧 P0/mainline/workbench/readiness/preflight/wrapper 文档只能作为 historical context 或 evidence archive。

因此下一轮实现不做全站大重写，先做 3 个能直接改变 `workbench_real_demo_separation` 的 P0 前端修复：

1. 麦克风开始阶段显示 `待确认`，只有服务端 snapshot 证明真实、非空、可验收后才显示“真实麦克风/可验收”。
2. 拆分 `候选提醒`、`正式建议`、`方案分析` 计数，`#s-cards` 不再混合所有提醒。
3. 把 `整理会议` 改成独立 orchestration，不再通过三个按钮 `.click()` 并发拼装。

### 8.3 本轮真实页面麦克风自测与多 Agent 再审查

2026-07-08 14:11-14:20 CST 进行了两轮真实 Workbench 页面自测。

第一轮直接点击 `开始会议 -> 结束会议`：

```text
session_id=rec_mrboj702
页面状态=没有检测到麦克风声音
WebSocket chunk_count=145
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
final_count=0
non_empty_transcript=false
acceptance_eligible=false
acceptance_blockers=[degraded_asr_session, asr_final_missing, asr_transcript_empty]
UI 结果=历史中显示“降级 · rec_mrboj702 · 0 条文字 · 未识别到有效语音”
```

第二轮先加载示例会议，再点击 `开始会议 -> 结束会议`，用于验证“真实麦克风失败时不清空上一场已打开会议”：

```text
previous_session=workbench_mrboukhq
failed_mic_session=rec_mrboum34
before=示例会议 4 条文字可见
during=麦克风待确认；页面提示没有检测到麦克风声音
after=页面恢复并保留 workbench_mrboukhq 的 4 条文字
history=rec_mrboum34 作为降级会话保留，final_count=0
```

这两轮说明：

- Workbench 主按钮和 WebSocket 实时 ASR 链路已经接上，且服务端确实启动了 `sherpa_onnx_realtime` sidecar。
- 当前真实麦克风输入仍是近静音/无有效语音，因此 `real_mic_gate_a` 和 `real_mic_gate_b` 都保持 No-Go。
- “文字又没有了”的直接体验问题已修复一部分：当用户已经打开一场有文字会议，再启动真实麦克风但失败/空 ASR 时，页面会保留上一场会议，并把失败原因显示在当前状态区。

后端 Agent 最新只读审查补充：

- 后端已经有防 fake/demo 进入验收的护栏，但仍是 MVP 单体编排：`app.py` 同时承担生产、demo、mock、local-event、LLM、ASR、storage、evidence policy。
- 最大架构风险不是某个 fake 明目张胆进入产品路径，而是产品路径、demo 路径、证据路径共用同一套薄元数据约定，时间久了容易被测试便利性反向污染。
- 中期需要拆 `ProductionRecognizerFactory` / `DemoRecognizerFactory`、`EvidencePolicyService`、`LlmExecutionService`、`DeletionService`，生产 app 默认不挂 demo/mock routes。
- 但当前不做全站重构，P0 仍只推进能改变 7 个 release-decisive state 的任务。

## 9. 执行计划与 Checklist

### 阶段 0：当前状态止血，已完成

- [x] 多 Agent 审查前端、后端、文档。
- [x] 确认 8765 旧进程缓存旧 HTML。
- [x] 重启 8765，使 `/workbench` 返回新版容器。
- [x] 浏览器验证新版 Workbench：`hasOldStream=false`。
- [x] 点击 `试用示例`，验证 demo 可渲染，但标记为不计入真实验收。
- [x] 修正 `test_app.py` 两处旧契约断言，使其验证新增 provider/provenance 元信息。
- [x] `test_app.py` 全量恢复绿灯：`82 passed, 2 warnings`。

### 阶段 1：生产主线边界硬化

- [x] TDD：新增 source/provenance contract 测试，mock/demo/local-event/degraded 默认 `acceptance_eligible=false`。
- [x] TDD：真实会议模式 sidecar 不可用时 hard fail，不允许静默 fake fallback 成功。
- [x] TDD：mock/degraded session 默认禁止进入 enabled LLM cards/approach/minutes，除非显式 demo/test mode。
- [ ] TDD：测试命名清理，把假 real/mainline 改成 connection/mock/stub 名称。

### 阶段 2：Workbench 产品化主流程

- [x] P0 止血：后端 `provider_error` 在页面显示为“实时识别不可用”，不再表现为成功录音。
- [x] P0 止血：空 snapshot 不覆盖最后一条 `live-partial`，保留“临时实时文字”。
- [x] P0 止血：`/audio/check` 的 `realtime_asr_available=false` 会约束 `开始会议` 主按钮。
- [x] P0 止血：演示 session 触发建议/方案/复盘时走 `/live/asr/demo/sessions/*` 专用边界；降级/非验收 session 不默认绕过后端门禁。
- [ ] 顶栏收敛为主动作：`开始/结束会议`、`导入录音`、`生成建议`、`生成复盘`、`历史`。
- [ ] `试用示例` 移到明确的演示区域，不与真实会议同级。
- [ ] 页面首屏显示当前 session source badge：真实麦克风/导入录音/演示/降级。
- [x] 麦克风开始阶段显示 `待确认`，不能在服务端确认前标记为真实可验收。
- [x] `可能遗漏` 统一为候选提醒，不计入正式建议卡数量；footer 拆分候选提醒、正式建议、方案分析。
- [x] `整理会议` 使用独立 orchestration，统一 loading、失败汇总和按钮禁用，不再 `.click()` 三连发。
- [x] 真实会议启动失败或空 ASR 时不清空上一场已打开会议；失败态显示在当前状态区，失败 session 进入历史并标记降级。
- [ ] 正式建议、方案、复盘结构化展示，不混入实时文字流。
- [ ] 删除确认展示 session 来源、时间、字数、建议数、复盘状态和删除范围。

### 阶段 3：Real Mic Gate A/B/C

- [ ] Gate A：浏览器真实麦克风采集非静音输入并生成 input health evidence。
- [ ] Gate B：非 fake ASR final >= 1，文本非空。
- [ ] Gate C1：同 session 生成建议卡。
- [ ] Gate C2：同 session 生成方案卡和复盘。
- [ ] Gate C3：历史打开同 session。
- [ ] Gate C4：删除同 session，并验证历史中消失。
- [ ] Gate C5：生成 evidence bundle 和 `go_no_go.md`。

最新 Gate A 记录：

```text
artifacts/tmp/audio_health/gate-a-real-mic-20260708-140009.health.json
artifacts/tmp/acceptance/p0-real-mic-gate-a-20260708-140009/manifest.json
verdict=no_go
health_status=blocked_audio_too_quiet
duration_seconds=15.893
rms=0.0
peak=0.0
active_sample_ratio=0.0
llm_called=false
```

结论：真实麦克风输入仍是 No-Go。按 Stop Rule，Gate A 失败时不跑 Gate B/C，不调用 ASR/LLM 下游。

Workbench 页面真实麦克风记录：

```text
rec_mrboj702:
  chunks=145
  final_count=0
  acceptance_eligible=false
  ui=降级 · 未识别到有效语音

rec_mrboum34:
  chunks=22
  final_count=0
  acceptance_eligible=false
  ui=保留上一场 workbench_mrboukhq；失败会话进入历史
```

### 阶段 4：全站按钮与业务闭环验收

这一阶段不是新增功能发散，而是把当前 Workbench 从“研发控制台”收敛成用户能理解的产品主流程。每个按钮必须有明确业务含义、真实/演示边界和可验收结果。

| 当前入口 | 当前实际行为 | 当前判断 | P0 改造要求 |
|---|---|---|---|
| `开始会议` / `结束会议` | `getUserMedia -> /live/asr/stream/ws/{session_id} -> 结束后拉 /live/asr/sessions/{id}/events` | 主线入口，但 real mic No-Go；sidecar 不可用仍可能 fake fallback | 默认真实会议模式必须 hard fail 或明确 degraded，不允许静默 fake；页面必须显示输入电平、ASR provider、是否可验收 |
| `导入录音` | `POST /live/asr/transcribe-file/sessions`，再拉同 session events | File lane 已 Go | 保留为“导入录音”子链路，不能外推为 real mic Go |
| `生成会议建议` | `POST /live/asr/sessions/{id}/llm-execution-runs`，手动生成正式建议卡 | 可用但不是实时自动建议 | 只能对 `acceptance_eligible=true` 或显式 demo session 开启；候选提醒与正式建议卡分层展示 |
| `分析方案利弊` | `POST /live/asr/sessions/{id}/approach-cards` | 可用但入口过工程化 | 合并进 Copilot 面板或“整理会议”后的方案分析区，不与主录音入口同级 |
| `生成会后复盘` | `POST /live/asr/sessions/{id}/minutes`，右侧 `<pre>` 展示 | 可用但展示偏调试 | 结构化展示摘要、决策、待办、风险、待确认问题和证据 |
| `试用示例` | 前端 `MOCK_PAYLOAD` -> `/live/asr/mock/sessions` | demo only | 移到演示区域，历史和当前 session 必须强标“示例，不计入真实验收” |
| `历史记录` | `GET /live/asr/sessions`，点击再拉 events | 部分可用 | 显示标题/时间/来源/字数/建议数/复盘状态/验收 eligibility |
| `刷新实时文字` | 录音中只提示自动更新，非录音中重新拉 snapshot | 概念误导 | 改为异常恢复/重新加载，不作为主按钮 |
| `删除本次会议` | `DELETE /live/asr/sessions/{id}`，删除 session JSON | 部分可用 | 确认框必须展示来源、时间、字数、建议数、复盘状态和删除范围；后续补音频/export/evidence 删除语义 |

按钮验收规则：

- 每个入口都必须有对应 API、前端状态、失败态和测试。
- mock/demo/local-event/degraded 不能默认进入正式 enabled LLM 链路。
- 用户看到的“建议”必须区分 `候选提醒` 和 `正式 AI 建议卡`。
- 页面第一屏必须回答三个问题：这是真实会议还是演示？语音识别是否可用？AI 分析是否会调用远程中转站？

### 阶段 5：当前执行顺序，不再扩散

当前 active goal 继续沿用，不新建目标：

```text
完成 Meeting Copilot P0 主链路恢复与真实自测：
修复 Workbench 文字消失与 mock/真实混淆问题，
建立主线 evidence bundle，
跑通至少一条录音导入链路和一条真实麦克风链路，
并输出可追溯 Go/No-Go 报告。
```

为了避免继续循环，后续执行只按以下顺序推进：

1. 后端边界 hardening：真实会议模式 sidecar 不可用时 hard fail；mock/degraded/local-event 默认禁止 enabled LLM cards/approach/minutes。
2. Workbench 主流程止血：demo 下沉、主按钮收敛、source badge 强提示、候选/正式建议分离、删除确认补上下文。
3. File lane 回归：确认已 Go 子链路没有被边界改造破坏。
4. Real mic Gate A：只验证真实输入健康；不通过则停止 ASR/LLM 下游。
5. Real mic Gate B：只验证非 fake ASR final；不通过则保持 No-Go。
6. Real mic Gate C：同 session 建议、方案、复盘、历史、删除、evidence bundle。

只要 Gate A/B 任一失败，结论必须写 `Real mic: No-Go`，不得继续用 demo/file lane 包装成主线完成。

## 10. 当前验证记录

本轮已运行：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  tests/test_mainline_evidence_bundle_runner.py
```

结果：

```text
42 passed, 2 warnings
```

本轮修正测试断言后运行：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_app.py
```

结果：

```text
82 passed, 2 warnings
```

Source/provenance contract 切片运行：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py
```

结果：

```text
87 passed, 2 warnings
```

Workbench/主线相关回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  tests/test_mainline_evidence_bundle_runner.py
```

结果：

```text
37 passed, 2 warnings
```

8765 运行态验证：

```text
POST /live/asr/mock/sessions -> event_source.input_source=mock
POST /live/asr/mock/sessions -> event_source.acceptance_eligible=false
GET /live/asr/sessions/{sid}/events -> event_source.acceptance_eligible=false
DELETE /live/asr/sessions/{sid} -> deleted=true
```

Workbench demo smoke：

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

结果：

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

浏览器验证：

```text
url=http://127.0.0.1:8765/workbench
title=会议助手
hasOldStream=false
hasTranscriptStream=true
hasSuggestionsPanel=true
hasApproachPanel=true
```

示例按钮验证：

```text
utteranceCount=4
suggestionLikeCount=4
sessionMeta=演示会议 ... 示例内容，不计入真实验收。
```

Stage 1 hardening 验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py
```

结果：

```text
90 passed, 2 warnings
```

相关套件回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_real_llm_path.py \
  code/web_mvp/backend/tests/test_metrics.py \
  code/web_mvp/backend/tests/test_g3_g4_g5.py \
  code/web_mvp/backend/tests/test_e2e_mainline.py \
  code/web_mvp/backend/tests/test_shadow_trial.py
```

结果：

```text
61 passed, 2 warnings
```

Workbench P0 止血回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py
```

```text
31 passed, 2 warnings
```

核心回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py
```

```text
136 passed, 2 warnings
```

Workbench demo smoke：

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

DEC-232 Workbench P0 产品语义修复 focused tests：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_real_mic_source_badge_stays_pending_until_server_snapshot \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_footer_separates_candidate_formal_and_approach_counts \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_organize_meeting_uses_real_orchestrator_not_button_clicks
```

```text
3 passed, 2 warnings
```

DEC-232 Workbench 全量与核心回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q code/web_mvp/backend/tests/test_workbench.py
```

```text
38 passed, 2 warnings
```

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py
```

```text
145 passed, 2 warnings
```

DEC-234 Workbench 真实麦克风失败恢复 focused test：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_real_mic_failure_restores_previous_readable_session
```

```text
1 passed, 2 warnings
```

DEC-234 Workbench 全量：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py
```

```text
39 passed, 2 warnings
```

DEC-234 核心回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py
```

```text
107 passed, 2 warnings
```

DEC-234 Workbench demo smoke：

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

DEC-234 真实页面验证：

```text
加载示例会议 workbench_mrboukhq -> 4 条文字可见
启动真实麦克风 -> 状态为“待确认”，页面提示没有检测到麦克风声音
结束麦克风 -> 生成降级会话 rec_mrboum34，final_count=0
页面结果 -> 保留上一场 workbench_mrboukhq 的 4 条文字，并在状态区显示失败原因
```

真实麦克风 Gate A：

```text
artifacts/tmp/audio_health/gate-a-real-mic-20260708-135928.health.json
health_status=blocked_audio_too_short
duration_seconds=9.472
rms=0.0
peak=0.0
active_sample_ratio=0.0

artifacts/tmp/audio_health/gate-a-real-mic-20260708-140009.health.json
health_status=blocked_audio_too_quiet
duration_seconds=15.893
rms=0.0
peak=0.0
active_sample_ratio=0.0
```

real mic lane evidence bundle：

```text
artifacts/tmp/acceptance/p0-real-mic-gate-a-20260708-140009/manifest.json
verdict=no_go
audio_source=real_mic
real_mic_health_status=blocked_audio_too_quiet
llm_called=false
final_segment_count=0
```

音频/evidence 工具测试：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  tests/test_audio_capture_healthcheck.py \
  tests/test_real_mic_full_chain_runner.py
```

```text
15 passed, 1 warning
```

## 11. 当前目标状态

当前 active goal 仍保持：

```text
完成 Meeting Copilot P0 主链路恢复与真实自测：
修复 Workbench 文字消失与 mock/真实混淆问题，
建立主线 evidence bundle，
跑通至少一条录音导入链路和一条真实麦克风链路，
并输出可追溯 Go/No-Go 报告。
```

但根据本轮审查，必须重新解释为：

```text
File lane: 已 Go，继续回归。
Real mic lane: 只按 Gate A/B/C 串行推进。
Production MVP: Gate C 通过前保持 No-Go。
```

## 12. 多 Agent 审查后的最新判断

2026-07-08 再次按前端、后端、文档、测试四路只读 Agent 对抗审查后，当前判断更新为：

```text
产品初心没有丢：中文技术会议实时 AI Copilot，不是转写工具。
主线没有完成：real mic Gate A/B/C 仍未通过。
当前不是空壳：file lane、demo smoke、LLM cards、approach、minutes、history、delete 都有局部实现。
当前也不是生产级：页面主流程、真实/演示边界、非空 final gate、删除语义和 real mic evidence 都不足。
```

为什么用户会感觉“越做越复杂、主线没跑通”：

- 文档层存在历史入口堆叠：旧 readiness/preflight/wrapper、synthetic preview、07-07 real mic 记录、07-08 file lane Go 都还在，容易被误当当前主线。
- 页面层仍像研发控制台：九个顶栏按钮同级，`试用示例` 和真实会议同级，`刷新实时文字` 概念误导。
- 产品层把 `候选提醒`、`正式 AI 建议卡`、`方案卡`、`会后复盘` 混得不够清楚，用户看不出 AI 到底是在实时辅助还是事后处理。
- 后端层已阻断 fake fallback，但仍有残余假成功风险：空 final、降级 session、local-event、删除 cascade 文案、测试 fake Go 都可能被误读。
- 测试层能证明工程连接性，但真实主链路证据仍不足：Workbench smoke 是 demo/fake LLM；file lane Go 不等于 real mic Go。

当前阶段只承认以下 release-decisive state：

| State | 当前状态 | 下一步改变它的唯一方式 |
|---|---:|---|
| `backend_acceptance_enforcement` | 部分 Go | demo/test 绕过从生产端点隔离；mock/local/degraded/空 ASR 不可生成正式产物 |
| `workbench_real_demo_separation` | 部分 Go | 继续修真实会议失败不清空上一场、正式建议结构化、删除/evidence 展示 |
| `formal_card_evidence` | 部分 Go / real mic No-Go | 已支持 quote/timestamp/segment 回跳；仍需同 real mic session 产出正式建议卡 |
| `delete_evidence` | No-Go for production | 删除覆盖 session、events、cards、minutes、exports、evidence bundle、音频或保留状态 |
| `real_mic_gate_a` | No-Go | 真实麦克风或用户授权音频必须证明非静音有效输入 |
| `real_mic_gate_b` | No-Go | 同 session 必须产生非 fake、非 fallback、非空 ASR final |
| `real_mic_gate_c` | No-Go | 同 session 必须闭合建议、方案、复盘、历史、删除、evidence |

## 13. P0 恢复执行 Checklist

后续不再按“继续评测评估”推进，只按下列 checklist 执行。每个动作必须能改变上表某个 state，或降低真实/演示混淆风险。

### 13.1 文档与主线收束

- [x] 保留 `docs/current-status-and-p0-execution-plan-2026-07-08.md` 为唯一活文档。
- [x] 记录四路 Agent 审查结论：前端、后端、文档、测试均确认 `file lane Go / real mic No-Go / Production MVP No-Go`。
- [x] 更新 `docs/current-mainline-index.md` 顶部，声明旧 2026-07-03/07-04/07-07/早期 07-08 P0 文档均为 historical context。
- [x] 在 `docs/decision-log.md` 追加 DEC-229：P0 后续只围绕 Workbench 产品主流程、后端验收边界和 real mic Gate A/B/C。

### 13.2 后端 P0 边界 hardening

- [x] 默认真实会议 WebSocket 不允许 fake ASR fallback；sidecar 不可用时返回 `provider_error` 且不持久化 session。
- [x] mock/demo/local-event/fallback/degraded session 默认禁止 enabled LLM cards、approach、minutes。
- [ ] TDD：所有 live session summary/event_source 显式包含 `input_source`、`provider_mode`、`is_mock`、`asr_fallback_used`、`degradation_reasons`、`acceptance_eligible`、`acceptance_blockers`，不能只靠 provider 名称推断。
- [x] TDD：`final_count>=1` 和 `non_empty_transcript=true` 必须成为 enabled LLM 与真实验收 gate 的硬条件。
- [x] TDD：file lane 若 ASR 文本为空，必须 blocked，不允许生成正式建议/方案/复盘。
- [x] TDD：删除接口返回必须拆分 `session_record_deleted`、`audio_deleted`、`exports_deleted`、`evidence_deleted`；没有实际删除的范围不能写成已删除。
- [ ] 后置重构边界：只抽小模块，不做大迁移。优先抽 `asr_acceptance_policy.py`，其余 `asr_session_service.py`、`llm_derivation_service.py`、router 拆分放到 Gate A/B/C 后。

### 13.3 Workbench P0 产品主流程

- [x] `provider_error` 显示为“实时识别不可用”，不再表现为成功录音。
- [x] 空 snapshot 不覆盖最后一条 `live-partial`。
- [x] `/audio/check.realtime_asr_available=false` 会禁用/改写 `开始会议`。
- [x] TDD：首屏必须显示 source badge：`真实麦克风 / 导入录音 / 演示 / 降级`，并展示是否可验收。
- [x] TDD：顶栏只保留主动作：`开始/结束会议`、`导入录音`、`整理会议`、`历史`；`试用示例` 下沉到演示区域。
- [x] TDD：`候选提醒` 不计入正式 `会议建议` 数量；正式 AI 建议卡只出现在右侧建议面板。
- [x] TDD：删除确认展示 session 来源、时间、文字数、正式建议数、方案数、复盘状态和删除范围。
- [x] TDD：真实会议启动失败时不清空上一场已打开会议，失败态必须持久显示在当前状态区。

### 13.4 真实麦克风 Gate A/B/C

- [ ] Gate A：真实麦克风采集非静音输入，保存 input health evidence：设备、授权状态、duration、RMS/peak、active ratio、WAV sha256、volumedetect 或等效日志。
- [ ] Gate A 不通过：停止，不跑 LLM，不写成主链路成功。
- [ ] Gate B：同一 session 产生 `provider_mode=real`、`is_mock=false`、`asr_fallback_used=false`、`final_count>=1`、文本非空。
- [ ] Gate B 不通过：停止，不跑 Gate C，不写成主链路成功。
- [ ] Gate C：同一 session 完成正式建议卡、方案卡、会后复盘、历史恢复、删除验证、evidence bundle 和 `go_no_go.md`。

### 13.5 公开音频 lane

- [ ] 公开音频不替代 real mic，只作为独立 file/public lane。
- [ ] 当前不下载 GB 级公开包，不抓公开视频，不读私有音频。
- [ ] 先修复公开音频测试缺口：`tests/test_asr_event_generation_from_public_or_synthetic_audio.py` 期望的 `tools/asr_event_generation_plan.py` 当前不存在。
- [ ] 只有具备合法 bounded clip manifest 后，才跑 `public audio -> local ASR -> same cards/minutes/UI/delete`。

## 14. 当前立即执行顺序

本轮后续只按下面顺序执行，避免再次陷入循环：

1. 先完成文档收束：主线索引和 DEC-235。
2. 用 TDD 把 demo/test 绕过从生产端点 schema 中隔离，阻止 `allow_non_acceptance_execution` 继续出现在真实产品路径。
3. 用 TDD 补正式建议卡 evidence 展示与点击回跳，让“AI 建议”不再只是文本卡片。
4. 用 TDD 补删除/evidence 范围：session、events、cards、minutes、exports、evidence bundle、audio retention 状态必须逐项展示。
5. 跑核心回归和 Workbench demo smoke，只作为工程 sanity。
6. 进入 real mic Gate A；只有 Gate A 通过才继续 Gate B/C。

禁止把以下动作再当成主线进展：

- 只新增 readiness/preflight/wrapper/report。
- 重跑 demo smoke 并宣称真实会议通过。
- 重跑 file lane 并宣称 real mic 通过。
- real mic 没有非空 final 前继续扩 LLM 质量评测。
- P0 未过前推进 Tauri 安装包、系统音频、移动端、自动建单。

## 15. 2026-07-08 全站溯源审计后的生产交付恢复计划

本节是用户再次要求“启动多 Agent 理解项目、梳理前后端现状、写计划和 checklist、不要继续陷入评估循环”后的执行收束。它不新增另一个总控文档，仍以本文档作为唯一活计划。

### 15.1 当前产品真实进度

```text
Demo/mock UI: 可演示，但只算 demo。
File upload lane: Go，可作为授权录音导入子链路基线。
Real mic browser lane: No-Go，入口已接上，但有效输入和非空 ASR final 未通过。
Real mic command lane: No-Go，已有静音/太低音量证据。
Production MVP: No-Go，Gate C 通过前不能发布。
```

项目不是空壳，也不是生产级。当前已经有：

- Workbench 主页面、真实麦克风入口、导入录音入口、候选提醒、正式建议、方案分析、会后复盘、历史、删除。
- 后端 live ASR session、WebSocket 实时 ASR 入口、上传文件 batch ASR、OpenAI-compatible LLM 调用、minutes/approach/cards API。
- file lane evidence bundle：`uploaded_wav -> local_funasr_batch -> real_gateway -> cards -> approach -> minutes -> UI -> delete` 已有 `verdict=go` 记录。
- 真实麦克风失败时不清空上一场可读会议的 UX 修复。

但仍缺少生产交付必需的四个闭环：

- 真实麦克风有效输入与非 fake ASR final。
- 同一真实 session 的正式建议卡、方案、复盘、历史、删除和 evidence bundle。
- 生产 API 与 demo/mock/test 绕过的物理隔离。
- 用户可理解的简洁主流程 UI，而不是研发控制台。

### 15.2 为什么还有 mock/demo 数据

`MOCK_PAYLOAD`、`/live/asr/mock/sessions`、demo smoke 仍然保留，原因是它们承担两类合法用途：

- 本地 UI/交互回归：不用真实麦克风、不调用真实 ASR/LLM，也能验证页面、历史、删除、候选提醒和布局没有坏。
- 演示入口：用户可以点“试用示例”理解产品形态。

它们不能承担的用途：

- 不能证明真实会议跑通。
- 不能证明 ASR 可用。
- 不能证明正式 LLM 建议质量。
- 不能证明 Production MVP。

因此后续保留 demo/mock，但必须继续隔离：

- demo 路由只能返回 `input_source=mock`、`provider_mode=mock`、`acceptance_eligible=false`。
- demo session 可以显式绕过生成演示建议，但绕过字段不能继续污染生产端点。
- Workbench 必须把 demo 标成“示例内容，不计入真实验收”。
- 测试命名不能再用 `real/mainline` 描述 fake/mock 路径。

### 15.3 前端现状与问题

当前 `workbench.html/js` 已经完成一批主流程修复：

- 顶栏主动作已收敛到 `开始会议`、`导入录音`、`整理会议`、`历史记录`、`删除本次会议`。
- `试用示例` 已下沉到演示区域。
- `source-badge` 已能区分演示、导入、待确认、真实麦克风、降级。
- 候选提醒与正式建议计数已拆分。
- `整理会议` 已改成 orchestration，不再 `.click()` 三连发。
- 真实麦克风失败或空 ASR 时，会恢复上一场已打开的可读会议。

仍然存在的 P0/P1 问题：

- 页面仍有较多工程按钮和名词：`生成会议建议`、`分析方案利弊`、`生成会后复盘`、`重新加载文字`。用户会觉得复杂。
- 正式建议卡的 evidence 目前仍偏弱，用户无法像产品需求要求那样直接跳回原话和时间戳。
- 会后复盘展示仍偏 Markdown/调试形态，未形成结构化块：摘要、决策、待办、风险、待确认问题。
- 删除确认虽然更诚实，但 production delete 还没有覆盖 audio/export/evidence bundle，只能显示 `not_tracked`。
- “实时建议”产品价值仍未完全成立：目前会中自动的是候选提醒，正式建议仍主要由用户点击 `整理会议` 触发。

### 15.4 后端现状与问题

当前后端已有重要护栏：

- 默认真实会议 WebSocket 不允许 fake ASR fallback。
- sidecar 不可用时返回 `provider_error`，不再静默持久化 fake session。
- mock/demo/local-event/fallback/degraded/空 ASR 默认不能进入 enabled LLM cards/approach/minutes。
- event_source 已包含 `input_source`、`provider_mode`、`is_mock`、`asr_fallback_used`、`acceptance_eligible`、`acceptance_blockers`、`final_count`、`non_empty_transcript` 等验收字段。
- 删除接口返回结构化 `delete_scope`，不会把未追踪的 audio/export/evidence 假装成已删除。

仍然存在的生产级问题：

- `app.py` 约 1600+ 行，混合了 production、demo、mock、local-event、ASR、LLM、storage、evidence policy、delete。它可以继续支撑 P0 修复，但不适合长期生产维护。
- DEC-236 后，`allow_non_acceptance_execution` 不再在产品请求模型里；生产派生端点收到该字段会 422，demo/test 绕过能力迁到 `/live/asr/demo/sessions/*`。
- `get_recognizer()` 仍有 fake fallback 工厂。当前上层会挡住，但生产 factory 应该 fail closed，不应该返回 fake provider。
- `real_mic_full_chain_runner.py` 不是最终产品 Gate C，它是“真实麦克风录音 -> 本地 sherpa 文件 replay -> local-event-file handoff”。它只能做 Gate B 的辅助影子验证，不能替代 Workbench browser live mic。
- JSON/in-memory repo 适合本地技术预览，不是生产存储。并发、schema migration、审计、音频资产生命周期仍未完成。

### 15.5 下一批只允许执行的 P0 工作

下一批工作必须改变 7 个 release-decisive state 之一。优先顺序如下：

1. `backend_acceptance_enforcement`
   - 把 `allow_non_acceptance_execution` 从生产端点请求模型隔离到 demo/dev-only 入口。
   - 生产端点不接受 mock/local/degraded/空 ASR 生成正式 cards/approach/minutes。
   - 测试覆盖：生产请求带绕过字段也不能通过；demo endpoint 或测试 factory 可以显式演示。

2. `formal_card_evidence`
   - 正式建议卡必须显示 evidence quote、时间戳、来源 segment。
   - 点击 evidence 能定位到对应 transcript 或至少高亮对应原文块。
   - LLM 输入不能只传 evidence id，必须能解析到可追溯原话。

3. `delete_evidence`
   - 删除响应继续逐项返回 `session_record/events/cards/minutes/exports/evidence_bundle/audio_retention`。
   - UI 删除确认和删除结果必须展示每一项实际状态：`deleted`、`not_found`、`not_tracked`、`retained_by_policy`。
   - 不能把 `not_tracked` 写成 `deleted`。

4. `workbench_real_demo_separation`
   - 将右侧工具按钮进一步收束成一个主动作 `整理会议` 和一个可展开的“高级操作”区。
   - 降低页面术语：用户第一眼只看到“开始会议 / 导入录音 / 整理会议 / 历史 / 删除”。
   - 页面第一屏持续回答：这是真实会议还是示例？ASR 是否可用？AI 是否会调用远程中转站？

5. `real_mic_gate_a/b/c`
   - Gate A 只跑真实输入健康，失败即停止。
   - Gate B 只在 Gate A Go 后验证非 fake ASR final。
   - Gate C 只在 Gate B Go 后验证同 session cards/approach/minutes/history/delete/evidence。

### 15.6 不再继续做的工作

以下任务暂时停止，除非它们直接服务于上面的 P0 状态：

- 新增 ASR provider 横评。
- 新增 readiness/preflight/approval wrapper。
- 新增 synthetic/replay/report-only 工具。
- 重复证明 demo smoke。
- 用 file lane Go 外推 real mic Go。
- P0 未通过前推进 Tauri 安装包、Windows、移动端、系统音频、多 speaker、自动建单。
- 大爆炸式拆分 `app.py`。当前只允许小步抽取 P0 policy/service，避免重构本身拖垮主线。

### 15.7 执行 checklist

- [x] TDD：生产端点不再接受 `allow_non_acceptance_execution` 作为绕过正式 LLM gate 的公开字段。
- [x] TDD：demo/dev-only 路由保留演示能力，派生响应带 `execution_boundary=demo_non_acceptance_execution`。
- [x] TDD：正式建议卡渲染 evidence quote、timestamp 和 segment id。
- [x] TDD：点击 evidence 后 transcript 中对应原文可定位或高亮。
- [x] TDD：LLM suggestion cards 输入使用 evidence quote，不只使用 evidence id。
- [ ] TDD：approach/minutes 输入也结构化使用 evidence quote，而不是只拼接 transcript。
- [ ] TDD：删除响应和 UI 删除结果逐项展示 `session/events/cards/minutes/exports/evidence/audio_retention` 状态。
- [ ] TDD：Workbench 高级操作区收起低频按钮，主流程只保留 5 个核心动作。
- [x] TDD：Workbench HTML 使用 2026-07-08 JS cache-busting 版本，避免浏览器继续加载旧脚本。
- [ ] TDD：Workbench AI 调用透明度修正，明确 ASR correction 可能调用 LLM，或关闭默认隐式纠错调用。
- [ ] 自测：核心后端回归、Workbench 全量测试、demo smoke、file lane evidence bundle 回归。
- [ ] 真实自测：Gate A 通过前不跑 Gate B/C；Gate A 失败必须生成 No-Go evidence。
- [ ] 真实自测：Gate A Go 后才跑 Workbench browser live mic Gate B/C，并生成同 session `go_no_go.md`。

### 15.8 成功标准

下一阶段只有满足以下条件，才允许把状态从 `Production MVP: No-Go` 改为候选 Go：

- `real_mic_gate_a=Go`：真实麦克风或用户授权音频非静音、可识别。
- `real_mic_gate_b=Go`：同 session 有非 fake、非 fallback、非空 ASR final。
- `real_mic_gate_c=Go`：同 session 完成正式建议、方案、复盘、历史恢复、删除、evidence bundle。
- `formal_card_evidence=Go`：建议卡能回到原话证据。
- `delete_evidence=Go`：删除范围不再只停留在 session JSON。
- `backend_acceptance_enforcement=Go`：生产端点没有 demo/test 绕过污染。
- `workbench_real_demo_separation=Go`：用户不会把 demo、file lane、真实麦克风三者混淆。

### 15.9 DEC-236 实施记录：生产端点移除 demo/test 绕过口

本轮已完成第一项 P0 修复，改变 `backend_acceptance_enforcement` 状态。

改动：

- `CreateLlmExecutionRunsRequest` 移除 `allow_non_acceptance_execution`。
- 生产端点 `/live/asr/sessions/{id}/llm-execution-runs`、`/approach-cards`、`/minutes`、`/minutes.json` 一律使用正式验收 gate。
- 新增 demo 专用端点 `/live/asr/demo/sessions/{id}/llm-execution-runs`、`/approach-cards`、`/minutes`、`/minutes.json`，内部显式标记 `execution_boundary=demo_non_acceptance_execution`。
- Workbench demo session 的建议、方案、复盘改走 demo 专用端点；真实/导入 session 仍走生产端点。
- Workbench HTML 脚本版本更新为 `20260708-p0-boundary`，降低旧 JS 缓存导致“页面还是旧功能”的风险。

验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_production_derivation_endpoints_reject_non_acceptance_bypass_field \
  code/web_mvp/backend/tests/test_app.py::test_demo_derivation_endpoint_can_execute_mock_session_without_public_bypass_field
```

```text
2 passed, 2 warnings
```

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py \
  code/web_mvp/backend/tests/test_real_llm_path.py \
  code/web_mvp/backend/tests/test_metrics.py \
  code/web_mvp/backend/tests/test_g3_g4_g5.py \
  code/web_mvp/backend/tests/test_e2e_mainline.py \
  code/web_mvp/backend/tests/test_shadow_trial.py
```

```text
161 passed, 2 warnings
```

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

```bash
git diff --check
```

```text
passed with no output
```

当前状态：

```text
backend_acceptance_enforcement: 部分 Go -> 更接近 Go，但仍需继续处理 fake recognizer factory、LLM 输入 evidence quote 和生产/demo router 物理隔离。
Real mic: No-Go
Production MVP: No-Go
```

### 15.10 DEC-237 实施记录：正式建议卡补齐 EvidenceSpan quote/timestamp/回跳

本轮已完成第二项 P0 修复，改变 `formal_card_evidence` 的结构性状态，但不改变真实麦克风 Go/No-Go。

改动：

- 后端 `_execution_previews_from_record()` 会从 transcript final events 的 `payload.evidence_spans` 建 evidence 索引。
- LLM execution preview/run/card 增加 `evidence_spans` 和 `evidence_context`，其中 `evidence_context` 形如 `[00:00-00:03] 先灰度 10%。`。
- `llm_service.execute_candidate()` 现在把 `evidence_context` 作为 LLM user prompt 的证据上下文，并把 `evidence_spans` 持久化进正式建议卡。
- Workbench 正式建议卡不再只显示 evidence id，会展示证据时间窗和原话 quote。
- Workbench transcript utterance 增加 segment/evidence data attributes；点击建议卡 evidence 会滚动并高亮对应原话。

验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_returns_preview_queue_without_calling_llm \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_formal_suggestions_render_evidence_quotes_and_clickback
```

```text
2 focused tests passed
```

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_minutes.py \
  code/web_mvp/backend/tests/test_llm_service.py \
  code/web_mvp/backend/tests/test_real_llm_path.py \
  code/web_mvp/backend/tests/test_metrics.py \
  code/web_mvp/backend/tests/test_g3_g4_g5.py \
  code/web_mvp/backend/tests/test_e2e_mainline.py \
  code/web_mvp/backend/tests/test_shadow_trial.py
```

```text
169 passed, 2 warnings
```

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

```bash
git diff --check
```

```text
passed with no output
```

当前状态：

```text
formal_card_evidence: 部分 Go，real mic 仍 No-Go。
Real mic: No-Go。
Production MVP: No-Go。
```

### 15.11 三路全站审计与 DEC-238 实施记录：生产拒绝 mock LLM provider

本轮按用户要求重新启动三路多 Agent 对抗审查，并把结论落到：

```text
docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md
```

审计结论：

- 当前项目不是空壳：Workbench、file lane、demo smoke、LLM cards、approach、minutes、history、delete 都有局部实现。
- 当前项目也不是生产级：真实麦克风完整产品链路仍 No-Go，正式建议不满足 10-30 秒准实时自动出现，删除/evidence/audio retention 未闭合。
- File lane 是当前唯一可信 Go 子链路，不能外推为 real mic Go。
- Public audio lane 只有白名单/no-download 边界，没有跑到公开音频 ASR -> 正式建议卡 Go。
- Workbench 和测试命名仍有 demo/file/real mic/degraded 混淆风险。

本轮完成的 P0 后端边界修复：

- 生产派生端点新增 LLM provider gate。
- `LLM_GATEWAY_IS_MOCK=true` 时，生产 `/live/asr/sessions/{id}/llm-execution-runs`、`/approach-cards`、`/minutes`、`/minutes.json` 统一返回 409。
- demo 路由 `/live/asr/demo/sessions/*` 继续允许 mock LLM，用于 UI 回归和示例，不计入真实验收。

Focused TDD：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_production_derivation_endpoints_reject_mock_llm_provider
```

```text
1 passed, 2 warnings
```

相关边界回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_production_derivation_endpoints_reject_mock_llm_provider \
  code/web_mvp/backend/tests/test_app.py::test_demo_derivation_endpoint_can_execute_mock_session_without_public_bypass_field \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_execution_runs_enabled_rejects_mock_session_without_explicit_demo_allowance \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_enabled_approach_and_minutes_reject_mock_session_without_explicit_demo_allowance
```

```text
4 passed, 2 warnings
```

当前状态：

```text
backend_acceptance_enforcement: 部分 Go -> 更接近 Go。
File lane: Go。
Real mic: No-Go。
Production MVP: No-Go。
```

### 15.12 DEC-239/240/241 与真实麦克风 Gate A 复测

本轮按用户要求重新做前后端主线溯源，并把详细审计追加到：

```text
docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md
```

新结论：

- 前端不是空白页，Workbench 有真实麦克风、导入录音、整理会议、历史、删除、建议、方案、复盘入口。
- 当前最强自动化 UI 证据仍是 demo/mock smoke，不能算真实会议 Go。
- “开麦后文字没了”的直接原因是：旧逻辑开始录音就创建 `rec_*` 并清空主视图；真实 ASR 没有非空 final 时，空 snapshot 覆盖用户看到的内容。
- 后端审计发现：缺少显式 metadata 的 recognizer 可能默认被当作 `real`，有生产验收误放行风险。

本轮已修：

- DEC-240：`recognizer` 缺少 `provider/provider_mode/is_mock/fallback_used` 任一 metadata 时，默认 fail closed：`provider_mode=unknown/is_mock=true/fallback_used=true/degradation_reasons=recognizer_metadata_missing`，生产 WebSocket 返回 provider_error，不持久化 session。
- DEC-241：Workbench 增加录音草稿状态。若当前已有上一场可读会议，点击开始记录不会立即清空主视图；只有收到第一条非空 partial/final/transcript_final 才切到新会议文字。provider_error、空 final、空 snapshot 会恢复上一场会议。

验证：

```text
test_asr_stream.py + test_real_asr_to_cards.py = 8 passed, 2 warnings
test_workbench.py = 41 passed, 2 warnings
主线相关回归 = 172 passed, 2 warnings
node code/web_mvp/e2e/workbench_smoke.mjs = workbench smoke OK
git diff --check = passed
```

真实麦克风 Gate A 复测：

```text
artifacts/tmp/audio_health/gate-a-real-mic-20260708-160858.health.json
artifacts/tmp/acceptance/p0-real-mic-gate-a-20260708-160858/manifest.json
```

结果：

```text
verdict=no_go
health_status=blocked_audio_too_quiet
rms=0.0
peak=0.0
active_sample_ratio=0.0
asr_provider=not_started
llm_called=false
```

Stop rule：

- Gate A 未过，所以本轮不跑 Gate B/C。
- 不调用 ASR。
- 不调用 LLM。
- 不把 demo smoke 或 file lane Go 外推为真实麦克风 Go。

当前状态仍为：

```text
Demo/mock: Go for demo only
File lane: Go
Real mic: No-Go
Production MVP: No-Go
```

### 15.13 DEC-242：真实 Workbench 页面麦克风 Gate A 取证

本轮继续按用户要求跑真实页面主链路，不再只做工具侧边界测试。

已完成：

- Workbench 增加浏览器侧麦克风健康报告 `workbench_browser_mic_health`。
- 该报告在停止录音或 WebSocket 关闭时发布。
- 报告写入 `document.body.dataset.browserMicHealth`，并输出 JSON console log，方便自动化取证。
- 报告明确声明：

```text
raw_audio_uploaded=false
remote_asr_called=false
llm_called=false
```

TDD：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_tracks_browser_mic_health_for_gate_a_evidence
```

结果：

```text
1 passed, 2 warnings
```

真实页面复测：

```text
page=http://127.0.0.1:8765/workbench?reload=browser-health2
script=/static/workbench.js?v=20260708-p0-browser-health2
session_id=rec_mrbtjiwq
```

页面录音中状态：

```text
rec_state=● 录音中
source_badge=待确认
sys_status=没有检测到麦克风声音。请检查浏览器权限、macOS 输入设备或输入音量；外放声音不一定会进入麦克风输入。
session_meta=录音中 · 34 条实时文字
```

停止后浏览器侧健康报告：

```text
sample_count=163840
chunk_count=35
rms=0
peak=0
active_sample_ratio=0
health_status=blocked_audio_too_quiet
```

停止后后端 session：

```text
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
degradation_reasons=[asr_final_empty]
final_count=0
non_empty_final_count=0
acceptance_eligible=false
acceptance_blockers=[degraded_asr_session, asr_final_missing, asr_transcript_empty]
```

证据文件：

```text
artifacts/tmp/audio_health/workbench-browser-mic-health-20260708-163131.json
```

结论：

- 真实 Workbench 页面主路径已触发：浏览器麦克风 -> WebSocket -> sherpa realtime -> session snapshot -> UI 降级展示。
- 但 Gate A 仍失败：浏览器采样全部为 0，`rms/peak/active_sample_ratio` 都是 0。
- 这次不是 LLM 慢，也不是建议卡链路先坏，而是没有有效音频输入，所以 stop rule 仍成立：不跑 Gate B/C，不调用 LLM。
- 下一步必须先解决 macOS/浏览器输入路由或改用系统音频/虚拟声卡；否则继续测 ASR/LLM 会继续空转。

## 2026-07-08 Update: Real Mic Recorded Realtime Lane 已通过

新增当前主线状态：

```text
Demo/mock: Go for demo only
File lane: Go
Simulated realtime wav: Go
Real mic recorded realtime lane: Go
Browser live mic lane: Not yet proven
Production MVP: Conditional No-Go
```

本轮新增 `real-mic-recorded-realtime` evidence lane，解决了此前真实麦克风 Gate A 通过后没有推进到 Gate B/C 的缺口。该 lane 的输入是已授权的真实麦克风 WAV 和对应健康报告，runner 会把 WAV 按实时 chunk 发送到 WebSocket：

```text
/live/asr/stream/ws/{session_id}?audio_source=real_mic_recorded_wav
```

通过证据：

```text
artifact_root=artifacts/tmp/acceptance/p0-real-mic-recorded-realtime-afplay-20260708-01
verdict=go
audio_source=real_mic_recorded_wav
counts_as_real_mic_go_evidence=true
browser_live_mic_go_evidence=false
asr_provider=sherpa_onnx_realtime
asr_fallback_used=false
llm_provider=real_gateway
transcript_char_count=86
suggestion_card_count=3
approach_card_count=3
minutes_char_count=404
workbench_same_session_visible=true
frontend_card_count=6
delete_verified=true
degradation_reasons=[]
```

重要边界：

- 这是“真实麦克风录音回放实时链路” Go，不是浏览器 `getUserMedia` live mic Go。
- Workbench 已将该来源展示为“真实麦克风录音”，避免和浏览器实时采集混淆。
- 当前不应继续重复录音边界实验；下一步主线是浏览器 live mic gate 和生产级音频质量/语义质量提示。

报告入口：

```text
docs/p0-real-mic-recorded-realtime-selftest-report-2026-07-08.md
```

## 2026-07-08 Update: 下一阶段完成目标和自测总计划

剩余工作已经收束到：

```text
docs/meeting-copilot-completion-target-and-selftest-plan-2026-07-08.md
```

该文档明确：

- 当前真实状态基线。
- 生产级 MVP 的 10 个完成条件。
- P0/P1/P2/P3 剩余工作总清单。
- 每项工作的涉及文件、步骤和自测命令。
- 固定回归命令。
- Release candidate gates。
- Stop rules。

下一阶段推荐目标：

```text
完成 Browser live mic evidence lane，并让未通过时机器化输出 No-Go。
```

执行顺序：

```text
1. Browser live mic evidence lane
2. 实时自动建议 orchestrator
3. ASR semantic quality gate
4. Release acceptance runner
5. 录音导入/导出、Provider 成本、隐私删除和长会稳定性
```

在 browser live mic bundle 通过前，项目状态仍保持：

```text
Production MVP: Conditional No-Go
```

## 2026-07-08 Update: P0-4 Workbench 产品化 UI 已完成到可验证状态

本轮把 Workbench 从工程测试台收敛成会议中可用的产品页面，但这只改变 C4/P0-4，不改变生产发布 verdict。

完成项：

```text
首屏主操作=开始会议 / 结束会议 / 导入录音 / 历史记录
session-only 操作=整理会议 / 刷新文字 / 删除本次会议 / 生成会议建议 / 分析方案利弊 / 生成会议纪要
主分区=实时文字 / 实时建议 / AI 建议 / 方案分析 / 会议纪要 / 历史记录
用户文案=语音识别 / AI 分析 / 依据 / 会议纪要
移动布局=@media (max-width:900px) + browser layout overflow check
```

验证证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_workbench.py
47 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_workbench.py code/web_mvp/backend/tests/test_app.py -k 'workbench or delete_asr_live_session or privacy'
48 passed, 87 deselected, 2 warnings

node code/web_mvp/e2e/workbench_smoke.mjs
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified, screenshots=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/ui_screenshots/workbench-p0-4-smoke

git diff --check
clean
```

截图证据：

```text
artifacts/tmp/ui_screenshots/workbench-p0-4-smoke/workbench-desktop.png
artifacts/tmp/ui_screenshots/workbench-p0-4-smoke/workbench-mobile.png
```

仍未完成：

```text
C1 Browser live mic evidence lane: Not yet proven
C2 正式建议自动触发: Not yet implemented
C3 ASR 语义质量 gate: Not yet integrated into live/web acceptance
C10 Release acceptance runner: Not yet implemented
Production MVP: Conditional No-Go
```
