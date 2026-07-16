from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_all_buttons_smoke.mjs"


def test_workbench_all_buttons_smoke_script_covers_import_export_and_clickback():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "DOM.setFileInputFiles" in text
    assert "btn-upload" in text
    assert "simulated-release-review.16k.wav" in text
    assert "btn-export-transcript" in text
    assert "btn-export-minutes" in text
    assert "btn-export-audio" in text
    assert "evidence-focus" in text
    assert "history-modal-item" in text
    assert "btn-delete" in text


def test_workbench_all_buttons_smoke_uses_history_button_to_open_history_modal():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'document.getElementById("btn-history").click()' in text
    assert 'document.getElementById("history-modal").hidden === false' in text
    assert 'document.querySelector("#review-workspace > summary").click()' not in text
    assert "const historyItemSelector = `.history-modal-item[data-session-id=" in text
    assert "document.querySelector(${JSON.stringify(historyItemSelector)})" in text
    assert "history-list').innerText.includes" not in text


def test_workbench_all_buttons_smoke_declares_user_visible_button_coverage():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "buttonCoverage" in text
    assert "button_coverage" in text
    assert "cockpitStage" in text
    for button_id in [
        "btn-record",
        "btn-stop",
        "btn-load",
        "btn-upload",
        "btn-history",
        "btn-settings",
        "btn-organize",
        "btn-live",
        "btn-delete",
        "btn-cards",
        "btn-approach",
        "btn-minutes",
        "btn-export-transcript",
        "btn-export-minutes",
        "btn-export-audio",
        "btn-auto-suggestion-toggle",
    ]:
        assert button_id in text


def test_workbench_all_buttons_smoke_requires_complete_coverage_before_go_status():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "function assertCoverageComplete" in text
    assert 'entry.coverage === "pending"' in text
    assert "pending button coverage" in text
    assert "pending focus filter coverage" in text
    assert "pending overview jump coverage" in text
    assert 'markButtonCovered("btn-load", "hidden_demo_only"' in text
    assert text.index("assertCoverageComplete();") < text.index('status: "go_workbench_all_buttons_smoke"')


def test_workbench_all_buttons_smoke_preserves_canonical_text_on_rerecognition_and_provider_error():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "reconciledFinalProbe" in text
    assert "providerErrorPreservationProbe" in text
    assert 'utterances: document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length' in text
    assert "reconciled final corrupted canonical text" in text
    assert "provider error removed committed segments" in text
    assert "provider error removed canonical containers" in text


def test_workbench_all_buttons_smoke_requires_executed_candidates_to_leave_reminder_panel():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "正式建议处理后实时提醒应为空" in text
    assert "#candidate-panel [data-card-kind=\"candidate\"]" in text
    assert 'document.getElementById("c-gap")?.innerText === "0"' in text
    assert 'document.getElementById("s-candidates")?.innerText === "0"' in text
    assert "还没有实时提醒" in text
    assert 'clickOverviewJump(page, "reminders", "overview_jump_reminders", "#candidate-panel", "已跳到实时提醒")' not in text


