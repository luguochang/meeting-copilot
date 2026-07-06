from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManifestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Sample:
    id: str
    audio_path: Path
    language: str
    scenario: str
    duration_seconds: float
    reference_path: Path | None = None
    annotation_path: Path | None = None


@dataclass(frozen=True)
class Manifest:
    version: int
    samples: list[Sample]


def load_manifest(path: Path) -> Manifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if version != 1:
        raise ManifestValidationError(f"unsupported manifest version: {version}")
    samples = [_parse_sample(item, path.parent) for item in data.get("samples", [])]
    if not samples:
        raise ManifestValidationError("manifest must include at least one sample")
    return Manifest(version=version, samples=samples)


def _parse_sample(item: dict[str, Any], base_dir: Path) -> Sample:
    required = ["id", "audio_path", "language", "scenario", "duration_seconds"]
    for key in required:
        if key not in item:
            raise ManifestValidationError(f"sample is missing required field: {key}")

    audio_path = _resolve_path(item["audio_path"], base_dir)
    if not audio_path.exists():
        raise ManifestValidationError(f"audio file does not exist: {audio_path}")

    reference_path = _optional_existing_path(item.get("reference_path"), base_dir, "reference file")
    annotation_path = _optional_existing_path(item.get("annotation_path"), base_dir, "annotation file")

    return Sample(
        id=str(item["id"]),
        audio_path=audio_path,
        language=str(item["language"]),
        scenario=str(item["scenario"]),
        duration_seconds=float(item["duration_seconds"]),
        reference_path=reference_path,
        annotation_path=annotation_path,
    )


def _optional_existing_path(value: str | None, base_dir: Path, label: str) -> Path | None:
    if value is None:
        return None
    path = _resolve_path(value, base_dir)
    if not path.exists():
        raise ManifestValidationError(f"{label} does not exist: {path}")
    return path


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path
