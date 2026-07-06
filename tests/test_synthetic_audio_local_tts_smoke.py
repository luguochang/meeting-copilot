import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "synthetic_audio_local_tts_smoke.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_audio_local_tts_smoke",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_tts_smoke_dry_run_is_path_safe_and_does_not_execute():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_local_tts_smoke_report(
        script_id="api-review-001",
        target_root="artifacts/tmp/synthetic_audio",
        execute_local_tts=False,
    )

    assert report["report_mode"] == "synthetic_audio_local_tts_smoke"
    assert report["report_version"] == "synthetic_audio_local_tts_smoke.v1"
    assert report["smoke_status"] == "ready_for_explicit_local_tts_execution"
    assert report["script_id"] == "api-review-001"
    assert report["script_file"] == "data/asr_eval/synthetic_meetings/scripts/api-review.json"
    assert report["target_root"] == "artifacts/tmp/synthetic_audio"
    assert report["aiff_output_path"] == "artifacts/tmp/synthetic_audio/api-review-001.aiff"
    assert report["wav_output_path"] == "artifacts/tmp/synthetic_audio/api-review-001.wav"
    assert report["generation_status"] == "not_started"
    assert report["safe_to_execute_local_tts_now"] is False
    assert report["safe_to_call_remote_tts"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_commit_generated_audio"] is False
    assert report["turn_count"] == 3
    assert report["validation_errors"] == []
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_local_tts_smoke_rejects_unknown_script_and_forbidden_root():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_local_tts_smoke_report(
        script_id="unknown-script",
        target_root="data/asr_eval/local_samples",
        execute_local_tts=False,
    )

    assert report["smoke_status"] == "blocked"
    assert "script_id is not approved" in report["validation_errors"]
    assert "target_root is not allowed" in report["validation_errors"]
    assert report["safe_to_execute_local_tts_now"] is False


def test_local_tts_smoke_explicit_execution_requires_allowed_script_and_root():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_local_tts_smoke_report(
        script_id="release-review-001",
        target_root="artifacts/tmp/synthetic_audio",
        execute_local_tts=True,
        run_commands=False,
    )

    assert report["smoke_status"] == "ready_for_local_tts_execution"
    assert report["safe_to_execute_local_tts_now"] is True
    assert report["generation_status"] == "execution_skipped_by_test_harness"
    assert report["tts_command_preview"][0] == "say"
    assert report["validation_errors"] == []
