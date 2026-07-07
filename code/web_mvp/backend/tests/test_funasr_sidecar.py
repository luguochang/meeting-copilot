"""Tests for FunasrSidecarRecognizer (G2, mocked subprocess)."""
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
        yield '{"event_type":"partial","text":"灰度","segment_id":"x","sample_rate":16000}\n'.encode("utf-8")
        while not self._stdin.closed:
            time.sleep(0.01)
        yield '{"event_type":"final","text":"灰度 5%","segment_id":"x","sample_rate":16000}\n'.encode("utf-8")
        self.done.set()


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(self.stdin)

    def wait(self, timeout=None):
        self.stdout.done.wait(timeout or 15)
        return 0

    def kill(self):
        pass


def test_funasr_sidecar_feeds_chunks_and_reads_final(monkeypatch):
    fake = _FakeProc()
    monkeypatch.setattr(asr_stream.subprocess, "Popen", lambda *a, **k: fake)
    rec = asr_stream.FunasrSidecarRecognizer("sess_f")
    rec.recognize_chunk(b"\x00" * 9600)
    rec.recognize_chunk(b"\x00" * 9600)
    time.sleep(0.15)
    final = rec.finalize()
    assert final["event_type"] == "final"
    assert final["segment_id"] == "stream_seg_sess_f"
    assert final["text"] == "灰度 5%"
    assert len(fake.stdin.written) == 2


def test_get_recognizer_prefers_funasr_when_available(monkeypatch):
    # FunASR available -> returns FunasrSidecarRecognizer (not sherpa/fake)
    class _StubRecognizer:
        def __init__(self, sid):
            self.sid = sid
        def recognize_chunk(self, pcm):
            return {"event_type": "partial", "text": "stub", "segment_id": "s", "confidence": 0.8}
        def finalize(self):
            return {"event_type": "final", "text": "stub", "segment_id": "s", "confidence": 0.9}
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: _StubRecognizer(sid))
    rec = asr_stream.get_recognizer("sess_pref")
    assert isinstance(rec, _StubRecognizer)


def test_get_recognizer_falls_back_to_sherpa_then_fake(monkeypatch):
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda sid: None)
    rec = asr_stream.get_recognizer("sess_fb2")
    assert isinstance(rec, asr_stream.FakeStreamRecognizer)
