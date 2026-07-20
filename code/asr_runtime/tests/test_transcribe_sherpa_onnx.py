import sys
import types
import json
import subprocess

import numpy as np

sys.modules.setdefault("soundfile", types.SimpleNamespace(read=lambda *_args, **_kwargs: None))

from scripts import transcribe_sherpa_onnx


class FakeSherpaResult:
    def __init__(self, text):
        self.text = text


class FakeSherpaStream:
    def __init__(self):
        self.accept_count = 0
        self.ready = False
        self.finished = False
        self.endpoint_consumed = False

    def accept_waveform(self, sample_rate, chunk):
        self.accept_count += 1
        self.ready = True

    def input_finished(self):
        self.finished = True
        self.ready = True


class FakeSherpaRecognizer:
    calls = []

    @classmethod
    def from_zipformer2_ctc(cls, **kwargs):
        cls.calls.append(kwargs)
        return cls()

    def create_stream(self):
        return FakeSherpaStream()

    def is_ready(self, stream):
        if stream.ready:
            stream.ready = False
            return True
        return False

    def decode_stream(self, stream):
        return None

    def is_endpoint(self, stream):
        if stream.accept_count == 2 and not stream.endpoint_consumed:
            stream.endpoint_consumed = True
            return True
        return False

    def get_result_all(self, stream):
        if stream.finished:
            return FakeSherpaResult("")
        if stream.accept_count == 1:
            return FakeSherpaResult("先灰度")
        return FakeSherpaResult("先灰度 10%")

    def reset(self, stream):
        return None


def test_stream_events_emits_partial_final_and_end_of_stream(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        types.SimpleNamespace(OnlineRecognizer=FakeSherpaRecognizer),
    )
    monkeypatch.setattr(
        transcribe_sherpa_onnx.sf,
        "read",
        lambda *_args, **_kwargs: (np.ones(16000, dtype=np.float32), 16000),
    )
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.onnx").write_bytes(b"fake")
    (model_dir / "tokens.txt").write_text("fake tokens", encoding="utf-8")

    events = transcribe_sherpa_onnx.stream_events(
        audio_path=tmp_path / "sample.wav",
        model_dir=model_dir,
        num_threads=2,
        chunk_ms=500,
    )

    assert [(event.event_type, event.segment_id, event.text) for event in events] == [
        ("partial", "sherpa_001", "先灰度"),
        ("final", "sherpa_001", "先灰度 10%"),
        ("end_of_stream", "sherpa_eos", ""),
    ]
    assert events[0].start_ms == 0
    assert events[0].end_ms == 500
    assert events[1].start_ms == 0
    assert events[1].end_ms == 1000
    assert events[2].received_at_ms >= events[1].received_at_ms
    assert FakeSherpaRecognizer.calls[0]["num_threads"] == 2


def test_main_can_write_streaming_events(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(
        sys.modules,
        "sherpa_onnx",
        types.SimpleNamespace(OnlineRecognizer=FakeSherpaRecognizer),
    )
    monkeypatch.setattr(
        transcribe_sherpa_onnx.sf,
        "read",
        lambda *_args, **_kwargs: (np.ones(16000, dtype=np.float32), 16000),
    )
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.onnx").write_bytes(b"fake")
    (model_dir / "tokens.txt").write_text("fake tokens", encoding="utf-8")
    events_output = tmp_path / "events.json"

    transcribe_sherpa_onnx.main(
        [
            str(tmp_path / "sample.wav"),
            "--model-dir",
            str(model_dir),
            "--events-output",
            str(events_output),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    events = json.loads(events_output.read_text(encoding="utf-8"))
    assert payload["raw"]["mode"] == "file_replayed_streaming_events"
    assert payload["raw"]["model_id"] == "model"
    assert "model_dir" not in payload["raw"]
    assert payload["segments"][0]["id"] == "sherpa_001"
    assert [event["event_type"] for event in events] == [
        "partial",
        "final",
        "end_of_stream",
    ]


def test_cli_runs_as_direct_script_with_current_python():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/transcribe_sherpa_onnx.py",
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Transcribe audio with sherpa-onnx streaming CTC" in result.stdout


def test_find_first_error_does_not_include_local_path(tmp_path):
    model_dir = tmp_path / "private-model-path"
    model_dir.mkdir()

    try:
        transcribe_sherpa_onnx._find_first(model_dir, ["*.onnx"])
    except FileNotFoundError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected missing model to raise FileNotFoundError")

    assert "private-model-path" not in message
    assert str(model_dir) not in message
