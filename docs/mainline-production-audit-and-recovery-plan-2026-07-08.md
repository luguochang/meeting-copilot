# Meeting Copilot 主线审计与生产级恢复计划 - 2026-07-08

> 状态：主线恢复执行基线
> 来源：2026-07-08 多 Agent 审计，覆盖产品需求、前端体验、后端架构、真实全链路证据。
> 目标：停止继续扩大边界测试，把项目拉回最初产品初心：**中文技术会议实时 Copilot，而不是录音转文字工具**。
> 当前结论：项目不是从零开始，但也远未达到生产级可交付。当前应定义为 `Local Web Demo / 内部技术预览`，不能定义为 Production MVP。

---

## 0. 执行结论

你对当前项目的担心是成立的。

当前最大问题不是某一个按钮、某一个接口或某一个样式坏了，而是四条边界混在了一起：

1. **真实链路和 mock/demo 链路混在一起**，导致用户无法判断看到的是能力证明，还是演示数据。
2. **前端没有把主流程做成简单产品**，而像研发实验台，按钮多、概念多、状态反馈弱。
3. **后端结构过度集中**，`app.py` 承担路由、服务编排、领域规则、文件读写、demo 工具、校验策略等多种职责。
4. **验收证据不统一**，很多测试叫 real/e2e，但内部仍可能使用 fake ASR、mock LLM 或 sample session。

下一阶段必须先做 P0 主线恢复：

```text
真实或用户授权音频
  -> 非 fake ASR
  -> ASR live session
  -> 前端同一场会议可见实时文字
  -> LLM 建议卡 / 方案分析 / 会后复盘
  -> 历史记录
  -> 删除
  -> 可追溯证据包
```

如果这条链路没有跑通，后续桌面壳、移动端、系统音频、复杂架构拆分都只能后置。

---

## 1. 原始产品初心

产品需求文档定义的 Meeting Copilot 是：

```text
中文技术会议实时 AI Copilot
不是通用音频转文字工具
不是会后总结工具
不是研发验收台
```

核心价值必须体现在会中：

- 实时识别讨论主题、候选决策、行动项、风险、未闭环问题。
- 根据 EvidenceSpan 给出低打扰建议卡，提醒 owner、deadline、rollback、test、monitoring 等工程缺口。
- 会后生成带证据的纪要，而不是凭空总结。
- 用户能看到每条建议为什么出现、依据哪段会议原话。

产品 No-Go 条件仍然有效：

- 只有实时文字，没有建议卡，失败。
- 只有会后总结，没有会中提醒，失败。
- 建议卡没有证据，失败。
- mock 数据被当成真实能力展示，失败。
- 中文技术会议 ASR 不稳定，失败。
- 前端看不懂、点不通、状态不可信，失败。

---

## 2. 当前代码与文档现实

### 2.1 文档现实

当前 `docs/` 下已有 161 个 Markdown 文档。文档不是太少，而是主线过多：

- 有产品需求：`docs/product-requirements.md`
- 有 PC Local Web MVP：`docs/pc-local-web-mvp-requirements.md`
- 有最小价值 demo：`docs/minimum-valuable-demo-script.md`
- 有大量 readiness、preflight、desktop、real-mic、selftest 文档
- 已有 2026-07-07 的恢复计划：`docs/production-mainline-recovery-plan-2026-07-07.md`

问题是：文档数量已经超过执行吸收能力，必须重新确立一个当前唯一主线。

本文件从 2026-07-08 起作为新的主线恢复入口。后续重要决策必须继续写回此文件或在同目录新增决策记录，并在此文件索引。

### 2.2 代码现实

当前关键文件规模：

| 文件 | 行数 | 风险 |
|---|---:|---|
| `code/web_mvp/backend/meeting_copilot_web_mvp/app.py` | 1469 | API、服务、规则、持久化、demo 混在一起 |
| `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js` | 609 | UI 状态、音频采集、API、渲染、mock payload 混在一起 |
| `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py` | 405 | WebSocket、ASR provider、LLM correction、落库混在一起 |
| `code/web_mvp/backend/meeting_copilot_web_mvp/llm_service.py` | 444 | 同步 HTTP、重试、prompt、provider 元信息混在一起 |
| `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html` | 127 | 产品页雏形存在，但操作区与概念组织不成熟 |

