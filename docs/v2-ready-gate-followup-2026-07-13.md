# V2 Ready Gate Follow-up

> 日期：2026-07-13
> 范围：FunASR ready gate、Workbench 浏览器等待状态、ASR 超时失败路径、录音期正式 AI 建议门禁。
> 成本边界：本轮没有调用用户远程 LLM gateway，没有启用远程 ASR。

## 结论

本轮完成了模型就绪协议的最小闭环，并把录音期正式建议的验证条件跑到了可解释的结果：

Correction scope status（必须分开解读）：

- `natural_runtime_correction=No-Go`：自然/受控 FunASR 运行时出现漂移时，safe mapping 拒绝修正，页面不伪造“AI 已校正”。
- `deterministic_fixture_correction=Go`：DEC-346 的独立 fixture 已通过真实 backend correction API、持久化 revision、canonical target/source、原始 ASR disclosure 和 evidence clickback。
- `production_remote_correction=Not run`：本轮没有调用远程 gateway；任何已暴露旧 key 均不得复用。

- 本地 FunASR worker 先输出独立 `ready` 事件；后端在收到 ready 前不读取浏览器音频帧。
- 后端 WebSocket 在真实 provider 上发送 `asr_starting -> asr_ready`，ready 超时发送结构化 `provider_error(asr_ready_timeout)` 并关闭连接。
- Workbench 在收到 `asr_ready` 前只把 PCM 帧放入有界队列；ready 后按顺序 flush，再继续发送新帧。
- 连接建立不再等同于模型可用；超时不会被前端当成普通断线自动重连。
- 默认 Chrome sandbox 下的 fake 音频文件和此前可见 Chrome 系统麦克风复测曾得到 `rms=0`、`peak=0`，这些运行仍是输入层 No-Go；录音文件仍能保存并通过 SHA 校验，说明失败发生在采样层而不是导出层。
- 使用仅用于诊断的 Chrome `--no-sandbox` 加非静音 WAV 后，本地 FunASR、连续文字、final、录音导出、no-cost 建议/方案/纪要可以闭环；该开关不作为生产运行参数。
- 使用“语音 A + 静音间隔 + 语音 B + 静音间隔”的多段受控音频后，会议尚未结束时产生了 5 个 final；本机 fake OpenAI-compatible gateway 在录音期间生成并展示 1 张正式建议卡。这个结果证明调度链路可用，但不等同于真实远程 gateway 或自然多人会议通过。

## 实现与协议

### Backend

`code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`：

- `FunasrSidecarRecognizer.wait_ready()` 作为当前 FunASR 的 readiness contract。
- `ASR_READY_TIMEOUT_S=30`，等待在 worker thread 中执行，不阻塞事件循环。
- 成功事件包含 `provider`、`ready=true` 和 `ready_latency_ms`。
- 超时事件包含 `error_code=asr_ready_timeout`、provider metadata 和 `asr_ready_timeout` degradation reason。
- 没有 `wait_ready()` 的正式兼容 provider 使用隐式 ready；fake/test recognizer 保留旧测试事件序列。

### Frontend

`code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`：

- `_micAsrReady` 将 socket open、ASR ready、audio capture 三个状态分开。
- `queueOrSendMicFrame()` 和 `flushQueuedMicFrames()` 在未 ready 时不会发送 PCM。
- `asr_starting` 显示“正在准备实时识别”；`asr_ready` 后才 flush 队列。
- 停止会议发生在 ready 之前时，先停止采集，ready 后补发有界队列并发送 `END`。
- `asr_ready_timeout` 进入人工可见失败路径并停止重连。

## 证据

### 1. 真实 8767 FunASR handshake

服务：`http://127.0.0.1:8767`，本地 Paraformer 模型，session=`probe_asr_ready_20260713`。

```text
event_types=asr_starting,asr_ready,partial
provider=funasr_realtime
ready=true
ready_latency_ms=5278.7
provider_mode=real
is_mock=false
asr_fallback_used=false
remote_asr_called=false
```

该 probe 发送了静音 PCM，故没有把它当作非空中文 final 或产品验收；它只证明服务端 ready gate 和真实 worker 启动路径已生效。对应 session 的录音保存为 `saved=true`，但 transcript 为空并被 `asr_no_final` 正确阻断。

### 2. 浏览器输入复测

以下三次均使用 `no_cost_deterministic`，没有产生付费 LLM 调用，且保留为输入层失败证据：

