from pathlib import Path
import json
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "code" / "web_mvp" / "e2e" / "deterministic_correction_fixture.mjs"
RUNNER = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_deterministic_correction_e2e.mjs"


def test_deterministic_correction_fixture_starts_before_backend_revision():
    node_script = f"""
import {{ buildDeterministicCorrectionRecord, expectedDeterministicCorrection }} from {json.dumps(MODULE.as_uri())};
const record = buildDeterministicCorrectionRecord("deterministic_correction_fixture");
const expected = expectedDeterministicCorrection();
console.log(JSON.stringify({{
  session_id: record.session_id,
  input_source: record.input_source,
  provider_mode: record.provider_mode,
  is_mock: record.is_mock,
  final_count: record.events.filter((event) => event.event_type === "transcript_final").length,
  revision_count: record.events.filter((event) => event.event_type === "transcript_revision").length,
  original_text: record.events.find((event) => event.event_type === "transcript_final")?.payload?.text,
  expected_target_segment_id: expected.target_segment_id,
      expected_revision_source_segment_id: expected.revision_source_segment_id,
      original_evidence_card_count: record.suggestion_cards.filter(
        (card) => (card.evidence_span_ids || []).includes(expected.original_evidence_id),
      ).length,
}}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "session_id": "deterministic_correction_fixture",
        "input_source": "simulated_realtime_wav",
        "provider_mode": "real",
        "is_mock": False,
        "final_count": 1,
        "revision_count": 0,
        "original_text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒",
        "expected_target_segment_id": "det_corr_seg_1",
        "expected_revision_source_segment_id": "det_corr_seg_1:rtc-v1",
        "original_evidence_card_count": 1,
    }


def test_deterministic_correction_runner_proves_backend_and_canonical_ui_contracts():
    text = RUNNER.read_text(encoding="utf-8")

    assert "realtime-corrections/run-once" in text
    assert "revised_segment_ids" in text
    assert '.transcript-segment[data-status="corrected"]' in text
    assert "data-source-segment-id" in text
    assert "details.original-asr-text" in text
    assert "counts_as_production_llm_evidence: false" in text
    assert "remote_asr_called: false" in text
