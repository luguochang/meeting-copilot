#!/usr/bin/env python3
"""Fail-closed, evidence-only gate for packaged macOS system audio.

This tool does not launch the application, invoke a native helper, request TCC
permission, play audio, or call a backend. It evaluates three independently
produced, redacted evidence documents and binds them to one inspected packaged
candidate:

1. real packaged ScreenCaptureKit helper probe;
2. packaged Tauri WebView IPC -> backend -> local ASR -> V2 recording chain;
3. the shared React UI running inside the same packaged Tauri WebView.

Passing only one or two layers is intentionally useful evidence, but never
counts as NEXT-001/NEXT-002 packaged product acceptance. Direct-backend, fake,
helper-only, silent-PCM, unredacted, or candidate-mismatched reports fail
closed. The aggregate report copies no source payload or meeting content.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import stat
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_system_audio_tauri_gate"
APP_IDENTIFIER = "com.meetingcopilot.desktop"
APP_BINARY_RELATIVE = Path("Contents/MacOS/meeting-copilot-desktop")
HELPER_BINARY_RELATIVE = Path(
    "Contents/Resources/MeetingCopilotRuntime.bundle/bin/"
    "meeting-copilot-native-system-audio"
)
REPORT_SCHEMA = "meeting_copilot.packaged_system_audio_tauri_gate.v1"
HELPER_SCHEMA = "meeting_copilot.packaged_system_audio_helper_evidence.v1"
TAURI_SCHEMA = "meeting_copilot.packaged_system_audio_tauri_chain_evidence.v1"
UI_SCHEMA = "meeting_copilot.packaged_system_audio_shared_ui_evidence.v1"
AUDIBLE_RMS_THRESHOLD = 0.001
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECRET_STRING_PATTERN = re.compile(
    r"(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|https?://|"
    r"(?:^|\s)/(?:Users|home|private|var|tmp)/)",
    re.IGNORECASE,
)
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "credential",
    "full_transcript",
    "meeting_content",
    "password",
    "prompt",
    "provider_url",
    "recording_path",
    "response",
    "secret",
    "transcript_text",
    "utterance_text",
    "websocket_url",
}
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "evidence_scope",
    "chain_id_sha256",
    "candidate",
    "provenance",
    "privacy",
    "observations",
    "decision",
}
CANDIDATE_KEYS = {
    "app_identifier",
    "app_binary_sha256",
    "helper_binary_sha256",
}
PROVENANCE_KEYS = {
    "source",
    "packaged_app",
    "fake_audio",
    "fake_asr",
    "fake_llm",
    "helper_only",
    "direct_backend",
}
PRIVACY_KEYS = {
    "raw_audio_uploaded",
    "remote_asr_called",
    "user_content_in_evidence",
    "secrets_in_evidence",
}

HELPER_OBSERVATION_KEYS = {
    "permission",
    "capture_framework",
    "pcm_event_count",
    "frame_count",
    "pcm_bytes",
    "nonzero_pcm_event_count",
    "raw_nonzero_byte_count",
    "audible_pcm_event_count",
    "peak_rms",
    "metric_unavailable",
    "raw_audio_files_written",
    "remote_upload_attempted",
}
HELPER_DECISION_KEYS = {
    "counts_as_real_packaged_helper_capture",
    "counts_as_tauri_ipc_backend_asr_recording",
    "counts_as_shared_ui_system_audio",
    "counts_as_product_acceptance",
}

TAURI_BASE_OBSERVATION_KEYS = {
    "capture_mode",
    "tauri_ipc_start_ok",
    "tauri_ipc_status_ok",
    "tauri_ipc_stop_ok",
    "helper_exit_ok",
    "transport_ready",
    "pcm_seen",
    "audible_pcm_seen",
    "pcm_event_count",
    "audible_pcm_event_count",
    "pcm_bytes_sent",
    "backend_authenticated_loopback",
    "backend_audio_event_count",
    "asr_provider",
    "asr_ready",
    "asr_final_count",
    "recording_owner",
    "system_audio_track_chunk_count",
    "system_audio_track_bytes",
    "recording_assembled",
    "system_audio_playback_range_ok",
    "capture_epoch_forwarded",
    "track_sequence_monotonic",
    "capture_timestamp_forwarded",
    "meeting_ended",
}
TAURI_DUAL_OBSERVATION_KEYS = {
    "independent_track_ids",
    "independent_capture_epochs",
    "microphone_transport_ready",
    "microphone_pcm_seen",
    "microphone_audible_pcm_seen",
    "microphone_pcm_bytes_sent",
    "microphone_track_chunk_count",
    "microphone_track_bytes",
    "microphone_playback_range_ok",
    "mixed_playback_range_ok",
    "dedup_evaluated",
    "duplicate_final_count",
}
TAURI_DECISION_KEYS = {
    "counts_as_tauri_ipc_backend_asr_recording",
    "counts_as_direct_backend_evidence",
    "counts_as_shared_ui_system_audio",
    "counts_as_product_acceptance",
}

UI_BASE_OBSERVATION_KEYS = {
    "capture_mode",
    "packaged_webview_origin",
    "system_audio_source_selected",
    "capture_started",
    "transport_ready_visible",
    "pcm_seen_visible",
    "audible_pcm_visible",
    "connected_silence_state_verified",
    "realtime_transcript_visible_during_capture",
    "realtime_correction_terminal_visible_during_capture",
    "realtime_ai_suggestion_visible_during_capture",
    "meeting_end_completed",
    "review_transcript_visible",
    "recording_playback_worked",
    "history_reopen_worked",
    "permission_denial_from_real_tcc",
    "permission_denial_message_visible",
    "permission_denial_no_microphone_fallback",
    "browser_error_count",
    "http_5xx_count",
    "screenshot_count",
    "screenshots_redacted",
}
UI_DUAL_OBSERVATION_KEYS = {
    "dual_track_source_selected",
    "both_track_statuses_visible",
    "single_track_failure_from_real_runtime",
    "single_track_failure_visible",
    "single_track_failure_not_shown_as_complete",
    "system_audio_playback_selected",
    "microphone_playback_selected",
    "mixed_playback_selected",
    "no_duplicate_transcript_visible",
}
UI_DECISION_KEYS = {
    "counts_as_shared_ui_system_audio",
    "counts_as_direct_backend_evidence",
    "counts_as_helper_only_evidence",
    "counts_as_product_acceptance",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_positive_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _is_nonnegative_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _unique(values: list[str]) -> list[str]:
    return sorted(set(values))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _validate_exact_keys(
    value: Any,
    expected: set[str],
    *,
    label: str,
    required: set[str] | None = None,
) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{label}_not_object"]
    actual = {str(key) for key in value}
    blockers: list[str] = []
    if actual - expected:
        blockers.append(f"{label}_contains_unexpected_fields")
    missing = (required if required is not None else expected) - actual
    if missing:
        blockers.append(f"{label}_missing_required_fields")
    return blockers


def _contains_sensitive_material(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if str(raw_key).strip().lower() in SENSITIVE_KEYS:
                return True
            if _contains_sensitive_material(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_material(item) for item in value)
    return isinstance(value, str) and SECRET_STRING_PATTERN.search(value) is not None


def _safe_evidence_reference(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return "<external-evidence-path-redacted>"


def _load_evidence(
    path: Path | None,
    *,
    repo_root: Path,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    source = {
        "path": _safe_evidence_reference(path, repo_root),
        "sha256": None,
        "size_bytes": 0,
        "loaded": False,
    }
    if path is None:
        return None, source, [f"{label}_evidence_missing"]
    resolved = path.resolve()
    approved_root = (repo_root / "artifacts").resolve()
    try:
        resolved.relative_to(approved_root)
    except ValueError:
        return None, source, [f"{label}_evidence_outside_artifacts"]
    if path.is_symlink() or not resolved.is_file():
        return None, source, [f"{label}_evidence_not_regular_file"]
    try:
        size = resolved.stat().st_size
    except OSError:
        return None, source, [f"{label}_evidence_unreadable"]
    source["size_bytes"] = size
    if size <= 0 or size > MAX_EVIDENCE_BYTES:
        return None, source, [f"{label}_evidence_size_invalid"]
    try:
        raw = resolved.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, source, [f"{label}_evidence_invalid_json"]
    source["sha256"] = _sha256_bytes(raw)
    source["loaded"] = True
    if not isinstance(payload, dict):
        return None, source, [f"{label}_evidence_not_object"]
    if _contains_sensitive_material(payload):
        return None, source, [f"{label}_evidence_not_redacted"]
    return payload, source, []


def inspect_packaged_candidate(
    app_path: Path | None,
) -> tuple[dict[str, Any], list[str]]:
    summary: dict[str, Any] = {
        "verified": False,
        "app_identifier": None,
        "app_binary_sha256": None,
        "helper_binary_sha256": None,
        "app_binary_is_macho": False,
        "helper_binary_is_macho": False,
    }
    if app_path is None:
        return summary, ["packaged_candidate_missing"]
    app = app_path.resolve()
    blockers: list[str] = []
    if app_path.is_symlink() or app.name != "Meeting Copilot.app" or not app.is_dir():
        return summary, ["packaged_candidate_invalid"]
    info_path = app / "Contents/Info.plist"
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return summary, ["packaged_candidate_info_plist_invalid"]
    identifier = info.get("CFBundleIdentifier")
    summary["app_identifier"] = identifier if isinstance(identifier, str) else None
    if identifier != APP_IDENTIFIER:
        blockers.append("packaged_candidate_identifier_invalid")

    for label, relative, hash_key, macho_key in (
        (
            "app_binary",
            APP_BINARY_RELATIVE,
            "app_binary_sha256",
            "app_binary_is_macho",
        ),
        (
            "helper_binary",
            HELPER_BINARY_RELATIVE,
            "helper_binary_sha256",
            "helper_binary_is_macho",
        ),
    ):
        binary = app / relative
        try:
            binary.resolve().relative_to(app)
        except (OSError, ValueError):
            blockers.append(f"packaged_candidate_{label}_escapes_bundle")
            continue
        if (
            binary.is_symlink()
            or not binary.is_file()
            or not os.access(binary, os.X_OK)
        ):
            blockers.append(f"packaged_candidate_{label}_missing_or_not_executable")
            continue
        try:
            magic = binary.read_bytes()[:4]
            summary[hash_key] = _sha256_file(binary)
        except OSError:
            blockers.append(f"packaged_candidate_{label}_unreadable")
            continue
        summary[macho_key] = magic in MACHO_MAGICS
        if magic not in MACHO_MAGICS:
            blockers.append(f"packaged_candidate_{label}_not_macho")
    summary["verified"] = not blockers
    return summary, _unique(blockers)


def _common_layer_blockers(
    payload: dict[str, Any] | None,
    *,
    schema: str,
    scope: str,
    source: str,
    helper_only: bool,
    candidate: Mapping[str, Any],
    observation_keys: set[str],
    required_observation_keys: set[str],
    decision_keys: set[str],
) -> list[str]:
    if payload is None:
        return []
    blockers: list[str] = []
    blockers.extend(_validate_exact_keys(payload, TOP_LEVEL_KEYS, label="top_level"))
    blockers.extend(
        _validate_exact_keys(
            payload.get("candidate"), CANDIDATE_KEYS, label="candidate"
        )
    )
    blockers.extend(
        _validate_exact_keys(
            payload.get("provenance"), PROVENANCE_KEYS, label="provenance"
        )
    )
    blockers.extend(
        _validate_exact_keys(payload.get("privacy"), PRIVACY_KEYS, label="privacy")
    )
    blockers.extend(
        _validate_exact_keys(
            payload.get("observations"),
            observation_keys,
            label="observations",
            required=required_observation_keys,
        )
    )
    blockers.extend(
        _validate_exact_keys(payload.get("decision"), decision_keys, label="decision")
    )
    if payload.get("schema_version") != schema:
        blockers.append("schema_version_invalid")
    if payload.get("evidence_scope") != scope:
        blockers.append("evidence_scope_invalid")

    evidence_candidate = _mapping(payload.get("candidate"))
    for key in CANDIDATE_KEYS:
        expected = candidate.get(key)
        if (
            not candidate.get("verified")
            or expected is None
            or evidence_candidate.get(key) != expected
        ):
            blockers.append(f"candidate_binding_invalid:{key}")

    provenance = _mapping(payload.get("provenance"))
    expected_provenance = {
        "source": source,
        "packaged_app": True,
        "fake_audio": False,
        "fake_asr": False,
        "fake_llm": False,
        "helper_only": helper_only,
        "direct_backend": False,
    }
    for key, expected in expected_provenance.items():
        if provenance.get(key) != expected:
            blockers.append(f"provenance_invalid:{key}")

    privacy = _mapping(payload.get("privacy"))
    for key in PRIVACY_KEYS:
        if privacy.get(key) is not False:
            blockers.append(f"privacy_boundary_invalid:{key}")
    return _unique(blockers)


def _require_true(observations: Mapping[str, Any], keys: set[str]) -> list[str]:
    return [
        f"observation_not_true:{key}"
        for key in keys
        if observations.get(key) is not True
    ]


def _require_false(observations: Mapping[str, Any], keys: set[str]) -> list[str]:
    return [
        f"observation_not_false:{key}"
        for key in keys
        if observations.get(key) is not False
    ]


def _require_positive(observations: Mapping[str, Any], keys: set[str]) -> list[str]:
    return [
        f"observation_not_positive:{key}"
        for key in keys
        if not _is_positive_number(observations.get(key))
    ]


def evaluate_helper_layer(
    payload: dict[str, Any] | None,
    *,
    candidate: Mapping[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
    blockers = _common_layer_blockers(
        payload,
        schema=HELPER_SCHEMA,
        scope="packaged_helper_probe",
        source="packaged_native_helper_probe",
        helper_only=True,
        candidate=candidate,
        observation_keys=HELPER_OBSERVATION_KEYS,
        required_observation_keys=HELPER_OBSERVATION_KEYS,
        decision_keys=HELPER_DECISION_KEYS,
    )
    observations = _mapping((payload or {}).get("observations"))
    decision = _mapping((payload or {}).get("decision"))
    if payload is not None:
        if payload.get("chain_id_sha256") is not None:
            blockers.append("helper_probe_must_not_claim_product_chain")
        if observations.get("permission") != "authorized":
            blockers.append("screen_recording_permission_not_authorized")
        if observations.get("capture_framework") != "ScreenCaptureKit":
            blockers.append("capture_framework_invalid")
        blockers.extend(
            _require_positive(
                observations,
                {
                    "pcm_event_count",
                    "frame_count",
                    "pcm_bytes",
                    "nonzero_pcm_event_count",
                    "raw_nonzero_byte_count",
                    "audible_pcm_event_count",
                },
            )
        )
        peak_rms = observations.get("peak_rms")
        if not _is_positive_number(peak_rms) or float(peak_rms) < AUDIBLE_RMS_THRESHOLD:
            blockers.append("silent_pcm_below_audible_threshold")
        blockers.extend(
            _require_false(
                observations,
                {
                    "metric_unavailable",
                    "raw_audio_files_written",
                    "remote_upload_attempted",
                },
            )
        )
        expected_decision = {
            "counts_as_real_packaged_helper_capture": True,
            "counts_as_tauri_ipc_backend_asr_recording": False,
            "counts_as_shared_ui_system_audio": False,
            "counts_as_product_acceptance": False,
        }
        for key, expected in expected_decision.items():
            if decision.get(key) is not expected:
                blockers.append(f"decision_scope_invalid:{key}")
    blockers = _unique(blockers)
    summary = {
        "pcm_event_count": observations.get("pcm_event_count")
        if _is_nonnegative_integer(observations.get("pcm_event_count"))
        else 0,
        "frame_count": observations.get("frame_count")
        if _is_nonnegative_integer(observations.get("frame_count"))
        else 0,
        "pcm_bytes": observations.get("pcm_bytes")
        if _is_nonnegative_integer(observations.get("pcm_bytes"))
        else 0,
        "audible_pcm_event_count": observations.get("audible_pcm_event_count")
        if _is_nonnegative_integer(observations.get("audible_pcm_event_count"))
        else 0,
        "peak_rms": float(observations.get("peak_rms"))
        if _is_positive_number(observations.get("peak_rms"))
        else 0.0,
    }
    return payload is not None and not blockers, blockers, summary


def evaluate_tauri_layer(
    payload: dict[str, Any] | None,
    *,
    candidate: Mapping[str, Any],
    require_dual: bool,
) -> tuple[bool, list[str], dict[str, Any]]:
    required_keys = set(TAURI_BASE_OBSERVATION_KEYS)
    if require_dual:
        required_keys |= TAURI_DUAL_OBSERVATION_KEYS
    blockers = _common_layer_blockers(
        payload,
        schema=TAURI_SCHEMA,
        scope="tauri_ipc_backend_asr_recording",
        source="packaged_tauri_webview_ipc",
        helper_only=False,
        candidate=candidate,
        observation_keys=TAURI_BASE_OBSERVATION_KEYS | TAURI_DUAL_OBSERVATION_KEYS,
        required_observation_keys=required_keys,
        decision_keys=TAURI_DECISION_KEYS,
    )
    observations = _mapping((payload or {}).get("observations"))
    decision = _mapping((payload or {}).get("decision"))
    chain_id = (payload or {}).get("chain_id_sha256")
    if payload is not None:
        if not isinstance(chain_id, str) or not SHA256_PATTERN.fullmatch(chain_id):
            blockers.append("product_chain_hash_invalid")
        allowed_modes = {"system_audio", "dual_track"}
        if observations.get("capture_mode") not in allowed_modes:
            blockers.append("capture_mode_invalid")
        if require_dual and observations.get("capture_mode") != "dual_track":
            blockers.append("dual_track_capture_not_proven")
        blockers.extend(
            _require_true(
                observations,
                {
                    "tauri_ipc_start_ok",
                    "tauri_ipc_status_ok",
                    "tauri_ipc_stop_ok",
                    "helper_exit_ok",
                    "transport_ready",
                    "pcm_seen",
                    "audible_pcm_seen",
                    "backend_authenticated_loopback",
                    "asr_ready",
                    "recording_assembled",
                    "system_audio_playback_range_ok",
                    "capture_epoch_forwarded",
                    "track_sequence_monotonic",
                    "capture_timestamp_forwarded",
                    "meeting_ended",
                },
            )
        )
        blockers.extend(
            _require_positive(
                observations,
                {
                    "pcm_event_count",
                    "audible_pcm_event_count",
                    "pcm_bytes_sent",
                    "backend_audio_event_count",
                    "asr_final_count",
                    "system_audio_track_chunk_count",
                    "system_audio_track_bytes",
                },
            )
        )
        if observations.get("asr_provider") != "packaged_local_funasr":
            blockers.append("local_packaged_asr_not_proven")
        if observations.get("recording_owner") != "v2":
            blockers.append("v2_recording_owner_not_proven")
        if require_dual:
            blockers.extend(
                _require_true(
                    observations,
                    {
                        "independent_track_ids",
                        "independent_capture_epochs",
                        "microphone_transport_ready",
                        "microphone_pcm_seen",
                        "microphone_audible_pcm_seen",
                        "microphone_playback_range_ok",
                        "mixed_playback_range_ok",
                        "dedup_evaluated",
                    },
                )
            )
            blockers.extend(
                _require_positive(
                    observations,
                    {
                        "microphone_pcm_bytes_sent",
                        "microphone_track_chunk_count",
                        "microphone_track_bytes",
                    },
                )
            )
            if observations.get("duplicate_final_count") != 0:
                blockers.append("duplicate_final_count_not_zero")
        expected_decision = {
            "counts_as_tauri_ipc_backend_asr_recording": True,
            "counts_as_direct_backend_evidence": False,
            "counts_as_shared_ui_system_audio": False,
            "counts_as_product_acceptance": False,
        }
        for key, expected in expected_decision.items():
            if decision.get(key) is not expected:
                blockers.append(f"decision_scope_invalid:{key}")
    blockers = _unique(blockers)
    summary = {
        "capture_mode": observations.get("capture_mode")
        if observations.get("capture_mode") in {"system_audio", "dual_track"}
        else None,
        "pcm_event_count": observations.get("pcm_event_count")
        if _is_nonnegative_integer(observations.get("pcm_event_count"))
        else 0,
        "pcm_bytes_sent": observations.get("pcm_bytes_sent")
        if _is_nonnegative_integer(observations.get("pcm_bytes_sent"))
        else 0,
        "asr_final_count": observations.get("asr_final_count")
        if _is_nonnegative_integer(observations.get("asr_final_count"))
        else 0,
        "system_audio_track_chunk_count": observations.get(
            "system_audio_track_chunk_count"
        )
        if _is_nonnegative_integer(observations.get("system_audio_track_chunk_count"))
        else 0,
        "microphone_track_chunk_count": observations.get("microphone_track_chunk_count")
        if _is_nonnegative_integer(observations.get("microphone_track_chunk_count"))
        else 0,
        "duplicate_final_count": observations.get("duplicate_final_count")
        if _is_nonnegative_integer(observations.get("duplicate_final_count"))
        else None,
    }
    return payload is not None and not blockers, blockers, summary


def evaluate_ui_layer(
    payload: dict[str, Any] | None,
    *,
    candidate: Mapping[str, Any],
    require_dual: bool,
) -> tuple[bool, list[str], dict[str, Any]]:
    required_keys = set(UI_BASE_OBSERVATION_KEYS)
    if require_dual:
        required_keys |= UI_DUAL_OBSERVATION_KEYS
    blockers = _common_layer_blockers(
        payload,
        schema=UI_SCHEMA,
        scope="packaged_shared_ui",
        source="packaged_tauri_shared_ui",
        helper_only=False,
        candidate=candidate,
        observation_keys=UI_BASE_OBSERVATION_KEYS | UI_DUAL_OBSERVATION_KEYS,
        required_observation_keys=required_keys,
        decision_keys=UI_DECISION_KEYS,
    )
    observations = _mapping((payload or {}).get("observations"))
    decision = _mapping((payload or {}).get("decision"))
    chain_id = (payload or {}).get("chain_id_sha256")
    if payload is not None:
        if not isinstance(chain_id, str) or not SHA256_PATTERN.fullmatch(chain_id):
            blockers.append("product_chain_hash_invalid")
        if observations.get("capture_mode") not in {"system_audio", "dual_track"}:
            blockers.append("capture_mode_invalid")
        if require_dual and observations.get("capture_mode") != "dual_track":
            blockers.append("dual_track_ui_not_proven")
        blockers.extend(
            _require_true(
                observations,
                {
                    "packaged_webview_origin",
                    "system_audio_source_selected",
                    "capture_started",
                    "transport_ready_visible",
                    "pcm_seen_visible",
                    "audible_pcm_visible",
                    "connected_silence_state_verified",
                    "realtime_transcript_visible_during_capture",
                    "realtime_correction_terminal_visible_during_capture",
                    "realtime_ai_suggestion_visible_during_capture",
                    "meeting_end_completed",
                    "review_transcript_visible",
                    "recording_playback_worked",
                    "history_reopen_worked",
                    "permission_denial_from_real_tcc",
                    "permission_denial_message_visible",
                    "permission_denial_no_microphone_fallback",
                    "screenshots_redacted",
                },
            )
        )
        for key in ("browser_error_count", "http_5xx_count"):
            if observations.get(key) != 0:
                blockers.append(f"ui_error_count_not_zero:{key}")
        if not _is_positive_number(observations.get("screenshot_count")):
            blockers.append("redacted_screenshot_evidence_missing")
        if require_dual:
            blockers.extend(
                _require_true(
                    observations,
                    {
                        "dual_track_source_selected",
                        "both_track_statuses_visible",
                        "single_track_failure_from_real_runtime",
                        "single_track_failure_visible",
                        "single_track_failure_not_shown_as_complete",
                        "system_audio_playback_selected",
                        "microphone_playback_selected",
                        "mixed_playback_selected",
                        "no_duplicate_transcript_visible",
                    },
                )
            )
        expected_decision = {
            "counts_as_shared_ui_system_audio": True,
            "counts_as_direct_backend_evidence": False,
            "counts_as_helper_only_evidence": False,
            "counts_as_product_acceptance": False,
        }
        for key, expected in expected_decision.items():
            if decision.get(key) is not expected:
                blockers.append(f"decision_scope_invalid:{key}")
    blockers = _unique(blockers)
    summary = {
        "capture_mode": observations.get("capture_mode")
        if observations.get("capture_mode") in {"system_audio", "dual_track"}
        else None,
        "browser_error_count": observations.get("browser_error_count")
        if _is_nonnegative_integer(observations.get("browser_error_count"))
        else None,
        "http_5xx_count": observations.get("http_5xx_count")
        if _is_nonnegative_integer(observations.get("http_5xx_count"))
        else None,
        "screenshot_count": observations.get("screenshot_count")
        if _is_nonnegative_integer(observations.get("screenshot_count"))
        else 0,
    }
    return payload is not None and not blockers, blockers, summary


def _prefixed(label: str, blockers: list[str]) -> list[str]:
    return [f"{label}:{blocker}" for blocker in blockers]


def build_report(
    *,
    repo_root: Path,
    app_path: Path | None,
    helper_evidence_path: Path | None,
    tauri_evidence_path: Path | None,
    ui_evidence_path: Path | None,
    run_id: str,
    target: str,
) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters")
    if target not in {"next001", "next002", "both"}:
        raise ValueError("target must be next001, next002, or both")
    repo_root = repo_root.resolve()
    candidate, candidate_blockers = inspect_packaged_candidate(app_path)
    helper, helper_source, helper_load_blockers = _load_evidence(
        helper_evidence_path, repo_root=repo_root, label="helper"
    )
    tauri, tauri_source, tauri_load_blockers = _load_evidence(
        tauri_evidence_path, repo_root=repo_root, label="tauri"
    )
    ui, ui_source, ui_load_blockers = _load_evidence(
        ui_evidence_path, repo_root=repo_root, label="ui"
    )

    helper_ok, helper_blockers, helper_summary = evaluate_helper_layer(
        helper, candidate=candidate
    )
    tauri_next001_ok, tauri_next001_blockers, _ = evaluate_tauri_layer(
        tauri, candidate=candidate, require_dual=False
    )
    tauri_next002_ok, tauri_next002_blockers, tauri_summary = evaluate_tauri_layer(
        tauri, candidate=candidate, require_dual=True
    )
    ui_next001_ok, ui_next001_blockers, _ = evaluate_ui_layer(
        ui, candidate=candidate, require_dual=False
    )
    ui_next002_ok, ui_next002_blockers, ui_summary = evaluate_ui_layer(
        ui, candidate=candidate, require_dual=True
    )

    tauri_chain = (tauri or {}).get("chain_id_sha256")
    ui_chain = (ui or {}).get("chain_id_sha256")
    chain_matches = (
        isinstance(tauri_chain, str)
        and SHA256_PATTERN.fullmatch(tauri_chain) is not None
        and tauri_chain == ui_chain
    )
    chain_blockers = (
        [] if chain_matches else ["tauri_and_ui_product_chain_hash_mismatch"]
    )
    base_load_blockers = helper_load_blockers + tauri_load_blockers + ui_load_blockers
    next001_passed = bool(
        not candidate_blockers
        and not base_load_blockers
        and helper_ok
        and tauri_next001_ok
        and ui_next001_ok
        and chain_matches
    )
    next002_passed = bool(next001_passed and tauri_next002_ok and ui_next002_ok)
    requested_passed = next001_passed if target == "next001" else next002_passed
    require_dual = target != "next001"
    selected_tauri_blockers = (
        tauri_next002_blockers if require_dual else tauri_next001_blockers
    )
    selected_ui_blockers = ui_next002_blockers if require_dual else ui_next001_blockers
    blockers = _unique(
        _prefixed("candidate", candidate_blockers)
        + _prefixed("input", base_load_blockers)
        + _prefixed("helper_probe", helper_blockers)
        + _prefixed("tauri_chain", selected_tauri_blockers)
        + _prefixed("shared_ui", selected_ui_blockers)
        + _prefixed("chain_binding", chain_blockers)
    )
    if requested_passed:
        status = (
            "go_next001_next002_packaged_system_audio_not_public_release"
            if target in {"next002", "both"}
            else "go_next001_packaged_system_audio_not_public_release"
        )
    else:
        status = "no_go_packaged_system_audio"

    return {
        "schema_version": REPORT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "target": target,
        "status": status,
        "blockers": blockers,
        "candidate": candidate,
        "chain_binding": {
            "tauri_and_ui_chain_match": chain_matches,
            "chain_id_sha256": tauri_chain if chain_matches else None,
        },
        "layers": {
            "helper_probe": {
                "status": "passed" if helper_ok else "blocked",
                "blockers": _unique(helper_load_blockers + helper_blockers),
                "source_evidence": helper_source,
                "summary": helper_summary,
                "counts_as_real_packaged_helper_capture": helper_ok,
                "counts_as_product_acceptance": False,
            },
            "tauri_ipc_backend_asr_recording": {
                "status": (
                    "passed"
                    if (tauri_next002_ok if require_dual else tauri_next001_ok)
                    else "blocked"
                ),
                "blockers": _unique(tauri_load_blockers + selected_tauri_blockers),
                "source_evidence": tauri_source,
                "summary": tauri_summary,
                "counts_as_tauri_ipc_backend_asr_recording": (
                    tauri_next002_ok if require_dual else tauri_next001_ok
                ),
                "counts_as_direct_backend_evidence": False,
                "counts_as_product_acceptance": False,
            },
            "shared_ui": {
                "status": (
                    "passed"
                    if (ui_next002_ok if require_dual else ui_next001_ok)
                    else "blocked"
                ),
                "blockers": _unique(ui_load_blockers + selected_ui_blockers),
                "source_evidence": ui_source,
                "summary": ui_summary,
                "counts_as_shared_ui_system_audio": (
                    ui_next002_ok if require_dual else ui_next001_ok
                ),
                "counts_as_helper_only_evidence": False,
                "counts_as_direct_backend_evidence": False,
                "counts_as_product_acceptance": False,
            },
        },
        "decision": {
            "passed": requested_passed,
            "counts_as_real_packaged_helper_capture": helper_ok,
            "counts_as_tauri_ipc_backend_asr_recording": (
                tauri_next002_ok if require_dual else tauri_next001_ok
            ),
            "counts_as_shared_ui_system_audio": (
                ui_next002_ok if require_dual else ui_next001_ok
            ),
            "counts_as_next001_packaged_product_acceptance": next001_passed,
            "counts_as_next002_packaged_product_acceptance": next002_passed,
            "counts_as_requested_packaged_acceptance": requested_passed,
            "counts_as_packaged_system_audio_three_layer_acceptance": requested_passed,
            "counts_as_helper_only_product_acceptance": False,
            "counts_as_direct_backend_product_acceptance": False,
            "counts_as_fake_product_acceptance": False,
            "counts_as_public_release_evidence": False,
        },
        "privacy_cost_flags": {
            "gate_invoked_tcc": False,
            "gate_started_capture": False,
            "gate_started_packaged_app": False,
            "gate_called_backend": False,
            "gate_called_remote_asr": False,
            "gate_called_remote_llm": False,
            "source_payload_copied_to_report": False,
            "raw_audio_in_report": False,
            "meeting_content_in_report": False,
            "secrets_in_report": False,
        },
        "scope_boundary": {
            "evidence_only": True,
            "silent_pcm_never_accepted": True,
            "fake_never_counts_as_product_acceptance": True,
            "helper_only_never_counts_as_product_acceptance": True,
            "direct_backend_never_counts_as_product_acceptance": True,
            "web_only_never_counts_as_packaged_shared_ui": True,
            "public_release_requires_separate_gate": True,
        },
    }


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    repo_root = repo_root.resolve()
    resolved = (
        output_root.resolve()
        if output_root.is_absolute()
        else (repo_root / output_root).resolve()
    )
    approved = (repo_root / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def write_report(
    report: Mapping[str, Any],
    *,
    repo_root: Path,
    output_root: Path,
    run_id: str,
) -> Path:
    root = resolve_output_root(repo_root, output_root)
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(run_root, 0o700)
    destination = run_root / "report.json"
    temporary = run_root / ".report.json.tmp"
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, destination)
    os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--app-path", type=Path)
    parser.add_argument("--helper-evidence", type=Path)
    parser.add_argument("--tauri-evidence", type=Path)
    parser.add_argument("--ui-evidence", type=Path)
    parser.add_argument(
        "--target", choices=("next001", "next002", "both"), default="both"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        repo_root=args.repo_root,
        app_path=args.app_path,
        helper_evidence_path=args.helper_evidence,
        tauri_evidence_path=args.tauri_evidence,
        ui_evidence_path=args.ui_evidence,
        run_id=args.run_id,
        target=args.target,
    )
    report_path = write_report(
        report,
        repo_root=args.repo_root,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    printable = dict(report)
    printable["report_path"] = _safe_evidence_reference(report_path, args.repo_root)
    print(json.dumps(printable, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"]["counts_as_requested_packaged_acceptance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
