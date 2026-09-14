from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.pi_stage0_asr_entity_gate import (
    AsrEntityFixtureError,
    REQUIRED_CATEGORIES,
    evaluate_fixture,
    load_fixture,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPO_ROOT
    / "tools"
    / "realtime_coach_eval"
    / "fixtures"
    / "asr_entity_safety_physical_v1.json"
)
TOOL_PATH = REPO_ROOT / "tools" / "pi_stage0_asr_entity_gate.py"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _real_fixture() -> dict[str, object]:
    return copy.deepcopy(dict(load_fixture(FIXTURE_PATH)))


def _fully_human_fixture() -> dict[str, object]:
    text = "错误率百分之三不暂停张三负责明天下午六点MeetingCopilot"
    surfaces = {
        "number": "百分之三",
        "negation": "不暂停",
        "owner": "张三",
        "deadline": "明天下午六点",
        "product_name": "MeetingCopilot",
    }
    categories = {
        category: {
            "status": "scored_reference",
            "entities": [
                {
                    "entity_id": f"{category}_1",
                    "reference_surface": surface,
                    "accepted_hypothesis_surfaces": [surface],
                }
            ],
            "hypothesis_entities": [
                {
                    "hypothesis_entity_id": f"hypothesis_{category}_1",
                    "surface": surface,
                    "reference_entity_id": f"{category}_1",
                }
            ],
        }
        for category, surface in surfaces.items()
    }
    return {
        "schema_version": "meeting_copilot.pi_stage0_asr_entity_fixture.v1",
        "fixture_id": "fully_human_test_fixture",
        "required_categories": list(REQUIRED_CATEGORIES),
        "cases": [
            {
                "case_id": "human_case_1",
                "audio_source": "browser_live_mic_external_playback",
                "evidence_files": [
                    {
                        "role": "test_only",
                        "path": "tests/test_pi_stage0_asr_entity_gate.py",
                        "sha256": "0" * 64,
                    }
                ],
                "hypothesis": {
                    "segments": [text],
                    "text_sha256": _sha256_text(text),
                },
                "reference": {
                    "status": "human_transcript",
                    "human_verified": True,
                    "text": text,
                    "text_sha256": _sha256_text(text),
                },
                "categories": categories,
            }
        ],
    }


def test_real_physical_fixture_fails_closed() -> None:
    report = evaluate_fixture(
        _real_fixture(), verify_source_files=True, repo_root=REPO_ROOT
    )

    assert report["status"] == "no_go"
    assert report["passed"] is False
    assert report["metric"] == "entity_precision_and_recall"
    assert report["limitations"] == [
        "precision_requires_explicit_hypothesis_entity_annotations",
        "normalized_substring_matching_without_occurrence_alignment",
        "human_reference_provenance_is_fixture_declared",
    ]
    assert report["controlled_engineering_baseline"]["status"] == "partial"
    assert report["human_reference_gate"]["status"] == "incomplete"
    assert report["summary"]["reference_status_counts"] == {
        "controlled_stimulus_script": 1,
        "missing_human_reference": 3,
    }


def test_controlled_capture_reports_cer_and_only_supported_entity_recall() -> None:
    report = evaluate_fixture(_real_fixture())
    controlled_case = report["cases"][0]

    assert controlled_case["character_error_rate"]["edit_distance"] == 10
    assert controlled_case["character_error_rate"]["reference_characters"] == 425
    assert controlled_case["character_error_rate"]["cer"] == pytest.approx(
        0.023529411764705882
    )
    assert report["categories"]["negation"][
        "controlled_reference_entity_recall"
    ] == 1.0
    assert report["categories"]["negation"][
        "controlled_reference_entity_count"
    ] == 6
    for category in ("number", "owner", "deadline", "product_name"):
        assert report["categories"][category][
            "controlled_reference_entity_recall"
        ] is None
        assert (
            f"missing_controlled_reference_entity:{category}"
            in report["controlled_engineering_baseline"]["blockers"]
        )


def test_cases_without_independent_reference_are_never_entity_scored() -> None:
    report = evaluate_fixture(_real_fixture())

    for case in report["cases"][1:]:
        assert case["reference_status"] == "missing_human_reference"
        assert case["character_error_rate"] is None
        for category in REQUIRED_CATEGORIES:
            result = case["categories"][category]
            assert result["status"] == "unscored_missing_human_reference"
            assert result["reference_entity_recall"] is None
            assert result["entities"] == []


def test_complete_human_reference_fixture_can_pass() -> None:
    report = evaluate_fixture(_fully_human_fixture())

    assert report["status"] == "go"
    assert report["passed"] is True
    assert report["human_reference_gate"] == {
        "status": "passed",
        "passed": True,
        "blockers": [],
    }
    for category in REQUIRED_CATEGORIES:
        assert report["categories"][category]["human_reference_entity_recall"] == 1.0
        assert report["categories"][category]["human_entity_precision"] == 1.0


