from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "code" / "web_mvp" / "e2e" / "workbench_v2_post_meeting_ui_gate.mjs"


def test_post_meeting_ui_gate_is_explicitly_isolated_and_fail_closed():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'MEETING_COPILOT_UI_GATE_CONFIRM' in source
    assert 'isolated-test-meeting' in source
    assert 'includes("E2E")' in source
    assert 'counts_as_packaged_client_go: false' in source
    assert 'acceptance_eligible: false' in source


def test_post_meeting_ui_gate_covers_all_user_final_documents_and_downloads():
    source = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "E2E_MINUTES_FINAL",
        "E2E_DECISION_FINAL",
        "E2E_ACTION_FINAL",
        "E2E_RISK_FINAL",
        "E2E_TRANSCRIPT_FINAL",
        "Markdown",
        "Word 文档",
        "JSON",
        "导出脱敏诊断包",
        "返回会议列表",
    ):
        assert marker in source
