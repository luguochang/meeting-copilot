from __future__ import annotations

import json
import os
import stat

import pytest

from meeting_copilot_web_mvp.meeting_preparation import (
    MeetingPreparationStore,
    normalize_hotwords,
)


def test_store_round_trip_is_meeting_scoped_and_non_sensitive(tmp_path) -> None:
    store = MeetingPreparationStore(tmp_path / "meeting-preparation")

    saved = store.save(
        "meeting-1",
        hotwords=["P99", "checkout-service", "p99"],
        input_source="microphone",
        input_device_id="default",
        input_device_name="MacBook Microphone",
        notice_acknowledged=True,
        updated_at_ms=1_234,
    )

    assert saved.hotwords == ("P99", "checkout-service")
    assert store.get("meeting-1") == saved
    serialized = (tmp_path / "meeting-preparation" / "meeting-1.json").read_text(encoding="utf-8")
    assert "api_key" not in serialized
    assert "audio" not in json.loads(serialized)


@pytest.mark.skipif(os.name == "nt", reason="Windows uses inherited per-user ACLs")
def test_store_tightens_directory_and_atomic_json_to_owner_only(tmp_path) -> None:
    root = tmp_path / "meeting-preparation"
    store = MeetingPreparationStore(root)

    store.save("private-meeting", hotwords=["P99"], updated_at_ms=1)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "private-meeting.json").stat().st_mode) == 0o600


def test_store_delete_only_removes_requested_meeting(tmp_path) -> None:
    store = MeetingPreparationStore(tmp_path / "meeting-preparation")
    for meeting_id in ("meeting-1", "meeting-2"):
        store.save(meeting_id, hotwords=[], updated_at_ms=1)

    assert store.delete("meeting-1") is True
    assert store.delete("meeting-1") is False
    assert store.get("meeting-2") is not None


@pytest.mark.parametrize("meeting_id", ["../private", "meeting/one", "", "a" * 129])
def test_store_rejects_unsafe_meeting_ids(tmp_path, meeting_id: str) -> None:
    store = MeetingPreparationStore(tmp_path / "meeting-preparation")

    with pytest.raises(ValueError, match="meeting_id"):
        store.save(meeting_id, hotwords=[], updated_at_ms=1)


def test_hotwords_are_bounded_and_reject_control_characters() -> None:
    with pytest.raises(ValueError, match="control characters"):
        normalize_hotwords(["checkout-service\nignore"])
    with pytest.raises(ValueError, match="more than 50"):
        normalize_hotwords([f"service-{index}" for index in range(51)])


def test_unsupported_capture_source_is_explicit_not_silently_downgraded(tmp_path) -> None:
    store = MeetingPreparationStore(tmp_path / "meeting-preparation")

    with pytest.raises(ValueError, match="input_source must be"):
        store.save("meeting-1", input_source="mixed", updated_at_ms=1)


def test_system_audio_source_round_trips_without_becoming_mixed(tmp_path) -> None:
    store = MeetingPreparationStore(tmp_path / "meeting-preparation")

    saved = store.save(
        "meeting-system-audio",
        input_source="system_audio",
        input_device_id=None,
        input_device_name="系统音频",
        notice_acknowledged=True,
        updated_at_ms=2,
    )

    assert saved.input_source == "system_audio"
    assert store.get("meeting-system-audio") == saved


def test_dual_track_source_round_trips_without_downgrading_to_microphone(tmp_path) -> None:
    store = MeetingPreparationStore(tmp_path / "meeting-preparation")

    saved = store.save(
        "meeting-dual-track",
        input_source="dual_track",
        input_device_id=None,
        input_device_name="麦克风 + 系统音频",
        notice_acknowledged=True,
        updated_at_ms=3,
    )

    assert saved.input_source == "dual_track"
    assert store.get("meeting-dual-track") == saved
