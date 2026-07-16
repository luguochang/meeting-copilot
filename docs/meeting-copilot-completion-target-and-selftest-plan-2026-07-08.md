# Meeting Copilot Completion Target And Self-Test Plan

> 日期：2026-07-08
> 状态：P0/P1/P2/P3 completion target implemented to documented boundaries; final no-cost release summary Go; true packaged desktop production remains Conditional No-Go
> 目的：把“前后端还要做什么、做到什么算完成、怎么自测”一次性收束，避免继续在零散边界评估里打转。
> 当前依据：`docs/current-mainline-index.md`、`docs/current-status-and-p0-execution-plan-2026-07-08.md`、`docs/p0-real-mic-recorded-realtime-selftest-report-2026-07-08.md`。

## 1. 当前事实基线

截至 2026-07-08，本目标文档中的 P0/P1/P2/P3 任务已按本文档边界完成并自测；仍不能声明“签名打包后的桌面生产版完成”，因为真实桌面 native 麦克风 adapter、ASR worker 执行、签名、notarization 和安装包交付仍是后续阶段。当前可追溯证据：

```text
Demo/mock: Go for demo only
File lane: Go
Simulated realtime wav: Go
Real mic recorded realtime lane: Go
Browser live mic lane: Go
P1 productization/privacy/provider/soak: Go to documented gates
P2 Mac desktop: Go as dev shell + no-op IPC evidence, not real native mic/worker/package
P2 Windows: Go as compatibility plan, not implementation
P3 Mobile: Go as future plan, not implementation
Final no-cost release summary: Go
Production MVP: Conditional No-Go
```

已通过的关键证据：

```text
artifacts/tmp/acceptance/p0-file-lane-20260708-after-p0fix/
artifacts/tmp/acceptance/p0-simulated-realtime-20260708-01/
artifacts/tmp/acceptance/p0-real-mic-recorded-realtime-afplay-20260708-01/
artifacts/tmp/acceptance/p0-browser-live-mic-tech-audio-20260708-231952/
artifacts/tmp/release_acceptance/final-completion-target-20260708-nocost-summary/
```

浏览器 Live Mic 通过证据（2026-07-08 DEC-248）：

```text
verdict=go
audio_source=browser_live_mic
browser_live_mic_go_evidence=true
counts_as_real_mic_go_evidence=true
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
llm_provider=real_gateway
transcript_char_count=58
final_segment_count=1
suggestion_card_count=3
approach_card_count=2
minutes_char_count=273
workbench_same_session_visible=true
frontend_card_count=5
delete_verified=true
degradation_reasons=[]
```

真实麦克风录音回放通过证据：

```text
verdict=go
audio_source=real_mic_recorded_wav
counts_as_real_mic_go_evidence=true
browser_live_mic_go_evidence=false
asr_provider=sherpa_onnx_realtime
asr_fallback_used=false
llm_provider=real_gateway
suggestion_card_count=3
approach_card_count=3
minutes_char_count=404
workbench_same_session_visible=true
frontend_card_count=6
delete_verified=true
degradation_reasons=[]
```

必须保留的边界：

- `real_mic_recorded_wav` 证明真实麦克风录音可进入实时主链路，但不等于浏览器 `getUserMedia` live mic 已通过。
- `simulated_realtime_wav` 只证明 no-mic 实时协议和业务链路，不计入真实麦克风 Go。
- demo/mock 只能用于 UI 回归和示例，不能计入正式验收。
- 不读取 `configs/local/`，不提交 secret，不读取未授权 `.m4a` 或真实用户录音。
- 默认不新增付费远程 ASR；远程 ASR 只能作为后续可选 provider，并必须单独记录费用/隐私边界。

## 2. 最终完成定义

本项目进入“生产级可交付 MVP”必须同时满足以下 10 个完成条件。

### C1. 浏览器 Live Mic 主链路 Go

完成定义：

```text
Workbench 浏览器 getUserMedia
-> WebSocket realtime ASR
-> non-empty ASR final
-> 正式建议卡
-> 方案卡
-> 会议纪要
-> 同 session UI 可见
-> 历史恢复
-> 删除验证
-> evidence bundle verdict=go
```

验收指标：

- `audio_source=browser_live_mic` 或等价明确字段。
- `browser_live_mic_go_evidence=true`。
- `asr_provider_mode=real`。
- `asr_fallback_used=false`。
- `final_segment_count>=1`。
- `transcript_char_count>=30`。
- `suggestion_card_count>=1`。
- `approach_card_count>=1`。
- `minutes_char_count>0`。
- `workbench_same_session_visible=true`。
- `delete_verified=true`。
- `degradation_reasons=[]`。

必须新增或更新：

- Browser live mic evidence lane。
- Browser mic health report 和后端 session evidence 的统一 bundle。
- Workbench live mic 自动化或半自动自测脚本。

### C2. 会议中实时建议自动触发

完成定义：

用户点击“开始会议”后，系统应在会议过程中自动根据上下文生成建议，而不是依赖用户会后手动点多个按钮。

验收指标：

- ASR final 或稳定 revision 到达后，自动调度候选检测。
- 候选满足工程语义和冷却策略时，自动触发建议卡。
- 同一证据不重复刷屏。
- 有 cooldown、频率上限和低置信度抑制。
- 用户能暂停/恢复自动建议。
- 自动建议卡能显示证据原文、时间、触发原因、置信度。

