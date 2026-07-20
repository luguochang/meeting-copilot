from fake_asr_backend import ScriptedChineseMeetingRecognizer


def test_scripted_recognizer_emits_two_ordered_positive_duration_segments():
    recognizer = ScriptedChineseMeetingRecognizer("timestamp-contract")

    events = [recognizer.recognize_chunk(b"pcm")[0] for _ in range(4)]

    assert [(event["start_ms"], event["end_ms"]) for event in events] == [
        (0, 300),
        (0, 600),
        (6_000, 6_300),
        (6_000, 6_600),
    ]
    assert all(event["start_ms"] < event["end_ms"] for event in events)
    assert [event["segment_id"] for event in events if event["event_type"] == "final"] == [
        "scripted_segment_1",
        "scripted_segment_2",
    ]
    assert recognizer.finalize() == []


def test_scripted_recognizer_finalize_reuses_deterministic_segment_windows():
    recognizer = ScriptedChineseMeetingRecognizer("early-finalize-contract")
    recognizer.recognize_chunk(b"pcm")

    final_events = recognizer.finalize()

    assert [
        (event["segment_id"], event["start_ms"], event["end_ms"])
        for event in final_events
    ] == [
        ("scripted_segment_1", 0, 600),
        ("scripted_segment_2", 6_000, 6_600),
    ]
    assert all(event["start_ms"] < event["end_ms"] for event in final_events)
