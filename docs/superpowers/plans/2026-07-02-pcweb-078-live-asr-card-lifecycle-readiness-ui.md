# PCWEB-078 Live ASR Card Lifecycle Readiness UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show PCWEB-077 card lifecycle readiness summaries in the local Web workbench after Live ASR streams finish, without enabling LLM calls, card creation, event mutation, credential reads, or paid services.

**Architecture:** The FastAPI backend remains unchanged for PCWEB-077. The static frontend derives a local dry-run candidate from already received Live ASR events, POSTs the response-only summary endpoint, and renders a read-only readiness panel with phases, blockers, next decisions, and disabled safety flags.

**Tech Stack:** FastAPI static assets, browser `fetch`, browser `EventSource`, headless Chrome CDP smoke, pytest docs/static gates.

---

### Task 1: RED Static And Browser Expectations

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/e2e/browser_smoke.mjs`

- [x] **Step 1: Add failing static asset expectations**

Add assertions that the index contains `card-lifecycle-readiness-panel` and the script contains `loadLiveAsrCardLifecycleReadinessSummary`, `buildCardLifecycleReadinessCandidateResponse`, `renderCardLifecycleReadinessSummary`, `llm-card-lifecycle-readiness-summaries`, and `card_lifecycle_summary_phases`.

- [x] **Step 2: Add failing browser smoke expectations**

After the existing Live ASR terminal summary wait, assert that the panel includes `blocked_until_enabled`, `12 phases`, `not_called`, `not_appended`, and `not_written`; assert 12 rendered `.lifecycle-phase` rows; assert the captured fetch URLs include `/llm-card-lifecycle-readiness-summaries`.

- [x] **Step 3: Verify RED**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "workbench_index_serves_state_first_ui_shell or workbench_static_assets_are_served or scripted_browser_e2e_gate_exists_and_checks_critical_ui_paths or web_mvp_readme_documents_scripted_browser_e2e_gate" -q
```

Expected: failures for missing PCWEB-078 DOM/script/docs references.

### Task 2: Implement Read-Only Frontend Panel

**Files:**
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

- [x] **Step 1: Add the panel shell**

Add a `section.lifecycle-section` after the suggestion card section with a `div#card-lifecycle-readiness-panel`.

- [x] **Step 2: Add state reset and render helpers**

Clear the panel from `renderEmpty`, show a loading/empty state when Live ASR starts, and render summaries with status metrics, block reasons, next decisions, scoped safe flags, and 12 phase rows.

- [x] **Step 3: Add candidate derivation**

Implement `buildCardLifecycleReadinessCandidateResponse(events)` from `llm_request_draft_event`, `transcript_final`/`transcript_revision`, and `state_event` payloads. Prefer active evidence and no degradation. Use `model=not_called` and `usage.total_tokens=0`.

Implementation note: terminal Live ASR SSE handling must pass the complete accumulated event stream to the derivation helper. A single terminal `evaluation_summary` event is insufficient because it does not contain request draft, evidence, state, or transcript timing context.

Implementation note: the local contract probe uses the core-gate allowed `owner_gap` card type for all target kinds. This keeps the response-only UI probe compatible with current card validation and does not decide the future formal card taxonomy for `ActionItem`, `Risk`, or `OpenQuestion`.

- [x] **Step 4: Add POST call after terminal Live ASR summary**

Call PCWEB-077 from `applyTerminalLiveEventSideEffects` when `currentEventMode === "live_asr"`. Guard stale sessions by checking session id and mode after the response returns.

### Task 3: Update Documentation

**Files:**
- Modify: `code/web_mvp/README.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/privacy-and-data-flow.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`

- [x] **Step 1: Add PCWEB-078 references**

Document that the Web workbench consumes PCWEB-077 after Live ASR terminal summary and keeps the chain response-only/no-cost/no-write.

- [x] **Step 2: Update docs gate**

Require PCWEB-078 and `llm-card-lifecycle-readiness-summaries` in README, plan, traceability, acceptance, privacy, project structure, roadmap, and decision log.

### Task 4: GREEN And Regression

**Files:**
- Verify all modified files.

- [x] **Step 1: Run focused pytest**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py -k "workbench_index_serves_state_first_ui_shell or workbench_static_assets_are_served or scripted_browser_e2e_gate_exists_and_checks_critical_ui_paths or web_mvp_readme_documents_scripted_browser_e2e_gate" -q
```

- [x] **Step 2: Run browser smoke**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp
node e2e/browser_smoke.mjs
```

- [x] **Step 3: Run backend regression**

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

- [x] **Step 4: Run quality gate**

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

### Task 5: Review And Hygiene

**Files:**
- Review: all modified files.

- [x] **Step 1: Request independent review**

Ask a read-only reviewer to check PCWEB-078 for frontend races, stale-session handling, hidden write/LLM/secret boundary regressions, and docs consistency.

Review result: no Critical issues. Important contract mismatch fixed by skipping readiness POST when no request draft has active evidence and no degradation. Minor fallback/test gaps fixed by running terminal side effects after JSON fallback and by asserting readiness POST body/no-EventSource readiness behavior in browser smoke.

- [x] **Step 2: Clean caches and check ports**

Remove `.pytest_cache` and `__pycache__`, then check ports `8767` and `9223`.

- [x] **Step 3: Sensitive marker scan**

Run the local sensitive-marker scan from the terminal without committing the marker list into documentation. Exclude `configs/local/**`, virtual environments, `__pycache__`, and `.pytest_cache`.

Expected: no committed source or documentation hit.