这说明项目不是没有做东西，而是已经堆出了多个可用雏形，但没有形成可交付主干。

---

## 3. 多 Agent 审计结论

### 3.1 产品需求 Agent 结论

当前产品定位仍应坚持：

```text
实时会议 Copilot > 会议转写 > 会后总结
```

必须保留的价值链：

- EvidenceSpan 作为证据硬约束。
- Engineering context gate，非工程会议不触发工程建议。
- Topic / DecisionCandidate / ActionItem / Risk / OpenQuestion。
- owner / deadline / rollback / test / monitoring 缺口雷达。
- 每场会议低频建议卡，而不是刷屏。
- 会后纪要必须能回跳证据。

当前最大产品风险：

```text
mock / fake / demo 能力看起来像真实产品能力。
```

### 3.2 前端 Agent 结论

当前 `/workbench` 已具备雏形：

- 开始会议
- 导入录音
- 生成会后复盘
- 生成会议建议
- 分析方案利弊
- 试用示例
- 历史记录
- 刷新实时文字
- 删除本次会议

但生产前端不达标，P0 问题包括：

- `试用示例 -> 渲染文字` 当前失败。点击后等待 `.utterance` 超时。
- 服务端 `/live/asr/sessions/{id}/events` 当前会触发 `NameError: _asr_live_event_source_metadata`。
- 前端拿到 `created.live_events` 后又强依赖二次 `/events`，二次失败时没有兜底渲染。
- 示例会议和真实会议没有明确产品边界。
- 录音中仍可点击示例、上传、删除、生成，状态机不安全。
- 主内容区在等待 ASR 时容易空白，用户会以为没有开始。
- 建议卡、方案卡、实时提醒都 append 到 transcript 底部，用户不容易看到。
- 历史记录缺标题、来源、示例/真实标签、选中态、删除上下文。
- 页面命名复杂，`可能遗漏`、`实时建议`、`提醒` 等概念混乱。

前端结论：

```text
当前 workbench 更像研发实验台，不像用户可放心使用的产品页。
```

### 3.3 后端 Agent 结论

后端当前最大问题是单体过胖：

- `app.py` 已经不是 API 层，而是路由、服务编排、领域规则、文件读写、安全策略、demo 工具的混合体。
- `/demo/fixtures`、`/live/asr/mock/sessions`、`/live/asr/transcribe-file/sessions`、真实 ASR WS、LLM endpoints 全部在同一个 app 默认暴露。
- LLM 调用是同步 HTTP 路径，失败重试里有阻塞 sleep。
- 文件转写接口在 async route 中一次性读完整上传文件，并同步跑 FunASR subprocess。
- `asr_stream.py` 自动从真实 provider fallback 到 Fake，生产语义危险。
- 配置读取散落在 `metrics.py`、`llm_service.py`、`app.py`。
- JSON 文件存储只适合作为本地 MVP，不适合生产并发和审计。

后端结论：

```text
当前后端可以继续作为本地 demo 骨架，但不能按这个结构直接推进生产级。
```

### 3.4 全链路证据 Agent 结论

已经有过部分真实验证：

- 浏览器真实麦克风 -> WebSocket -> local ASR -> persistence 曾经手动验证过。
- 示例/历史/纪要/删除 smoke 曾经通过过，但使用 sample + fake local LLM。
- 本地 persistence/history/minutes/cards 有测试覆盖。

但还没有足够证据证明：

- 一段真实中文技术会议音频跑过非 fake ASR。
- 同一场真实 session 在前端展示 transcript、cards、approach、minutes。
- 外部真实 LLM gateway 的质量、稳定性、成本可接受。
- 真实麦克风长时间会议的延迟、准确率、断线恢复可用。
- 系统音频链路可用。
- Tauri/Mac/Windows 桌面壳可用。

全链路结论：

```text
当前不是 0，但真实产品主链路仍未闭环。
```

---

## 4. 当前能力矩阵

