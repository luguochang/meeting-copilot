from __future__ import annotations

import asyncio
import json
from pathlib import Path
import struct
import sys
import textwrap
import time

from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.asr_live_repository import InMemoryAsrLiveSessionRepository
from meeting_copilot_web_mvp.diarization_runtime import (
    DiarizationRuntime,
    SubprocessDiarizationSidecar,
    default_worker_command,
)
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


PCM_FRAME = struct.pack("<f", 0.1) * 160


def test_default_worker_command_uses_funasr_interpreter_and_local_model_cli(monkeypatch, tmp_path):
    worker = tmp_path / "funasr_diarization_worker.py"
    worker.write_text("# test worker\n", encoding="utf-8")
    funasr_python = tmp_path / "python"
    funasr_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("MEETING_COPILOT_DIARIZATION_WORKER", str(worker))
    monkeypatch.setenv("MEETING_COPILOT_FUNASR_PYTHON", str(funasr_python))
    monkeypatch.setenv("MEETING_COPILOT_DIARIZATION_VAD_DIR", "/local/vad")
    monkeypatch.setenv("MEETING_COPILOT_DIARIZATION_CAMPLUS_DIR", "/local/camplus")

    assert default_worker_command() == [
        str(funasr_python),
        str(worker),
        "--vad-dir",
        "/local/vad",
        "--camplus-dir",
        "/local/camplus",
    ]


class FakeSidecar:
    def __init__(self, on_event, on_diagnostic, *, fail_submit: bool = False):
        self.on_event = on_event
        self.on_diagnostic = on_diagnostic
        self.fail_submit = fail_submit
        self.session_id = None
        self.frames: list[tuple[bytes, int]] = []
        self.aborted = False
        self.finished = False

    def start(self, session_id: str) -> None:
        self.session_id = session_id
        self.on_event(
            {
                "event_type": "ready",
                "protocol": "funasr-diarization-jsonl.v1",
                "model_download_status": "not_performed",
            }
        )

    def submit(self, pcm: bytes, *, sample_start: int) -> bool:
        if self.fail_submit:
            self.on_diagnostic("sidecar_audio_queue_full")
            return False
        self.frames.append((pcm, sample_start))
        return True

    def finish(self, *, timeout_s: float) -> None:
        self.finished = True
        if self.frames:
            self.on_event(
                {
                    "event_type": "speaker.turn",
                    "speaker_id": "speaker_1",
                    "sample_start": 0,
                    "sample_end": 160,
                    "confidence": 0.96,
                    "is_stable": True,
                }
            )
        self.on_event({"event_type": "speaker.done", "status": "completed"})

    def abort(self) -> None:
        self.aborted = True


def _factory(holder: dict[str, FakeSidecar], *, fail_submit: bool = False):
    def create(on_event, on_diagnostic):
        sidecar = FakeSidecar(on_event, on_diagnostic, fail_submit=fail_submit)
        holder["sidecar"] = sidecar
        return sidecar

    return create


def _commit_segment(persistence: V2Persistence, meeting_id: str = "diarization-runtime") -> dict[str, object]:
    return persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="final-runtime-1",
        segment_id="segment-runtime-1",
        text="保留原始转写事实，不因说话人归因而改写。",
        normalized_text="保留原始转写事实，不因说话人归因而改写。",
        started_at_ms=0,
        ended_at_ms=10,
        evidence_hash="evidence-runtime-1",
        now_ms=1_000,
        enqueue_jobs=False,
    )


