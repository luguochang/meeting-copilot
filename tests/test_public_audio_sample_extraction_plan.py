import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "public_audio_sample_extraction_plan.py"
EXAMPLE_PATH = REPO_ROOT / "data" / "asr_eval" / "public_sample_plan.example.json"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "public_audio_sample_extraction_plan",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_plan_defaults_to_no_download_and_whitelisted_source_only():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
    )

    assert report["plan_mode"] == "public_audio_sample_extraction_plan_only"
    assert report["plan_version"] == "public_audio_sample_extraction_plan.v1"
    assert report["plan_status"] == "blocked_no_planned_samples"
    assert report["review_status"] == "requires_manual_review"
    assert report["safe_to_download_now"] is False
    assert report["safe_to_extract_now"] is False
    assert report["download_status"] == "not_started"
    assert report["download_mode"] == "manual_review_only"
    assert report["source_id"] == "aishell4_openslr_slr111"
    assert report["source_url"] == "https://www.openslr.org/111/"
    assert report["source_license"] == "CC BY-SA 4.0"
    assert report["source_snapshot_date"] == "2026-07-02"
    assert report["source_split"] == "test"
    assert report["target_root"] == "artifacts/tmp/public_audio"
    assert report["max_duration_seconds"] == 180
    assert report["max_download_bytes"] == 600_000_000
    assert report["max_total_bytes"] == 600_000_000
    assert report["checksum_algorithm"] == "sha256"
    assert report["derived_artifact_policy"] == "do_not_commit_raw_or_large_audio"
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_commit_raw_audio"] is False
    assert report["next_action"] == "create_concrete_public_audio_sample_manifest"
    assert report["validation_errors"] == []
    assert report["planned_sample_schema"]["required_fields_before_download"] == [
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
    assert report["planned_samples"] == []
    assert report["planned_sample_count"] == 0
    assert report["planned_total_duration_seconds"] == 0
    assert report["planned_samples_status"] == "not_planned"


def test_sample_plan_rejects_unapproved_source_id():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="random_video_site",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
    )

    assert report["plan_status"] == "blocked"
    assert "source_id is not approved" in report["validation_errors"]
    assert report["safe_to_download_now"] is False
    assert report["safe_to_call_remote_asr"] is False


def test_sample_plan_rejects_forbidden_target_roots():
    tool = load_tool_module()

    for target_root in ["configs/local", "data/asr_eval/local_samples", "outputs"]:
        report = tool.build_public_sample_extraction_plan(
            source_id="aishell4_openslr_slr111",
            target_root=target_root,
            max_duration_seconds=180,
            max_download_bytes=600_000_000,
        )
        assert report["plan_status"] == "blocked"
        assert "target_root is not allowed" in report["validation_errors"]
        assert report["safe_to_download_now"] is False


def test_sample_plan_rejects_unbounded_budgets():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=0,
        max_download_bytes=0,
    )

    assert report["plan_status"] == "blocked"
    assert "max_duration_seconds must be between 1 and 1800" in report["validation_errors"]
    assert "max_download_bytes must be between 1 and 1000000000" in report["validation_errors"]


def test_sample_plan_validates_planned_samples_without_download_commands():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
        planned_samples=[
            {
                "sample_id": "aishell4-test-placeholder-001",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/<official-member-path-placeholder>.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "0" * 64,
                "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                "cleanup_required": True,
            }
        ],
    )

    assert report["plan_status"] == "ready_for_manual_download_review"
    assert report["planned_samples_status"] == "schema_validated_no_download"
    assert report["planned_sample_count"] == 1
    assert report["planned_total_duration_seconds"] == 60
    assert report["safe_to_download_now"] is False
    assert report["safe_to_extract_now"] is False
    assert report["download_command"] is None
    assert report["extract_command"] is None
    assert report["transcode_command"] is None
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json


