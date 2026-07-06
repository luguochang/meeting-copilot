from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.demo_eval import evaluate_demo_outputs
from scripts.meeting_analysis import validate_analysis
from scripts.meeting_events import build_state_events
from scripts.transcript_normalizer import load_glossary
from scripts.transcript_report import build_report, load_provider_transcript


@dataclass(frozen=True)
class DemoPipelineResult:
    transcript_report: dict[str, Any]
    analysis: dict[str, Any]
    events: list[dict[str, Any]]
    evaluation: dict[str, Any]


def run_demo_pipeline(
    provider_json_path: Path,
    audio_path: str,
    duration_seconds: float,
    analysis_json_path: Path,
    golden_path: Path | None,
    glossary_path: Path | None,
    output_dir: Path,
) -> DemoPipelineResult:
    provider_transcript = load_provider_transcript(provider_json_path)
    provider = str(provider_transcript.raw.get("provider", "unknown"))
    transcript_report = asdict(
        build_report(
            audio_path=audio_path,
            provider=provider,
            text=provider_transcript.text,
            duration_seconds=duration_seconds,
            latency_ms=provider_transcript.latency_ms,
            provider_segments=provider_transcript.segments,
            normalization_terms=[
                {"canonical": term.canonical, "aliases": term.aliases}
                for term in load_glossary(glossary_path)
            ]
            if glossary_path
            else None,
        )
    )
    analysis = json.loads(analysis_json_path.read_text(encoding="utf-8"))
    evidence_span_ids = {item["id"] for item in transcript_report["evidence_spans"]}
    validate_analysis(analysis, evidence_span_ids)
    events = [
        asdict(event)
        for event in build_state_events(
            analysis,
            created_at_ms=0,
            source="demo_pipeline",
        )
    ]
    golden = json.loads(golden_path.read_text(encoding="utf-8")) if golden_path else {}
    evaluation = evaluate_demo_outputs(
        analysis=analysis,
        transcript_report=transcript_report,
        golden=golden,
        events=events,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "transcript-report.json", transcript_report)
    _write_json(output_dir / "analysis.json", analysis)
    _write_json(output_dir / "events.json", events)
    _write_json(output_dir / "evaluation.json", evaluation)
    return DemoPipelineResult(
        transcript_report=transcript_report,
        analysis=analysis,
        events=events,
        evaluation=evaluation,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local Meeting Copilot demo pipeline.")
    parser.add_argument("--provider-json", required=True, type=Path)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--duration-seconds", required=True, type=float)
    parser.add_argument("--analysis-json", required=True, type=Path)
    parser.add_argument("--golden", type=Path)
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    result = run_demo_pipeline(
        provider_json_path=args.provider_json,
        audio_path=args.audio,
        duration_seconds=args.duration_seconds,
        analysis_json_path=args.analysis_json,
        golden_path=args.golden,
        glossary_path=args.glossary,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "passes_minimum_gate": result.evaluation["passes_minimum_gate"],
                "state_event_count": result.evaluation["state_event_count"],
                "suggestion_card_count": result.evaluation["suggestion_card_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
