from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "code" / "web_mvp" / "e2e" / "fake_llm_gateway.mjs"


def test_fake_llm_gateway_supports_asr_correction_cards_approach_and_minutes():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "ASR 转写修正器" in text
    assert 'correctionMode === "rewrite_technical_terms"' in text
    assert '|| "unchanged"' in text
    assert "方案考量" in text
    assert "纪要" in text
    assert "suggestion_text" in text
    assert "MEETING_COPILOT_FAKE_LLM_PORT" in text


def test_fake_llm_gateway_has_explicit_correction_fixture_mode():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "MEETING_COPILOT_FAKE_LLM_CORRECTION_MODE" in text
    assert "rewrite_technical_terms" in text
    assert "correctTechnicalTerms" in text
    assert "corrected_transcript" in text
    assert "tracout" in text
    assert "checkout" in text
    assert "check\\s+kout" in text
    assert "check\\s+(?:acout|kout|out)" in text
    assert "p\\s*九[九b]" in text