def test_human_false_positive_entity_fails_precision_gate() -> None:
    fixture = _fully_human_fixture()
    hypothesis = fixture["cases"][0]["hypothesis"]
    hypothesis["segments"] = [f"{hypothesis['segments'][0]}李四"]
    hypothesis["text_sha256"] = _sha256_text(hypothesis["segments"][0])
    fixture["cases"][0]["categories"]["owner"]["hypothesis_entities"].append(
        {
            "hypothesis_entity_id": "hypothesis_owner_false_positive",
            "surface": "李四",
            "reference_entity_id": None,
        }
    )

    report = evaluate_fixture(fixture)

    assert report["status"] == "no_go"
    assert report["categories"]["owner"]["human_entity_precision"] == 0.5
    assert report["categories"]["owner"]["human_false_positive_count"] == 1
    assert "human_false_positive_entity:owner" in report["human_reference_gate"][
        "blockers"
    ]


def test_human_reference_without_hypothesis_annotations_fails_closed() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["categories"]["owner"].pop("hypothesis_entities")

    report = evaluate_fixture(fixture)

    assert report["status"] == "no_go"
    assert report["categories"]["owner"]["human_entity_precision"] is None
    assert "missing_human_hypothesis_annotations:owner" in report[
        "human_reference_gate"
    ]["blockers"]


def test_hypothesis_match_must_reference_declared_entity() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["categories"]["owner"]["hypothesis_entities"][0][
        "reference_entity_id"
    ] = "unknown_owner"

    with pytest.raises(AsrEntityFixtureError, match="not declared in this category"):
        evaluate_fixture(fixture)


def test_missing_category_is_rejected() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["categories"].pop("product_name")

    with pytest.raises(AsrEntityFixtureError, match="exactly the required categories"):
        evaluate_fixture(fixture)


def test_claimed_human_reference_must_be_verified() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["reference"]["human_verified"] = False

    with pytest.raises(AsrEntityFixtureError, match="must be human_verified"):
        evaluate_fixture(fixture)


def test_reference_entity_surface_must_exist_in_reference() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["categories"]["owner"]["entities"][0][
        "reference_surface"
    ] = "李四"

    with pytest.raises(AsrEntityFixtureError, match="absent from independent reference"):
        evaluate_fixture(fixture)


def test_punctuation_only_accepted_surface_cannot_create_false_match() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["categories"]["owner"]["entities"][0][
        "accepted_hypothesis_surfaces"
    ] = ["!!!"]

    with pytest.raises(AsrEntityFixtureError, match="must contain letters or numbers"):
        evaluate_fixture(fixture)


def test_uppercase_hash_is_rejected() -> None:
    fixture = _fully_human_fixture()
    fixture["cases"][0]["hypothesis"]["text_sha256"] = fixture["cases"][0][
        "hypothesis"
    ]["text_sha256"].upper()

    with pytest.raises(AsrEntityFixtureError, match="lowercase SHA-256"):
        evaluate_fixture(fixture)


def test_evidence_symlink_cannot_escape_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (repo_root / "evidence.txt").symlink_to(outside)
    fixture = _fully_human_fixture()
    fixture["cases"][0]["evidence_files"] = [
        {
            "role": "test_only",
            "path": "evidence.txt",
            "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
        }
    ]

    with pytest.raises(AsrEntityFixtureError, match="resolves outside repo_root"):
        evaluate_fixture(fixture, verify_source_files=True, repo_root=repo_root)


def test_cli_exit_codes_and_report_redaction(tmp_path: Path) -> None:
    output_path = tmp_path / "report.json"
    no_go = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            str(FIXTURE_PATH),
            "--verify-source-files",
            "--repo-root",
            str(REPO_ROOT),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert no_go.returncode == 1
    report_text = output_path.read_text(encoding="utf-8")
    fixture = _real_fixture()
    full_reference = fixture["cases"][0]["reference"]["text"]
    full_hypothesis = "".join(fixture["cases"][0]["hypothesis"]["segments"])
    assert full_reference not in report_text
    assert full_hypothesis not in report_text
    assert json.loads(report_text)["status"] == "no_go"

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text('{"schema_version":"wrong"}', encoding="utf-8")
    invalid = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            str(invalid_path),
            "--unsafe-skip-source-file-verification",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["status"] == "blocked"


def test_cli_requires_repo_root_for_default_evidence_verification() -> None:
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), str(FIXTURE_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--repo-root is required" in result.stderr
