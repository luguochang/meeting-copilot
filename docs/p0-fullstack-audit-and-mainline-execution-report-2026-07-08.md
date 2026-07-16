# Meeting Copilot P0 全站审计与主线执行报告 - 2026-07-08

> 状态：当前 P0 主线恢复报告
> 来源：用户反馈、产品/前端 Agent、后端/架构 Agent、真实链路/evidence Agent、主线程代码与运行态复核
> 目标：停止评估循环，把后续工作限制在能改变发布 Go/No-Go 的主链路事项上。
> 产品初心：中文技术会议实时 AI Copilot，不是普通音频转文字工具。

## 1. 一句话结论

项目不是空壳，但也还不是可发布产品。

当前可信状态：

| Lane | 状态 | 允许声明 | 禁止声明 |
|---|---:|---|---|
| Demo/mock | Go for demo only | 可用于 UI 回归、示例演示 | 不能算真实会议跑通 |
| File upload | Go | 导入录音子链路已跑通 | 不能外推为实时麦克风会议 Go |
| Public audio | No-Go / 未执行 | 已有白名单/no-download 边界 | 不能说公开音频到建议卡链路通过 |
| Real mic | No-Go | 有入口和历史降级证据 | 不能说真实麦克风全链路通过 |
| Production MVP | No-Go | 本地技术预览 | 不能发布或对外宣称生产级可用 |

主线没有完成的根因不是“完全没代码”，而是：

- 真实麦克风 Gate A/B/C 未通过。
- 正式建议仍主要依赖手动整理，不满足 PRD 的 10-30 秒准实时建议。
- demo/file/real mic/degraded 多条路径在页面和测试命名里容易混淆。
- 删除/evidence bundle/audio retention 仍没有形成产品会话生命周期闭环。
- 后端仍是 MVP 单体，生产、demo、mock、local-event、ASR、LLM、storage、delete policy 混在 `app.py` 中。

## 2. 当初产品目标是否被满足

原始目标是：

```text
真实或用户授权音频
  -> 非 fake ASR
  -> 实时文字
  -> 10-30 秒内建议或明确降级
  -> 方案分析
  -> 会后复盘
  -> 历史恢复
  -> 删除
  -> evidence bundle
```

当前满足情况：

| 能力 | 当前状态 | 说明 |
|---|---:|---|
| 手动开始/停止会议 | 部分满足 | Workbench 有开始/结束；暂停/恢复不是稳定产品入口 |
| 真实麦克风采集 | No-Go | 页面入口存在，但最新 evidence 为近静音、ASR final=0 |
| 导入录音转写 | Go | `uploaded_wav -> local_funasr_batch -> cards/minutes -> UI -> delete` 已有 evidence |
| 实时 ASR 非 fake 边界 | 部分 Go | 默认 fake fallback 已阻断；仍需继续收紧 provider factory 和验收 contract |
| 会中候选提醒 | 部分满足 | 规则候选提醒可自动出现 |
| 正式建议卡 | 部分满足 | 可手动生成，已有 quote/timestamp/clickback；未满足准实时自动生成 |
| 方案分析 | 部分满足 | 可手动生成；evidence 输入仍需结构化强化 |
| 会后复盘 | 部分满足 | 可生成 Markdown；结构化展示和导出体验不足 |
| 历史恢复 | 部分满足 | live ASR sessions 可列、可打开；来源分组和验收语义不足 |
| 删除 | No-Go for production | session JSON 可删；audio/export/evidence bundle 仍 not tracked |
| evidence bundle | 工具层有，产品层未闭合 | runner 可生成 bundle，但 live session repo 不追踪生命周期 |

## 3. 多 Agent 审计结论

### 3.1 产品/前端

Workbench 已经有实际入口：

- `开始会议`
- `导入录音`
- `整理会议`
- `历史记录`
- `删除本次会议`
- 演示示例
- 候选提醒
- 正式建议
- 方案分析
- 会后复盘

