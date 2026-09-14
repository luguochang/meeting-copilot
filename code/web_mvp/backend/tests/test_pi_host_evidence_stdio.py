import asyncio
from pathlib import Path
import shutil

import pytest

from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar, PiCoachRuntimeError
from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
from meeting_copilot_web_mvp.realtime_intelligence import (
    RealtimeIntelligenceRequest, run_realtime_coach_via_pi,
)
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.mark.parametrize("mutation", [None, "revision", "ended"])
def test_real_stdio_host_search_reaches_database_and_python_validator(tmp_path, mutation):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the real JSONL integration test")
    root = Path(__file__).resolve().parents[4]
    bridge = root / "code/agent_runtime/pi_coach_bridge"
    if not (bridge / "node_modules/@earendil-works/pi-agent-core").exists():
        pytest.skip("Pi bridge npm dependencies are required")
    store = V2Persistence(tmp_path / "meeting.db")
    for index in range(13):
        text = ("Release requires legal approval." if index == 0
                else "We will release Friday." if index == 12 else f"Routine update {index}.")
        store.commit_final_and_enqueue(
            meeting_id="meeting", final_id=f"f-{index}",
            segment_id="old-0" if index == 0 else f"new-{index}", text=text,
            normalized_text=text, started_at_ms=index * 1000,
            ended_at_ms=index * 1000 + 900, evidence_hash=f"hash-{index}",
            now_ms=index * 1000 + 1000, source_track="system_audio",
        )
    bound_meetings = []

    class ChangingRegistry(PiEvidenceRegistry):
        def validate(self, quotes):
            if mutation == "revision":
                store.commit_transcript_revision(
                    meeting_id="meeting", segment_id="old-0", expected_evidence_hash="hash-0",
                    corrected_text="Release no longer requires legal approval.",
                    revision_id="changed", now_ms=20000,
                )
            elif mutation == "ended":
                store.end_meeting(meeting_id="meeting", now_ms=20000)
            return super().validate(quotes)

    def factory(meeting_id):
        bound_meetings.append(meeting_id)
        return ChangingRegistry(store, meeting_id=meeting_id)

    runtime = PiCoachSidecar(
        command=[node, str(bridge / "test/fixtures/host_evidence_stdio.mjs")],
        evidence_registry_factory=factory,
    )
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting", state_revision=1,
        new_paragraphs=[{"id": "new-12", "text": "We will release Friday.",
                         "revision": 1, "source_track": "system_audio",
                         "correction_status": "no_change"}],
        context_paragraphs=[], retrieval_paragraphs=[], semantic_windows=[],
        rolling_state={}, meeting_goal="Confirm release conditions.",
    )
    try:
        evaluation = run_realtime_coach_via_pi(
            request=request, pi_runtime=runtime,
            provider_config={"model": "controlled", "api_key": "test-only",
                             "base_url": "http://unused.invalid", "timeout_seconds": 5},
        )
        if mutation:
            with pytest.raises(PiCoachRuntimeError) as caught:
                asyncio.run(evaluation)
            assert caught.value.code == "pi_evidence_superseded"
            return
        result = asyncio.run(evaluation)
        assert bound_meetings == ["meeting"]
        assert result["intervention"] is not None
        assert "old-0" in result["intervention"].evidence_segment_ids
        assert result["agent_metrics"]["history_searches"] == 1
        assert result["agent_metrics"]["history_results"] == 1
        assert result["agent_metrics"]["turns"] == 2
    finally:
        runtime.close()
        store.close()


def test_real_stdio_host_span_reaches_database_and_registers_neighbors(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the real JSONL integration test")
    root = Path(__file__).resolve().parents[4]
    bridge = root / "code/agent_runtime/pi_coach_bridge"
    fixture = bridge / "test/fixtures/host_span_evidence_stdio.mjs"
    if not (bridge / "node_modules/@earendil-works/pi-agent-core").exists():
        pytest.skip("Pi bridge npm dependencies are required")
    store = V2Persistence(tmp_path / "meeting.db")
    for index in range(3):
        text = "发布前需要法务批准。" if index == 0 else (
            "我们会在周五发布。" if index == 2 else "中间说明。"
        )
        store.commit_final_and_enqueue(
            meeting_id="meeting", final_id=f"f-{index}", segment_id=f"old-{index}" if index < 2 else "new-12",
            text=text, normalized_text=text, started_at_ms=index * 1000,
            ended_at_ms=index * 1000 + 900, evidence_hash=f"hash-{index}",
            now_ms=index * 1000 + 1000, source_track="system_audio",
        )
    store.mark_correction_segments_no_change(
        meeting_id="meeting", segment_ids=["old-0", "old-1", "new-12"],
        max_input_transcript_seq=100, now_ms=5_000,
    )
    runtime = PiCoachSidecar(
        command=[node, str(fixture)],
        evidence_registry_factory=lambda meeting_id: PiEvidenceRegistry(store, meeting_id=meeting_id),
    )
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting", state_revision=1,
        new_paragraphs=[{"id": "new-12", "text": "我们会在周五发布。", "revision": 1,
                         "source_track": "system_audio", "correction_status": "no_change",
                         "evidence_quality": "reviewed"}],
        context_paragraphs=[], retrieval_paragraphs=[], semantic_windows=[],
        rolling_state={}, meeting_goal="Confirm release conditions.",
    )
    try:
        result = asyncio.run(run_realtime_coach_via_pi(
            request=request, pi_runtime=runtime,
            provider_config={"model": "controlled", "api_key": "test-only",
                             "base_url": "http://unused.invalid", "timeout_seconds": 5},
        ))
        assert result["intervention"] is not None
        assert result["intervention"].evidence_segment_ids == ("old-0", "new-12")
        assert result["agent_metrics"]["history_searches"] == 1
        assert result["agent_metrics"]["history_results"] == 2
    finally:
        runtime.close()
        store.close()
