# Mainline Usable E2E Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one local runner that executes the PC mainline from M1 audio healthcheck through Web Live ASR session, draft review, feedback/export closure, and traceable JSON/Markdown report output.

**Architecture:** Add a focused Python runner under `tools/` that reuses existing production paths: `audio_capture_healthcheck.build_audio_capture_health_report` and FastAPI `TestClient(create_app())` endpoints. The runner writes artifacts only under approved ignored roots, never reads private paths, and treats expected ASR-quality/system-audio gaps as explicit blockers rather than product-chain failures.

**Tech Stack:** Python standard library (`argparse`, `json`, `wave`, `struct`, `math`, `datetime`, `pathlib`), FastAPI TestClient, pytest, existing Web MVP backend.

---

### Task 1: Runner Contract Tests

**Files:**
- Create: `tests/test_mainline_usable_e2e_runner.py`
- Create: `tools/mainline_usable_e2e_runner.py`

- [ ] **Step 1: Write failing import/contract tests**

Add tests that load `tools/mainline_usable_e2e_runner.py` with `importlib.util` and assert:

```python
def test_runner_executes_mainline_and_writes_traceable_reports(tmp_path):
    tool = load_tool_module()
    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_contract_review",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
    )

    assert report["report_mode"] == "mainline_usable_e2e_selftest"
    assert report["overall_status"] == "mainline_product_chain_exercised_with_expected_blockers"
    assert report["audio_health"]["health_status"] == "audio_capture_health_passed"
    assert report["mainline_trial"]["trial_status"] == "mainline_trial_session_created"
    assert report["live_asr"]["event_counts"]["transcript_final"] >= 1
    assert report["live_asr"]["event_counts"]["state_event"] >= 1
    assert report["live_asr"]["event_counts"]["suggestion_candidate_event"] >= 1
    assert report["live_asr"]["event_counts"]["llm_request_draft_event"] >= 1
    assert report["draft_review"]["draft_status"] == "draft_review_created"
    assert report["closure"]["closure_status"] == "mainline_trial_feedback_export_preview_created"
    assert report["closure"]["final_decision"] == "inconclusive_requires_more_shadow_tests"
    assert report["gap_summary"]["blocked_by_asr_quality"] >= 1
    assert report["gap_summary"]["blocked_requires_m2_system_audio_capture"] >= 1
    assert Path(report["artifacts"]["json_report_path"]).exists()
    assert Path(report["artifacts"]["markdown_report_path"]).exists()
```

Add safety assertions:

```python
def test_runner_report_has_no_remote_or_private_side_effects(tmp_path):
    report = tool.run_mainline_usable_e2e_selftest(...)
    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }
    assert "/Users/" not in json.dumps(report, ensure_ascii=False)
    assert "configs/local" not in json.dumps(report, ensure_ascii=False)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
```

Expected: fail because `tools/mainline_usable_e2e_runner.py` does not exist.

### Task 2: Minimal Runner Implementation

**Files:**
- Create: `tools/mainline_usable_e2e_runner.py`

- [ ] **Step 1: Implement minimal runner**

Implement:

```python
def run_mainline_usable_e2e_selftest(
    *,
    session_id: str,
    repo_root: Path = REPO_ROOT,
    output_root: Path | None = None,
    run_browser_smoke: bool = False,
    browser_smoke_runner: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

The function must:

- write a synthetic `16 kHz mono s16 WAV >= 10s` under `artifacts/tmp/audio_health/`;
- run M1 healthcheck;
- create a TestClient from `meeting_copilot_web_mvp.app.create_app`;
- call `POST /desktop/mainline-asr-blocked-trial/sessions`;
- call `GET /live/asr/sessions/{session_id}/events`;
- call `GET /live/asr/sessions/{session_id}/draft.md`;
- call `POST /desktop/mainline-trial-feedback-export-closures`;
- collect event counts, draft line count, closure decision, safety flags, and gap entries;
- write JSON and Markdown reports under `artifacts/tmp/mainline_selftests/`;
- return a report with no private absolute paths.

- [ ] **Step 2: Verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
```

Expected: pass.

### Task 3: CLI And Docs

**Files:**
- Modify: `tools/mainline_usable_e2e_runner.py`
- Create: `docs/mainline-usable-e2e-selftest-2026-07-04.md`
- Modify: `docs/decision-log.md`

- [ ] **Step 1: Add CLI test and implementation**

Add test for:

```python
exit_code = tool.main(["--session-id", "m15_cli_review", "--output-root", str(tmp_path / "artifacts/tmp/mainline_selftests")], out=out)
payload = json.loads(out.getvalue())
assert exit_code == 0
assert payload["overall_status"] == "mainline_product_chain_exercised_with_expected_blockers"
```

Implement `main(argv=None, out=sys.stdout)`.

- [ ] **Step 2: Record run contract**

Create `docs/mainline-usable-e2e-selftest-2026-07-04.md` documenting the runner command, pass criteria, expected blockers, and forbidden side effects. Append DEC-208 to `docs/decision-log.md`.

- [ ] **Step 3: Verify regression and hygiene**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py tests/test_audio_capture_healthcheck.py code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence -q -p no:cacheprovider
```

Then run sensitive scan for API-key shaped tokens, relay domains, private audio filenames, and local model cache paths. Expected: no matches.

### Self-Review

Spec coverage: covers one-command runner, M1 healthcheck, Web mainline session, events, draft, closure, report artifacts, blockers, safety flags, docs, tests, and sensitive scan.

Placeholder scan: no TBD/TODO placeholders.

Type consistency: function names, status names, artifact keys, and test names are consistent across tasks.
