# Meeting Copilot V2 Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the 2026-07-12 V2 refactor into a persistent, testable and honestly releasable local-first meeting copilot.

**Architecture:** Restore contracts from the data layer outward. SQLite owns durable state, the realtime meeting service owns audio/ASR/canonical state, AI derivations pass explicit gates, and the Workbench consumes stable APIs without patch-script coupling. Every phase ends with behavior tests before the next layer is changed.

**Tech Stack:** Python 3.14, FastAPI, SQLite, vanilla JavaScript, local FunASR/Sherpa, OpenAI-compatible LLM gateway, pytest, Node/Chrome CDP, Tauri.

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
