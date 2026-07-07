"""Tests for the SherpaSidecarRecognizer (real ASR sidecar, mocked subprocess)."""
import threading
import time
from meeting_copilot_web_mvp import asr_stream


class _FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []
        self.closed = False

    def write(self, b):
        self.written.append(b)
        return len(b)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeStdout:
    def __init__(self, stdin):
        self._stdin = stdin
        self.done = threading.Event()

    def __iter__(self):
        yield b'{"event_type":"partial","text":"hi","segment_id":"x","sample_rate":16000}\n'
        while not self._stdin.closed:
            time.sleep(0.01)
        yield b'{"event_type":"final","text":"hi there","segment_id":"x","sample_rate":16000}\n'
        self.done.set()


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(self.stdin)

    def wait(self, timeout=None):
        # block until the reader has consumed the final (mimics real worker exit)
        self.stdout.done.wait(timeout or 10)
        return 0

    def kill(self):
        pass


def test_sherpa_sidecar_feeds_chunks_and_reads_final(monkeypatch):
    fake = _FakeProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: fake)
    rec = asr_stream.SherpaSidecarRecognizer("sess", asr_stream.Path("/tmp/model"))
    rec.recognize_chunk(b"\x00" * 6400)
    rec.recognize_chunk(b"\x00" * 6400)
    time.sleep(0.15)  # let the reader thread drain the partial into the queue
    final = rec.finalize()
    assert final["event_type"] == "final"
    assert final["segment_id"] == "stream_seg_sess"
    assert final["text"] == "hi there"
    # chunks were fed to the sidecar stdin
    assert len(fake.stdin.written) == 2
    assert fake.stdin.written[0] == b"\x00" * 6400


def test_get_recognizer_falls_back_to_fake_when_sherpa_absent(monkeypatch):
    # FunASR + sherpa both unavailable -> Fake
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_SHERPA_VENV_PY", asr_stream.Path("/nonexistent/python"))
    rec = asr_stream.get_recognizer("sess_fb")
    assert isinstance(rec, asr_stream.FakeStreamRecognizer)
