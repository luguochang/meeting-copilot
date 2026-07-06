from pathlib import Path

from scripts.inspect_audio import inspect_audio


def test_inspect_audio_reads_wav_metadata(tmp_path: Path):
    wav = tmp_path / "sample.wav"
    wav.write_bytes(
        b"RIFF"
        + (36).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + (0).to_bytes(4, "little")
    )

    metadata = inspect_audio(wav)

    assert metadata.path == wav
    assert metadata.sample_rate == 16000
    assert metadata.channels == 1
    assert metadata.duration_seconds == 0.0
