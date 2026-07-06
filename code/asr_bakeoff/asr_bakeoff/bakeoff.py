from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from asr_bakeoff.manifest import load_manifest
from asr_bakeoff.metrics import char_error_rate, entity_accuracy, latency_summary
from asr_bakeoff.providers.base import AsrProvider


def run_bakeoff(manifest_path: Path, provider: AsrProvider, output_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    sample_reports = []
    cers: list[float] = []
    entity_f1s: list[float] = []
    latencies = []

    for sample in manifest.samples:
        reference_text = sample.reference_path.read_text(encoding="utf-8") if sample.reference_path else None
        reference_entities = _load_reference_entities(sample.annotation_path) if sample.annotation_path else None
        try:
            result = provider.transcribe(sample.id, sample.audio_path)
        except Exception as exc:  # noqa: BLE001 - provider failures must be isolated per sample.
            sample_reports.append(
                {
                    "id": sample.id,
                    "scenario": sample.scenario,
                    "language": sample.language,
                    "status": "failed",
                    "error": str(exc),
                    "hypothesis": None,
                    "reference": reference_text,
                    "cer": None,
                    "entity_accuracy": None,
                    "evaluation_status": {
                        "cer": "not_evaluated",
                        "entity_accuracy": "not_evaluated",
                    },
                    "latency_ms": None,
                }
            )
            continue

        hypothesis_entities = result.entities
        entity_result = (
            entity_accuracy(reference_entities, hypothesis_entities)
            if reference_entities is not None
            else None
        )
        cer = char_error_rate(reference_text, result.text) if reference_text is not None else None

        if cer is not None:
            cers.append(cer)
        if entity_result is not None:
            entity_f1s.append(entity_result.f1)
        latencies.append(result.latency_ms)
        sample_reports.append(
            {
                "id": sample.id,
                "scenario": sample.scenario,
                "language": sample.language,
                "status": "success",
                "error": None,
                "hypothesis": result.text,
                "reference": reference_text,
                "cer": cer,
                "entity_accuracy": asdict(entity_result) if entity_result is not None else None,
                "evaluation_status": {
                    "cer": "scored" if cer is not None else "not_evaluated",
                    "entity_accuracy": "scored" if entity_result is not None else "not_evaluated",
                },
                "latency_ms": result.latency_ms,
            }
        )

    report = {
        "provider": provider.name,
        "summary": {
            "sample_count": len(sample_reports),
            "failed_sample_count": sum(1 for sample in sample_reports if sample["status"] == "failed"),
            "scored_cer_sample_count": len(cers),
            "scored_entity_sample_count": len(entity_f1s),
            "avg_cer": _avg(cers),
            "avg_entity_f1": _avg(entity_f1s),
            "latency": asdict(latency_summary(latencies)),
        },
        "samples": sample_reports,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _load_reference_entities(annotation_path: Path) -> list[str]:
    data = json.loads(annotation_path.read_text(encoding="utf-8"))
    entities = data.get("technical_entities", [])
    return [str(item.get("normalized") or item["text"]) for item in entities]


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)
