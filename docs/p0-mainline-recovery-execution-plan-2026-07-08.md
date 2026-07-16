# Meeting Copilot P0 主线恢复执行计划 - 2026-07-08

> 状态：执行基线
> 目标：把项目从“Local Web Demo / 评测与边界文档堆叠”拉回最初产品主线：**中文技术会议实时 Copilot**。
> 本文是 2026-07-08 之后的 P0 执行入口。后续如果计划变化，必须更新本文或在 `docs/decision-log.md` 追加决策。

---

## 1. 当前结论

当前项目不是 0，也不能宣布 Production MVP。

| 链路 | 结论 | 可声明范围 |
|---|---:|---|
| 文件导入主链路 | Go | `uploaded_wav -> local_funasr_batch -> real_gateway -> cards/minutes -> Workbench -> delete` |
| Workbench demo smoke | Go for demo only | 只证明 mock ASR / fake LLM 的 UI 操作链 |
| 真实麦克风命令行采集 | No-Go | 录到近静音，ASR final = 0 |
| 真实麦克风完整产品链路 | No-Go / 未证明 | 还没有 `real mic -> ASR -> LLM -> UI -> delete` 证据包 |
| 整体 P0 | Partial / No-Go | 文件导入已通，实时会议主链路未通 |

可信 Go 证据包：

```text
artifacts/tmp/acceptance/p0-file-lane-20260708-ui/
artifacts/tmp/acceptance/p0-file-lane-20260708-after-p0fix/
```

关键指标：

```text
verdict=go
audio_source=uploaded_wav
asr_provider=local_funasr_batch
asr_provider_mode=real
llm_provider=real_gateway
llm_called=true
ui_coverage=headless_chrome
session_id=file_5216710cce83
transcript_char_count=93
final_segment_count=1
suggestion_card_count=3
approach_card_count=3
minutes_char_count=401
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
delete_verified=true
degradation_reasons=[]
```

补丁后复跑的 Go 证据：

```text
run_id=p0-file-lane-20260708-after-p0fix
verdict=go
audio_source=uploaded_wav
asr_provider=local_funasr_batch
asr_provider_mode=real
asr_fallback_used=false
llm_provider=real_gateway
ui_coverage=headless_chrome
transcript_char_count=97
final_segment_count=1
suggestion_card_count=3
approach_card_count=3
minutes_char_count=425
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
delete_verified=true
degradation_reasons=[]
```

真实麦克风 No-Go 证据：

```text
artifacts/tmp/real_mic_shadow_tests/p0_real_mic_20260708/full_chain_summary.json
```

关键指标：

```text
runner_status=blocked_by_local_asr
capture_status=recorded_from_real_microphone
audio_duration_seconds=11.906
mean_volume=-91.0 dB
max_volume=-91.0 dB
asr.text=""
event_counts.final=0
web_handoff.candidate_count=0
```

判断：真实麦克风 No-Go 当前首先是**采集层近静音**，不是下游 LLM、卡片、Workbench 或删除逻辑失败。

2026-07-08 追加 P0 修复结论：

```text
真实麦克风或真实 ASR 如果只返回空 final，后端必须创建可追溯 degraded session。
该 session 的 final_count=0、passes_minimum_gate=false、degradation_reasons 包含 asr_final_empty。
这不是 Go 证据，只是让 UI、历史和 evidence bundle 能解释 No-Go 原因，不再以 /events 404 断链。
```

---

## 2. 产品主线边界

P0 必须围绕这一条链路恢复：

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

P0 不再扩展：

- Tauri / Mac / Windows 安装包。
- iOS / Android。
- 系统音频采集。
- 多人 speaker diarization。
- Jira / Linear / GitHub 自动建单。
- 云端账号、同步、团队权限。
- 新增默认付费 ASR provider。
- 继续新增 readiness / preflight / approval wrapper。

这些不是放弃，而是等 P0 主链路可重复通过后再排期。

---

## 3. 必须修复的 P0 问题

### P0-1：真实 WS 入口不能静默 fallback 成 Fake 还标 real

现状：

- `asr_stream.get_recognizer()` 顺序是 sherpa -> FunASR -> Fake。
- 如果 sidecar 不可用，真实 WebSocket 入口可能返回 Fake transcript。
- 持久化 record 默认 provider 仍可能是 `local_real_asr`。
- session summary 只按 provider 是否为 `local_mock_asr/fake` 判断 mock。

风险：

```text
真实麦克风 smoke 可能产生 fake transcript，却被历史和证据包当成 real。
```

P0 修复：

- recognizer 必须暴露 metadata：`provider`、`provider_mode`、`is_mock`、`fallback_used`、`degradation_reason`。
- `handle_stream()` 持久化时写入这些 metadata。
- 真实会议验收中如果 `fallback_used=true` 或 `is_mock=true`，evidence bundle 必须 No-Go。
- UI 历史列表必须显示 `演示/真实/降级`，不能只显示“麦克风”。

验收：

