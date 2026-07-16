# P0 Real Mic Recorded Realtime Self-Test Report

> 日期：2026-07-08
> 状态：Real mic recorded realtime lane Go；browser live mic 仍需单独验证
> 证据目录：`artifacts/tmp/acceptance/p0-real-mic-recorded-realtime-afplay-20260708-01/`

## 结论

本轮把 P0 主链路从“只做真实麦克风健康检查”推进到可追溯的完整业务链路：

```text
真实麦克风录音 -> realtime WebSocket ASR -> ASR live session
-> LLM 建议卡 -> 方案卡 -> 会议纪要
-> Headless Workbench 同 session 可见 -> 删除验证
```

最终通过的 manifest：

```text
verdict=go
audio_source=real_mic_recorded_wav
counts_as_real_mic_go_evidence=true
browser_live_mic_go_evidence=false
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
llm_provider=real_gateway
transcript_char_count=86
final_segment_count=1
suggestion_card_count=3
approach_card_count=3
minutes_char_count=404
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
delete_verified=true
degradation_reasons=[]
```

边界必须保留：这次是“真实麦克风采集到的录音文件，再按实时 chunk 回放进同一条 WebSocket ASR 管道”。它证明真实麦克风录音可进入主业务链路，但不是浏览器 `getUserMedia` live mic 的最终通过证据。浏览器 live mic 仍需在用户方便配合时单独跑。

## 本轮代码变化

- `tools/mainline_evidence_bundle_runner.py`
  - 新增 `--lane real-mic-recorded-realtime`。
  - 新增 `--health-report`。
  - 新增 `run_real_mic_recorded_realtime_lane_bundle(...)`。
  - WebSocket streaming helper 支持传入 `audio_source`。
  - 健康检查未通过时 fail closed，不继续 ASR/LLM/UI。
  - manifest 增加 `counts_as_real_mic_go_evidence` 和 `browser_live_mic_go_evidence`。

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
  - acceptance lane 允许 `real_mic_recorded_wav` 进入正式派生端点。

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
  - Workbench 识别 `real_mic_recorded_wav`，展示为“真实麦克风录音”。
  - 明确提示它不是浏览器实时采集证据。

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
  - 增加 `real_mic_recorded` badge 样式。
  - 更新 Workbench JS cache-busting 版本。

## TDD 记录

先写红灯测试：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q \
  tests/test_mainline_evidence_bundle_runner.py::test_real_mic_recorded_realtime_lane_requires_passed_health_before_streaming \
  tests/test_mainline_evidence_bundle_runner.py::test_real_mic_recorded_realtime_lane_streams_real_mic_wav_to_ws_and_writes_traceable_bundle
```

红灯结果：

```text
2 failed
AttributeError: module 'mainline_evidence_bundle_runner' has no attribute 'run_real_mic_recorded_realtime_lane_bundle'
```

Workbench 文案红灯：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_labels_real_mic_recorded_wav_as_recorded_not_browser_live
```

红灯结果：

```text
1 failed
assert 'real_mic_recorded_wav' in workbench.js
```

实现后 focused green：

```text
2 passed, 2 warnings
1 passed, 2 warnings
```

## 自测过程

### Attempt 1：当前外放环境真实麦克风录音

健康检查：

```text
audio=artifacts/tmp/audio_health/real-mic-gate-a-20260708-212055.wav
health_status=audio_capture_health_passed
duration_seconds=13.336
rms=0.036224
peak=0.293457
active_sample_ratio=0.836425
```

完整 lane：

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/mainline_evidence_bundle_runner.py \
  --lane real-mic-recorded-realtime \
  --audio artifacts/tmp/audio_health/real-mic-gate-a-20260708-212055.wav \
  --health-report artifacts/tmp/audio_health/real-mic-gate-a-20260708-212055.health.json \
  --run-id p0-real-mic-recorded-realtime-20260708-01
