import base64
import io
import json
import queue
import sys
import threading
import time
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
    is_final_flags = []
    hotword_values = []

    def __init__(self, **_kwargs):
        type(self).init_count += 1

    def generate(self, **kwargs):
        cache = kwargs["cache"]
        cache["call_number"] = cache.get("call_number", 0) + 1
        type(self).cache_ids.append(id(cache))
        type(self).cache_call_numbers.append(cache["call_number"])
        type(self).is_final_flags.append(bool(kwargs["is_final"]))
        type(self).hotword_values.append(tuple(kwargs.get("hotword") or ()))
        marker = int(round(float(kwargs["input"][0])))
        return [{"text": {1: "第一场内容", 2: "第二场内容"}.get(marker, "其他内容")}]


class ExplodingResidentAutoModel:
    def __init__(self, **_kwargs):
        pass

    def generate(self, **_kwargs):
        raise RuntimeError("private provider failure details")


class InteractiveResidentStdin:
    def __init__(self):
        self._lines: queue.Queue[bytes] = queue.Queue()
        self._condition = threading.Condition()
        self.read_count = 0

    def feed(self, payload: bytes) -> None:
        assert payload.endswith(b"\n")
        self._lines.put(payload)

    def readline(self, _size: int = -1) -> bytes:
        line = self._lines.get(timeout=2.0)
        with self._condition:
            self.read_count += 1
            self._condition.notify_all()
        return line

    def wait_for_reads(self, expected: int, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self.read_count < expected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"reader consumed {self.read_count}, expected {expected}"
                    )
                self._condition.wait(remaining)