必须新增或更新：

- 后端实时 orchestration 状态机。
- 前端自动建议状态显示。
- 自动建议 e2e 测试。

### C3. ASR 中文技术会议质量可控

完成定义：

系统不能只因为 transcript 非空就算通过，必须对中文技术会议的语义质量做最低门槛判断。

验收指标：

- 对测试技术会议样本能召回核心实体：接口、灰度、错误率、回滚、owner、deadline、SLO/P99 等。
- 对无意义转写或严重错字输出 `asr_semantic_quality_blocked`，不继续生成正式建议卡。
- UI 能提示“声音可用但识别语义质量不足”。
- 质量报告写入 evidence bundle。

必须新增或更新：

- ASR semantic quality gate。
- 技术会议关键词/实体评测集。
- provider bake-off 输出和默认 provider 决策。

### C4. Workbench 产品化 UI 完成

完成定义：

Workbench 从工程测试台变成会议中可实际使用的产品页面。

验收指标：

- 首屏只保留 4 个核心动作：开始会议、结束会议、导入录音、历史记录。
- “整理会议”只在有 session 后出现，且状态清楚。
- 不再暴露复杂工程名词给普通用户。
- 会议中页面分为：实时文字、实时建议、会后纪要、历史记录。
- 空状态、麦克风无输入、ASR 不可用、LLM 失败、删除成功都有清楚提示。
- 移动宽度和桌面宽度下文字不重叠。
- 按钮、badge、卡片文案统一。

必须新增或更新：

- Workbench 信息架构重构。
- UI 状态字典。
- 视觉回归 screenshot。
- 全按钮点击 smoke。

### C5. 录音导入和会后复盘可用

完成定义：

用户能导入录音，生成完整文字稿、建议卡、方案卡和纪要，并能保存/删除。

验收指标：

- 支持 `.wav` 和经安全转换后的常见音频格式。
- 导入后展示 transcript、cards、approach、minutes。
- 能导出 transcript 和 minutes。
- 删除后 session、派生产物和可追踪音频状态一致。
- file lane evidence bundle 保持 Go。

必须新增或更新：

- 录音导入 UI 状态。
- 导出入口。
- 文件转换失败提示。
- retention/delete evidence。

### C6. Provider 和成本边界清楚

完成定义：

LLM 和 ASR provider 都有清楚配置边界，默认成本只花在 LLM gateway，远程 ASR 不默认启用。

验收指标：

- OpenAI-compatible LLM gateway 通过环境变量或本地配置注入。
- UI 不显示 secret。
- 日志和 evidence 不打印 API key。
- LLM mock 不能进入生产验收端点。
- 远程 ASR provider 默认关闭。
- 若启用远程 ASR，报告必须显示 `remote_asr_called=true` 和 provider 名称。
- 每次完整自测记录 token usage 或调用次数。

必须新增或更新：

- Provider config 文档。
- Cost/privacy flags。
- Provider health endpoint。
- 可选远程 ASR adapter 接口，但不默认启用。

### C7. 本地数据、隐私和删除策略完成

完成定义：

会议音频、转写、建议、纪要、日志和 evidence 的本地存储位置、保留时长、删除行为都可解释、可验证。

验收指标：

- 本地 runtime 数据在明确目录下。
- ignored artifacts 不进入 git。
- 删除 session 后 API 返回结构化 `delete_scope`。
- UI 删除确认展示会删除哪些内容、哪些当前未追踪。
- evidence bundle 不含 secret。
- 未授权用户音频不被读取或上传。

必须新增或更新：

- Retention policy 文档。
- Delete self-test。
- Privacy/cost flags schema。
- Evidence bundle sanitizer。

### C8. Mac 桌面客户端 MVP 完成

完成定义：

Mac 上能以客户端形式打开产品，不只是浏览器测试页。

验收指标：

- 能启动本地 WebView。
- 能连接本地 backend 或内嵌服务。
- 麦克风权限说明清楚。
- 不启动真实采集前不请求权限。
- 开始会议、结束会议、导入录音、历史记录可用。
- 打包产物或开发版启动命令可复现。

必须新增或更新：

- Tauri/Electron 形态决策。
- Mac 权限说明。
- 本地启动脚本。
- 桌面 smoke test。

### C9. Windows 桌面兼容计划完成

完成定义：

Windows 不要求和 Mac 同时发布，但必须明确差异和后续实现路径。

验收指标：

- 记录 Windows 音频采集 API 差异。
- 记录安装包、权限、系统音频、麦克风输入差异。
- 同一前端/后端主体代码可复用。
- 平台差异封装在 desktop adapter 层。

必须新增或更新：

- Windows compatibility plan。
- Desktop adapter interface。
- Windows smoke checklist。

### C10. 发布前质量门禁完成

完成定义：

每次声称可发布前，必须跑一组固定命令和 evidence bundle。

验收指标：

```text
后端主线测试全部通过
Workbench smoke 通过
file lane bundle Go
simulated realtime bundle Go
real mic recorded realtime bundle Go
browser live mic bundle Go
git diff --check clean
运行中服务 /health ok
Workbench 页面加载最新 JS 版本
```

必须新增或更新：

- `tools/release_acceptance_runner.py` 或等价脚本。
- Release acceptance report。
- CI/local 命令文档。

## 3. 剩余工作总清单

