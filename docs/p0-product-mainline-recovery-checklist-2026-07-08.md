# Meeting Copilot P0 产品主线恢复 Checklist - 2026-07-08

> 状态：执行中
> 来源：2026-07-08 四路只读审查（产品/文档、前端 Workbench、后端架构、证据自测）
> 目标：停止继续扩大边界评测，把开发重新收敛到“真实会议实时 Copilot”主线。

## 1. 当前可信结论

当前不是“完全没做”，也不能宣布 Production MVP。

| Lane | 结论 | 可声明范围 |
|---|---:|---|
| 导入录音 file lane | Go | `uploaded_wav -> local_funasr_batch -> real_gateway -> cards/minutes -> Workbench -> delete` |
| Workbench demo smoke | Go for demo only | 只证明 mock ASR / fake LLM 的 UI 操作链 |
| 真实麦克风 real mic lane | No-Go | 已有可追溯 degraded session，但输入音频近静音，ASR final=0 |
| Production MVP | No-Go | 真实麦克风同 session Copilot 闭环未证明 |

可信 file lane Go evidence：

```text
artifacts/tmp/acceptance/p0-file-lane-20260708-after-p0fix/
```

可信 real mic No-Go evidence：

```text
artifacts/tmp/acceptance/p0-real-mic-lane-20260708-degraded/
artifacts/tmp/real_mic_shadow_tests/p0_real_mic_20260708/full_chain_summary.json
```

真实麦克风当前阻塞判断：

```text
health_status=blocked_audio_too_quiet
rms=0.0
peak=0.0
active_sample_ratio=0.0
silence_ratio=1.0
final_event_count=0
```

因此当前首要根因是“麦克风输入路径没有有效语音”，不是 LLM、建议卡或 UI 把文字吞掉。仍需保留“设备选择错误或权限/输入路由异常”的可能性。

## 2. 产品主线定义

P0 主线只认这一条链路：

```text
真实或用户授权音频
  -> 非 fake ASR
  -> ASR final / EvidenceSpan
  -> 会议状态与工程缺口候选
  -> 低频有证据建议
  -> 会后复盘
  -> 历史恢复
  -> 删除
  -> evidence bundle
```

产品价值不在“音频转文字”，而在：

- 会中发现 owner、deadline、rollback、test、monitoring 等工程缺口。
- 同一 session 持续维护会议状态、建议、方案分析和复盘。
- 每条建议和纪要结论必须带证据，不能空口断言。
- mock/demo/replay/fallback 只能作为开发和演示，不得进入真实 Go 结论。

## 3. 当前主要缺口

1. 真实麦克风 lane 没有通过：有录音文件但近静音，ASR final=0。
2. Workbench 仍像按钮实验台：顶部按钮过多，demo、真实、降级历史混在同一认知层。
3. “实时建议”不是真正实时：录音中只实时转写，LLM 建议仍依赖结束后手动点击。
4. 后端主会话模型双轨：`/live/asr/sessions` 与旧 `/sessions` 并行，生产主线边界不清。
5. live suggestion card schema 与 core `SuggestionCardV1` 不完全一致，证据模型需收敛。
6. mock/fake 隔离已有元数据，但显式 mock endpoint 仍应把 `is_mock/provider_mode/ingest_mode` 持久化为硬字段。
7. `go_no_go.md` 的 real mic degraded bundle 文件列表不准确，影响证据包可信度。
8. 当前 evidence 来自脏工作树，后续报告必须明确 dirty 状态或形成可复现 commit。

## 4. 禁止继续扩大的方向

P0 未通前，不继续做：

- 新 readiness / preflight / approval wrapper。
- 安装包、Tauri、Mac/Windows/iOS/Android 发布链路。
- 系统音频、多 speaker diarization、Jira/Linear/GitHub 自动建单。
- ASR provider 横向扩展评测。
- 在 real mic 没有有效 transcript 前做 LLM 质量扩展评测。
- 把 demo smoke、API-only、mock/fake、replay/local-event-file 当真实主链路 Go。

## 5. 下一轮执行 Gate

### Gate A：真实麦克风输入健康

目标：证明本机输入设备能采到有效语音。

必须保存：

- avfoundation 设备列表。
- 选择的 `audio_device_index`。
- healthcheck JSON。
- WAV 文件。
- ffmpeg `volumedetect` 日志。

Go 标准：

```text
health_status=audio_capture_health_passed
duration_seconds >= 10
sample_rate=16000
channel_count=1
rms >= 0.01
peak >= 0.05
active_sample_ratio >= 0.08
capture_status=recorded_from_real_microphone
```

Gate A 不过，不跑 Gate B/C。

### Gate B：真实麦克风 -> 本地 ASR -> Web handoff

目标：只证明真实麦克风能变成文字并进入 Web live session，不跑 LLM。

Go 标准：

```text
runner_status=main_flow_passed
asr.status=ok
asr.text 非空
event_counts.final >= 1
web_handoff.handoff_status=web_live_asr_ingested
web_handoff.live_event_counts.transcript_final >= 1
remote_asr_called=false
llm_called=false
```

Gate B 不过，不跑 Gate C。

### Gate C：真实麦克风 P0 product lane

