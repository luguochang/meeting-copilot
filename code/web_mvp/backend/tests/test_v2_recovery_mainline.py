from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import wave

from meeting_copilot_web_mvp.audio_assets import (
    DEFAULT_CHUNK_DURATION_SECONDS,
    SAMPLE_RATE_HZ,
    RealtimeWavAssetWriter,
    assemble_realtime_wav_asset,
)
from meeting_copilot_web_mvp.recording_export import RecordingExportExecutor
from meeting_copilot_web_mvp.v2_persistence import V2Persistence
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor


MEETING_ID = "phase2-recovery-mainline"
SOURCE_TYPE = "browser_live_mic"


def _float32_payload(duration_seconds: int, *, value: float) -> bytes:
    sample = struct.pack("<f", value)
    return sample * (SAMPLE_RATE_HZ * duration_seconds)


def _chunk_recorder(
    persistence: V2Persistence,
    *,
    now_ms: int,
) -> Callable[[dict[str, object]], None]:
    def record(chunk: dict[str, object]) -> None:
        persistence.record_audio_chunk(
            meeting_id=MEETING_ID,
            track="microphone",
            epoch=0,
            chunk_seq=int(chunk["chunk_index"]),
            relative_path=str(chunk["relative_path"]),
            sha256=str(chunk["sha256"]),
            sample_rate_hz=int(chunk["sample_rate_hz"]),
            sample_count=int(chunk["sample_count"]),
            duration_ms=int(chunk["duration_ms"]),
            file_size_bytes=int(chunk["file_size_bytes"]),
            now_ms=now_ms,
        )

    return record


async def _complete_jobs_with_reopened_executor(
    persistence: V2Persistence,
    job_ids: set[str],
) -> set[tuple[str, str]]:
    handled: set[tuple[str, str]] = set()

    async def handle(job: dict) -> dict[str, str]:
        handled.add((str(job["kind"]), str(job["id"])))
        return {"executor": "reopened-backend", "job_id": str(job["id"])}

    executor = DurableJobExecutor(
        persistence,
        correction_handler=handle,
        suggestion_handler=handle,
        worker_id="phase2-reopened",
        poll_interval_ms=5,
        now_ms=lambda: 3_000,
    )
    await executor.start()
    try:
        async with asyncio.timeout(2.0):
            while any(
                persistence.get_job(job_id)["status"] != "succeeded"
                for job_id in job_ids
            ):
                await asyncio.sleep(0.005)
    finally:
        await executor.stop(timeout_s=1.0)
    return handled


async def _export_recovered_recording(
    persistence: V2Persistence,
    *,
    data_dir: Path,
    meeting_id: str,
) -> dict:
    def export(job: dict) -> dict:
        recording = persistence.get_recording_session(
            meeting_id,
            track=str(job["track"]),
            epoch=int(job["epoch"]),
        )
        return assemble_realtime_wav_asset(
            data_dir=data_dir,
            session_id=meeting_id,
            source_type=str(recording["source_type"]),
            sample_rate_hz=int(recording["sample_rate_hz"]),
            expected_chunk_count=int(job["input_chunk_count"]),
            expected_sample_count=int(job["input_sample_count"]),
            expected_journal_sha256=str(job["input_journal_sha256"]),
        )

    executor = RecordingExportExecutor(
        persistence,
        export_handler=export,
        worker_id="phase2-recording-export",
        poll_interval_ms=5,
        now_ms=lambda: 3_000,
    )
    await executor.start()
    executor.wake()
    try:
        async with asyncio.timeout(2):
            while True:
                exports = persistence.list_recording_exports(meeting_id=meeting_id)
                if exports and exports[0]["status"] == "succeeded":
                    return exports[0]
                await asyncio.sleep(0.005)
    finally:
        await executor.stop(timeout_s=1)


