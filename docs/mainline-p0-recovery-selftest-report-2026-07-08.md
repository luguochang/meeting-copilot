# Meeting Copilot P0 主链路恢复自测报告 - 2026-07-08

> 状态：P0 部分通过，真实麦克风 Lane 未通过
> 对应计划：`docs/p0-mainline-recovery-execution-plan-2026-07-08.md`
> 目标：验证“真实或用户授权音频 -> ASR -> LLM 建议/方案/纪要 -> Workbench 同 session 展示 -> 删除 -> evidence bundle”是否成立。

---

## 1. 本轮完成项

### 1.1 修复 Workbench 文字消失

已修复：

- `/live/asr/sessions/{session_id}/events` 读取时调用不存在的 `_asr_live_event_source_metadata`，导致前端拿不到事件。
- `试用示例` 创建 session 后强依赖二次 `/events`，二次读取失败时主区域空白。
- 录音中仍可点击导入、演示、删除、生成等冲突操作。
- 历史列表无法区分演示/导入录音/麦克风来源。
- 真实 WS fallback 到 Fake 时，后端会持久化 `provider_mode=mock`、`is_mock=true`、`asr_fallback_used=true`、`degradation_reasons`，不再冒充 real。
- Workbench 快照渲染现在兼容 raw `final` 和 `transcript_final`，避免“实时显示过，刷新/历史后没字”。
- 开始会议后主内容不再裸空，会显示“正在听，会实时显示文字”。
- 停止录音或刷新时，如果服务端最终快照暂时为空，会保留已有实时文字。
- 导入录音成功后使用同一套 `applySessionEvents()` 完整快照渲染，避免导入/历史/刷新三条路径不一致。
- 真实 ASR 空 final 不再导致 `/live/asr/sessions/{sid}/events` 404。后端会创建 degraded session：`final_count=0`、`passes_minimum_gate=false`、`degradation_reasons=["asr_final_empty"]`。
- Workbench 增加麦克风 peak/rms 电平提示。浏览器已连接但持续静音时，会提示检查浏览器权限、macOS 输入设备、输入音量，并说明外放声音不一定会进入麦克风输入。

结果：

- 示例会议可先用创建接口返回的 `live_events` 兜底渲染。
- 后端读取接口返回 `event_source`。
- 历史列表显示来源标签。
- 录音/整理中禁用冲突按钮。
- 文字消失问题已被回归测试覆盖，但真实麦克风 lane 仍需有效采集后才能判定 Go。

### 1.2 建立主线 Evidence Bundle Runner

新增：

- `tools/mainline_evidence_bundle_runner.py`
- `code/web_mvp/e2e/workbench_session_verify.mjs`
- `tests/test_mainline_evidence_bundle_runner.py`

runner 产物目录：

```text
artifacts/tmp/acceptance/<run-id>/
  manifest.json
  go_no_go.md
  upload_response.json
  session_events.json
  llm_runs.json
  suggestion_cards.json
  approach_cards.json
  minutes.md
  minutes.json
  ui_verification.json
  workbench-after.png
  delete_response.json
```

runner 的原则：

- 不把 mock/fake 写成真实 Go。
- 不把 API-only 写成 UI 已验证。
- 不把 LLM 未配置写成 LLM 通过。
- 不把真实麦克风静音样本写成麦克风通过。

---

## 2. 录音导入 Lane 结果

### 2.1 运行命令

```bash
PYTHONPATH=code/web_mvp/backend:code/core \
python3 tools/mainline_evidence_bundle_runner.py \
  --audio code/asr_runtime/outputs/simulated-release-review.16k.wav \
  --run-id p0-file-lane-20260708-ui
```

### 2.2 Evidence Bundle

目录：

```text
artifacts/tmp/acceptance/p0-file-lane-20260708-ui/
artifacts/tmp/acceptance/p0-file-lane-20260708-after-p0fix/
```

关键结果：

| 字段 | 值 |
|---|---|
| verdict | `go` |
| audio_source | `uploaded_wav` |
| asr_provider | `local_funasr_batch` |
| llm_provider | `real_gateway` |
| ui_coverage | `headless_chrome` |
| session_id | `file_5216710cce83` |
| transcript_char_count | `93` |
| final_segment_count | `1` |
| suggestion_card_count | `3` |
| approach_card_count | `3` |
| minutes_char_count | `401` |
| workbench_same_session_visible | `true` |
| frontend_utterance_count | `1` |
| frontend_card_count | `6` |
| frontend_minutes_visible | `true` |
| delete_verified | `true` |
| degradation_reasons | `[]` |

补丁后复跑结果：