目标：同一真实麦克风 session 跑通 transcript、建议、方案、复盘、Workbench、历史、删除。

Go 标准：

```text
audio_source=real_mic
real_mic_health_status=audio_capture_health_passed
asr_provider_mode=real
asr_fallback_used=false
transcript_char_count > 0
final_segment_count >= 1
llm_provider=real_gateway
llm_called=true
suggestion_card_count >= 1
approach_card_count >= 1
minutes_char_count > 0
all_cards_have_evidence=true
ui_coverage=headless_chrome
workbench_same_session_visible=true
frontend_utterance_count >= 1
frontend_card_count >= 1
frontend_minutes_visible=true
delete_verified=true
degradation_reasons=[]
```

## 6. P0 修复 Checklist

### A. 证据与真实/演示隔离

- [x] 显式 mock session 必须持久化 `is_mock=true`、`provider_mode=mock`、`ingest_mode=mock_asr_session`，即使 provider 名不是 `local_mock_asr`。
- [ ] local event file session 必须在 summary/events/UI/evidence 中显示 replay/local-file 来源，不得被误解为 live mic。
- [ ] fake fallback session 必须禁止进入真实 Go bundle。
- [x] real mic degraded bundle 的 `go_no_go.md` 文件列表必须与真实产物一致。

### B. Workbench 主流程可理解

- [x] 首屏健康检查接入 `/audio/check`，展示麦克风、ASR、AI 分析是否可用。
- [x] 历史列表区分“我的会议”和“示例/演示/降级”，至少用强标签和文案说明。
- [x] 真实麦克风空 final 历史项必须显示“未识别到有效语音”，不能只显示 `0 条文字`。
- [x] WebSocket 关闭后不能短暂显示“已生成文字”，应保持“正在整理”直到 snapshot 判定。
- [ ] 建议卡和方案卡进入稳定区域；不再只附着在 transcript 底部。
- [ ] 顶部按钮收敛：主动作优先，示例/刷新等降级为次级入口。

### C. 真实麦克风主链路

- [x] 设备列表和设备 index 写入 evidence。
- [ ] healthcheck 支持明确记录候选设备和音量门槛。
- [ ] real mic runner 支持从 Gate A 的设备 index 继续跑 Gate B。
- [ ] 增加真实麦克风 product lane bundle：录音/ASR/LLM/UI/delete 统一入 manifest。

### D. 后端生产边界

- [ ] 确认 `/live/asr/sessions` 是 P0 主线 session API，旧 `/sessions` 暂作为历史/core snapshot path。
- [ ] 增加 live ASR record 到 core snapshot/card contract 的投影测试或明确 No-Go reason。
- [ ] `llm_request_draft_event` 或 execution preview 必须携带 evidence quote，而不只是 evidence id。
- [ ] LLM correction 从 WebSocket finalize 的强同步路径中拆出或加超时/降级可观测。
- [ ] 为 live/file/mock/local-event-file 统一 `audio_retention` 字段。

## 7. 本轮立即执行范围

先做高收益小修，不做大拆：

1. mock endpoint 持久化真实/演示硬字段。
2. Workbench 启动前健康检查与来源状态文案。
3. 历史列表强化 degraded/mock 标签。
4. 修复 WS close 后短暂 `ready` 的误导状态。
5. 更新 decision log，说明本轮四路审查后的执行方向。

这些修完后，再进入 Gate A 的真实麦克风输入健康验证。

## 8. 本轮执行结果 - 2026-07-08 11:23

已完成：

- 后端显式 mock session 持久化 `is_mock/provider_mode/ingest_mode`，避免自定义 provider 名绕过 mock 判定。
- Workbench 首屏接入 `/audio/check`，浏览器实测显示：`麦克风可用（1 个输入）`、`本地识别可用 · FunASR · sherpa`、`AI 分析已配置`。
- Workbench 历史列表实测把 `rec_mrbh7ml8` 显示为 `降级 · 未识别到有效语音`。
- WebSocket close 后不再先设为 `ready`，而是保持 `processing` 等待 snapshot 判定。
- evidence runner 的 `go_no_go.md` 改为按实际 bundle 文件渲染 Evidence Files，real mic degraded bundle 不再列 file lane 的 `upload_response.json/llm_runs.json/minutes.md`。

Gate A 真实麦克风输入健康复测：

```text
设备列表：artifacts/tmp/audio_health/p0-real-mic-device-list-20260708-112308.log
输入设备：[0] MacBook Air麦克风
Health JSON：artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.json
WAV：artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.wav
Volumedetect：artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.volumedetect.log
```

结果：

```text
health_status=blocked_audio_too_quiet
duration_seconds=11.907
sample_rate=16000
channel_count=1
rms=0.0
peak=0.0
active_sample_ratio=0.0
silence_ratio=1.0
mean_volume=-91.0 dB
max_volume=-91.0 dB
audio_device_index=0
capture_status=recorded_from_real_microphone
```

结论：

```text
Gate A No-Go。设备能写出 WAV，但输入仍是全静音。
按本 checklist，Gate A 不过，不继续跑 Gate B/C。
下一步必须先修 macOS/浏览器/输入设备的麦克风有效输入问题，直到 Workbench 或 healthcheck 能检测到非静音语音。
```
