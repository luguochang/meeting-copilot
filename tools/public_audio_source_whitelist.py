#!/usr/bin/env python3
"""Report the approved public-audio source whitelist without downloading audio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PATH = REPO_ROOT / "data" / "asr_eval" / "public_sources.json"
ALLOWED_SOURCE_READ_ROOTS = (
    "data/asr_eval",
    "artifacts/tmp/public_audio",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)

EXPECTED_MANIFEST_VERSION = "public_audio_source_whitelist.v1"
EXPECTED_SOURCE_IDS = {
    "aishell4_openslr_slr111",
    "alimeeting_openslr_slr119",
    "aishell1_openslr_slr33",
}
EXPECTED_ALLOWED_STORAGE_ROOTS = [
    "data/asr_eval/public_raw",
    "artifacts/tmp/public_audio",
]
EXPECTED_FORBIDDEN_STORAGE_ROOTS = [
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
]


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix_parts for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return None


def _is_under_allowed_metadata_root(relative_path: Path) -> bool:
    path_text = relative_path.as_posix()
    return any(path_text == root or path_text.startswith(f"{root}/") for root in ALLOWED_SOURCE_READ_ROOTS)


def validate_source_path_before_read(path: Path) -> list[str]:
    raw_path = Path(path)
    absolute_path = raw_path if raw_path.is_absolute() else REPO_ROOT / raw_path

    for _, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(raw_path, suffix_parts):
            return ["source_path is forbidden"]

    if raw_path.is_absolute() and _repo_relative_path(raw_path) is None:
        return ["source_path is outside repository"]

    resolved_path = absolute_path.resolve(strict=False)
    for _, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(resolved_path, suffix_parts):
            return ["source_path is forbidden"]

    resolved_relative = _repo_relative_path(resolved_path)
    if resolved_relative is None:
        return ["source_path resolves outside repository"]

    raw_relative = _repo_relative_path(absolute_path)
    if raw_relative is None:
        return ["source_path is outside repository"]
    if not _is_under_allowed_metadata_root(raw_relative) or not _is_under_allowed_metadata_root(resolved_relative):
        return ["source_path is not under approved public audio metadata roots"]
    return []


def validate_public_sources(payload: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if payload.get("manifest_version") != EXPECTED_MANIFEST_VERSION:
        errors.append("manifest_version must be public_audio_source_whitelist.v1")
    if payload.get("default_download_status") != "not_started":
        errors.append("default_download_status must be not_started")
    if payload.get("safe_to_download_now") is not False:
        errors.append("safe_to_download_now must be false")
    if payload.get("safe_to_read_user_audio") is not False:
        errors.append("safe_to_read_user_audio must be false")
    if payload.get("safe_to_read_configs_local") is not False:
        errors.append("safe_to_read_configs_local must be false")
    if payload.get("allowed_storage_roots") != EXPECTED_ALLOWED_STORAGE_ROOTS:
        errors.append("allowed_storage_roots must match the public audio storage policy")
    if payload.get("forbidden_storage_roots") != EXPECTED_FORBIDDEN_STORAGE_ROOTS:
        errors.append("forbidden_storage_roots must match the local/private audio policy")

    sources = payload.get("sources")
    if not isinstance(sources, list):
        return [*errors, "sources must be a list"]

    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            errors.append("source entries must be objects")
            continue
        source_id = source.get("source_id")
        if not isinstance(source_id, str):
            errors.append("source.source_id must be a string")
            continue
        source_ids.add(source_id)
        if source.get("default_download_enabled") is not False:
            errors.append(f"source {source_id} default_download_enabled must be false")
        if source.get("raw_audio_committed_to_repo") is not False:
            errors.append(f"source {source_id} raw_audio_committed_to_repo must be false")
        if source.get("product_value_validation_allowed") is not False:
            errors.append(f"source {source_id} product_value_validation_allowed must be false")
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://www.openslr.org/"):
            errors.append(f"source {source_id} url must be an OpenSLR HTTPS URL")
        if not isinstance(source.get("license"), str) or not source.get("license"):
            errors.append(f"source {source_id} license must be explicit")
        if not isinstance(source.get("use_boundary"), str) or not source.get("use_boundary"):
            errors.append(f"source {source_id} use_boundary must be explicit")
        if not isinstance(source.get("not_for"), str) or not source.get("not_for"):
            errors.append(f"source {source_id} not_for must be explicit")

    if source_ids != EXPECTED_SOURCE_IDS:
        errors.append("sources must contain exactly the approved OpenSLR source ids")
    observed_sources = payload.get("observed_but_not_whitelisted_sources", [])
    if observed_sources and not isinstance(observed_sources, list):
        errors.append("observed_but_not_whitelisted_sources must be a list")
    if isinstance(observed_sources, list):
        for candidate in observed_sources:
            if not isinstance(candidate, dict):
                errors.append("observed_but_not_whitelisted_sources entries must be objects")
                continue
            candidate_id = candidate.get("source_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                errors.append("observed_but_not_whitelisted_sources.source_id must be a string")
                continue
            if candidate_id in EXPECTED_SOURCE_IDS:
                errors.append(f"observed source {candidate_id} must not duplicate a whitelisted source")
            if candidate.get("default_download_enabled") is not False:
                errors.append(f"observed source {candidate_id} default_download_enabled must be false")
            if candidate.get("raw_audio_committed_to_repo") is not False:
                errors.append(f"observed source {candidate_id} raw_audio_committed_to_repo must be false")
            if candidate.get("product_value_validation_allowed") is not False:
                errors.append(f"observed source {candidate_id} product_value_validation_allowed must be false")
            if not isinstance(candidate.get("reason_not_whitelisted"), str) or not candidate.get(
                "reason_not_whitelisted"
            ):
                errors.append(f"observed source {candidate_id} reason_not_whitelisted must be explicit")
    return errors


def _blocked_report(errors: list[str]) -> dict[str, object]:
    return {
        "report_mode": "public_audio_source_whitelist_only",
        "manifest_version": None,
        "download_status": "not_started",
        "source_validation_status": "failed",
        "source_validation_errors": errors,
        "source_count": 0,
        "sources": [],
        "observed_but_not_whitelisted_count": 0,
        "observed_but_not_whitelisted_sources": [],
        "allowed_storage_roots": EXPECTED_ALLOWED_STORAGE_ROOTS,
        "forbidden_storage_roots": EXPECTED_FORBIDDEN_STORAGE_ROOTS,
        "safe_to_download_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_raw_audio": False,
        "next_action": "fix_validation_errors",
    }


def build_public_audio_source_whitelist_report(source_path: Path = DEFAULT_SOURCE_PATH) -> dict[str, object]:
    path_errors = validate_source_path_before_read(source_path)
    if path_errors:
        return _blocked_report(path_errors)

    payload = _load_json(source_path)
    errors = validate_public_sources(payload)
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    observed_sources = (
        payload.get("observed_but_not_whitelisted_sources")
        if isinstance(payload.get("observed_but_not_whitelisted_sources"), list)
        else []
    )
    source_summaries = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_summaries.append(
            {
                "source_id": source.get("source_id"),
                "name": source.get("name"),
                "url": source.get("url"),
                "license": source.get("license"),
                "dataset_kind": source.get("dataset_kind"),
                "recommended_first_subset": source.get("recommended_first_subset"),
                "use_boundary": source.get("use_boundary"),
                "not_for": source.get("not_for"),
                "download_status": "not_started",
                "default_download_enabled": False,
            }
        )
    observed_summaries = []
    for candidate in observed_sources:
        if not isinstance(candidate, dict):
            continue
        observed_summaries.append(
            {
                "source_id": candidate.get("source_id"),
                "name": candidate.get("name"),
                "url": candidate.get("url"),
                "license": candidate.get("license"),
                "dataset_kind": candidate.get("dataset_kind"),
                "reason_not_whitelisted": candidate.get("reason_not_whitelisted"),
                "download_status": "not_started",
                "default_download_enabled": False,
            }
        )

    return {
        "report_mode": "public_audio_source_whitelist_only",
        "manifest_version": payload.get("manifest_version"),
        "download_status": "not_started",
        "source_validation_status": "failed" if errors else "passed",
        "source_validation_errors": errors,
        "source_count": len(source_summaries),
        "sources": source_summaries,
        "observed_but_not_whitelisted_count": len(observed_summaries),
        "observed_but_not_whitelisted_sources": observed_summaries,
        "allowed_storage_roots": payload.get("allowed_storage_roots", []),
        "forbidden_storage_roots": payload.get("forbidden_storage_roots", []),
        "safe_to_download_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_raw_audio": False,
        "next_action": "create_bounded_sample_extraction_plan",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-path", type=Path, default=DEFAULT_SOURCE_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_public_audio_source_whitelist_report(args.source_path)
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["source_validation_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
