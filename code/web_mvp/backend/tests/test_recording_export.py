from __future__ import annotations

import asyncio
import struct
import wave

from meeting_copilot_web_mvp.audio_assets import (
    RealtimeWavAssetWriter,
    assemble_realtime_wav_asset,
)
from meeting_copilot_web_mvp.recording_export import RecordingExportExecutor
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _float32_payload(sample_count: int, value: float = 0.2) -> bytes:
    return struct.pack("<f", value) * sample_count


def test_background_recording_export_assembles_and_commits_ready_state(tmp_path):
    persistence = V2Persistence(tmp_path / "meeting_copilot.db")
    meeting_id = "background-export"
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
    persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=100,
        lease_owner="capture-worker",
        lease_ms=10_000,
        now_ms=1_100,
    )
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id=meeting_id,
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
        on_chunk_committed=lambda chunk: persistence.record_audio_chunk(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            chunk_seq=int(chunk["chunk_index"]),
            relative_path=str(chunk["relative_path"]),
            sha256=str(chunk["sha256"]),
            sample_rate_hz=int(chunk["sample_rate_hz"]),
            sample_count=int(chunk["sample_count"]),
            duration_ms=int(chunk["duration_ms"]),
            file_size_bytes=int(chunk["file_size_bytes"]),
            lease_owner="capture-worker",
            lease_ms=10_000,
            now_ms=1_200,
        ),
    )
    writer.write_float32_pcm(_float32_payload(23))
    sealed = writer.seal()
    queued = persistence.seal_recording_and_enqueue_export(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        lease_owner="capture-worker",
        output_relative_path=str(sealed["relative_path"]),
        interrupted=False,
        now_ms=1_300,
    )

    def export(job):
        return assemble_realtime_wav_asset(
            data_dir=tmp_path,
            session_id=str(job["meeting_id"]),
            source_type="browser_live_mic",
            sample_rate_hz=100,
        )

    async def run() -> None:
        executor = RecordingExportExecutor(
            persistence,
            export_handler=export,
            worker_id="export-worker",
            poll_interval_ms=5,
        )
        await executor.start()
        executor.wake()
        try:
            async with asyncio.timeout(2):
                while persistence.list_recording_exports(meeting_id=meeting_id)[0]["status"] != "succeeded":
                    await asyncio.sleep(0.005)
        finally:
            await executor.stop(timeout_s=1)

    asyncio.run(run())

    export_job = persistence.list_recording_exports(meeting_id=meeting_id)[0]
    recording = persistence.get_recording_session(meeting_id, track="microphone", epoch=0)
    audio_path = tmp_path / "audio_assets" / meeting_id / "audio.wav"
    with wave.open(str(audio_path), "rb") as wav_file:
        assert wav_file.getnframes() == 23

    assert export_job["id"] == queued["export"]["id"]
    assert export_job["status"] == "succeeded"
    assert export_job["attempts"] == 1
    assert recording["status"] == "ready"
    assert recording["output_sha256"] == export_job["output"]["sha256"]
    persistence.close()