| 能力 | 当前状态 | 说明 |
|---|---|---|
| 本地 Web UI 打开 | 部分通过 | `/workbench` 存在，但产品体验和状态机不足 |
| 真实麦克风采集 | 部分通过 | 曾经手动验证，但缺稳定自动化证据 |
| 文件导入转写 | 部分通过 | 接口存在，真实 provider 与错误处理需重新验收 |
| ASR final 入库 | 部分通过 | 有 ASR live session 结构，但 mock/real 边界混乱 |
| 中文技术会议准确率 | 未证明 | 需要固定评测集与真实会议 shadow trial |
| EvidenceSpan | 部分通过 | 结构存在，但前端展示与正式约束不足 |
| 建议卡 | 部分通过 | 有候选/正式卡，但 evidence、生命周期、UI 可见性不足 |
| 方案利弊分析 | 部分通过 | endpoint 与 UI 入口存在，真实质量未验收 |
| 会后复盘 | 部分通过 | Markdown/JSON 雏形存在，展示和证据追溯不足 |
| 历史记录 | 部分通过 | 能列出，但缺标题、来源、真实/示例标签、误删保护 |
| 删除 | 部分通过 | endpoint 存在，但删除语义和原始音频生命周期不足 |
| 真实 LLM gateway | 部分通过 | OpenAI-compatible 路径存在，但生产成本/超时/降级未闭环 |
| 系统音频 | 未完成 | MVP 原需求有，但当前 Web 优先阶段先后置 |
| 桌面安装包 | 未完成 | 当前先做本地 Web MVP，桌面壳后置 |
| 生产级后端分层 | 未完成 | `app.py` 单体过胖 |
| 生产级安全/权限 | 未完成 | 本地 MVP 可接受，网络暴露不可接受 |

---

## 5. 当前 P0 Bug 与偏航点

### 5.1 直接导致页面“文字没了”的问题

当前发现明确 P0：

```text
GET /live/asr/sessions/{session_id}/events
  -> NameError: _asr_live_event_source_metadata
```

证据：

- `app.py:749` 调用了 `_asr_live_event_source_metadata(record)`。
- 代码中没有发现该函数定义。
- 前端 `workbench.js:266` 在试用示例后强依赖这个 `/events` endpoint。
- 因此示例 session 即使已经创建，也会因为二次读取失败而无法渲染文字。

这个 bug 必须在 P0 修复，不应继续绕开。

### 5.2 mock 仍在主产品路径里

当前前端仍包含：

- `workbench.js:10` 的 `MOCK_PAYLOAD`
- `workbench.js:262` 调用 `/live/asr/mock/sessions`
- `app.py:508` 暴露 `/live/asr/mock/sessions`

允许保留 demo，但必须改成清晰隔离：

- UI 明确标记“演示数据”。
- 历史列表标记来源。
- 证据报告标记 `asr_provider=fake/local_mock_asr`。
- 真实验收不能使用 mock session。

### 5.3 后端路由职责过宽

当前同一个 `app.py` 同时包含：

- health / metrics / audio check
- workbench 静态页
- demo fixtures
- mock ASR sessions
- local event file ingest
- live ASR sessions
- ordinary sessions
- LLM drafts/previews/runs
- approach cards
- minutes
- report/delete
- validation helpers

这会导致：

- 修改任一主线都容易影响其他链路。
- 测试越来越庞大。
- mock/demo 很难从生产入口剥离。
- 后续桌面端复用 core 时边界不清。

---

## 6. 重要决策记录

### DEC-2026-07-08-001：当前先做 PC Local Web MVP，不先做桌面壳

决定：

```text
优先把 /workbench 本地 Web 主链路做通，再进入 Tauri/Mac/Windows 安装包。
```

理由：

- 当前主链路本身仍未闭环。
- 桌面壳只会增加权限、打包、签名、系统音频、更新机制等复杂度。
- 真实价值必须先在 Web MVP 中证明。

影响：

- Mac 客户端、Windows 客户端、系统音频作为 P1/P2。
- 当前 P0 只要求 Mac 本机浏览器可用，真实麦克风/导入录音可用。

### DEC-2026-07-08-002：真实链路与 demo 链路必须强隔离

决定：