- sidecar 不可用且走 Fake 时，summary `provider_mode=mock` 或 `fallback_used=true`。
- evidence bundle 不会把 Fake fallback 判为 Go。

### P0-2：Workbench 主流程仍是“按钮实验台”，不是会议产品

现状：

- 顶部仍有 9 个按钮：开始会议、导入录音、生成会后复盘、生成会议建议、分析方案利弊、试用示例、历史记录、刷新实时文字、删除本次会议。
- 建议卡和方案卡追加到 transcript 底部，用户不容易看见。
- 历史仍混放演示/导入录音/麦克风。
- 开始会议后如果没有 ASR 事件，主内容容易空白或只显示临时状态。

P0 修复：

- 保留主动作：`开始/结束会议`、`导入录音`、`生成会议建议`、`生成会后复盘`、`历史`、`删除`。
- `试用示例` 保留但明确标记为演示，不参与真实 Go。
- 录音中禁用导入、演示、删除、生成类按钮。
- 页面稳定容器至少分为：实时文字、实时建议、方案分析、会后复盘。
- 建议生成后进入“实时建议”区域，不再只追加到转写底部。
- 麦克风录音期间显示“正在听/等待语音/正在识别”，不能空白。

验收：

- 用户不用读文档能完成：开始会议或导入录音 -> 看到文字 -> 生成建议 -> 生成复盘 -> 历史打开 -> 删除。
- demo session 在 UI、历史、证据包里都显示 `演示`。
- 真实 Go 证据不能依赖 `/live/asr/mock/sessions`。

### P0-3：真实麦克风必须先做有效采集门槛

现状：

- `capture_status=recorded_from_real_microphone` 只说明写出了 WAV。
- 最新音频近静音：`mean_volume/max_volume=-91.0 dB`。
- ASR final=0。

P0 修复：

- 先枚举 avfoundation 设备，记录 device index。
- 每个麦克风样本必须计算 volume。
- 采集 Go 条件：`max_volume > -50 dB`，并且 ASR final >= 1。
- 命令行 mic 如果继续静音，优先走产品入口 `browser getUserMedia`。
- Workbench 增加麦克风电平预检或至少“没有检测到声音”的明确状态。

验收：

- 真实麦克风样本不是近静音。
- 同一 session 产生 ASR final。
- 之后再接 LLM cards/minutes/UI/delete bundle。

### P0-4：证据包必须区分 lane，不再混称全链路

现状：

- `p0-file-lane-20260708-ui` 是可信 file lane Go。
- `real_mic_full_chain_runner.py` 当前边界是 `real_mic_local_asr_web_handoff_no_remote_asr_no_llm`，不能称完整产品全链路。

P0 修复：

- evidence bundle manifest 必须包含：
  - `audio_source`
  - `input_audio_path_kind`
  - `asr_provider`
  - `asr_provider_mode`
  - `asr_fallback_used`
  - `llm_provider`
  - `llm_called`
  - `ui_coverage`
  - `session_id`
  - `delete_verified`
  - `verdict`
  - `degradation_reasons`
- `audio_source=real_mic` 的 bundle 必须覆盖 LLM 和 UI，不能只停在 web handoff。

### P0-5：空 ASR 结果必须可追溯，但不能污染 Go 证据

现状：

- 真实浏览器开麦曾出现 WebSocket chunk 持续进入后端，但 ASR sidecar 最终返回空 final。
- 旧逻辑只在 `accumulated_finals` 非空时落库。
- 当 final text 为空时不创建 live ASR session，导致 `/live/asr/sessions/{sid}/events` 返回 404。
- 用户看到的是“文字没了/没有历史/不知道失败在哪一层”。

P0 修复：

- `handle_stream()` 在 END 后无论是否有非空 final，都必须创建 live ASR session record。
- 非空 final 走正常 transcript pipeline。
- 空 final 不得创建 `transcript_final`，只能创建 `evaluation_summary`。
- record-level `degradation_reasons` 必须包含 `asr_final_empty` 或 `asr_no_final`。
- summary 必须保持 `final_count=0`。
- `evaluation_summary.payload.passes_minimum_gate=false`。

验收：

- 空 final session 的 `/events` 返回 200，不再 404。
- UI 能显示“没有识别到有效语音/麦克风无输入”。
- history 和 evidence bundle 可以看到 provider、provider_mode、fallback、degradation。
- Go/No-Go 判定不得因为存在 degraded session 而变 Go。

---

## 4. 执行 Checklist

### Phase A：冻结主线事实

- [x] 明确 file lane Go：`p0-file-lane-20260708-ui`。
- [x] 补丁后复跑 file lane Go：`p0-file-lane-20260708-after-p0fix`。
- [x] 明确 real mic lane No-Go：近静音，final=0。
- [x] 明确整体 P0 是 Partial / No-Go。
- [x] 更新 `docs/decision-log.md` 写入 DEC-219。
- [x] 更新 `docs/current-mainline-index.md`，把 2026-07-08 P0 recovery 设为当前入口。
- [ ] 更新 `docs/mainline-p0-recovery-selftest-report-2026-07-08.md`，标记 `_asr_live_event_source_metadata` 已修复，不再把旧 bug 当当前阻塞。