class TimestampedEventStream:
    def __init__(self):
        self._condition = threading.Condition()
        self.events: list[tuple[float, dict]] = []

    def write(self, text: str) -> int:
        parsed = [json.loads(line) for line in text.splitlines() if line]
        with self._condition:
            now = time.monotonic()
            self.events.extend((now, event) for event in parsed)
            self._condition.notify_all()
        return len(text)

    def flush(self) -> None:
        return None

    def wait_for(self, predicate, timeout: float = 1.0) -> list[tuple[float, dict]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not predicate([event for _, event in self.events]):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError("worker event was not emitted before timeout")
                self._condition.wait(remaining)
            return list(self.events)


class GatedResidentModel:
    def __init__(self):
        self.first_call_started = threading.Event()
        self.release_first_call = threading.Event()
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        cache = kwargs["cache"]
        cache["call_number"] = cache.get("call_number", 0) + 1
        marker = int(round(float(kwargs["input"][0])))
        self.calls.append(
            {
                "cache_id": id(cache),
                "cache_call_number": cache["call_number"],
                "marker": marker,
                "is_final": bool(kwargs["is_final"]),
            }
        )
        if len(self.calls) == 1:
            self.first_call_started.set()
            if not self.release_first_call.wait(1.0):
                raise RuntimeError("test did not release first inference")
        return [{"text": {1: "旧句预览", 2: "新句预览"}.get(marker, "未知预览")}]


def _install_fake_model(monkeypatch):
    FakeResidentAutoModel.init_count = 0
    FakeResidentAutoModel.cache_ids = []
    FakeResidentAutoModel.cache_call_numbers = []
    FakeResidentAutoModel.is_final_flags = []
    FakeResidentAutoModel.hotword_values = []
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


def _start_interactive_worker(monkeypatch):
    stdin = InteractiveResidentStdin()
    stdout = TimestampedEventStream()
    model = GatedResidentModel()
    errors: list[BaseException] = []
    args = funasr_stream_worker.parse_args(
        ["--resident", "--chunk-size", "0,1,0"]
    )
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    def run() -> None:
        try:
            funasr_stream_worker._run_resident_mode(
                model=model,
                np_module=np,
                args=args,
                hotwords=[],
                stdin=stdin,
            )
        except BaseException as exc:  # surfaced in the calling test
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return stdin, stdout, model, worker, errors


def test_onnx_adapter_preserves_cache_and_normalizes_online_result_shape():
    calls = []

    class FakeOnnxModel:
        def __call__(self, audio, *, param_dict):
            calls.append((audio, param_dict))
            param_dict["cache"]["seen"] = True
            return [{"preds": ("实时文字", ["实", "时", "文", "字"])}]

    cache = {}
    adapter = funasr_stream_worker.OnnxStreamingModelAdapter(FakeOnnxModel())
    result = adapter.generate(
        input=np.ones(960, dtype=np.float32),
        cache=cache,
        is_final=False,
        chunk_size=[0, 1, 0],
        hotword=["trace_id"],
    )

    assert result == [{"text": "实时文字"}]
    assert cache == {"seen": True}
    assert calls[0][1]["is_final"] is False


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

    start_encoded = funasr_stream_worker.encode_resident_command(
        "start_session",
        session_id="session-1",
        hotwords=["P99", "订单中台", "p99"],
    )
    start_decoded = funasr_stream_worker.decode_resident_command(start_encoded)
    assert start_decoded.hotwords == ("P99", "订单中台")


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

    with pytest.raises(funasr_stream_worker.ResidentProtocolError) as invalid_hotwords:
        funasr_stream_worker.decode_resident_command(
            b'{"command":"start_session","session_id":"session-1","hotwords":["bad\\nword"]}\n'
        )
    assert invalid_hotwords.value.code == "invalid_hotwords"


def test_flush_utterance_command_round_trip_and_boundary_validation():
    encoded = funasr_stream_worker.encode_resident_command(
        "flush_utterance",
        session_id="session-1",
        boundary_id="session-1:utterance:1:boundary-1",
    )

    decoded = funasr_stream_worker.decode_resident_command(encoded)
    assert decoded.command == "flush_utterance"
    assert decoded.session_id == "session-1"
    assert decoded.boundary_id == "session-1:utterance:1:boundary-1"

    with pytest.raises(funasr_stream_worker.ResidentProtocolError) as missing:
        funasr_stream_worker.decode_resident_command(
            b'{"command":"flush_utterance","session_id":"session-1"}\n'
        )
    assert missing.value.code == "invalid_command_fields"

    for boundary_id in ("", "bad\nline", "é boundary"):
        with pytest.raises(funasr_stream_worker.ResidentProtocolError) as invalid:
            funasr_stream_worker.encode_resident_command(
                "flush_utterance",
                session_id="session-1",
                boundary_id=boundary_id,
            )
        assert invalid.value.code == "invalid_boundary_id"

    with pytest.raises(funasr_stream_worker.ResidentProtocolError) as extra:
        funasr_stream_worker.decode_resident_command(
            b'{"command":"flush_utterance","session_id":"session-1","boundary_id":"b","pcm_base64":"x"}\n'
        )
    assert extra.value.code == "invalid_command_fields"


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


def test_resident_mode_marks_the_short_tail_as_final_for_funasr(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command(
                "start_session", session_id="tail-session"
            ),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="tail-session",
                pcm_bytes=_audio_payload(1, sample_count=480),
            ),
            funasr_stream_worker.encode_resident_command(
                "end_session", session_id="tail-session"
            ),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert FakeResidentAutoModel.is_final_flags == [True]
    assert any(event["event_type"] == "final" for event in events)


def test_flush_utterance_decodes_residual_as_final_and_acks_after_partial(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="flush-session",
                pcm_bytes=_audio_payload(1, sample_count=480),
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance",
                session_id="flush-session",
                boundary_id="flush-session:utterance:1:boundary-1",
            ),
            funasr_stream_worker.encode_resident_command("abort_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert FakeResidentAutoModel.is_final_flags == [True]
    assert [event["event_type"] for event in events[:4]] == [
        "ready",
        "session_started",
        "partial",
        "utterance_boundary_complete",
    ]
    assert events[2]["text"] == "第一场内容"
    assert events[3]["boundary_id"] == "flush-session:utterance:1:boundary-1"
    assert events[3]["utterance_index"] == 1
    assert events[3]["duplicate"] is False


def test_flush_utterance_skips_silent_tail_without_final_inference(monkeypatch):
    boundary_id = "flush-session:utterance:1:boundary-silent-tail"
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="flush-session",
                pcm_bytes=_audio_payload(1, sample_count=960),
            ),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="flush-session",
                pcm_bytes=_audio_payload(0, sample_count=480),
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance",
                session_id="flush-session",
                boundary_id=boundary_id,
            ),
            funasr_stream_worker.encode_resident_command("abort_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert FakeResidentAutoModel.is_final_flags == [False]
    ack = next(
        event
        for event in events
        if event["event_type"] == "utterance_boundary_complete"
    )
    assert ack["boundary_id"] == boundary_id
    assert ack["duplicate"] is False
    assert ack["skipped_silence_bytes"] == 480 * 4
    assert ack["drain_ms"] >= 0


@pytest.mark.parametrize(
    ("pcm_bytes", "expected"),
    [
        (_audio_payload(0, sample_count=321), False),
        (_audio_payload(0.05, sample_count=321), True),
        (np.array([np.nan], dtype="<f4").tobytes(), True),
    ],
)
def test_preview_speech_detector_is_conservative_for_non_finite_input(
    pcm_bytes,
    expected,
):
    assert (
        funasr_stream_worker._pcm_contains_preview_speech(
            np_module=np,
            pcm_bytes=pcm_bytes,
        )
        is expected
    )


def test_flush_resets_utterance_state_and_next_audio_uses_fresh_cache(monkeypatch):
    boundary_id = "flush-session:utterance:1:boundary-1"
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command(
                "audio", session_id="flush-session", pcm_bytes=_audio_payload(1, 480)
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance", session_id="flush-session", boundary_id=boundary_id
            ),
            funasr_stream_worker.encode_resident_command(
                "audio", session_id="flush-session", pcm_bytes=_audio_payload(2, 480)
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance",
                session_id="flush-session",
                boundary_id="flush-session:utterance:2:boundary-2",
            ),
            funasr_stream_worker.encode_resident_command("abort_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    partials = [event for event in events if event["event_type"] == "partial"]
    acks = [event for event in events if event["event_type"] == "utterance_boundary_complete"]
    assert [(event["text"], event["segment_id"]) for event in partials] == [
        ("第一场内容", "funasr_sc_001"),
        ("第二场内容", "funasr_sc_002"),
    ]
    assert [(event["boundary_id"], event["utterance_index"], event["duplicate"]) for event in acks] == [
        (boundary_id, 1, False),
        ("flush-session:utterance:2:boundary-2", 2, False),
    ]
    assert FakeResidentAutoModel.cache_call_numbers == [1, 1]
    assert len(set(FakeResidentAutoModel.cache_ids)) == 2


def test_duplicate_flush_only_replays_duplicate_ack_without_reinference(monkeypatch):
    boundary_id = "flush-session:utterance:1:boundary-1"
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command(
                "audio", session_id="flush-session", pcm_bytes=_audio_payload(1, 480)
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance", session_id="flush-session", boundary_id=boundary_id
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance", session_id="flush-session", boundary_id=boundary_id
            ),
            funasr_stream_worker.encode_resident_command("abort_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert [event["event_type"] for event in events[:5]] == [
        "ready",
        "session_started",
        "partial",
        "utterance_boundary_complete",
        "utterance_boundary_complete",
    ]
    assert [event["duplicate"] for event in events if event["event_type"] == "utterance_boundary_complete"] == [
        False,
        True,
    ]
    assert FakeResidentAutoModel.is_final_flags == [True]
    assert FakeResidentAutoModel.cache_call_numbers == [1]


def test_slow_inference_pressure_prioritizes_causal_flush_and_isolates_next_utterance(
    monkeypatch,
):
    stdin, stdout, model, worker, errors = _start_interactive_worker(monkeypatch)
    session_id = "pressure-flush"
    boundary_id = f"{session_id}:utterance:1:boundary-1"
    backlog_chunk = _audio_payload(1, sample_count=96)
    oversized_backlog_chunk = _audio_payload(1, sample_count=960 * 4)
    backlog_count = funasr_stream_worker.RESIDENT_PENDING_AUDIO_MAX_COMMANDS + 36

    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "start_session", session_id=session_id
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "audio",
            session_id=session_id,
            pcm_bytes=_audio_payload(1),
        )
    )
    assert model.first_call_started.wait(0.5)

    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "audio",
            session_id=session_id,
            pcm_bytes=oversized_backlog_chunk,
        )
    )
    for _ in range(backlog_count):
        stdin.feed(
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id=session_id,
                pcm_bytes=backlog_chunk,
            )
        )
    boundary_sent_at = time.monotonic()
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "flush_utterance",
            session_id=session_id,
            boundary_id=boundary_id,
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "audio",
            session_id=session_id,
            pcm_bytes=_audio_payload(2),
        )
    )
    # A retry may arrive after current-utterance audio has already been sent.
    # It must move ahead only as an idempotent ACK and must not discard audio 2.
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "flush_utterance",
            session_id=session_id,
            boundary_id=boundary_id,
        )
    )
    stdin.wait_for_reads(3 + backlog_count + 3)
    model.release_first_call.set()

    captured = stdout.wait_for(
        lambda events: len(
            [
                event
                for event in events
                if event.get("event_type") == "utterance_boundary_complete"
            ]
        )
        == 2,
        timeout=0.75,
    )
    acknowledgements = [
        (timestamp, event)
        for timestamp, event in captured
        if event.get("event_type") == "utterance_boundary_complete"
    ]
    assert acknowledgements[0][0] - boundary_sent_at < 0.75
    assert [event["duplicate"] for _, event in acknowledgements] == [False, True]
    assert acknowledgements[0][1]["skipped_preview_bytes"] == (
        len(oversized_backlog_chunk) + backlog_count * len(backlog_chunk)
    )

    captured = stdout.wait_for(
        lambda events: any(event.get("text") == "新句预览" for event in events),
        timeout=0.5,
    )
    ordered_events = [event for _, event in captured]
    first_ack_index = next(
        index
        for index, event in enumerate(ordered_events)
        if event.get("event_type") == "utterance_boundary_complete"
    )
    new_partial_index = next(
        index for index, event in enumerate(ordered_events) if event.get("text") == "新句预览"
    )
    assert first_ack_index < new_partial_index
    assert [call["marker"] for call in model.calls] == [1, 2]
    assert [call["cache_call_number"] for call in model.calls] == [1, 1]
    assert len({call["cache_id"] for call in model.calls}) == 2

    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "abort_session", session_id=session_id
        )
    )
    stdout.wait_for(
        lambda events: any(event.get("event_type") == "session_aborted" for event in events)
    )
    stdin.feed(funasr_stream_worker.encode_resident_command("shutdown"))
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert errors == []


