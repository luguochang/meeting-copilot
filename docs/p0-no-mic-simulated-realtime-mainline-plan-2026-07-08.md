# P0 No-Mic Simulated Realtime Mainline Plan - 2026-07-08

> 状态：当前执行计划
> 背景：用户当前不方便配合真实麦克风收音。真实麦克风 Gate A/B/C 暂停，不继续消耗时间在静音输入上。
> 目标：用仓库内合成/公开授权 WAV 模拟“会议中实时音频流”，跑通实时 ASR 协议和后续 Copilot 主业务流。

## 1. 决策

新增一条 `simulated_realtime_wav` lane：

```text
公开/合成 WAV
  -> 按实时 chunk 发送到 /live/asr/stream/ws/{session_id}
  -> 非 fake realtime recognizer
  -> ASR live session
  -> suggestion cards
  -> approach cards
  -> minutes
  -> Workbench same-session verification
  -> history/delete
  -> evidence bundle
```

这条 lane 只证明“实时协议 + 后续业务链路”可运行，不证明真实麦克风可用。真实麦克风仍保留为 `Real mic: No-Go / user final validation required`，必须等用户可配合后再跑。

## 2. 为什么要做

当前真实麦克风卡在浏览器/系统输入近静音：

```text
rms=0
peak=0
active_sample_ratio=0
health_status=blocked_audio_too_quiet
```

继续反复开麦只会重复 Gate A No-Go。产品主线还需要验证：实时音频进入系统后，是否能自动持久化文字、生成建议、方案、纪要、历史和删除证据。`simulated_realtime_wav` 可以绕开设备输入问题，先把非麦克风部分做完整。

## 3. 验收边界

必须满足：

- manifest `audio_source=simulated_realtime_wav`。
- manifest 明确 `counts_as_real_mic_go_evidence=false`。
- ASR provider 不能是 `fake` 或 `local_mock_asr`。
- ASR fallback 不能使用。
- session 必须有非空 `transcript_final`。
- 同 session 必须生成建议卡、方案卡和会议纪要。
- Workbench 能打开同一 session，并看到文字、卡片和纪要。
- 删除后同一 session 的 events API 返回 404。

不能宣称：

- 不能把它写成真实麦克风 Go。
- 不能把它写成生产发布 Go。
- 不能把 demo/mock ASR 或 fake LLM 包装成 no-mic Go。

## 4. 实现任务

### Task A: evidence runner 增加 simulated-realtime lane

修改 `tools/mainline_evidence_bundle_runner.py`：

- 新增 `run_simulated_realtime_lane_bundle(...)`。
- 使用 `meeting_copilot_web_mvp.mic_capture.pcm_chunks_from_wav()` 将 WAV 切为 Float32 PCM chunk。
- 用 FastAPI `TestClient.websocket_connect()` 发送 chunk 和 `END`。
- 从 `/live/asr/sessions/{id}/events` 读取持久化 session。
- 继续调用 production `/llm-execution-runs`、`/approach-cards`、`/minutes`。
- 可选调用 headless Workbench verifier。
- 删除 session 并写入 manifest。

### Task B: 后端 provenance 不伪装真实麦克风

修改 `asr_stream.handle_stream(...)` 和 `/live/asr/stream/ws/{session_id}`：

- WebSocket 支持 `?audio_source=simulated_realtime_wav`。
- 持久化 live session 时写入 `audio_source`。
- `event_source.input_source` 返回 `simulated_realtime_wav`。
- `simulated_realtime_wav` 可以作为 no-mic lab lane 进入 production derivation endpoints，但证据包必须标注不算真实麦克风。

### Task C: Workbench 来源标签修正

修改 `workbench.js` / `workbench.html`：

- `simulated_realtime_wav` 显示为 `模拟实时`。
- 不因为 provider 包含 `sherpa` / `funasr_realtime` 而显示为 `真实麦克风`。

### Task D: TDD 与自测

新增或修改测试：

- `test_simulated_realtime_lane_bundle_streams_wav_to_ws_and_writes_traceable_bundle`
- `test_workbench_labels_simulated_realtime_wav_without_claiming_real_mic`
- 后端 provenance focused test：WS query `audio_source=simulated_realtime_wav` 后 session event_source 是该来源。

运行：

```bash
PYTHONPATH=code/web_mvp/backend:code/core LLM_GATEWAY_BASE_URL= LLM_GATEWAY_API_KEY= pytest -q tests/test_mainline_evidence_bundle_runner.py code/web_mvp/backend/tests/test_asr_stream.py code/web_mvp/backend/tests/test_workbench.py
```

真实 no-mic 自测：

```bash
PYTHONPATH=code/web_mvp/backend:code/core \
python tools/mainline_evidence_bundle_runner.py \
  --lane simulated-realtime \
  --audio code/asr_runtime/outputs/simulated-release-review.16k.wav \
  --run-id p0-simulated-realtime-20260708
```

## 5. 交付物

- `artifacts/tmp/acceptance/<run-id>/manifest.json`
- `artifacts/tmp/acceptance/<run-id>/go_no_go.md`
- `session_events.json`
- `llm_runs.json`
- `suggestion_cards.json`
- `approach_cards.json`
- `minutes.md`
- `minutes.json`
- `delete_response.json`
- 可选 `ui_verification.json` 和截图

## 6. 当前下一步

按 TDD 执行 Task A/B/C/D。完成后更新：

- `docs/decision-log.md`
- `docs/current-mainline-index.md`
- `docs/current-status-and-p0-execution-plan-2026-07-08.md`