def test_v2_recovery_mainline_bounds_audio_rpo_and_completes_durable_jobs(tmp_path):
    data_dir = tmp_path / "runtime"
    database_path = data_dir / "meeting_copilot.db"
    audio_path = data_dir / "audio_assets" / MEETING_ID / "audio.wav"

    before_interrupt = V2Persistence(database_path)
    created = before_interrupt.create_meeting(
        meeting_id=MEETING_ID,
        title="Phase 2 recovery",
        now_ms=1_000,
    )
    assert created["state"] == "live"

    interrupted_writer = RealtimeWavAssetWriter(
        data_dir=data_dir,
        session_id=MEETING_ID,
        source_type=SOURCE_TYPE,
        on_chunk_committed=_chunk_recorder(before_interrupt, now_ms=1_100),
    )
    interrupted_writer.write_float32_pcm(_float32_payload(9, value=0.1))

    durable_before_interrupt = before_interrupt.list_audio_chunks(MEETING_ID)
    assert len(durable_before_interrupt) == 1
    assert durable_before_interrupt[0]["duration_ms"] == 5_000
    assert durable_before_interrupt[0]["sample_count"] == 5 * SAMPLE_RATE_HZ
    assert not audio_path.exists()

    committed_final = before_interrupt.commit_final_and_enqueue(
        meeting_id=MEETING_ID,
        final_id="final-before-interrupt",
        segment_id="segment-before-interrupt",
        text="The durable recovery path must finish queued work.",
        normalized_text="The durable recovery path must finish queued work.",
        started_at_ms=1_050,
        ended_at_ms=1_150,
        evidence_hash="phase2-recovery-evidence",
        now_ms=1_200,
    )
    pending_job_ids = set(committed_final["job_ids"].values())
    assert {
        job["status"] for job in before_interrupt.list_jobs(meeting_id=MEETING_ID)
    } == {"pending"}

    # A process crash drops the writer's four-second in-memory tail without closing it.
    del interrupted_writer
    before_interrupt.close()

    reopened = V2Persistence(database_path)
    try:
        assert reopened.get_snapshot(MEETING_ID)["runtime"]["phase"] == "live"
        assert reopened.recover_interrupted_recordings(now_ms=2_000) == [MEETING_ID]
        assert reopened.get_snapshot(MEETING_ID)["runtime"]["phase"] == "interrupted"

        recovered_writer = RealtimeWavAssetWriter(
            data_dir=data_dir,
            session_id=MEETING_ID,
            source_type=SOURCE_TYPE,
            on_chunk_committed=_chunk_recorder(reopened, now_ms=2_100),
        )

        # Constructor replay is idempotent in SQLite and reopens interrupted recording state.
        assert len(reopened.list_audio_chunks(MEETING_ID)) == 1
        assert reopened.get_snapshot(MEETING_ID)["runtime"]["phase"] == "live"

        recovered_writer.write_float32_pcm(_float32_payload(5, value=0.2))
        recovered_chunks = reopened.list_audio_chunks(MEETING_ID)
        assert [(chunk["epoch"], chunk["chunk_seq"]) for chunk in recovered_chunks] == [
            (0, 0),
            (0, 1),
        ]

        reopened_jobs = reopened.list_jobs(meeting_id=MEETING_ID)
        assert {job["id"] for job in reopened_jobs} == pending_job_ids
        assert {job["status"] for job in reopened_jobs} == {"pending"}

        handled = asyncio.run(
            _complete_jobs_with_reopened_executor(reopened, pending_job_ids)
        )
        assert handled == {
            ("correction", committed_final["job_ids"]["correction"]),
            ("suggestion", committed_final["job_ids"]["suggestion"]),
        }
        for job_id in pending_job_ids:
            completed = reopened.get_job(job_id)
            assert completed["status"] == "succeeded"
            assert completed["attempts"] == 1
            assert completed["output"] == {
                "executor": "reopened-backend",
                "job_id": job_id,
            }

        metadata = recovered_writer.close()
        with wave.open(str(audio_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == SAMPLE_RATE_HZ
            wav_duration_ms = round(
                wav_file.getnframes() / wav_file.getframerate() * 1_000
            )

        durable_audio_ms = sum(chunk["duration_ms"] for chunk in recovered_chunks)
        durable_samples = sum(chunk["sample_count"] for chunk in recovered_chunks)
        observed_rpo_ms = 9_000 - durable_before_interrupt[0]["duration_ms"]

        assert observed_rpo_ms == 4_000
        assert 0 < observed_rpo_ms <= round(DEFAULT_CHUNK_DURATION_SECONDS * 1_000)
        assert durable_audio_ms == metadata["duration_ms"] == wav_duration_ms == 10_000
        assert durable_samples == 10 * SAMPLE_RATE_HZ
        assert 9_000 + 5_000 - wav_duration_ms == observed_rpo_ms
        assert "recording.interrupted" in {
            event["type"] for event in reopened.list_events(MEETING_ID)
        }
    finally:
        reopened.close()


def test_v2_process_sigkill_recovery_meets_rpo_and_rto(tmp_path):
    data_dir = tmp_path / "process-runtime"
    ready_path = tmp_path / "worker-ready.json"
    worker_path = Path(__file__).parent / "fixtures" / "v2_recovery_crash_worker.py"
    backend_root = Path(__file__).parents[1]
    core_root = backend_root.parent.parent / "core"
    worker_env = dict(os.environ)
    worker_env["PYTHONPATH"] = os.pathsep.join(
        [str(backend_root), str(core_root), worker_env.get("PYTHONPATH", "")]
    )
    process = subprocess.Popen(
        [sys.executable, str(worker_path), str(data_dir), str(ready_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=worker_env,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(process.stderr.read() if process.stderr else "worker exited")
            time.sleep(0.02)
        assert ready_path.exists(), "crash worker did not commit its durable boundary"
        before = json.loads(ready_path.read_text(encoding="utf-8"))
        assert before["committed_audio_ms"] == 5_000

        process.kill()
        assert process.wait(timeout=5) != 0

        recovery_started = time.monotonic()
        persistence = V2Persistence(data_dir / "meeting_copilot.db")
        try:
            assert persistence.recover_expired_recording_leases(now_ms=2_000) == [
                "phase2-process-recovery"
            ]
            recording_export = asyncio.run(
                _export_recovered_recording(
                    persistence,
                    data_dir=data_dir,
                    meeting_id="phase2-process-recovery",
                )
            )
            handled = asyncio.run(
                _complete_jobs_with_reopened_executor(
                    persistence,
                    set(before["job_ids"]),
                )
            )
            metadata = dict(recording_export["output"])
            rto_ms = round((time.monotonic() - recovery_started) * 1_000)
            chunks = persistence.list_audio_chunks("phase2-process-recovery")
            report = {
                "schema_version": "v2-recording-lease-recovery.v2",
                "termination": "SIGKILL",
                "rpo_ms": 4_000,
                "rto_ms": rto_ms,
                "audio_duration_ms": metadata["duration_ms"],
                "chunk_count": len(chunks),
                "job_count": len(handled),
                "jobs_succeeded": all(
                    persistence.get_job(job_id)["status"] == "succeeded"
                    for job_id in before["job_ids"]
                ),
            }
            report_path = os.environ.get("MEETING_COPILOT_RECOVERY_REPORT")
            if report_path:
                destination = Path(report_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

            assert report["rpo_ms"] <= 5_000
            assert report["rto_ms"] <= 30_000
            assert report["audio_duration_ms"] == 5_000
            assert report["chunk_count"] == 1
            assert report["job_count"] == 2
            assert report["jobs_succeeded"] is True
        finally:
            persistence.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
