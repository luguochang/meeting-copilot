# Meeting Copilot 生产主线恢复计划与 Checklist - 2026-07-07

> 状态：执行目标文档
> 来源：4 个只读 Agent 审计结果汇总：需求/设计文档、前端真实度、后端结构、主流程测试证据。
> 目标：停止继续堆 readiness/preflight/report/no-op 脚手架，把项目重新收敛到真实产品主流程：**真实或用户授权音频 → ASR → EvidenceSpan/会议状态 → 有证据建议卡 → 会后纪要 → 前端展示/历史/删除**。
> 边界：本计划不是宣布 Production MVP ready；它定义从当前状态走向生产级可交付产品的执行顺序和验收标准。

---

## 0. 用户意图复述与执行原则

用户明确指出：

1. 当前项目不能继续在局部细节上打补丁。
2. 前端看起来不像真实产品，仍混有 mock/demo/debug/readiness 内容。
3. 后端结构越来越复杂，`app.py` 与多套 session/ASR/LLM/report 链路混在一起。
4. 项目可能偏离最初需求文档与代码设计文档。
5. 最重要的是先跑通主流程，再做结构治理和生产级优化。
6. 后续可以使用多 Agent、自动化工具、全站溯源重构，但目标是产品主线恢复，而不是继续制造复杂度。

因此后续执行原则是：

```text
先证明真实主流程成立
  -> 再收敛前端产品入口
  -> 再拆后端结构
  -> 再补生产级安全/观测/稳定性
```

凡是不直接改善以下状态的工作，默认后置：

- 真实音频输入是否可用；
- ASR final/revision 是否稳定产生；
- 是否生成 EvidenceSpan；
- 是否基于 EvidenceSpan 生成正式建议卡/纪要；
- 前端是否展示同一次真实 session 的转写、卡片、纪要；
- 是否有持久化验收证据包。

---

## 1. 当前真实状态判断

### 1.1 项目不是 0，也不是生产可用

当前更准确的状态是：

```text
Local Shadow Preview / PC Web MVP mainline partially usable
不是 Production MVP
不是 Shadow Pilot ready
也没有充分证据证明完整真实端到端已跑通
```

已有能力：

- `/workbench` 已经接入后端 API、文件上传、WebSocket ASR、历史、删除、LLM 建议、方案分析、纪要生成。
- 后端已有 ASR live session 主线雏形：音频/文件/mock ASR -> ASR live events -> suggestion candidates -> LLM cards -> approach cards -> minutes。
- 有多个接近真实链路的 e2e 脚本，例如真实 wav + WebSocket + sherpa sidecar + LLM API 路径。
- 有前端 Chrome smoke，可验证 `/workbench` 页面、示例会话、建议卡、方案卡、纪要、删除。

不足：

- 没有发现一个持久化 artifact 证明：**同一次真实音频输入，经真实 ASR、真实 LLM，生成纪要，并在前端展示**。
- 很多测试名叫 real/e2e，但内部仍使用 fake recognizer、mock ASR session、fake LLM、本地 mock OpenAI server。
- 旧 `/index.html` + `app.js` 是研发验收台，不是生产用户页。
- `app.py` 过胖，后端 route、service、policy、persistence orchestration 混在一起。
- 普通 session 与 ASR live session 是两套模型。
- ASR sidecar 不可用时可能 silent fallback fake recognizer，生产语义危险。
- LLM correction、suggestion、approach、minutes 的配置、降级、持久化语义不统一。

### 1.2 主线定义

需求文档定义的产品主线不是“转写 + 总结”，而是：

```text
真实中文技术会议音频
  -> 实时 ASR partial/final/revision
  -> 稳定转写与 EvidenceSpan
  -> 工程语境门禁
  -> 会议状态机
       Topic / DecisionCandidate / ActionItem / Risk / OpenQuestion
  -> 缺口雷达
       owner / deadline / rollback / test / monitoring
  -> 低打扰建议卡片
       10-30 秒窗口、有证据、可保留/忽略/复制/标错
  -> 带证据会后纪要
  -> Markdown/JSON 导出
  -> 历史/反馈/删除
```