### P0-1. Browser Live Mic Evidence Lane

目标：

把浏览器页面真实开麦从“手工观察”升级成可追踪 evidence bundle。

状态更新（2026-07-08 23:22 CST）：

```text
status=completed_for_c1_browser_live_mic_gate
artifact_root=artifacts/tmp/browser_live_mic/p0-browser-live-mic-tech-audio-20260708-231952
bundle_root=artifacts/tmp/acceptance/p0-browser-live-mic-tech-audio-20260708-231952
verdict=go
```

边界：

- 本次自测使用 macOS `say -v Tingting` 播放可复现中文技术会议短句，通过浏览器 `getUserMedia` 采集进入 Workbench，不读取私人音频。
- 该证据证明浏览器 live mic 主链路和 evidence lane Go。
- 该证据不代表 C2 实时自动建议 orchestrator 已完成；当前正式建议仍主要由 `整理会议` 流程触发。
- 该证据不代表 C3 semantic quality gate 已完成；当前只靠 transcript 长度和业务卡片证据，后续必须加中文技术会议语义质量门禁。

涉及文件：

- `tools/mainline_evidence_bundle_runner.py`
- `code/web_mvp/e2e/workbench_session_verify.mjs`
- `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `code/web_mvp/backend/tests/test_workbench.py`
- `tests/test_mainline_evidence_bundle_runner.py`

步骤：

- [x] 写红灯测试：browser live mic health pass 但无 ASR final 时必须 No-Go。
- [x] 写红灯测试：browser live mic final/cards/minutes/UI/delete 全部满足时 bundle Go。
- [x] 写红灯测试：browser live mic 短转写不能误判 Go。
- [x] 写红灯测试：browser live mic verifier 失败时也必须写出 runner 可消费证据。
- [x] 在 Workbench 停止录音时导出 browser mic health、session id、source metadata。
- [x] 新增 `browser-live-mic` lane，读取 browser health + session events + downstream evidence。
- [x] 跑一次真实 Workbench 页面开麦自测，使用可复现本机 TTS 技术会议音频。
- [x] 写入当前 mainline 文档和 decision log；独立报告可由 bundle `go_no_go.md` 追踪。

完成命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  tests/test_mainline_evidence_bundle_runner.py \
  code/web_mvp/backend/tests/test_workbench.py

node code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
```

### P0-2. 实时自动建议 Orchestrator

目标：

让会议中自动出现建议卡，而不是会后手动点按钮。

状态更新（2026-07-08 DEC-249）：

```text
status=completed_for_p0_2_focused_gate
production_mvp_status=still_conditional_no_go
reason=C3 semantic quality gate / C10 release acceptance / P1-P3 still incomplete
```

已完成：

- 新增 session-level `auto_suggestion` 状态机，记录 enabled、paused、status、cooldown、max_calls_per_hour、processed_candidate_ids 和 suppressed。
- 新增生产 API：`GET/PATCH/POST /live/asr/sessions/{session_id}/auto-suggestions/...`。
- 自动建议只处理 `scheduler_event_type=llm_candidate_queued` 的候选。
- 正式 LLM 调用前会检查 production acceptance blockers、暂停状态、重复 candidate、低置信度/低质量 degradation、cooldown 和频率上限。
- Workbench 展示自动建议状态，并支持暂停/恢复。
- Workbench 在 session snapshot 和 live final 后调用同一套 `runAutoSuggestionsOnce()` 链路，不通过 `btn-cards.click()` 包装旧手动按钮。
- 旧“生成会议建议”按钮仍作为手动补偿入口保留，但不计入自动建议证据。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_auto_suggestions.py
result=9 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_asr_stream.py
result=75 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
result=passed

node --check code/web_mvp/e2e/workbench_smoke.mjs
result=passed
```

边界：

- 该状态只证明 C2/P0-2 自动建议 focused gate 完成。
- 该状态不证明 C3 semantic quality gate 已完成；语义错乱但非空的 transcript 仍需 P0-3 阻断。
- 该状态不证明 C10 release acceptance runner 已完成。
- `Production MVP` 仍保持 `Conditional No-Go`。
- 复审发现并修复旧实现只在 `END` 后 snapshot 可用的问题：现在 WebSocket 收到非空 live final 后会在 `END` 前增量 upsert ASR live session，自动建议和暂停/恢复可在录音中工作。
- `max_calls_per_hour` 已改为按 1 小时滑动窗口计算，避免历史累计导致永久 rate-limit。
- 正式建议卡已展示触发原因和置信度。

涉及文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_repository.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/tests/test_real_asr_to_cards.py`
- `code/web_mvp/backend/tests/test_workbench.py`

步骤：

- [x] 写红灯测试：ASR final 后自动生成 candidate，但不会重复生成同证据卡。
- [x] 写红灯测试：cooldown 内不重复调用 LLM。
- [x] 写红灯测试：低质量 ASR transcript 不触发正式 LLM。
- [x] 实现 session-level orchestration state。
- [x] 前端展示“自动建议开启/暂停/生成中/已生成”。
- [x] 更新 Workbench static/smoke syntax gate，覆盖自动建议状态与触发入口。

完成命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  code/web_mvp/backend/tests/test_app.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_workbench.py

