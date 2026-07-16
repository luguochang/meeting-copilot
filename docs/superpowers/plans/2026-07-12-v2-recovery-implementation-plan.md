# Meeting Copilot V2 Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the 2026-07-12 V2 refactor into a persistent, testable and honestly releasable local-first meeting copilot.

**Architecture:** Restore contracts from the data layer outward. SQLite owns durable state, the realtime meeting service owns audio/ASR/canonical state, AI derivations pass explicit gates, and the Workbench consumes stable APIs without patch-script coupling. Every phase ends with behavior tests before the next layer is changed.

**Tech Stack:** Python 3.14, FastAPI, SQLite, vanilla JavaScript, local FunASR/Sherpa, OpenAI-compatible LLM gateway, pytest, Node/Chrome CDP, Tauri.

## Current Execution Status (2026-07-13, refreshed)

The V2 recovery is being verified with package-scoped test entrypoints. The latest
browser evidence is green for the Workbench and the fixture-only long-meeting UI
flow, but these results do not close the production gates below.

- [x] Canonical transcript counting in the long-meeting verifier excludes empty-state
  `.utterance` placeholders after delete.
- [x] The all-buttons verifier uses the same canonical selector for import waits,
  delete assertions, and screenshot state reports.
- [x] Root regression: `python3 -m pytest -q tests` -> `351 passed, 2 warnings`.
- [x] Backend regression: `PYTHONPATH=.:../../core python3 -m pytest -q` from
  `code/web_mvp/backend` -> `642 passed, 2 warnings`.
- [x] All-buttons browser E2E: `go_workbench_all_buttons_smoke`, 25 screenshots,
  no runtime exceptions, console errors, loading failures, or HTTP 5xx responses.
- [x] Long-meeting Workbench fixture E2E: `go_long_meeting_ui_evidence`, 12
  canonical segments, 1 correction, 4 suggestion cards, 2 approach cards,
  minutes, evidence clickback, exports, and delete reset.
- [x] Deterministic 20-minute synthetic soak: `artifacts/tmp/soak/v2-recovery-synthetic-20260713/soak_report.json`, 600 chunks, RTF `0.1`, RSS growth `12 MB`, no remote ASR/LLM.
- [x] Existing public Chinese ASR baseline was rechecked from
  `artifacts/tmp/asr_reports/public_chinese_asr_baseline_20260710.json`; it remains
  `needs_asr_optimization_before_release`, so no additional public audio was downloaded.
- [x] Existing real-microphone recording export SHA was independently rehashed:
  `realtime-focus-speaker-to-real-mic-production-20260711-final`, HTTP `200`,
  `3194924` bytes, computed SHA matched the session SHA.
- [x] Fresh controlled real-microphone no-cost evidence was recorded and classified
  honestly: the 30-second run had audio health pass but no ASR final; the 60-second
  run was `blocked_audio_too_quiet`; both saved audio and matched export SHA, with
  zero browser runtime/console/network errors.
- [x] Real-microphone verifier report counts now use canonical transcript rows; the
  historical No-Go JSON was not rewritten.
- [x] Local FunASR streaming worker uses an explicit `ready` event and an existing
  local Paraformer model directory; direct worker evidence produced 11 non-empty
  partials and 1 final without model download.
- [x] Controlled Chrome browser microphone evidence: `browser_live_mic` captured
  non-empty real FunASR final, saved audio with matching SHA, and passed the local
  audio health gate. This is controlled speaker/TTS evidence, not natural
  multi-speaker meeting acceptance.
- [x] Same-segment stable-partial -> final candidate upgrade: confirmed final now
  produces a `_final` queued candidate, and a local fake OpenAI-compatible gateway
  generated one formal card with usage `170` tokens. The user gateway was not called
  in this verification.
- [x] FunASR readiness gate: backend waits for explicit local worker `ready`, the
  Workbench queues audio until `asr_ready`, and `asr_ready_timeout` stops retrying.
  Real 8767 handshake passed with `asr_starting -> asr_ready -> partial` and
  `ready_latency_ms=5278.7`.
- [x] Deterministic correction fixture: the real backend correction endpoint,
  canonical corrected target/source IDs, original ASR disclosure and evidence
  clickback all passed with two screenshots. The local fake gateway result is
  explicitly excluded from production LLM evidence.
- [ ] One root-level regression command that configures all package paths; bare
  `python3 -m pytest -q` still fails collection because this repository contains
  multiple package-local test namespaces. Use the package-scoped commands above.
- [ ] Real microphone acoustic gate and non-empty non-fake ASR final.
- [ ] Recording-time formal AI suggestion gate in production mode.
- [ ] Real wall-clock 20-minute soak.
- [ ] Mac package production acceptance and Windows real-machine acceptance.

Fresh evidence reports: `docs/v2-funasr-real-mic-evidence-2026-07-13.md` and
`docs/v2-ready-gate-followup-2026-07-13.md`. The ready gate is implemented, but
the fresh browser follow-up captured zero input samples (`rms=0`, `peak=0`) and
therefore remains No-Go for non-empty ASR final. Chinese technical term quality,
model warmup latency, natural multi-speaker audio, real-gateway recording-time AI,
and wall-clock soak remain open.

The fixture long-meeting run is explicitly not a real audio runtime, production
soak, paid LLM run, or real-microphone acceptance. See `DEC-336` in
`docs/decision-log.md` for the decision and evidence paths.

The historical task checkboxes below remain an implementation backlog and are
not rewritten merely because package regressions pass. Only the reconciled
status block above and dated decision-log entries represent current evidence.
Unpassed release gates remain unchecked.

---