def test_runtime_fans_out_pcm_and_persists_independent_speaker_revision(tmp_path):
    persistence = V2Persistence(tmp_path / "meeting.db")
    holder: dict[str, FakeSidecar] = {}
    runtime = DiarizationRuntime(
        "diarization-runtime",
        persistence=persistence,
        sidecar_factory=_factory(holder),
    )

    _commit_segment(persistence)
    runtime.start()
    assert runtime.submit_pcm(PCM_FRAME) is True
    assert runtime.submit_pcm(PCM_FRAME) is True
    assert [start for _pcm, start in holder["sidecar"].frames] == [0, 160]
    runtime.observe_final(
        {
            "segment_id": "segment-runtime-1",
            "text": "保留原始转写事实，不因说话人归因而改写。",
            "start_ms": 0,
            "end_ms": 10,
        }
    )

    diagnostics = runtime.finish()
    assert diagnostics["ready"] is True
    assert diagnostics["speaker_turn_count"] == 1
    assert diagnostics["model_download_status"] == "not_performed"
    assert holder["sidecar"].finished is True

    segment = persistence.get_transcript_segment("diarization-runtime", "segment-runtime-1")
    assert segment["text"] == "保留原始转写事实，不因说话人归因而改写。"
    assert segment["normalized_text"] == segment["text"]
    assert segment["revision"] == 1
    assert segment["speaker_id"] == "speaker_1"
    assert segment["speaker_attribution_revision"] == 1
    assert persistence.list_meeting_speakers("diarization-runtime")["speakers"][0]["speaker_id"] == "speaker_1"
    events = persistence.list_event_page("diarization-runtime", after_seq=0, limit=100)["events"]
    assert sum(event["type"] == "transcript.segment.speaker_revised" for event in events) == 1
    assert (
        persistence.create_or_update_speaker_run(
            meeting_id="diarization-runtime",
            run_id="diarization:diarization-runtime",
            source="local_realtime_diarization",
            model="cam++-zh-cn-local",
            status="completed",
            metadata={"protocol": "funasr-diarization-jsonl.v1", "offline": True},
            now_ms=2_000,
        )["status"]
        == "completed"
    )

    persistence.close()


def test_diarization_failure_is_fail_open_for_asr_and_stops_pcm_fanout():
    holder: dict[str, FakeSidecar] = {}
    runtime = DiarizationRuntime(
        "fail-open-session",
        sidecar_factory=_factory(holder, fail_submit=True),
    )
    runtime.start()
    assert runtime.submit_pcm(PCM_FRAME) is False
    assert runtime.submit_pcm(PCM_FRAME) is False
    assert runtime.diagnostics["audio_disabled"] is True
    assert "diarization_audio_fanout_stopped" in runtime.diagnostics["degradation_reasons"]
    runtime.abort()
    assert holder["sidecar"].aborted is True


class _ScriptedWebSocket:
    def __init__(self):
        self.messages = [{"bytes": PCM_FRAME}, {"text": "END"}]
        self.sent: list[str] = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self.messages:
            return self.messages.pop(0)
        return {"text": "END"}

    async def send_text(self, value: str):
        self.sent.append(value)

    async def close(self):
        self.closed = True


class _FakeRecognizer:
    provider = "fake"
    provider_mode = "mock"
    is_mock = True
    fallback_used = True
    degradation_reasons = []

    def __init__(self, session_id):
        self.session_id = session_id
        self._seq = 0

    def recognize_chunk(self, pcm):
        self._seq += 1
        return [
            {
                "event_type": "partial",
                "segment_id": f"segment-{self.session_id}",
                "text": "ASR 仍然继续输出",
                "start_ms": 0,
                "end_ms": 10,
                "confidence": 0.9,
            }
        ]

    def finalize(self):
        return [
            {
                "event_type": "final",
                "segment_id": f"segment-{self.session_id}",
                "text": "ASR 仍然继续输出",
                "start_ms": 0,
                "end_ms": 10,
                "confidence": 0.9,
            }
        ]

    def abort(self):
        return None


def test_asr_stream_continues_when_injected_diarization_sidecar_fails(monkeypatch):
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda session_id: _FakeRecognizer(session_id))
    websocket = _ScriptedWebSocket()
    asyncio.run(
        asr_stream.handle_stream(
            websocket,
            "asr-fail-open",
            asr_live_repo=InMemoryAsrLiveSessionRepository(),
            allow_fake_fallback=True,
            diarization_enabled=True,
            diarization_sidecar_factory=_factory({}, fail_submit=True),
        )
    )

    output = [json.loads(value) for value in websocket.sent]
    assert any(item.get("event_type") == "partial" for item in output)
    assert any(item.get("event_type") == "final" for item in output)
    assert websocket.closed is True


