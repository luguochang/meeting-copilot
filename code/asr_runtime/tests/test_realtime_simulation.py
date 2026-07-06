import subprocess
import sys
import json
from pathlib import Path

from scripts.incremental_scheduler import AnalysisSchedulerConfig
from scripts.realtime_simulation import run_realtime_simulation
from scripts.streaming_contract import StreamingTranscriptEvent
from scripts.transcript_report import build_report


def test_realtime_simulation_builds_provider_transcript_and_llm_triggers():
    result = run_realtime_simulation(
        provider="mock-stream",
        events=[
            StreamingTranscriptEvent(
                event_type="partial",
                segment_id="seg_a",
                text="先灰度",
                start_ms=0,
                end_ms=800,
                received_at_ms=900,
            ),
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_a",
                text="先灰度 10%",
                start_ms=0,
                end_ms=2200,
                received_at_ms=2300,
            ),
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_b",
                text="如果错误率超过 0.1% 就回滚",
                start_ms=2200,
                end_ms=5200,
                received_at_ms=7000,
                confidence=0.88,
            ),
        ],
        scheduler_config=AnalysisSchedulerConfig(min_final_interval_ms=10_000),
    )

    assert result.provider_transcript["segments"] == [
        {
            "id": "seg_a",
            "start_ms": 0,
            "end_ms": 2200,
            "text": "先灰度 10%",
            "confidence": None,
            "is_final": True,
        },
        {
            "id": "seg_b",
            "start_ms": 2200,
            "end_ms": 5200,
            "text": "如果错误率超过 0.1% 就回滚",
            "confidence": 0.88,
            "is_final": True,
        },
    ]
    assert [decision.reason for decision in result.decisions] == [
        "partial_ignored",
        "final_segment",
        "cooldown",
    ]
    assert result.llm_trigger_count == 1

    report = build_report(
        audio_path="stream://mock",
        provider="mock-stream",
        text=result.provider_transcript["text"],
        duration_seconds=5.2,
        latency_ms=result.provider_transcript["latency_ms"],
        provider_segments=result.provider_transcript["segments"],
    )

    assert [span.quote for span in report.evidence_spans] == [
        "先灰度 10%",
        "如果错误率超过 0.1% 就回滚",
    ]


def test_realtime_simulation_flags_state_change_as_llm_trigger():
    result = run_realtime_simulation(
        provider="mock-stream",
        events=[
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_a",
                text="先灰度 10%",
                start_ms=0,
                end_ms=2200,
                received_at_ms=1000,
            ),
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_b",
                text="这里还没有确认回滚负责人",
                start_ms=2200,
                end_ms=5200,
                received_at_ms=12_000,
            ),
        ],
        scheduler_config=AnalysisSchedulerConfig(
            min_final_interval_ms=30_000,
            min_state_change_interval_ms=10_000,
        ),
        state_change_segment_ids={"seg_b"},
    )

    assert [decision.reason for decision in result.decisions] == [
        "final_segment",
        "state_change",
    ]
    assert result.llm_trigger_count == 2


def test_realtime_simulation_flags_revision_state_change_by_revised_segment_id():
    result = run_realtime_simulation(
        provider="mock-stream",
        events=[
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_rollout",
                text="先灰度 10%",
                start_ms=0,
                end_ms=2200,
                received_at_ms=1000,
            ),
            StreamingTranscriptEvent(
                event_type="revision",
                segment_id="seg_metric_rev",
                revision_of="seg_metric",
                text="如果错误率超过 0.1% 就回滚",
                start_ms=2200,
                end_ms=5200,
                received_at_ms=12_000,
            ),
        ],
        scheduler_config=AnalysisSchedulerConfig(
            min_final_interval_ms=30_000,
            min_state_change_interval_ms=10_000,
        ),
        state_change_segment_ids={"seg_metric"},
    )

    assert [decision.reason for decision in result.decisions] == [
        "final_segment",
        "state_change",
    ]
    assert result.llm_trigger_count == 2


def test_realtime_simulation_cli_runs_as_direct_script():
    result = subprocess.run(
        [sys.executable, "scripts/realtime_simulation.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Run a mock realtime transcript simulation" in result.stdout


def test_realtime_simulation_cli_outputs_scheduler_summary(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "event_type": "partial",
                    "segment_id": "seg_a",
                    "text": "先灰度",
                    "start_ms": 0,
                    "end_ms": 800,
                    "received_at_ms": 900,
                },
                {
                    "event_type": "final",
                    "segment_id": "seg_a",
                    "text": "先灰度 10%",
                    "start_ms": 0,
                    "end_ms": 2200,
                    "received_at_ms": 2300,
                },
                {
                    "event_type": "final",
                    "segment_id": "seg_b",
                    "text": "如果错误率超过 0.1% 就回滚",
                    "start_ms": 2200,
                    "end_ms": 5200,
                    "received_at_ms": 7000,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/realtime_simulation.py",
            "--events-json",
            str(events_path),
            "--min-final-interval-ms",
            "10000",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["llm_trigger_count"] == 1
    assert [item["reason"] for item in payload["decisions"]] == [
        "partial_ignored",
        "final_segment",
        "cooldown",
    ]
    assert len(payload["provider_transcript"]["segments"]) == 2


def test_realtime_simulation_cli_runs_checked_in_mock_events():
    events_path = Path("../../configs/asr_providers/mock-streaming-events.release-review.json")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/realtime_simulation.py",
            "--events-json",
            str(events_path),
            "--min-final-interval-ms",
            "30000",
            "--min-state-change-interval-ms",
            "10000",
            "--state-change-segment-id",
            "seg_owner_gap",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["llm_trigger_count"] == 2
    assert [item["reason"] for item in payload["decisions"]] == [
        "partial_ignored",
        "final_segment",
        "partial_ignored",
        "cooldown",
        "state_change",
        "cooldown",
    ]
    assert [item["id"] for item in payload["provider_transcript"]["segments"]] == [
        "seg_rollout",
        "seg_metric",
        "seg_owner_gap",
    ]
