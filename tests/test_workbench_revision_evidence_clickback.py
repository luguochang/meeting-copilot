from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_revision_evidence_clickback.mjs"


def test_workbench_revision_evidence_clickback_script_covers_revision_and_original_evidence():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "transcript_revision" in text
    assert "superseded_evidence_spans" in text
    assert "llm-execution-previews" in text
    assert "evidence-link" in text
    assert "evidence-focus" in text
    assert "original_evidence_id" in text
    assert "revision_evidence_id" in text
    assert "corrected_segment_visible" in text
    assert "data-status=\"corrected\"" in text
    assert "revision_evidence_clickback" in text
    assert "revision_clickback" in text
    assert "revision_evidence_clickback_report.json" in text


def test_workbench_revision_evidence_clickback_records_screenshot_steps_and_uses_no_paid_gateway():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "captureStep" in text
    assert "screenshots" in text
    assert "revision_loaded" in text
    assert "original_evidence_clickback" in text
    assert "revision_evidence_clickback" in text
    assert "revision_relationship_visible" in text
    assert "LLM_GATEWAY_BASE_URL: \"\"" in text
    assert "LLM_GATEWAY_API_KEY: \"\"" in text
    assert "process.env.LLM_GATEWAY_API_KEY" not in text


def test_workbench_revision_evidence_clickback_opts_into_demo_history_explicitly():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "?demo=1&verify=revision-evidence-clickback" in text


def test_workbench_revision_evidence_clickback_uses_current_history_modal_open_action():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "history-modal-item" in text
    assert 'button[data-action="open"]' in text


def test_workbench_revision_evidence_clickback_asserts_canonical_transcript_contract():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "transcript-segment" in text
    assert "sourceSegmentId" in text
    assert "original-asr-text" in text
