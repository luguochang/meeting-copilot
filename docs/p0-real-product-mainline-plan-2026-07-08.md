# Meeting Copilot P0 Real Product Mainline Plan - 2026-07-08

> 状态：当前执行入口
> 来源：用户反馈 + 产品/前端/后端多 Agent 只读审查 + 本地代码审查
> 目标：停止继续扩大评测边界，把项目拉回“中文技术会议实时 Copilot”主线。
> 当前结论：`file lane Go / demo only / real mic No-Go / Production MVP No-Go`。

## 1. 这轮重新对齐的结论

当前项目不是没有主链路，也不是已经生产可交付。

已证明：

- 导入录音 file lane 已跑通：`uploaded_wav -> local_funasr_batch -> real_gateway -> suggestion cards -> approach cards -> minutes -> Workbench same session -> delete`。
- Workbench demo smoke 能跑，但只证明示例和 UI 操作链，不证明真实会议。
- `/live/asr/sessions` 已是当前 P0 主线 API；旧 `/sessions` 先保留为 legacy/core snapshot path。

未证明：

- 真实麦克风能采到有效中文技术会议语音。
- 真实麦克风能生成非 fake ASR final。
- 真实麦克风同一 session 自动进入建议、方案、复盘、历史和删除。
- 当前页面能让非开发者一眼理解“真实会议、导入录音、示例、降级”的区别。
- 后端 mock/dev/local-event-file/real live API 已达到生产隔离。

当前最大的主线问题：

```text
Workbench 看起来能点很多东西，但真实产品主动作不清晰；
真实麦克风输入仍是 No-Go；
后端主线骨架已接通，但 mock/demo/local-file/legacy snapshot 没有生产级隔离。
```

2026-07-08 追加三路只读复审结论：

- 前端：Workbench 已有 `开始会议 -> getUserMedia -> WebSocket`、`导入录音 -> file ASR`、`生成会议建议/方案/复盘`、`历史/删除` 等功能，但仍偏工程验证台；`试用示例` 是 mock，`生成会议建议` 是手动批量 LLM，不等于实时自动建议。
- 后端：当前真实产品更像 `/live/asr/sessions` 族 API，但旧 `/sessions` core snapshot、demo fixture、mock ASR、local-event-file、file upload 和 real mic 都并存；生产主链路缺少统一 `LiveSessionRecord` schema、source policy gate 和 core card contract。
- 测试：`file upload lane` 是最可信的已跑通子链路；大量名字带 real/mainline 的测试实际使用 fake recognizer、fake LLM、mock session 或 local server，只能证明连接路径，不能证明真实麦克风产品主链路。
- 下一步不再扩评测，只按 `Gate A: 有效真实输入 -> Gate B: 非 fake ASR final -> Gate C: 同 session 建议/方案/复盘/历史/删除/evidence` 串行推进。

## 2. 产品初心与不可变验收

产品定位仍是：

```text
中文技术会议实时 AI Copilot
不是通用音频转文字工具
不是会后摘要工具
不是研发调试面板
```

P0 只认这一条真实产品链路：

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

No-Go 条件：

- 只有文字，没有建议卡：No-Go。
- 只有会后总结，没有会中或准实时建议：No-Go。
- 建议卡没有证据片段：No-Go。
- mock/fake/demo 被包装成真实能力：No-Go。
- file lane Go 被外推成 real mic Go：No-Go。
- real mic 没有有效输入还继续跑 LLM 质量评估：No-Go。

## 3. Lane 状态

| Lane | 当前状态 | 下一步 |
|---|---:|---|
| Demo/mock | Go for demo only | 保留为“查看示例”，必须强标示，不进入真实验收 |
| File lane | Go | 保持回归，不重复包装为主线进展 |
| Real mic command-line capture | No-Go | 已录到全静音/近静音，不能继续下游 |
| Real mic browser Workbench | No-Go / 待实测 | 先修 UI 与有效输入，再跑同 session bundle |
| Production MVP | No-Go | 等 real mic Gate C 通过 |

## 4. 当前真实缺陷

### 4.1 前端缺陷

- 顶栏动作过多，真实会议、导入录音、示例、历史、刷新、删除、建议、方案、复盘全部同级。
- `试用示例` 与真实会议入口并列，用户容易把 demo 当真实能力。
- `刷新实时文字` 实际是 snapshot reload，不是实时订阅。
- 中央 `stream` 同时承载原文、规则候选、LLM 建议、方案卡、错误空态，信息层级混乱。
- empty final 之前会先移除 `live-partial`，可能造成“文字又没了”的体感。
- fake fallback 以前会优先显示为演示，应该显示为“非真实识别/降级”。
- `/audio/check` 以前把文件转写可用和实时 ASR 可用混在一起，导致用户以为开麦一定可识别。

### 4.2 后端缺陷

- `app.py` 过胖，路由、服务编排、领域规则、文件读写、demo/mock、安全校验混在一起。
- `/live/asr/sessions`、旧 `/sessions`、`/live/asr/mock/sessions`、`/live/asr/local-event-files/sessions` 共同暴露，生产语义未收敛。
- mock ASR session 可以继续调用 LLM 生成 cards，历史中会出现“示例 ASR + 真实 LLM”的混合记录。
- file upload 和 local-event-file record metadata 不完全统一，例如 `provider_mode/is_mock/asr_fallback_used/degradation_reasons/ingest_mode` 有入口差异。
- WebSocket 持久化失败主要是 warning，缺少对前端明确失败事件。
- LLM correction 可能因环境变量存在自动发生，成本和隐私开关不够显式。
- JSON file repository 只适合本地 MVP，不适合生产并发和审计。

