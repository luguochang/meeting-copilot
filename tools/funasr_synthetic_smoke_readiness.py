#!/usr/bin/env python3
"""Report whether the local cached FunASR synthetic smoke can be run safely."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "funasr_synthetic_smoke_readiness.v1"

APPROVED_AUDIO_ROOTS = {"artifacts/tmp/synthetic_audio"}
APPROVED_EVENTS_ROOTS = {"artifacts/tmp/asr_events"}
APPROVED_REPORT_ROOTS = {"artifacts/tmp/asr_reports"}
FORBIDDEN_ROOTS = {
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
}

FUNASR_PYTHON = "code/asr_runtime/.venv-funasr/bin/python"
FUNASR_SCRIPT = "code/asr_runtime/scripts/transcribe_funasr.py"
MODEL_ALIAS = "paraformer-zh-streaming"
DEVICE = "cpu"
CHUNK_SIZE = "0,10,5"
ENCODER_CHUNK_LOOK_BACK = "4"
DECODER_CHUNK_LOOK_BACK = "1"
FINAL_WINDOW_MS = "3000"
STREAMING_MODEL_ID = "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"

REQUIRED_CACHED_MODELS = {
    STREAMING_MODEL_ID: [
        "model.pt",
        "config.yaml",
    ]
}


def _default_model_cache_root() -> Path:
    return Path.home() / ".cache" / "modelscope" / "models" / "iic"


def _default_legacy_hub_model_cache_root() -> Path:
    return Path.home() / ".cache" / "modelscope" / "hub" / "models" / "iic"


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _validate_path(label: str, path: str, roots: set[str], root_label: str) -> list[str]:
    errors: list[str] = []
    if not _is_under_any_root(path, roots):
        errors.append(f"{label} is not under approved {root_label} root")
    if _is_under_any_root(path, FORBIDDEN_ROOTS):
        errors.append(f"{label} is forbidden")
    return errors


def _model_cache_root_errors(label: str, path: Path) -> list[str]:
    for candidate in (path, path.resolve(strict=False)):
        if candidate.suffix.casefold() == ".m4a":
            return [f"{label} is blocked: audio file"]
        for root in FORBIDDEN_ROOTS:
            suffix_parts = tuple(root.split("/"))
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"{label} is blocked: {root}"]
    return []


def _cached_model_components(model_cache_root: Path) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    for model_id, required_files in REQUIRED_CACHED_MODELS.items():
        model_dir = model_cache_root / model_id
        missing = [
            filename
            for filename in required_files
            if not (model_dir / filename).is_file()
        ]
        components.append(
            {
                "model_id": model_id,
                "required_files": required_files,
                "status": "missing" if missing else "present",
                "missing_files": missing,
            }
        )
    return components


def _all_cached_models_present(components: list[dict[str, object]]) -> bool:
    return all(component.get("status") == "present" for component in components)


def _report_path(path: str, roots: set[str]) -> str:
    return path if _is_under_any_root(path, roots) else "<redacted_invalid_path>"


def _local_model_dir_label() -> str:
    return f"modelscope_runtime_models_iic/{STREAMING_MODEL_ID}"


def _command_preview(audio_path: str, events_output_path: str) -> list[str]:
    return [
        FUNASR_PYTHON,
        FUNASR_SCRIPT,
        audio_path,
        "--streaming",
        "--model",
        MODEL_ALIAS,
        "--local-model-dir",
        f"<{_local_model_dir_label()}>",
        "--device",
        DEVICE,
        "--chunk-size",
        CHUNK_SIZE,
        "--encoder-chunk-look-back",
        ENCODER_CHUNK_LOOK_BACK,
        "--decoder-chunk-look-back",
        DECODER_CHUNK_LOOK_BACK,
        "--final-window-ms",
        FINAL_WINDOW_MS,
        "--events-output",
        events_output_path,
    ]


def build_funasr_synthetic_smoke_readiness_report(
    *,
    audio_path: str,
    events_output_path: str,
    provider_output_path: str,
    transcript_report_path: str,
    smoke_report_path: str,
    model_cache_root: Path | None = None,
    legacy_hub_model_cache_root: Path | None = None,
    audio_exists: bool | None = None,
    venv_python_exists: bool | None = None,
) -> dict[str, object]:
    explicit_model_cache_root = model_cache_root is not None
    cache_root = model_cache_root if model_cache_root is not None else _default_model_cache_root()
    legacy_cache_root = (
        legacy_hub_model_cache_root
        if legacy_hub_model_cache_root is not None
        else _default_legacy_hub_model_cache_root()
    )
    if audio_exists is None:
        audio_exists = (REPO_ROOT / audio_path).is_file() if _is_relative_safe_path(audio_path) else False
    if venv_python_exists is None:
        venv_python_exists = (REPO_ROOT / FUNASR_PYTHON).is_file()

    model_cache_root_errors = _model_cache_root_errors("model_cache_root", cache_root)
    legacy_model_cache_root_errors = _model_cache_root_errors(
        "legacy_hub_model_cache_root",
        legacy_cache_root,
    )
    if model_cache_root_errors:
        components: list[dict[str, object]] = []
        cached_models_present = False
    else:
        components = _cached_model_components(cache_root)
        cached_models_present = _all_cached_models_present(components)

    if model_cache_root_errors or legacy_model_cache_root_errors:
        legacy_components: list[dict[str, object]] = []
        legacy_cached_models_present = False
    else:
        legacy_components = _cached_model_components(legacy_cache_root)
        legacy_cached_models_present = _all_cached_models_present(legacy_components)

    errors: list[str] = []
    errors.extend(model_cache_root_errors)
    errors.extend(legacy_model_cache_root_errors)
    errors.extend(_validate_path("audio_path", audio_path, APPROVED_AUDIO_ROOTS, "synthetic audio"))
    errors.extend(_validate_path("events_output_path", events_output_path, APPROVED_EVENTS_ROOTS, "events"))
    errors.extend(_validate_path("provider_output_path", provider_output_path, APPROVED_REPORT_ROOTS, "reports"))
    errors.extend(
        _validate_path(
            "transcript_report_path",
            transcript_report_path,
            APPROVED_REPORT_ROOTS,
            "reports",
        )
    )
    errors.extend(_validate_path("smoke_report_path", smoke_report_path, APPROVED_REPORT_ROOTS, "reports"))
    if not audio_exists:
        errors.append("synthetic audio file is missing")
    if not venv_python_exists:
        errors.append("FunASR venv python is missing")
    if not cached_models_present:
        errors.append("required FunASR cached model files are missing")

    blocked = bool(errors)
    report_audio_path = _report_path(audio_path, APPROVED_AUDIO_ROOTS)
    report_events_output_path = _report_path(events_output_path, APPROVED_EVENTS_ROOTS)
    report_provider_output_path = _report_path(provider_output_path, APPROVED_REPORT_ROOTS)
    report_transcript_report_path = _report_path(transcript_report_path, APPROVED_REPORT_ROOTS)
    report_smoke_report_path = _report_path(smoke_report_path, APPROVED_REPORT_ROOTS)
    return {
        "report_mode": "funasr_synthetic_smoke_readiness",
        "report_version": REPORT_VERSION,
        "readiness_status": "blocked"
        if blocked
        else "cache_preflight_passed_offline_execution_not_proven",
        "provider": "funasr_streaming",
        "model_alias": MODEL_ALIAS,
        "device": DEVICE,
        "audio_path": report_audio_path,
        "events_output_path": report_events_output_path,
        "provider_output_path": report_provider_output_path,
        "transcript_report_path": report_transcript_report_path,
        "smoke_report_path": report_smoke_report_path,
        "venv_python": FUNASR_PYTHON,
        "funasr_script": FUNASR_SCRIPT,
        "model_cache_root_label": "modelscope_default_iic_cache",
        "model_cache_root_input_status": "blocked_forbidden_root"
        if model_cache_root_errors
        else "explicit_root_validated_no_path_echo"
        if explicit_model_cache_root
        else "default_runtime_cache_checked_no_path_echo",
        "model_cache_layout": "modelscope_runtime_models_iic",
        "offline_guard_status": "required_before_execution",
        "local_model_dir_label": _local_model_dir_label(),
        "model_download_status": "not_started",
        "required_cached_models_status": "present" if cached_models_present else "missing",
        "required_cached_models": components,
        "legacy_hub_model_cache_root_label": "modelscope_legacy_hub_models_iic_cache",
        "legacy_hub_cached_models_status": "present" if legacy_cached_models_present else "missing",
        "legacy_hub_cached_models_note": (
            "legacy hub cache is not sufficient for this FunASR command; "
            "runtime cache must be present to avoid model download"
        ),
        "command_preview": _command_preview(report_audio_path, report_events_output_path),
        "provider_stdout_redirect_path": report_provider_output_path,
        "postprocess_transcript_report_path": report_transcript_report_path,
        "postprocess_smoke_report_path": report_smoke_report_path,
        "execution_mode": "preflight_only_no_execution_authorization",
        "safe_to_execute_local_funasr_now": False,
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_generated_audio": False,
        "safe_to_commit_asr_artifacts": False,
        "validation_errors": errors,
        "next_action": "fix_validation_errors"
        if blocked
        else "establish_offline_execution_guard_or_explicit_model_download_approval",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-path", required=True)
    parser.add_argument("--events-output-path", required=True)
    parser.add_argument("--provider-output-path", required=True)
    parser.add_argument("--transcript-report-path", required=True)
    parser.add_argument("--smoke-report-path", required=True)
    parser.add_argument("--model-cache-root", type=Path)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    audio_exists: bool | None = None,
    venv_python_exists: bool | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_synthetic_smoke_readiness_report(
        audio_path=args.audio_path,
        events_output_path=args.events_output_path,
        provider_output_path=args.provider_output_path,
        transcript_report_path=args.transcript_report_path,
        smoke_report_path=args.smoke_report_path,
        model_cache_root=args.model_cache_root,
        audio_exists=audio_exists,
        venv_python_exists=venv_python_exists,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["readiness_status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