```text
任何 fake/mock/demo/sample 链路都不能被称为真实端到端通过。
```

执行规则：

- UI 标记 session 来源。
- API 返回 `provider_mode` 或等价字段。
- 证据包记录 `audio_source`、`asr_provider`、`llm_provider`、`ui_coverage`、`persistence`。
- 生产 profile 禁止 ASR 自动 fallback Fake。

### DEC-2026-07-08-003：下一阶段只允许围绕 P0 主链路修复

决定：

```text
下一轮不做新功能扩张，不做桌面壳，不做移动端，不做大规模边界探索。
```

允许做：

- 修复当前主流程 bug。
- 简化前端主流程。
- 建立真实/示例标签。
- 建立证据包 runner。
- 拆出最小后端 service/router 边界，前提是不破坏当前接口。

### DEC-2026-07-08-004：费用边界保持不变

决定：

```text
默认新增费用只允许来自用户显式配置的 OpenAI-compatible LLM 中转站。
```

影响：

- 不默认接阿里/讯飞等付费远程 ASR。
- 本地 ASR 仍是优先方向。
- 远程 ASR 可作为以后可选 provider，不进入默认 P0。

---

## 7. P0 恢复目标

### 7.1 P0 目标一句话

完成一个可以反复运行的证据链：

```text
真实麦克风或公开测试音频
  -> ASR
  -> Workbench 实时/会后展示
  -> LLM 建议卡 / 方案分析 / 纪要
  -> 历史
  -> 删除
  -> artifact bundle
```

### 7.2 P0 不做范围

当前不做：

- Tauri/Mac 安装包。
- Windows 安装包。
- iOS/Android。
- 系统音频采集。
- 多人 speaker diarization。
- Jira/Linear/GitHub issue 自动创建。
- 云端账号和同步。
- 新增付费 ASR provider 默认接入。

这些不是放弃，而是等 P0 主链路闭环后再排期。

---

## 8. P0 执行阶段与 Checklist

### Phase A：主线冻结与真实/示例边界修复

目标：用户打开 `/workbench` 不再被 mock 和真实混淆，页面能正常显示文字。

- [ ] 修复 `/live/asr/sessions/{session_id}/events` 的 `_asr_live_event_source_metadata` 缺失问题。
- [ ] 给 ASR live session summary/events 返回明确来源字段：`audio_source`、`asr_provider`、`is_mock`、`provider_mode`。
- [ ] 前端加载示例时，即使二次 `/events` 失败，也先用 `created.live_events` 渲染，并显示错误提示。
- [ ] `试用示例` 改名为 `查看演示` 或放入次级入口。
- [ ] 示例 session 在历史列表中标记 `演示`，真实 session 标记 `真实录音` 或 `麦克风`。
- [ ] 录音中禁用导入、演示、删除、生成类按钮。
- [ ] 开始会议后立即显示 `正在听 / 等待语音 / 正在识别`，避免空白。
- [ ] 给删除确认增加 session 来源、时间、字数、建议数。
- [ ] 增加前端 smoke：示例创建后必须出现至少 1 条 `.utterance`。

验收：

- [ ] 点击演示后能看到文字。
- [ ] 历史能区分演示和真实。
- [ ] 录音中不能切换到演示或删除当前 session。
- [ ] 页面上不再让用户误以为 mock 就是真实会议能力。

### Phase B：Workbench 主流程产品化

目标：让页面变成简单产品，而不是研发实验台。

页面主操作收敛为：

```text
开始会议 / 结束会议
导入录音
生成建议
生成复盘
历史
删除
```

界面信息架构：

- [ ] 顶部只保留主状态和核心操作。
- [ ] 左侧或上方显示当前会议来源、ASR provider、LLM provider、是否演示。
- [ ] 中间分成稳定容器：`实时文字`、`实时建议`、`方案分析`、`会后复盘`。
- [ ] 建议卡和方案卡不再 append 到 transcript 底部。
- [ ] 生成动作有明确 loading、success、error 状态。
- [ ] 复盘用结构化 Markdown 展示，不只放在 `<pre>`。
- [ ] 历史列表显示标题、时间、来源、字数、建议数、复盘状态、当前选中态。
- [ ] 错误提示持久显示在状态区，toast 只用于短反馈。

