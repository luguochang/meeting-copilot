# V2 FunASR 与真实麦克风证据

> 日期：2026-07-13
> 范围：本地 FunASR worker、8767 WebSocket 主链路、Chrome `getUserMedia`、同 segment final 候选升级。
> 成本边界：本轮 ASR 全部本地执行；建议卡代码路径使用本机 fake OpenAI-compatible gateway 验证，未调用用户的远程中转站。

## 结论

本轮确认了四件事：

1. FunASR 已经使用本机 Paraformer streaming 模型目录启动，不再在会议期间触发 ModelScope 下载；worker 会先发送显式 `ready` 控制事件。
2. 本地 FunASR 能对可控中文技术语音产生真实 partial/final，8767 服务能把事件持久化并保存录音。
3. Chrome 真实 `getUserMedia` 到 WebSocket 的路径已经在本机跑通。受控扬声器中文语音被 MacBook Air 麦克风采集，页面实时显示了技术会议文字并产生本地风险/待办/待确认提醒。
4. 原先“实时提醒有了但正式 AI 建议为 0”的根因是同一个 `segment_id` 的 stable partial 和 final 共用状态事件 ID，final 被去重逻辑跳过，调度器看不到 queued candidate。现在 final 会生成带 `_final` 后缀的状态/候选 ID，正式建议链路可以继续执行。

这不等同于生产发布通过。当前仍有三个生产阻塞：中文技术术语和断句质量、FunASR 冷启动导致的首字延迟、真实远程 gateway 下的录音期正式建议与长会 soak。

## 证据

### 1. Worker 级本地 ASR

输入：`artifacts/tmp/synthetic_audio/tingting_r130/incident-review-001.wav`，21.061 秒中文技术语音。

```text
worker_ready.event_type=ready
worker_ready.model_resolution=local_model_dir
ready_latency_ms=3549.2
partial_event_count=11
final_event_count=1
returncode=0
```

最终文本能够识别出 `order-worker`、消费堆积、lag、告警延迟、临时扩容、库存接口等上下文，但存在英文/中文混淆、数字和术语错误，不能直接作为生产中文准确率结论。

### 2. 8767 WebSocket 模拟实时链路

同一份 WAV 按 300ms 音频块发送到：

```text
ws://127.0.0.1:8767/live/asr/stream/ws/{session_id}?audio_source=synthetic_realtime_tts
```

证据：

```text
session_id=real_tts_ws_56a3914391
events_received=75
nonempty_partial_count=11
nonempty_final_count=1
first_nonempty_partial_ms=4020.5
first_final_ms=22306.1
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
audio_export_http=200
audio_export_bytes=673992
```

一次以 30ms 发送 300ms 音频的压力错误复现为 `ASR sidecar audio queue is full`，服务中止并记录 `stream_interrupted`。这证明有界队列保护生效，也说明验证器必须按实际音频节奏发送，不能用无限快的 burst 模拟实时输入。

### 3. Chrome 真实麦克风受控声源

本次使用 Chrome 的真实 `getUserMedia`，通过本机扬声器播放受控中文技术语音，采集设备为 MacBook Air 麦克风。页面日志确认：

```text
麦克风已授权
WS 已连接
sampleRate=16000
输入正常
```

同 session 后端证据：

```text
session_id=rec_mrih83en
input_source=browser_live_mic
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
acceptance_eligible=true
duration_ms=72300
audio_chunks=241
non_empty_final_count=1
persisted_event_count=12
suggestion_candidate_count=2
suggestion_card_count=0
audio_export_http=200
audio_export_bytes=2310188
audio_sha256_matches_session=true
```

浏览器侧健康与延迟指标：

```text
health_status=audio_capture_health_passed
rms=0.0332049584
peak=0.3821476102
active_sample_ratio=0.5759441836
first_audio_active_offset_ms=13313
first_text_visible_latency_ms=15118
first_partial_after_audio_active_latency_ms=1806
first_final_after_audio_active_latency_ms=59628
partial_visible_count=13
final_visible_count=1
remote_asr_called=false
llm_called=false
```

这次是真实浏览器麦克风采集，但声源是受控 TTS，不代表自然多人会议的远场、串音、多人说话和口音质量已经通过。

### 4. 正式建议卡执行路径

在本地 fake OpenAI-compatible gateway 上重跑真实 FunASR 流，确认同 segment final 修复后：

```text
session_id=real_tts_llm_c75f826704
queued_candidate_count_before_run=1
queued_candidate_id=asr_suggestion_candidate_asr_risk_event_vad_endpoint_001_final
generated_card_count=1
provider=openai_compatible_gateway
model=gpt-test
call_count=1
total_tokens=170
```

该验证调用的是本机 `127.0.0.1` fake gateway，不能作为用户中转站可用性或真实费用结论；它只证明正式 executor、OpenAI-compatible body、usage ledger 和建议卡落盘已接通。

## 代码与测试变化

- `code/asr_runtime/scripts/funasr_stream_worker.py`：显式 ready 事件、本地模型参数和 streaming profile。
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`：只把显式 ready 当作 worker ready，不再把第一条 ASR 文本误判为 ready。
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`：同 segment final 生成 `_final` 状态/候选，使 deferred partial 不阻塞 confirmed final。
- `code/web_mvp/backend/tests/test_funasr_sidecar.py`：覆盖 partial 不能冒充 ready。
- `code/web_mvp/backend/tests/test_live_events.py`：覆盖同 segment partial -> final 的 queued candidate 升级。

回归结果：

```text
code/asr_runtime: 89 passed, 1 warning
code/web_mvp/backend: 636 passed, 2 warnings
```

## 下一步与发布边界

1. 增加 FunASR 预热或“模型 ready 后才开始正式录音”的 UX，降低约 3.5 秒冷启动对首字延迟的影响。
2. 对本地模型做中文技术会议公开集/受控集的术语、数字、断句评测；当前不能用一次 TTS final 宣称准确率达标。
3. 使用用户真实 gateway 只做一次明确授权的录音期正式 AI 建议验收，记录 request、usage、card latency，不在日常自测中重复消耗额度。
4. 继续保持自然多人会议、真实 20 分钟 wall-clock soak、Mac 包和 Windows 实机发布门禁未通过。
