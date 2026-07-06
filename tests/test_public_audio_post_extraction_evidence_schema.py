import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "public_audio_post_extraction_evidence_schema.py"


EXPECTED_FALSE_FLAGS = [
    "safe_to_download_public_audio_now",
    "safe_to_extract_public_audio_now",
    "safe_to_transcode_audio_now",
    "safe_to_read_audio_file_now",
    "safe_to_call_asr_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_run_external_command_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "public_audio_post_extraction_evidence_schema",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_evidence_report() -> dict:
    return {
        "manifest_version": "public_audio_post_extraction_evidence.v1",
        "planned_sample_id": "alimeeting-eval-clip-001",
        "source_id": "alimeeting_openslr_slr119",
        "source_url": "https://www.openslr.org/119/",
        "source_license": "CC BY-SA 4.0",
        "source_snapshot_date": "2026-07-03",
        "archive_name": "Eval_Ali.tar.gz",
        "archive_member_path": "Eval_Ali/session-001/audio.wav",
        "clip_start_seconds": 0,
        "clip_end_seconds": 45,
        "expected_duration_seconds": 45,
        "expected_sha256_after_extract": "a" * 64,
        "observed_sha256": "a" * 64,
        "observed_duration_seconds": 45,
        "sample_rate_hz": 16000,
        "channel_count": 1,
        "container_format": "wav",
        "codec": "pcm_s16le",
        "license_citation": "AliMeeting / OpenSLR SLR119 / CC BY-SA 4.0",
        "cleanup_status": "deleted_after_evidence_recorded",
        "derived_artifact_root": "artifacts/tmp/public_audio",
        "safe_to_download_public_audio": False,
        "safe_to_extract_public_audio": False,
        "safe_to_transcode_audio": False,
        "safe_to_read_audio_file": False,
        "safe_to_call_asr": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
    }


def test_default_schema_report_specifies_post_extraction_evidence_contract_without_audio_access():
    tool = load_tool_module()

    report = tool.build_public_audio_post_extraction_evidence_schema()

    assert report["decision_id"] == "DRV-034"
    assert report["report_mode"] == "public_audio_post_extraction_evidence_schema"
    assert report["schema_version"] == "public_audio_post_extraction_evidence.v1"
    assert report["schema_status"] == "specified_not_executable"
    assert report["execution_boundary"] == "schema_only_no_audio_read_no_download_no_asr"
    assert report["evidence_report_status"] == "not_provided"
    assert report["approved_evidence_report_root"] == "artifacts/tmp/public_audio"
    assert "observed_sha256" in report["required_fields"]
    assert "cleanup_status" in report["required_fields"]
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_post_extraction_evidence_report_validates_without_reading_audio():
    tool = load_tool_module()

    report = tool.build_public_audio_post_extraction_evidence_schema(
        evidence_report=valid_evidence_report(),
    )

    assert report["evidence_report_status"] == "schema_validated_no_audio_access"
    assert report["evidence_report_validation_status"] == "passed"
    assert report["evidence_report_validation_errors"] == []
    assert report["evidence_summary"] == {
        "planned_sample_id": "alimeeting-eval-clip-001",
        "source_id": "alimeeting_openslr_slr119",
        "observed_duration_seconds": 45,
        "sample_rate_hz": 16000,
        "channel_count": 1,
        "observed_sha256": "a" * 64,
        "cleanup_status": "deleted_after_evidence_recorded",
    }
    assert report["safe_to_read_audio_file_now"] is False
    assert report["safe_to_call_asr_now"] is False
    assert report["safe_to_download_public_audio_now"] is False


def test_post_extraction_evidence_blocks_side_effect_flags_and_checksum_mismatch():
    tool = load_tool_module()
    evidence = valid_evidence_report()
    evidence["safe_to_read_audio_file"] = True
    evidence["observed_sha256"] = "b" * 64

    report = tool.build_public_audio_post_extraction_evidence_schema(evidence_report=evidence)

    assert report["evidence_report_status"] == "blocked_by_schema_validation"
    assert report["evidence_report_validation_status"] == "failed"
    assert "safe_to_read_audio_file must be false" in report["evidence_report_validation_errors"]
    assert (
        "observed_sha256 must match expected_sha256_after_extract"
        in report["evidence_report_validation_errors"]
    )
    assert report["safe_to_read_audio_file_now"] is False
    assert report["safe_to_call_asr_now"] is False


def test_post_extraction_evidence_rejects_forbidden_report_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("evidence report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_public_audio_post_extraction_evidence_schema(
        evidence_report_path="configs/local/public-audio-evidence.json",
    )

    assert report["evidence_report_status"] == "blocked_by_path_guard"
    assert report["evidence_report_read_status"] == "blocked"
    assert "evidence_report_path is blocked: configs/local" in report["evidence_report_validation_errors"]
    assert report["safe_to_read_audio_file_now"] is False


def test_post_extraction_evidence_rejects_all_forbidden_report_paths_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("evidence report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    forbidden_cases = [
        ("configs/local/public-audio-evidence.json", "evidence_report_path is blocked: configs/local"),
        (
            "data/asr_eval/local_samples/public-audio-evidence.json",
            "evidence_report_path is blocked: data/asr_eval/local_samples",
        ),
        (
            "data/asr_eval/samples/public-audio-evidence.json",
            "evidence_report_path is blocked: data/asr_eval/samples",
        ),
        (
            "data/local_runtime/public-audio-evidence.json",
            "evidence_report_path is blocked: data/local_runtime",
        ),
        ("outputs/public-audio-evidence.json", "evidence_report_path is blocked: outputs"),
    ]
    for path, expected_error in forbidden_cases:
        report = tool.build_public_audio_post_extraction_evidence_schema(
            evidence_report_path=path,
        )
        assert report["evidence_report_status"] == "blocked_by_path_guard"
        assert report["evidence_report_read_status"] == "blocked"
        assert expected_error in report["evidence_report_validation_errors"]


def test_post_extraction_evidence_rejects_repo_outside_symlink_and_non_json_report_paths_before_reading(
    monkeypatch,
    tmp_path,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    allowed_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    allowed_dir.mkdir(parents=True)
    outside_report = tmp_path / "public-audio-evidence.json"
    outside_report.write_text("{}", encoding="utf-8")
    symlink_report = allowed_dir / "linked-evidence.json"
    symlink_report.symlink_to(outside_report)
    text_report = allowed_dir / "evidence.txt"
    text_report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    def fail_if_read(*args, **kwargs):
        raise AssertionError("evidence report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    outside = tool.build_public_audio_post_extraction_evidence_schema(
        evidence_report_path=str(outside_report),
    )
    symlink = tool.build_public_audio_post_extraction_evidence_schema(
        evidence_report_path=str(symlink_report),
    )
    non_json = tool.build_public_audio_post_extraction_evidence_schema(
        evidence_report_path="artifacts/tmp/public_audio/evidence.txt",
    )

    assert outside["evidence_report_status"] == "blocked_by_path_guard"
    assert outside["evidence_report_validation_errors"] == ["evidence_report_path is outside repository"]
    assert symlink["evidence_report_status"] == "blocked_by_path_guard"
    assert symlink["evidence_report_validation_errors"] == [
        "evidence_report_path is outside repository"
    ]
    assert non_json["evidence_report_status"] == "blocked_by_path_guard"
    assert "evidence_report_path must be a JSON report file" in non_json[
        "evidence_report_validation_errors"
    ]


def test_post_extraction_evidence_rejects_audio_path_text_in_report_fields():
    tool = load_tool_module()
    evidence = valid_evidence_report()
    evidence["planned_sample_id"] = "data/asr_eval/local_samples/private.m4a"

    report = tool.build_public_audio_post_extraction_evidence_schema(evidence_report=evidence)

    assert report["evidence_report_status"] == "blocked_by_schema_validation"
    assert "planned_sample_id must not contain local path text" in report["evidence_report_validation_errors"]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "local_samples" not in report_json
    assert ".m4a" not in report_json


def test_post_extraction_evidence_blocks_schema_contract_violations():
    tool = load_tool_module()

    invalid_cases = [
        (
            {"source_url": "https://www.openslr.org/111/"},
            "source_url must match approved source",
        ),
        (
            {"archive_member_path": "../Eval_Ali/private.wav"},
            "archive_member_path must be a safe relative archive member path",
        ),
        (
            {"clip_start_seconds": 20, "clip_end_seconds": 20},
            "clip_end_seconds must be greater than clip_start_seconds",
        ),
        (
            {"observed_duration_seconds": 48},
            "observed_duration_seconds must match expected_duration_seconds within 1 second",
        ),
        (
            {"sample_rate_hz": 0},
            "sample_rate_hz must be a positive integer",
        ),
        (
            {"channel_count": 0},
            "channel_count must be a positive integer",
        ),
        (
            {"cleanup_status": "not_cleaned"},
            "cleanup_status must be one of deleted_after_evidence_recorded, kept_ignored_until_manual_cleanup",
        ),
        (
            {"derived_artifact_root": "outputs/public_audio"},
            "derived_artifact_root must be under approved public audio artifact root",
        ),
    ]

    for patch, expected_error in invalid_cases:
        evidence = valid_evidence_report() | patch
        report = tool.build_public_audio_post_extraction_evidence_schema(
            evidence_report=evidence,
        )
        assert report["evidence_report_status"] == "blocked_by_schema_validation"
        assert expected_error in report["evidence_report_validation_errors"]
        assert report["safe_to_read_audio_file_now"] is False
        assert report["safe_to_call_asr_now"] is False


def test_public_audio_post_extraction_evidence_cli_outputs_json_without_side_effects(capsys):
    tool = load_tool_module()

    exit_code = tool.main([], out=io.StringIO())

    assert exit_code == 0


def test_tool_source_does_not_read_audio_run_commands_or_call_remote_services():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "ffmpeg",
        "afconvert",
        "sounddevice",
        "pyaudio",
        "AudioSegment",
        "wave.open",
        "requests.",
        "urllib.request",
        "modelscope",
        "AutoModel",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source