| Artifact | 输入 | 结果 |
| --- | --- | --- |
| `artifacts/tmp/browser_live_mic/v2-ready-gate-no-cost-20260713` | Chrome headless fake audio，原始 WAV | `blocked_audio_too_quiet`, RMS/peak=0 |
| `artifacts/tmp/browser_live_mic/v2-ready-gate-no-cost-tts-20260713` | Chrome headless fake audio，16k TTS WAV | `blocked_audio_too_quiet`, RMS/peak=0 |
| `artifacts/tmp/browser_live_mic/v2-ready-gate-real-mic-no-cost-20260713` | 可见 Chrome 系统麦克风 | `blocked_audio_too_quiet`, RMS/peak=0 |

补充的受控主流程证据：

| Artifact | 输入与运行参数 | 结果 |
| --- | --- | --- |
| `artifacts/tmp/browser_live_mic/v2-regression-mainline-20260713` | 非静音 WAV；Chrome `--no-sandbox`；`no_cost_deterministic` | `passed_no_cost_mainline`，1 final，实时文字可见，录音 SHA 匹配，console/network error=0 |
| `artifacts/tmp/browser_live_mic/v2-local-gateway-two-turn-20260713` | 两段中文受控语音+静音；Chrome `--no-sandbox`；本机 fake OpenAI-compatible gateway | 5 finals，录音期正式建议 `passed_realtime_ai_suggestion_visible`，建议首显约 `15028ms` |

前三次都满足：

```text
browser_console_error_count=0
network_error_count=0
audio_export_http_status=200
audio_sha256_matches_session=true
remote_asr_called=false
llm_called=false
```

前三次都不满足：

```text
non_empty_final_count > 0
realtime_text_visible_during_recording
formal_cards_generated
```

所以本轮没有放宽验收门禁，也没有把静音录音标成真实会议成功。新增受控多段证据只证明“final 到正式建议”的业务链路在会议进行中能工作；它没有清除真实自然麦克风、真实远程 gateway、中文技术准确率或生产 Chrome sandbox 的 blocker。

### 3. 录音期正式建议门禁

`production_enabled` 的录音期建议门禁必须看到：

- 录音阶段、同一 session 的 UI sample；
- 初始建议数为 0，随后出现至少 1 张有证据的正式建议卡；
- 不把 `partial_hint_event` 本地提醒当成正式 AI 建议；
- 建议必须由已持久化 final 触发，不能以不稳定 partial 直接付费调用。

多段受控证据的核心数据：

```text
session_id=rec_mrijxquv
provider=funasr_realtime
provider_mode=real
is_mock=false
asr_final_count=5
health_status=audio_capture_health_passed
first_text_after_audio_active_latency_ms=4128
first_final_after_audio_active_latency_ms=12298
realtime_ai_suggestion_status=passed_realtime_ai_suggestion_visible
max_recording_ai_suggestions=1
first_ai_suggestion_visible_latency_ms=15028
suggestion_card_count=1
approach_card_count=1
minutes_char_count=213
audio_sha256_matches_session=true
remote_asr_called=false
counts_as_production_llm_evidence=false
```

该运行使用的是本机 fake gateway，`counts_as_production_llm_evidence=false` 是正确结果；它不能替代一次明确授权的真实远程 provider 验收。

## TDD 与验证

RED 先复现：

- 后端未调用 `wait_ready()`，ready gate 测试失败。
- 前端 socket open 直接 flush，浏览器契约测试失败。
- ready timeout 未停止重连，失败路径测试失败。

GREEN：

```text
backend test_asr_stream.py + test_funasr_sidecar.py: 56 passed, 2 warnings
focused Workbench readiness tests: 3 passed, 2 warnings
full backend regression: 641 passed, 2 warnings
root regression: 342 passed, 2 warnings
ASR runtime regression: 89 passed, 1 warning
all-buttons browser smoke: go_workbench_all_buttons_smoke, 25 screenshots
node --check workbench.js and browser verifier: passed
python3 -m py_compile asr_stream.py: passed
git diff --check: passed
real 8767 FunASR handshake: passed (ready + partial)
browser mainline with zero-input audio: failed honestly at audio health gate
```

## 当前边界与下一步

- 已通过：本地 FunASR 显式 ready、后端 readiness gate、前端等待/flush、ready timeout fail-closed。
- 已通过：本地 FunASR 显式 ready、后端 readiness gate、前端等待/flush、ready timeout fail-closed；非静音受控输入的实时文字、final、录音保存/导出、会后复盘；多段输入的录音期正式建议（本机 fake gateway）。
- 未通过：生产 Chrome sandbox 下的 fake-file 输入、自然真实多人麦克风会议、真实远程 gateway 的录音期正式建议、中文技术语义质量、真实 wall-clock 长会、Mac/Windows 发布验收。
- 单段连续音频没有中途 final 时，录音期正式建议门禁失败是符合设计的；验收音频必须包含至少两个可识别的发言段/停顿，不能用“整段结束才 final”的输入证明实时建议。
- 下一开发主线是：在用户明确授权和成本确认后执行一次真实远程 gateway 录音期建议验收；其余时间使用本机 fake gateway/no-cost lane 回归，不重复消耗额度。

