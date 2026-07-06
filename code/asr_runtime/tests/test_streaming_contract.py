from scripts.streaming_contract import (
    StreamingTranscriptEvent,
    build_provider_transcript_from_stream,
)


def test_streaming_contract_uses_only_final_events_as_segments():
    transcript = build_provider_transcript_from_stream(
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
                confidence=0.91,
            ),
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_b",
                text="如果错误率超过 0.1% 就回滚",
                start_ms=2200,
                end_ms=5200,
                received_at_ms=5400,
            ),
        ],
    )

    assert transcript["text"] == "先灰度 10%如果错误率超过 0.1% 就回滚"
    assert transcript["latency_ms"] == 5400
    assert transcript["segments"] == [
        {
            "id": "seg_a",
            "start_ms": 0,
            "end_ms": 2200,
            "text": "先灰度 10%",
            "confidence": 0.91,
            "is_final": True,
        },
        {
            "id": "seg_b",
            "start_ms": 2200,
            "end_ms": 5200,
            "text": "如果错误率超过 0.1% 就回滚",
            "confidence": None,
            "is_final": True,
        },
    ]
    assert transcript["raw"]["partial_event_count"] == 1
    assert transcript["raw"]["final_event_count"] == 2


def test_streaming_contract_applies_revision_to_existing_final_segment():
    transcript = build_provider_transcript_from_stream(
        provider="mock-stream",
        events=[
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_a",
                text="先挥 10%",
                start_ms=0,
                end_ms=2200,
                received_at_ms=2300,
            ),
            StreamingTranscriptEvent(
                event_type="revision",
                segment_id="seg_a",
                text="先灰度 10%",
                start_ms=0,
                end_ms=2300,
                received_at_ms=4000,
                revision_of="seg_a",
            ),
        ],
    )

    assert transcript["text"] == "先灰度 10%"
    assert transcript["latency_ms"] == 4000
    assert transcript["segments"] == [
        {
            "id": "seg_a",
            "start_ms": 0,
            "end_ms": 2300,
            "text": "先灰度 10%",
            "confidence": None,
            "is_final": True,
            "revision_of": "seg_a",
        }
    ]
    assert transcript["raw"]["revision_event_count"] == 1


def test_streaming_contract_rejects_unknown_event_type():
    try:
        build_provider_transcript_from_stream(
            provider="mock-stream",
            events=[
                StreamingTranscriptEvent(
                    event_type="draft",
                    segment_id="seg_a",
                    text="先灰度",
                    start_ms=0,
                    end_ms=800,
                    received_at_ms=900,
                )
            ],
        )
    except ValueError as exc:
        assert "unsupported streaming event_type: draft" in str(exc)
    else:
        raise AssertionError("expected unknown streaming event type to be rejected")


def test_streaming_contract_records_error_and_end_of_stream_events():
    transcript = build_provider_transcript_from_stream(
        provider="mock-stream",
        events=[
            StreamingTranscriptEvent(
                event_type="final",
                segment_id="seg_a",
                text="先灰度 10%",
                start_ms=0,
                end_ms=2200,
                received_at_ms=2300,
            ),
            StreamingTranscriptEvent(
                event_type="error",
                segment_id="err_001",
                text="provider timeout",
                start_ms=2200,
                end_ms=2200,
                received_at_ms=2600,
            ),
            StreamingTranscriptEvent(
                event_type="end_of_stream",
                segment_id="eos_001",
                text="",
                start_ms=2200,
                end_ms=2200,
                received_at_ms=2700,
            ),
        ],
    )

    assert transcript["segments"] == [
        {
            "id": "seg_a",
            "start_ms": 0,
            "end_ms": 2200,
            "text": "先灰度 10%",
            "confidence": None,
            "is_final": True,
        }
    ]
    assert transcript["raw"]["error_event_count"] == 1
    assert transcript["raw"]["end_of_stream_event_count"] == 1
    assert transcript["raw"]["errors"] == [
        {"id": "err_001", "message": "provider timeout", "received_at_ms": 2600}
    ]


def test_streaming_contract_rejects_empty_final_segments():
    try:
        build_provider_transcript_from_stream(
            provider="mock-stream",
            events=[
                StreamingTranscriptEvent(
                    event_type="final",
                    segment_id="seg_empty",
                    text="   ",
                    start_ms=0,
                    end_ms=1000,
                    received_at_ms=1100,
                )
            ],
        )
    except ValueError as exc:
        assert "empty final/revision text" in str(exc)
    else:
        raise AssertionError("expected empty final to be rejected")


def test_streaming_contract_rejects_invalid_timestamps():
    try:
        build_provider_transcript_from_stream(
            provider="mock-stream",
            events=[
                StreamingTranscriptEvent(
                    event_type="final",
                    segment_id="seg_bad_time",
                    text="先灰度 10%",
                    start_ms=2000,
                    end_ms=1000,
                    received_at_ms=2100,
                )
            ],
        )
    except ValueError as exc:
        assert "end_ms must be >= start_ms" in str(exc)
    else:
        raise AssertionError("expected reversed timestamp range to be rejected")


def test_streaming_contract_rejects_invalid_confidence():
    try:
        build_provider_transcript_from_stream(
            provider="mock-stream",
            events=[
                StreamingTranscriptEvent(
                    event_type="final",
                    segment_id="seg_bad_confidence",
                    text="先灰度 10%",
                    start_ms=0,
                    end_ms=1000,
                    received_at_ms=1100,
                    confidence=1.5,
                )
            ],
        )
    except ValueError as exc:
        assert "confidence must be between 0 and 1" in str(exc)
    else:
        raise AssertionError("expected invalid confidence to be rejected")