def test_abort_bypasses_sustained_audio_backlog_and_next_session_starts_clean(
    monkeypatch,
):
    stdin, stdout, model, worker, errors = _start_interactive_worker(monkeypatch)
    abandoned_session = "pressure-abort"
    replacement_session = "pressure-replacement"
    backlog_chunk = _audio_payload(1, sample_count=96)
    backlog_count = funasr_stream_worker.RESIDENT_PENDING_AUDIO_MAX_COMMANDS + 48

    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "start_session", session_id=abandoned_session
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "audio",
            session_id=abandoned_session,
            pcm_bytes=_audio_payload(1),
        )
    )
    assert model.first_call_started.wait(0.5)
    for _ in range(backlog_count):
        stdin.feed(
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id=abandoned_session,
                pcm_bytes=backlog_chunk,
            )
        )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "abort_session", session_id=abandoned_session
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "start_session", session_id=replacement_session
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "audio",
            session_id=replacement_session,
            pcm_bytes=_audio_payload(2),
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "end_session", session_id=replacement_session
        )
    )
    stdin.feed(funasr_stream_worker.encode_resident_command("shutdown"))
    stdin.wait_for_reads(2 + backlog_count + 5)

    released_at = time.monotonic()
    model.release_first_call.set()
    captured = stdout.wait_for(
        lambda events: any(
            event.get("event_type") == "session_aborted"
            and event.get("session_id") == abandoned_session
            for event in events
        ),
        timeout=0.75,
    )
    aborted_at = next(
        timestamp
        for timestamp, event in captured
        if event.get("event_type") == "session_aborted"
        and event.get("session_id") == abandoned_session
    )
    assert aborted_at - released_at < 0.75

    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert errors == []
    events = [event for _, event in stdout.events]
    abandoned_telemetry = next(
        event
        for event in events
        if event.get("event_type") == "telemetry"
        and event.get("session_id") == abandoned_session
    )
    assert abandoned_telemetry["input_samples"] == 960 + backlog_count * 96
    assert any(
        event.get("text") == "新句预览"
        and event.get("session_id") == replacement_session
        for event in events
    )
    assert [call["marker"] for call in model.calls] == [1, 2]
    assert [call["cache_call_number"] for call in model.calls] == [1, 1]
    assert len({call["cache_id"] for call in model.calls}) == 2


