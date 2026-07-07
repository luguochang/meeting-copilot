import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_readiness.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_synthetic_smoke_readiness",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_required_funasr_cache(cache_root: Path) -> None:
    required = {
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online": [
            "model.pt",
            "config.yaml",
        ]
    }
    for model_id, files in required.items():
        model_dir = cache_root / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        for filename in files:
            (model_dir / filename).write_text("fake", encoding="utf-8")


def test_funasr_synthetic_smoke_readiness_is_local_only_and_path_safe(tmp_path):
    tool = load_tool_module()
    cache_root = tmp_path / "modelscope" / "hub" / "models" / "iic"
    create_required_funasr_cache(cache_root)

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        events_output_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        provider_output_path="artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        smoke_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        model_cache_root=cache_root,
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["report_mode"] == "funasr_synthetic_smoke_readiness"
    assert report["report_version"] == "funasr_synthetic_smoke_readiness.v1"
    assert report["readiness_status"] == "cache_preflight_passed_offline_execution_not_proven"
    assert report["audio_path"] == "artifacts/tmp/synthetic_audio/api-review-001.wav"
    assert report["provider"] == "funasr_streaming"
    assert report["model_alias"] == "paraformer-zh-streaming"
    assert report["required_cached_models_status"] == "present"
    assert report["offline_guard_status"] == "required_before_execution"
    assert report["local_model_dir_label"] == (
        "modelscope_runtime_models_iic/"
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    )
    assert report["model_download_status"] == "not_started"
    assert report["safe_to_execute_local_funasr_now"] is False
    assert report["safe_to_download_models"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["validation_errors"] == []
    assert report["execution_mode"] == "preflight_only_no_execution_authorization"
    assert report["next_action"] == "establish_offline_execution_guard_or_explicit_model_download_approval"
    assert report["command_preview"] == [
        "code/asr_runtime/.venv-funasr/bin/python",
        "code/asr_runtime/scripts/transcribe_funasr.py",
        "artifacts/tmp/synthetic_audio/api-review-001.wav",
        "--streaming",
        "--model",
        "paraformer-zh-streaming",
        "--local-model-dir",
        (
            "<modelscope_runtime_models_iic/"
            "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online>"
        ),
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
        "artifacts/tmp/asr_events/api-review-001.funasr.events.json",
    ]

    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert str(tmp_path) not in report_json
    assert "configs/local" not in report_json


def test_funasr_synthetic_smoke_readiness_blocks_missing_cache(tmp_path):
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        events_output_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        provider_output_path="artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        smoke_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        model_cache_root=tmp_path / "empty-cache",
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["readiness_status"] == "blocked"
    assert report["required_cached_models_status"] == "missing"
    assert "required FunASR cached model files are missing" in report["validation_errors"]
    assert report["safe_to_execute_local_funasr_now"] is False
    assert report["safe_to_download_models"] is False


def test_funasr_synthetic_smoke_readiness_does_not_require_vad_for_streaming_command(tmp_path):
    tool = load_tool_module()
    cache_root = tmp_path / "modelscope" / "models" / "iic"
    create_required_funasr_cache(cache_root)

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        events_output_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        provider_output_path="artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        smoke_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        model_cache_root=cache_root,
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["readiness_status"] == "cache_preflight_passed_offline_execution_not_proven"
    assert [model["model_id"] for model in report["required_cached_models"]] == [
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    ]
    assert report["required_cached_models_status"] == "present"


def test_funasr_synthetic_smoke_readiness_blocks_when_only_legacy_hub_cache_exists(tmp_path):
    tool = load_tool_module()
    runtime_cache_root = tmp_path / "modelscope" / "models" / "iic"
    legacy_hub_cache_root = tmp_path / "modelscope" / "hub" / "models" / "iic"
    create_required_funasr_cache(legacy_hub_cache_root)

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        events_output_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        provider_output_path="artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        smoke_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        model_cache_root=runtime_cache_root,
        legacy_hub_model_cache_root=legacy_hub_cache_root,
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["readiness_status"] == "blocked"
    assert report["required_cached_models_status"] == "missing"
    assert report["legacy_hub_cached_models_status"] == "present"
    assert "required FunASR cached model files are missing" in report["validation_errors"]
    assert report["safe_to_execute_local_funasr_now"] is False
    assert report["safe_to_download_models"] is False


def test_funasr_synthetic_smoke_readiness_rejects_forbidden_paths(tmp_path):
    tool = load_tool_module()
    cache_root = tmp_path / "modelscope" / "hub" / "models" / "iic"
    create_required_funasr_cache(cache_root)

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path="data/asr_eval/local_samples/private.wav",
        events_output_path="outputs/private.events.json",
        provider_output_path="outputs/private.provider.json",
        transcript_report_path="outputs/private.transcript-report.json",
        smoke_report_path="outputs/private.smoke-report.json",
        model_cache_root=cache_root,
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["readiness_status"] == "blocked"
    assert "audio_path is not under approved synthetic audio root" in report["validation_errors"]
    assert "events_output_path is not under approved events root" in report["validation_errors"]
    assert "provider_output_path is not under approved reports root" in report["validation_errors"]
    assert "transcript_report_path is not under approved reports root" in report["validation_errors"]
    assert "smoke_report_path is not under approved reports root" in report["validation_errors"]


def test_funasr_synthetic_smoke_readiness_redacts_invalid_absolute_paths(tmp_path):
    tool = load_tool_module()
    cache_root = tmp_path / "modelscope" / "models" / "iic"
    create_required_funasr_cache(cache_root)
    private_audio_path = "/private/local/meeting.wav"
    private_report_path = "/private/local/report.json"

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path=private_audio_path,
        events_output_path=private_report_path,
        provider_output_path=private_report_path,
        transcript_report_path=private_report_path,
        smoke_report_path=private_report_path,
        model_cache_root=cache_root,
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["readiness_status"] == "blocked"
    assert report["audio_path"] == "<redacted_invalid_path>"
    assert report["events_output_path"] == "<redacted_invalid_path>"
    assert report["provider_output_path"] == "<redacted_invalid_path>"
    assert report["transcript_report_path"] == "<redacted_invalid_path>"
    assert report["smoke_report_path"] == "<redacted_invalid_path>"
    report_json = json.dumps(report, ensure_ascii=False)
    assert private_audio_path not in report_json
    assert private_report_path not in report_json
    assert "/private/local" not in report_json


def test_funasr_synthetic_smoke_readiness_cli_accepts_explicit_model_cache_root_without_leaking_path(
    tmp_path,
    capsys,
):
    tool = load_tool_module()
    cache_root = tmp_path / "approved-user-model-cache" / "iic"
    create_required_funasr_cache(cache_root)

    exit_code = tool.main(
        [
            "--audio-path",
            "artifacts/tmp/synthetic_audio/api-review-001.wav",
            "--events-output-path",
            "artifacts/tmp/asr_events/api-review-001.funasr.events.json",
            "--provider-output-path",
            "artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
            "--transcript-report-path",
            "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
            "--smoke-report-path",
            "artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
            "--model-cache-root",
            str(cache_root),
        ],
        audio_exists=True,
        venv_python_exists=True,
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["readiness_status"] == "cache_preflight_passed_offline_execution_not_proven"
    assert report["required_cached_models_status"] == "present"
    assert report["model_cache_root_input_status"] == "explicit_root_validated_no_path_echo"
    assert report["safe_to_execute_local_funasr_now"] is False
    assert report["safe_to_download_models"] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert str(cache_root) not in report_json
    assert str(tmp_path) not in report_json


def test_funasr_synthetic_smoke_readiness_blocks_forbidden_model_cache_root_before_reading(
    monkeypatch,
):
    tool = load_tool_module()

    def fail_if_model_components_are_read(_model_cache_root):
        raise AssertionError("forbidden model cache root was inspected")

    monkeypatch.setattr(tool, "_cached_model_components", fail_if_model_components_are_read)

    report = tool.build_funasr_synthetic_smoke_readiness_report(
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        events_output_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        provider_output_path="artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        smoke_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json",
        model_cache_root=REPO_ROOT / "configs" / "local" / "funasr-model-cache",
        audio_exists=True,
        venv_python_exists=True,
    )

    assert report["readiness_status"] == "blocked"
    assert report["model_cache_root_input_status"] == "blocked_forbidden_root"
    assert "model_cache_root is blocked: configs/local" in report["validation_errors"]
    assert report["required_cached_models_status"] == "missing"
    assert report["safe_to_download_models"] is False
