# P0 No-Mic Simulated Realtime Self-Test Report - 2026-07-08

> 状态：Go for no-mic simulated realtime lane
> 结论边界：这证明实时 WebSocket ASR 协议和后续 Copilot 业务链路闭合；不证明真实麦克风采集可用。
> 真实麦克风状态：仍为 `No-Go / deferred until user can cooperate`。

## 1. 执行命令

```bash
PYTHONPATH=code/web_mvp/backend:code/core \
python3 tools/mainline_evidence_bundle_runner.py \
  --lane simulated-realtime \
  --audio code/asr_runtime/outputs/simulated-release-review.16k.wav \
  --run-id p0-simulated-realtime-20260708-01
```

## 2. Go/No-Go 结果

证据目录：

```text
artifacts/tmp/acceptance/p0-simulated-realtime-20260708-01/
```

manifest 摘要：

```text
verdict=go
audio_source=simulated_realtime_wav
counts_as_real_mic_go_evidence=false
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
llm_provider=real_gateway
ui_coverage=headless_chrome
transcript_char_count=68
final_segment_count=1
suggestion_card_count=3
approach_card_count=3
minutes_char_count=407
all_cards_have_evidence=true
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
delete_verified=true
degradation_reasons=[]
```

Go/No-Go 文件：

```text
artifacts/tmp/acceptance/p0-simulated-realtime-20260708-01/go_no_go.md
```

## 3. 实际链路

本次跑通的是：

```text
simulated-release-review.16k.wav
  -> pcm_chunks_from_wav()
  -> /live/asr/stream/ws/p0-simulated-realtime-20260708-01?audio_source=simulated_realtime_wav
  -> sherpa_onnx_realtime
  -> ASR live session
  -> production /llm-execution-runs
  -> production /approach-cards
  -> production /minutes
  -> headless Workbench same-session verification
  -> DELETE /live/asr/sessions/{session_id}
  -> 404 delete verification
```

session provenance：

```text
event_source.input_source=simulated_realtime_wav
event_source.acceptance_eligible=true
event_source.acceptance_blockers=[]
is_mock=false
asr_fallback_used=false
```

ASR final：

```text
我们这次灰度百分之十如果错误率超过百分之零点一就回滚这里还没有确认回滚负责人张三下周三补充兼容性测试用例监控指标还需要确认P99和错误率
```

## 4. Evidence Files

```text
approach_cards.json
delete_response.json
input_audio.sha256
llm_runs.json
manifest.json
minutes.json
minutes.md
session_events.json
sessions_list_after_delete.json
sessions_list_before_delete.json
suggestion_cards.json
transcript.normalized.txt
ui_verification.json
ui_verification.stderr.log
ui_verification.stdout.log
workbench-after.png
ws_events.json
```

## 5. 测试回归

Focused TDD：

```text
tests/test_mainline_evidence_bundle_runner.py::test_simulated_realtime_lane_bundle_streams_wav_to_ws_and_writes_traceable_bundle
code/web_mvp/backend/tests/test_asr_stream.py::test_asr_stream_simulated_realtime_wav_source_is_persisted_without_claiming_real_mic
code/web_mvp/backend/tests/test_workbench.py::test_workbench_labels_simulated_realtime_wav_without_claiming_real_mic
= 3 passed
```

相关全量：

```text
tests/test_mainline_evidence_bundle_runner.py
code/web_mvp/backend/tests/test_app.py
code/web_mvp/backend/tests/test_asr_stream.py
code/web_mvp/backend/tests/test_workbench.py
code/web_mvp/backend/tests/test_file_convert.py
code/web_mvp/backend/tests/test_real_asr_to_cards.py
code/web_mvp/backend/tests/test_approach_cards.py
code/web_mvp/backend/tests/test_minutes.py
code/web_mvp/backend/tests/test_llm_service.py
code/web_mvp/backend/tests/test_real_llm_path.py
code/web_mvp/backend/tests/test_metrics.py
code/web_mvp/backend/tests/test_g3_g4_g5.py
code/web_mvp/backend/tests/test_e2e_mainline.py
code/web_mvp/backend/tests/test_shadow_trial.py
= 180 passed, 2 warnings
```

Browser demo smoke：

```text
node code/web_mvp/e2e/workbench_smoke.mjs
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

## 6. 当前产品判断

已经解除的疑问：

- 非麦克风输入卡住时，实时 WebSocket ASR 协议本身可以跑通。
- ASR live session 能进入同一套正式建议、方案、纪要、历史、删除和 evidence bundle。
- Workbench 能恢复并展示同一 session 的文字、建议卡、方案卡和纪要。

仍未解除的疑问：

- 浏览器/系统真实麦克风输入仍未证明有效。
- 真实会议中低延迟自动建议还需要产品化调度，不应只依赖“结束后整理会议”。
- 小视口下 Workbench transcript 区域仍偏窄，截图中中文换行过密，需要后续 UI 质量修复。
- LLM 调用次数偏多：ASR 修正、3 次正式建议、1 次方案、1 次纪要。后续应批量生成建议卡并减少费用。

## 7. 下一步

优先级建议：

1. 把 no-mic simulated realtime lane 保留为日常回归，防止主业务链路倒退。
2. 优化 Workbench 主界面小视口布局和术语复杂度。
3. 增加会中准实时建议调度：ASR final 后低频自动触发或明确降级。
4. 用户方便后，再跑真实麦克风 Gate A/B/C。
