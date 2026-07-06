import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "public_audio_planned_sample_manifest_decision.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "public_audio_planned_sample_manifest_decision",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_audio_manifest_decision_blocks_without_verified_archive_members():
    tool = load_tool_module()

    report = tool.build_public_audio_planned_sample_manifest_decision()

    assert report["decision_mode"] == "public_audio_planned_sample_manifest_decision_only"
    assert report["decision_id"] == "DRV-031"
    assert report["decision_status"] == "blocked_no_verified_public_sample_manifest"
    assert report["public_audio_stage_status"] == "blocked_no_planned_samples"
    assert report["safe_to_download_now"] is False
    assert report["safe_to_extract_now"] is False
    assert report["safe_to_transcode_now"] is False
    assert report["safe_to_call_asr_now"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["download_command"] is None
    assert report["extract_command"] is None
    assert report["transcode_command"] is None
    assert report["candidate_source_order"] == [
        "alimeeting_openslr_slr119",
        "aishell4_openslr_slr111",
    ]
    assert report["blocked_reasons"] == [
        "no_verified_archive_member_path",
        "no_expected_clip_sha256_after_extract",
        "no_user_approval_for_gb_archive_download",
    ]
    assert report["required_manifest_evidence"] == [
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
    assert report["next_action"] == "obtain_verified_archive_index_or_keep_blocked"
    report_json = json.dumps(report, ensure_ascii=False)
    assert "<official-member-path-placeholder>" not in report_json
    assert "placeholder" not in report_json.lower()
    assert "/Users/" not in report_json


def test_public_audio_manifest_decision_accepts_schema_validated_manifest_without_download(
    monkeypatch,
    tmp_path,
):
    tool = load_tool_module()
    sample_tool = tool.public_audio_sample_extraction_plan
    repo_root = tmp_path / "repo"
    planned_samples_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    planned_samples_dir.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(sample_tool, "REPO_ROOT", repo_root)
    planned_samples_path = planned_samples_dir / "alimeeting-planned-samples.json"
    planned_samples_path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "alimeeting-eval-review-001",
                    "source_id": "alimeeting_openslr_slr119",
                    "source_url": "https://www.openslr.org/119/",
                    "source_license": "CC BY-SA 4.0",
                    "archive_name": "Eval_Ali.tar.gz",
                    "archive_member_path": "Eval_Ali/session-001/far/meeting.wav",
                    "clip_start_seconds": 0,
                    "clip_end_seconds": 60,
                    "expected_duration_seconds": 60,
                    "expected_sha256_after_extract": "a" * 64,
                    "license_citation": "AliMeeting / OpenSLR SLR119 / CC BY-SA 4.0",
                    "cleanup_required": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    report = tool.build_public_audio_planned_sample_manifest_decision(
        source_id="alimeeting_openslr_slr119",
        planned_samples_file="artifacts/tmp/public_audio/alimeeting-planned-samples.json",
    )

    assert report["decision_status"] == "schema_validated_no_download"
    assert report["public_audio_stage_status"] == "ready_for_manual_download_review"
    assert report["planned_sample_count"] == 1
    assert report["planned_total_duration_seconds"] == 60
    assert report["safe_to_download_now"] is False
    assert report["safe_to_extract_now"] is False
    assert report["download_command"] is None
    assert report["extract_command"] is None
    assert report["transcode_command"] is None


def test_public_audio_manifest_decision_rejects_forbidden_manifest_path_before_read(
    monkeypatch,
):
    tool = load_tool_module()
    sample_tool = tool.public_audio_sample_extraction_plan

    def fail_if_read(*args, **kwargs):
        raise AssertionError("planned samples file was read before path guard")

    monkeypatch.setattr(Path, "open", fail_if_read)

    report = tool.build_public_audio_planned_sample_manifest_decision(
        source_id="alimeeting_openslr_slr119",
        planned_samples_file="configs/local/public-audio-samples.json",
    )

    assert report["decision_status"] == "blocked_by_manifest_validation"
    assert report["public_audio_stage_status"] == "blocked"
    assert "planned samples file path is forbidden" in report["validation_errors"]
    assert report["safe_to_download_now"] is False
    assert sample_tool.REPO_ROOT == tool.REPO_ROOT


def test_public_audio_manifest_decision_does_not_promote_observed_candidates():
    tool = load_tool_module()

    report = tool.build_public_audio_planned_sample_manifest_decision(
        source_id="magichub_web_meeting_candidate",
    )

    assert report["decision_status"] == "blocked_by_manifest_validation"
    assert report["public_audio_stage_status"] == "blocked"
    assert "source_id is not approved for executable public sample manifest decision" in report[
        "validation_errors"
    ]
    assert report["candidate_source_order"] == [
        "alimeeting_openslr_slr119",
        "aishell4_openslr_slr111",
    ]
    assert report["safe_to_download_now"] is False
