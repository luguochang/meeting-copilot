from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_long_meeting_evidence_verify.mjs"


def test_workbench_long_meeting_evidence_script_covers_mainline_without_paid_calls():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "long_meeting_ui_report.json" in text
    assert "synthetic_duration_minutes: 20" in text
    assert "counts_as_20_60_min_production_soak: false" in text
    assert "transcript_revision" in text
    assert "revision_utterance_count" in text
    assert "suggestion_card_count" in text
    assert "approach_card_count" in text
    assert "minutes_visible" in text
    assert "evidence-link" in text
    assert "evidence-focus" in text
    assert "LLM_GATEWAY_BASE_URL: \"\"" in text
    assert "LLM_GATEWAY_API_KEY: \"\"" in text
    assert "process.env.LLM_GATEWAY_API_KEY" not in text


def test_workbench_long_meeting_evidence_script_records_visual_steps_and_exports():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "captureStep" in text
    assert "screenshots" in text
    for step in [
        "long_session_loaded",
        "original_evidence_clickback",
        "revision_evidence_clickback",
        "minutes_and_approach_visible",
        "exports_verified",
        "delete_reset",
    ]:
        assert step in text


def test_workbench_long_meeting_evidence_opts_into_demo_history_visibility():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "`http://127.0.0.1:${port}/workbench?demo=1`" in text


def test_workbench_long_meeting_evidence_uses_current_history_modal_contract():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "document.querySelector('.history-modal-item[data-session-id=\"${sessionId}\"]')" in text
    assert "document.querySelector(`.history-modal-item[data-session-id=\"${sid}\"]`)" in text
    assert 'querySelector(\'button[data-action="open"]\')' in text


def test_workbench_long_meeting_evidence_uses_canonical_transcript_contract():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'transcript-segment[data-status="corrected"]' in text
    assert 'transcript-segment[data-transcript-segment-id]' in text
    assert 'utterances: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length' in text
    assert ".transcript-segment.evidence-focus" in text
    assert 'document.getElementById("review-workspace").open = true' in text
