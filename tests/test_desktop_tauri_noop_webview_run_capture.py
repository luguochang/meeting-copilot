import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "desktop_tauri_noop_webview_run_capture_app.py"

EXPECTED_COMMANDS = [
    ("runtime.get_status", "runtime_get_status"),
    ("session.prepare", "session_prepare"),
    ("asr_worker.health", "asr_worker_health"),
    ("mic_adapter.prepare", "mic_adapter_prepare"),
    ("mic_adapter.status", "mic_adapter_status"),
    ("mic_adapter.start", "mic_adapter_start"),
    ("mic_adapter.pause", "mic_adapter_pause"),
    ("mic_adapter.resume", "mic_adapter_resume"),
    ("mic_adapter.stop", "mic_adapter_stop"),
    ("mic_adapter.delete_audio_chunks", "mic_adapter_delete_audio_chunks"),
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_tauri_noop_webview_run_capture_app",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_tauri_noop_run_result(run_id: str = "pcweb-119-test-run") -> dict[str, object]:
    return {
        "run_result_version": "desktop_tauri_noop_run_result.v1",
        "run_id": run_id,
        "run_environment": "tauri_webview",
        "explicit_tauri_run_approval_recorded": True,
        "web_app_url_status": "local_dev_url_loaded",
        "ipc_transport_status": "tauri_ipc_available",
        "command_results": [
            {
                "command_id": command_id,
                "command_name": command_name,
                "invoke_status": "returned",
                "result": {
                    "command_id": command_id,
                    "command_status": "noop_bound",
                    "implementation_status": "noop_only",
                    "transport_status": "tauri_ipc_bound",
                    "side_effect_status": "none",
                    "safe_to_invoke_noop": True,
                    "safe_to_execute_real_action": False,
                    "captures_audio": False,
                    "spawns_process": False,
                    "calls_remote_provider": False,
                    "writes_local_files": False,
                },
            }
            for command_id, command_name in EXPECTED_COMMANDS
        ],
    }


def test_capture_app_writes_valid_tauri_validation_evidence_to_ignored_root(tmp_path):
    tool = load_tool_module()
    app = tool.create_capture_app(
        output_root=tmp_path,
        allow_non_repo_output_root_for_tests=True,
    )

    client = TestClient(app)
    response = client.post(
        "/desktop/tauri-noop-run-results/validations",
        json={"run_result": valid_tauri_noop_run_result()},
    )

    assert response.status_code == 200
    evidence_files = sorted(tmp_path.glob("*.pcweb-119-tauri-noop-run-validation.json"))
    assert len(evidence_files) == 1

    evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    assert evidence["capture_version"] == "desktop_tauri_noop_webview_run_capture.v1"
    assert evidence["capture_status"] == "captured_validated_tauri_noop_run"
    assert evidence["source_endpoint"] == "/desktop/tauri-noop-run-results/validations"
    assert evidence["run_result"]["run_environment"] == "tauri_webview"
    assert evidence["validation_report"]["result_validation_status"] == "passed"
    assert evidence["validation_report"]["real_tauri_noop_run_evidence_status"] == (
        "ready_for_worker_mic_source_approval_review"
    )
    assert evidence["validated_command_count"] == 10
    assert evidence["returned_command_count"] == 10
    assert evidence["safe_to_capture_audio_now"] is False
    assert evidence["safe_to_start_asr_worker_now"] is False
    assert evidence["safe_to_call_remote_asr_now"] is False
    assert evidence["safe_to_call_llm_now"] is False


def test_capture_app_does_not_write_invalid_or_browser_fallback_result(tmp_path):
    tool = load_tool_module()
    app = tool.create_capture_app(
        output_root=tmp_path,
        allow_non_repo_output_root_for_tests=True,
    )
    browser_fallback = valid_tauri_noop_run_result()
    browser_fallback["run_environment"] = "browser_fallback"
    browser_fallback["explicit_tauri_run_approval_recorded"] = False

    client = TestClient(app)
    response = client.post(
        "/desktop/tauri-noop-run-results/validations",
        json={"run_result": browser_fallback},
    )

    assert response.status_code == 422
    assert list(tmp_path.glob("*.pcweb-119-tauri-noop-run-validation.json")) == []


def test_capture_app_rejects_forbidden_output_root_before_serving():
    tool = load_tool_module()

    report = tool.validate_output_root(REPO_ROOT / "configs" / "local")

    assert "path is blocked: configs/local" in report