但它仍像研发工作台，不像开会时能放心使用的产品：

- demo、导入录音、真实麦克风、降级历史在同一认知层里。
- 候选提醒和正式建议还没有形成清晰生命周期。
- 录音中自动的是候选提醒，正式建议仍主要靠手动点击。
- 页面名词偏复杂，用户看到“生成会议建议/分析方案利弊/重新加载文字”等会误解当前产品到底在做什么。
- “文字没了”常见原因是：录音中有 partial，停止后服务端没有落库 final；空 snapshot 或降级 session 就会显示未识别到有效语音。

### 3.2 后端/架构

后端已经有重要护栏：

- 真实 WebSocket 默认不允许 fake ASR fallback 冒充成功。
- mock/demo/local-event/degraded/空 ASR 默认不能走生产 LLM 派生端点。
- demo 派生能力已迁到 `/live/asr/demo/sessions/*`。
- event_source 已包含 `input_source/provider_mode/is_mock/asr_fallback_used/acceptance_eligible/acceptance_blockers/final_count/non_empty_transcript`。

但生产级结构仍不足：

- `app.py` 过胖，承担 API、编排、验收、文件安全、LLM、ASR、删除语义。
- `/sessions/*` 与 `/live/asr/sessions/*` 两套 session 语义并存。
- demo/mock/production 仍靠字段约束和路由约定隔离，长期容易被测试便利性污染。
- LLM provider 此前只暴露 `is_mock`，生产端点未阻断 mock LLM provider。

本轮已修复其中一个 P0 边界：

```text
生产派生端点现在拒绝 LLM_GATEWAY_IS_MOCK=true 的 provider。
demo 路由仍允许 mock LLM，用于 UI 回归和示例。
```

### 3.3 真实链路/evidence

File lane 是当前唯一可信的 Go 子链路：

```text
uploaded_wav
  -> local_funasr_batch
  -> real_gateway
  -> suggestion cards
  -> approach cards
  -> minutes
  -> Workbench same session
  -> delete
```

Real mic 不能宣称 Go：

- 2026-07-07 曾有人工浏览器麦克风到 ASR 入库的成功片段，但同 session 正式建议/方案为 0。
- 2026-07-08 当前 P0 Gate A/B 证据退回到近静音、final=0。
- 最新状态仍是 `Real mic: No-Go`。

Public audio 也不能宣称 Go：

- 当前只有白名单/no-download 边界。
- 没有实际公开音频抽取。
- 没有公开音频实时 ASR 到正式建议卡的 Go evidence。
- 现行 `tests/test_asr_event_generation_from_public_or_synthetic_audio.py` 还暴露当前非归档工具缺口，不能算主线通过。

## 4. 为什么过去看起来陷入循环

过去做了大量有价值但不直接改变发布状态的工作：

- readiness
- preflight
- approval wrapper
- schema preview
- synthetic/replay preview
- Tauri no-op
- provider 横评边界
- public audio no-download 边界

这些工作降低了合规和误判风险，但不能替代主链路：

```text
真实/授权音频 -> 非 fake ASR -> 同 session 正式建议/方案/复盘/UI/历史/删除/evidence
```

后续只有能改变以下 7 个状态的任务才算 P0 主线：

| 状态键 | 当前状态 | 后续 Go 证据 |
|---|---:|---|
| `backend_acceptance_enforcement` | 部分 Go | mock ASR、mock LLM、local-event、degraded、空 ASR 都不能进入生产正式产物 |
| `workbench_real_demo_separation` | 部分 Go | UI 不会混淆真实会议、导入录音、演示、降级 |
| `formal_card_evidence` | 部分 Go | cards/approach/minutes 都结构化使用 evidence quote/timestamp |
| `delete_evidence` | No-Go | session/events/cards/minutes/export/evidence/audio retention 都有逐项删除状态 |
| `real_mic_gate_a` | No-Go | 真实麦克风或用户授权音频非静音 |
| `real_mic_gate_b` | No-Go | 同 session 有非 fake、非 fallback、非空 ASR final |
| `real_mic_gate_c` | No-Go | 同 session 完成建议、方案、复盘、UI、历史、删除、evidence bundle |