node code/web_mvp/e2e/workbench_smoke.mjs
```

### P0-3. ASR Semantic Quality Gate

目标：

避免“有文字但语义错乱”继续生成建议。

状态更新（2026-07-08 DEC-250）：

```text
status=completed_for_p0_3_focused_gate
production_mvp_status=still_conditional_no_go
reason=C10 release acceptance runner / P1-P3 still incomplete
```

已完成：

- 新增 deterministic `asr_semantic_quality` gate，不调用 LLM、不新增远程 ASR 费用。
- 新增 10 条中文技术会议短句评测集，覆盖接口/API、灰度、回滚、错误率、owner、deadline、SLO/P99、监控告警、行动项等语义。
- realtime ASR session 和 file lane session 均写入 `event_source.asr_semantic_quality`。
- 无意义转写、流畅但非技术内容、核心工程实体不足时输出 `asr_semantic_quality_blocked`，并进入 `degradation_reasons` 和 acceptance blockers。
- 正式建议、方案、纪要、自动建议和 evidence bundle Go/No-Go 都会被该 blocker 阻断。
- Workbench 已把该 blocker 显示为用户级提示：“识别语义质量不足：声音可用，但没有听清关键业务内容，先不生成正式建议。”
- `tools/mainline_evidence_bundle_runner.py` manifest 已新增 `asr_semantic_quality_status`、`asr_semantic_quality_blocked` 和 `asr_semantic_quality`。
- 新增语义质量报告工具 `code/asr_bakeoff/asr_bakeoff/semantic_quality_report.py`，记录默认不启用远程 ASR、默认不新增 ASR 费用。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_semantic_quality.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_workbench.py \
  tests/test_mainline_evidence_bundle_runner.py
result=89 passed, 2 warnings

PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests
result=19 passed, 1 warning

report=artifacts/tmp/asr_eval/semantic_quality/p0-3-semantic-quality-report-20260708.json
sample_count=10
expected_status_match_count=10
false_pass_count=0
false_block_count=0
keyword_recall_average=1.0
remote_asr_default_enabled=false
cost_status=no_paid_remote_service
```

边界：

- 该状态只证明 C3/P0-3 语义质量 focused gate 完成。
- 该状态不证明 C10 release acceptance runner 已完成。
- 该状态不证明 P1 录音导入/导出、Provider 配置、隐私删除、长会稳定性已全部完成。
- 该状态不改变默认成本策略：远程 ASR 仍默认关闭，只能作为后续可选 provider 或对照评测。

涉及文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/asr_bakeoff/`
- `data/asr_eval/` 或 approved `artifacts/tmp/asr_eval/`
- `tests/test_mainline_evidence_bundle_runner.py`
- `code/web_mvp/backend/tests/test_asr_stream.py`

步骤：

- [x] 建立 10 条中文技术会议短句评测集，包含 expected keywords。
- [x] 写红灯测试：无意义转写触发 `asr_semantic_quality_blocked`。
- [x] 写红灯测试：核心关键词召回达标时允许进入 cards。
- [x] 在 bundle manifest 增加 `asr_semantic_quality_status`。
- [x] 在 Workbench 显示“识别语义质量不足”。
- [x] 输出 provider bake-off report。

完成命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  tests/test_mainline_evidence_bundle_runner.py

PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests
```

### P0-4. Workbench 产品化 UI 重构

目标：

把测试台改成会议中可用的产品界面。

状态更新（2026-07-08 22:35 CST）：

```text
status=completed_for_p0_4_ui_gate
production_mvp_status=still_conditional_no_go
reason=historical_status_before_p0_1_browser_live_mic_gate; superseded_by_DEC_248
```

已完成：

- 首屏主操作收敛为 4 个：开始会议、结束会议、导入录音、历史记录。
- `整理会议`、刷新文字、删除本次会议、单项生成建议/方案/纪要都移动到有 session 后才显示的会议操作区。
- 页面信息架构收敛为实时文字、实时建议、AI 建议、方案分析、会议纪要、历史记录。
- 用户可见文案从 `LLM/ASR/export/evidence` 等工程词，改为语音识别、AI 分析、依据、会议纪要等产品词。
- 增加移动端 `@media (max-width:900px)` 布局保护，并用 browser smoke 验证 desktop/mobile 没有横向溢出。
- `workbench_smoke.mjs` 扩展为全按钮主流程 smoke：初始四按钮、session-only 操作区、示例会话、历史、AI 建议、方案分析、会议纪要、删除和桌面/移动截图。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_workbench.py
result=47 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_workbench.py code/web_mvp/backend/tests/test_app.py -k 'workbench or delete_asr_live_session or privacy'
result=48 passed, 87 deselected, 2 warnings

node code/web_mvp/e2e/workbench_smoke.mjs
result=workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified

screenshots=artifacts/tmp/ui_screenshots/workbench-p0-4-smoke/workbench-desktop.png
screenshots=artifacts/tmp/ui_screenshots/workbench-p0-4-smoke/workbench-mobile.png

