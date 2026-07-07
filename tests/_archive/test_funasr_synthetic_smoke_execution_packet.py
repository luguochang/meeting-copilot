import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_execution_packet.py"


EXPECTED_FALSE_FLAGS = [
    "safe_to_execute_now",
    "safe_to_run_asr_now",
    "safe_to_download_models_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_public_audio_now",
    "safe_to_read_audio_file_now",
    "safe_to_write_artifacts_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_synthetic_smoke_execution_packet",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ready_readiness_report() -> dict:
    return {
        "report_mode": "funasr_synthetic_smoke_readiness",
        "report_version": "funasr_synthetic_smoke_readiness.v1",
        "readiness_status": "cache_preflight_passed_offline_execution_not_proven",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "device": "cpu",
        "venv_python": "code/asr_runtime/.venv-funasr/bin/python",
        "funasr_script": "code/asr_runtime/scripts/transcribe_funasr.py",
        "local_model_dir_label": (
            "modelscope_runtime_models_iic/"
            "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
        ),
        "required_cached_models_status": "present",
        "offline_guard_status": "required_before_execution",
        "model_download_status": "not_started",
        "execution_mode": "preflight_only_no_execution_authorization",
        "safe_to_execute_local_funasr_now": False,
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "validation_errors": [],
    }


def test_default_packet_blocks_without_funasr_readiness():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_execution_packet()

    assert report["decision_id"] == "DRV-045"
    assert report["packet_mode"] == "funasr_synthetic_smoke_execution_packet"
    assert report["packet_status"] == "blocked_missing_funasr_readiness"
    assert report["execution_approval_status"] == "not_approved"
    assert report["command_previews"] == []
    assert report["expected_drv044_batch_artifact_provenance"] is None
    assert report["next_action"] == "provide_drv043_funasr_readiness_evidence"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_ready_readiness_builds_batch_execution_packet_without_leaking_local_paths():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_report=ready_readiness_report(),
    )

    assert report["packet_status"] == "ready_for_manual_batch_funasr_synthetic_smoke_run"
    assert report["execution_approval_status"] == "not_approved_manual_run_only"
    assert report["provider"] == "funasr_streaming"
    assert report["model_alias"] == "paraformer-zh-streaming"
    assert report["scenario_count"] == 5
    assert report["engineering_scenario_count"] == 4
    assert report["negative_control_count"] == 1
    assert [scenario["scenario_id"] for scenario in report["scenario_execution_specs"]] == [
        "api-review-001",
        "architecture-review-001",
        "incident-review-001",
        "release-review-001",
        "non-engineering-control-001",
    ]
    assert len(report["command_previews"]) == 5
    first_command = report["command_previews"][0]["argv"]
    assert first_command[:3] == [
        "code/asr_runtime/.venv-funasr/bin/python",
        "code/asr_runtime/scripts/transcribe_funasr.py",
        "artifacts/tmp/synthetic_audio/api-review-001.wav",
    ]
    assert "--local-model-dir" in first_command
    assert (
        "<modelscope_runtime_models_iic/"
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online>"
    ) in first_command
    assert first_command[-2:] == [
        "--events-output",
        "artifacts/tmp/asr_events/api-review-001.funasr.events.json",
    ]
    assert len(report["postprocess_command_previews"]) == 5
    first_postprocess = report["postprocess_command_previews"][0]
    assert first_postprocess["scenario_id"] == "api-review-001"
    assert first_postprocess["transcript_report_argv"] == [
        "python3",
        "code/asr_runtime/scripts/transcript_report.py",
        "--audio",
        "artifacts/tmp/synthetic_audio/api-review-001.wav",
        "--provider-json",
        "artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        "--glossary",
        "data/asr_eval/glossaries/technical-terms.zh.json",
        "--output",
        "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
    ]
    assert first_postprocess["smoke_report_argv"] == [
        "python3",
        "tools/funasr_synthetic_smoke_single_result_builder.py",
        "--provider-json",
        "artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        "--transcript-report",
        "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        "--events-json",
        "artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        "--script-json",
        "data/asr_eval/synthetic_meetings/scripts/api-review.json",
    ]
    assert report["expected_outputs"]["smoke_report_paths"] == [
        "artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        "artifacts/tmp/asr_reports/architecture-review-001.funasr.smoke-report.json",
        "artifacts/tmp/asr_reports/incident-review-001.funasr.smoke-report.json",
        "artifacts/tmp/asr_reports/release-review-001.funasr.smoke-report.json",
        "artifacts/tmp/asr_reports/non-engineering-control-001.funasr.smoke-report.json",
    ]
    provenance = report["expected_drv044_batch_artifact_provenance"]
    assert provenance["source_kind"] == "local_funasr_synthetic_smoke_artifacts"
    assert len(provenance["artifacts"]) == 5
    assert provenance["artifacts"][0] == {
        "artifact_kind": "funasr_synthetic_smoke_result_report",
        "scenario_id": "api-review-001",
        "path": "artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        "sha256_source": "compute_after_manual_run",
    }
    assert report["next_action"] == (
        "manual_user_run_each_command_then_compute_sha256_and_submit_drv044_batch_evidence"
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_execution_packet_requires_full_engineering_and_negative_control_scenario_set():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_report=ready_readiness_report(),
        scenario_ids=[
            "api-review-001",
            "architecture-review-001",
            "incident-review-001",
            "release-review-001",
        ],
    )

    assert report["packet_status"] == "blocked_missing_required_scenarios"
    assert "missing required negative-control scenario: non-engineering-control-001" in report[
        "validation_errors"
    ]
    assert report["command_previews"] == []
    assert report["postprocess_command_previews"] == []
    assert report["expected_drv044_batch_artifact_provenance"] is None
    assert report["safe_to_execute_now"] is False


def test_execution_packet_rejects_unsafe_scenario_id_before_building_commands():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_report=ready_readiness_report(),
        scenario_ids=[
            "api-review-001",
            "architecture-review-001",
            "incident-review-001",
            "release-review-001",
            "configs/local/private-meeting",
            "non-engineering-control-001",
        ],
    )

    assert report["packet_status"] == "blocked_by_scenario_guard"
    assert report["validation_errors"] == ["scenario_id is unsafe: configs/local/private-meeting"]
    assert report["command_previews"] == []
    assert report["postprocess_command_previews"] == []
    assert report["expected_drv044_batch_artifact_provenance"] is None
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_execution_packet_rejects_readiness_with_side_effect_flags():
    tool = load_tool_module()
    readiness = ready_readiness_report()
    readiness["safe_to_call_remote_asr"] = True

    report = tool.build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_report=readiness,
    )

    assert report["packet_status"] == "blocked_invalid_funasr_readiness"
    assert "funasr_readiness safe_to_call_remote_asr must be false" in report[
        "validation_errors"
    ]
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["command_previews"] == []
    assert report["postprocess_command_previews"] == []


def test_execution_packet_cli_blocks_forbidden_readiness_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("readiness path was read before guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_path="configs/local/funasr-readiness.json",
    )

    assert report["packet_status"] == "blocked_by_readiness_path_guard"
    assert report["funasr_readiness_read_status"] == "blocked"
    assert report["validation_errors"] == [
        "funasr_readiness_path is blocked: configs/local"
    ]
    assert report["safe_to_read_configs_local_now"] is False
