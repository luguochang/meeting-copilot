#!/usr/bin/env python3
"""Build a bounded Chinese public-audio sample extraction plan without downloading audio."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_ID = "public-audio-sample-extraction-2026-07-10"
PLAN_VERSION = "public_audio_sample_extraction_plan.v1"
SOURCE_SNAPSHOT_DATE = "2026-07-10"

APPROVED_SOURCES = {
    "alimeeting_openslr_slr119": {
        "source_url": "https://www.openslr.org/119/",
        "source_license": "CC BY-SA 4.0",
        "default_source_split": "eval",
        "source_language": "zh-CN Mandarin",
        "source_priority_rank": 1,
        "dataset_role": "primary_mandarin_meeting_acoustics",
        "meeting_acoustics_evidence": True,
        "baseline_only": False,
        "counts_toward_public_meeting_wall_clock_candidate": True,
        "recommended_next_gate": "public_audio_wall_clock_soak_after_post_extraction_evidence",
        "not_for_gate_reason": "not proof of software-engineering product value without technical-domain shadow feedback",
    },
    "aishell4_openslr_slr111": {
        "source_url": "https://www.openslr.org/111/",
        "source_license": "CC BY-SA 4.0",
        "default_source_split": "test",
        "source_language": "zh-CN Mandarin",
        "source_priority_rank": 2,
        "dataset_role": "supplemental_mandarin_meeting_acoustics",
        "meeting_acoustics_evidence": True,
        "baseline_only": False,
        "counts_toward_public_meeting_wall_clock_candidate": True,
        "recommended_next_gate": "public_audio_wall_clock_soak_after_post_extraction_evidence",
        "not_for_gate_reason": "not proof of software-engineering product value without technical-domain shadow feedback",
    },
    "aishell1_openslr_slr33": {
        "source_url": "https://www.openslr.org/33/",
        "source_license": "Apache License v2.0",
        "default_source_split": "resource_or_small_dev_subset",
        "source_language": "zh-CN Mandarin",
        "source_priority_rank": 3,
        "dataset_role": "mandarin_read_speech_baseline_only",
        "meeting_acoustics_evidence": False,
        "baseline_only": True,
        "counts_toward_public_meeting_wall_clock_candidate": False,
        "recommended_next_gate": "mandarin_asr_runtime_smoke_only",
        "not_for_gate_reason": "baseline read speech is not proof of meeting acoustics, speaker overlap, or product value",
    },
}

ALLOWED_TARGET_ROOTS = {
    "data/asr_eval/public_raw",
    "artifacts/tmp/public_audio",
}
ALLOWED_OUTPUT_ROOTS = {
    "artifacts/tmp/asr_reports",
    "artifacts/tmp/public_audio",
}
FORBIDDEN_TARGET_ROOTS = {
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
}
ALLOWED_INPUT_FILE_ROOTS = {
    "data/asr_eval",
    "artifacts/tmp/public_audio",
}
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)

PLANNED_SAMPLE_REQUIRED_FIELDS = [
    "sample_id",
    "source_id",
    "source_url",
    "source_license",
    "archive_name",
    "archive_member_path",
    "clip_start_seconds",
    "clip_end_seconds",
    "expected_duration_seconds",
    "expected_sha256_after_extract",
    "license_citation",
    "cleanup_required",
]

_LOWERCASE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER_MARKERS = ("<", ">", "placeholder", "todo", "tbd")


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path:
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _contains_placeholder_text(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_allowed_relative_root(relative_path: Path, roots: set[str]) -> bool:
    path_text = relative_path.as_posix()
    return any(path_text == root or path_text.startswith(f"{root}/") for root in roots)


def _validate_repo_path_before_access(path: str | Path, *, label: str, allowed_roots: set[str]) -> list[str]:
    raw_path = Path(path)
    absolute_path = raw_path if raw_path.is_absolute() else REPO_ROOT / raw_path

    for _, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(raw_path, suffix_parts):
            return [f"{label} path is forbidden"]

    if raw_path.is_absolute() and _repo_relative_path(raw_path) is None:
        return [f"{label} path is outside repository"]

    resolved_path = absolute_path.resolve(strict=False)
    for _, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(resolved_path, suffix_parts):
            return [f"{label} path is forbidden"]

    resolved_relative = _repo_relative_path(resolved_path)
    if resolved_relative is None:
        return [f"{label} path resolves outside repository"]

    raw_relative = _repo_relative_path(absolute_path)
    if raw_relative is None:
        return [f"{label} path is outside repository"]
    if not _is_under_allowed_relative_root(raw_relative, allowed_roots) or not _is_under_allowed_relative_root(
        resolved_relative,
        allowed_roots,
    ):
        return [f"{label} path is not under approved public audio artifact roots"]
    return []


def resolve_input_file_path_after_validation(path: str | Path, *, label: str) -> tuple[Path | None, list[str]]:
    errors = _validate_repo_path_before_access(path, label=label, allowed_roots=ALLOWED_INPUT_FILE_ROOTS)
    if errors:
        return None, errors
    raw_path = Path(path)
    absolute_path = raw_path if raw_path.is_absolute() else REPO_ROOT / raw_path
    return absolute_path.resolve(strict=False), []


def resolve_output_file_path_after_validation(path: str | Path, *, label: str = "output") -> tuple[Path | None, list[str]]:
    errors = _validate_repo_path_before_access(path, label=label, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    if errors:
        return None, errors
    raw_path = Path(path)
    absolute_path = raw_path if raw_path.is_absolute() else REPO_ROOT / raw_path
    return absolute_path.resolve(strict=False), []


def _validate_non_empty_string(
    *,
    sample: dict[str, object],
    key: str,
    index: int,
    errors: list[str],
) -> str | None:
    value = sample.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"planned_samples[{index}].{key} must be non-empty")
        return None
    return value


def _validate_planned_samples(
    *,
    planned_samples: list[dict[str, object]],
    source_id: str,
    source_url: str | None,
    source_license: str | None,
    max_clip_seconds: int,
    sample_budget_count: int,
    sample_budget_minutes: int,
) -> tuple[list[dict[str, object]], int, list[str]]:
    errors: list[str] = []
    normalized_samples: list[dict[str, object]] = []
    total_duration_seconds = 0

    if len(planned_samples) > sample_budget_count:
        errors.append("planned_samples count exceeds sample_budget_count")

    for index, sample in enumerate(planned_samples):
        if not isinstance(sample, dict):
            errors.append(f"planned_samples[{index}] must be an object")
            continue

        for field in PLANNED_SAMPLE_REQUIRED_FIELDS:
            if field not in sample:
                errors.append(f"planned_samples[{index}].{field} is required")

        sample_id = _validate_non_empty_string(sample=sample, key="sample_id", index=index, errors=errors)
        planned_source_id = _validate_non_empty_string(sample=sample, key="source_id", index=index, errors=errors)
        planned_source_url = _validate_non_empty_string(sample=sample, key="source_url", index=index, errors=errors)
        planned_source_license = _validate_non_empty_string(
            sample=sample,
            key="source_license",
            index=index,
            errors=errors,
        )
        archive_name = _validate_non_empty_string(sample=sample, key="archive_name", index=index, errors=errors)
        archive_member_path = _validate_non_empty_string(
            sample=sample,
            key="archive_member_path",
            index=index,
            errors=errors,
        )
        license_citation = _validate_non_empty_string(
            sample=sample,
            key="license_citation",
            index=index,
            errors=errors,
        )

        if sample_id and not _is_relative_safe_path(sample_id):
            errors.append(f"planned_samples[{index}].sample_id must be a safe relative id")
        if planned_source_id and planned_source_id != source_id:
            errors.append(f"planned_samples[{index}].source_id must match selected source_id")
        if planned_source_url and planned_source_url != source_url:
            errors.append(f"planned_samples[{index}].source_url must match selected source_url")
        if planned_source_license and planned_source_license != source_license:
            errors.append(f"planned_samples[{index}].source_license must match selected source_license")
        if archive_name and not _is_relative_safe_path(archive_name):
            errors.append(f"planned_samples[{index}].archive_name must be a safe relative archive name")
        if archive_member_path and not _is_relative_safe_path(archive_member_path):
            errors.append(
                f"planned_samples[{index}].archive_member_path must be a safe relative archive member path"
            )
        if archive_member_path and _contains_placeholder_text(archive_member_path):
            errors.append(f"planned_samples[{index}].archive_member_path must not contain placeholder text")

        clip_start = sample.get("clip_start_seconds")
        clip_end = sample.get("clip_end_seconds")
        expected_duration = sample.get("expected_duration_seconds")
        duration_seconds: int | None = None

        if not _is_number(clip_start):
            errors.append(f"planned_samples[{index}].clip_start_seconds must be a number")
        if not _is_number(clip_end):
            errors.append(f"planned_samples[{index}].clip_end_seconds must be a number")
        if not _is_number(expected_duration):
            errors.append(f"planned_samples[{index}].expected_duration_seconds must be a number")

        if _is_number(clip_start) and _is_number(clip_end):
            if float(clip_start) < 0:
                errors.append(f"planned_samples[{index}].clip_start_seconds must be non-negative")
            if float(clip_end) <= float(clip_start):
                errors.append(f"planned_samples[{index}].clip_end_seconds must be greater than clip_start_seconds")
            else:
                duration_seconds = int(round(float(clip_end) - float(clip_start)))
                if duration_seconds > max_clip_seconds:
                    errors.append(f"planned_samples[{index}].duration exceeds max_clip_seconds")

        if _is_number(expected_duration) and duration_seconds is not None:
            if int(round(float(expected_duration))) != duration_seconds:
                errors.append(
                    f"planned_samples[{index}].expected_duration_seconds must equal clip_end_seconds minus clip_start_seconds"
                )
            total_duration_seconds += duration_seconds

        checksum = sample.get("expected_sha256_after_extract")
        if not isinstance(checksum, str) or _LOWERCASE_SHA256_RE.fullmatch(checksum) is None:
            errors.append(
                f"planned_samples[{index}].expected_sha256_after_extract must be 64 lowercase hex characters"
            )

        cleanup_required = sample.get("cleanup_required")
        if cleanup_required is not True:
            errors.append(f"planned_samples[{index}].cleanup_required must be true")

        normalized_samples.append(
            {
                "sample_id": sample_id,
                "source_id": planned_source_id,
                "source_url": planned_source_url,
                "source_license": planned_source_license,
                "archive_name": archive_name,
                "archive_member_path": archive_member_path,
                "clip_start_seconds": clip_start,
                "clip_end_seconds": clip_end,
                "expected_duration_seconds": expected_duration,
                "expected_sha256_after_extract": checksum,
                "license_citation": license_citation,
                "cleanup_required": cleanup_required,
            }
        )

    if total_duration_seconds > sample_budget_minutes * 60:
        errors.append("planned_samples total duration exceeds sample_budget_minutes")

    return normalized_samples, total_duration_seconds, errors


def _validate_budget(
    max_duration_seconds: int,
    max_download_bytes: int,
    max_clip_seconds: int,
    sample_budget_count: int,
    sample_budget_minutes: int,
) -> list[str]:
    errors: list[str] = []
    if not 1 <= max_duration_seconds <= 1800:
        errors.append("max_duration_seconds must be between 1 and 1800")
    if not 1 <= max_download_bytes <= 1_000_000_000:
        errors.append("max_download_bytes must be between 1 and 1000000000")
    if not 1 <= max_clip_seconds <= 600:
        errors.append("max_clip_seconds must be between 1 and 600")
    if not 1 <= sample_budget_count <= 20:
        errors.append("sample_budget_count must be between 1 and 20")
    if not 1 <= sample_budget_minutes <= 60:
        errors.append("sample_budget_minutes must be between 1 and 60")
    return errors


def _source_metadata_for_report(source: dict[str, object] | None) -> dict[str, object]:
    if source is None:
        return {
            "source_language": None,
            "source_priority_rank": None,
            "dataset_role": None,
            "meeting_acoustics_evidence": False,
            "baseline_only": False,
            "counts_toward_public_meeting_wall_clock_candidate": False,
            "recommended_next_gate": "fix_source_selection",
            "not_for_gate_reason": "source is not approved",
        }
    return {
        "source_language": source["source_language"],
        "source_priority_rank": source["source_priority_rank"],
        "dataset_role": source["dataset_role"],
        "meeting_acoustics_evidence": source["meeting_acoustics_evidence"],
        "baseline_only": source["baseline_only"],
        "counts_toward_public_meeting_wall_clock_candidate": source[
            "counts_toward_public_meeting_wall_clock_candidate"
        ],
        "recommended_next_gate": source["recommended_next_gate"],
        "not_for_gate_reason": source["not_for_gate_reason"],
    }


def build_public_sample_extraction_plan(
    *,
    source_id: str,
    target_root: str,
    max_duration_seconds: int,
    max_download_bytes: int,
    source_split: str | None = None,
    selection_criteria: str = "first manually reviewed Mandarin meeting clips that fit the byte and duration budget",
    sample_budget_count: int = 10,
    sample_budget_minutes: int = 20,
    max_clip_seconds: int = 300,
    planned_samples: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    source = APPROVED_SOURCES.get(source_id)
    if source is None:
        errors.append("source_id is not approved")
        source_url = None
        source_license = None
        resolved_source_split = source_split
    else:
        source_url = source["source_url"]
        source_license = source["source_license"]
        resolved_source_split = source_split or str(source["default_source_split"])

    if not _is_under_any_root(target_root, ALLOWED_TARGET_ROOTS):
        errors.append("target_root is not allowed")
    if _is_under_any_root(target_root, FORBIDDEN_TARGET_ROOTS):
        errors.append("target_root is forbidden")
    if not selection_criteria:
        errors.append("selection_criteria must be non-empty")
    if not resolved_source_split:
        errors.append("source_split must be non-empty")

    errors.extend(
        _validate_budget(
            max_duration_seconds=max_duration_seconds,
            max_download_bytes=max_download_bytes,
            max_clip_seconds=max_clip_seconds,
            sample_budget_count=sample_budget_count,
            sample_budget_minutes=sample_budget_minutes,
        )
    )

    resolved_planned_samples = planned_samples or []
    planned_sample_validation_errors: list[str] = []
    planned_total_duration_seconds = 0
    normalized_planned_samples: list[dict[str, object]] = []
    if resolved_planned_samples:
        (
            normalized_planned_samples,
            planned_total_duration_seconds,
            planned_sample_validation_errors,
        ) = _validate_planned_samples(
            planned_samples=resolved_planned_samples,
            source_id=source_id,
            source_url=source_url,
            source_license=source_license,
            max_clip_seconds=max_clip_seconds,
            sample_budget_count=sample_budget_count,
            sample_budget_minutes=sample_budget_minutes,
        )
        errors.extend(planned_sample_validation_errors)

    blocked = bool(errors)
    planned_samples_status = "not_planned"
    if resolved_planned_samples:
        planned_samples_status = "invalid" if planned_sample_validation_errors else "schema_validated_no_download"
    plan_status = "blocked" if blocked else "ready_for_manual_download_review"
    next_action = "fix_validation_errors" if blocked else "manual_source_artifact_review"
    if not blocked and not resolved_planned_samples:
        plan_status = "blocked_no_planned_samples"
        next_action = "create_concrete_public_audio_sample_manifest"

    source_metadata = _source_metadata_for_report(source)
    return {
        "plan_mode": "public_audio_sample_extraction_plan_only",
        "plan_id": PLAN_ID,
        "plan_version": PLAN_VERSION,
        "plan_status": plan_status,
        "review_status": "requires_manual_review",
        "source_snapshot_date": SOURCE_SNAPSHOT_DATE,
        "source_id": source_id,
        "source_url": source_url,
        "source_license": source_license,
        "source_split": resolved_source_split,
        **source_metadata,
        "selection_criteria": selection_criteria,
        "sample_budget_count": sample_budget_count,
        "sample_budget_minutes": sample_budget_minutes,
        "target_root": target_root,
        "allowed_roots": sorted(ALLOWED_TARGET_ROOTS),
        "forbidden_roots": sorted(FORBIDDEN_TARGET_ROOTS),
        "max_duration_seconds": max_duration_seconds,
        "max_clip_seconds": max_clip_seconds,
        "max_download_bytes": max_download_bytes,
        "max_total_bytes": max_download_bytes,
        "download_status": "not_started",
        "download_mode": "manual_review_only",
        "download_command": None,
        "extract_command": None,
        "transcode_command": None,
        "planned_sample_schema": {
            "required_fields_before_download": PLANNED_SAMPLE_REQUIRED_FIELDS,
            "checksum_note": (
                "expected_sha256_after_extract is a manual-review manifest field before execution; "
                "after any approved extraction, record observed clip sha256 before ASR use"
            ),
            "download_command": None,
            "extract_command": None,
            "transcode_command": None,
            "cleanup_required": True,
        },
        "planned_samples": normalized_planned_samples,
        "planned_sample_count": len(resolved_planned_samples),
        "planned_total_duration_seconds": planned_total_duration_seconds,
        "planned_samples_status": planned_samples_status,
        "derived_artifact_policy": "do_not_commit_raw_or_large_audio",
        "attribution_policy": "retain source id, URL, license and citation in reports",
        "checksum_algorithm": "sha256",
        "cleanup_policy": "delete artifacts under target_root after validation or keep ignored local only",
        "retention_policy": "reports only in repo; audio artifacts ignored",
        "abort_thresholds": {
            "download_bytes_exceeds_max": True,
            "target_root_not_allowed": True,
            "source_not_whitelisted": True,
        },
        "safe_to_download_now": False,
        "safe_to_extract_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_raw_audio": False,
        "remote_asr_call_count": 0,
        "llm_call_count": 0,
        "raw_audio_uploaded": False,
        "validation_errors": errors,
        "next_action": next_action,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--target-root", default="artifacts/tmp/public_audio")
    parser.add_argument("--max-duration-seconds", type=int, default=1200)
    parser.add_argument("--max-download-bytes", type=int, default=900_000_000)
    parser.add_argument("--source-split")
    parser.add_argument("--sample-budget-count", type=int, default=10)
    parser.add_argument("--sample-budget-minutes", type=int, default=20)
    parser.add_argument("--max-clip-seconds", type=int, default=300)
    parser.add_argument("--planned-samples-file")
    parser.add_argument("--output")
    return parser.parse_args(argv)


def _load_planned_samples_file(path: str | None) -> tuple[list[dict[str, object]] | None, list[str]]:
    if path is None:
        return None, []
    planned_samples_path, path_errors = resolve_input_file_path_after_validation(
        path,
        label="planned samples file",
    )
    if path_errors:
        return None, path_errors
    try:
        with planned_samples_path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except OSError:
        return None, ["planned samples file could not be read"]
    except json.JSONDecodeError:
        return None, ["planned samples file must contain valid JSON"]
    if not isinstance(payload, list):
        return None, ["planned samples file must contain a JSON array"]
    return payload, []


def _write_report(report: dict[str, object], output_path: str | None) -> list[str]:
    if output_path is None:
        return []
    resolved_output_path, output_errors = resolve_output_file_path_after_validation(output_path)
    if output_errors:
        return output_errors
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return []


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    planned_samples, planned_samples_file_errors = _load_planned_samples_file(args.planned_samples_file)
    report = build_public_sample_extraction_plan(
        source_id=args.source_id,
        target_root=args.target_root,
        max_duration_seconds=args.max_duration_seconds,
        max_download_bytes=args.max_download_bytes,
        source_split=args.source_split,
        sample_budget_count=args.sample_budget_count,
        sample_budget_minutes=args.sample_budget_minutes,
        max_clip_seconds=args.max_clip_seconds,
        planned_samples=planned_samples,
    )
    if planned_samples_file_errors:
        report["plan_status"] = "blocked"
        report["planned_samples_status"] = "invalid"
        report["validation_errors"] = [
            *report["validation_errors"],
            *planned_samples_file_errors,
        ]
        report["next_action"] = "fix_validation_errors"

    output_errors = _write_report(report, args.output)
    if output_errors:
        report["plan_status"] = "blocked"
        report["validation_errors"] = [
            *report["validation_errors"],
            *output_errors,
        ]
        report["next_action"] = "fix_validation_errors"

    if args.output is None or output_errors:
        json.dump(report, out, ensure_ascii=False, indent=2)
        print(file=out)

    return 1 if report["plan_status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