最低可交付 Web MVP 主线可收敛为：

```text
导入录音或开始会议
  -> ASR live session
  -> 实时文字
  -> 建议卡片/方案卡
  -> 会后复盘
  -> 历史记录
  -> 删除
```

---

## 2. 偏离主线的问题清单

### 2.1 前端问题

#### P0 问题

- `/workbench` 虽接了真实 API，但真实麦克风浏览器链路没有 E2E 覆盖。
- 真实 LLM + 浏览器 UI 链路没有 E2E 覆盖。
- “试用示例”仍用前端硬编码 `MOCK_PAYLOAD` 与 `/live/asr/mock/sessions`。
- 旧 `/` 页面仍大量暴露 fixture、synthetic preview、mainline trial、desktop readiness、native bridge、noop validation。
- 产品主入口与研发验收入口没有清晰隔离。

#### P1 问题

- 录音权限拒绝、WebSocket 断线、AudioContext 不支持、LLM 不可用等错误状态不够产品化。
- 长会议 DOM/event 内存增长没有治理。
- 卡片生命周期不完整：keep/dismiss/copy/wrong/evidence jump/feedback 未形成统一产品闭环。
- 历史记录缺更完整的 session 标题、搜索、排序、状态说明。
- `/workbench` 用户页缺明显 Markdown/JSON 导出入口。

### 2.2 后端问题

#### P0 问题

- `app.py` 超过 1400 行，承担 app factory、路由、schema、业务编排、持久化协调、校验、静态页面等职责。
- ASR live session 用裸 dict，普通 session 用 `SessionRecord`，两套生命周期割裂。
- ASR sidecar 不可用时 fallback fake recognizer，生产下可能伪装成真实 ASR。
- `minutes` 与 `minutes.json` 持久化语义不一致。
- LLM 配置散落，缺统一 typed settings，缺 fail-fast readiness。

#### P1 问题

- `asr_stream.py` 同时管理 WebSocket、sidecar subprocess、ASR correction、normalization、session persistence。
- LLM correction、suggestion、approach、minutes 共用同一网关配置，但没有按用例区分模型、timeout、token budget、retry、prompt version。
- metrics 是内存计数器，不覆盖 ASR latency、LLM latency、degraded reasons、sidecar lifecycle、token usage、错误分类。
- 文件上传缺生产级限制：大小、格式、超时、资源配额、恶意文件防护。
- API 缺认证/授权；本地 MVP 可接受，网络暴露不可接受。

### 2.3 测试与验收问题

#### P0 问题

- 没有统一 acceptance runner。
- e2e 脚本分散，很多输出只 print 或写 `/tmp`，没有标准证据包。
- 没有一个持久化证据包证明完整链路：音频输入 -> ASR -> LLM -> 纪要 -> 前端展示。
- 测试命名与真实程度容易混淆：real/e2e 可能仍是 fake recognizer 或 local mock LLM。

#### P1 问题

- 缺真实 provider 与 mock provider 的显式分层。
- 缺 ASR 质量、LLM 质量、前端 UI 展示统一 Go/No-Go。
- 缺真实会议 shadow trial 人工标注闭环。

---

## 3. 单一事实源规则

从本计划开始，任何验收报告必须明确标记以下维度，不允许混用：

| 维度 | 可选值 |
|---|---|
| audio_source | mock_events / uploaded_wav / ws_wav_simulation / real_mic / system_audio |
| asr_provider | fake / sherpa / funasr_streaming / funasr_batch / remote |
| llm_provider | disabled / fake_client / local_mock_openai / real_gateway |
| ui_coverage | none / API_only / headless_chrome / manual_browser |
| persistence | memory / json_file / artifact_bundle |
| verdict | go / no_go / degraded / inconclusive |

任何报告如出现以下情况，不能称为真实端到端通过：