UI 风格原则：

- 使用低噪音工具型界面。
- 避免复杂术语堆叠。
- 避免把研发指标暴露成用户主概念。
- 强调“现在在听什么、识别到了什么、建议了什么、依据是什么”。
- 颜色采用克制的生产力工具风格，避免花哨营销页。

验收：

- [ ] 用户不读文档也能完成：开始会议 -> 看到文字 -> 生成建议 -> 生成复盘 -> 历史打开 -> 删除。
- [ ] 375px、768px、1024px、1440px 宽度下按钮不溢出。
- [ ] 所有按钮都有禁用、加载、失败状态。

### Phase C：证据包 Runner

目标：结束“跑过但说不清跑了什么”的问题。

新增或整理一个主线 runner：

```text
code/web_mvp/e2e/mainline_evidence_bundle.*
```

每次运行生成：

```text
artifacts/mainline/<timestamp>/
  manifest.json
  go_no_go.md
  server.log
  browser-console.log
  network-summary.json
  input-audio.sha256
  session-events.json
  transcript.txt
  suggestion-cards.json
  approach-cards.json
  minutes.md
  minutes.json
  workbench-before.png
  workbench-after.png
```

`manifest.json` 必须包含：

- [ ] `audio_source`
- [ ] `asr_provider`
- [ ] `asr_provider_mode`
- [ ] `llm_provider`
- [ ] `llm_provider_mode`
- [ ] `session_id`
- [ ] `transcript_char_count`
- [ ] `final_segment_count`
- [ ] `suggestion_card_count`
- [ ] `minutes_char_count`
- [ ] `frontend_utterance_count`
- [ ] `frontend_card_count`
- [ ] `delete_verified`
- [ ] `verdict`
- [ ] `degradation_reasons`

Go 条件：

- [ ] ASR provider 不是 fake。
- [ ] transcript 非空。
- [ ] final segment 至少 1 条。
- [ ] LLM provider 是 real_gateway，或报告明确 degraded，不能写 Go。
- [ ] 建议卡至少 1 条，且有 evidence。
- [ ] 复盘非空。
- [ ] 前端截图中可见同一 session 的 transcript 和生成结果。
- [ ] 删除后 session 不再出现在历史列表。

### Phase D：真实音频主链路自测

目标：至少完成两条真实主线，不再只靠 mock。

Lane 1：公开测试音频或用户授权录音文件

- [ ] 准备一段中文技术会议测试音频，记录来源和 sha256。
- [ ] 通过导入录音跑 ASR。
- [ ] 生成建议卡。
- [ ] 生成方案分析。
- [ ] 生成会后复盘。
- [ ] 在 Workbench 历史中打开同一 session。
- [ ] 截图并保存证据包。
- [ ] 删除 session 并验证。

Lane 2：真实麦克风

- [ ] 打开 Workbench。
- [ ] 获取麦克风权限。
- [ ] 外放中文技术会议测试音频或由用户现场说话。
- [ ] 观察 partial/final 产生。
- [ ] 结束会议后生成建议卡/复盘。
- [ ] 保存证据包。

验收：

- [ ] 两条 Lane 都完成，才能说“本地 Web 主链路初步跑通”。
- [ ] 任一 Lane 使用 fake ASR，只能算演示，不算真实主链路。

### Phase E：后端最小生产化拆分

目标：先止住后端继续膨胀，不做大爆炸重构。

拆分顺序：

1. `settings.py`
   - 集中 `data_dir`、`asr_provider`、`llm_gateway`、`enable_demo_routes`、`enable_mock_routes`、`max_upload_mb`。
2. `api/live_asr.py`
   - 迁移 ASR live session routes。
3. `api/llm.py`
   - 迁移 LLM drafts/previews/runs、approach、minutes。
4. `api/demo.py`
   - 迁移 demo/mock routes，并受 feature flag 控制。
5. `services/live_asr_service.py`
   - 聚合 session get/list/delete/persist。
6. `services/llm_execution_service.py`
   - 聚合 LLM preview/run/cost/degradation。
