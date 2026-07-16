#!/usr/bin/env python3
"""Validate packaged Tauri Workbench frontend probe artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBE_ROOT = REPO_ROOT / "artifacts/tmp/desktop_frontend_probe_runtime"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/desktop_tauri_current_run/packaged-webview-runtime-probe-current"

EXPECTED_SELECTORS = (
    "history-list",
    "session-meta",
    "transcript-stream",
    "suggestions-panel",
    "approach-panel",
    "minutes-panel",
    "s-desktop",
)


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def selectors_all_present(payload: dict[str, Any]) -> bool:
    selectors = payload.get("selectors")
    if not isinstance(selectors, dict):
        return False
    return all(selectors.get(selector) is True for selector in EXPECTED_SELECTORS)


def same_chain_blockers(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return ["missing_latest-same-chain.json"]
    blockers: list[str] = []
    if payload.get("packaged_same_chain_probe") is not True:
        blockers.append("same_chain_probe_missing_marker")
    if payload.get("chain_mode") != "no_cost_controlled":
        blockers.append("same_chain_probe_not_no_cost_controlled")
    if payload.get("api_base_url") != "http://127.0.0.1:8765":
        blockers.append("same_chain_probe_api_base_url_invalid")
    if not str(payload.get("session_id") or "").strip():
        blockers.append("same_chain_session_id_missing")
    if payload.get("uses_mock_asr_session") is not True:
        blockers.append("same_chain_mock_asr_session_not_marked")
    if payload.get("uses_deterministic_demo_derivation") is not True:
        blockers.append("same_chain_deterministic_demo_not_marked")

    required_true_fields = {
        "session_created": "same_chain_session_not_created",
        "events_ingested": "same_chain_events_not_ingested",
        "events_visible_in_api": "same_chain_events_not_visible_in_api",
        "events_visible_in_workbench": "same_chain_events_not_visible_in_workbench",
        "same_session_id_observed": "same_chain_session_not_verified",
        "transcript_visible": "same_chain_transcript_not_visible",
        "minutes_visible": "same_chain_minutes_not_visible",
        "history_visible": "same_chain_history_not_visible",
        "delete_verified": "same_chain_delete_not_verified",
        "history_removed_after_delete": "same_chain_history_not_removed_after_delete",
    }
    for field, blocker in required_true_fields.items():
        if payload.get(field) is not True:
            blockers.append(blocker)

    if int(payload.get("suggestion_card_count") or 0) < 1:
        blockers.append("same_chain_suggestion_cards_missing")
    if int(payload.get("approach_card_count") or 0) < 1:
        blockers.append("same_chain_approach_cards_missing")

    required_false_fields = {
        "captures_audio": "same_chain_probe_captures_audio",
        "spawns_process": "same_chain_probe_spawns_process",
        "calls_remote_provider": "same_chain_probe_calls_remote_provider",
        "raw_audio_uploaded": "same_chain_probe_raw_audio_uploaded",
        "remote_asr_called": "same_chain_probe_remote_asr_called",
        "remote_llm_called": "same_chain_probe_remote_llm_called",
        "paid_provider_called": "same_chain_probe_paid_provider_called",
    }
    for field, blocker in required_false_fields.items():
        if payload.get(field) is not False:
            blockers.append(blocker)

    if payload.get("errors"):
        blockers.append("same_chain_probe_reported_errors")
    return blockers


def build_evidence(probe_root: Path, run_id: str) -> dict[str, Any]:
    probe_root = probe_root.resolve()
    page_load = load_json(probe_root / "latest-page-load.json")
    inline_dom = load_json(probe_root / "latest-inline-dom.json")
    workbench_runtime = load_json(probe_root / "latest-workbench-runtime.json")
    backend_api = load_json(probe_root / "latest-backend-api.json")
    same_chain = load_json(probe_root / "latest-same-chain.json")
    blockers: list[str] = []

    if page_load is None:
        blockers.append("missing_latest-page-load.json")
    if inline_dom is None:
        blockers.append("missing_latest-inline-dom.json")
    if workbench_runtime is None:
        blockers.append("missing_latest-workbench-runtime.json")

    page_payload = page_load.get("payload", {}) if isinstance(page_load, dict) else {}
    inline_payload = inline_dom.get("payload", {}) if isinstance(inline_dom, dict) else {}
    runtime_payload = (
        workbench_runtime.get("payload", {}) if isinstance(workbench_runtime, dict) else {}
    )
    backend_api_payload = backend_api.get("payload", {}) if isinstance(backend_api, dict) else {}
    same_chain_payload = same_chain.get("payload", {}) if isinstance(same_chain, dict) else None
    runtime_status = runtime_payload.get("runtime_status", {})

    if page_load is not None and page_payload.get("rust_page_load_probe") is not True:
        blockers.append("page_load_probe_missing_rust_page_load_probe")
    if page_load is not None and not str(page_payload.get("url", "")).startswith("tauri://"):
        blockers.append("page_load_probe_not_tauri_url")
    if inline_dom is not None and inline_payload.get("inline_probe") is not True:
        blockers.append("inline_dom_probe_missing_inline_probe")
    if inline_dom is not None and not selectors_all_present(inline_payload):
        blockers.append("inline_dom_probe_missing_expected_selectors")
    if workbench_runtime is not None and runtime_payload.get("ready_state") != "complete":
        blockers.append("workbench_runtime_probe_not_complete")
    if workbench_runtime is not None and runtime_payload.get("desktop_status_text") != "桌面壳已连接":
        blockers.append("workbench_runtime_probe_desktop_status_not_connected")
    if workbench_runtime is not None and runtime_payload.get("api_base_url") != "http://127.0.0.1:8765":
        blockers.append("workbench_runtime_probe_api_base_url_invalid")
    if workbench_runtime is not None and runtime_status.get("command_status") != "ok":
        blockers.append("workbench_runtime_status_not_ok")
    if workbench_runtime is not None and not selectors_all_present(runtime_payload):
        blockers.append("workbench_runtime_probe_missing_expected_selectors")

    backend_api_ready = (
        isinstance(backend_api_payload, dict)
        and backend_api_payload.get("packaged_api_probe") is True
        and backend_api_payload.get("api_base_url") == "http://127.0.0.1:8765"
        and backend_api_payload.get("health_ok") is True
        and backend_api_payload.get("sessions_loaded") is True
    )
    same_chain_specific_blockers = same_chain_blockers(same_chain_payload)
    same_chain_ready = not same_chain_specific_blockers

    status = "go_packaged_webview_runtime_probe" if not blockers else "no_go_packaged_webview_runtime_probe"
    remaining_blockers = [
        "developer_id_signing_not_done",
        "notarization_not_done",
        "gatekeeper_acceptance_not_done",
        "windows_real_machine_not_verified",
    ]
    if not same_chain_ready:
        remaining_blockers.insert(0, "packaged_same_chain_realtime_meeting_flow_not_verified")
    return {
        "schema_version": "packaged_frontend_probe_evidence.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "blockers": blockers,
        "probe_root": str(probe_root),
        "probe_files": {
            "page_load": str(probe_root / "latest-page-load.json"),
            "inline_dom": str(probe_root / "latest-inline-dom.json"),
            "workbench_runtime": str(probe_root / "latest-workbench-runtime.json"),
            "backend_api": str(probe_root / "latest-backend-api.json"),
            "same_chain": str(probe_root / "latest-same-chain.json"),
            "latest": str(probe_root / "latest.json"),
        },
        "counts_as_packaged_runtime_probe_evidence": not blockers,
        "counts_as_packaged_dom_evidence": not blockers,
        "counts_as_packaged_backend_api_evidence": backend_api_ready,
        "counts_as_packaged_same_chain_no_cost_evidence": same_chain_ready,
        "counts_as_packaged_screenshot_evidence": False,
        "counts_as_packaged_mainline_evidence": (not blockers) and same_chain_ready,
        "counts_as_production_real_llm_evidence": False,
        "counts_as_production_real_mic_evidence": False,
        "counts_as_public_release_package": False,
        "packaged_workbench_loaded": page_payload.get("rust_page_load_probe") is True,
        "packaged_inline_dom_selectors_present": selectors_all_present(inline_payload),
        "packaged_workbench_runtime_connected": runtime_status.get("command_status") == "ok",
        "packaged_backend_api_connected": backend_api_ready,
        "packaged_same_chain_flow_complete": same_chain_ready,
        "packaged_same_chain_session_id": (same_chain_payload or {}).get("session_id") if isinstance(same_chain_payload, dict) else None,
        "packaged_same_chain_suggestion_card_count": int((same_chain_payload or {}).get("suggestion_card_count") or 0) if isinstance(same_chain_payload, dict) else 0,
        "packaged_same_chain_approach_card_count": int((same_chain_payload or {}).get("approach_card_count") or 0) if isinstance(same_chain_payload, dict) else 0,
        "same_chain_blockers": same_chain_specific_blockers,
        "desktop_api_base_url": runtime_payload.get("api_base_url"),
        "desktop_status_text": runtime_payload.get("desktop_status_text"),
        "expected_selectors": list(EXPECTED_SELECTORS),
        "privacy_cost_flags": {
            "captures_audio": False,
            "spawns_process": False,
            "calls_remote_provider": False,
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "configs_local_read": False,
        },
        "remaining_blockers": remaining_blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-root", type=Path, default=DEFAULT_PROBE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default="packaged-webview-runtime-probe-current")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = build_evidence(probe_root=args.probe_root, run_id=args.run_id)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "evidence.json"
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "evidence": str(output_path)}, ensure_ascii=False))
    return 0 if not evidence["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