| 字段 | 值 |
|---|---|
| run_id | `p0-file-lane-20260708-after-p0fix` |
| verdict | `go` |
| audio_source | `uploaded_wav` |
| asr_provider | `local_funasr_batch` |
| asr_provider_mode | `real` |
| asr_fallback_used | `false` |
| llm_provider | `real_gateway` |
| ui_coverage | `headless_chrome` |
| transcript_char_count | `97` |
| final_segment_count | `1` |
| suggestion_card_count | `3` |
| approach_card_count | `3` |
| minutes_char_count | `425` |
| frontend_utterance_count | `1` |
| frontend_card_count | `6` |
| frontend_minutes_visible | `true` |
| delete_verified | `true` |
| degradation_reasons | `[]` |

结论：

```text
录音导入 -> FunASR batch -> 真实 OpenAI-compatible LLM 中转站 -> 建议卡/方案卡/纪要
-> Workbench 同 session 展示 -> 删除

已通过 P0 file lane。
```

注意：

- 这证明了“导入录音主链路”可用。
- 这不等价于“真实麦克风实时会议主链路”可用。

---

## 3. 真实麦克风 Lane 结果

### 3.1 运行命令

```bash
PYTHONPATH=code/web_mvp/backend:code/core:tools \
python3 tools/real_mic_full_chain_runner.py \
  --session-id p0_real_mic_20260708 \
  --record-seconds 15
```

### 3.2 Evidence

报告：

```text
artifacts/tmp/real_mic_shadow_tests/p0_real_mic_20260708/full_chain_summary.json
```

音频：

```text
artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/p0_real_mic_20260708/audio.wav
```

关键结果：

| 字段 | 值 |
|---|---|
| runner_status | `blocked_by_local_asr` |
| capture_status | `recorded_from_real_microphone` |
| audio_file_size_bytes | `381092` |
| asr_status | `ok` |
| asr_text | 空 |
| final_count | `0` |
| candidate_count | `0` |

音量检测：

```text
Duration: 00:00:11.91
mean_volume: -91.0 dB
max_volume: -91.0 dB
```

随后播放本地测试音频再录一次：

```text
artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/p0_real_mic_speaker_probe/audio.wav
```

音量仍然是：

```text
mean_volume: -91.0 dB
max_volume: -91.0 dB
```

结论：

```text
真实麦克风 Lane 未通过。
当前不是 ASR 语义质量问题，而是命令行麦克风采集路径录到近静音。
```

影响：

- 不能声明真实麦克风实时会议全链路已通过。
- 后续必须优先修麦克风采集/权限/设备选择，或用浏览器 `getUserMedia` 产品入口建立真实麦克风证据。

---

## 4. 自动化验证

### 4.1 后端/前端相关测试

```bash
PYTHONPATH=code/web_mvp/backend:code/core \
pytest -q \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  tests/test_mainline_evidence_bundle_runner.py
```

结果：

```text
22 passed, 2 warnings
```

本轮 P0 回归补充：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_workbench.py
```

结果：

```text
25 passed, 2 warnings
```

新增覆盖：

- raw `final` 快照可渲染。
- 开麦后中心区不裸空。
- 空快照不覆盖已有实时文字。
- 导入录音成功后复用完整 session 快照渲染。
- degraded 空 ASR session 不再显示为“已生成文字”，也不会允许继续生成建议、方案或会后复盘。

补充 P0 回归：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  tests/test_mainline_evidence_bundle_runner.py
```

结果：

```text
36 passed, 2 warnings
```

新增覆盖：

- 真实 ASR 返回空 final 时仍创建 degraded live session，`/events` 返回 200。
- 空 final 不创建 `transcript_final`，`evaluation_summary.passes_minimum_gate=false`，防止污染 Go 证据。
- Workbench 静音麦克风状态提示已静态回归覆盖。
- Workbench 对 degraded 空 transcript session 的按钮禁用和状态文案已回归覆盖。

### 4.2 Workbench 浏览器 Smoke

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

结果：

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

### 4.3 Workbench 真实麦克风页面自测

服务：

```text
http://127.0.0.1:8765/workbench?debug=20260708-p0fix
```

操作：

```text
打开 Workbench -> 点击开始会议 -> 浏览器 getUserMedia 进入录音中 -> 等待约 5 秒 -> 点击结束会议 -> 打开历史 degraded session
```

结果：

| 字段 | 值 |
|---|---|
| session_id | `rec_mrbh7ml8` |
| provider | `sherpa_onnx_realtime` |
| provider_mode | `real` |
| is_mock | `false` |
| asr_fallback_used | `false` |
| degradation_reasons | `["asr_final_empty"]` |
| event_types | `["evaluation_summary"]` |
| passes_minimum_gate | `false` |
| final_event_count | `0` |

页面观察：