## 5. 当前执行计划

### P0-1 后端验收边界

- [x] 生产端点移除 `allow_non_acceptance_execution` 公开绕过字段。
- [x] demo 能力迁到 `/live/asr/demo/sessions/*`。
- [x] 正式建议卡使用 evidence quote/timestamp。
- [x] 生产端点拒绝 mock LLM provider。
- [ ] 生产 recognizer factory fail closed，不返回 fake recognizer 给生产路径。
- [ ] 抽出 `acceptance_policy.py`，减少 `app.py` 内的验收策略散落。

### P0-2 Workbench 主流程收敛

- [x] 顶部主动作初步收敛。
- [x] demo 下沉到演示区。
- [x] source badge 区分演示/导入/真实/降级。
- [ ] 历史按真实会议、导入录音、演示、失败/降级分组。
- [ ] 主视图减少术语，只保留用户自然理解的动作。
- [ ] 录音中明确区分临时文字、已确认文字、服务端整理状态。
- [ ] 正式建议准实时队列或明确降级为会后待确认。

### P0-3 Evidence 与复盘闭环

- [x] 正式建议卡 evidence quote/timestamp/clickback。
- [ ] approach cards 使用结构化 evidence context。
- [ ] minutes 使用结构化 evidence context。
- [ ] 历史恢复后 evidence clickback 仍稳定。
- [ ] Markdown/JSON 导出入口补到 Workbench。

### P0-4 删除和 evidence bundle 生命周期

- [x] 删除响应不再谎称未追踪资产已删除。
- [ ] live session schema 增加 `audio_retention/export_retention/evidence_bundle_retention`。
- [ ] UI 删除确认和删除结果逐项展示状态。
- [ ] evidence bundle 成为产品会话一等状态，而不是 runner 外挂产物。

### P0-5 真实主链路 Gate

- [ ] Gate A：真实输入健康，非静音。
- [ ] Gate B：非 fake ASR final，文本非空。
- [ ] Gate C：同 session 跑通建议、方案、复盘、UI、历史、删除、evidence bundle。

Gate A 不过，不跑 Gate B/C；Gate B 不过，不跑 Gate C。

## 6. 当前修复记录

### DEC-238：生产派生端点拒绝 mock LLM provider

问题：

- 后端已经能在响应里暴露 `llm_provider.is_mock`。
- 但生产 `/live/asr/sessions/{id}/llm-execution-runs|approach-cards|minutes|minutes.json` 此前不会阻断 `LLM_GATEWAY_IS_MOCK=true`。
- 这会导致“真实 ASR session + mock LLM provider”生成看似正式的生产派生产物。

决策：

- 生产派生端点在读取 `LlmConfig` 后，如果 `config.is_mock=true`，直接返回 409。
- demo 路由 `/live/asr/demo/sessions/*` 继续允许 mock LLM，用于 UI 回归和演示。
- 这只收紧生产验收边界，不改变 demo 行为。

验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_production_derivation_endpoints_reject_mock_llm_provider
```

结果：

```text
1 passed, 2 warnings
```

相关回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_production_derivation_endpoints_reject_mock_llm_provider \
  code/web_mvp/backend/tests/test_app.py::test_demo_derivation_endpoint_can_execute_mock_session_without_public_bypass_field \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_execution_runs_enabled_rejects_mock_session_without_explicit_demo_allowance \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_enabled_approach_and_minutes_reject_mock_session_without_explicit_demo_allowance
```

结果：

```text
4 passed, 2 warnings
```

当前影响：

```text
backend_acceptance_enforcement: 部分 Go -> 更接近 Go
Real mic: No-Go
Production MVP: No-Go
```

## 7. 下一步执行顺序

不再启动新的泛化评估。后续按以下顺序执行：