git diff --check
result=clean
```

边界：

- 该状态只证明 C4/P0-4 页面产品化和 demo/full-button smoke 完成。
- 该状态不证明 C1 browser live mic 已 Go。
- 该状态不证明 C2 正式建议已自动触发。
- 该状态不证明 C3 ASR 语义质量 gate 已接入。
- 因此 `Production MVP` 仍保持 `Conditional No-Go`。

涉及文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `code/web_mvp/backend/tests/test_workbench.py`
- `code/web_mvp/e2e/workbench_smoke.mjs`

步骤：

- [x] 写按钮清单测试，确保每个按钮都有明确状态和禁用条件。
- [x] 收敛首屏按钮：开始会议、结束会议、导入录音、历史记录。
- [x] 改文案：去掉工程内部术语。
- [x] 分区：实时文字、实时建议、会议纪要、历史记录。
- [x] 增加麦克风无输入、ASR 不可用、LLM 失败的用户级提示。
- [x] 增加移动/桌面 screenshot 自测。

完成命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_workbench.py
node code/web_mvp/e2e/workbench_smoke.mjs
```

### P0-5. Release Acceptance Runner

目标：

用一个命令跑完发布前主链路，减少人工漏项。

状态更新（2026-07-08 DEC-251）：

```text
status=completed_for_p0_5_release_gate
release_acceptance_verdict=go_for_p0_lanes
production_mvp_status=still_conditional_no_go
reason=historical_status_before_P1_P2_P3_completion; superseded_by_DEC_252_to_DEC_258_for_documented_boundaries
```

已完成：

- 新增 `tools/release_acceptance_runner.py` 作为发布验收聚合器。
- runner 复用 `tools/mainline_evidence_bundle_runner.py`，不复制 ASR/LLM/UI lane 判定逻辑。
- 默认串联：backend mainline pytest、Workbench smoke、`git diff --check`、`/health`、Workbench JS 版本、file lane、simulated realtime lane、real mic recorded realtime lane、browser live mic Go bundle。
- 任一 required check 失败或 required lane `verdict != go` 时，release verdict 为 `no_go`。
- browser live mic 未证明时输出 `blocked_browser_live_mic_not_proven`，并且不能用 `real_mic_recorded_wav` 替代。
- report 固定写入 `artifacts/tmp/release_acceptance/<run-id>/summary.json` 和 `report.md`。
- release summary 包含 `git_commit`、checks、lanes、artifact links 和 privacy/cost flags。
- 修复汇总层 `privacy_cost_flags.llm_called`：当 lane 顶层 `llm_called=true` 时，release 汇总必须为 true。
- 新增 `docs/release-acceptance-checklist.md`。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_release_acceptance_runner.py \
  tests/test_mainline_evidence_bundle_runner.py \
  code/web_mvp/backend/tests/test_asr_semantic_quality.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_workbench.py
result=95 passed, 2 warnings

PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests
result=19 passed, 1 warning

python3 -m py_compile tools/release_acceptance_runner.py tools/mainline_evidence_bundle_runner.py code/asr_bakeoff/asr_bakeoff/semantic_quality_report.py
result=passed
```

真实 release acceptance CLI：

```text
raw_cli_artifact=artifacts/tmp/release_acceptance/p0-5-release-acceptance-20260708
corrected_summary_artifact=artifacts/tmp/release_acceptance/p0-5-release-acceptance-20260708-corrected-summary
verdict=go
blockers=[]
checks=pytest_backend_mainline,workbench_smoke,git_diff_check,health_endpoint,workbench_js_version
lanes=file_lane:go,simulated_realtime:go,real_mic_recorded_realtime:go,browser_live_mic:go
privacy_cost_flags.llm_called=true
privacy_cost_flags.remote_asr_called=false
privacy_cost_flags.configs_local_read=false
privacy_cost_flags.user_audio_committed_to_repo=false
```

边界：

- 该状态证明 P0 release gate 已具备并通过当前 P0 lanes。
- 该状态不证明 P1 录音导入/导出、Provider config、隐私保留删除、长会稳定性已完成。
- 该状态不证明 P2/P3 桌面打包和移动端规划已完成。
- 因此 `Production MVP` 仍保持 `Conditional No-Go`。

涉及文件：

- `tools/release_acceptance_runner.py`
- `tools/mainline_evidence_bundle_runner.py`
- `tests/test_release_acceptance_runner.py`
- `docs/release-acceptance-checklist.md`

步骤：

- [x] 写红灯测试：任一 required lane No-Go 时 release verdict No-Go。
- [x] 写红灯测试：所有 required lane Go 时汇总 report Go。
- [x] 串联 pytest、Workbench smoke、file lane、simulated realtime、real mic recorded realtime。
- [x] browser live mic 在未验证时明确输出 `blocked_browser_live_mic_not_proven`。
- [x] 生成 `artifacts/tmp/release_acceptance/<run-id>/report.md`。

完成命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q tests/test_release_acceptance_runner.py
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/release_acceptance_runner.py --run-id local-release-candidate
```

### P1-1. 录音导入和导出产品化

目标：

导入录音后能自然完成转写、纪要、导出和删除。

状态更新（2026-07-08 DEC-252）：

```text
status=completed_for_p1_1_recording_import_export
production_mvp_status=still_conditional_no_go
reason=P1-4 long meeting soak / P2-P3 desktop-mobile planning / final release verification still incomplete
```

已完成：

