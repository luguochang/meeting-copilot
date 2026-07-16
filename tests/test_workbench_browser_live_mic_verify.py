from pathlib import Path
import json
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_browser_live_mic_verify.mjs"


def test_browser_live_mic_verifier_counts_only_canonical_transcript_rows():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'transcript?.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length' in text
    assert 'utterance_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length' in text
    assert 'workbench_same_session_visible: Boolean(document.getElementById("session-meta")?.innerText && document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length >= 1)' in text
    assert 'document.querySelectorAll(".utterance")' not in text


def test_browser_live_mic_verifier_preflights_fake_audio_and_captures_chrome_diagnostics():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "validateFakeAudioInput" in text
    assert "invalid_empty_audio_file" in text
    assert "fake_audio_validation" in text
    assert "chrome_stderr_tail" in text
    assert 'stdio: ["ignore", "pipe", "pipe"]' in text
    assert "MEETING_COPILOT_BROWSER_MIC_CHROME_DIAGNOSTICS" in text
    assert "MEETING_COPILOT_BROWSER_MIC_CHROME_NO_SANDBOX" in text
    assert "copyFile" in text
    assert "chromeAudioInputFile" in text
    assert "fake_audio_input_copy" in text


def test_realtime_transcript_compaction_classifier_distinguishes_setting_and_outcome_states():
    module = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_browser_live_mic_compaction.mjs"
    node_script = f"""
import {{ buildRealtimeTranscriptCompactionReport }} from {json.dumps(module.as_uri())};
const sample = [{{
  at_ms: 1000,
  corrected_transcript_row_count: 0,
  max_rows_for_single_active_segment: 1,
  committed_transcript_row_count: 1,
}}];
const cases = [
  buildRealtimeTranscriptCompactionReport({{ correctionEnabled: false, recordingPhaseUiSamples: sample }}).status,
  buildRealtimeTranscriptCompactionReport({{ correctionEnabled: true, correctionStatus: {{ status: "no_revision_needed" }}, recordingPhaseUiSamples: sample }}).status,
  buildRealtimeTranscriptCompactionReport({{ correctionEnabled: true, correctionStatus: {{ status: "completed", revised_segment_ids: ["seg-1"] }}, recordingPhaseUiSamples: [{{ ...sample[0], corrected_transcript_row_count: 1, corrected_transcript_segment_ids: ["seg-1"], corrected_transcript_source_segment_ids: ["revision-1"] }}] }}).status,
  buildRealtimeTranscriptCompactionReport({{ correctionEnabled: true, correctionStatus: {{ status: "correction_rejected" }}, recordingPhaseUiSamples: sample }}).status,
];
console.log(JSON.stringify(cases));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        "correction_disabled_by_setting",
        "no_revision_needed",
        "passed_compacted_realtime_correction_visible",
        "failed_realtime_correction_not_visible",
    ]


def test_realtime_transcript_compaction_classifier_fails_closed_on_missing_or_stale_correction_evidence():
    module = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_browser_live_mic_compaction.mjs"
    node_script = f"""
import {{ buildRealtimeTranscriptCompactionReport }} from {json.dumps(module.as_uri())};
const committed = {{
  at_ms: 1000,
  corrected_transcript_row_count: 0,
  corrected_transcript_segment_ids: [],
  corrected_transcript_source_segment_ids: [],
  max_rows_for_single_active_segment: 1,
  committed_transcript_row_count: 1,
}};
const cases = [
  buildRealtimeTranscriptCompactionReport({{
    correctionEnabled: true,
    correctionStatus: {{ status: "completed", revised_segment_ids: ["seg-1"] }},
    recordingPhaseUiSamples: [committed],
  }}),
  buildRealtimeTranscriptCompactionReport({{
    correctionEnabled: true,
    correctionStatus: {{ status: "completed", revised_segment_ids: ["seg-1"] }},
    recordingPhaseUiSamples: [{{
      ...committed,
      corrected_transcript_row_count: 1,
      corrected_transcript_segment_ids: ["seg-1"],
      corrected_transcript_source_segment_ids: ["seg-1"],
    }}],
  }}),
  buildRealtimeTranscriptCompactionReport({{
    correctionEnabled: false,
    correctionStatus: {{ status: "correction_disabled_by_setting" }},
    recordingPhaseUiSamples: [{{ ...committed, corrected_transcript_row_count: 1 }}],
  }}),
  buildRealtimeTranscriptCompactionReport({{
    correctionEnabled: null,
    correctionStatus: {{}},
    recordingPhaseUiSamples: [committed],
  }}),
  buildRealtimeTranscriptCompactionReport({{
    correctionEnabled: true,
    correctionStatus: {{
      status: "partially_completed",
      revised_segment_ids: ["seg-1"],
      rejected_segment_ids: ["seg-2"],
      processed_segment_ids: ["seg-1", "seg-2"],
    }},
    recordingPhaseUiSamples: [{{
      ...committed,
      corrected_transcript_row_count: 1,
      corrected_transcript_segment_ids: ["seg-1"],
      corrected_transcript_source_segment_ids: ["seg-1:rtc-v1"],
    }}],
  }}),
  buildRealtimeTranscriptCompactionReport({{
    correctionEnabled: true,
    correctionStatus: {{ status: "mapping_rejected", processed_segment_ids: ["seg-1"] }},
    recordingPhaseUiSamples: [committed],
  }}),
];
console.log(JSON.stringify(cases.map((item) => ({{
  status: item.status,
  classification_reason: item.classification_reason || null,
}}))));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        {"status": "failed_realtime_correction_not_visible", "classification_reason": "revised_evidence_missing"},
        {"status": "passed_compacted_realtime_correction_visible", "classification_reason": None},
        {"status": "correction_disabled_by_setting", "classification_reason": "correction_disabled_by_setting"},
        {"status": "failed_realtime_correction_not_visible", "classification_reason": "correction_evidence_missing"},
        {"status": "passed_partial_correction_visible", "classification_reason": "partial_correction_with_rejected_segments"},
        {"status": "failed_realtime_correction_not_visible", "classification_reason": "correction_mapping_rejected"},
    ]


def test_browser_live_mic_verifier_samples_canonical_corrected_segment_identity():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'row.dataset.status === "corrected"' in text
    assert "corrected_transcript_segment_ids" in text
    assert "corrected_transcript_source_segment_ids" in text