1. 跑核心回归，确认 DEC-238 没破坏 demo/file/workbench。
2. 补 `approach/minutes` 的结构化 evidence context。
3. 补删除/evidence/audio retention 的逐项状态。
4. 收敛 Workbench 主流程和历史分组。
5. 跑真实麦克风 Gate A；若 A 失败，只输出 No-Go evidence 和设备/输入修复建议。
6. Gate A Go 后再跑 Gate B/C。

## 8. 不做清单

P0 未通前不继续做：

- ASR provider 横评扩展。
- 新 readiness/preflight/approval wrapper。
- Tauri/安装包/移动端/系统音频。
- 自动建单、多人识别、长期记忆。
- 用 demo smoke 证明真实会议。
- 用 file lane Go 证明 real mic Go。
- real mic 没有非空 ASR final 前做 LLM 质量扩展评测。

## 9. DEC-239：Workbench 真实开麦主线审计与下一轮 checklist

### 9.1 运行态复核

本轮重新复核 8765 运行态，发现旧进程曾脱离 screen 成为孤儿进程：

```text
old pid=37725 listening on 127.0.0.1:8765
screen session not found
```

处理后重新拉起受控 screen：

```text
screen=38984.meeting-copilot-8765
pid=38991
GET /health -> ok
GET /audio/check -> mic_available=true, file_asr_available=true, realtime_asr_available=true, llm_configured=true
GET /workbench -> workbench.js?v=20260708-p0-boundary
```

Workbench demo/UI smoke 已通过：

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

结果：

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

解释：

- 这只证明 demo/mock UI 回归可用。
- 不证明真实麦克风 Go。
- 不证明生产 MVP Go。

### 9.2 前端 Agent 审计结论

Workbench 当前不是空白页，页面入口和真实调用如下：

| 页面入口 | 实际链路 | 当前验收含义 |
|---|---|---|
| 开始/结束会议 | `getUserMedia -> WebSocket /live/asr/stream/ws/{sid} -> GET /live/asr/sessions/{sid}/events` | 真实入口存在，但 real mic Gate A/B/C 未 Go |
| 导入录音 | `POST /live/asr/transcribe-file/sessions -> GET /live/asr/sessions/{sid}/events` | file lane Go |
| 整理会议 | 顺序调用正式建议、方案分析、会后复盘 | 目前是手动/准自动整理，不是录音中 10-30 秒自动正式建议 |
| 历史记录 | `GET /live/asr/sessions`，点击后打开 session events | 部分可用，来源/验收分组还不足 |
| 删除本次会议 | `DELETE /live/asr/sessions/{sid}` | session record 可删；audio/export/evidence retention 未闭合 |
| 生成会议建议 | `POST {production-or-demo-base}/llm-execution-runs` | 真实 session 走 production；demo session 走 demo |
| 分析方案利弊 | `POST {production-or-demo-base}/approach-cards` | 可用但 evidence context 需继续强化 |
| 生成会后复盘 | `POST {production-or-demo-base}/minutes` | 可用但仍是 Markdown 面板，结构化产品体验不足 |
| 重新加载文字 | `GET /live/asr/sessions/{sid}/events` | 只是刷新，不应作为核心用户动作 |
| 试用示例 | `POST /live/asr/mock/sessions` | demo/mock only，不计入真实验收 |

真实链路与 demo/mock 边界：

- 真实麦克风入口存在，但当前缺浏览器真实开麦 e2e 和真实 ASR final Go evidence。
- 导入录音链路是当前唯一可信 Go 子链路。
- `试用示例`、`workbench_smoke.mjs`、`test_e2e_mainline.py` 主要验证 demo/mock/fake LLM，不应命名或描述成真实主链路 Go。
- 页面仍会让用户误解“实时会议 + 实时建议已经自动跑通”，但正式建议、方案和复盘现在仍需点击按钮或点击 `整理会议`。

### 9.3 “开麦后文字没了”的证据化根因

最可能根因不是页面完全没功能，而是当前开麦流程的视图切换策略太激进：