### 4.3 真实麦克风缺陷

最新可信证据：

```text
health_status=blocked_audio_too_quiet
rms=0.0
peak=0.0
active_sample_ratio=0.0
silence_ratio=1.0
mean_volume=-91.0 dB
max_volume=-91.0 dB
final_event_count=0
```

判断：

```text
当前 real mic 首要 blocker 是输入层没有有效语音，不是 LLM 或建议卡质量问题。
```

## 5. 本轮已经完成的 P0 止血

- `/audio/check` 增加 `file_asr_available`、`realtime_asr_available`、`realtime_asr_providers`、`asr_readiness_summary`，避免把“导入录音可用”误说成“实时会议可用”。
- Workbench 启动检查文案区分 `实时识别可用`、`实时识别不可用；导入录音可用`、`本地识别不可用`。
- Workbench source 判定中 `fallbackUsed/degradation` 优先于 `isMock`，fake fallback 不再显示成普通演示，而是显示 `降级 / 非真实识别`。
- raw empty final 不再先移除 `live-partial`，会保留最后一条临时文字并提示“最终识别暂时为空”。
- 已补测试：
  - `test_audio_check_distinguishes_file_asr_and_realtime_asr`
  - `test_workbench_runs_startup_audio_check_and_explains_provider_readiness`
  - `test_workbench_history_labels_demo_degraded_and_live_mic_sessions`
  - `test_workbench_empty_final_preserves_last_live_partial`

## 6. 后续执行 Checklist

### A. Workbench 变成产品页

- [x] `/audio/check` 区分实时 ASR 与文件 ASR。
- [x] fake fallback 显示为降级/非真实识别。
- [x] empty final 保留最后一条临时文字。
- [x] 建议卡、方案卡从 transcript 流里分离到稳定区域，避免把正式 AI 输出混在实时文字下面。
- [ ] 顶栏收敛为主动作：`开始/结束会议`、`导入录音`、`历史`；示例、刷新、删除、生成类动作降级到明确区域或菜单。
- [ ] 会后复盘继续保留稳定区域，并补同 session 自动刷新/历史选中态。
- [ ] 开始会议后若实时 ASR 不可用，主按钮不得暗示真实识别可用；允许“试用录音流程”但必须标明非真实。
- [ ] 新增 browser smoke：stub `getUserMedia` + WebSocket，覆盖 partial -> empty final -> snapshot empty 的 UI 行为。

### B. 真实麦克风 Gate

- [ ] Gate A：通过 Workbench 或 healthcheck 证明有效输入，不再是静音。
- [ ] Gate A 证据必须包含设备、音量、电平、WAV、volumedetect。
- [ ] Gate B：真实音频 -> 本地 ASR -> Web handoff，且 ASR text 非空、final >= 1。
- [ ] Gate C：同一 real mic session 自动/准自动生成建议、方案、复盘、历史、删除、evidence bundle。
- [ ] Gate C 不通过前，不能声明 Production MVP。

### C. 后端生产边界收敛

- [ ] 为 live ASR record 建统一 schema，所有入口写齐 `provider/provider_mode/is_mock/ingest_mode/asr_fallback_used/degradation_reasons/audio_retention`。
- [ ] mock/degraded session 默认禁止进入真实 LLM execution；如保留演示，必须 dev-only 或显式 demo flag。
- [ ] local-event-file 创建接口返回的 `safe_to_call_llm_now=false` 必须在 cards/approach/minutes 消费端执行，不能只作为 ingest 响应提示。
- [ ] live suggestion card 若继续标 `SuggestionCardV1`，必须适配 core contract；否则改名为 live draft card，避免把轻量卡片误当正式生产卡。
- [ ] `/live/asr/sessions` 作为 P0 主线；旧 `/sessions` 写明 legacy，避免前端新功能再接旧接口。
- [ ] WS 持久化失败向前端发送明确 `provider_error` 或 close reason，不只写 warning。
- [ ] LLM correction 改为显式开关或记录 `remote_llm_called/usage/degraded reason`，避免隐藏成本。
- [ ] 拆分 `app.py` 的计划后置到主链路跑通后执行，先不大拆影响 P0。

### D. Evidence Bundle

- [ ] file lane 保留每日/阶段回归，不再重复包装为真实主线。
- [ ] real mic bundle 必须同时覆盖 input health、ASR、LLM、UI、delete。
- [ ] mock/demo/local-event-file bundle 必须自动 No-Go for production。
- [ ] `go_no_go.md` 必须列真实存在的证据文件，不能复制 file lane 文件列表。

## 7. 下一步执行顺序

1. 跑完整 focused tests，确认本轮止血不破坏现有 file/demo 链路。
2. 用当前 Workbench 做一次页面按钮/状态自测，确认页面至少不会再把 fallback 和 empty final 误导成成功。
3. 若浏览器可用，进行真实麦克风 UI lane：先观察电平，有声音再继续 END -> snapshot。
4. 如果电平仍静音，停止下游 ASR/LLM，输出 Gate A No-Go，并要求修系统输入或浏览器权限。
5. 如果电平有效且 ASR final 非空，立即进入 Gate C：生成建议、方案、复盘、历史打开、删除、evidence bundle。

## 8. 禁止事项

- 不再新增同类 readiness/preflight/approval wrapper。
- 不再扩 ASR provider 横评。
- 不用 demo smoke 证明真实会议。
- 不用 file lane Go 证明真实麦克风。
- 不在 real mic 没有 ASR final 前做 LLM 质量评测。
- 不在 P0 未通前推进安装包、移动端、系统音频、多人识别、自动建单。