def test_workbench_all_buttons_smoke_is_valid_javascript():
    completed = subprocess.run(
        ["node", "--check", str(SCRIPT)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_workbench_all_buttons_smoke_uses_fake_llm_gateway_not_paid_env():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "startFakeLlmGateway" in text
    assert "LLM_GATEWAY_API_KEY" in text
    assert "sk-e2e-fake" in text
    assert "LLM_GATEWAY_BASE_URL" in text
    assert "process.env.LLM_GATEWAY_API_KEY" not in text


def test_workbench_smoke_retries_chrome_profile_cleanup():
    scripts = [
        REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_smoke.mjs",
        REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_all_buttons_smoke.mjs",
    ]
    for smoke_script in scripts:
        text = smoke_script.read_text(encoding="utf-8")

        assert "async function removeTempDirWithRetry" in text
        assert "ENOTEMPTY" in text
        assert "EBUSY" in text
        assert "await removeTempDirWithRetry(chromeUserDataDir)" in text


def test_workbench_all_buttons_smoke_records_visual_step_screenshots():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "async function captureStep" in text
    assert "workbench_visual_acceptance_report.json" in text
    assert "screenshots" in text
    assert "checklist" in text
    for step in [
        "initial_page",
        "import_recording",
        "history_open",
        "history_reopen",
        "suggestions_generated",
        "evidence_clickback",
        "approach_generated",
        "minutes_generated",
        "transcript_refreshed",
        "exports_verified",
        "audio_export_verified",
        "auto_suggestion_paused",
        "auto_suggestion_resumed",
        "organize_completed",
        "delete_reset",
    ]:
        assert step in text


def test_workbench_all_buttons_smoke_checks_desktop_and_375x812_mobile_layout():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "const DESKTOP_VIEWPORT" in text
    assert "const MOBILE_VIEWPORT = { width: 375, height: 812" in text
    assert '"Emulation.setDeviceMetricsOverride"' in text
    assert "function readViewportLayoutProbe" in text
    assert "document.documentElement.scrollWidth" in text
    assert "document.documentElement.clientWidth" in text
    assert "overlapping_button_pairs" in text
    assert "clipped_major_text" in text
    assert "mobile_375x812" in text
    assert "mobile_layout_probe" in text


def test_workbench_all_buttons_smoke_covers_left_focus_filters():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "focusFilterCoverage" in text
    assert "focus_filter_coverage" in text
    assert "data-focus-type" in text
    assert "data-candidate-type" in text
    assert "disabled_zero_count_filter" in text
    for focus_type in ["DecisionCandidate", "ActionItem", "Risk", "OpenQuestion"]:
        assert focus_type in text
    for step in [
        "candidate_filter_risk",
        "candidate_filter_all",
    ]:
        assert step in text


def test_workbench_all_buttons_smoke_covers_meeting_overview_navigation():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "overviewJumpCoverage" in text
    assert "overview_jump_coverage" in text
    assert "overviewJumpFocusStates" in text
    assert "overview_jump_focus_state" in text
    assert "overviewJumpFocusStates.push(overviewJumpFocusState)" in text
    assert "data-overview-target" in text
    assert "function isElementInViewport" in text
    assert "overviewJumpFocusState" in text
    assert "previousToastText" in text
    assert "active_element_matches" in text
    assert "overview jump focus target did not receive focus" in text
    assert "toast_after_click_matches" in text
    assert "target_in_viewport" in text
    assert 'clickOverviewJump(page, "transcript", "overview_jump_transcript", "#transcript-stream", "已跳到实时文字")' in text
    assert '"#transcript-stream .utterance"' not in text
    assert "document.getElementById('toast').innerText.length > 0" not in text
    for target in ["transcript", "reminders", "suggestions", "approach", "audio", "minutes"]:
        assert target in text
    for step in [
        "overview_jump_transcript",
        "overview_jump_reminders",
        "overview_jump_suggestions",
        "overview_jump_approach",
        "overview_jump_audio",
        "overview_jump_minutes",
    ]:
        assert step in text


def test_workbench_all_buttons_smoke_fail_closes_on_browser_runtime_and_network_errors():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'cdpPage.send("Network.enable")' in text
    assert 'cdpPage.on("Runtime.exceptionThrown"' in text
    assert 'cdpPage.on("Runtime.consoleAPICalled"' in text
    assert 'cdpPage.on("Network.loadingFailed"' in text
    assert 'cdpPage.on("Network.responseReceived"' in text
    assert "function assertBrowserDiagnosticsClean" in text
    assert "runtime_exceptions" in text
    assert "error_console" in text
    assert "network_loading_failed" in text
    assert "http_5xx" in text
    assert "expectedNetworkFailureAllowlist" in text


def test_workbench_all_buttons_smoke_executes_state_restore_and_suggestion_dedupe_probes():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "recordingFailureRestoreProbe" in text
    assert "processed_candidate_ids" in text
    assert "executedSuggestionCandidateIds" in text
    assert "currentCandidateFocusType" in text
    assert "suggestionSemanticDedupeProbe" in text
    assert "different_card_ids_same_semantics" in text
    assert "folded_suggestion_count" in text