```text
点击开始会议
  -> 立即创建 rec_* 新 session
  -> prepareNewSession 清空当前主视图
  -> 等待 WebSocket ASR events
  -> 停止后 refreshRecordedSession 拉服务端 events
  -> 如果服务端没有非空 transcript_final
     -> 显示未识别到有效语音或降级状态
```

关键结论：

- 开麦会立即把用户当前看到的会议文字替换成新 `rec_*` 会话。
- 如果录音中只有临时 partial 或没有任何非空 final，停止后服务端 snapshot 为空，页面就会变成“未识别到有效语音”。
- 之前已加了“失败后尽量恢复上一场会议”的逻辑，但只覆盖一部分场景，缺少真实浏览器开麦 e2e 验证。
- 用户看到“文字又没有了”，本质是 `real_mic_gate_b` 没有稳定通过，同时前端没有把“录音草稿”和“已保存会议文字”做成两个明确区域。

### 9.4 下一轮 P0 checklist

后续只做以下能改变发布状态的工作。

#### P0-A Workbench 开麦不丢字

- [ ] TDD：新增 Workbench browser e2e，stub `getUserMedia`，连接真实 `/live/asr/stream/ws/{sid}`，用测试 recognizer 产出 partial/final。
- [ ] TDD：新增失败态 e2e，覆盖 provider_error、空 final、空 snapshot，断言旧会议文字不丢。
- [ ] 实现：开麦后先进入“录音草稿”状态，不立即覆盖上一场已保存会议。
- [ ] 实现：只有收到第一条非空 partial/final 或服务端确认非空 transcript 后，才把主视图切到新 session。
- [ ] 实现：失败时明确显示“没有转出文字”，同时保留上一场可见文字。
- [ ] 验证：`test_workbench.py`、新增 e2e、`workbench_smoke.mjs`。

对应 release-decisive 状态：

```text
workbench_real_demo_separation
real_mic_gate_b visibility
```

#### P0-B 真实主链路的最小浏览器自测

- [ ] 重启 8765，确认最新 Workbench 版本。
- [ ] 浏览器打开 `/workbench`。
- [ ] 真实麦克风 Gate A：录音 20 秒，记录电平、rms、peak、active ratio。
- [ ] Gate A 通过后才做 Gate B：检查 `/live/asr/sessions/{sid}/events` 是否有非 fake、非 fallback、非空 `transcript_final`。
- [ ] Gate B 通过后才做 Gate C：同一 session 调 `整理会议`，验证建议、方案、复盘、历史、删除。
- [ ] 生成 `artifacts/tmp/acceptance/<run-id>/manifest.json` 和 Go/No-Go report。

对应 release-decisive 状态：

```text
real_mic_gate_a
real_mic_gate_b
real_mic_gate_c
```

#### P0-C UI 语义简化

- [ ] 把 `开始会议` 改成 `开始记录`，录音中改成 `结束记录`。
- [ ] 把 `生成会议建议/分析方案利弊/生成会后复盘` 降级为结果区内部操作或合并到 `整理会议`。
- [ ] 把 `候选提醒` 改成 `可能要补充`。
- [ ] 把 `正式建议` 改成 `建议`。
- [ ] 把 `方案分析` 改成 `利弊分析`。
- [ ] 把 `会后复盘` 改成 `会后总结`。
- [ ] 把 `降级` 改成 `未通过` 或 `没有转出文字`。
- [ ] 演示入口改成 `查看示例会议`，持续显示“示例，不是真实会议”。

对应 release-decisive 状态：

```text
workbench_real_demo_separation
```

#### P0-D 后端结构止血而非大重构

- [ ] 保持 P0 阶段不做大拆分，避免再偏离主链路。
- [ ] TDD：`recognizer` 缺少显式 `provider_mode/is_mock/fallback_used` metadata 时，不能默认当作 production real ASR。
- [ ] 只抽出 `acceptance_policy.py`：`event_source` metadata、acceptance blockers、enabled LLM gate。
- [ ] 保持路由行为不变，先用 characterization tests 锁住。
- [ ] 后续再按 router 拆分：`asr_routes.py`、`llm_derivation_routes.py`、`workbench_routes.py`、`demo_routes.py`。