def test_sample_plan_rejects_unsafe_or_incomplete_planned_samples():
    tool = load_tool_module()

    invalid_cases = [
        (
            {
                "sample_id": "absolute-member-path",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "/tmp/audio.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "1" * 64,
                "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                "cleanup_required": True,
            },
            "planned_samples[0].archive_member_path must be a safe relative archive member path",
        ),
        (
            {
                "sample_id": "parent-member-path",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/../audio.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "2" * 64,
                "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                "cleanup_required": True,
            },
            "planned_samples[0].archive_member_path must be a safe relative archive member path",
        ),
        (
            {
                "sample_id": "bad-clip-range",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/audio.wav",
                "clip_start_seconds": 60,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 0,
                "expected_sha256_after_extract": "3" * 64,
                "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                "cleanup_required": True,
            },
            "planned_samples[0].clip_end_seconds must be greater than clip_start_seconds",
        ),
        (
            {
                "sample_id": "bad-checksum",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/audio.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "not-a-sha",
                "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                "cleanup_required": True,
            },
            "planned_samples[0].expected_sha256_after_extract must be 64 lowercase hex characters",
        ),
        (
            {
                "sample_id": "cleanup-not-required",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/audio.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "4" * 64,
                "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                "cleanup_required": False,
            },
            "planned_samples[0].cleanup_required must be true",
        ),
        (
            {
                "sample_id": "missing-license",
                "source_id": "aishell4_openslr_slr111",
                "source_url": "https://www.openslr.org/111/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/audio.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "5" * 64,
                "license_citation": "",
                "cleanup_required": True,
            },
            "planned_samples[0].license_citation must be non-empty",
        ),
    ]

    for sample, expected_error in invalid_cases:
        report = tool.build_public_sample_extraction_plan(
            source_id="aishell4_openslr_slr111",
            target_root="artifacts/tmp/public_audio",
            max_duration_seconds=180,
            max_download_bytes=600_000_000,
            planned_samples=[sample],
        )
        assert report["plan_status"] == "blocked"
        assert report["planned_samples_status"] == "invalid"
        assert expected_error in report["validation_errors"]
        assert report["safe_to_download_now"] is False
        assert report["download_command"] is None


def test_sample_plan_rejects_planned_sample_attribution_not_bound_to_source():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
        planned_samples=[
            {
                "sample_id": "wrong-source-attribution",
                "source_id": "alimeeting_openslr_slr119",
                "source_url": "https://www.openslr.org/119/",
                "source_license": "CC BY-SA 4.0",
                "archive_name": "test.tar.gz",
                "archive_member_path": "test/audio.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 60,
                "expected_duration_seconds": 60,
                "expected_sha256_after_extract": "6" * 64,
                "license_citation": "AliMeeting / OpenSLR SLR119 / CC BY-SA 4.0",
                "cleanup_required": True,
            }
        ],
    )

    assert report["plan_status"] == "blocked"
    assert report["planned_samples_status"] == "invalid"
    assert "planned_samples[0].source_id must match selected source_id" in report["validation_errors"]
    assert "planned_samples[0].source_url must match selected source_url" in report["validation_errors"]
    assert report["safe_to_download_now"] is False


def test_sample_plan_rejects_all_forbidden_target_roots():
    tool = load_tool_module()

    for target_root in [
        "configs/local",
        "configs/local/public_audio",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]:
        report = tool.build_public_sample_extraction_plan(
            source_id="aishell4_openslr_slr111",
            target_root=target_root,
            max_duration_seconds=180,
            max_download_bytes=600_000_000,
        )
        assert report["plan_status"] == "blocked"
        assert "target_root is forbidden" in report["validation_errors"]
        assert report["safe_to_download_now"] is False