## 本轮补充：canonical evidence 与修正验证口径

### Evidence clickback 修复

`workbench_revision_evidence_clickback.mjs` 原本直接等待普通 `history-list` 出现 fixture。由于默认历史接口会隐藏 `simulated_realtime_wav`，这不是 SQLite migration 丢数据，而是 E2E 没有显式进入 demo history。脚本现在：

- 使用 `?demo=1&verify=revision-evidence-clickback`；
- 通过当前 Workbench `history-modal-item` 的 `data-action="open"` 打开会话；
- 以 canonical transcript 契约验证修正段，而不是期待旧式两条可见 revision utterance。

同时修复 Workbench evidence 定位：

- canonical span 保留 `data-segment-id` 作为修正目标；
- canonical span 新增 `data-source-segment-id` 作为修正来源；
- evidence clickback 同时按目标 ID、来源 ID和 evidence ID查找；
- 点击原始目标时自动展开同段的“查看原始识别”。

浏览器证据：

```text
artifact=artifacts/tmp/ui_screenshots/workbench-revision-evidence-clickback-20260713-final
status=go_revision_evidence_clickback
screenshot_count=5
original_evidence_clickback=passed
revision_evidence_clickback=passed
revision_relationship_visible=passed
original_asr_disclosure=passed
remote_asr_called=false
remote_llm_called=false
```

### 修正 verifier 四态

`workbench_browser_live_mic_compaction.mjs` 把 compaction 判定提取为纯函数，并从 session 的 `realtime_transcript_correction` 状态读取修正是否真的尝试过：

```text
correction_disabled_by_setting
no_revision_needed
passed_compacted_realtime_correction_visible
failed_realtime_correction_not_visible
```

`failed_duplicate_active_segment_rows` 仍是独立硬失败。`correction_rejected`、`combined_rejected_segment_ids` 或 provider failure 在没有可见 revision 时仍失败；修正关闭或 provider 明确返回无需修正则不失败，但报告会保留 `correction_observed=false`。

纯函数回归覆盖四态。受控双段音频运行中，L2 设置为 true，fake gateway 被真实调用，但 FunASR 输出与 fixture 映射不一致，safe-correction 规则拒绝了 1 个 segment：

```text
artifact=artifacts/tmp/browser_live_mic/v2-local-gateway-two-turn-correction-enabled-current-20260713-retry
health_status=audio_capture_health_passed
asr_final_count=5
partial_visible_count=17
realtime_ai_suggestion_status=passed_realtime_ai_suggestion_visible
realtime_transcript_compaction_status=failed_realtime_correction_not_visible
correction_enabled=true
correction_status=no_revision_needed
correction_attempted=true
rejected_segment_count=1
max_corrected_transcript_row_count=0
counts_as_production_llm_evidence=false
```

这个失败是预期的安全边界证据，不是修正成功；后续应使用独立 deterministic correction fixture 验证 `先恢度 -> 先灰度` / `P 九九 -> P99`，不要继续依赖 FunASR 运行时恰好输出某个错误词。

### 本轮真实结果边界

- 通过：本地 FunASR ready、受控双段中文音频 partial/final、录音保存和 SHA、录音期正式建议、会后方案/纪要、Workbench 同 session、canonical evidence clickback。
- 未通过：真实远程 LLM 证据、自然多人真实麦克风、生产 Chrome sandbox fake-file 输入、中文技术术语质量、真实 wall-clock 长会、Mac/Windows release。
- 本轮没有读取或写入用户提供的远程 key，也没有调用远程 ASR；之前在对话中明文出现的 key 仍应在真正验收前轮换。

## Fresh regression closeout

all-buttons smoke 在 canonical namespace collision probe 中发现了一个补充段来源 ID回归：无目标 `revision-supplement:*` 的 DOM `data-segment-id` 不应使用 projection namespace。修复后 fresh 浏览器回归重新通过：

```text
status=go_workbench_all_buttons_smoke
screenshot_count=25
revision_supplement_probe=passed
transcript_namespace_collision_probe=passed
reload_recovery_probe=passed
transcript_scroll_follow_probe=passed
mobile_horizontal_overflow=false
runtime_exceptions=[]
error_console=[]
network_loading_failed=[]
http_5xx=[]
```

代码回归：

```text
backend=642 passed, 2 warnings
root tests=347 passed, 2 warnings
asr runtime=89 passed, 1 warning
node --check=passed
git diff --check=passed
```

