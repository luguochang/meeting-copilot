from __future__ import annotations

from pathlib import Path

from .audio_assets import inspect_realtime_audio_journal, resolve_realtime_recording_layout
from .v2_persistence import RecordingRecoveryConflict, V2Persistence


def _reconcile_recording_journals(
    persistence: V2Persistence,
    *,
    data_dir: str | Path,
    recordings: list[dict[str, object]],
    now_ms: int,
    require_expired_lease: bool,
) -> None:
    root = Path(data_dir)
    for recording in recordings:
        meeting_id = str(recording["meeting_id"])
        try:
            committed = [chunk for chunk in persistence.list_audio_chunks(meeting_id)
                         if chunk["track"] == recording["track"] and int(chunk["epoch"]) == int(recording["epoch"])]
            by_sequence = {int(chunk["chunk_seq"]): chunk for chunk in committed}
            layout = resolve_realtime_recording_layout(recording=recording, chunks=committed, data_dir=root)
            if not (root / str(layout["output_relative_path"])).parent.exists():
                continue
            journal = inspect_realtime_audio_journal(
                data_dir=root,
                session_id=meeting_id,
                sample_rate_hz=int(recording["sample_rate_hz"]),
                track_id=layout["track_id"],
                epoch=int(recording["epoch"]),
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
                    captured_at_ms=by_sequence.get(int(chunk["chunk_seq"]), {}).get("timestamp_ms", chunk.get("timestamp_ms")),
                    source_sequence_start=chunk.get("source_sequence_start"),
                    source_sequence_end=chunk.get("source_sequence_end"),
                    source_timestamp_start_ms=chunk.get("source_timestamp_start_ms"),
                    source_timestamp_end_ms=chunk.get("source_timestamp_end_ms"),
                    expected_capture_generation=int(recording["capture_generation"]),
                    **(
                        {"require_lease_expired_at_ms": now_ms}
                        if require_expired_lease
                        else {"expected_capture_lease_owner": str(recording["lease_owner"])}
                    ),
                )
        except (OSError, ValueError, RecordingRecoveryConflict):
            continue


def reconcile_and_recover_expired_recordings(
    persistence: V2Persistence,
    *,
    data_dir: str | Path,
    now_ms: int,
) -> list[str]:
    """Reconcile fsynced PCM journals before expiring their capture leases."""

    now_ms = max(0, int(now_ms))
    expired = persistence.list_expired_recording_sessions(now_ms=now_ms)
    _reconcile_recording_journals(
        persistence,
        data_dir=data_dir,
        recordings=expired,
        now_ms=now_ms,
        require_expired_lease=True,
    )

    return persistence.recover_expired_recording_leases(now_ms=now_ms)


def reconcile_and_recover_abandoned_recordings(
    persistence: V2Persistence,
    *,
    data_dir: str | Path,
    now_ms: int,
) -> list[str]:
    """Fence captures left active when the previous packaged backend died."""

    now_ms = max(0, int(now_ms))
    active = persistence.list_active_recording_sessions()
    _reconcile_recording_journals(
        persistence,
        data_dir=data_dir,
        recordings=active,
        now_ms=now_ms,
        require_expired_lease=False,
    )
    return persistence.recover_abandoned_recording_leases(
        expected_recordings=active,
        now_ms=now_ms,
    )