def test_end_session_bypasses_sustained_audio_backlog(monkeypatch):
    stdin, stdout, model, worker, errors = _start_interactive_worker(monkeypatch)
    session_id = "pressure-end"
    backlog_chunk = _audio_payload(1, sample_count=96)
    backlog_count = funasr_stream_worker.RESIDENT_PENDING_AUDIO_MAX_COMMANDS + 24

    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "start_session", session_id=session_id
        )
    )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "audio", session_id=session_id, pcm_bytes=_audio_payload(1)
        )
    )
    assert model.first_call_started.wait(0.5)
    for _ in range(backlog_count):
        stdin.feed(
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id=session_id,
                pcm_bytes=backlog_chunk,
            )
        )
    stdin.feed(
        funasr_stream_worker.encode_resident_command(
            "end_session", session_id=session_id
        )
    )
    stdin.feed(funasr_stream_worker.encode_resident_command("shutdown"))
    stdin.wait_for_reads(2 + backlog_count + 2)

    released_at = time.monotonic()
    model.release_first_call.set()
    captured = stdout.wait_for(
        lambda events: any(event.get("event_type") == "session_ended" for event in events),
        timeout=0.75,
    )
    ended_at = next(
        timestamp
        for timestamp, event in captured
        if event.get("event_type") == "session_ended"
    )
    assert ended_at - released_at < 0.75

    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert errors == []
    events = [event for _, event in stdout.events]
    telemetry = next(event for event in events if event.get("event_type") == "telemetry")
    assert telemetry["input_samples"] == 960 + backlog_count * 96
    assert [call["marker"] for call in model.calls] == [1]
    assert any(
        event.get("event_type") == "final" and event.get("text") == "旧句预览"
        for event in events
    )


