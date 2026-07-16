import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "packaged_frontend_probe_evidence.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "packaged_frontend_probe_evidence",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_probe(path: Path, payload: dict):
    path.write_text(
        json.dumps(
            {
                "schema_version": "desktop_frontend_probe.v1",
                "source": "tauri_packaged_webview",
                "payload": payload,
                "captures_audio": False,
                "spawns_process": False,
                "calls_remote_provider": False,
                "writes_local_files": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_packaged_frontend_probe_evidence_requires_workbench_runtime_probe(tmp_path):
    tool = load_tool_module()
    probe_root = tmp_path / "probe"
    probe_root.mkdir()
    write_probe(probe_root / "latest-page-load.json", {"rust_page_load_probe": True, "url": "tauri://localhost/workbench.html"})
    write_probe(probe_root / "latest-inline-dom.json", {"inline_probe": True, "selectors": {selector: True for selector in tool.EXPECTED_SELECTORS}})

    evidence = tool.build_evidence(probe_root=probe_root, run_id="unit")

    assert evidence["status"] == "no_go_packaged_webview_runtime_probe"
    assert "missing_latest-workbench-runtime.json" in evidence["blockers"]
    assert evidence["counts_as_packaged_runtime_probe_evidence"] is False


def test_packaged_frontend_probe_evidence_go_when_page_inline_and_runtime_are_valid(tmp_path):
    tool = load_tool_module()
    probe_root = tmp_path / "probe"
    probe_root.mkdir()
    selectors = {selector: True for selector in tool.EXPECTED_SELECTORS}
    write_probe(probe_root / "latest-page-load.json", {"rust_page_load_probe": True, "url": "tauri://localhost/workbench.html"})
    write_probe(probe_root / "latest-inline-dom.json", {"inline_probe": True, "selectors": selectors})
    write_probe(
        probe_root / "latest-workbench-runtime.json",
        {
            "ready_state": "complete",
            "desktop_status_text": "桌面壳已连接",
            "api_base_url": "http://127.0.0.1:8765",
            "runtime_status": {"command_status": "ok", "desktop_api_base_url": "http://127.0.0.1:8765"},
            "selectors": selectors,
        },
    )
    write_probe(
        probe_root / "latest-backend-api.json",
        {
            "packaged_api_probe": True,
            "api_base_url": "http://127.0.0.1:8765",
            "health_ok": True,
            "sessions_loaded": True,
            "session_count": 1,
        },
    )

    evidence = tool.build_evidence(probe_root=probe_root, run_id="unit")

    assert evidence["status"] == "go_packaged_webview_runtime_probe"
    assert evidence["blockers"] == []
    assert evidence["counts_as_packaged_runtime_probe_evidence"] is True
    assert evidence["counts_as_packaged_dom_evidence"] is True
    assert evidence["counts_as_packaged_backend_api_evidence"] is True
    assert evidence["counts_as_packaged_mainline_evidence"] is False


def _write_valid_runtime_probe_set(tool, probe_root: Path):
    selectors = {selector: True for selector in tool.EXPECTED_SELECTORS}
    write_probe(
        probe_root / "latest-page-load.json",
        {"rust_page_load_probe": True, "url": "tauri://localhost/workbench.html"},
    )
    write_probe(probe_root / "latest-inline-dom.json", {"inline_probe": True, "selectors": selectors})
    write_probe(
        probe_root / "latest-workbench-runtime.json",
        {
            "ready_state": "complete",
            "desktop_status_text": "桌面壳已连接",
            "api_base_url": "http://127.0.0.1:8765",
            "runtime_status": {"command_status": "ok", "desktop_api_base_url": "http://127.0.0.1:8765"},
            "selectors": selectors,
        },
    )
    write_probe(
        probe_root / "latest-backend-api.json",
        {
            "packaged_api_probe": True,
            "api_base_url": "http://127.0.0.1:8765",
            "health_ok": True,
            "sessions_loaded": True,
            "session_count": 1,
        },
    )


def _valid_same_chain_payload(**overrides):
    payload = {
        "packaged_same_chain_probe": True,
        "chain_mode": "no_cost_controlled",
        "api_base_url": "http://127.0.0.1:8765",
        "session_id": "packaged_probe_unit",
        "uses_mock_asr_session": True,
        "uses_deterministic_demo_derivation": True,
        "session_created": True,
        "events_ingested": True,
        "events_visible_in_api": True,
        "events_visible_in_workbench": True,
        "same_session_id_observed": True,
        "transcript_visible": True,
        "suggestion_card_count": 2,
        "approach_card_count": 1,
        "minutes_visible": True,
        "history_visible": True,
        "delete_verified": True,
        "history_removed_after_delete": True,
        "captures_audio": False,
        "spawns_process": False,
        "calls_remote_provider": False,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "remote_llm_called": False,
        "paid_provider_called": False,
        "errors": [],
    }
    payload.update(overrides)
    return payload


def test_packaged_frontend_probe_evidence_marks_missing_same_chain_as_remaining_blocker(tmp_path):
    tool = load_tool_module()
    probe_root = tmp_path / "probe"
    probe_root.mkdir()
    _write_valid_runtime_probe_set(tool, probe_root)

    evidence = tool.build_evidence(probe_root=probe_root, run_id="unit")

    assert evidence["status"] == "go_packaged_webview_runtime_probe"
    assert evidence["counts_as_packaged_same_chain_no_cost_evidence"] is False
    assert evidence["counts_as_packaged_mainline_evidence"] is False
    assert "packaged_same_chain_realtime_meeting_flow_not_verified" in evidence["remaining_blockers"]


def test_packaged_frontend_probe_evidence_accepts_complete_no_cost_same_chain(tmp_path):
    tool = load_tool_module()
    probe_root = tmp_path / "probe"
    probe_root.mkdir()
    _write_valid_runtime_probe_set(tool, probe_root)
    write_probe(probe_root / "latest-same-chain.json", _valid_same_chain_payload())

    evidence = tool.build_evidence(probe_root=probe_root, run_id="unit")

    assert evidence["status"] == "go_packaged_webview_runtime_probe"
    assert evidence["counts_as_packaged_same_chain_no_cost_evidence"] is True
    assert evidence["counts_as_packaged_mainline_evidence"] is True
    assert evidence["packaged_same_chain_flow_complete"] is True
    assert evidence["packaged_same_chain_session_id"] == "packaged_probe_unit"
    assert "packaged_same_chain_realtime_meeting_flow_not_verified" not in evidence["remaining_blockers"]


def test_packaged_frontend_probe_evidence_rejects_remote_or_incomplete_same_chain(tmp_path):
    tool = load_tool_module()
    probe_root = tmp_path / "probe"
    probe_root.mkdir()
    _write_valid_runtime_probe_set(tool, probe_root)
    write_probe(
        probe_root / "latest-same-chain.json",
        _valid_same_chain_payload(remote_llm_called=True, delete_verified=False),
    )

    evidence = tool.build_evidence(probe_root=probe_root, run_id="unit")

    assert evidence["counts_as_packaged_same_chain_no_cost_evidence"] is False
    assert evidence["counts_as_packaged_mainline_evidence"] is False
    assert "same_chain_probe_remote_llm_called" in evidence["same_chain_blockers"]
    assert "same_chain_delete_not_verified" in evidence["same_chain_blockers"]
    assert "packaged_same_chain_realtime_meeting_flow_not_verified" in evidence["remaining_blockers"]
