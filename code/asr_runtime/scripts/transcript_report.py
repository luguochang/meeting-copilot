from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.command_result import ProviderTranscript, parse_provider_stdout
from scripts.transcript_normalizer import GlossaryTerm, load_glossary, normalize_transcript_text


@dataclass(frozen=True)
class TranscriptSegment:
    id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None


@dataclass(frozen=True)
class EvidenceSpan:
    id: str
    segment_id: str
    start_ms: int
    end_ms: int
    quote: str


@dataclass(frozen=True)
class TranscriptReport:
    audio_path: str
    provider: str
    duration_seconds: float
    latency_ms: int
    rtf: float
    text: str
    normalized_text: str
    normalization_changes: list[dict[str, str]]
    segments: list[TranscriptSegment]
    evidence_spans: list[EvidenceSpan]


def build_report(
    audio_path: str,
    provider: str,
    text: str,
    duration_seconds: float,
    latency_ms: int,
    provider_segments: list[dict[str, Any]] | None = None,
    normalization_terms: list[dict[str, Any]] | None = None,
) -> TranscriptReport:
    end_ms = int(duration_seconds * 1000)
    segments = _build_segments(provider_segments, text, end_ms)
    normalized = normalize_transcript_text(
        text,
        glossary_terms=_normalization_terms(normalization_terms),
    )
    evidence_spans = [
        EvidenceSpan(
            id=f"ev_{index:03d}",
            segment_id=segment.id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            quote=segment.text[:240],
        )
        for index, segment in enumerate(segments, start=1)
    ]
    rtf = round((latency_ms / 1000) / duration_seconds, 6) if duration_seconds else 0.0
    return TranscriptReport(
        audio_path=audio_path,
        provider=provider,
        duration_seconds=duration_seconds,
        latency_ms=latency_ms,
        rtf=rtf,
        text=text,
        normalized_text=normalized.text,
        normalization_changes=normalized.changes,
        segments=segments,
        evidence_spans=evidence_spans,
    )


def _build_segments(
    provider_segments: list[dict[str, Any]] | None,
    fallback_text: str,
    fallback_end_ms: int,
) -> list[TranscriptSegment]:
    if not provider_segments:
        return [
            TranscriptSegment(
                id="seg_001",
                start_ms=0,
                end_ms=fallback_end_ms,
                text=fallback_text,
                confidence=None,
            )
        ]

    segments: list[TranscriptSegment] = []
    for index, segment in enumerate(provider_segments, start=1):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                id=f"seg_{index:03d}",
                start_ms=int(segment.get("start_ms", 0)),
                end_ms=int(segment.get("end_ms", fallback_end_ms)),
                text=text,
                confidence=_optional_float(segment.get("confidence")),
            )
        )
    if segments:
        return segments
    return [
        TranscriptSegment(
            id="seg_001",
            start_ms=0,
            end_ms=fallback_end_ms,
            text=fallback_text,
            confidence=None,
        )
    ]


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _normalization_terms(items: list[dict[str, Any]] | None) -> list[GlossaryTerm]:
    return [
        GlossaryTerm(
            canonical=str(item["canonical"]),
            aliases=[str(alias) for alias in item.get("aliases", [])],
        )
        for item in items or []
    ]


def load_provider_transcript(path: Path) -> ProviderTranscript:
    return parse_provider_stdout(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a transcript report JSON.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--provider")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text-file", type=Path)
    input_group.add_argument("--provider-json", type=Path)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        help="Audio duration in seconds. Optional for --provider-json when provider output includes audio_duration_seconds.",
    )
    parser.add_argument("--latency-ms", type=int)
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.provider_json:
        provider_transcript = load_provider_transcript(args.provider_json)
        text = provider_transcript.text.strip()
        provider = args.provider or str(provider_transcript.raw.get("provider", "unknown"))
        latency_ms = args.latency_ms if args.latency_ms is not None else provider_transcript.latency_ms
        duration_seconds = (
            args.duration_seconds
            if args.duration_seconds is not None
            else provider_transcript.duration_seconds
        )
        if duration_seconds is None:
            parser.error(
                "--duration-seconds is required when --provider-json has no audio_duration_seconds"
            )
    else:
        if not args.provider:
            parser.error("--provider is required when using --text-file")
        if args.duration_seconds is None:
            parser.error("--duration-seconds is required when using --text-file")
        if args.latency_ms is None:
            parser.error("--latency-ms is required when using --text-file")
        text = args.text_file.read_text(encoding="utf-8").strip()
        provider = args.provider
        latency_ms = args.latency_ms
        duration_seconds = args.duration_seconds

    report = build_report(
        audio_path=args.audio,
        provider=provider,
        text=text,
        duration_seconds=duration_seconds,
        latency_ms=latency_ms,
        provider_segments=provider_transcript.segments if args.provider_json else None,
        normalization_terms=[
            {"canonical": term.canonical, "aliases": term.aliases}
            for term in load_glossary(args.glossary)
        ]
        if args.glossary
        else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "rtf": report.rtf}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