- Workbench 支持导入 `.wav/.m4a/.mp3` 录音并通过 `/live/asr/transcribe-file/sessions` 生成 ASR live session。
- 导入成功后使用完整 session snapshot 渲染实时文字、候选提醒和后续 AI 派生产物，不在上传成功前清空上一场会议。
- 新增 transcript 导出端点 `GET /live/asr/sessions/{session_id}/transcript.txt`，按 `[mm:ss] text` 输出 final segments，并用 attachment header 下载。
- 现有 minutes 导出端点 `GET /live/asr/sessions/{session_id}/minutes.md` 增加 attachment header。
- Workbench 新增 `导出文字稿` 和 `导出纪要` 按钮，并在没有 transcript/minutes 时给出用户级提示。
- 删除确认已经展示来源、文字数、AI 建议、方案分析、纪要状态和删除范围。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_workbench.py
result=63 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
result=passed
```

涉及文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `code/web_mvp/backend/tests/test_file_convert.py`
- `code/web_mvp/backend/tests/test_workbench.py`

步骤：

- [x] 支持安全音频格式转换的 UI 提示。
- [x] 增加 transcript 导出。
- [x] 增加 minutes 导出。
- [x] 删除确认展示音频和派生产物范围。
- [x] file lane bundle 保持 Go。

### P1-2. Provider Config 和费用提示

目标：

配置透明，避免误用付费项。

状态更新（2026-07-08 DEC-253）：

```text
status=completed_for_p1_2_provider_config_and_cost
production_mvp_status=still_conditional_no_go
reason=P1-4 long meeting soak / P2-P3 desktop-mobile planning / final release verification still incomplete
```

已完成：

- 新增 `GET /providers/health`，只暴露非密钥 provider readiness。
- Workbench 启动检查读取 `/providers/health`，只展示 provider、model、configured 和本地 ASR 可用性。
- `docs/provider-config-and-cost-policy.md` 明确默认付费项只有启用 AI 分析时的 LLM gateway。
- 远程 ASR 默认关闭：`remote_asr_default_enabled=false`、`raw_audio_uploaded_by_default=false`。
- mainline evidence 和 release acceptance summary 汇总 `llm_call_count` 和 `llm_usage_total_tokens`。
- release 汇总会把 lane 顶层 `llm_called=true` 正确聚合到 `privacy_cost_flags.llm_called=true`。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_app.py::test_provider_health_endpoint_masks_llm_secret_and_disables_remote_asr_by_default \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_runs_startup_audio_check_and_explains_provider_readiness \
  tests/test_mainline_evidence_bundle_runner.py::test_simulated_realtime_lane_bundle_streams_wav_to_ws_and_writes_traceable_bundle \
  tests/test_release_acceptance_runner.py::test_release_acceptance_sums_lane_llm_usage
result=4 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
result=passed
```

涉及文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/llm_service.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- `docs/provider-config-and-cost-policy.md`

步骤：

- [x] 写 provider health endpoint。
- [x] UI 只显示 provider/model/configured，不显示 key。
- [x] LLM usage 进入 evidence bundle。
- [x] 远程 ASR adapter 只提供接口，不默认启用。
- [x] 文档记录默认成本策略。

### P1-3. 数据保留和隐私策略

目标：

所有本地数据和删除行为可解释。

状态更新（2026-07-08 DEC-254）：

```text
status=completed_for_p1_3_privacy_retention_delete
production_mvp_status=still_conditional_no_go
reason=P1-4 long meeting soak / P2-P3 desktop-mobile planning / final release verification still incomplete
```

已完成：

- 新增 `docs/privacy-retention-and-delete-policy.md`，明确 session、audio、transcript、cards、minutes、evidence 的本地保存位置和删除边界。
- `DELETE /live/asr/sessions/{session_id}` 返回结构化 `delete_scope`，明确删除 session record、transcript events、suggestion cards、approach cards、minutes。
- 删除响应明确 `audio`、`exports`、`evidence_bundle` 当前不归 live session repo 追踪，避免过度承诺。
- Workbench 删除确认展示会议来源、文字条数、AI 建议、方案分析、纪要状态和删除范围，并提示不会删除用户电脑另存的原始音频文件。
- `tools/mainline_evidence_bundle_runner.py` 和 `tools/release_acceptance_runner.py` 的 evidence JSON writer 均会 redact `api_key/authorization/token/secret` 和 `LLM_GATEWAY_API_KEY` 字符串值。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_mainline_evidence_bundle_runner.py \
  tests/test_release_acceptance_runner.py \
  code/web_mvp/backend/tests/test_app.py::test_delete_asr_live_session_reports_exact_delete_scope_without_overclaiming
