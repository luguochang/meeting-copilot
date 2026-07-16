import base64
import io
import json
import sys
import types

import numpy as np
import pytest

from scripts import funasr_stream_worker


class FakeWorkerStdin:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)


class FakeResidentAutoModel:
    init_count = 0
    cache_ids = []
    cache_call_numbers = []

    def __init__(self, **_kwargs):
        type(self).init_count += 1

    def generate(self, **kwargs):
        cache = kwargs["cache"]
        cache["call_number"] = cache.get("call_number", 0) + 1
        type(self).cache_ids.append(id(cache))
        type(self).cache_call_numbers.append(cache["call_number"])
        marker = int(round(float(kwargs["input"][0])))
        return [{"text": {1: "第一场内容", 2: "第二场内容"}.get(marker, "其他内容")}]


class ExplodingResidentAutoModel:
    def __init__(self, **_kwargs):
        pass

    def generate(self, **_kwargs):
        raise RuntimeError("private provider failure details")


def _install_fake_model(monkeypatch):
    FakeResidentAutoModel.init_count = 0
    FakeResidentAutoModel.cache_ids = []
    FakeResidentAutoModel.cache_call_numbers = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeResidentAutoModel),
    )


def _run_worker(monkeypatch, stdin_bytes: bytes, argv: list[str]) -> list[dict]:
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(stdin_bytes))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    funasr_stream_worker.main(argv)

    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _audio_payload(marker: float, sample_count: int = 960) -> bytes:
    return np.full(sample_count, marker, dtype="<f4").tobytes()


def test_resident_command_header_encode_and_decode_helpers_round_trip():
    pcm_bytes = _audio_payload(1, sample_count=4)
    encoded = funasr_stream_worker.encode_resident_command(
        "audio",
        session_id="session-1",
        pcm_bytes=pcm_bytes,
    )
    wire_payload = json.loads(encoded)

    header = funasr_stream_worker.decode_resident_command_header(wire_payload)
    decoded = funasr_stream_worker.decode_resident_command(encoded)

    assert encoded.endswith(b"\n")
    assert wire_payload == {
        "command": "audio",
        "session_id": "session-1",
        "pcm_base64": base64.b64encode(pcm_bytes).decode("ascii"),
    }
    assert header.command == "audio"
    assert header.session_id == "session-1"
    assert decoded.command == "audio"
    assert decoded.session_id == "session-1"
    assert decoded.pcm_bytes == pcm_bytes


def test_resident_command_decoder_rejects_bad_base64_and_extra_fields():
    with pytest.raises(funasr_stream_worker.ResidentProtocolError) as bad_base64:
        funasr_stream_worker.decode_resident_command(
            b'{"command":"audio","session_id":"session-1","pcm_base64":"%%%"}\n'
        )
    assert bad_base64.value.code == "invalid_pcm_base64"

    with pytest.raises(funasr_stream_worker.ResidentProtocolError) as extra_field:
        funasr_stream_worker.decode_resident_command(
            b'{"command":"start_session","session_id":"session-1","unexpected":true}\n'
        )
    assert extra_field.value.code == "invalid_command_fields"

    with pytest.raises(funasr_stream_worker.ResidentProtocolError) as duplicate_field:
        funasr_stream_worker.decode_resident_command(
            b'{"command":"start_session","session_id":"first","session_id":"second"}\n'
        )
    assert duplicate_field.value.code == "duplicate_json_field"