对应 release-decisive 状态：

```text
backend_acceptance_enforcement
```

#### P0-E 删除和 evidence lifecycle

- [ ] live session record 增加明确 retention 状态：`audio_retention`、`export_retention`、`evidence_bundle_retention`。
- [ ] 删除响应逐项返回 deleted / not_tracked / already_missing。
- [ ] Workbench 删除确认和删除结果展示逐项状态。
- [ ] evidence bundle 成为 session lifecycle 的一等产物，而不是 runner 外挂。

对应 release-decisive 状态：

```text
delete_evidence
```

### 9.5 停止规则

- Gate A 不过，不跑 Gate B/C。
- Gate B 不过，不调用生产正式建议、方案或复盘。
- demo smoke 通过，只能写 `Demo/mock Go for demo only`。
- file lane Go，不能外推为 real mic Go。
- app.py 大拆分不能优先于真实主链路。
- 不再新增 readiness/preflight/wrapper-only 文档作为 P0 进展。

## 10. 后端 Agent 审计补充

后端审计确认：当前项目已经有 live ASR、文件上传、LLM 建议、方案、纪要、历史和删除接口，但测试证明等级不一致。

### 10.1 当前最重要的后端事实

- `app.py` 约 1863 行，`create_app()` 同时承担 HTTP routing、ASR live session 编排、LLM derivation、local event file 安全、acceptance gate、历史 summary、删除和 legacy/demo path。生产结构不理想。
- P0 阶段不应先做大拆分。应该先用 TDD 把主链路 gate、真实/模拟边界、删除语义钉牢，再做小步提取。
- `test_real_asr_to_cards.py` 名字像真实链路，但实际是 fake recognizer + fake LLM，只证明 WebSocket 到 production route 的连接线，不证明真实 ASR/LLM。
- `test_e2e_mainline.py` 使用 `/live/asr/mock/sessions` 和 `/live/asr/demo/.../llm-execution-runs`，只能算 demo mainline。
- `test_file_convert.py` 证明上传 API 控制流和空转写阻断，但 `batch_transcribe.transcribe_file()` 被 monkeypatch，不证明真实文件 ASR 子进程稳定性。

### 10.2 新增 P0 风险：recognizer metadata 缺失默认放行

后端审计发现：

```text
asr_stream._recognizer_provider_metadata()
```

在 recognizer 缺少 `provider/is_mock/fallback_used/provider_mode` metadata 时，会根据 `configured_provider="local_real_asr"` 推导为：

```text
is_mock=false
fallback_used=false
provider_mode=real
```

这会让自定义 fake/test recognizer 在缺少显式 metadata 时被 production acceptance gate 当作 real ASR。当前生产内置 `FakeStreamRecognizer` 自带 metadata，可以被 block；但默认策略本身不安全，未来 provider adapter 很容易误放行。

P0 决策：

- 所有 production-eligible recognizer 必须显式声明 `provider`、`provider_mode`、`is_mock`、`fallback_used`。
- metadata 缺失时默认 `provider_mode=unknown` 或 `is_mock=true`，并加入 acceptance blocker。
- TDD 先写失败测试，再改实现。

### 10.3 其他后端 P0/P1 缺口

- minutes JSON 只检查能 parse 成 dict，没有严格 schema，也没有校验 evidence quote 是否来自 transcript。
- sidecar stderr 当前多处 `DEVNULL`，真实会议失败后诊断信息不足。
- live 删除响应已经不再 overclaim cascade，但旧隐私测试仍可能期待 `cascade` 字段，需要统一契约。
- 文件上传直接 `await file.read()`，缺文件大小、MIME/扩展、时长、并发/超时保护；本地 MVP 可接受，生产前必须补。
- 配置读取分散，`LlmConfig.from_env()`、`metrics.validate_config()` 和 import-time `log_config_status()` 都各自读环境/`.env`，后续应集中 config provider。

