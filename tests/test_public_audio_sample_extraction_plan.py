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


def alimeeting_sample(**overrides):
    sample = {
        "sample_id": "alimeeting-eval-review-001",
        "source_id": "alimeeting_openslr_slr119",
        "source_url": "https://www.openslr.org/119/",
        "source_license": "CC BY-SA 4.0",
        "archive_name": "Eval_Ali.tar.gz",
        "archive_member_path": "Eval_Ali/session_eval_001/audio.wav",
        "clip_start_seconds": 0,
        "clip_end_seconds": 120,
        "expected_duration_seconds": 120,
        "expected_sha256_after_extract": "a" * 64,
        "license_citation": "AliMeeting / OpenSLR SLR119 / CC BY-SA 4.0",
        "cleanup_required": True,
    }
    sample.update(overrides)
    return sample


def test_chinese_meeting_source_defaults_to_no_download_and_public_wall_clock_candidate():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="alimeeting_openslr_slr119",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=1_200,
        max_download_bytes=900_000_000,
        sample_budget_count=10,
        sample_budget_minutes=20,
        max_clip_seconds=300,
    )

    assert report["plan_mode"] == "public_audio_sample_extraction_plan_only"
    assert report["plan_version"] == "public_audio_sample_extraction_plan.v1"
    assert report["plan_status"] == "blocked_no_planned_samples"
    assert report["review_status"] == "requires_manual_review"
    assert report["source_id"] == "alimeeting_openslr_slr119"
    assert report["source_language"] == "zh-CN Mandarin"
    assert report["source_priority_rank"] == 1
    assert report["dataset_role"] == "primary_mandarin_meeting_acoustics"
    assert report["meeting_acoustics_evidence"] is True
    assert report["baseline_only"] is False
    assert report["counts_toward_public_meeting_wall_clock_candidate"] is True
    assert report["source_split"] == "eval"
    assert report["recommended_next_gate"] == "public_audio_wall_clock_soak_after_post_extraction_evidence"
    assert report["download_status"] == "not_started"
    assert report["download_mode"] == "manual_review_only"
    assert report["safe_to_download_now"] is False
    assert report["safe_to_extract_now"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_commit_raw_audio"] is False
    assert report["remote_asr_call_count"] == 0
    assert report["llm_call_count"] == 0
    assert report["raw_audio_uploaded"] is False
    assert report["next_action"] == "create_concrete_public_audio_sample_manifest"
    assert report["planned_samples"] == []
    assert report["planned_sample_count"] == 0
    assert report["planned_total_duration_seconds"] == 0


def test_chinese_sources_are_ranked_and_aishell1_is_baseline_not_meeting_evidence():
    tool = load_tool_module()

    aishell4 = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=1_200,
        max_download_bytes=900_000_000,
        sample_budget_count=10,
        sample_budget_minutes=20,
    )
    aishell1 = tool.build_public_sample_extraction_plan(
        source_id="aishell1_openslr_slr33",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
        sample_budget_count=3,
        sample_budget_minutes=5,
    )

    assert aishell4["source_language"] == "zh-CN Mandarin"
    assert aishell4["source_priority_rank"] == 2
    assert aishell4["dataset_role"] == "supplemental_mandarin_meeting_acoustics"
    assert aishell4["meeting_acoustics_evidence"] is True
    assert aishell4["counts_toward_public_meeting_wall_clock_candidate"] is True
    assert aishell4["source_split"] == "test"

    assert aishell1["source_language"] == "zh-CN Mandarin"
    assert aishell1["source_priority_rank"] == 3
    assert aishell1["dataset_role"] == "mandarin_read_speech_baseline_only"
    assert aishell1["meeting_acoustics_evidence"] is False
    assert aishell1["baseline_only"] is True
    assert aishell1["counts_toward_public_meeting_wall_clock_candidate"] is False
    assert aishell1["recommended_next_gate"] == "mandarin_asr_runtime_smoke_only"
    assert "not proof of meeting acoustics" in aishell1["not_for_gate_reason"]


