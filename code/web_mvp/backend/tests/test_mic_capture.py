"""Tests for mic_capture PCM chunk generation."""
import struct
import tempfile
import wave
from pathlib import Path

from meeting_copilot_web_mvp.mic_capture import pcm_chunks_from_wav


def test_pcm_chunks_from_wav_produces_float32_chunks():
    # 100 samples of value 16384, 16-bit mono PCM
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    path = Path(tmp.name)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<100h", *([16384] * 100)))
    try:
        chunks = list(pcm_chunks_from_wav(path, chunk_samples=30))
    finally:
        path.unlink()
    # 100 samples / 30 = 4 chunks (30, 30, 30, 10)
    assert len(chunks) == 4
    assert len(chunks[0]) == 30 * 4  # float32
    assert len(chunks[-1]) == 10 * 4
    vals = struct.unpack("<30f", chunks[0])
    assert abs(vals[0] - 16384 / 32768.0) < 1e-6


def test_pcm_chunks_from_wav_rejects_non_16bit():
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    path = Path(tmp.name)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)  # 8-bit
        w.setframerate(16000)
        w.writeframes(b"\x80" * 10)
    try:
        raised = False
        try:
            list(pcm_chunks_from_wav(path))
        except ValueError:
            raised = True
        assert raised
    finally:
        path.unlink()