### 10.4 修复顺序更新

下一轮先做两个会直接改变主线可信度的修复：

1. 后端：recognizer metadata fail-closed，防止未知 recognizer 被当作 production real ASR。
2. 前端：Workbench 开麦不丢字 e2e 与实现，防止真实麦克风失败时主视图被空 session 覆盖。

这两个完成后再跑真实麦克风 Gate A。Gate A 通过前，不继续扩展 LLM 质量评测或 app.py 大拆分。

## 11. DEC-240 实施：recognizer metadata fail-closed

问题：

- `_recognizer_provider_metadata()` 此前对缺少 metadata 的 recognizer 使用 `configured_provider="local_real_asr"`，并默认推导为 `provider_mode=real`、`is_mock=false`、`fallback_used=false`。
- 这会让测试或未来自定义 recognizer 在没有显式声明真实性时被 production acceptance gate 误放行。

TDD 红灯：

新增测试：

```text
code/web_mvp/backend/tests/test_asr_stream.py::test_asr_stream_recognizer_without_metadata_fails_closed_by_default
```

红灯表现：

```text
测试卡住等待 provider_error；服务端没有立刻 fail closed，而是进入正常识别路径。
```

实现：

- `recognizer` 必须显式声明 `provider`、`provider_mode`、`is_mock`、`fallback_used`。
- 缺少任一字段时，metadata 输出：

```text
provider_mode=unknown
is_mock=true
fallback_used=true
degradation_reasons includes recognizer_metadata_missing
```

- 默认生产 WebSocket 因 `provider_mode != real` / `is_mock=true` / `fallback_used=true` 返回 provider_error，不持久化 session。
- 原连接线测试中的 fake recognizer 改为显式 `test_contract_real_asr`，避免再依赖缺 metadata 默认放行。

验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py
```

结果：

```text
8 passed, 2 warnings
```

影响：

```text
backend_acceptance_enforcement: 部分 Go -> 更接近 Go
Real mic: 仍 No-Go
Production MVP: 仍 No-Go
```

## 12. DEC-241 实施：Workbench 开麦不立即清空上一场文字

问题：

- 开始真实麦克风录音时，旧逻辑会立即创建 `rec_*` session 并调用 `prepareNewSession()` 清空主视图。
- 如果真实 ASR 后续没有非空 final，停止后空 snapshot 会让页面显示“未识别到有效语音”，用户看到的就是“文字又没有了”。

TDD 红灯：

新增测试：

```text
code/web_mvp/backend/tests/test_workbench.py::test_workbench_recording_draft_does_not_claim_view_until_text_arrives
```

红灯表现：

```text
缺少 recordingDraftHasClaimedView/startRecordingDraftSession/claimRecordingDraftView；
开始录音 handler 仍直接 prepareNewSession 清空主视图。
```

实现：

- 新增 `recordingDraftHasClaimedView` 状态。
- 新增 `startRecordingDraftSession(sessionId)`：
  - 有上一场可读会议时，只进入录音草稿状态，保留上一场主视图。
  - 无上一场文字时，仍显示“正在听，会实时显示文字”空态。
  - source badge 仍显示麦克风待确认，不提前标记为真实验收。
- 新增 `claimRecordingDraftView()`：
  - 收到第一条非空 partial/final/transcript_final 时，才清空旧视图并切到新会议文字。
  - provider_error、空 final、空 snapshot 仍优先恢复上一场会议。

验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_workbench.py
```

结果：

```text
41 passed, 2 warnings
```

影响：

```text
workbench_real_demo_separation: 更接近 Go
real_mic_gate_b: 可见性改善，但仍未 Go
Real mic: 仍 No-Go
Production MVP: 仍 No-Go
```

## 13. 真实麦克风 Gate A 复测结果