def test_sample_plan_validates_concrete_chinese_meeting_samples_without_download_commands():
    tool = load_tool_module()

    report = tool.build_public_sample_extraction_plan(
        source_id="alimeeting_openslr_slr119",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=1_200,
        max_download_bytes=900_000_000,
        sample_budget_count=10,
        sample_budget_minutes=20,
        max_clip_seconds=300,
        planned_samples=[
            alimeeting_sample(sample_id="alimeeting-eval-review-001"),
            alimeeting_sample(
                sample_id="alimeeting-eval-review-002",
                clip_start_seconds=120,
                clip_end_seconds=240,
                expected_sha256_after_extract="b" * 64,
            ),
        ],
    )

    assert report["plan_status"] == "ready_for_manual_download_review"
    assert report["planned_samples_status"] == "schema_validated_no_download"
    assert report["planned_sample_count"] == 2
    assert report["planned_total_duration_seconds"] == 240
    assert report["safe_to_download_now"] is False
    assert report["safe_to_extract_now"] is False
    assert report["download_command"] is None
    assert report["extract_command"] is None
    assert report["transcode_command"] is None
    assert report["remote_asr_call_count"] == 0
    assert report["llm_call_count"] == 0
    assert report["raw_audio_uploaded"] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "sk-" not in report_json


def test_sample_plan_rejects_unapproved_or_non_meeting_source_for_meeting_wall_clock():
    tool = load_tool_module()

    unknown = tool.build_public_sample_extraction_plan(
        source_id="random_video_site",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
    )
    aishell1_wall_clock = tool.build_public_sample_extraction_plan(
        source_id="aishell1_openslr_slr33",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=1_200,
        max_download_bytes=900_000_000,
        sample_budget_count=10,
        sample_budget_minutes=20,
        planned_samples=[
            {
                "sample_id": "aishell1-baseline-001",
                "source_id": "aishell1_openslr_slr33",
                "source_url": "https://www.openslr.org/33/",
                "source_license": "Apache License v2.0",
                "archive_name": "data_aishell.tgz",
                "archive_member_path": "data_aishell/wav/test/S0764/BAC009S0764W0121.wav",
                "clip_start_seconds": 0,
                "clip_end_seconds": 120,
                "expected_duration_seconds": 120,
                "expected_sha256_after_extract": "c" * 64,
                "license_citation": "AISHELL-1 / OpenSLR SLR33 / Apache License v2.0",
                "cleanup_required": True,
            }
        ],
    )

    assert unknown["plan_status"] == "blocked"
    assert "source_id is not approved" in unknown["validation_errors"]
    assert unknown["safe_to_download_now"] is False
    assert aishell1_wall_clock["plan_status"] == "ready_for_manual_download_review"
    assert aishell1_wall_clock["counts_toward_public_meeting_wall_clock_candidate"] is False
    assert aishell1_wall_clock["recommended_next_gate"] == "mandarin_asr_runtime_smoke_only"
    assert aishell1_wall_clock["safe_to_download_now"] is False


def test_sample_plan_rejects_forbidden_target_roots_and_unbounded_budgets():
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
            source_id="alimeeting_openslr_slr119",
            target_root=target_root,
            max_duration_seconds=180,
            max_download_bytes=600_000_000,
        )
        assert report["plan_status"] == "blocked"
        assert "target_root is forbidden" in report["validation_errors"]
        assert report["safe_to_download_now"] is False

    report = tool.build_public_sample_extraction_plan(
        source_id="alimeeting_openslr_slr119",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=0,
        max_download_bytes=0,
    )
    assert report["plan_status"] == "blocked"
    assert "max_duration_seconds must be between 1 and 1800" in report["validation_errors"]
    assert "max_download_bytes must be between 1 and 1000000000" in report["validation_errors"]


