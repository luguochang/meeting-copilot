#!/usr/bin/env python3
"""Build a local ASR event-generation plan without running ASR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import PurePosixPath
from typing import TextIO


PLAN_VERSION = "asr_event_generation_plan.v1"
EVENT_CONTRACT = "partial_final_revision_error_eos"

APPROVED_INPUT_LAYERS = {"public_audio_sample", "synthetic_audio"}
APPROVED_PROVIDERS = {"funasr_streaming", "sherpa_onnx_streaming", "mock_streaming"}
APPROVED_PUBLIC_AUDIO_ROOTS = {"artifacts/tmp/public_audio", "data/asr_eval/public_raw"}
APPROVED_SYNTHETIC_AUDIO_ROOTS = {"artifacts/tmp/synthetic_audio"}
APPROVED_OUTPUT_ROOTS = {"artifacts/tmp/asr_events"}

METRICS_REQUIRED = [
    "duration_seconds",
    "rtf",
    "first_partial_latency_ms",
    "final_latency_p95_ms",
    "segment_count",
    "raw_cer",
    "normalized_cer",
    "raw_technical_entity_recall",
    "raw_technical_entity_precision",
    "technical_entity_recall",
    "technical_entity_precision",
    "cpu_peak_percent",
    "memory_peak_mb",
]


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _input_roots_for_layer(input_layer: str) -> set[str]:
    if input_layer == "public_audio_sample":
        return APPROVED_PUBLIC_AUDIO_ROOTS
    if input_layer == "synthetic_audio":
        return APPROVED_SYNTHETIC_AUDIO_ROOTS
    return set()


def build_asr_event_generation_plan(
    *,
    input_layer: str,
    audio_path: str,
    provider_candidate: str,
    output_event_path: str,
) -> dict[str, object]:
    errors: list[str] = []
    if input_layer not in APPROVED_INPUT_LAYERS:
        errors.append("input_layer is not approved")
    if not _is_under_any_root(audio_path, _input_roots_for_layer(input_layer)):
        errors.append("audio_path is not under an approved input root")
    if not _is_under_any_root(output_event_path, APPROVED_OUTPUT_ROOTS):
        errors.append("output_event_path is not under an approved output root")
    if provider_candidate not in APPROVED_PROVIDERS:
        errors.append("provider_candidate is not approved for local-only ASR")

    blocked = bool(errors)
    return {
        "plan_mode": "asr_event_generation_plan_only",
        "plan_version": PLAN_VERSION,
        "plan_status": "blocked" if blocked else "ready_for_manual_local_asr_review",
        "input_layer": input_layer,
        "audio_path": audio_path,
        "provider_candidate": provider_candidate,
        "output_event_path": output_event_path,
        "approved_input_roots": sorted(_input_roots_for_layer(input_layer)),
        "approved_output_roots": sorted(APPROVED_OUTPUT_ROOTS),
        "approved_local_providers": sorted(APPROVED_PROVIDERS),
        "event_contract": EVENT_CONTRACT,
        "metrics_required": METRICS_REQUIRED,
        "run_status": "not_started",
        "safe_to_run_asr_now": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_write_event_artifact_now": False,
        "validation_errors": errors,
        "next_action": "fix_validation_errors" if blocked else "manual_local_asr_event_smoke",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-layer", required=True)
    parser.add_argument("--audio-path", required=True)
    parser.add_argument("--provider-candidate", required=True)
    parser.add_argument("--output-event-path", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_asr_event_generation_plan(
        input_layer=args.input_layer,
        audio_path=args.audio_path,
        provider_candidate=args.provider_candidate,
        output_event_path=args.output_event_path,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["plan_status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