- `LLM called = false`，但声称 LLM 链路通过；
- 使用 fake/local mock LLM，但声称真实 LLM 通过；
- 使用 mock ASR session，但声称真实 ASR 通过；
- 没有前端展示，却声称完整产品链路通过；
- 没有 artifact bundle，只靠终端输出；
- ASR segment_count = 0；
- minutes 为空；
- card 没有 evidence。

---

## 4. P0：先跑通最小真实主流程

### 4.1 Lane 1：录音文件转换全链路（优先）

原因：文件上传 + FunASR batch 是当前最可能形成高质量闭环的路径，也有明确产品价值。

目标链路：

```text
上传 wav
  -> /live/asr/transcribe-file/sessions
  -> FunASR batch 或明确真实 ASR provider
  -> ASR live session
  -> session events
  -> LLM suggestion cards
  -> approach cards
  -> minutes markdown/json
  -> /workbench 历史中打开同一 session
  -> 前端展示 transcript/cards/minutes
  -> 删除 session
  -> artifact bundle
```

Checklist：

- [ ] 使用固定音频 fixture，并记录 sha256。
- [ ] 启动后端，使用唯一数据目录。
- [ ] 配置真实 LLM gateway；如不可用，报告必须标记为 degraded/inconclusive，不能 Go。
- [ ] 上传 wav 到 `/live/asr/transcribe-file/sessions`。
- [ ] 保存上传 response。
- [ ] 保存 session events。
- [ ] 保存 raw transcript。
- [ ] 保存 normalized transcript。
- [ ] 计算或记录技术实体 recall。
- [ ] 调 `/llm-execution-runs`。
- [ ] 调 `/approach-cards`。
- [ ] 调 `/minutes`。
- [ ] 调 `/minutes.json` 或统一后的 JSON endpoint。
- [ ] 打开 `/workbench`。
- [ ] 从历史打开该 session，或通过 URL/API 加载该 session。
- [ ] 验证实时文字区域显示 transcript。
- [ ] 验证实时建议区域显示 suggestion cards。
- [ ] 验证方案卡显示。
- [ ] 验证会后复盘显示。
- [ ] 保存前端截图。
- [ ] 保存 browser console/network errors。
- [ ] 删除 session。
- [ ] 验证删除后历史/事件/计数清理。
- [ ] 输出 `go_no_go.md`。

Go 条件：

- [ ] transcript 非空。
- [ ] ASR provider 不是 fake。
- [ ] LLM provider 是 real_gateway。
- [ ] suggestion card 至少 1 条，且每条有 evidence。
- [ ] minutes markdown 非空。
- [ ] minutes JSON 结构完整。
- [ ] workbench 成功展示同一 session 的 transcript/cards/minutes。
- [ ] 删除生效。
- [ ] artifact bundle 完整。

### 4.2 Lane 2：实时 WebSocket 音频全链路（第二优先）

目标链路：

```text
wav 模拟麦克风经 WebSocket 输入
  -> partial/final/revision
  -> ASR live session 落库
  -> LLM cards/minutes/approach
  -> /workbench 展示
  -> artifact bundle
```

Checklist：

- [ ] 复用或改造 `real_e2e_real_llm.py`，输出 artifact bundle。
- [ ] 保存 WebSocket 收到的 partial/final events。
- [ ] 保存落库后的 transcript_final events。
- [ ] 记录 ASR provider 与是否 fallback。
- [ ] production/acceptance 模式禁止 fake fallback。
- [ ] 调真实 LLM。
- [ ] 打开 `/workbench` 查看该 session。
- [ ] 保存截图和 console logs。
- [ ] 输出 Go/No-Go。

Go 条件：

- [ ] 收到 final transcript。
- [ ] session events 非空。
- [ ] ASR provider 不是 fake。
- [ ] LLM called true。
- [ ] cards/minutes 非空。
- [ ] UI 可见。

说明：如果 sherpa 实时准确率暂时低，该 lane 可先作为 smoke；ASR 质量 Go 优先由 Lane 1 文件转换链路承担。

---

## 5. P0：建立验收证据包

所有 acceptance run 必须输出目录：

```text
artifacts/tmp/acceptance/YYYYMMDD-HHMMSS-mainline/
```

