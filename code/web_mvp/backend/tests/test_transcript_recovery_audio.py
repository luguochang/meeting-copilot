from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import wave

import pytest

from meeting_copilot_web_mvp.transcript_recovery_audio import (
    MAX_RECOVERY_WAV_BYTES,
    RecoveryAudioError,
    build_recovery_source,
    coverage_digest,
    crop_verified_recovery_audio,
    managed_recovery_path,
    public_recovery_source,
    range_plan,
)


def _recording(tmp_path: Path, *, seconds: int = 12, epoch: int = 1):
    path = tmp_path / f"epoch-{epoch}.wav"
    pcm = struct.pack("<h", 142) * (16_000 * seconds)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(pcm)
    return {"meeting_id": "synthetic-meeting", "track": "microphone", "epoch": epoch,
            "status": "ready", "sample_rate_hz": 16_000, "sample_count": 16_000 * seconds,
            "duration_ms": seconds * 1_000, "capture_generation": 1, "journal_sha256": "a" * 64,
            "output_relative_path": path.name, "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "output_file_size_bytes": path.stat().st_size}


def _segment(identity, start, end, **kwargs):
    return {"segment_id": identity, "source_track": "microphone", "started_at_ms": start,
            "ended_at_ms": end, "revision": 1, "evidence_hash": "evidence-v1", **kwargs}


def test_plan_preserves_clock_and_does_not_label_uncovered_audio_as_missing_speech(tmp_path):
    first, second = _recording(tmp_path, seconds=10), _recording(tmp_path, seconds=12, epoch=4)
    segments = [_segment("microphone:e4:1", 10_000, 13_000),
                _segment("microphone:e4:2", 12_000, 15_000),
                _segment("system:e4:1", 15_000, 22_000, source_track="system_audio")]
    source = build_recovery_source(second, recordings=[second, first], segments=segments)
    assert source["timeline_offset_ms"] == 10_000
    assert [(item["start_ms"], item["end_ms"]) for item in source["uncovered_ranges"]] == [(15_000, 22_000)]
    public = public_recovery_source(source)
    assert "output_relative_path" not in public
    assert "text" not in str(public)
    assert source["ranges_truncated"] is False


def test_uncovered_plan_is_bounded_and_fully_covered_source_has_no_candidates(tmp_path):
    recording = _recording(tmp_path, seconds=301)
    source = build_recovery_source(recording, recordings=[recording], segments=[])
    assert [(item["start_sample"], item["end_sample"]) for item in source["uncovered_ranges"]] == [
        (0, 300 * 16_000), (300 * 16_000, 301 * 16_000)]
    covered = build_recovery_source(recording, recordings=[recording],
                                    segments=[_segment("covered", 0, 301_000)])
    assert covered["uncovered_ranges"] == []


def test_source_plans_existing_six_hour_wave_metadata_without_loading_pcm(tmp_path):
    recording = {**_recording(tmp_path), "sample_count": 345_875_200,
                 "duration_ms": 21_617_200, "output_file_size_bytes": 691_750_444}
    source = build_recovery_source(recording, recordings=[recording], segments=[])
    assert source["sample_count"] == 345_875_200
    assert len(source["uncovered_ranges"]) == 64
    assert source["ranges_truncated"] is True
    assert all(item["end_sample"] - item["start_sample"] <= 300 * 16_000 for item in source["uncovered_ranges"])


def test_coverage_revision_changes_but_other_tracks_and_later_ranges_do_not():
    arguments = {"track": "microphone", "start_ms": 4_000, "end_ms": 9_000}
    base = [_segment("inside", 4_000, 5_000)]
    original = coverage_digest(base, **arguments)
    assert coverage_digest([_segment("inside", 4_000, 5_000, revision=2)], **arguments) != original
    assert coverage_digest([*base, _segment("later", 9_000, 13_000),
                            _segment("other", 4_000, 5_000, source_track="system_audio")], **arguments) == original


def test_audio_identity_includes_prior_epoch_clock(tmp_path):
    first, second = _recording(tmp_path, seconds=10), _recording(tmp_path, epoch=2)
    source = build_recovery_source(second, recordings=[first, second], segments=[])
    changed = build_recovery_source(second, recordings=[{**first, "duration_ms": 11_000,
                                                         "sample_count": 11 * 16_000}, second], segments=[])
    assert source["audio_digest"] != changed["audio_digest"]


@pytest.mark.parametrize("patch", [{"duration_ms": None}, {"duration_ms": 13_000},
                                   {"sample_count": None}, {"sample_rate_hz": None}])
def test_prior_epoch_clock_requires_consistent_sample_mapping(tmp_path, patch):
    first, second = _recording(tmp_path), _recording(tmp_path, epoch=2)
    with pytest.raises(RecoveryAudioError, match="recovery_timeline_unknown"):
        build_recovery_source(second, recordings=[{**first, **patch}, second], segments=[])


def test_unknown_track_cannot_simultaneously_cover_both_tracks(tmp_path):
    microphone = _recording(tmp_path)
    system = {**microphone, "track": "system_audio"}
    segments = [_segment("legacy", 0, 12_000, source_track=None)]
    for recording in [microphone, system]:
        with pytest.raises(RecoveryAudioError, match="recovery_coverage_unknown"):
            build_recovery_source(recording, recordings=[microphone, system], segments=segments)
    with pytest.raises(RecoveryAudioError, match="recovery_coverage_unknown"):
        coverage_digest(segments, track="microphone", start_ms=1_000, end_ms=2_000)
    source = build_recovery_source(microphone, recordings=[microphone, system],
                                   segments=[_segment("old", 20_000, 22_000, source_track=None)])
    assert source["uncovered_ranges"]


def test_source_identity_binds_owned_path_and_rejects_foreign_meeting_inputs(tmp_path):
    recording = _recording(tmp_path)
    original = build_recovery_source(recording, recordings=[recording], segments=[])
    changed = build_recovery_source({**recording, "output_relative_path": "another.wav"},
                                    recordings=[recording], segments=[])
    assert changed["audio_digest"] != original["audio_digest"]
    with pytest.raises(RecoveryAudioError, match="recovery_timeline_unknown"):
        build_recovery_source(recording, recordings=[{**recording, "meeting_id": "another"}], segments=[])
    with pytest.raises(RecoveryAudioError, match="recovery_coverage_unknown"):
        build_recovery_source(recording, recordings=[recording],
                               segments=[_segment("foreign", 0, 2_000, meeting_id="another")])


@pytest.mark.parametrize("patch,code", [({"status": "active"}, "recovery_audio_not_ready"),
    ({"output_file_size_bytes": MAX_RECOVERY_WAV_BYTES + 1}, "recovery_audio_capacity_exceeded"),
    ({"output_sha256": ""}, "recovery_audio_invalid")])
def test_source_rejects_active_unverified_or_oversized_audio(tmp_path, patch, code):
    recording = {**_recording(tmp_path), **patch}
    with pytest.raises(RecoveryAudioError, match=code):
        build_recovery_source(recording, recordings=[recording], segments=[])


def test_unknown_coverage_and_active_prior_clock_do_not_get_guessed(tmp_path):
    first, second = _recording(tmp_path), _recording(tmp_path, epoch=2)
    for status in ["active", "exporting"]:
        with pytest.raises(RecoveryAudioError, match="recovery_timeline_unknown"):
            build_recovery_source(second, recordings=[{**first, "status": status}, second], segments=[])
    with pytest.raises(RecoveryAudioError, match="recovery_coverage_unknown"):
        build_recovery_source(second, recordings=[second], segments=[_segment("unknown", None, None)])


@pytest.mark.parametrize("start,end", [(-1, 2), (1, 1), (0, 999_999), (True, 16_000)])
def test_range_rejects_negative_empty_out_of_recording_or_boolean_samples(tmp_path, start, end):
    recording = _recording(tmp_path)
    source = build_recovery_source(recording, recordings=[recording], segments=[])
    with pytest.raises(RecoveryAudioError, match="recovery_range_invalid"):
        range_plan(source, [], start_sample=start, end_sample=end)


@pytest.mark.parametrize("relative", ["../outside.wav", "a/../../outside.wav", "C:relative.wav", "a\\file.wav",
                                      "audio.wav:stream"])
def test_managed_path_rejects_escape_drive_relative_and_windows_separator(tmp_path, relative):
    with pytest.raises(RecoveryAudioError, match="recovery_audio_path_invalid"):
        managed_recovery_path(tmp_path, relative)


def test_exact_crop_preserves_original_hash_and_source_sample_count(tmp_path):
    recording = _recording(tmp_path)
    source = build_recovery_source(recording, recordings=[recording], segments=[])
    destination = tmp_path / "candidate.wav"
    crop_verified_recovery_audio(data_dir=tmp_path, source=source, start_sample=16_000,
                                 end_sample=48_000, destination=destination)
    with wave.open(str(destination), "rb") as cropped:
        assert cropped.getnframes() == 32_000
        assert cropped.readframes(32_000) == struct.pack("<h", 142) * 32_000
    assert hashlib.sha256((tmp_path / recording["output_relative_path"]).read_bytes()).hexdigest() == source["output_sha256"]


def test_modified_wave_is_rejected_before_destination_is_written(tmp_path):
    recording = _recording(tmp_path)
    source = build_recovery_source(recording, recordings=[recording], segments=[])
    path = tmp_path / recording["output_relative_path"]
    content = bytearray(path.read_bytes())
    content[-1] ^= 1
    path.write_bytes(content)
    destination = tmp_path / "candidate.wav"
    with pytest.raises(RecoveryAudioError, match="recovery_audio_changed"):
        crop_verified_recovery_audio(data_dir=tmp_path, source=source, start_sample=0,
                                     end_sample=16_000, destination=destination)
    assert not destination.exists()


def test_existing_destination_cannot_be_overwritten(tmp_path):
    recording = _recording(tmp_path)
    source = build_recovery_source(recording, recordings=[recording], segments=[])
    destination = tmp_path / "candidate.wav"
    destination.write_bytes(b"user-content")
    with pytest.raises(RecoveryAudioError, match="recovery_audio_path_invalid"):
        crop_verified_recovery_audio(data_dir=tmp_path, source=source, start_sample=0,
                                     end_sample=16_000, destination=destination)
    assert destination.read_bytes() == b"user-content"
