# Workbench Function Completion Audit

> Date: 2026-07-09
> Status: audited, not fully closed
> Scope: answer whether the current Workbench page functions are complete and self-tested before external release.

## 1. Conclusion

No, the page cannot honestly be declared "all functions complete and fully self-tested" yet.

The current Workbench has a working Web MVP mainline and multiple proven flows, but the evidence is mixed:

- Some flows are fresh UI click evidence from the current workspace.
- Some flows are backend/API or source-contract tests, not real browser button clicks.
- Some flows are historical evidence artifacts, not rerun in this audit.
- Some release blockers are unrelated to page function, such as Developer ID signing, notarization, Gatekeeper, and Windows real-machine verification.

The external release track moved too far ahead of the page-function audit. That was a sequencing mistake: external packaging/release checks must not be treated as proof that every Workbench button and product flow has closed.

## 2. Fresh Evidence From This Audit

### Commands

```bash
node --check code/web_mvp/e2e/workbench_smoke.mjs
node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
```

Result: both passed.

```bash
PYTHONPATH=/Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend:/Users/chase/Documents/面试/meeting-copilot/code/core \
pytest -q code/web_mvp/backend/tests/test_workbench.py::test_workbench_has_recording_export_buttons_and_download_handlers
```

Result: `1 passed, 2 warnings`.

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

Result:

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified, screenshots=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/ui_screenshots/workbench-p0-4-smoke
```

```bash
PYTHONPATH=/Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend:/Users/chase/Documents/面试/meeting-copilot/code/core \
pytest -q \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_auto_suggestions.py \
  code/web_mvp/backend/tests/test_file_convert.py \
  tests/test_workbench_productized_ui.py \
  tests/test_workbench_desktop_runtime_probe.py
```

Result: `84 passed, 2 warnings`.

### Freshly Verified UI Behaviors

The current `workbench_smoke.mjs` now verifies these browser UI behaviors with no paid provider calls:

- Workbench loads at `/workbench?demo=1`.
- Primary user actions are present: `开始会议`, `结束会议`, `导入录音`, `历史记录`.
- Session-only actions are hidden before a session exists.
- Desktop and mobile layout screenshots are captured and checked for horizontal overflow.
- `试用示例` creates a session.
- Transcript utterances render.
- Realtime suggestion candidates render.
- `历史记录` shows the current session.
- `生成会议建议` renders formal suggestion cards.
- `分析方案利弊` renders approach cards.
- `生成会议纪要` renders minutes.
- `刷新文字` refreshes from stable session snapshot and keeps transcript visible.
- `暂停自动建议` / `恢复自动建议` toggles through the real UI/API state.
- `整理会议` runs the one-click organize flow and updates page status.
- `导出文字稿` and `导出纪要` create download links that honor `apiBaseUrl`, which is required in packaged Tauri WebView.
- `删除本次会议` clears visible counters and resets the session view.

## 3. Newly Found And Fixed Issue

### Export URL Bug

Audit found that `导出文字稿` and `导出纪要` used relative URLs directly:

```js
link.href = url
```

That is acceptable only when the page is served from the same backend origin. In packaged Tauri WebView, the frontend can use a separate `desktop_api_base_url`, so download URLs must also go through `apiUrl()`.

Fix:

```js
link.href = apiUrl(url)
```

Regression coverage:

- `test_workbench_has_recording_export_buttons_and_download_handlers` now asserts the export path uses `apiUrl(url)`.
- `workbench_smoke.mjs` temporarily sets `apiBaseUrl = "http://127.0.0.1:19090"`, clicks both export buttons, intercepts anchor download clicks, and verifies both generated hrefs start with that API base.

## 4. Currently Supported, With Evidence

### Supported In Current Fresh UI Smoke

- Demo opt-in meeting flow.
- Transcript display.
- Suggestion candidate display.
- Formal suggestion card generation through fake OpenAI-compatible gateway.
- Approach card generation through fake OpenAI-compatible gateway.
- Minutes generation through fake OpenAI-compatible gateway.
- History list visibility.
- Refresh text.
- Auto suggestion pause/resume UI toggle.
- One-click organize meeting.
- Export button path correctness for Web and Tauri API-base scenarios.
- Delete and visible reset.
- Desktop/mobile layout guardrails.

### Supported By Fresh Pytest/API Coverage

- Workbench route and static JS serving.
- Productized Workbench language and default demo hiding.
- Root route serving current Workbench.
- Startup audio/provider readiness messaging.
- Source labels for demo, degraded, live mic, simulated realtime, and recorded mic sessions.
- Refresh button using stable snapshot instead of removed SSE replay client.
- Stable panels outside transcript stream.
- Export button existence and handler contract.
- Auto suggestion run-once, live-final generation, pause/resume, idempotency, cooldown, rate limit, and acceptance blocker behavior.
- File conversion/import backend path.
- Desktop runtime probe source contract.

### Supported By Existing Historical Evidence, Not Rerun In This Audit

- Browser live mic mainline evidence exists under:
  - `artifacts/tmp/acceptance/browser-live-mic-tts-20260709-mainline-001/manifest.json`
- Simulated realtime mainline evidence exists under:
  - `artifacts/tmp/acceptance/current-simulated-realtime-20260709/`
  - `artifacts/tmp/release_acceptance/release-current-20260709-browser-mic-gate-and-ui-path-fixed/lanes/simulated-realtime/`
- File lane evidence exists under:
  - `artifacts/tmp/release_acceptance/release-current-20260709-browser-mic-gate-and-ui-path-fixed/lanes/file-lane/`
- Packaged Tauri no-cost same-chain probe exists under:
  - `artifacts/tmp/desktop_tauri_current_run/packaged-same-chain-no-cost-20260709/evidence.json`

These are useful evidence, but they must be labeled correctly. Historical evidence does not replace current all-button UI regression.

## 5. Not Fully Closed

These items must not be described as fully complete yet:

- `导入录音` has backend/file-lane evidence, but the actual browser file picker and import button are not covered by a fresh UI click test in this audit.
- Export buttons now have click/path validation, but actual browser download-to-file verification is still not covered.
- Evidence quote clickback is source-covered but not yet browser-click verified.
- File lane and simulated realtime lane downstream buttons are mostly API/runner verified; not all are proven by clicking the Workbench buttons in those lanes.
- True user microphone/manual meeting is not rerun in this audit. Existing browser live mic evidence uses controlled browser automation and should not be confused with a final manual meeting acceptance by the user.
- Tauri packaged Workbench user-click flow is not fully covered. Existing packaged evidence is runtime/no-cost same-chain probe, not full user operation coverage for start/import/export/delete.
- Windows is not verified on a Windows real machine.
- Mac public release still has external blockers: Developer ID signing, notarization, Gatekeeper acceptance.
- Current user-visible `http://127.0.0.1:8765/workbench` was unreachable during this audit because nothing was listening on port 8765. A backend existed on port 8000, but the page open in the in-app browser was pointed at 8765. This is a product/devops gap: launch and port management are not closed.