def test_sample_plan_rejects_forbidden_planned_samples_file_before_read(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("planned samples file was read before path guard")

    monkeypatch.setattr("builtins.open", fail_if_read)

    samples, errors = tool._load_planned_samples_file("data/local_runtime/public-samples.json")

    assert samples is None
    assert errors == ["planned samples file path is forbidden"]


def test_sample_plan_rejects_repo_outside_and_symlink_planned_samples_file_before_read(
    monkeypatch,
    tmp_path,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "samples.json"
    outside.write_text("[]", encoding="utf-8")
    allowed_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    allowed_dir.mkdir(parents=True)
    symlink_path = allowed_dir / "samples-link.json"
    symlink_path.symlink_to(outside)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    def fail_if_read(*args, **kwargs):
        raise AssertionError("planned samples file was read before path guard")

    monkeypatch.setattr("builtins.open", fail_if_read)

    outside_samples, outside_errors = tool._load_planned_samples_file(str(outside))
    symlink_samples, symlink_errors = tool._load_planned_samples_file(str(symlink_path))

    assert outside_samples is None
    assert outside_errors == ["planned samples file path is outside repository"]
    assert symlink_samples is None
    assert symlink_errors == ["planned samples file path resolves outside repository"]


def test_sample_plan_loads_relative_planned_samples_file_from_repo_root(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    planned_samples_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    planned_samples_dir.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.chdir(tmp_path)
    planned_samples_path = planned_samples_dir / "planned_samples.json"
    planned_samples_path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "alimeeting-eval-manual-review-001",
                    "source_id": "alimeeting_openslr_slr119",
                    "source_url": "https://www.openslr.org/119/",
                    "source_license": "CC BY-SA 4.0",
                    "archive_name": "Eval_Ali.tar.gz",
                    "archive_member_path": "Eval_Ali/<official-member-path-placeholder>.wav",
                    "clip_start_seconds": 0,
                    "clip_end_seconds": 60,
                    "expected_duration_seconds": 60,
                    "expected_sha256_after_extract": "9" * 64,
                    "license_citation": "AliMeeting / OpenSLR SLR119 / CC BY-SA 4.0",
                    "cleanup_required": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    samples, errors = tool._load_planned_samples_file(
        "artifacts/tmp/public_audio/planned_samples.json",
    )

    assert errors == []
    assert samples is not None
    assert samples[0]["sample_id"] == "alimeeting-eval-manual-review-001"


def test_sample_plan_rejects_planned_samples_over_budget():
    tool = load_tool_module()

    overlong_sample = {
        "sample_id": "overlong-clip",
        "source_id": "aishell4_openslr_slr111",
        "source_url": "https://www.openslr.org/111/",
        "source_license": "CC BY-SA 4.0",
        "archive_name": "test.tar.gz",
        "archive_member_path": "test/audio.wav",
        "clip_start_seconds": 0,
        "clip_end_seconds": 181,
        "expected_duration_seconds": 181,
        "expected_sha256_after_extract": "6" * 64,
        "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
        "cleanup_required": True,
    }
    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
        max_clip_seconds=180,
        planned_samples=[overlong_sample],
    )
    assert report["plan_status"] == "blocked"
    assert "planned_samples[0].duration exceeds max_clip_seconds" in report["validation_errors"]

    valid_sample = {
        "sample_id": "valid-60s",
        "source_id": "aishell4_openslr_slr111",
        "source_url": "https://www.openslr.org/111/",
        "source_license": "CC BY-SA 4.0",
        "archive_name": "test.tar.gz",
        "archive_member_path": "test/audio.wav",
        "clip_start_seconds": 0,
        "clip_end_seconds": 60,
        "expected_duration_seconds": 60,
        "expected_sha256_after_extract": "7" * 64,
        "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
        "cleanup_required": True,
    }
    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
        sample_budget_count=1,
        planned_samples=[valid_sample, valid_sample | {"sample_id": "valid-60s-second"}],
    )
    assert report["plan_status"] == "blocked"
    assert "planned_samples count exceeds sample_budget_count" in report["validation_errors"]

    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
        sample_budget_minutes=1,
        planned_samples=[valid_sample, valid_sample | {"sample_id": "valid-60s-second"}],
    )
    assert report["plan_status"] == "blocked"
    assert "planned_samples total duration exceeds sample_budget_minutes" in report["validation_errors"]


def test_public_sample_plan_example_is_no_download_and_path_safe():
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    assert example["source_id"] == "aishell4_openslr_slr111"
    assert example["source_snapshot_date"] == "2026-07-02"
    assert example["source_split"] == "test"
    assert example["target_root"] == "artifacts/tmp/public_audio"
    assert example["download_status"] == "not_started"
    assert example["safe_to_download_now"] is False
    assert example["planned_sample_schema"]["required_fields_before_download"] == [
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
    assert example["planned_samples"] == []
    example_json = json.dumps(example, ensure_ascii=False)
    assert "/Users/" not in example_json
    assert "configs/local" not in example_json


def test_public_sample_plan_cli_validates_planned_samples_file(monkeypatch, tmp_path, capsys):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    planned_samples_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    planned_samples_dir.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    planned_samples_path = planned_samples_dir / "planned_samples.json"
    planned_samples_path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "aishell4-test-placeholder-001",
                    "source_id": "aishell4_openslr_slr111",
                    "source_url": "https://www.openslr.org/111/",
                    "source_license": "CC BY-SA 4.0",
                    "archive_name": "test.tar.gz",
                    "archive_member_path": "test/<official-member-path-placeholder>.wav",
                    "clip_start_seconds": 0,
                    "clip_end_seconds": 60,
                    "expected_duration_seconds": 60,
                    "expected_sha256_after_extract": "8" * 64,
                    "license_citation": "AISHELL-4 / OpenSLR SLR111 / CC BY-SA 4.0",
                    "cleanup_required": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = tool.main(
        [
            "--source-id",
            "aishell4_openslr_slr111",
            "--target-root",
            "artifacts/tmp/public_audio",
            "--planned-samples-file",
            str(planned_samples_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["planned_samples_status"] == "schema_validated_no_download"
    assert report["planned_sample_count"] == 1
    assert report["planned_total_duration_seconds"] == 60
    assert report["safe_to_download_now"] is False
    assert report["download_command"] is None


def test_public_sample_plan_cli_reports_invalid_planned_samples_file_without_path_leak(monkeypatch, tmp_path, capsys):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    planned_samples_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    planned_samples_dir.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    planned_samples_path = planned_samples_dir / "planned_samples.json"
    planned_samples_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    exit_code = tool.main(
        [
            "--source-id",
            "aishell4_openslr_slr111",
            "--target-root",
            "artifacts/tmp/public_audio",
            "--planned-samples-file",
            str(planned_samples_path),
        ]
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["plan_status"] == "blocked"
    assert report["planned_samples_status"] == "invalid"
    assert "planned samples file must contain a JSON array" in report["validation_errors"]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "/private/" not in report_json