def test_end_after_successful_flush_does_not_emit_duplicate_final(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command("start_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command(
                "audio", session_id="flush-session", pcm_bytes=_audio_payload(1, 480)
            ),
            funasr_stream_worker.encode_resident_command(
                "flush_utterance",
                session_id="flush-session",
                boundary_id="flush-session:utterance:1:boundary-1",
            ),
            funasr_stream_worker.encode_resident_command("end_session", session_id="flush-session"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    assert [event["event_type"] for event in events] == [
        "ready",
        "session_started",
        "partial",
        "utterance_boundary_complete",
        "telemetry",
        "session_ended",
    ]
    ended = events[-1]
    assert ended["final_emitted"] is False
    assert not [event for event in events if event["event_type"] == "final"]


def test_resident_mode_processes_an_exact_stride_without_waiting_for_end(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command(
                "start_session", session_id="exact-stride"
            ),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="exact-stride",
                pcm_bytes=_audio_payload(1, sample_count=960),
            ),
            funasr_stream_worker.encode_resident_command(
                "end_session", session_id="exact-stride"
            ),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    _run_worker(monkeypatch, commands, ["--resident", "--chunk-size", "0,1,0"])

    assert FakeResidentAutoModel.is_final_flags == [False]


def test_resident_mode_aligns_first_preview_stride_to_detected_speech(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command(
                "start_session", session_id="speech-aligned"
            ),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="speech-aligned",
                pcm_bytes=_audio_payload(0, sample_count=960),
            ),
            funasr_stream_worker.encode_resident_command(
                "audio",
                session_id="speech-aligned",
                pcm_bytes=_audio_payload(1, sample_count=960),
            ),
            funasr_stream_worker.encode_resident_command(
                "end_session", session_id="speech-aligned"
            ),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    events = _run_worker(
        monkeypatch,
        commands,
        ["--resident", "--chunk-size", "0,1,0"],
    )

    partials = [event for event in events if event["event_type"] == "partial"]
    telemetry = next(event for event in events if event["event_type"] == "telemetry")
    assert [event["text"] for event in partials] == ["第一场内容"]
    assert FakeResidentAutoModel.is_final_flags == [False]
    assert telemetry["input_samples"] == 1_920
    assert telemetry["inference_calls"] == 1


def test_resident_mode_applies_and_isolates_session_hotwords(monkeypatch):
    commands = b"".join(
        [
            funasr_stream_worker.encode_resident_command(
                "start_session", session_id="hotword-one", hotwords=["订单中台"]
            ),
            funasr_stream_worker.encode_resident_command(
                "audio", session_id="hotword-one", pcm_bytes=_audio_payload(1, 480)
            ),
            funasr_stream_worker.encode_resident_command("end_session", session_id="hotword-one"),
            funasr_stream_worker.encode_resident_command(
                "start_session", session_id="hotword-two", hotwords=["缓存穿透"]
            ),
            funasr_stream_worker.encode_resident_command(
                "audio", session_id="hotword-two", pcm_bytes=_audio_payload(2, 480)
            ),
            funasr_stream_worker.encode_resident_command("end_session", session_id="hotword-two"),
            funasr_stream_worker.encode_resident_command("shutdown"),
        ]
    )

    _run_worker(monkeypatch, commands, ["--resident", "--chunk-size", "0,1,0", "--hotwords", "P99"])

    assert FakeResidentAutoModel.hotword_values == [
        ("P99", "订单中台"),
        ("P99", "缓存穿透"),
    ]


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
                pcm_bytes=_audio_payload(1, sample_count=1_920),
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