def test_sample_plan_rejects_unsafe_or_mismatched_planned_samples():
    tool = load_tool_module()

    invalid_cases = [
        (
            alimeeting_sample(archive_member_path="/tmp/audio.wav"),
            "planned_samples[0].archive_member_path must be a safe relative archive member path",
        ),
        (
            alimeeting_sample(archive_member_path="Eval_Ali/../audio.wav"),
            "planned_samples[0].archive_member_path must be a safe relative archive member path",
        ),
        (
            alimeeting_sample(clip_start_seconds=60, clip_end_seconds=60, expected_duration_seconds=0),
            "planned_samples[0].clip_end_seconds must be greater than clip_start_seconds",
        ),
        (
            alimeeting_sample(expected_sha256_after_extract="not-a-sha"),
            "planned_samples[0].expected_sha256_after_extract must be 64 lowercase hex characters",
        ),
        (
            alimeeting_sample(archive_member_path="Eval_Ali/<official-member-path-placeholder>.wav"),
            "planned_samples[0].archive_member_path must not contain placeholder text",
        ),
        (
            alimeeting_sample(cleanup_required=False),
            "planned_samples[0].cleanup_required must be true",
        ),
        (
            alimeeting_sample(
                source_id="aishell4_openslr_slr111",
                source_url="https://www.openslr.org/111/",
                source_license="CC BY-SA 4.0",
            ),
            "planned_samples[0].source_id must match selected source_id",
        ),
    ]

    for sample, expected_error in invalid_cases:
        report = tool.build_public_sample_extraction_plan(
            source_id="alimeeting_openslr_slr119",
            target_root="artifacts/tmp/public_audio",
            max_duration_seconds=1_200,
            max_download_bytes=900_000_000,
            max_clip_seconds=300,
            planned_samples=[sample],
        )
        assert report["plan_status"] == "blocked"
        assert report["planned_samples_status"] == "invalid"
        assert expected_error in report["validation_errors"]
        assert report["safe_to_download_now"] is False
        assert report["download_command"] is None


def test_sample_plan_rejects_planned_samples_file_before_read_when_path_forbidden(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("planned samples file was read before path guard")

    monkeypatch.setattr(Path, "open", fail_if_read)

    samples, errors = tool._load_planned_samples_file("configs/local/public-samples.json")

    assert samples is None
    assert errors == ["planned samples file path is forbidden"]


def test_public_sample_plan_example_is_chinese_no_download_and_path_safe():
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    assert example["source_id"] == "aishell4_openslr_slr111"
    assert example["example_purpose"] == "schema_example_only"
    assert example["source_language"] == "zh-CN Mandarin"
    assert example["meeting_acoustics_evidence"] is True
    assert example["baseline_only"] is False
    assert example["counts_toward_public_meeting_wall_clock_candidate"] is True
    assert example["download_status"] == "not_started"
    assert example["safe_to_download_now"] is False
    assert example["planned_samples"] == []
    example_json = json.dumps(example, ensure_ascii=False)
    assert "/Users/" not in example_json
    assert "configs/local" not in example_json


def test_public_sample_plan_cli_writes_no_download_report_to_approved_artifact_root(monkeypatch, tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    output_root = repo_root / "artifacts" / "tmp" / "asr_reports"
    output_root.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    output_path = output_root / "approval.json"

    exit_code = tool.main(
        [
            "--source-id",
            "alimeeting_openslr_slr119",
            "--target-root",
            "artifacts/tmp/public_audio",
            "--max-duration-seconds",
            "1200",
            "--max-download-bytes",
            "900000000",
            "--sample-budget-count",
            "10",
            "--sample-budget-minutes",
            "20",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["plan_status"] == "blocked_no_planned_samples"
    assert report["source_language"] == "zh-CN Mandarin"
    assert report["safe_to_download_now"] is False
    assert report["remote_asr_call_count"] == 0
    assert report["llm_call_count"] == 0
    assert report["raw_audio_uploaded"] is False
