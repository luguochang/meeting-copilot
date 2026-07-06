import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_approved_runner.py"

APPROVAL_TOKEN = "APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY"
EXPECTED_FALSE_FLAGS = [
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_synthetic_smoke_approved_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ready_packet() -> dict:
    scenarios = [
        ("api-review-001", "api-review"),
        ("architecture-review-001", "architecture-review"),
        ("incident-review-001", "incident-review"),
        ("release-review-001", "release-review"),
        ("non-engineering-control-001", "non-engineering-control"),
    ]
    command_previews = []
    postprocess_command_previews = []
    events_paths = []
    provider_paths = []
    transcript_paths = []
    smoke_paths = []
    artifacts = []
    for scenario_id, script_stem in scenarios:
        audio_path = f"artifacts/tmp/synthetic_audio/{scenario_id}.wav"
        events_path = f"artifacts/tmp/asr_events/{scenario_id}.funasr.events.json"
        provider_path = f"artifacts/tmp/asr_reports/{scenario_id}.funasr.provider.json"
        transcript_path = f"artifacts/tmp/asr_reports/{scenario_id}.funasr.transcript-report.json"
        smoke_path = f"artifacts/tmp/asr_reports/{scenario_id}.funasr.smoke-report.json"
        command_previews.append(
            {
                "scenario_id": scenario_id,
                "scenario_kind": "negative_control"
                if scenario_id.startswith("non-engineering")
                else "engineering",
                "argv": [
                    "code/asr_runtime/.venv-funasr/bin/python",
                    "code/asr_runtime/scripts/transcribe_funasr.py",
                    audio_path,
                    "--streaming",
                    "--model",
                    "paraformer-zh-streaming",
                    "--local-model-dir",
                    "<modelscope_runtime_models_iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online>",
                    "--device",
                    "cpu",
                    "--chunk-size",
                    "0,10,5",
                    "--encoder-chunk-look-back",
                    "4",
                    "--decoder-chunk-look-back",
                    "1",
                    "--final-window-ms",
                    "3000",
                    "--events-output",
                    events_path,
                ],
                "stdout_redirect_path": provider_path,
            }
        )
        postprocess_command_previews.append(
            {
                "scenario_id": scenario_id,
                "scenario_kind": "negative_control"
                if scenario_id.startswith("non-engineering")
                else "engineering",
                "transcript_report_argv": [
                    "python3",
                    "code/asr_runtime/scripts/transcript_report.py",
                    "--audio",
                    audio_path,
                    "--provider-json",
                    provider_path,
                    "--glossary",
                    "data/asr_eval/glossaries/technical-terms.zh.json",
                    "--output",
                    transcript_path,
                ],
                "smoke_report_argv": [
                    "python3",
                    "tools/funasr_synthetic_smoke_single_result_builder.py",
                    "--provider-json",
                    provider_path,
                    "--transcript-report",
                    transcript_path,
                    "--events-json",
                    events_path,
                    "--script-json",
                    f"data/asr_eval/synthetic_meetings/scripts/{script_stem}.json",
                ],
                "smoke_report_stdout_redirect_path": smoke_path,
            }
        )
        events_paths.append(events_path)
        provider_paths.append(provider_path)
        transcript_paths.append(transcript_path)
        smoke_paths.append(smoke_path)
        artifacts.append(
            {
                "artifact_kind": "funasr_synthetic_smoke_result_report",
                "scenario_id": scenario_id,
                "path": smoke_path,
                "sha256_source": "compute_after_manual_run",
            }
        )
    return {
        "decision_id": "DRV-045",
        "packet_mode": "funasr_synthetic_smoke_execution_packet",
        "packet_version": "funasr_synthetic_smoke_execution_packet.v1",
        "packet_status": "ready_for_manual_batch_funasr_synthetic_smoke_run",
        "execution_approval_status": "not_approved_manual_run_only",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "scenario_count": 5,
        "engineering_scenario_count": 4,
        "negative_control_count": 1,
        "command_previews": command_previews,
        "postprocess_command_previews": postprocess_command_previews,
        "expected_outputs": {
            "events_paths": events_paths,
            "provider_output_paths": provider_paths,
            "transcript_report_paths": transcript_paths,
            "smoke_report_paths": smoke_paths,
        },
        "expected_drv044_batch_artifact_provenance": {
            "source_kind": "local_funasr_synthetic_smoke_artifacts",
            "artifacts": artifacts,
        },
        "safe_to_run_asr_now": False,
        "safe_to_read_audio_file_now": False,
        "safe_to_write_artifacts_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "validation_errors": [],
    }


def approval_record() -> dict:
    return {
        "approval_record_version": "funasr_synthetic_smoke_execution_approval.v1",
        "approval_id": "funasr-synthetic-smoke-approval-001",
        "approval_scope": "local_funasr_synthetic_smoke_5_scenarios_only",
        "approval_token": APPROVAL_TOKEN,
        "approval_confirmed_by_user": True,
        "approved_packet_path": "artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json",
        "approved_scenario_count": 5,
        "allow_read_synthetic_audio": True,
        "allow_write_ignored_asr_artifacts": True,
        "allow_run_local_funasr": True,
        "deny_real_user_audio": True,
        "deny_microphone": True,
        "deny_remote_asr": True,
        "deny_llm": True,
        "deny_model_download": True,
    }


def create_model_dir(tmp_path: Path) -> Path:
    model_dir = tmp_path / "modelscope" / "iic" / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.pt").write_text("fake", encoding="utf-8")
    (model_dir / "config.yaml").write_text("fake", encoding="utf-8")
    return model_dir


def test_default_runner_is_dry_run_and_never_executes(tmp_path):
    tool = load_tool_module()
    calls = []

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=ready_packet(),
        execute=False,
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert report["runner_id"] == "DRV-048"
    assert report["runner_status"] == "dry_run_ready_requires_execute_flag_and_approval"
    assert report["approval_record_status"] == "not_provided"
    assert report["planned_provider_command_count"] == 5
    assert report["planned_postprocess_command_count"] == 10
    assert report["executed_command_count"] == 0
    assert calls == []
    assert report["safe_to_run_asr_now"] is False
    assert report["safe_to_read_synthetic_audio_now"] is False
    assert report["safe_to_write_ignored_asr_artifacts_now"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_execute_requires_valid_approval_and_local_model_dir(tmp_path):
    tool = load_tool_module()
    calls = []

    missing_approval = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=ready_packet(),
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )
    bad_model = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=ready_packet(),
        approval_record=approval_record(),
        execute=True,
        local_model_dir=tmp_path / "missing-model",
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert missing_approval["runner_status"] == "blocked_missing_or_invalid_execution_approval"
    assert "approval_record is required when execute=true" in missing_approval["validation_errors"]
    assert bad_model["runner_status"] == "blocked_invalid_local_model_dir"
    assert "local_model_dir is missing" in bad_model["validation_errors"]
    assert calls == []


def test_execute_rejects_unconfirmed_approval_template(tmp_path):
    tool = load_tool_module()
    calls = []
    unconfirmed = approval_record()
    unconfirmed["approval_confirmed_by_user"] = False

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=ready_packet(),
        approval_record=unconfirmed,
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert report["runner_status"] == "blocked_missing_or_invalid_execution_approval"
    assert "approval_confirmed_by_user must be true" in report["validation_errors"]
    assert calls == []


def test_valid_execute_uses_fake_runner_for_provider_and_postprocess_commands(tmp_path):
    tool = load_tool_module()
    calls = []

    def fake_run(argv, *, stdout_path=None):
        calls.append({"argv": argv, "stdout_path": stdout_path})
        return {"returncode": 0, "stderr": "", "stdout_path": stdout_path}

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=ready_packet(),
        approval_record=approval_record(),
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=fake_run,
    )

    assert report["runner_status"] == "executed_local_funasr_synthetic_smoke_commands"
    assert report["approval_record_status"] == "passed"
    assert report["executed_command_count"] == 15
    assert len(calls) == 15
    first_provider = calls[0]
    assert first_provider["argv"][:2] == [
        "code/asr_runtime/.venv-funasr/bin/python",
        "code/asr_runtime/scripts/transcribe_funasr.py",
    ]
    assert "--local-model-dir" in first_provider["argv"]
    local_model_dir_arg = first_provider["argv"][first_provider["argv"].index("--local-model-dir") + 1]
    assert local_model_dir_arg == str(create_model_dir(tmp_path))
    assert first_provider["stdout_path"] == "artifacts/tmp/asr_reports/api-review-001.funasr.provider.json"
    assert calls[5]["argv"][1] == "code/asr_runtime/scripts/transcript_report.py"
    assert calls[6]["argv"][1] == "tools/funasr_synthetic_smoke_single_result_builder.py"
    assert calls[6]["stdout_path"] == "artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json"
    assert report["safe_to_run_asr_now"] is True
    assert report["safe_to_read_synthetic_audio_now"] is True
    assert report["safe_to_write_ignored_asr_artifacts_now"] is True
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json