这次补充修复只涉及 canonical DOM 追踪字段，未改变 ASR、LLM、录音保存或历史过滤边界。

## 本轮后续：DEC-345 correction verifier 严格化与主链路 retry

本轮没有继续扩大边界评测，而是修复 verifier 的证据放行漏洞。录音期 UI sample 现在同时采集：

```text
corrected_transcript_segment_ids
corrected_transcript_source_segment_ids
```

canonical corrected segment 的目标 ID 来自 data-segment-id，来源 ID 来自 data-source-segment-id。报告只在 backend revised_segment_ids 与 UI 目标 ID 相交时输出：

```text
passed_compacted_realtime_correction_visible
```

以下情况全部 fail-closed：后台有 revision 但 UI 不可见、UI 有孤立/残留 correction、completed 无 revision、partially_completed、mapping_rejected、provider failure、degraded。关闭设置输出 correction_disabled_by_setting；backend 明确且没有任何孤立证据的 no_revision_needed 才不失败。

验证结果：

```text
focused verifier=5 passed
backend=642 passed
root=349 passed
asr runtime=89 passed
node --check=passed
git diff --check=passed
```

16 秒受控 retry：

```text
artifact=code/web_mvp/backend/artifacts/tmp/browser_live_mic/v2-correction-verifier-retry2-20260713
health_status=audio_capture_health_passed
asr_final_count=3
partial_visible_count=8
first_text_after_audio_active_latency_ms=4364
first_final_after_audio_active_latency_ms=12304
realtime_ai_suggestion_status=passed_realtime_ai_suggestion_visible
first_ai_suggestion_visible_latency_ms=16110
live_reminder_drift_status=passed
audio_sha256_matches_session=true
browser_console_error_count=0
network_error_count=0
realtime_transcript_compaction_status=failed_realtime_correction_not_visible
classification_reason=correction_rejected
rejected_segment_count=1
counts_as_production_llm_evidence=false
```

该失败是预期的安全边界：FunASR 输出漂移与 fake fixture 的修正映射不一致，safe correction 拒绝一个 segment；页面没有显示伪造的“AI 已校正”。8 秒 retry 只得到 1 个 final，录音期正式卡未出现，证明实时建议验收必须覆盖足够的 final 调度窗口。

当前主线结论不变：受控本地 FunASR + 本机 fake gateway 的 partial/final、录音保存、录音期正式建议、会后方案/纪要和浏览器证据已闭环；L2 成功修正可见证据、真实远程 gateway、自然多人中文麦克风、中文技术语义质量、真实 wall-clock 长会和 Mac/Windows 发布仍未通过。临时验证服务已关闭，8767 主服务保持运行。

## DEC-346 deterministic correction E2E 结果

本轮新增的 deterministic correction runner 没有预置 transcript_revision。它先读取 backend events 确认 0 个 revision，再调用 realtime-corrections/run-once；本机 fake OpenAI-compatible gateway 返回只改术语的文本，backend safe correction 接受后持久化 1 个 revision。

最终 artifact：

code/web_mvp/backend/artifacts/tmp/ui_screenshots/workbench-deterministic-correction-20260713-retry5/deterministic_correction_report.json

核心结果：

status=go_deterministic_correction_e2e
provider=funasr_realtime / provider_mode=real / is_mock=false
backend correction status=completed
revision_count=1
revised_segment_ids=[det_corr_seg_1]
canonical target=det_corr_seg_1
canonical source=det_corr_seg_1:rtc-v1
original_asr_disclosure=true
original_evidence_clickback=true
clickback_focus_count=1
screenshots=2
counts_as_production_llm_evidence=false
remote_asr_called=false

该 fixture 预置一张带原始 evidence 的建议卡，只用于验证已有 evidence-card 到 canonical transcript 的回跳关系；修正结果没有预置，修正 response、revision event、revision status 和页面状态全部由本次 backend 调用产生。

中间失败 artifact 保留：第一次是 runner CSS selector 拼接错误，第二次是 source ID 语义错误，第三次是关闭 details 时读取 innerText，第四次是 fixture 没有 evidence 卡。它们没有被覆盖为成功，最终 retry5 才纳入 Go evidence。

回归：deterministic fixture test=2 passed；backend=642 passed；root=351 passed；ASR runtime=89 passed；Node syntax、git diff check 和 8767 health 均通过。

当前边界更新为：L2 correction 的本地 backend 到 canonical UI 实现已通过 deterministic evidence；真实远程 gateway、真实中文多人麦克风、中文技术语义质量、真实 wall-clock 长会和 Mac/Windows 发布仍未通过。
