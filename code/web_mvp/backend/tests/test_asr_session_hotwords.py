from __future__ import annotations

from meeting_copilot_web_mvp import asr_stream


def test_session_hotwords_are_isolated_and_clearable() -> None:
    asr_stream.clear_session_hotwords("meeting-one")
    asr_stream.clear_session_hotwords("meeting-two")

    assert asr_stream.set_session_hotwords(
        "meeting-one",
        ["P99", "checkout-service", "p99"],
    ) == ("P99", "checkout-service")
    assert asr_stream.session_hotwords("meeting-one") == ("P99", "checkout-service")
    assert asr_stream.session_hotwords("meeting-two") == ()

    asr_stream.clear_session_hotwords("meeting-one")
    assert asr_stream.session_hotwords("meeting-one") == ()


def test_funasr_command_merges_default_and_meeting_hotwords(monkeypatch) -> None:
    monkeypatch.setattr(asr_stream, "_hotwords", lambda: ["P99", "Redis"])

    command = asr_stream._funasr_worker_command(
        session_hotword_values=("checkout-service", "P99"),
    )

    hotword_index = command.index("--hotwords")
    assert command[hotword_index + 1] == "P99 Redis checkout-service"


def test_custom_hotwords_reuse_resident_worker_without_cross_meeting_leak(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Recognizer:
        pass

    recognizer = Recognizer()

    class Manager:
        def create_session(self, session_id, *, hotwords=()):
            captured.update({"session_id": session_id, "hotwords": hotwords})
            return recognizer

    monkeypatch.setattr(asr_stream, "funasr_realtime_available", lambda: True)
    monkeypatch.setattr(asr_stream, "_funasr_resident_enabled", lambda: True)
    monkeypatch.setattr(
        asr_stream,
        "_get_funasr_resident_manager",
        lambda: Manager(),
    )
    asr_stream.set_session_hotwords("meeting-hotword", ["订单中台"])

    result = asr_stream._maybe_funasr_sidecar("meeting-hotword")

    assert result is recognizer
    assert captured == {
        "session_id": "meeting-hotword",
        "hotwords": ("订单中台",),
    }
    asr_stream.clear_session_hotwords("meeting-hotword")