def test_runner_blocks_forbidden_packet_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("packet path was read before guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet_path="configs/local/funasr.packet.json",
        execute=False,
    )

    assert report["runner_status"] == "blocked_by_packet_path_guard"
    assert report["packet_read_status"] == "blocked"
    assert "execution_packet_path is blocked: configs/local" in report["validation_errors"]
    assert report["safe_to_read_configs_local_now"] is False


def test_runner_unwraps_approval_record_template_report_from_path(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    approval_dir = repo_root / "artifacts" / "tmp"
    approval_dir.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    approval_path = approval_dir / "approval-wrapper.json"
    wrapper = {
        "approval_record_builder_id": "DRV-049",
        "report_mode": "funasr_synthetic_smoke_execution_approval_record",
        "approval_template_status": "approval_record_template_ready_not_confirmed",
        "approval_record_template": approval_record() | {"approval_confirmed_by_user": False},
    }
    approval_path.write_text(json.dumps(wrapper), encoding="utf-8")

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=ready_packet(),
        approval_record_path="artifacts/tmp/approval-wrapper.json",
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: {"returncode": 0, "stderr": ""},
    )

    assert report["runner_status"] == "blocked_missing_or_invalid_execution_approval"
    assert report["approval_record_status"] == "failed"
    assert report["validation_errors"] == ["approval_confirmed_by_user must be true"]
    assert report["executed_command_count"] == 0