result=23 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_release_acceptance_runner.py::test_release_summary_writer_redacts_secret_values
result=1 passed, 2 warnings
```

涉及文件：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_repository.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `docs/privacy-retention-and-delete-policy.md`

步骤：

- [x] 文档明确 session/audio/transcript/cards/minutes/evidence 保存位置。
- [x] API 删除返回完整 `delete_scope`。
- [x] UI 删除确认展示范围。
- [x] Evidence sanitizer 测试不含 secret。

### P1-4. 长会议稳定性和性能

目标：

20 到 60 分钟会议不会内存膨胀、卡死或刷屏。

状态更新（2026-07-08 DEC-255）：

```text
status=completed_for_p1_4_synthetic_soak_gate
production_mvp_status=still_conditional_no_go
reason=P2-P3 desktop-mobile planning / final release verification still incomplete
```

已完成：

- 新增 `tools/long_meeting_soak_runner.py`，构造确定性的 20 分钟模拟实时会议计划，默认 `chunk_seconds=2`，`chunk_count=600`，不会真实 sleep 20 分钟。
- runner 通过 metrics 注入记录 `asr_rtf`、`llm_call_count`、`llm_usage_total_tokens`、`memory_rss`、`card_count` 和 `suppression_count`。
- 默认不启动真实麦克风、不读取私人音频、不调用远程 ASR、不调用真实 LLM。
- 当卡片数超过 `max_cards_per_10_minutes` 时，runner 输出 `suppression_count`，并以 `suggestion_frequency_cap_exceeded` 作为 No-Go blocker。
- 缺失或非法 metrics 输出 `verdict=blocked`。
- `soak_report.json` 落盘前会 redacts secret-like 字符串，避免 `sk-...` 或 `Bearer ...` 泄露。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q tests/test_long_meeting_soak_runner.py
result=5 passed, 1 warning

python3 -m py_compile tools/long_meeting_soak_runner.py tests/test_long_meeting_soak_runner.py
result=passed

python3 tools/long_meeting_soak_runner.py --run-id p1-4-long-meeting-soak-20260708 --duration-minutes 20
artifact=artifacts/tmp/soak/p1-4-long-meeting-soak-20260708/soak_report.json
verdict=go
duration_minutes=20
expected_audio_seconds=1200
chunk_count=600
asr_rtf=0.1
llm_call_count=0
remote_asr_called=false

python3 tools/long_meeting_soak_runner.py --run-id p1-4-frequency-cap-20260708 --duration-minutes 20 --fake-metrics-json <24-card-over-cap-metrics>
artifact=artifacts/tmp/soak/p1-4-frequency-cap-20260708/soak_report.json
verdict=no_go
suppression_count=12
blockers=[suggestion_frequency_cap_exceeded]
```

边界：

- 该状态证明 P1-4 的 deterministic soak decision gate 和频率抑制 gate 可复现。
- 该状态不等于真实 backend 进程连续运行 20-60 分钟的生产压测；真实 RSS/CPU 采样需要后续在 release candidate 或桌面端集成阶段单独执行。
- 当前默认成本仍为零额外 ASR/LLM 调用；只有注入 metrics，不触发真实 provider。

涉及文件：

- `tools/long_meeting_soak_runner.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/metrics.py`
- `code/web_mvp/backend/tests/test_metrics.py`

步骤：

- [x] 构造 20 分钟模拟实时输入。
- [x] 记录 ASR RTF、LLM call count、memory、card count。
- [x] 超过频率上限时自动抑制建议。
- [x] 输出 soak report。

### P2-1. Mac 桌面客户端

目标：

Mac 上能以客户端形式运行。

状态更新（2026-07-08 DEC-256）：

```text
status=completed_as_mac_dev_shell_and_noop_ipc_evidence
production_mvp_status=still_conditional_no_go
reason=final release verification still incomplete; real desktop mic/worker/packaging remains next-stage work
```

已完成：

- `code/desktop_tauri` 已具备 Tauri v2 Mac dev shell scaffold。
- `tauri.conf.json` 指向本地 Web MVP：`devUrl=http://127.0.0.1:8765/`，主窗口加载 `workbench.html`。
- `Info.plist` 已声明 `NSMicrophoneUsageDescription` 和 `NSAudioCaptureUsageDescription`。
- 10 个 Tauri IPC command 已绑定，关键真实执行 flag 仍保持 false。
- 历史 PCWEB-118 受控 `cargo check` 证据存在。
- 历史 PCWEB-119 真实 Tauri no-op WebView IPC evidence 存在，10/10 command returned。
- 本轮清理了源码树内误生成的 `code/desktop_tauri/src-tauri/target`，构建输出继续使用 ignored `artifacts/tmp/desktop_tauri_target`。
- 修复 PCWEB-120 policy/import 漂移，worker mic source evidence packet 可从历史 Tauri no-op evidence 重新生成。
- 新增 `docs/desktop-mac-mvp-plan.md`。

证据：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py
result=13 passed, 1 warning

python3 -m py_compile tools/desktop_worker_mic_source_from_tauri_evidence.py
result=passed

PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_worker_mic_source_from_tauri_evidence.py \
  --tauri-evidence-path artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json
