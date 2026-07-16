#!/usr/bin/env python3
"""Create a traceable P0 mainline evidence bundle.

The first lane is intentionally API-only: uploaded audio -> ASR live session ->
LLM cards/approach/minutes -> delete. Browser and real-mic lanes are added as
separate evidence dimensions so this runner never claims more than it proves.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"

for import_root in (WEB_BACKEND_ROOT, CORE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from fastapi.testclient import TestClient  # noqa: E402
from meeting_copilot_web_mvp.app import create_app  # noqa: E402
from meeting_copilot_web_mvp import llm_service  # noqa: E402
from meeting_copilot_web_mvp.mic_capture import pcm_chunks_from_wav  # noqa: E402


DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "acceptance"
DEFAULT_AUDIO_PATH = REPO_ROOT / "code" / "asr_runtime" / "outputs" / "simulated-release-review.16k.wav"
BROWSER_LIVE_MIC_MIN_TRANSCRIPT_CHARS = 30
UiVerifier = Callable[..., dict[str, Any]]


def run_file_lane_bundle(
    *,
    audio_path: Path,
    artifact_root: Path,
    data_dir: Path,
    run_id: str | None = None,
    ui_verifier: UiVerifier | None = None,
) -> dict[str, Any]:
    run_id = run_id or _default_run_id()
    artifact_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    started_at = _now()

    manifest = _base_manifest(
        run_id=run_id,
        started_at=started_at,
        artifact_root=artifact_root,
        audio_source="uploaded_wav",
        ui_coverage="API_only",
    )
    manifest["input_audio_path_kind"] = "fixture" if _is_inside(audio_path, REPO_ROOT) else "user_authorized_runtime_artifact"

    if not audio_path.exists():
        manifest["degradation_reasons"].append("input_audio_missing")
        manifest["verdict"] = "no_go"
        manifest["ended_at"] = _now()
        _write_bundle_minimum(artifact_root, manifest)
        return {"artifact_root": str(artifact_root), "manifest": manifest}

    manifest["input_audio_sha256"] = _sha256(audio_path)
    (artifact_root / "input_audio.sha256").write_text(
        f"{manifest['input_audio_sha256']}  {audio_path.name}\n",
        encoding="utf-8",
    )

    client = TestClient(create_app(data_dir=data_dir))
    llm_config = llm_service.LlmConfig.from_env()
    manifest["llm_provider"] = _llm_provider_mode(llm_config)
    manifest["llm_provider_metadata"] = llm_service.provider_metadata(llm_config)

    try:
        upload_response = _upload_audio(client, audio_path)
        _write_json(artifact_root / "upload_response.json", upload_response)
        session_id = str(upload_response["session_id"])
        manifest["session_id"] = session_id
        manifest["asr_provider"] = str(upload_response.get("provider", ""))
        manifest["asr_provider_mode"] = "real" if manifest["asr_provider"] != "local_mock_asr" else "mock"
        manifest["asr_fallback_used"] = False
        manifest["transcript_char_count"] = len(str(upload_response.get("transcript") or ""))
        (artifact_root / "transcript.raw.txt").write_text(str(upload_response.get("raw_transcript") or ""), encoding="utf-8")
        (artifact_root / "transcript.normalized.txt").write_text(str(upload_response.get("transcript") or ""), encoding="utf-8")

        session_events = _get_json(client, f"/live/asr/sessions/{session_id}/events")
        _write_json(artifact_root / "session_events.json", session_events)
        _apply_asr_semantic_quality_from_session_events(manifest, session_events)
        final_events = [
            event for event in session_events.get("events", [])
            if event.get("event_type") == "transcript_final"
        ]
        manifest["final_segment_count"] = len(final_events)

        _run_downstream_bundle_steps(
            client=client,
            session_id=session_id,
            artifact_root=artifact_root,
            manifest=manifest,
            ui_verifier=ui_verifier,
            data_dir=data_dir,
        )

        delete_response = _delete_json(client, f"/live/asr/sessions/{session_id}")
        _write_json(artifact_root / "delete_response.json", delete_response)
        manifest["delete_verified"] = _get_status(client, f"/live/asr/sessions/{session_id}/events") == 404
    except Exception as exc:
        manifest["degradation_reasons"].append(f"runner_error:{type(exc).__name__}:{_safe_error(exc)}")

    _apply_go_no_go(manifest)
    manifest["ended_at"] = _now()
    _write_bundle_minimum(artifact_root, manifest)
    return {"artifact_root": str(artifact_root), "manifest": manifest}


def run_simulated_realtime_lane_bundle(
    *,
    audio_path: Path,
    artifact_root: Path,
    data_dir: Path,
    run_id: str | None = None,
    ui_verifier: UiVerifier | None = None,
) -> dict[str, Any]:
    """Stream a WAV through the realtime WebSocket path and bundle evidence.

    This no-mic lane proves the realtime protocol and downstream Copilot flow.
    It is explicitly not real microphone evidence.
    """
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-simulated-realtime")
    session_id = run_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    manifest = _base_manifest(
        run_id=run_id,
        started_at=_now(),
        artifact_root=artifact_root,
        audio_source="simulated_realtime_wav",
        ui_coverage="API_only",
    )
    manifest["session_id"] = session_id
    manifest["websocket_path"] = f"/live/asr/stream/ws/{session_id}?audio_source=simulated_realtime_wav"
    manifest["input_audio_path_kind"] = "fixture" if _is_inside(audio_path, REPO_ROOT) else "user_authorized_runtime_artifact"
    manifest["counts_as_real_mic_go_evidence"] = False

    if not audio_path.exists():
        manifest["degradation_reasons"].append("input_audio_missing")
        manifest["verdict"] = "no_go"
        manifest["ended_at"] = _now()
        _write_bundle_minimum(artifact_root, manifest)
        return {"artifact_root": str(artifact_root), "manifest": manifest}

    manifest["input_audio_sha256"] = _sha256(audio_path)
    (artifact_root / "input_audio.sha256").write_text(
        f"{manifest['input_audio_sha256']}  {audio_path.name}\n",
        encoding="utf-8",
    )

    client = TestClient(create_app(data_dir=data_dir))
    llm_config = llm_service.LlmConfig.from_env()
    manifest["llm_provider"] = _llm_provider_mode(llm_config)
    manifest["llm_provider_metadata"] = llm_service.provider_metadata(llm_config)

    try:
        ws_events = _stream_wav_over_testclient_ws(
            client,
            session_id=session_id,
            audio_path=audio_path,
        )
        _write_json(artifact_root / "ws_events.json", ws_events)
        provider_error = next(
            (event for event in ws_events if event.get("event_type") == "provider_error"),
            None,
        )
        if provider_error is not None:
            manifest["asr_provider"] = str(provider_error.get("provider") or "not_started")
            manifest["asr_provider_mode"] = str(provider_error.get("provider_mode") or "unknown")
            manifest["asr_fallback_used"] = bool(provider_error.get("asr_fallback_used", True))
            for reason in list(provider_error.get("degradation_reasons") or []):
                if reason not in manifest["degradation_reasons"]:
                    manifest["degradation_reasons"].append(str(reason))
        else:
            session_events = _get_json(client, f"/live/asr/sessions/{session_id}/events")
            _write_json(artifact_root / "session_events.json", session_events)
            _apply_asr_semantic_quality_from_session_events(manifest, session_events)
            manifest["asr_provider"] = str(session_events.get("provider", ""))
            manifest["asr_provider_mode"] = str(session_events.get("provider_mode") or "unknown")
            manifest["asr_fallback_used"] = bool(session_events.get("asr_fallback_used", False))
            final_events = [
                event for event in session_events.get("events", [])
                if event.get("event_type") == "transcript_final"
            ]
            manifest["final_segment_count"] = len(final_events)
            transcript = " ".join(_event_text(event) for event in final_events)
            manifest["transcript_char_count"] = len(transcript)
            (artifact_root / "transcript.normalized.txt").write_text(transcript, encoding="utf-8")

            history_before_delete = _get_json(client, "/live/asr/sessions")
            _write_json(artifact_root / "sessions_list_before_delete.json", history_before_delete)

            _run_downstream_bundle_steps(
                client=client,
                session_id=session_id,
                artifact_root=artifact_root,
                manifest=manifest,
                ui_verifier=ui_verifier,
                data_dir=data_dir,
            )

            delete_response = _delete_json(client, f"/live/asr/sessions/{session_id}")
            _write_json(artifact_root / "delete_response.json", delete_response)
            manifest["delete_verified"] = _get_status(client, f"/live/asr/sessions/{session_id}/events") == 404
            history_after_delete = _get_json(client, "/live/asr/sessions")
            _write_json(artifact_root / "sessions_list_after_delete.json", history_after_delete)
    except Exception as exc:
        manifest["degradation_reasons"].append(f"runner_error:{type(exc).__name__}:{_safe_error(exc)}")

    _apply_go_no_go(manifest)
    manifest["ended_at"] = _now()
    _write_bundle_minimum(artifact_root, manifest)
    return {"artifact_root": str(artifact_root), "manifest": manifest}


def run_real_mic_recorded_realtime_lane_bundle(
    *,
    audio_path: Path,
    health_report: dict[str, Any],
    artifact_root: Path,
    data_dir: Path,
    run_id: str | None = None,
    ui_verifier: UiVerifier | None = None,
) -> dict[str, Any]:
    """Stream a real microphone recording through the realtime WebSocket path.

    This lane proves a real microphone capture artifact can feed the same
    realtime ASR and Copilot business flow. It is still distinct from a browser
    live getUserMedia run, so the manifest records that boundary explicitly.
    """
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-real-mic-recorded")
    session_id = run_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    manifest = _base_manifest(
        run_id=run_id,
        started_at=_now(),
        artifact_root=artifact_root,
        audio_source="real_mic_recorded_wav",
        ui_coverage="API_only",
    )
    manifest["session_id"] = session_id
    manifest["websocket_path"] = f"/live/asr/stream/ws/{session_id}?audio_source=real_mic_recorded_wav"
    manifest["input_audio_path_kind"] = "user_authorized_runtime_artifact"
    manifest["counts_as_real_mic_go_evidence"] = False
    manifest["browser_live_mic_go_evidence"] = False
    _apply_real_mic_health_to_manifest(manifest, health_report)
    _write_json(artifact_root / "real_mic_health_report.json", health_report)

    if not audio_path.exists():
        manifest["degradation_reasons"].append("input_audio_missing")
        manifest["verdict"] = "no_go"
        manifest["ended_at"] = _now()
        _write_bundle_minimum(artifact_root, manifest)
        return {"artifact_root": str(artifact_root), "manifest": manifest}

    manifest["input_audio_sha256"] = _sha256(audio_path)
    (artifact_root / "input_audio.sha256").write_text(
        f"{manifest['input_audio_sha256']}  {audio_path.name}\n",
        encoding="utf-8",
    )

    health_status = manifest["real_mic_health_status"]
    if health_status != "audio_capture_health_passed":
        manifest["degradation_reasons"].append(_real_mic_health_blocker(health_status))
        manifest["ended_at"] = _now()
        _apply_go_no_go(manifest)
        manifest["counts_as_real_mic_go_evidence"] = False
        _write_bundle_minimum(artifact_root, manifest)
        return {"artifact_root": str(artifact_root), "manifest": manifest}

    client = TestClient(create_app(data_dir=data_dir))
    llm_config = llm_service.LlmConfig.from_env()
    manifest["llm_provider"] = _llm_provider_mode(llm_config)
    manifest["llm_provider_metadata"] = llm_service.provider_metadata(llm_config)

    try:
        ws_events = _stream_wav_over_testclient_ws(
            client,
            session_id=session_id,
            audio_path=audio_path,
            audio_source="real_mic_recorded_wav",
        )
        _write_json(artifact_root / "ws_events.json", ws_events)
        provider_error = next(
            (event for event in ws_events if event.get("event_type") == "provider_error"),
            None,
        )
        if provider_error is not None:
            manifest["asr_provider"] = str(provider_error.get("provider") or "not_started")
            manifest["asr_provider_mode"] = str(provider_error.get("provider_mode") or "unknown")
            manifest["asr_fallback_used"] = bool(provider_error.get("asr_fallback_used", True))
            for reason in list(provider_error.get("degradation_reasons") or []):
                if reason not in manifest["degradation_reasons"]:
                    manifest["degradation_reasons"].append(str(reason))
        else:
            session_events = _get_json(client, f"/live/asr/sessions/{session_id}/events")
            _write_json(artifact_root / "session_events.json", session_events)
            _apply_asr_semantic_quality_from_session_events(manifest, session_events)
            manifest["asr_provider"] = str(session_events.get("provider", ""))
            manifest["asr_provider_mode"] = str(session_events.get("provider_mode") or "unknown")
            manifest["asr_fallback_used"] = bool(session_events.get("asr_fallback_used", False))
            final_events = [
                event for event in session_events.get("events", [])
                if event.get("event_type") == "transcript_final"
            ]
            manifest["final_segment_count"] = len(final_events)
            transcript = " ".join(_event_text(event) for event in final_events)
            manifest["transcript_char_count"] = len(transcript)
            (artifact_root / "transcript.normalized.txt").write_text(transcript, encoding="utf-8")

            history_before_delete = _get_json(client, "/live/asr/sessions")
            _write_json(artifact_root / "sessions_list_before_delete.json", history_before_delete)

            _run_downstream_bundle_steps(
                client=client,
                session_id=session_id,
                artifact_root=artifact_root,
                manifest=manifest,
                ui_verifier=ui_verifier,
                data_dir=data_dir,
            )

            delete_response = _delete_json(client, f"/live/asr/sessions/{session_id}")
            _write_json(artifact_root / "delete_response.json", delete_response)
            manifest["delete_verified"] = _get_status(client, f"/live/asr/sessions/{session_id}/events") == 404
            history_after_delete = _get_json(client, "/live/asr/sessions")
            _write_json(artifact_root / "sessions_list_after_delete.json", history_after_delete)
    except Exception as exc:
        manifest["degradation_reasons"].append(f"runner_error:{type(exc).__name__}:{_safe_error(exc)}")

    _apply_go_no_go(manifest)
    manifest["counts_as_real_mic_go_evidence"] = manifest["verdict"] == "go"
    manifest["ended_at"] = _now()
    _write_bundle_minimum(artifact_root, manifest)
    return {"artifact_root": str(artifact_root), "manifest": manifest}


def run_real_mic_lane_bundle(
    *,
    health_report: dict[str, Any],
    artifact_root: Path,
    run_id: str | None = None,
    asr_probe: dict[str, Any] | None = None,
    ui_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a traceable real-mic lane bundle without overstating success.

    This lane can represent both a successful real-mic run and a degraded run
    such as silent microphone input. It intentionally reuses the same P0
    manifest shape as the file lane so Go/No-Go gates stay comparable.
    """
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-real-mic")
    artifact_root.mkdir(parents=True, exist_ok=True)
    manifest = _base_manifest(
        run_id=run_id,
        started_at=_now(),
        artifact_root=artifact_root,
        audio_source="real_mic",
        ui_coverage="not_verified",
    )
    manifest["input_audio_path_kind"] = "user_authorized_runtime_artifact"
    _apply_real_mic_health_to_manifest(manifest, health_report)
    _write_json(artifact_root / "real_mic_health_report.json", health_report)

    health_status = manifest["real_mic_health_status"]
    if health_status != "audio_capture_health_passed":
        manifest["degradation_reasons"].append(_real_mic_health_blocker(health_status))

    if asr_probe is not None:
        _write_json(artifact_root / "asr_probe.json", asr_probe)
        if isinstance(asr_probe.get("asr_semantic_quality"), dict):
            _apply_asr_semantic_quality(manifest, asr_probe.get("asr_semantic_quality"))
        manifest["session_id"] = str(asr_probe.get("session_id") or "")
        manifest["asr_provider"] = str(asr_probe.get("provider") or "not_started")
        manifest["asr_provider_mode"] = str(asr_probe.get("provider_mode") or "unknown")
        manifest["asr_fallback_used"] = bool(asr_probe.get("asr_fallback_used", False))
        for reason in list(asr_probe.get("degradation_reasons") or []):
            if reason not in manifest["degradation_reasons"]:
                manifest["degradation_reasons"].append(str(reason))
        final_events = [
            event for event in list(asr_probe.get("events") or [])
            if event.get("event_type") in {"transcript_final", "final"}
            and _event_text(event)
        ]
        manifest["final_segment_count"] = len(final_events)
        transcript = " ".join(_event_text(event) for event in final_events)
        manifest["transcript_char_count"] = len(transcript)
    else:
        manifest["degradation_reasons"].append("asr_probe_missing")

    if ui_report is not None:
        _write_json(artifact_root / "ui_verification.json", ui_report)
        _apply_ui_report(manifest, ui_report)

    _apply_go_no_go(manifest)
    manifest["ended_at"] = _now()
    _write_bundle_minimum(artifact_root, manifest)
    return {"artifact_root": str(artifact_root), "manifest": manifest}


