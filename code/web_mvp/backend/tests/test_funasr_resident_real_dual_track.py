"""Opt-in local-model gate for dual-track resident FunASR concurrency."""

from __future__ import annotations

import os
import sys
import threading
import wave
from array import array
from pathlib import Path

import pytest

from meeting_copilot_web_mvp.funasr_resident import (
    FunasrResidentBusyError,
    FunasrResidentWorkerManager,
)


RUN_REAL_DUAL_TRACK = os.environ.get(
    "MEETING_COPILOT_RUN_REAL_FUNASR_DUAL_TRACK", ""
).strip() == "1"


def _float32_pcm_from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 16_000
        samples = array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    return array("f", (sample / 32_768.0 for sample in samples)).tobytes()


@pytest.mark.skipif(
    not RUN_REAL_DUAL_TRACK,
    reason="set MEETING_COPILOT_RUN_REAL_FUNASR_DUAL_TRACK=1 for the local-model gate",
)
def test_real_local_model_runs_two_same_meeting_tracks_without_worker_busy() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    python = repo_root / "code/asr_runtime/.venv-funasr/bin/python"
    worker = repo_root / "code/asr_runtime/scripts/funasr_stream_worker.py"
    model = (
        Path.home()
        / ".cache/modelscope/hub/models/iic/"
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    )
    audio = Path(os.environ["MEETING_COPILOT_REAL_FUNASR_AUDIO"])
    assert python.is_file()
    assert worker.is_file()
    assert (model / "model.pt").is_file()
    assert audio.is_file()

    command = [
        str(python),
        str(worker),
        "--model",
        str(model),
        "--chunk-size",
        "0,30,15",
        "--encoder-chunk-look-back",
        "4",
        "--decoder-chunk-look-back",
        "1",
    ]
    environment = os.environ.copy()
    for key in list(environment):
        upper = key.upper()
        if upper.endswith("_API_KEY") or upper in {
            "AUTHORIZATION",
            "MEETING_COPILOT_LOCAL_API_TOKEN",
        }:
            environment.pop(key, None)
    environment["MODELSCOPE_OFFLINE"] = "1"
    manager = FunasrResidentWorkerManager(command, environment=environment)
    sessions = []
    try:
        sessions = [
            manager.create_session("real-dual-meeting"),
            manager.create_session("real-dual-meeting"),
        ]
        assert all(session.wait_ready(90.0) for session in sessions)
        active_status = manager.status()
        assert active_status["active_session_count"] == 2
        assert active_status["worker_count"] == 2
        assert active_status["running_worker_count"] == 2
        assert len({worker_status["pid"] for worker_status in active_status["workers"]}) == 2
        with pytest.raises(FunasrResidentBusyError):
            manager.create_session("real-third-track")

        pcm = _float32_pcm_from_wav(audio)
        chunk_bytes = 28_800 * 4
        barrier = threading.Barrier(3)
        results: list[list[dict]] = []
        failures: list[BaseException] = []

        def transcribe(session) -> None:
            barrier.wait()
            try:
                for offset in range(0, len(pcm), chunk_bytes):
                    session.recognize_chunk(pcm[offset : offset + chunk_bytes])
                results.append(session.finalize())
            except BaseException as exc:  # surfaced by the assertion below
                failures.append(exc)

        threads = [
            threading.Thread(target=transcribe, args=(session,), daemon=True)
            for session in sessions
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=60.0)

        assert all(not thread.is_alive() for thread in threads)
        assert failures == []
        assert len(results) == 2
        assert all(
            any(event.get("event_type") == "final" and event.get("text") for event in events)
            for events in results
        )
        assert all(session.worker_diagnostics["input_samples"] > 0 for session in sessions)
        assert all(session.worker_diagnostics["inference_calls"] > 0 for session in sessions)
        assert manager.completed_session_count == 2
        assert manager.process_start_count == 2
        assert manager.status()["active_session_count"] == 0
    finally:
        for session in sessions:
            session.abort()
        manager.shutdown()
    stopped = manager.status()
    assert stopped["running_worker_count"] == 0
    assert stopped["shutdown"] is True