本轮在当前机器上执行 20 秒真实麦克风健康检查：

```bash
python3 tools/audio_capture_healthcheck.py \
  --repo-root "$PWD" \
  --record-seconds 20 \
  --audio-device-index 0 \
  --output-audio-path artifacts/tmp/audio_health/gate-a-real-mic-20260708-160858.wav
```

设备枚举：

```text
AVFoundation audio devices:
[0] MacBook Air麦克风
```

健康报告：

```text
artifacts/tmp/audio_health/gate-a-real-mic-20260708-160858.health.json
health_status=blocked_audio_too_quiet
duration_seconds=15.883
rms=0.0
peak=0.0
active_sample_ratio=0.0
silence_ratio=1.0
raw_audio_uploaded=false
remote_asr_called=false
llm_called=false
configs_local_read=false
```

标准 evidence bundle：

```text
artifacts/tmp/acceptance/p0-real-mic-gate-a-20260708-160858/manifest.json
verdict=no_go
audio_source=real_mic
real_mic_health_status=blocked_audio_too_quiet
asr_provider=not_started
llm_called=false
final_segment_count=0
suggestion_card_count=0
```

结论：

- `real_mic_gate_a=No-Go`。
- Stop rule 生效：不继续 Gate B/C，不调用 ASR，不调用 LLM。
- 当前外放声音没有进入 `MacBook Air麦克风` 输入，或系统权限/输入路由/输入音量导致录制为静音。
- 下一次真实麦克风验证前，应先在 macOS 系统设置里确认麦克风输入电平会跳动，或改用可明确进入输入设备的数字系统音频/虚拟声卡方案。

## 14. DEC-242：真实 Workbench 页面麦克风 Gate A 复测

为避免继续停留在工具侧健康检查，本轮又跑了一次真实 Workbench 页面路径：

```text
Workbench 页面
  -> 点击开始会议
  -> 浏览器 getUserMedia
  -> WebSocket /live/asr/stream/ws/{session_id}
  -> sherpa_onnx_realtime
  -> 停止会议
  -> session snapshot
  -> UI 降级展示
```

新增前端证据能力：

- `workbench_browser_mic_health`
- `sample_count`
- `chunk_count`
- `rms`
- `peak`
- `active_sample_ratio`
- `health_status`
- `raw_audio_uploaded=false`
- `remote_asr_called=false`
- `llm_called=false`

为了让浏览器自动化稳定取证，报告同时落到：

```text
document.body.dataset.browserMicHealth
console.info("[workbench] workbench_browser_mic_health " + JSON.stringify(report))
```

测试验证：

```text
code/web_mvp/backend/tests/test_workbench.py::test_workbench_tracks_browser_mic_health_for_gate_a_evidence
Result: 1 passed, 2 warnings
```

真实页面 evidence：

```text
artifact=artifacts/tmp/audio_health/workbench-browser-mic-health-20260708-163131.json
page=http://127.0.0.1:8765/workbench?reload=browser-health2
script=/static/workbench.js?v=20260708-p0-browser-health2
session_id=rec_mrbtjiwq
sample_count=163840
chunk_count=35
rms=0
peak=0
active_sample_ratio=0
health_status=blocked_audio_too_quiet
```

后端同 session snapshot：

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
suggestion_card_count=0
approach_card_count=0
has_minutes=false
```

结论：

- 真实页面主路径已跑到后端 ASR 和 session snapshot，不是按钮没接。
- 页面录音中能够显示“没有检测到麦克风声音”，停止后能够降级到“未识别到有效语音”。
- 当前 No-Go 的直接原因是浏览器麦克风样本全 0，Gate A 失败。
- 因 Gate A 失败，本轮没有继续 Gate B/C，没有调用 LLM，也不把 demo/file lane 外推成真实麦克风 Go。

当前 P0 状态保持：

```text
Demo/mock: Go for demo only
File lane: Go
Real mic: No-Go
Production MVP: No-Go
```
