import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import packaged_tauri_ipc_smoke as tool  # noqa: E402


def valid_probe() -> dict:
    return {
        "schema_version": "desktop_frontend_probe.v1",
        "source": "tauri_packaged_webview",
        "payload": {
            "packaged_ipc_probe": True,
            "runtime_command_status": "ok",
            "runtime_implementation_status": "real",
            "provider_command_status": "ok",
            "microphone_command_status": "ok",
            "microphone_helper_present": True,
            "microphone_captures_audio": False,
            "consent_bypassed": False,
            "errors": [],
        },
    }


def test_load_ipc_probe_requires_packaged_webview_provenance(tmp_path: Path):
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(valid_probe()), encoding="utf-8")

    assert tool.load_ipc_probe(path) == valid_probe()

    invalid = valid_probe()
    invalid["source"] = "direct_backend_http"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="Tauri WebView"):
        tool.load_ipc_probe(path)


def test_probe_checks_require_safe_real_commands_without_capture():
    assert all(tool.probe_checks(valid_probe()).values())

    unsafe = valid_probe()
    unsafe["payload"]["microphone_captures_audio"] = True
    unsafe["payload"]["consent_bypassed"] = True
    checks = tool.probe_checks(unsafe)
    assert checks["microphone_not_started"] is False
    assert checks["consent_not_bypassed"] is False


def test_paths_and_run_ids_are_confined_to_artifacts_tmp(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "artifacts/tmp").mkdir(parents=True)

    assert tool.resolve_output_root(repo, Path("artifacts/tmp/ipc")) == (
        repo / "artifacts/tmp/ipc"
    ).resolve()
    with pytest.raises(ValueError, match="artifacts/tmp"):
        tool.resolve_output_root(repo, tmp_path / "outside")
    with pytest.raises(ValueError, match="unsafe"):
        tool.validate_run_id("../escape")