必须包含：

- [ ] `manifest.json`
- [ ] `input_audio.sha256`
- [ ] `env_redacted.json`
- [ ] `server.log`
- [ ] `asr_events.json`
- [ ] `session_events.json`
- [ ] `transcript.raw.txt`
- [ ] `transcript.normalized.txt`
- [ ] `entity_recall.json`
- [ ] `llm_runs.json`
- [ ] `suggestion_cards.json`
- [ ] `approach_cards.json`
- [ ] `minutes.md`
- [ ] `minutes.json`
- [ ] `frontend_screenshot.png`
- [ ] `browser_console.json`
- [ ] `network_errors.json`
- [ ] `quality.json`
- [ ] `go_no_go.md`

`manifest.json` 必须写明：

```json
{
  "audio_source": "uploaded_wav | ws_wav_simulation | real_mic",
  "asr_provider": "funasr_batch | sherpa | fake | ...",
  "llm_provider": "real_gateway | local_mock_openai | fake_client | disabled",
  "ui_coverage": "headless_chrome | manual_browser | API_only",
  "data_dir": "...",
  "started_at": "...",
  "ended_at": "...",
  "verdict": "go | no_go | degraded | inconclusive"
}
```

---

## 6. P0：前端收敛为产品工作台

短期策略：

- `/workbench` 作为默认产品主入口。
- 旧 `/index.html` + `app.js` 降级为 debug/internal route，或仅 dev mode 暴露。
- 生产主界面不再出现 mock、fixture、synthetic、events.sse、sidecar、noop、readiness、gateway、tokens、model 等研发黑话。

P0 Checklist：

- [ ] 默认入口指向 `/workbench` 或明确产品入口。
- [ ] `试用示例` 明确标记为示例，不得混称真实会议。
- [ ] mock route 只在 dev/demo mode 可见或可用。
- [ ] 开始会议按钮状态：未开始/录音中/整理中/已结束清晰。
- [ ] 导入录音为主路径之一。
- [ ] 历史记录能打开真实上传/录音 session。
- [ ] 会后复盘显示已生成 minutes。
- [ ] 建议卡、方案卡、纪要刷新后不丢。
- [ ] 删除 session 后清理 transcript、cards、minutes、计数、底部状态。
- [ ] 前端 smoke 不只跑“试用示例”，新增真实 session 加载展示测试。

P1 Checklist：

- [ ] 麦克风权限拒绝有用户引导。
- [ ] WebSocket 断线有可理解状态。
- [ ] LLM 不可用显示 degraded，不伪装成功。
- [ ] 长会议 event/DOM 有窗口化或分页策略。
- [ ] 卡片支持保留、忽略、复制、标错。
- [ ] 卡片可跳转证据片段。
- [ ] 导出 Markdown/JSON 入口进入 `/workbench`。

---

## 7. P0/P1：后端主线收敛

### 7.1 固定 ASR live session 为当前唯一产品主线

短期不要继续扩展普通 session 与 ASR live session 两套产品路径。

Checklist：

- [ ] 定义 `LiveMeetingSession` schema 或等价 Pydantic model。
- [ ] session 字段至少包含：session_id、source、provider、events、transcript、suggestion_cards、approach_cards、minutes、usage、degradation_reasons、created_at、updated_at。
- [ ] 所有 `/workbench` API 围绕 live session。
- [ ] 普通 `/sessions` 标记 legacy/demo 或适配到 live session。
- [ ] minutes markdown/json 持久化语义统一。

### 7.2 禁止生产 silent fake fallback

Checklist：

- [ ] 引入 `MEETING_COPILOT_ENV=development|test|production`。
- [ ] 引入 `ASR_MODE=fake|sherpa|funasr|auto`。
- [ ] 引入 `ALLOW_FAKE_ASR=true|false`。
- [ ] test/dev 可 fake。
- [ ] acceptance/production 下 ASR 不可用必须 503 或 fail-fast。
- [ ] artifact manifest 记录是否 fallback。
- [ ] `/health/live` 与 `/health/ready` 区分。