- 开始会议后进入 `录音中`，session meta 增加实时事件计数。
- 系统状态明确显示：`没有检测到麦克风声音。请检查浏览器权限、macOS 输入设备或输入音量；外放声音不一定会进入麦克风输入。`
- 结束会议后 `/live/asr/sessions/rec_mrbh7ml8/events` 返回 200，不再 404。
- 历史记录中可看到该 session，`0 条文字 · 0 条建议`。
- 打开该历史 session 后，页面显示 `未识别到有效语音`，不会显示“已生成文字”。
- `生成会议建议`、`分析方案利弊`、`生成会后复盘` 在该 No-Go session 上禁用，只保留删除。

结论：

```text
Workbench 真实麦克风产品入口已能建立可追溯 No-Go session。
当前仍未通过 real mic lane，因为麦克风输入持续静音，ASR final_count=0。
```

---

## 5. 当前 Go/No-Go

| Lane | 结论 | 原因 |
|---|---|---|
| 录音导入 file lane | Go | ASR、真实 LLM、Workbench 同 session、删除均通过 |
| Workbench 演示 smoke | Go for demo only | 用 fake LLM 和 mock ASR，只能证明 UI 操作链 |
| 真实麦克风 mic lane | No-Go | 浏览器入口可用，但麦克风输入静音，ASR final=0；已保存 degraded session |
| 整体 P0 | Partial / No-Go | file lane 已通，但真实麦克风未通 |

---

## 6. 下一步只做什么

下一步不再扩大评测范围，聚焦一个阻塞：

```text
修复真实麦克风采集路径，让 mic lane 能录到非静音音频并产生 ASR final。
```

建议顺序：

1. 先在 macOS/浏览器层修复真实麦克风输入：选择正确输入设备、提高输入音量，确保 Workbench 能显示“检测到麦克风声音”。
2. 如果浏览器权限弹窗出现，需要用户允许麦克风。
3. 保留当前电平显示；低于阈值时继续明确提示“没有检测到声音”。
4. 下一轮 real mic evidence 需要保存 `manifest/go_no_go`，不要只保存旧 shadow report 或单个 degraded session。
5. mic lane 产生 transcript final 后，再接同一套 LLM cards/minutes/UI/delete evidence bundle。

通过条件：

- 真实麦克风音频 `max_volume` 明显高于静音，例如高于 `-50 dB`。
- ASR final_count >= 1。
- 同一 session 产生建议卡/纪要。
- Workbench 可见同一 session。
- 删除验证通过。

---

## 7. 追加复测：Gate A 真实麦克风输入健康 - 2026-07-08 11:23

本轮根据四路审查后的新 checklist，先只跑真实麦克风输入健康 Gate A，不继续扩展 ASR/LLM。

设备枚举：

```bash
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
  | tee artifacts/tmp/audio_health/p0-real-mic-device-list-20260708-112308.log
```

结果：

```text
AVFoundation audio devices:
[0] MacBook Air麦克风
```

Healthcheck：

```bash
PYTHONPATH=code/web_mvp/backend:code/core \
python3 tools/audio_capture_healthcheck.py \
  --record-seconds 15 \
  --audio-device-index 0 \
  --output-audio-path artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.wav \
  | tee artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.json
```

结果：

| 字段 | 值 |
|---|---|
| health_status | `blocked_audio_too_quiet` |
| capture_status | `recorded_from_real_microphone` |
| audio_device_index | `0` |
| duration_seconds | `11.907` |
| sample_rate | `16000` |
| channel_count | `1` |
| rms | `0.0` |
| peak | `0.0` |
| active_sample_ratio | `0.0` |
| silence_ratio | `1.0` |

ffmpeg 音量复核：

```bash
ffmpeg -hide_banner -nostats \
  -i artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.wav \
  -af volumedetect \
  -f null - 2>&1 \
  | tee artifacts/tmp/audio_health/p0-real-mic-health-20260708-next.volumedetect.log
```

结果：

```text
mean_volume: -91.0 dB
max_volume: -91.0 dB
histogram_91db: 190507
```

结论：

```text
Gate A No-Go。当前唯一可见输入设备是 MacBook Air 麦克风，录音文件可写出，但样本仍是全静音。
按 P0 checklist，Gate A 不通过时不继续跑 Gate B/C，避免把 ASR/LLM 下游噪音当成主线进展。
```

本轮同时完成的 P0 小修复：

- Workbench 首屏接入 `/audio/check`，实测显示麦克风、本地识别、AI 分析状态。
- Workbench 历史列表将空 ASR session 标为 `降级 · 未识别到有效语音`。
- WebSocket 关闭后不再短暂显示“已生成文字”，等待 snapshot 判定。
- 显式 mock endpoint 持久化 `is_mock=true`、`provider_mode=mock`、`ingest_mode=mock_asr_session`。
- evidence bundle 的 `go_no_go.md` 按实际产物列 Evidence Files，real mic degraded bundle 不再列不存在的 file lane 文件。

验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  tests/test_mainline_evidence_bundle_runner.py
```

```text
40 passed, 2 warnings
```

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```