### Phase B：后端真实/Mock 边界修复

- [x] 写失败测试：sidecar 不可用时 WS 不能被标成 real。
- [x] 给 recognizer 增加 metadata。
- [x] `handle_stream()` 持久化 `provider/is_mock/provider_mode/fallback_used/degradation_reasons`。
- [x] session summary/events 返回上述字段。
- [x] 更新 evidence bundle Go/No-Go：`asr_fallback_used=true` 必须 No-Go。
- [x] 写失败测试：真实 ASR 空 final 也必须落库为 degraded session，不再让 `/events` 404。
- [x] `handle_stream()` 空 final 落 `evaluation_summary`，并标记 `asr_final_empty`，保持 `final_count=0`。

验证命令：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_workbench.py
```

### Phase C：Workbench 主流程可用性修复

- [x] 写回归测试：开麦后不裸空、raw `final` 快照可渲染、空快照不覆盖已有实时文字、导入使用完整快照渲染。
- [ ] 写/扩展 E2E：初始按钮状态、demo 渲染、录音中禁用冲突按钮、删除后状态清空。
- [ ] 页面新增稳定容器：实时文字、实时建议、方案分析、会后复盘。
- [ ] 建议卡/方案卡渲染到稳定容器。
- [x] 录音中展示“正在听/等待语音”，不再把主内容清成空白。
- [x] 停止录音/刷新时如果服务端快照暂空，保留已有实时文字。
- [x] Workbench 增加麦克风 peak/rms 电平诊断，持续静音时提示检查浏览器权限、macOS 输入设备和输入音量。
- [ ] 历史列表显示来源、字数、建议数、复盘状态、当前选中态。
- [x] 记录当前页面端口与服务端口不一致风险：用户页曾停在 `127.0.0.1:8765`，实际服务监听 `127.0.0.1:8000`。

验证命令：

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

### Phase D：真实麦克风有效采集

- [ ] 枚举 avfoundation 输入设备。
- [ ] 对候选设备录 5-10 秒短样本。
- [ ] 计算 `mean_volume/max_volume`。
- [x] 如果命令行近静音，改用 Workbench `getUserMedia` 路径做产品入口自测。
- [x] 低电平时页面提示“没有检测到麦克风声音/检查麦克风权限或输入设备”，并说明外放声音不一定进入麦克风输入。
- [x] 重启 8765 服务后，用 Workbench 页面再次跑真实麦克风入口，确认新版空 ASR session 可追溯。
- [ ] 修复本机/浏览器麦克风输入静音问题，让 Workbench 显示“检测到麦克风声音”。
- [ ] 在非静音输入下再次跑 real mic lane，要求 ASR final_count >= 1。

Workbench 页面自测结果：

```text
session_id=rec_mrbh7ml8
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
degradation_reasons=["asr_final_empty"]
event_types=["evaluation_summary"]
passes_minimum_gate=false
final_event_count=0
```

结论：产品入口可创建可追溯 No-Go session，但仍不是 real mic lane Go；当前阻塞是麦克风输入持续静音。

采集 Go 条件：

```text
max_volume > -50 dB
ASR final_count >= 1
transcript_char_count > 0
```

### Phase E：完整 P0 bundle

- [ ] Lane 1 file lane 复跑，仍应 Go。
- [ ] Lane 2 real mic lane 跑出独立 bundle。
- [ ] Lane 2 必须覆盖：ASR final、LLM cards、approach cards、minutes、Workbench 同 session、delete。
- [ ] 更新 `docs/mainline-p0-recovery-selftest-report-2026-07-08.md`。

Go 条件：

```text
file lane: Go
real mic lane: Go or explicitly documented No-Go with single blocker
overall P0: only when real mic lane also Go
```

---

## 5. 当前不做的大重构

后端确实需要拆分，但 P0 不做大爆炸重构。

P0 只允许最小拆分：

- recognizer metadata。
- live ASR session metadata。
- evidence runner Go/No-Go。
- Workbench 状态机和渲染容器。

P1 再做：

- `app.py` 拆 router/service。
- file transcribe / LLM / minutes job 化。
- JSON repository version / concurrency。
- 音频保存与 retention。
- 生产级 settings / feature flags / auth / observability。

---

## 6. 生产级发布红线

以下任一成立，禁止宣布 Production MVP：

- ASR provider 为 fake/mock。
- ASR fallback 到 Fake。
- transcript 为空。
- real mic audio 近静音。
- final segment 为 0。
- LLM 未调用却声称生成真实建议。
- card 无 evidence。
- Workbench 没有同 session 截图或 UI 验证。
- delete 未验证。
- demo smoke 被当成真实 Go。

当前 Production MVP 结论仍是：

```text
No-Go
```