## 6. Why The Page Looked Broken

The in-app browser tab was on:

```text
http://127.0.0.1:8765/workbench?debug=1783438411814
```

The observed title was:

```text
无法访问此站点
```

Port check found no listener on `8765`, while `http://127.0.0.1:8000/health` returned:

```json
{"status":"ok","service":"meeting-copilot-web-mvp"}
```

So "文字又没有了" in that moment was not proof that transcript state disappeared. The page was simply connected to a dead port. The project needs a stable launch path so the product URL and backend port cannot drift.

## 7. Required Next Work

Priority order:

1. Add a stable local launch command/script for the product URL, preferably defaulting to `127.0.0.1:8765` because Tauri config and release tools assume that port.
2. Add a dedicated all-buttons UI smoke that covers:
   - import audio via file chooser,
   - export download event and saved file name/content,
   - evidence clickback,
   - history item reopen after delete/non-delete flows,
   - file-lane Workbench buttons, not just runner API calls.
3. Add one visible manual smoke checklist for the user-run real microphone meeting.
4. Keep external release checks separate from function-completion checks:
   - page function gate,
   - real meeting gate,
   - packaged app gate,
   - public release gate,
   - Windows real-machine gate.

## 8. Release Claim Boundary

Allowed claim now:

```text
Web Workbench core demo flow and several page controls pass fresh no-cost UI smoke;
backend/API tests for Workbench, file conversion, and auto suggestions pass;
historical evidence exists for browser-live-mic, simulated realtime, file lane, and packaged no-cost same-chain.
```

Not allowed claim now:

```text
All Workbench page functions are complete.
All buttons have UI-click closure.
The product is ready for public external release.
Mac and Windows desktop release are complete.
The user microphone meeting flow has been finally accepted by the user.
```
