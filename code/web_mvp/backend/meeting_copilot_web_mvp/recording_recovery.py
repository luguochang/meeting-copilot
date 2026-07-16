from __future__ import annotations

from pathlib import Path

from .audio_assets import inspect_realtime_audio_journal
from .v2_persistence import RecordingRecoveryConflict, V2Persistence


def reconcile_and_recover_expired_recordings(
    persistence: V2Persistence,
    *,
    data_dir: str | Path,
    now_ms: int,
) -> list[str]:
    """Reconcile fsynced PCM journals before expiring their capture leases."""

    root = Path(data_dir)
    now_ms = max(0, int(now_ms))
    expired = persistence.list_expired_recording_sessions(now_ms=now_ms)
    for recording in expired:
        meeting_id = str(recording["meeting_id"])
        session_dir = root / "audio_assets" / meeting_id
        if not session_dir.exists():
            continue
        try:
            journal = inspect_realtime_audio_journal(
                data_dir=root,
                session_id=meeting_id,
                sample_rate_hz=int(recording["sample_rate_hz"]),
            )
            for chunk in journal["chunks"]:
                persistence.record_audio_chunk(
                    meeting_id=meeting_id,
                    track=str(recording["track"]),
                    epoch=int(recording["epoch"]),
                    chunk_seq=int(chunk["chunk_seq"]),
                    relative_path=str(chunk["relative_path"]),
                    sha256=str(chunk["sha256"]),
                    sample_rate_hz=int(chunk["sample_rate_hz"]),
                    sample_count=int(chunk["sample_count"]),
                    duration_ms=int(chunk["duration_ms"]),
                    file_size_bytes=int(chunk["file_size_bytes"]),
                    now_ms=now_ms,
                    expected_capture_generation=int(recording["capture_generation"]),
                    require_lease_expired_at_ms=now_ms,
                )
        except (OSError, ValueError, RecordingRecoveryConflict):
            continue

    return persistence.recover_expired_recording_leases(now_ms=now_ms)
