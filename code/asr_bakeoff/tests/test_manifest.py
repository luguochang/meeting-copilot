import json
from pathlib import Path

import pytest

from asr_bakeoff.manifest import ManifestValidationError, load_manifest


def test_load_manifest_accepts_valid_manifest(tmp_path: Path):
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"fake wav")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {
                        "id": "S01",
                        "audio_path": str(sample),
                        "language": "zh-CN",
                        "scenario": "api_review",
                        "duration_seconds": 12.5,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.version == 1
    assert manifest.samples[0].id == "S01"
    assert manifest.samples[0].audio_path == sample


def test_load_manifest_rejects_missing_audio_file(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {
                        "id": "S01",
                        "audio_path": str(tmp_path / "missing.wav"),
                        "language": "zh-CN",
                        "scenario": "api_review",
                        "duration_seconds": 12.5,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestValidationError, match="audio file does not exist"):
        load_manifest(manifest_path)
