import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_execution_approval_record.py"
RUNNER_TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_approved_runner.py"
PACKET_PATH = "artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json"
APPROVAL_TOKEN = "APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY"


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool_module():
    return load_module(TOOL_PATH, "funasr_synthetic_smoke_execution_approval_record")


def load_runner_module():
    return load_module(RUNNER_TOOL_PATH, "funasr_synthetic_smoke_approved_runner")


def ready_packet() -> dict:
    return {
        "packet_mode": "funasr_synthetic_smoke_execution_packet",
        "packet_version": "funasr_synthetic_smoke_execution_packet.v1",
        "packet_status": "ready_for_manual_batch_funasr_synthetic_smoke_run",
        "execution_approval_status": "not_approved_manual_run_only",
        "scenario_count": 5,
        "engineering_scenario_count": 4,
        "negative_control_count": 1,
        "safe_to_run_asr_now": False,
        "safe_to_read_audio_file_now": False,
        "safe_to_write_artifacts_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "command_previews": [{}, {}, {}, {}, {}],
        "postprocess_command_previews": [{}, {}, {}, {}, {}],
    }


def create_model_dir(tmp_path: Path) -> Path:
    model_dir = tmp_path / "modelscope" / "iic" / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.pt").write_text("fake", encoding="utf-8")
    (model_dir / "config.yaml").write_text("fake", encoding="utf-8")
    return model_dir


def test_default_approval_record_template_is_not_confirmed_and_not_executable():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_execution_approval_record_report(
        execution_packet=ready_packet(),
        approved_packet_path=PACKET_PATH,
    )

    assert report["approval_record_builder_id"] == "DRV-049"
    assert report["approval_template_status"] == "approval_record_template_ready_not_confirmed"
    assert report["safe_to_run_asr_now"] is False
    assert report["safe_to_read_synthetic_audio_now"] is False
    assert report["safe_to_write_ignored_asr_artifacts_now"] is False
    record = report["approval_record_template"]
    assert record["approval_record_version"] == "funasr_synthetic_smoke_execution_approval.v1"
    assert record["approval_scope"] == "local_funasr_synthetic_smoke_5_scenarios_only"
    assert record["approval_token"] == APPROVAL_TOKEN
    assert record["approval_confirmed_by_user"] is False
    assert record["approved_packet_path"] == PACKET_PATH
    assert record["approved_scenario_count"] == 5


def test_confirmed_approval_record_is_accepted_by_runner_with_fake_execution(tmp_path):
    tool = load_tool_module()
    runner = load_runner_module()
    calls = []
    approval_report = tool.build_funasr_synthetic_smoke_execution_approval_record_report(
        execution_packet=ready_packet(),
        approved_packet_path=PACKET_PATH,
        confirm=True,
        approval_id="funasr-smoke-confirmed-test",
    )
    packet = runner_packet()

    report = runner.build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet=packet,
        approval_record=approval_report["approval_record_template"],
        execute=True,
        local_model_dir=create_model_dir(tmp_path),
        run_command=lambda _argv, stdout_path=None: calls.append((list(_argv), stdout_path))
        or {"returncode": 0, "stderr": "", "stdout_path": stdout_path},
    )

    assert approval_report["approval_template_status"] == "approval_record_confirmed_for_local_synthetic_smoke"
    assert report["runner_status"] == "executed_local_funasr_synthetic_smoke_commands"
    assert report["executed_command_count"] == 15
    assert len(calls) == 15


def runner_packet() -> dict:
    scenarios = [
        ("api-review-001", "api-review"),
        ("architecture-review-001", "architecture-review"),
        ("incident-review-001", "incident-review"),
        ("release-review-001", "release-review"),
        ("non-engineering-control-001", "non-engineering-control"),
    ]
    command_previews = []
    postprocess_command_previews = []
    for scenario_id, script_stem in scenarios:
        audio_path = f"artifacts/tmp/synthetic_audio/{scenario_id}.wav"
        events_path = f"artifacts/tmp/asr_events/{scenario_id}.funasr.events.json"
        provider_path = f"artifacts/tmp/asr_reports/{scenario_id}.funasr.provider.json"
        transcript_path = f"artifacts/tmp/asr_reports/{scenario_id}.funasr.transcript-report.json"
        smoke_path = f"artifacts/tmp/asr_reports/{scenario_id}.funasr.smoke-report.json"
        scenario_kind = "negative_control" if scenario_id.startswith("non-engineering") else "engineering"
        command_previews.append(
            {
                "scenario_id": scenario_id,
                "scenario_kind": scenario_kind,
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
                "scenario_kind": scenario_kind,
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
    return {
        "decision_id": "DRV-045",
        "packet_mode": "funasr_synthetic_smoke_execution_packet",
        "packet_version": "funasr_synthetic_smoke_execution_packet.v1",
        "packet_status": "ready_for_manual_batch_funasr_synthetic_smoke_run",
        "execution_approval_status": "not_approved_manual_run_only",
        "scenario_count": 5,
        "engineering_scenario_count": 4,
        "negative_control_count": 1,
        "command_previews": command_previews,
        "postprocess_command_previews": postprocess_command_previews,
        "safe_to_run_asr_now": False,
        "safe_to_read_audio_file_now": False,
        "safe_to_write_artifacts_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
    }


def test_approval_record_builder_blocks_forbidden_packet_path_before_read(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("packet path was read before guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_funasr_synthetic_smoke_execution_approval_record_report(
        execution_packet_path="configs/local/funasr.packet.json",
    )

    assert report["approval_template_status"] == "blocked_by_packet_path_guard"
    assert "execution_packet_path is blocked: configs/local" in report["validation_errors"]
    assert report["safe_to_read_configs_local_now"] is False
