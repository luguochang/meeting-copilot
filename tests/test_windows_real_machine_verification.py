import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "windows_real_machine_verification.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "windows_real_machine_verification",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_input(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def valid_windows_payload():
    return {
        "schema_version": "windows_real_machine_observation.v1",
        "host_os": "Windows 11 Pro 24H2",
        "machine_kind": "real_windows_machine",
        "app_package_kind": "tauri_windows_installer_or_portable",
        "checks": {
            "tauri_webview_opened": True,
            "backend_health_ok": True,
            "workbench_loaded": True,
            "provider_health_visible": True,
            "microphone_permission_path_verified": True,
            "realtime_asr_to_suggestions_minutes_history_delete_go": True,
            "file_import_export_go": True,
            "installer_or_portable_launch_smoke_go": True,
            "delete_verified": True,
            "no_secret_values_included": True,
        },
        "privacy_safety": {
            "secret_values_included": False,
            "raw_user_audio_included": False,
            "configs_local_included": False,
        },
    }


def test_windows_real_machine_validator_accepts_complete_real_machine_evidence():
    tool = load_tool_module()
    input_path = write_input(
        REPO_ROOT / "artifacts/tmp/windows_real_machine_inputs/unit-go/observation.json",
        valid_windows_payload(),
    )

    evidence = tool.build_evidence(
        input_path=input_path,
        run_id="unit-go",
    )

    assert evidence["status"] == "go_windows_real_machine_verified"
    assert evidence["windows_real_machine_verified"] is True
    assert evidence["remaining_blockers"] == []
    assert evidence["checks"]["realtime_asr_to_suggestions_minutes_history_delete_go"] is True


def test_windows_real_machine_validator_blocks_missing_required_checks():
    tool = load_tool_module()
    payload = valid_windows_payload()
    payload["checks"]["realtime_asr_to_suggestions_minutes_history_delete_go"] = False
    payload["privacy_safety"]["raw_user_audio_included"] = True
    input_path = write_input(
        REPO_ROOT / "artifacts/tmp/windows_real_machine_inputs/unit-blocked/observation.json",
        payload,
    )

    evidence = tool.build_evidence(
        input_path=input_path,
        run_id="unit-blocked",
    )

    assert evidence["status"] == "blocked_windows_real_machine_verification"
    assert evidence["windows_real_machine_verified"] is False
    assert evidence["remaining_blockers"] == [
        "windows_realtime_mainline_not_verified",
        "windows_input_includes_raw_user_audio",
    ]


def test_windows_real_machine_validator_rejects_input_outside_artifacts_tmp(tmp_path):
    tool = load_tool_module()
    outside = tmp_path / "observation.json"
    outside.write_text(json.dumps(valid_windows_payload()), encoding="utf-8")

    with pytest.raises(ValueError, match="input_path must be under artifacts/tmp"):
        tool.build_evidence(input_path=outside, run_id="unit-outside")