### Task 1: Restore The Persistence Contract

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/sqlite_repository.py`
- Create: `code/web_mvp/backend/tests/test_sqlite_repository.py`
- Modify: `code/web_mvp/backend/tests/test_app.py`

- [ ] Add failing tests proving repositories receive a data directory and create exactly one database file.
- [ ] Add a failing two-app restart test covering ASR live sessions and normal sessions.
- [ ] Add a failing idempotent JSON migration test.
- [ ] Make repository constructors and `create_app()` obey one data-directory contract.
- [ ] Stop swallowing migration exceptions; expose actionable startup failure details.
- [ ] Run persistence, app and ASR stream regressions.

### Task 2: Restore Workbench Asset And DOM Integrity

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/settings-panel.js`
- Delete or retire: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench-enhancements.js`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`
- Modify: `tests/test_workbench_productized_ui.py`

- [ ] Add failing tests for unique ids, valid modal placement and valid `/static/` assets.
- [ ] Remove duplicate history markup and move settings markup inside `<body>`.
- [ ] Load optional scripts only after `workbench.js`; remove the private-variable patch script.
- [ ] Add browser coverage proving settings/history controls appear and open once.
- [ ] Run Workbench static and browser smoke tests.

### Task 3: Implement Safe Settings And Usage Accounting

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/sqlite_repository.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/settings-panel.js`
- Create: `code/web_mvp/backend/tests/test_settings.py`

- [ ] Define a strict non-secret settings schema and reject API-key persistence.
- [ ] Persist preferences in SQLite and return them through GET/PATCH settings APIs.
- [ ] Aggregate real recorded token usage by session/day/month.
- [ ] Label currency values as estimated and use configured per-token rates.
- [ ] Enforce session/daily paid-call budgets before formal LLM execution.
- [ ] Verify settings restart, secret rejection, accounting and budget tests.

### Task 4: Enforce Degradation At Business Boundaries

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/degradation_controller.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/auto_suggestion_orchestrator.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Create: `code/web_mvp/backend/tests/test_degradation_controller.py`

- [ ] Add failing tests for each level's ASR, recording, local reminder and paid-LLM behavior.
- [ ] Apply degradation checks at WebSocket start and every paid derivation endpoint.
- [ ] Apply Level 1 suggestion filtering and Level 2 paid-AI blocking.
- [ ] Define the Level 3 recording-only server/client contract before enabling it.
- [ ] Persist degradation events with session evidence.
- [ ] Verify escalation, reset and cross-session isolation behavior.

### Task 5: Make WebSocket Reconnect Audio-Safe

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`
- Modify: `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`
- Modify: `code/web_mvp/backend/tests/test_workbench.py`

- [ ] Add a failing browser probe showing frames are currently lost while disconnected.
- [ ] Add a bounded ordered unsent-frame queue with visible overflow degradation.
- [ ] Flush queued frames after reconnect before new frames.
- [ ] Make same-session reconnect resume persistence without duplicating the meeting.
- [ ] Verify transcript continuity, audio duration and exported SHA after forced disconnect.

### Task 6: Restore Verification And Release Truth

**Files:**
- Modify: `验证测试.py`
- Modify: `启动服务.sh`
- Modify: `PRD对齐检查清单.md`
- Modify: `交付清单.md`
- Modify: `全阶段改进完成报告.md`
- Modify: `项目完成确认.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/decision-log.md`

- [ ] Fix the verification script root path and make skipped/non-200 checks fail honestly.
- [ ] Make production startup configure a persistent data directory and bind locally by default.
- [ ] Restore the complete Python regression to zero failures.
- [ ] Restore all-buttons E2E with screenshots and diagnostics.
- [ ] Run fresh real-microphone, recording-time AI and long-meeting gates.
- [ ] Produce and smoke-test the Mac package.
- [ ] Rewrite completion documents from current evidence and leave unpassed gates unchecked.

## 2026-07-13 Execution Reconciliation

本计划的历史任务清单不能直接作为“全部完成”标志；以下是本轮新鲜证据对主线的校准：

已验证：

- FunASR 显式 `ready`、后端 readiness gate、前端 ready 前有界缓存和 ready timeout fail-closed。
- backend `641 passed, 2 warnings`、root `342 passed, 2 warnings`、ASR runtime `89 passed, 1 warning`。
- Workbench all-buttons `go_workbench_all_buttons_smoke`，25 张截图，浏览器 runtime/console/network/HTTP 5xx 均为 0。
- 非静音受控浏览器输入的实时文字、final、录音保存/导出 SHA、会后建议/方案/纪要。
- 多段受控输入的录音期正式建议：5 个 final，首张正式建议约 15028ms 可见；使用本机 fake gateway，不计入 production LLM evidence。

仍未完成：

- 真实远程 gateway 的录音期正式建议和真实费用/usage 验收；本轮没有读取或调用用户 key。
- 当前 Chrome 默认 sandbox 下 fake-file 输入的有效采样问题；`--no-sandbox` 仅是诊断参数，不得进入生产启动配置。
- 自然多人中文会议的 ASR 术语、数字、断句和口音质量门禁。
- 真实麦克风自然声源、真实 wall-clock 20 分钟 soak、Mac 公测签名/公证/Gatekeeper、Windows 真机。
- 根目录裸 `python3 -m pytest -q` 的多包收集契约；当前必须使用 package-scoped 回归命令。

本轮主线结论：正式建议业务链路已经有可重复的受控闭环，下一步不再重复单段音频实验；只在成本/隐私明确后做一次真实远程 provider 验收，其他开发回归继续使用本机 fake gateway 或 no-cost deterministic lane。