def test_runner_blocks_packet_with_forbidden_provider_audio_path_before_execute(tmp_path):
    tool = load_tool_module()
    packet = ready_packet()
    packet["command_previews"][0]["argv"][2] = "configs/local/private.wav"
    calls = []

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=packet,
        approval_record=approval_record(),
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert report["runner_status"] == "blocked_invalid_execution_packet"
    assert "provider argv audio path for api-review-001 is blocked: configs/local" in report["validation_errors"]
    assert report["executed_command_count"] == 0
    assert calls == []
    assert report["safe_to_run_asr_now"] is False
    assert report["safe_to_read_synthetic_audio_now"] is False
    assert report["safe_to_write_ignored_asr_artifacts_now"] is False


def test_runner_blocks_packet_with_stdout_redirect_outside_approved_artifacts(tmp_path):
    tool = load_tool_module()
    packet = ready_packet()
    packet["command_previews"][0]["stdout_redirect_path"] = "outputs/private-provider.json"
    calls = []

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=packet,
        approval_record=approval_record(),
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert report["runner_status"] == "blocked_invalid_execution_packet"
    assert "provider stdout_redirect_path for api-review-001 is blocked: outputs" in report["validation_errors"]
    assert report["executed_command_count"] == 0
    assert calls == []


def test_runner_blocks_postprocess_command_reading_forbidden_provider_json(tmp_path):
    tool = load_tool_module()
    packet = ready_packet()
    provider_index = packet["postprocess_command_previews"][0]["transcript_report_argv"].index("--provider-json") + 1
    packet["postprocess_command_previews"][0]["transcript_report_argv"][provider_index] = "outputs/private-provider.json"
    calls = []

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=packet,
        approval_record=approval_record(),
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert report["runner_status"] == "blocked_invalid_execution_packet"
    assert "transcript provider-json for api-review-001 is blocked: outputs" in report["validation_errors"]
    assert report["executed_command_count"] == 0
    assert calls == []


def test_runner_blocks_malformed_command_preview_without_keyerror(tmp_path):
    tool = load_tool_module()
    packet = ready_packet()
    del packet["command_previews"][0]["argv"]
    calls = []

    report = tool.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=packet,
        approval_record=approval_record(),
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )

    assert report["runner_status"] == "blocked_invalid_execution_packet"
    assert "provider command for api-review-001 must include argv" in report["validation_errors"]
    assert report["executed_command_count"] == 0
    assert calls == []