### 7.3 拆 `app.py`，但按主流程小步拆

目标结构建议：

```text
meeting_copilot_web_mvp/
  app.py                  # create_app only
  config.py               # typed settings
  api/
    health.py
    workbench.py
    asr_live.py
    llm.py
    minutes.py
    sessions_legacy.py
  services/
    asr_live_service.py
    llm_execution_service.py
    minutes_service.py
    transcript_service.py
  domain/
    live_session.py
    events.py
    cards.py
  repositories/
    live_session_repository.py
    json_live_session_repository.py
```

迁移顺序：

- [ ] `/live/asr/sessions/{id}/llm-execution-runs` -> `llm_execution_service.py`
- [ ] `/live/asr/sessions/{id}/approach-cards` -> `llm_execution_service.py` 或独立 service
- [ ] `/live/asr/sessions/{id}/minutes` -> `minutes_service.py`
- [ ] `/live/asr/transcribe-file/sessions` -> `asr_live_service.py`
- [ ] local file validators -> 独立 helper/service
- [ ] `app.py` 只保留 create_app、middleware、include_router

### 7.4 Typed settings

Checklist：

- [ ] 新增 `config.py`。
- [ ] 集中读取 env。
- [ ] LLM、ASR、storage、upload limits、timeouts 都通过 settings 注入。
- [ ] 测试通过 test settings 注入，减少 monkeypatch env。
- [ ] readiness 对必要配置 fail-fast 或明确 degraded。

### 7.5 LLM 执行统一

Checklist：

- [ ] SuggestionCardOutput schema。
- [ ] ApproachCardOutput schema。
- [ ] MinutesOutput schema。
- [ ] AsrCorrectionOutput schema。
- [ ] 统一 LLM Gateway：timeout、retry、error classification、usage extraction、trace id。
- [ ] prompt version 进入 artifact/metrics。
- [ ] 缺配置、网关失败、JSON invalid、部分 candidate 失败的 degraded contract 统一。

---

## 8. 自动化测试分层命名规范

后续测试/脚本必须按真实程度命名：

- `unit_mock_*`
- `contract_mock_http_*`
- `integration_fake_asr_fake_llm_*`
- `integration_real_asr_mock_llm_*`
- `integration_real_asr_real_llm_*`
- `ui_smoke_fake_llm_*`
- `acceptance_real_audio_real_asr_real_llm_ui_*`

每个 e2e/acceptance 输出必须打印并写入 manifest：

- ASR provider；
- LLM provider；
- audio source；
- UI coverage；
- artifact directory；
- verdict。

---

## 9. Go / No-Go 标准

### 9.1 Web MVP mainline usable Go

- [ ] Lane 1 文件上传全链路通过。
- [ ] 使用真实 ASR provider，不是 fake。
- [ ] 使用真实 LLM gateway，不是 fake/local mock。
- [ ] `/workbench` 能展示同一 session 的 transcript/cards/minutes。
- [ ] minutes markdown/json 非空且结构完整。
- [ ] suggestion cards 100% 有 evidence。
- [ ] 删除 session 后页面和后端数据清理。
- [ ] artifact bundle 完整。

### 9.2 Realtime Copilot first-pilot Go

- [ ] Lane 2 实时 WS 链路通过。
- [ ] 建议卡在相关 final segment 后 10-30 秒内出现，或明确降级为会后待确认。
- [ ] 中文技术实体 recall >= 0.8 first-pilot 门槛。
- [ ] 至少 1 场真实授权中文技术会议 shadow trial 解除 inconclusive。
- [ ] 最好 3 场真实会议，有人工标注。
- [ ] 建议卡 useful/wrong/too_late/too_intrusive 反馈可记录。

### 9.3 Production MVP No-Go

以下任一出现即 No-Go：