result=policy_validation_status=passed / tauri_evidence_validation_status=passed / worker_mic_source_approval_packet_status=ready_for_manual_review_not_executable / worker_mic_source_approval_status=not_approved
```

边界：

- 该状态证明 Mac dev shell + no-op IPC + evidence packet gate。
- 该状态不证明真实麦克风采集、系统音频采集、ASR worker spawn、audio chunk lifecycle、签名、notarization 或 `.app/.dmg` 交付完成。

涉及文件：

- `code/desktop_tauri/src-tauri/`
- `code/web_mvp/backend/`
- `docs/desktop-mac-mvp-plan.md`

步骤：

- [x] 决定 Tauri 作为当前 Mac MVP 壳。
- [x] 绑定本地 WebView 到 Workbench。
- [x] 明确麦克风权限说明。
- [x] 本地 backend 启动/停止策略。
- [x] 生成开发版运行命令和 smoke。

### P2-2. Windows 兼容计划

目标：

提前约束平台差异，不在 Mac 实现里写死不可移植假设。

状态更新（2026-07-08 DEC-257）：

```text
status=completed_as_windows_compatibility_plan
production_mvp_status=still_conditional_no_go
reason=final release verification still incomplete; Windows implementation remains future work
```

已完成：

- 新增 `docs/desktop-windows-compatibility-plan.md`。
- 明确 Mac-first、Windows-second，业务 UI/backend/core 共享。
- 记录 Windows 麦克风权限、WASAPI loopback、设备驱动、蓝牙耳机、杀软、SmartScreen、installer/signing 差异。
- 定义 desktop adapter interface：runtime、audio、ASR worker、packaging。
- 定义 Windows smoke checklist。

边界：

- 该状态是兼容性计划完成，不是 Windows 实现完成。
- 未跑 Windows Tauri WebView、未实现 WASAPI、未打包 installer、未处理签名。

涉及文件：

- `docs/desktop-windows-compatibility-plan.md`
- `code/desktop_tauri/src-tauri/src/`

步骤：

- [x] 记录 Windows 麦克风权限和 WASAPI/system audio 差异。
- [x] 抽象 desktop audio adapter。
- [x] 记录安装包和签名差异。
- [x] 定义 Windows smoke checklist。

### P3-1. 移动端远期规划

目标：

只做产品路线记录，不进入当前 MVP 实现。

状态更新（2026-07-08 DEC-258）：

```text
status=completed_as_mobile_future_plan
production_mvp_status=still_conditional_no_go
reason=final release verification still incomplete; mobile is not current MVP blocker
```

已完成：

- 新增 `docs/mobile-app-future-plan.md`。
- 明确 iOS/Android 作为 companion app 远期路线，不作为 PC/Mac 实时会议主链路。
- 记录 Apple Developer Program、App Privacy、App Review、Google Play Console、个人开发者测试要求、User Data/Data safety、中国 Android 市场差异。
- 明确移动端不是当前 P0/P1/P2 blocker。

边界：

- 未创建 iOS/Android 工程。
- 未注册开发者账号。
- 未提交应用市场。
- 未实现移动端录音。

涉及文件：

- `docs/mobile-app-future-plan.md`

步骤：

- [x] 记录 iOS 上架、账号、权限、隐私说明、审核风险。
- [x] 记录 Android 应用市场差异。
- [x] 明确移动端不是当前 P0/P1 的 blocker。

## 4. 下一阶段执行顺序

不得并行乱开主线。推荐顺序：

```text
1. P0-4 Workbench 产品化 UI 重构
2. P0-1 Browser live mic evidence lane
3. P0-2 实时自动建议 orchestrator
4. P0-3 ASR semantic quality gate
5. P0-5 Release acceptance runner
6. P1-1 录音导入和导出产品化
7. P1-2 Provider config 和费用提示
8. P1-3 数据保留和隐私策略
9. P1-4 长会议稳定性和性能
10. P2-1 Mac 桌面客户端
11. P2-2 Windows 兼容计划
12. P3-1 移动端远期规划
```

优先做 UI 产品化再做 browser live mic，是因为当前页面仍偏工程测试台；先降低理解成本，后续真实开麦验证才更接近用户实际使用。

## 5. 下一目标建议

建议下一次设定的开发目标：

```text
完成 Workbench 产品化 UI 重构和全按钮自测：
把当前测试台改成会议中可用页面，保留已通过的 file/simulated/real-mic-recorded evidence lane，
并让 Workbench smoke 覆盖开始会议、结束会议、导入录音、整理会议、历史记录、删除和错误状态。
```

目标完成标准：

```text
test_workbench.py 全绿
workbench_smoke.mjs 全绿
Workbench 页面加载新版 JS
按钮/状态/错误提示清单写入报告
不破坏 file/simulated/real-mic-recorded lanes
```

## 6. 固定自测命令

每完成一个 P0 子目标，至少跑：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  tests/test_mainline_evidence_bundle_runner.py \
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

node code/web_mvp/e2e/workbench_smoke.mjs

git diff --check

curl -sS http://127.0.0.1:8765/health
curl -sS http://127.0.0.1:8765/workbench | rg "workbench.js\\?v"
```

发布候选还必须跑：

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/mainline_evidence_bundle_runner.py \
  --lane simulated-realtime \
  --audio code/asr_runtime/outputs/simulated-release-review.16k.wav \
  --run-id release-simulated-realtime

PYTHONPATH=code/web_mvp/backend:code/core python3 tools/mainline_evidence_bundle_runner.py \
  --lane real-mic-recorded-realtime \
  --audio artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.wav \
  --health-report artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.health.json \
  --run-id release-real-mic-recorded-realtime
```

browser live mic 未通过前，release verdict 必须保持：

```text
Production MVP: Conditional No-Go
```

## 7. Stop Rules

必须停止并写 No-Go 的情况：

- 浏览器麦克风采样 `rms=0` 或 `active_sample_ratio=0`。
- ASR provider 为 mock/fake/fallback。
- ASR final 为空。
- ASR 语义质量不达标。
- LLM provider 是 mock，却进入生产验收端点。
- 建议卡没有 evidence span。
- Workbench 同 session 不可见。
- 删除验证失败。
- 任何 evidence bundle 包含 secret。
- 任何真实用户音频未经授权被读取。

## 8. 文档自检结果

本文件自检项：

```text
覆盖当前已完成状态: yes
覆盖 browser live mic 未完成边界: yes
覆盖前端剩余工作: yes
覆盖后端剩余工作: yes
覆盖桌面客户端剩余工作: yes
覆盖移动端远期边界: yes
包含固定自测命令: yes
包含 Stop Rules: yes
无占位符: yes
```