```

结果：

```text
verdict=no_go
asr_provider=sherpa_onnx_realtime
transcript_char_count=83
suggestion_card_count=0
approach_card_count=0
```

根因：麦克风录到的是非技术会议内容（“蜜雪冰城/张红超”等），ASR 本身有 final，但候选策略没有工程决策/风险/行动项可触发，建议卡为 0。这个失败说明产品不会对非技术内容强行生成工程建议，是合理 No-Go，不是主链路代码断裂。

### Attempt 2：`say` 实时播放技术会议文案并录麦克风

健康检查：

```text
audio=artifacts/tmp/audio_health/p0-real-mic-tts-20260708-213652.wav
health_status=audio_capture_health_passed
duration_seconds=18.388
rms=0.064962
peak=1.0
active_sample_ratio=0.884624
clipping_ratio=0.000007
```

结果：

```text
verdict=no_go
asr_provider=sherpa_onnx_realtime
transcript_char_count=95
suggestion_card_count=0
approach_card_count=0
```

根因：麦克风实际录入没有稳定捕获到技术会议文案，ASR 输出语义混乱，候选为 0。FunASR streaming probe 在同一段音频上更差（final 仅“在音就”），因此没有切换 provider。

### 对照：干净 TTS WAV 直喂实时 ASR

命令生成的干净 WAV：

```text
artifacts/tmp/audio_health/p0-clean-tts-technical.wav
sha256=52368242d63ec7ee5534d4e260707679b7c7d5f95188f58fc1b977a30f0aebd6
duration=18.952688s
```

Sherpa probe 结果：

```text
今天技术会议讨论支付接口灰度发布我们先灰度百分之五监控错误率和九十九延迟如果错误率超过百分之零点一立刻回滚张三负责补充回滚方案明天确认核告景阈值
```

结论：本地 Sherpa 对干净中文技术会议音频可以识别出关键工程语义；前两次失败主要来自真实麦克风声学路径/背景声/播放音量，而不是业务链路必然失败。

### Attempt 3：`afplay` 干净技术 WAV，经 MacBook 麦克风真实录入

操作边界：

- 读取当前系统音量：`31`。
- 临时设置输出音量为 `75`。
- 播放 `artifacts/tmp/audio_health/p0-clean-tts-technical.wav`。
- 同时通过 `MacBook Air麦克风` 录音。
- 结束后恢复系统音量为 `31`。

健康检查：

```text
audio=artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.wav
health_status=audio_capture_health_passed
duration_seconds=17.979
rms=0.088097
peak=0.7229
active_sample_ratio=0.872794
silence_ratio=0.127206
clipping_ratio=0.0
```

ASR probe：

```text
可以用今天技术会议讨论致付接口规律发表我们先挥百分之五监控错误率和九十九延迟如果错误率超过百分之零点一立刻回滚张三负责补充回管方案明天确认<unk> 和告警阈值
```

虽然有错字，但保留了“技术会议、接口、错误率、回滚、张三、监控”等触发主链路的关键内容。

完整 bundle 命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/mainline_evidence_bundle_runner.py \
  --lane real-mic-recorded-realtime \
  --audio artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.wav \
  --health-report artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.health.json \
  --run-id p0-real-mic-recorded-realtime-afplay-20260708-01
```

完整 bundle 结果：

```text
verdict=go
audio_source=real_mic_recorded_wav
counts_as_real_mic_go_evidence=true
browser_live_mic_go_evidence=false
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
llm_provider=real_gateway
transcript_char_count=86
final_segment_count=1
suggestion_card_count=3
approach_card_count=3
minutes_char_count=404
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
delete_verified=true
degradation_reasons=[]
```

最终 transcript：

```text
可以，今天技术会议讨论支付接口灰度发布，我们灰度百分之五，监控错误率和 P99 延迟，如果错误率超过百分之零点一立刻回滚，张三负责补充回滚方案，明天确认 SLO 和告警阈值
```

## 证据文件

`artifacts/tmp/acceptance/p0-real-mic-recorded-realtime-afplay-20260708-01/`：

```text
manifest.json
go_no_go.md
real_mic_health_report.json
input_audio.sha256
ws_events.json
session_events.json
llm_runs.json
suggestion_cards.json
approach_cards.json
minutes.json
minutes.md
ui_verification.json
workbench-after.png
delete_response.json
sessions_list_before_delete.json
sessions_list_after_delete.json
ui_verification.stdout.log
ui_verification.stderr.log
```

## 当前产品状态

```text
Demo/mock: Go for demo only
File lane: Go
Simulated realtime wav: Go
Real mic recorded realtime lane: Go
Browser live mic lane: Not yet proven
Production MVP: Conditional No-Go until browser live mic and sustained meeting quality pass
```

## 后续主线

下一步不需要继续重复录音边界实验，应进入两个明确方向：

1. 浏览器 live mic gate：在用户方便配合时，用 Workbench 页面直接开麦，跑 `getUserMedia -> WebSocket -> ASR -> cards/minutes/UI/delete`。通过前不能写成 browser live mic Go。
2. 真实会议质量提升：加入输入电平提示、背景声/声学质量提示、ASR 语义质量 gate、必要时支持系统音频/虚拟声卡或远程 ASR provider 作为可选配置，但默认不新增付费 ASR。