def run_browser_live_mic_lane_bundle(
    *,
    browser_mic_health: dict[str, Any],
    artifact_root: Path,
    run_id: str | None = None,
    asr_probe: dict[str, Any] | None = None,
    ui_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bundle Workbench getUserMedia evidence without overstating success.

    This lane represents the browser live microphone path only. It consumes
    browser-side mic health plus same-session ASR/UI/delete evidence; it does
    not accept a WAV replay as a substitute for getUserMedia.
    """
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-browser-live-mic")
    artifact_root.mkdir(parents=True, exist_ok=True)
    manifest = _base_manifest(
        run_id=run_id,
        started_at=_now(),
        artifact_root=artifact_root,
        audio_source="browser_live_mic",
        ui_coverage="not_verified",
    )
    manifest["input_audio_path_kind"] = "browser_get_user_media"
    manifest["session_id"] = str(browser_mic_health.get("session_id") or "")
    manifest["counts_as_real_mic_go_evidence"] = False
    manifest["browser_live_mic_go_evidence"] = False
    _apply_browser_mic_health_to_manifest(manifest, browser_mic_health)
    _write_json(artifact_root / "browser_mic_health_report.json", browser_mic_health)

    health_status = manifest["browser_mic_health_status"]
    if health_status != "audio_capture_health_passed":
        manifest["degradation_reasons"].append(_browser_mic_health_blocker(health_status))

    if asr_probe is not None:
        _write_json(artifact_root / "asr_probe.json", asr_probe)
        if isinstance(asr_probe.get("asr_semantic_quality"), dict):
            _apply_asr_semantic_quality(manifest, asr_probe.get("asr_semantic_quality"))
        probe_session_id = str(asr_probe.get("session_id") or "")
        if probe_session_id:
            if manifest["session_id"] and probe_session_id != manifest["session_id"]:
                manifest["degradation_reasons"].append("browser_mic_session_mismatch")
            manifest["session_id"] = probe_session_id
        manifest["asr_provider"] = str(asr_probe.get("provider") or "not_started")
        manifest["asr_provider_mode"] = str(asr_probe.get("provider_mode") or "unknown")
        manifest["asr_fallback_used"] = bool(asr_probe.get("asr_fallback_used", False))
        for reason in list(asr_probe.get("degradation_reasons") or []):
            if reason not in manifest["degradation_reasons"]:
                manifest["degradation_reasons"].append(str(reason))
        final_events = [
            event for event in list(asr_probe.get("events") or [])
            if event.get("event_type") in {"transcript_final", "final"}
            and _event_text(event)
        ]
        manifest["final_segment_count"] = len(final_events)
        transcript = " ".join(_event_text(event) for event in final_events)
        manifest["transcript_char_count"] = len(transcript)
        if "llm_called" in asr_probe:
            manifest["llm_called"] = bool(asr_probe.get("llm_called"))
            manifest["llm_provider"] = str(asr_probe.get("llm_provider") or "real_gateway")
        manifest["derivation_mode"] = str(asr_probe.get("derivation_mode") or manifest.get("derivation_mode") or "unknown")
        manifest["gateway_base_url_kind"] = str(
            asr_probe.get("gateway_base_url_kind") or manifest.get("gateway_base_url_kind") or "unknown"
        )
        manifest["counts_as_production_llm_evidence"] = bool(
            asr_probe.get("counts_as_production_llm_evidence", False)
        )
        manifest["llm_call_count"] = int(asr_probe.get("llm_call_count") or manifest.get("llm_call_count") or 0)
        manifest["llm_usage_total_tokens"] = int(
            asr_probe.get("llm_usage_total_tokens") or manifest.get("llm_usage_total_tokens") or 0
        )
        manifest["suggestion_card_count"] = int(asr_probe.get("suggestion_card_count") or 0)
        manifest["approach_card_count"] = int(asr_probe.get("approach_card_count") or 0)
        manifest["minutes_char_count"] = int(asr_probe.get("minutes_char_count") or 0)
        manifest["all_cards_have_evidence"] = bool(asr_probe.get("all_cards_have_evidence", False))
        manifest["delete_verified"] = bool(asr_probe.get("delete_verified", False))
    else:
        manifest["degradation_reasons"].append("asr_probe_missing")

    if ui_report is not None:
        _write_json(artifact_root / "ui_verification.json", ui_report)
        _apply_ui_report(manifest, ui_report)

    if isinstance(manifest.get("privacy_cost_flags"), dict):
        manifest["privacy_cost_flags"]["llm_called"] = bool(manifest.get("llm_called"))

    _apply_browser_live_mic_production_llm_gate(manifest)
    _apply_go_no_go(manifest)
    if (
        manifest["audio_source"] == "browser_live_mic"
        and int(manifest.get("transcript_char_count") or 0) < BROWSER_LIVE_MIC_MIN_TRANSCRIPT_CHARS
    ):
        if "browser_live_mic_transcript_too_short" not in manifest["degradation_reasons"]:
            manifest["degradation_reasons"].append("browser_live_mic_transcript_too_short")
        manifest["verdict"] = "no_go"
    if manifest["audio_source"] == "browser_live_mic" and manifest["verdict"] != "go":
        if "browser_live_mic_not_proven" not in manifest["degradation_reasons"]:
            manifest["degradation_reasons"].append("browser_live_mic_not_proven")
        manifest["verdict"] = "no_go"
    manifest["browser_live_mic_go_evidence"] = manifest["verdict"] == "go"
    manifest["counts_as_real_mic_go_evidence"] = manifest["browser_live_mic_go_evidence"]
    manifest["ended_at"] = _now()
    _write_bundle_minimum(artifact_root, manifest)
    return {"artifact_root": str(artifact_root), "manifest": manifest}


def _apply_browser_live_mic_production_llm_gate(manifest: dict[str, Any]) -> None:
    if manifest.get("audio_source") != "browser_live_mic":
        return

    reasons = list(manifest.get("degradation_reasons") or [])

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    llm_called = manifest.get("llm_called") is True
    call_count = int(manifest.get("llm_call_count") or 0)
    usage_total = int(manifest.get("llm_usage_total_tokens") or 0)
    if llm_called and (call_count <= 0 or usage_total <= 0):
        add("browser_live_mic_llm_usage_evidence_missing")

    production_requested = manifest.get("derivation_mode") == "production_enabled"
    production_evidence_complete = (
        manifest.get("counts_as_production_llm_evidence") is True
        and llm_called
        and manifest.get("llm_provider") == "real_gateway"
        and manifest.get("gateway_base_url_kind") == "remote"
        and call_count > 0
        and usage_total > 0
    )
    if production_requested and not production_evidence_complete:
        add("browser_live_mic_production_llm_evidence_missing")

    manifest["degradation_reasons"] = reasons


def _apply_real_mic_health_to_manifest(manifest: dict[str, Any], health_report: dict[str, Any]) -> None:
    manifest["real_mic_health_status"] = str(health_report.get("health_status", "unknown"))
    manifest["real_mic_duration_seconds"] = float(health_report.get("duration_seconds") or 0.0)
    manifest["real_mic_rms"] = float(health_report.get("rms") or 0.0)
    manifest["real_mic_peak"] = float(health_report.get("peak") or 0.0)
    manifest["real_mic_active_sample_ratio"] = float(health_report.get("active_sample_ratio") or 0.0)
    manifest["real_mic_silence_ratio"] = float(health_report.get("silence_ratio") or 0.0)
    manifest["privacy_cost_flags"] = dict(health_report.get("privacy_cost_flags") or {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    })


def _apply_browser_mic_health_to_manifest(manifest: dict[str, Any], health_report: dict[str, Any]) -> None:
    manifest["browser_mic_health_status"] = str(health_report.get("health_status", "unknown"))
    manifest["browser_mic_sample_count"] = int(health_report.get("sample_count") or 0)
    manifest["browser_mic_chunk_count"] = int(health_report.get("chunk_count") or 0)
    manifest["browser_mic_rms"] = float(health_report.get("rms") or 0.0)
    manifest["browser_mic_peak"] = float(health_report.get("peak") or 0.0)
    manifest["browser_mic_active_sample_ratio"] = float(health_report.get("active_sample_ratio") or 0.0)
    manifest["privacy_cost_flags"] = {
        "raw_audio_uploaded": bool(health_report.get("raw_audio_uploaded", False)),
        "remote_asr_called": bool(health_report.get("remote_asr_called", False)),
        "llm_called": bool(health_report.get("llm_called", False)),
        "configs_local_read": bool(health_report.get("configs_local_read", False)),
        "user_audio_committed_to_repo": bool(health_report.get("user_audio_committed_to_repo", False)),
    }


def _browser_mic_health_blocker(health_status: str) -> str:
    mapping = {
        "blocked_audio_too_quiet": "browser_mic_audio_too_quiet",
        "blocked_no_audio_samples": "browser_mic_no_audio_samples",
        "blocked_no_clear_speech": "browser_mic_no_clear_speech",
        "blocked_audio_too_short": "browser_mic_audio_too_short",
    }
    return mapping.get(health_status, f"browser_mic_health_{health_status}")


def _base_manifest(
    *,
    run_id: str,
    started_at: str,
    artifact_root: Path,
    audio_source: str,
    ui_coverage: str,
) -> dict[str, Any]:
    return {
        "schema_version": "mainline_evidence_bundle.v1",
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": None,
        "git_commit": _git_commit(),
        "audio_source": audio_source,
        "input_audio_path_kind": "unknown",
        "input_audio_sha256": "",
        "asr_provider": "not_started",
        "asr_provider_mode": "unknown",
        "asr_fallback_used": None,
        "asr_semantic_quality_status": "not_evaluated",
        "asr_semantic_quality_blocked": False,
        "asr_semantic_quality": {},
        "llm_provider": "not_configured",
        "llm_provider_metadata": {},
        "llm_called": False,
        "llm_call_count": 0,
        "llm_usage_total_tokens": 0,
        "derivation_mode": "unknown",
        "gateway_base_url_kind": "unknown",
        "counts_as_production_llm_evidence": False,
        "ui_coverage": ui_coverage,
        "persistence": "json_file_and_artifact_bundle",
        "session_id": "",
        "artifact_root": _display_path(artifact_root),
        "transcript_char_count": 0,
        "final_segment_count": 0,
        "suggestion_card_count": 0,
        "approach_card_count": 0,
        "minutes_char_count": 0,
        "all_cards_have_evidence": False,
        "delete_verified": False,
        "workbench_same_session_visible": False,
        "frontend_utterance_count": 0,
        "frontend_card_count": 0,
        "frontend_minutes_visible": False,
        "browser_console_error_count": None,
        "network_error_count": None,
        "verdict": "no_go",
        "degradation_reasons": [],
    }


def _apply_ui_report(manifest: dict[str, Any], ui_report: dict[str, Any]) -> None:
    manifest["ui_coverage"] = str(ui_report.get("ui_coverage") or manifest["ui_coverage"])
    manifest["workbench_same_session_visible"] = bool(ui_report.get("workbench_same_session_visible"))
    manifest["frontend_utterance_count"] = int(ui_report.get("frontend_utterance_count") or 0)
    manifest["frontend_card_count"] = int(ui_report.get("frontend_card_count") or 0)
    manifest["frontend_minutes_visible"] = bool(ui_report.get("frontend_minutes_visible"))
    manifest["browser_console_error_count"] = int(ui_report.get("browser_console_error_count") or 0)
    manifest["network_error_count"] = int(ui_report.get("network_error_count") or 0)


def _apply_asr_semantic_quality(manifest: dict[str, Any], quality: dict[str, Any] | None) -> None:
    quality = dict(quality or {})
    status = str(quality.get("status") or "not_evaluated")
    blocked = bool(
        quality.get("blocker") == "asr_semantic_quality_blocked"
        or status == "blocked"
    )
    manifest["asr_semantic_quality"] = quality
    manifest["asr_semantic_quality_status"] = status
    manifest["asr_semantic_quality_blocked"] = blocked
    if blocked and "asr_semantic_quality_blocked" not in manifest["degradation_reasons"]:
        manifest["degradation_reasons"].append("asr_semantic_quality_blocked")


def _apply_asr_semantic_quality_from_session_events(
    manifest: dict[str, Any],
    session_events: dict[str, Any],
) -> None:
    event_source = session_events.get("event_source") if isinstance(session_events, dict) else None
    quality = event_source.get("asr_semantic_quality") if isinstance(event_source, dict) else None
    if isinstance(quality, dict):
        _apply_asr_semantic_quality(manifest, quality)


def _real_mic_health_blocker(health_status: str) -> str:
    mapping = {
        "blocked_audio_too_quiet": "real_mic_audio_too_quiet",
        "blocked_no_clear_speech": "real_mic_no_clear_speech",
        "blocked_audio_too_short": "real_mic_audio_too_short",
        "blocked_audio_clipping": "real_mic_audio_clipping",
        "blocked_unsupported_wav_format": "real_mic_unsupported_audio_format",
    }
    return mapping.get(health_status, f"real_mic_health_{health_status}")


def _event_text(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("normalized_text") or payload.get("text") or "")
    return str(event.get("text") or "")


def _apply_go_no_go(manifest: dict[str, Any]) -> None:
    blockers: list[str] = []
    if manifest.get("asr_semantic_quality_blocked") is True:
        blockers.append("asr_semantic_quality_blocked")
    if manifest.get("asr_provider") in {"not_started", "local_mock_asr", "fake"}:
        blockers.append("asr_provider_not_real")
    if manifest.get("asr_fallback_used") is not False:
        blockers.append("asr_fallback_unknown_or_used")
    if int(manifest.get("transcript_char_count") or 0) <= 0:
        blockers.append("transcript_empty")
    if int(manifest.get("final_segment_count") or 0) < 1:
        blockers.append("final_segment_missing")
    if manifest.get("llm_provider") != "real_gateway":
        blockers.append("llm_provider_not_real_gateway")
    if manifest.get("llm_called") is not True:
        blockers.append("llm_not_called")
    if int(manifest.get("suggestion_card_count") or 0) < 1:
        blockers.append("suggestion_card_missing")
    if manifest.get("all_cards_have_evidence") is not True:
        blockers.append("card_evidence_missing")
    if int(manifest.get("minutes_char_count") or 0) <= 0:
        blockers.append("minutes_empty")
    if manifest.get("ui_coverage") not in {"headless_chrome", "visible_chrome"}:
        if manifest.get("ui_coverage") == "in_app_browser_manual":
            blockers.append("ui_not_headless_verified")
        else:
            blockers.append("ui_not_verified_in_browser")
    if manifest.get("workbench_same_session_visible") is not True:
        blockers.append("workbench_same_session_not_visible")
    if int(manifest.get("frontend_utterance_count") or 0) < 1:
        blockers.append("ui_transcript_not_visible")
    if int(manifest.get("frontend_card_count") or 0) < 1:
        blockers.append("ui_cards_not_visible")
    if manifest.get("frontend_minutes_visible") is not True:
        blockers.append("ui_minutes_not_visible")
    if manifest.get("browser_console_error_count") not in {0, None}:
        blockers.append("browser_console_errors")
    if manifest.get("network_error_count") not in {0, None}:
        blockers.append("browser_network_errors")
    if manifest.get("delete_verified") is not True:
        blockers.append("delete_not_verified")

    existing = list(manifest.get("degradation_reasons") or [])
    for blocker in blockers:
        if blocker not in existing:
            existing.append(blocker)
    manifest["degradation_reasons"] = existing
    manifest["verdict"] = "go" if not existing else "no_go"


def _write_bundle_minimum(artifact_root: Path, manifest: dict[str, Any]) -> None:
    _write_json(artifact_root / "manifest.json", manifest)
    evidence_files = _bundle_evidence_files(artifact_root)
    (artifact_root / "go_no_go.md").write_text(
        _render_go_no_go(manifest, evidence_files=evidence_files),
        encoding="utf-8",
    )


def _bundle_evidence_files(artifact_root: Path) -> list[str]:
    if not artifact_root.exists():
        return ["manifest.json"]
    ignored = {"go_no_go.md"}
    files = [
        path.name
        for path in sorted(artifact_root.iterdir())
        if path.is_file() and path.name not in ignored
    ]
    return files or ["manifest.json"]


def _render_go_no_go(manifest: dict[str, Any], *, evidence_files: list[str] | None = None) -> str:
    reasons = manifest.get("degradation_reasons") or []
    lines = [
        f"# Mainline Evidence Bundle {manifest['run_id']}",
        "",
        f"Verdict: {manifest['verdict']}",
        "",
        "## Summary",
        "",
        f"- audio_source: {manifest['audio_source']}",
        f"- asr_provider: {manifest['asr_provider']}",
        f"- llm_provider: {manifest['llm_provider']}",
        f"- ui_coverage: {manifest['ui_coverage']}",
        f"- session_id: {manifest.get('session_id') or 'not_created'}",
        f"- transcript_char_count: {manifest['transcript_char_count']}",
        f"- suggestion_card_count: {manifest['suggestion_card_count']}",
        f"- approach_card_count: {manifest['approach_card_count']}",
        f"- minutes_char_count: {manifest['minutes_char_count']}",
        f"- delete_verified: {manifest['delete_verified']}",
        "",
        "## Blocking Fields",
        "",
    ]
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Evidence Files",
        "",
    ])
    files = evidence_files or ["manifest.json"]
    lines.extend(f"- {name}" for name in files)
    return "\n".join(lines) + "\n"


def _upload_audio(client: TestClient, audio_path: Path) -> dict[str, Any]:
    with audio_path.open("rb") as handle:
        response = client.post(
            "/live/asr/transcribe-file/sessions",
            files={"file": (audio_path.name, handle, "audio/wav")},
        )
    response.raise_for_status()
    return response.json()


def _stream_wav_over_testclient_ws(
    client: TestClient,
    *,
    session_id: str,
    audio_path: Path,
    audio_source: str = "simulated_realtime_wav",
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with client.websocket_connect(f"/live/asr/stream/ws/{session_id}?audio_source={audio_source}") as ws:
        for chunk in pcm_chunks_from_wav(audio_path):
            ws.send_bytes(chunk)
            try:
                event = json.loads(ws.receive_text())
            except Exception:
                continue
            events.append(event)
            if event.get("event_type") == "provider_error":
                return events
        ws.send_text("END")
        while True:
            try:
                event = json.loads(ws.receive_text())
            except Exception:
                break
            events.append(event)
            if event.get("event_type") in {"final", "provider_error"}:
                break
    return events


def _run_downstream_bundle_steps(
    *,
    client: TestClient,
    session_id: str,
    artifact_root: Path,
    manifest: dict[str, Any],
    ui_verifier: UiVerifier | None,
    data_dir: Path,
) -> None:
    llm_response = client.post(f"/live/asr/sessions/{session_id}/llm-execution-runs", json={"mode": "enabled"})
    if not llm_response.is_success:
        payload = _response_json_or_text(llm_response)
        _write_json(artifact_root / "llm_runs_error.json", payload)
        manifest["degradation_reasons"].append(f"llm_execution_blocked:{llm_response.status_code}")
        return
    llm_runs = llm_response.json()
    manifest["llm_called"] = True
    manifest["llm_call_count"] += int(llm_runs.get("run_count") or 0)
    manifest["llm_usage_total_tokens"] += _usage_total_tokens_from_runs(list(llm_runs.get("runs") or []))
    _write_json(artifact_root / "llm_runs.json", llm_runs)
    suggestion_cards = [
        run.get("card") for run in llm_runs.get("runs", [])
        if isinstance(run.get("card"), dict)
    ]
    _write_json(artifact_root / "suggestion_cards.json", suggestion_cards)
    manifest["suggestion_card_count"] = len(suggestion_cards)
    manifest["all_cards_have_evidence"] = all(bool(card.get("evidence_span_ids")) for card in suggestion_cards)

    approach_response = client.post(f"/live/asr/sessions/{session_id}/approach-cards", json={"mode": "enabled"})
    if not approach_response.is_success:
        payload = _response_json_or_text(approach_response)
        _write_json(artifact_root / "approach_cards_error.json", payload)
        manifest["degradation_reasons"].append(f"approach_cards_blocked:{approach_response.status_code}")
        return
    approach = approach_response.json()
    _write_json(artifact_root / "approach_cards.json", approach.get("approach_cards", []))
    manifest["approach_card_count"] = int(approach.get("count", 0))
    manifest["llm_call_count"] += 1
    manifest["llm_usage_total_tokens"] += _usage_total_tokens(approach.get("llm_usage"))

    minutes_response = client.post(f"/live/asr/sessions/{session_id}/minutes", json={"mode": "enabled"})
    if not minutes_response.is_success:
        payload = _response_json_or_text(minutes_response)
        _write_json(artifact_root / "minutes_error.json", payload)
        manifest["degradation_reasons"].append(f"minutes_blocked:{minutes_response.status_code}")
        return
    minutes = minutes_response.json()
    _write_json(artifact_root / "minutes.json", minutes)
    minutes_md = str(minutes.get("minutes_md") or "")
    (artifact_root / "minutes.md").write_text(minutes_md, encoding="utf-8")
    manifest["minutes_char_count"] = len(minutes_md)
    manifest["llm_call_count"] += 1
    manifest["llm_usage_total_tokens"] += _usage_total_tokens(minutes.get("llm_usage"))

    if ui_verifier is not None:
        ui_report = ui_verifier(
            session_id=session_id,
            data_dir=data_dir,
            artifact_root=artifact_root,
        )
        _write_json(artifact_root / "ui_verification.json", ui_report)
        _apply_ui_report(manifest, ui_report)


def _response_json_or_text(response) -> dict[str, Any]:
    try:
        body = response.json()
        if isinstance(body, dict):
            return body
        return {"body": body}
    except Exception:
        return {"text": response.text, "status_code": response.status_code}


def _usage_total_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    try:
        return int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def _usage_total_tokens_from_runs(runs: list[Any]) -> int:
    total = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        total += _usage_total_tokens(run.get("llm_usage"))
    return total


def _get_json(client: TestClient, path: str) -> dict[str, Any]:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _post_json(client: TestClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def _delete_json(client: TestClient, path: str) -> dict[str, Any]:
    response = client.delete(path)
    response.raise_for_status()
    return response.json()


def verify_workbench_same_session(
    *,
    session_id: str,
    data_dir: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    script = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_session_verify.mjs"
    resolved_data_dir = _resolve_repo_relative_path(data_dir)
    resolved_artifact_root = _resolve_repo_relative_path(artifact_root)
    env = {
        **os.environ,
        "MEETING_COPILOT_VERIFY_SESSION_ID": session_id,
        "MEETING_COPILOT_DATA_DIR": str(resolved_data_dir),
        "MEETING_COPILOT_ARTIFACT_ROOT": str(resolved_artifact_root),
    }
    proc = subprocess.run(
        ["node", str(script)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    resolved_artifact_root.mkdir(parents=True, exist_ok=True)
    (resolved_artifact_root / "ui_verification.stdout.log").write_text(proc.stdout, encoding="utf-8")
    (resolved_artifact_root / "ui_verification.stderr.log").write_text(proc.stderr, encoding="utf-8")
    report_path = resolved_artifact_root / "ui_verification.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": False,
            "frontend_utterance_count": 0,
            "frontend_card_count": 0,
            "frontend_minutes_visible": False,
            "browser_console_error_count": 0,
            "network_error_count": 1,
            "error": "ui verifier did not write ui_verification.json",
        }
    if proc.returncode != 0:
        report["ui_verifier_returncode"] = proc.returncode
    return report


def _resolve_repo_relative_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def _get_status(client: TestClient, path: str) -> int:
    return client.get(path).status_code


def _llm_provider_mode(config: llm_service.LlmConfig | None) -> str:
    if config is None:
        return "disabled"
    if config.is_mock:
        return "local_mock_openai"
    return "real_gateway"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(_sanitize_for_evidence(data), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _sanitize_for_evidence(data: Any) -> Any:
    secret_values = [
        value
        for value in [os.environ.get("LLM_GATEWAY_API_KEY")]
        if value
    ]
    return _sanitize_value(data, secret_values=secret_values)


def _sanitize_value(value: Any, *, secret_values: list[str]) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"api_key", "authorization", "token", "secret"}:
                sanitized[key_text] = "<redacted>" if item else item
            else:
                sanitized[key_text] = _sanitize_value(item, secret_values=secret_values)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item, secret_values=secret_values) for item in value]
    if isinstance(value, str):
        sanitized_text = value
        for secret in secret_values:
            sanitized_text = sanitized_text.replace(secret, "<redacted>")
        return sanitized_text
    return value


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return path.name


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _git_commit() -> str:
    git_head = REPO_ROOT / ".git" / "HEAD"
    if not git_head.exists():
        return "unknown"
    head = git_head.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref = REPO_ROOT / ".git" / head.removeprefix("ref: ").strip()
        return ref.read_text(encoding="utf-8").strip() if ref.exists() else "unknown"
    return head


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    return text.replace(os.environ.get("LLM_GATEWAY_API_KEY", ""), "<redacted>") if os.environ.get("LLM_GATEWAY_API_KEY") else text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-file-lane")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=["file", "simulated-realtime", "real-mic-recorded-realtime", "browser-live-mic"], default="file")
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO_PATH)
    parser.add_argument("--health-report", type=Path, default=None)
    parser.add_argument("--browser-mic-health-report", type=Path, default=None)
    parser.add_argument("--asr-probe", type=Path, default=None)
    parser.add_argument("--ui-report", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--skip-ui", action="store_true", help="Do not run the headless Workbench same-session verifier.")
    args = parser.parse_args(argv)

    run_id = args.run_id or _default_run_id()
    artifact_root = args.artifact_root or DEFAULT_ARTIFACT_ROOT / run_id
    data_dir = args.data_dir or artifact_root / "runtime_data"
    bundle_kwargs = {
        "audio_path": args.audio,
        "artifact_root": artifact_root,
        "data_dir": data_dir,
        "run_id": run_id,
        "ui_verifier": None if args.skip_ui else verify_workbench_same_session,
    }
    if args.lane == "simulated-realtime":
        bundle = run_simulated_realtime_lane_bundle(**bundle_kwargs)
    elif args.lane == "real-mic-recorded-realtime":
        if args.health_report is None:
            parser.error("--health-report is required for --lane real-mic-recorded-realtime")
        health_report = json.loads(args.health_report.read_text(encoding="utf-8"))
        bundle = run_real_mic_recorded_realtime_lane_bundle(
            health_report=health_report,
            **bundle_kwargs,
        )
    elif args.lane == "browser-live-mic":
        if args.browser_mic_health_report is None:
            parser.error("--browser-mic-health-report is required for --lane browser-live-mic")
        browser_mic_health = json.loads(args.browser_mic_health_report.read_text(encoding="utf-8"))
        asr_probe = json.loads(args.asr_probe.read_text(encoding="utf-8")) if args.asr_probe else None
        ui_report = json.loads(args.ui_report.read_text(encoding="utf-8")) if args.ui_report else None
        bundle = run_browser_live_mic_lane_bundle(
            browser_mic_health=browser_mic_health,
            artifact_root=artifact_root,
            run_id=run_id,
            asr_probe=asr_probe,
            ui_report=ui_report,
        )
    else:
        bundle = run_file_lane_bundle(**bundle_kwargs)
    print(json.dumps(bundle["manifest"], ensure_ascii=False, indent=2))
    return 0 if bundle["manifest"]["verdict"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