7. `providers/asr/*`
   - 明确 fake/sherpa/funasr provider ports。

约束：

- [ ] 先写 router contract tests，再迁移。
- [ ] 迁移后 endpoint 路径和响应字段保持兼容。
- [ ] 生产 profile 禁止 silent fallback fake ASR。
- [ ] demo/mock route 默认只在 dev profile 打开。

### Phase F：质量门禁

目标：定义什么时候可以说“进入可交付候选”。

必须通过：

- [ ] 后端核心测试通过。
- [ ] Workbench smoke 通过。
- [ ] 主线 evidence bundle 通过。
- [ ] 真实麦克风 Lane 通过。
- [ ] 录音导入 Lane 通过。
- [ ] 文档中没有把 fake/mock 当作真实能力。
- [ ] 关键错误有用户可理解提示。
- [ ] API key 不出现在日志、文档、前端。
- [ ] 删除语义明确。

不能通过即 No-Go：

- [ ] 前端看不到文字。
- [ ] 生成按钮点击后无可见结果。
- [ ] mock session 混进真实历史且无标记。
- [ ] ASR provider 是 fake 但报告写 Go。
- [ ] LLM 未调用但报告写 LLM 链路通过。
- [ ] 只有终端输出，没有 artifact bundle。

---

## 9. 后续执行优先级

### P0：必须立即做

1. 修复 `/events` NameError，恢复文字显示。
2. 修复 Workbench 状态机和按钮禁用。
3. 隔离演示与真实会议。
4. 建立 evidence bundle runner。
5. 跑通录音导入主链路。
6. 跑通真实麦克风主链路。

### P1：P0 通过后做

1. 后端 router/service/settings 最小拆分。
2. LLM 同步调用治理：timeout、retry、cost gate、降级提示。
3. 文件上传限制：大小、格式、超时。
4. 历史记录产品化。
5. Markdown/JSON 导出入口产品化。
6. ASR 中文技术会议评测集继续完善。

### P2：产品可用后做

1. Tauri Mac 桌面壳。
2. Windows 桌面壳。
3. 系统音频采集。
4. 自动更新、签名、公证。
5. iOS/Android 规划。
6. 远程 ASR 可选 provider。

---

## 10. 下一目标定义

下一阶段目标设为：

```text
P0 主链路恢复与真实自测：
修复 Workbench 文字消失与 mock/真实混淆问题，建立主线 evidence bundle，
跑通至少一条录音导入链路和一条真实麦克风链路，
并输出可追溯 Go/No-Go 报告。
```

验收产物：

- [ ] 修复后的代码。
- [ ] 后端/前端相关测试。
- [ ] Workbench smoke 结果。
- [ ] 至少 1 个录音导入 evidence bundle。
- [ ] 至少 1 个真实麦克风 evidence bundle。
- [ ] `docs/mainline-p0-recovery-selftest-report-2026-07-08.md`
- [ ] 当前文档更新状态。

---

## 11. 当前状态标记

| 项 | 状态 |
|---|---|
| 多 Agent 产品审计 | 已完成 |
| 多 Agent 前端审计 | 已完成 |
| 多 Agent 后端审计 | 已完成 |
| 多 Agent 全链路证据审计 | 已完成 |
| 主线恢复计划 | 已完成 |
| P0 代码修复 | 未开始 |
| P0 证据包 runner | 未开始 |
| 录音导入真实链路证据包 | 未完成 |
| 真实麦克风链路证据包 | 未完成 |
| 生产级可交付 | 未达到 |

---

## 12. 给后续开发 Agent 的硬约束

1. 先读本文件，再动代码。
2. 每个重要决策都要写入文档。
3. 不允许把 mock/fake 结果写成真实通过。
4. 不允许新增默认付费 ASR provider。
5. 不允许先做桌面壳绕开 Web 主链路问题。
6. 不允许只修样式、不修状态机。
7. 不允许只跑 API、不看前端。
8. 不允许只有终端日志，没有 evidence bundle。
9. 不允许大爆炸重构；每次拆分都要有 contract test。
10. 用户最终要的是生产级可交付产品，不是测试报告堆栈。
