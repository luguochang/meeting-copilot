#!/usr/bin/env python3
"""Score physical-microphone ASR entities without inventing reference labels.

The evaluator is deliberately offline.  It accepts only explicit entity
annotations from a fixture, separates controlled stimulus scripts from human
transcripts, and fails the Stage 0 gate when any required category lacks an
independent human reference.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "meeting_copilot.pi_stage0_asr_entity_gate.v1"
FIXTURE_SCHEMA_VERSION = "meeting_copilot.pi_stage0_asr_entity_fixture.v1"
REQUIRED_CATEGORIES = ("number", "negation", "owner", "deadline", "product_name")
REFERENCE_STATUSES = frozenset(
    {"human_transcript", "controlled_stimulus_script", "missing_human_reference"}
)
CATEGORY_STATUSES = frozenset(
    {"scored_reference", "not_present_in_reference", "unscored_missing_human_reference"}
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
LIMITATIONS = (
    "precision_requires_explicit_hypothesis_entity_annotations",
    "normalized_substring_matching_without_occurrence_alignment",
    "human_reference_provenance_is_fixture_declared",
)


class AsrEntityFixtureError(ValueError):
    """Raised when an ASR entity fixture is incomplete or self-inconsistent."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AsrEntityFixtureError(f"{field} must be an object")
    return value


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AsrEntityFixtureError(f"{field} must be a non-empty string")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _declared_sha256(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if not SHA256_PATTERN.fullmatch(text):
        raise AsrEntityFixtureError(f"{field} must be a lowercase SHA-256")
    return text


def normalize_for_comparison(value: str) -> str:
    """Keep letters/numbers/CJK while ignoring punctuation and segmentation."""

    return "".join(character.casefold() for character in value if character.isalnum())


def _edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_character in enumerate(reference, 1):
        current = [reference_index]
        for hypothesis_index, hypothesis_character in enumerate(hypothesis, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1]
                    + (reference_character != hypothesis_character),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> dict[str, Any]:
    normalized_reference = normalize_for_comparison(reference)
    normalized_hypothesis = normalize_for_comparison(hypothesis)
    if not normalized_reference:
        raise AsrEntityFixtureError("reference text is empty after normalization")
    edits = _edit_distance(normalized_reference, normalized_hypothesis)
    return {
        "reference_characters": len(normalized_reference),
        "hypothesis_characters": len(normalized_hypothesis),
        "edit_distance": edits,
        "cer": edits / len(normalized_reference),
    }


def _validate_evidence_files(
    case: Mapping[str, Any],
    *,
    case_field: str,
    verify_source_files: bool,
    repo_root: Path | None,
) -> list[dict[str, str]]:
    raw_files = case.get("evidence_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise AsrEntityFixtureError(f"{case_field}.evidence_files must be a non-empty array")
    files: list[dict[str, str]] = []
    for index, raw_file in enumerate(raw_files):
        field = f"{case_field}.evidence_files[{index}]"
        evidence = _mapping(raw_file, field)
        role = _non_empty_text(evidence.get("role"), f"{field}.role")
        relative_path = _non_empty_text(evidence.get("path"), f"{field}.path")
        declared_hash = _declared_sha256(evidence.get("sha256"), f"{field}.sha256")
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise AsrEntityFixtureError(f"{field}.path must be repository-relative")
        if verify_source_files:
            if repo_root is None:
                raise AsrEntityFixtureError("repo_root is required to verify source files")
            resolved_root = repo_root.resolve()
            source_path = (resolved_root / relative_path).resolve()
            if not source_path.is_relative_to(resolved_root):
                raise AsrEntityFixtureError(
                    f"evidence file resolves outside repo_root: {relative_path}"
                )
            if not source_path.is_file():
                raise AsrEntityFixtureError(f"missing evidence file: {relative_path}")
            actual_hash = _sha256_file(source_path)
            if actual_hash != declared_hash:
                raise AsrEntityFixtureError(f"evidence SHA-256 mismatch: {relative_path}")
        files.append({"role": role, "path": relative_path, "sha256": declared_hash})
    return files


def _score_case(
    raw_case: Any,
    *,
    index: int,
    verify_source_files: bool,
    repo_root: Path | None,
) -> dict[str, Any]:
    case_field = f"cases[{index}]"
    case = _mapping(raw_case, case_field)
    case_id = _non_empty_text(case.get("case_id"), f"{case_field}.case_id")
    audio_source = _non_empty_text(case.get("audio_source"), f"{case_field}.audio_source")
    if audio_source != "browser_live_mic_external_playback":
        raise AsrEntityFixtureError(
            f"{case_field}.audio_source must be browser_live_mic_external_playback"
        )
    evidence_files = _validate_evidence_files(
        case,
        case_field=case_field,
        verify_source_files=verify_source_files,
        repo_root=repo_root,
    )

    hypothesis = _mapping(case.get("hypothesis"), f"{case_field}.hypothesis")
    segments = hypothesis.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AsrEntityFixtureError(f"{case_field}.hypothesis.segments must be non-empty")
    hypothesis_segments = [
        _non_empty_text(segment, f"{case_field}.hypothesis.segments[{segment_index}]")
        for segment_index, segment in enumerate(segments)
    ]
    hypothesis_text = "".join(hypothesis_segments)
    hypothesis_hash = _declared_sha256(
        hypothesis.get("text_sha256"), f"{case_field}.hypothesis.text_sha256"
    )
    if _sha256_text(hypothesis_text) != hypothesis_hash:
        raise AsrEntityFixtureError(f"{case_field}.hypothesis.text_sha256 does not match segments")

    reference = _mapping(case.get("reference"), f"{case_field}.reference")
    reference_status = _non_empty_text(
        reference.get("status"), f"{case_field}.reference.status"
    )
    if reference_status not in REFERENCE_STATUSES:
        raise AsrEntityFixtureError(f"{case_field}.reference.status is unsupported")
    human_verified = reference.get("human_verified")
    if not isinstance(human_verified, bool):
        raise AsrEntityFixtureError(f"{case_field}.reference.human_verified must be boolean")
    if reference_status == "human_transcript" and not human_verified:
        raise AsrEntityFixtureError(f"{case_field} human_transcript must be human_verified")
    if reference_status != "human_transcript" and human_verified:
        raise AsrEntityFixtureError(
            f"{case_field} cannot mark {reference_status} as human_verified"
        )

    reference_text_value = reference.get("text")
    reference_text: str | None
    if reference_status == "missing_human_reference":
        if reference_text_value not in (None, ""):
            raise AsrEntityFixtureError(
                f"{case_field} missing_human_reference cannot include reference text"
            )
        _non_empty_text(reference.get("reason"), f"{case_field}.reference.reason")
        reference_text = None
    else:
        reference_text = _non_empty_text(reference_text_value, f"{case_field}.reference.text")
        reference_hash = _declared_sha256(
            reference.get("text_sha256"), f"{case_field}.reference.text_sha256"
        )
        if _sha256_text(reference_text) != reference_hash:
            raise AsrEntityFixtureError(f"{case_field}.reference.text_sha256 does not match text")

    raw_categories = _mapping(case.get("categories"), f"{case_field}.categories")
    if set(raw_categories) != set(REQUIRED_CATEGORIES):
        missing = sorted(set(REQUIRED_CATEGORIES) - set(raw_categories))
        extra = sorted(set(raw_categories) - set(REQUIRED_CATEGORIES))
        raise AsrEntityFixtureError(
            f"{case_field}.categories must contain exactly the required categories; "
            f"missing={missing}, extra={extra}"
        )

    normalized_reference = normalize_for_comparison(reference_text or "")
    normalized_hypothesis = normalize_for_comparison(hypothesis_text)
    category_results: dict[str, dict[str, Any]] = {}
    seen_entity_ids: set[str] = set()
    for category in REQUIRED_CATEGORIES:
        field = f"{case_field}.categories.{category}"
        category_value = _mapping(raw_categories[category], field)
        category_status = _non_empty_text(category_value.get("status"), f"{field}.status")
        if category_status not in CATEGORY_STATUSES:
            raise AsrEntityFixtureError(f"{field}.status is unsupported")
        raw_entities = category_value.get("entities")
        if not isinstance(raw_entities, list):
            raise AsrEntityFixtureError(f"{field}.entities must be an array")
        if reference_text is None and category_status != "unscored_missing_human_reference":
            raise AsrEntityFixtureError(f"{field} cannot be scored without independent reference text")
        if reference_text is not None and category_status == "unscored_missing_human_reference":
            raise AsrEntityFixtureError(f"{field} has reference text and cannot claim it is missing")
        if category_status != "scored_reference" and raw_entities:
            raise AsrEntityFixtureError(f"{field}.entities must be empty when category is unscored")
        if category_status == "scored_reference" and not raw_entities:
            raise AsrEntityFixtureError(f"{field}.entities must be non-empty when scored")

        entity_results: list[dict[str, Any]] = []
        reference_entities: dict[str, dict[str, Any]] = {}
        for entity_index, raw_entity in enumerate(raw_entities):
            entity_field = f"{field}.entities[{entity_index}]"
            entity = _mapping(raw_entity, entity_field)
            entity_id = _non_empty_text(entity.get("entity_id"), f"{entity_field}.entity_id")
            if entity_id in seen_entity_ids:
                raise AsrEntityFixtureError(f"duplicate entity_id in {case_id}: {entity_id}")
            seen_entity_ids.add(entity_id)
            reference_surface = _non_empty_text(
                entity.get("reference_surface"), f"{entity_field}.reference_surface"
            )
            normalized_surface = normalize_for_comparison(reference_surface)
            if not normalized_surface or normalized_surface not in normalized_reference:
                raise AsrEntityFixtureError(
                    f"{entity_field}.reference_surface is absent from independent reference"
                )
            accepted = entity.get("accepted_hypothesis_surfaces")
            if not isinstance(accepted, list) or not accepted:
                raise AsrEntityFixtureError(
                    f"{entity_field}.accepted_hypothesis_surfaces must be non-empty"
                )
            accepted_surfaces = [
                _non_empty_text(surface, f"{entity_field}.accepted_hypothesis_surfaces")
                for surface in accepted
            ]
            if any(not normalize_for_comparison(surface) for surface in accepted_surfaces):
                raise AsrEntityFixtureError(
                    f"{entity_field}.accepted_hypothesis_surfaces must contain "
                    "letters or numbers"
                )
            reference_entities[entity_id] = {
                "accepted_surfaces": accepted_surfaces,
            }
            entity_results.append(
                {
                    "entity_id": entity_id,
                    "matched": False,
                    "matched_surface": None,
                }
            )

        raw_hypothesis_entities = category_value.get("hypothesis_entities")
        hypothesis_annotation_complete = isinstance(raw_hypothesis_entities, list)
        if raw_hypothesis_entities is not None and not isinstance(raw_hypothesis_entities, list):
            raise AsrEntityFixtureError(f"{field}.hypothesis_entities must be an array")
        if reference_text is None and raw_hypothesis_entities:
            raise AsrEntityFixtureError(
                f"{field}.hypothesis_entities cannot be scored without independent reference text"
            )

        hypothesis_entity_results: list[dict[str, Any]] = []
        seen_hypothesis_entity_ids: set[str] = set()
        matched_reference_entity_ids: set[str] = set()
        for hypothesis_index, raw_hypothesis_entity in enumerate(raw_hypothesis_entities or []):
            hypothesis_field = f"{field}.hypothesis_entities[{hypothesis_index}]"
            hypothesis_entity = _mapping(raw_hypothesis_entity, hypothesis_field)
            hypothesis_entity_id = _non_empty_text(
                hypothesis_entity.get("hypothesis_entity_id"),
                f"{hypothesis_field}.hypothesis_entity_id",
            )
            if hypothesis_entity_id in seen_hypothesis_entity_ids:
                raise AsrEntityFixtureError(
                    f"duplicate hypothesis_entity_id in {case_id}: {hypothesis_entity_id}"
                )
            seen_hypothesis_entity_ids.add(hypothesis_entity_id)
            surface = _non_empty_text(
                hypothesis_entity.get("surface"), f"{hypothesis_field}.surface"
            )
            normalized_hypothesis_surface = normalize_for_comparison(surface)
            if (
                not normalized_hypothesis_surface
                or normalized_hypothesis_surface not in normalized_hypothesis
            ):
                raise AsrEntityFixtureError(
                    f"{hypothesis_field}.surface is absent from ASR hypothesis"
                )
            raw_reference_entity_id = hypothesis_entity.get("reference_entity_id")
            reference_entity_id = (
                _non_empty_text(
                    raw_reference_entity_id,
                    f"{hypothesis_field}.reference_entity_id",
                )
                if raw_reference_entity_id is not None
                else None
            )
            if reference_entity_id is not None:
                reference_entity = reference_entities.get(reference_entity_id)
                if reference_entity is None:
                    raise AsrEntityFixtureError(
                        f"{hypothesis_field}.reference_entity_id is not declared in this category"
                    )
                if reference_entity_id in matched_reference_entity_ids:
                    raise AsrEntityFixtureError(
                        f"duplicate hypothesis match for reference entity {reference_entity_id}"
                    )
                accepted_surfaces = reference_entity["accepted_surfaces"]
                if normalized_hypothesis_surface not in {
                    normalize_for_comparison(candidate) for candidate in accepted_surfaces
                }:
                    raise AsrEntityFixtureError(
                        f"{hypothesis_field}.surface is not an accepted surface for "
                        f"{reference_entity_id}"
                    )
                matched_reference_entity_ids.add(reference_entity_id)
            hypothesis_entity_results.append(
                {
                    "hypothesis_entity_id": hypothesis_entity_id,
                    "surface_sha256": _sha256_text(surface),
                    "reference_entity_id": reference_entity_id,
                    "matched": reference_entity_id is not None,
                }
            )

        if hypothesis_annotation_complete:
            for entity_result in entity_results:
                entity_id = entity_result["entity_id"]
                matched_hypothesis = next(
                    (
                        hypothesis_entity
                        for hypothesis_entity in hypothesis_entity_results
                        if hypothesis_entity["reference_entity_id"] == entity_id
                    ),
                    None,
                )
                entity_result["matched"] = matched_hypothesis is not None
                entity_result["matched_surface_sha256"] = (
                    matched_hypothesis["surface_sha256"]
                    if matched_hypothesis is not None
                    else None
                )
        else:
            # Preserve the legacy recall-only baseline for existing physical
            # fixtures. It is never sufficient for the precision gate.
            for entity_result in entity_results:
                accepted_surfaces = reference_entities[entity_result["entity_id"]][
                    "accepted_surfaces"
                ]
                matched_surface = next(
                    (
                        surface
                        for surface in accepted_surfaces
                        if normalize_for_comparison(surface) in normalized_hypothesis
                    ),
                    None,
                )
                entity_result["matched"] = matched_surface is not None
                entity_result["matched_surface_sha256"] = (
                    _sha256_text(matched_surface) if matched_surface is not None else None
                )

        matched_count = sum(bool(entity["matched"]) for entity in entity_results)
        true_positive_count = sum(
            bool(entity["matched"]) for entity in hypothesis_entity_results
        )
        false_positive_count = len(hypothesis_entity_results) - true_positive_count
        category_results[category] = {
            "status": category_status,
            "hypothesis_annotation_complete": hypothesis_annotation_complete,
            "reference_entity_count": len(entity_results),
            "matched_entity_count": matched_count,
            "reference_entity_recall": (
                matched_count / len(entity_results) if entity_results else None
            ),
            "hypothesis_entity_count": len(hypothesis_entity_results),
            "true_positive_count": true_positive_count,
            "false_positive_count": false_positive_count,
            "entity_precision": (
                true_positive_count / len(hypothesis_entity_results)
                if hypothesis_entity_results
                else 1.0
                if hypothesis_annotation_complete
                else None
            ),
            "missed_entity_ids": [
                entity["entity_id"] for entity in entity_results if not entity["matched"]
            ],
            "false_positive_hypothesis_entity_ids": [
                entity["hypothesis_entity_id"]
                for entity in hypothesis_entity_results
                if not entity["matched"]
            ],
            "entities": entity_results,
            "hypothesis_entities": hypothesis_entity_results,
        }

    return {
        "case_id": case_id,
        "audio_source": audio_source,
        "reference_status": reference_status,
        "human_verified": human_verified,
        "evidence_files": evidence_files,
        "hypothesis": {
            "segment_count": len(hypothesis_segments),
            "text_sha256": hypothesis_hash,
        },
        "character_error_rate": (
            character_error_rate(reference_text, hypothesis_text)
            if reference_text is not None
            else None
        ),
        "categories": category_results,
    }


def _empty_category_summary() -> dict[str, Any]:
    return {
        "controlled_reference_entity_count": 0,
        "controlled_matched_entity_count": 0,
        "controlled_reference_entity_recall": None,
        "controlled_hypothesis_entity_count": 0,
        "controlled_true_positive_count": 0,
        "controlled_false_positive_count": 0,
        "controlled_entity_precision": None,
        "controlled_annotated_case_count": 0,
        "controlled_missing_annotation_case_ids": [],
        "human_reference_entity_count": 0,
        "human_matched_entity_count": 0,
        "human_reference_entity_recall": None,
        "human_hypothesis_entity_count": 0,
        "human_true_positive_count": 0,
        "human_false_positive_count": 0,
        "human_entity_precision": None,
        "human_annotated_case_count": 0,
        "human_missing_annotation_case_ids": [],
        "not_present_in_reference_case_count": 0,
        "unscored_missing_human_reference_case_count": 0,
        "controlled_missed_entity_ids": [],
        "human_missed_entity_ids": [],
    }


def evaluate_fixture(
    fixture: Mapping[str, Any],
    *,
    verify_source_files: bool = False,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    if fixture.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise AsrEntityFixtureError("unsupported fixture schema_version")
    fixture_id = _non_empty_text(fixture.get("fixture_id"), "fixture_id")
    declared_categories = fixture.get("required_categories")
    if declared_categories != list(REQUIRED_CATEGORIES):
        raise AsrEntityFixtureError("required_categories must declare the five Stage 0 categories")
    raw_cases = fixture.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise AsrEntityFixtureError("cases must be a non-empty array")
    cases = [
        _score_case(
            raw_case,
            index=index,
            verify_source_files=verify_source_files,
            repo_root=repo_root,
        )
        for index, raw_case in enumerate(raw_cases)
    ]
    case_ids = [case["case_id"] for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise AsrEntityFixtureError("case_id values must be unique")

    categories = {category: _empty_category_summary() for category in REQUIRED_CATEGORIES}
    reference_status_counts = Counter(case["reference_status"] for case in cases)
    for case in cases:
        reference_scope = (
            "human"
            if case["reference_status"] == "human_transcript"
            else "controlled"
            if case["reference_status"] == "controlled_stimulus_script"
            else None
        )
        for category, category_result in case["categories"].items():
            summary = categories[category]
            status = category_result["status"]
            if status == "not_present_in_reference":
                summary["not_present_in_reference_case_count"] += 1
            elif status == "unscored_missing_human_reference":
                summary["unscored_missing_human_reference_case_count"] += 1
            elif reference_scope is not None:
                prefix = f"{reference_scope}_"
                summary[f"{prefix}reference_entity_count"] += category_result[
                    "reference_entity_count"
                ]
                summary[f"{prefix}matched_entity_count"] += category_result[
                    "matched_entity_count"
                ]
                summary[f"{prefix}missed_entity_ids"].extend(
                    f"{case['case_id']}:{entity_id}"
                    for entity_id in category_result["missed_entity_ids"]
                )
                if category_result["hypothesis_annotation_complete"]:
                    summary[f"{prefix}annotated_case_count"] += 1
                    summary[f"{prefix}hypothesis_entity_count"] += category_result[
                        "hypothesis_entity_count"
                    ]
                    summary[f"{prefix}true_positive_count"] += category_result[
                        "true_positive_count"
                    ]
                    summary[f"{prefix}false_positive_count"] += category_result[
                        "false_positive_count"
                    ]
                else:
                    summary[f"{prefix}missing_annotation_case_ids"].append(
                        case["case_id"]
                    )

    for summary in categories.values():
        for scope in ("controlled", "human"):
            count = summary[f"{scope}_reference_entity_count"]
            matched = summary[f"{scope}_matched_entity_count"]
            summary[f"{scope}_reference_entity_recall"] = matched / count if count else None
            hypothesis_count = summary[f"{scope}_hypothesis_entity_count"]
            true_positive_count = summary[f"{scope}_true_positive_count"]
            annotated_case_count = summary[f"{scope}_annotated_case_count"]
            summary[f"{scope}_entity_precision"] = (
                true_positive_count / hypothesis_count
                if hypothesis_count
                else 1.0
                if annotated_case_count
                else None
            )

    controlled_blockers: list[str] = []
    human_blockers: list[str] = []
    for category, summary in categories.items():
        if summary["controlled_reference_entity_count"] == 0:
            controlled_blockers.append(f"missing_controlled_reference_entity:{category}")
        elif summary["controlled_missing_annotation_case_ids"]:
            controlled_blockers.append(
                f"missing_controlled_hypothesis_annotations:{category}"
            )
        elif summary["controlled_missed_entity_ids"]:
            controlled_blockers.append(f"controlled_entity_mismatch:{category}")
        elif summary["controlled_false_positive_count"]:
            controlled_blockers.append(f"controlled_false_positive_entity:{category}")
        if summary["human_reference_entity_count"] == 0:
            human_blockers.append(f"missing_human_reference:{category}")
        elif summary["human_missing_annotation_case_ids"]:
            human_blockers.append(f"missing_human_hypothesis_annotations:{category}")
        elif summary["human_missed_entity_ids"]:
            human_blockers.append(f"human_entity_mismatch:{category}")
        elif summary["human_false_positive_count"]:
            human_blockers.append(f"human_false_positive_entity:{category}")
    non_human_cases = [
        case["case_id"] for case in cases if case["reference_status"] != "human_transcript"
    ]
    human_blockers.extend(f"case_missing_human_reference:{case_id}" for case_id in non_human_cases)

    controlled_passed = not controlled_blockers
    human_passed = not human_blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "status": "go" if human_passed else "no_go",
        "passed": human_passed,
        "scope": "physical_microphone_asr_entity_safety",
        "metric": "entity_precision_and_recall",
        "limitations": list(LIMITATIONS),
        "controlled_engineering_baseline": {
            "status": "passed" if controlled_passed else "partial",
            "passed": controlled_passed,
            "blockers": controlled_blockers,
            "reference_is_human": False,
        },
        "human_reference_gate": {
            "status": "passed" if human_passed else "incomplete",
            "passed": human_passed,
            "blockers": human_blockers,
        },
        "summary": {
            "case_count": len(cases),
            "reference_status_counts": dict(sorted(reference_status_counts.items())),
            "required_categories": list(REQUIRED_CATEGORIES),
        },
        "categories": categories,
        "cases": cases,
    }


def load_fixture(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AsrEntityFixtureError(f"cannot read fixture: {exc}") from exc
    return _mapping(value, "fixture")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument(
        "--verify-source-files",
        action="store_true",
        default=True,
        help="Verify evidence paths and SHA-256 values (default).",
    )
    verification.add_argument(
        "--unsafe-skip-source-file-verification",
        action="store_false",
        dest="verify_source_files",
        help="Skip evidence-file verification for isolated fixture unit tests only.",
    )
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    if args.verify_source_files and args.repo_root is None:
        parser.error(
            "--repo-root is required unless "
            "--unsafe-skip-source-file-verification is set"
        )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = evaluate_fixture(
            load_fixture(args.fixture),
            verify_source_files=args.verify_source_files,
            repo_root=args.repo_root,
        )
    except AsrEntityFixtureError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed": report["passed"],
                "controlled_engineering_baseline": report[
                    "controlled_engineering_baseline"
                ]["status"],
                "human_reference_gate": report["human_reference_gate"]["status"],
                "blockers": report["human_reference_gate"]["blockers"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
