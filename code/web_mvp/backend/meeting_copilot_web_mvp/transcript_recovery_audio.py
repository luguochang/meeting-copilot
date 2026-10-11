"""Bounded, immutable audio ranges for explicit historical transcript recovery.

Uncovered audio may be silence. This module never starts a model, mutates a
recording, or treats an absence of transcript as proof of missing speech.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import wave

MAX_RECOVERY_SECONDS = 300
MAX_RECOVERY_WAV_BYTES = 1024 * 1024 * 1024
MAX_RECOVERY_RANGES = 64
MIN_UNCOVERED_MS = 2_000
RECOVERY_TRACKS = frozenset({"microphone", "system_audio"})


class RecoveryAudioError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryAudioError("recovery_range_invalid")
    return value


def coverage_digest(
    segments: Sequence[Mapping[str, Any]], *, track: str, start_ms: int, end_ms: int,
) -> str:
    """Fingerprint only intersecting original evidence, never its prose."""
    rows = []
    for segment in segments:
        source_track = segment.get("source_track")
        if source_track not in {None, track}:
            continue
        start, end = segment.get("started_at_ms"), segment.get("ended_at_ms")
        if isinstance(start, int) and isinstance(end, int) and (end <= start_ms or start >= end_ms):
            continue
        if source_track is None:
            raise RecoveryAudioError("recovery_coverage_unknown")
        rows.append([str(segment.get("segment_id") or ""), int(segment.get("revision") or 1),
                     str(segment.get("evidence_hash") or ""), start, end, source_track])
    return _digest(sorted(rows, key=lambda item: item[0]))


def range_plan(
    source: Mapping[str, Any], segments: Sequence[Mapping[str, Any]], *, start_sample: int, end_sample: int,
) -> dict[str, Any]:
    start, end = _int(start_sample), _int(end_sample)
    rate, samples = int(source["sample_rate_hz"]), int(source["sample_count"])
    if start < 0 or end <= start or end > samples or end - start > MAX_RECOVERY_SECONDS * rate:
        raise RecoveryAudioError("recovery_range_invalid")
    offset = int(source["timeline_offset_ms"])
    start_ms, end_ms = offset + start * 1_000 // rate, offset + (end * 1_000 + rate - 1) // rate
    return {
        "track": source["track"], "epoch": int(source["epoch"]),
        "start_sample": start, "end_sample": end, "start_ms": start_ms, "end_ms": end_ms,
        "audio_digest": source["audio_digest"],
        "coverage_digest": coverage_digest(segments, track=str(source["track"]), start_ms=start_ms, end_ms=end_ms),
    }


def build_recovery_source(
    recording: Mapping[str, Any], *, recordings: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Plan ready PCM16 WAV sources on the existing cumulative track clock."""
    track, epoch = str(recording.get("track") or ""), int(recording.get("epoch") or 0)
    meeting_id = str(recording.get("meeting_id") or "")
    if not meeting_id or any(str(item.get("meeting_id") or "") != meeting_id for item in recordings):
        raise RecoveryAudioError("recovery_timeline_unknown")
    if any(item.get("meeting_id") is not None and str(item["meeting_id"]) != meeting_id for item in segments):
        raise RecoveryAudioError("recovery_coverage_unknown")
    if track not in RECOVERY_TRACKS or recording.get("status") != "ready":
        raise RecoveryAudioError("recovery_audio_not_ready")
    rate, samples = int(recording.get("sample_rate_hz") or 0), int(recording.get("sample_count") or 0)
    output_hash = str(recording.get("output_sha256") or "")
    if not 8_000 <= rate <= 48_000 or samples <= 0 or len(output_hash) != 64:
        raise RecoveryAudioError("recovery_audio_invalid")
    file_size = int(recording.get("output_file_size_bytes") or 0)
    if file_size <= 44 or file_size > MAX_RECOVERY_WAV_BYTES:
        raise RecoveryAudioError("recovery_audio_capacity_exceeded")
    prior = sorted((item for item in recordings if item.get("track") == track
                    and int(item.get("epoch") or 0) < epoch), key=lambda item: int(item["epoch"]))
    if any(item.get("status") in {"active", "exporting"} for item in prior):
        raise RecoveryAudioError("recovery_timeline_unknown")
    clock = []
    for item in prior:
        duration, count, prior_rate = item.get("duration_ms"), item.get("sample_count"), item.get("sample_rate_hz")
        if (any(isinstance(value, bool) or not isinstance(value, int) for value in (duration, count, prior_rate))
                or duration < 0 or count < 0 or not 8_000 <= prior_rate <= 48_000
                or abs(duration - count * 1_000 / prior_rate) > 1):
            raise RecoveryAudioError("recovery_timeline_unknown")
        clock.append([int(item["epoch"]), duration, count, prior_rate])
    offset = sum(item[1] for item in clock)
    source = {
        "meeting_id": str(recording["meeting_id"]), "track": track, "epoch": epoch,
        "sample_rate_hz": rate, "sample_count": samples, "timeline_offset_ms": offset,
        "output_relative_path": str(recording.get("output_relative_path") or ""),
        "output_sha256": output_hash, "output_file_size_bytes": file_size,
        "audio_digest": _digest([recording["meeting_id"], track, epoch, rate, samples,
                                  recording.get("capture_generation"), recording.get("journal_sha256"),
                                  output_hash, file_size, str(recording.get("output_relative_path") or ""),
                                  offset, clock]),
    }
    covered = []
    for segment in segments:
        if segment.get("source_track") not in {None, track}:
            continue
        start, end = segment.get("started_at_ms"), segment.get("ended_at_ms")
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            raise RecoveryAudioError("recovery_coverage_unknown")
        left = max(0, (start - offset) * rate // 1_000)
        right = min(samples, ((end - offset) * rate + 999) // 1_000)
        if right > left:
            if segment.get("source_track") is None:
                raise RecoveryAudioError("recovery_coverage_unknown")
            covered.append((left, right))
    cursor, gaps = 0, []
    for left, right in sorted(covered):
        if left > cursor:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < samples:
        gaps.append((cursor, samples))
    ranges = []
    remaining = 0
    for left, right in gaps:
        if (right - left) * 1_000 < MIN_UNCOVERED_MS * rate:
            continue
        while left < right:
            end = min(right, left + MAX_RECOVERY_SECONDS * rate)
            if len(ranges) < MAX_RECOVERY_RANGES:
                ranges.append(range_plan(source, segments, start_sample=left, end_sample=end))
            else:
                remaining += 1
            left = end
    source["uncovered_ranges"] = ranges
    source["ranges_truncated"] = remaining > 0
    return source


def public_recovery_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """No private paths or raw transcript in the sources response."""
    return {key: source[key] for key in ("meeting_id", "track", "epoch", "sample_rate_hz", "sample_count",
            "timeline_offset_ms", "audio_digest", "uncovered_ranges", "ranges_truncated")}


def managed_recovery_path(data_dir: Path, relative: str) -> Path:
    root = data_dir.resolve()
    path = Path(relative)
    if not relative or path.is_absolute() or path.drive or ".." in path.parts or "\\" in relative or ":" in relative:
        raise RecoveryAudioError("recovery_audio_path_invalid")
    candidate = root / path
    if not candidate.is_relative_to(root):
        raise RecoveryAudioError("recovery_audio_path_invalid")
    current = candidate
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise RecoveryAudioError("recovery_audio_path_invalid")
        current = current.parent
    if not candidate.resolve().is_relative_to(root):
        raise RecoveryAudioError("recovery_audio_path_invalid")
    return candidate


def crop_verified_recovery_audio(
    *, data_dir: Path, source: Mapping[str, Any], start_sample: int, end_sample: int, destination: Path,
) -> None:
    """Hash then crop the same open file descriptor without altering source PCM."""
    start, end = _int(start_sample), _int(end_sample)
    rate, samples = int(source["sample_rate_hz"]), int(source["sample_count"])
    if start < 0 or end <= start or end > samples or end - start > MAX_RECOVERY_SECONDS * rate:
        raise RecoveryAudioError("recovery_range_invalid")
    input_path = managed_recovery_path(data_dir, str(source["output_relative_path"]))
    if not destination.resolve().is_relative_to(data_dir.resolve()) or destination.exists():
        raise RecoveryAudioError("recovery_audio_path_invalid")
    managed_recovery_path(data_dir, destination.relative_to(data_dir).as_posix())
    try:
        with input_path.open("rb") as raw:
            before = os.fstat(raw.fileno())
            if before.st_size != int(source["output_file_size_bytes"]) or before.st_size > MAX_RECOVERY_WAV_BYTES:
                raise RecoveryAudioError("recovery_audio_changed")
            digest = hashlib.sha256()
            for block in iter(lambda: raw.read(1024 * 1024), b""):
                digest.update(block)
            if digest.hexdigest() != source["output_sha256"]:
                raise RecoveryAudioError("recovery_audio_changed")
            raw.seek(0)
            with wave.open(raw, "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes(),
                    audio.getcomptype()) != (1, 2, rate, samples, "NONE"):
                    raise RecoveryAudioError("recovery_audio_invalid")
                audio.setpos(start)
                with destination.open("xb") as output_file:
                    with wave.open(output_file, "wb") as output:
                        output.setnchannels(1)
                        output.setsampwidth(2)
                        output.setframerate(rate)
                        remaining = end - start
                        while remaining:
                            count = min(32_768, remaining)
                            pcm = audio.readframes(count)
                            if len(pcm) != count * 2:
                                raise RecoveryAudioError("recovery_audio_changed")
                            output.writeframesraw(pcm)
                            remaining -= count
            after = os.fstat(raw.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RecoveryAudioError("recovery_audio_changed")
    except (OSError, wave.Error, EOFError) as exc:
        raise RecoveryAudioError("recovery_audio_unavailable") from exc