- [ ] ASR segment_count = 0。
- [ ] ASR provider 是 fake 却未明确标记。
- [ ] LLM called = false 却声称 LLM 通过。
- [ ] local mock OpenAI 被当作真实 LLM provider。
- [ ] 会议纪要为空。
- [ ] 卡片无 evidence。
- [ ] 低置信度 ASR 被 LLM 放大为强结论。
- [ ] 前端没有验证。
- [ ] 无 artifact bundle。
- [ ] 删除语义不可解释或不可执行。
- [ ] 隐私状态不透明。

---

## 10. 推荐执行顺序

### Step 1：建立 acceptance runner 与证据包

目标：不要再靠零散脚本和口头判断。

- [ ] 新增或改造 acceptance runner。
- [ ] 先支持 Lane 1 文件上传全链路。
- [ ] 输出标准 artifact bundle。
- [ ] 失败时也保存证据。

### Step 2：让 `/workbench` 加载 acceptance session

目标：同一次真实 session 必须前端可见。

- [ ] 历史列表展示 acceptance session。
- [ ] 打开 session 后显示 transcript/cards/minutes。
- [ ] 前端测试保存截图/console。

### Step 3：修正 production fake fallback 语义

目标：不要让 fake 伪装真实。

- [ ] acceptance 模式禁止 fake fallback。
- [ ] health/ready 展示 ASR/LLM/storage 状态。

### Step 4：统一 live session schema 与 minutes 持久化

目标：减少两套模型和刷新丢失。

- [ ] LiveMeetingSession schema。
- [ ] minutes markdown/json 同步保存。
- [ ] cards/approach/minutes 与 session 生命周期一致。

### Step 5：小步拆 `app.py`

目标：不要一次大爆炸重构。

- [ ] 先拆 LLM execution service。
- [ ] 再拆 minutes service。
- [ ] 再拆 file transcribe service。
- [ ] 最后拆 routers。

### Step 6：前端产品/研发入口隔离

目标：生产用户只看到主流程。

- [ ] `/workbench` 产品化。
- [ ] 旧 `/` debug 化或 dev flag。
- [ ] mock/demo 文案清晰隔离。

### Step 7：实时 WS 链路 acceptance

目标：从“文件转换可用”推进到“实时 Copilot 可用”。

- [ ] 改造 `real_e2e_real_llm.py` 输出 artifact。
- [ ] 前端展示同一 session。
- [ ] 记录 latency 与 recall。

### Step 8：真实会议 shadow trial

目标：验证产品价值，不再只验证代码路径。

- [ ] 至少 1 场真实授权中文技术会议。
- [ ] 最好 3 场，每场 15-30 分钟。
- [ ] 人工标注卡片 useful/wrong/too_late/duplicate/intrusive。
- [ ] 人工标注纪要 missing/wrong/correct。
- [ ] 输出 first-pilot Go/No-Go。

---

## 11. 后置生产级工作

主流程未跑通前，不优先做：

- Tauri 安装包、签名、公证；
- 系统音频完整采集；
- 多用户账号体系；
- Postgres/云端部署；
- 长会议全面性能优化；
- 完整 OpenTelemetry/Prometheus；
- 企业权限/审计；
- 多会议知识库；
- 跨会议记忆。

主流程跑通后再补：

- [ ] 认证/授权或 local-only 绑定策略。
- [ ] 上传文件大小/MIME/超时/配额。
- [ ] ASR sidecar process pool、日志、健康检测、并发上限。
- [ ] LLM cost/token/rate-limit。
- [ ] 数据 retention 与彻底删除。
- [ ] 长会议内存与 UI 虚拟列表。
- [ ] 真实系统音频采集。
- [ ] Tauri/macOS 打包。

---

## 12. 最终目标

最终目标不是“测试都绿”或“文档很多”，而是可交付产品满足：

```text
用户能在真实中文技术会议中手动开始会议；
系统能稳定采集音频并产生可用 ASR；
系统能基于稳定证据维护会议状态；
系统能在合适窗口给出低打扰、有证据的工程建议；
会后能生成可追溯纪要；
用户能在前端查看、导出、反馈、删除；
所有验收都有可复验 artifact。
```

如果真实会议里只能得到转写，不能得到及时、有证据、低打扰的提醒，则产品主命题仍未成立。