def _protocol_echo_command(*, ready_delay_s: float = 0.0) -> list[str]:
    script = textwrap.dedent(
        f"""
        import json
        import sys
        import time

        sys.stderr.write("x" * 131072)
        sys.stderr.flush()
        time.sleep({ready_delay_s})
        print(json.dumps({{"event_type": "ready"}}), flush=True)
        for line in sys.stdin:
            payload = json.loads(line)
            print(json.dumps({{"event_type": "echo", "payload": payload}}), flush=True)
            if payload.get("type") in {{"end", "abort"}}:
                break
        """
    )
    return [sys.executable, "-u", "-c", script]


def test_subprocess_contract_carries_session_id_closes_stdin_and_drains_stderr():
    events: list[dict[str, object]] = []
    diagnostics: list[str] = []
    sidecar = SubprocessDiarizationSidecar(
        _protocol_echo_command(),
        on_event=events.append,
        on_diagnostic=diagnostics.append,
        max_audio_queue_bytes=16 * 1024,
    )
    started_at = time.monotonic()
    sidecar.start("contract-session")
    assert sidecar.submit(PCM_FRAME, sample_start=0) is True
    ready_deadline = time.monotonic() + 1.0
    while time.monotonic() < ready_deadline and not any(event.get("event_type") == "ready" for event in events):
        time.sleep(0.005)
    sidecar.finish(timeout_s=3.0)

    elapsed_s = time.monotonic() - started_at
    payloads = [event["payload"] for event in events if event.get("event_type") == "echo"]
    assert [payload["type"] for payload in payloads] == ["session", "audio", "end"]
    assert all(payload["session_id"] == "contract-session" for payload in payloads)
    assert sidecar.stderr_bytes_drained >= 131072
    assert elapsed_s < 2.0
    assert sidecar._process is not None and sidecar._process.poll() == 0
    assert "sidecar_stderr_drain_failed" not in " ".join(diagnostics)


def test_subprocess_abort_carries_session_id_and_reaps_child():
    events: list[dict[str, object]] = []
    sidecar = SubprocessDiarizationSidecar(
        _protocol_echo_command(ready_delay_s=0.2),
        on_event=events.append,
        on_diagnostic=lambda _reason: None,
    )
    sidecar.start("abort-session")
    sidecar.abort()

    payloads = [event["payload"] for event in events if event.get("event_type") == "echo"]
    assert any(payload["type"] == "abort" for payload in payloads)
    assert all(payload["session_id"] == "abort-session" for payload in payloads)
    assert sidecar._process is not None and sidecar._process.poll() == 0


def test_slow_ready_uses_byte_budget_and_reports_backpressure_without_unbounded_queue():
    runtime = DiarizationRuntime(
        "slow-ready-session",
        worker_command=_protocol_echo_command(ready_delay_s=0.5),
        audio_queue_max_bytes=1024,
        startup_deadline_s=2.0,
    )
    runtime.start()
    assert runtime.submit_pcm(PCM_FRAME) is True
    assert runtime.submit_pcm(PCM_FRAME) is False
    diagnostics = runtime.diagnostics
    assert diagnostics["audio_queue_byte_budget"] == 1024
    assert diagnostics["audio_queue_max_bytes_observed"] <= 1024
    assert diagnostics["audio_dropped_bytes"] >= len(PCM_FRAME)
    assert "diarization_backpressure" in diagnostics["degradation_reasons"]
    assert "diarization_audio_drop" in diagnostics["degradation_reasons"]
    runtime.abort()


def test_actual_worker_missing_models_fails_open_to_speaker_unknown(tmp_path):
    repo = Path(__file__).resolve().parents[4]
    worker = repo / "code/asr_runtime/scripts/funasr_diarization_worker.py"
    runtime = DiarizationRuntime(
        "missing-model-session",
        worker_command=[
            sys.executable,
            str(worker),
            "--vad-dir",
            str(tmp_path / "missing-vad"),
            "--camplus-dir",
            str(tmp_path / "missing-camplus"),
        ],
        finish_timeout_s=2.0,
    )
    runtime.start()
    diagnostics = runtime.finish()

    assert diagnostics["startup_state"] == "unavailable"
    assert any(reason.startswith("diarization_unavailable:") for reason in diagnostics["degradation_reasons"])
    assert diagnostics["model_download_status"] == "not_performed"