def test_resident_mode_loads_model_once_and_resets_every_session(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="session-1"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="session-1",
                pcm_bytes=_audio_payload(1),
            ),
            funasr_stream_worker.encode_resident_command("end_session", session_id="session-1"),
            funasr_stream_worker.encode_resident_command("start_session", session_id="session-2"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="session-2",
                pcm_bytes=_audio_payload(2),
            ),
            funasr_stream_worker.encode_resident_command("end_session", session_id="session-2"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert FakeResidentAutoModel.init_count == 1
    assert len(set(FakeResidentAutoModel.cache_ids)) == 2
    assert FakeResidentAutoModel.cache_call_numbers == [1, 1]
    assert all("session_id" in event for event in events)
    assert [event["event_type"] for event in events] == [
        "ready",
        "session_started",
        "partial",
        "final",
        "telemetry",
        "session_ended",
        "session_started",
        "partial",
        "final",
        "telemetry",
        "session_ended",
    ]
    assert events[0]["session_id"] is None
    assert events[0]["scope"] == "process"
    assert events[0]["protocol"] == "funasr-resident-jsonl.v1"
    partials = [event for event in events if event["event_type"] == "partial"]
    assert [(event["session_id"], event["text"]) for event in partials] == [
        ("session-1", "第一场内容"),
        ("session-2", "第二场内容"),
    ]
    telemetry = [event for event in events if event["event_type"] == "telemetry"]
    assert [event["session_id"] for event in telemetry] == ["session-1", "session-2"]
    assert [event["input_samples"] for event in telemetry] == [960, 960]
    assert [event["inference_calls"] for event in telemetry] == [1, 1]
    ended = [event for event in events if event["event_type"] == "session_ended"]
    assert all(event["status"] == "completed" for event in ended)
    assert all(event["reason"] == "end_session" for event in ended)
    assert all(event["final_emitted"] is True for event in ended)


def test_abort_discards_buffer_and_next_session_starts_clean(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="aborted"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="aborted",
                pcm_bytes=_audio_payload(1, sample_count=480),
            ),
            funasr_stream_worker.encode_resident_command("abort_session", session_id="aborted"),
            funasr_stream_worker.encode_resident_command("start_session", session_id="completed"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="completed",
                pcm_bytes=_audio_payload(2),
            ),
            funasr_stream_worker.encode_resident_command("end_session", session_id="completed"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert not [
        event
        for event in events
        if event["session_id"] == "aborted" and event["event_type"] in {"partial", "final"}
    ]
    aborted_telemetry = next(
        event
        for event in events
        if event["session_id"] == "aborted" and event["event_type"] == "telemetry"
    )
    assert aborted_telemetry["input_samples"] == 480
    assert aborted_telemetry["inference_calls"] == 0
    aborted = next(event for event in events if event["event_type"] == "session_aborted")
    assert aborted == {
        "event_type": "session_aborted",
        "session_id": "aborted",
        "scope": "session",
        "status": "aborted",
        "reason": "abort_session",
        "final_emitted": False,
    }
    completed_partial = next(
        event
        for event in events
        if event["session_id"] == "completed" and event["event_type"] == "partial"
    )
    assert completed_partial["text"] == "第二场内容"


def test_resident_mode_rejects_concurrent_start_and_exits(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="session-1"),
            funasr_stream_worker.encode_resident_command("start_session", session_id="session-2"),
        ]
    )
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(commands))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    with pytest.raises(SystemExit) as stopped:
        funasr_stream_worker.main(["--resident", "--chunk-size", "0,1,0"])

    assert stopped.value.code == 2
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["event_type"] for event in events] == [
        "ready",
        "session_started",
        "error",
    ]
    assert events[-1] == {
        "event_type": "error",
        "session_id": "session-2",
        "scope": "session",
        "error_code": "concurrent_session",
        "fatal": True,
    }
    assert FakeResidentAutoModel.init_count == 1


def test_resident_mode_rejects_session_mismatch_and_exits(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="session-1"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="session-2",
                pcm_bytes=_audio_payload(1),
            ),
        ]
    )
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(commands))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    with pytest.raises(SystemExit) as stopped:
        funasr_stream_worker.main(["--resident", "--chunk-size", "0,1,0"])

    assert stopped.value.code == 2
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert events[-1] == {
        "event_type": "error",
        "session_id": "session-2",
        "scope": "session",
        "error_code": "session_mismatch",
        "fatal": True,
    }
    assert FakeResidentAutoModel.cache_ids == []


def test_resident_mode_rejects_invalid_json_and_exits(monkeypatch):
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(b"not-json\n"))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    with pytest.raises(SystemExit) as stopped:
        funasr_stream_worker.main(["--resident"])

    assert stopped.value.code == 2
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert events[-1] == {
        "event_type": "error",
        "session_id": None,
        "scope": "process",
        "error_code": "invalid_json",
        "fatal": True,
    }


def test_resident_mode_emits_redacted_session_error_and_failed_end(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="session-1"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="session-1",
                pcm_bytes=_audio_payload(1),
            ),
        ]
    )
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=ExplodingResidentAutoModel),
    )
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(commands))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    with pytest.raises(SystemExit) as stopped:
        funasr_stream_worker.main(["--resident", "--chunk-size", "0,1,0"])

    assert stopped.value.code == 3
    assert "private provider failure details" not in stdout.getvalue()
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["event_type"] for event in events] == [
        "ready",
        "session_started",
        "error",
        "session_ended",
    ]
    assert events[-2] == {
        "event_type": "error",
        "session_id": "session-1",
        "scope": "session",
        "error_code": "inference_failed",
        "fatal": True,
    }
    assert events[-1] == {
        "event_type": "session_ended",
        "session_id": "session-1",
        "scope": "session",
        "status": "failed",
        "reason": "inference_failed",
        "final_emitted": False,
    }


def test_default_raw_pcm_eof_mode_keeps_event_order(monkeypatch):
    events = _run_worker(
        monkeypatch,
        _audio_payload(1),
        ["--chunk-size", "0,1,0"],
    )

    assert [event["event_type"] for event in events] == [
        "ready",
        "partial",
        "final",
        "telemetry",
    ]
    assert all(event["session_id"] is None for event in events)
    assert FakeResidentAutoModel.init_count == 1
