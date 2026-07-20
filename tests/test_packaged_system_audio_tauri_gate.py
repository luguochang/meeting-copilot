from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import stat
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import packaged_system_audio_tauri_gate as gate  # noqa: E402


MACHO = b"\xcf\xfa\xed\xfe"
CHAIN_HASH = hashlib.sha256(b"one-packaged-product-chain").hexdigest()


def _make_repo_and_app(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    artifacts = repo / "artifacts"
    artifacts.mkdir(parents=True)
    app = artifacts / "candidate" / "Meeting Copilot.app"
    contents = app / "Contents"
    binary = contents / "MacOS/meeting-copilot-desktop"
    helper = (
        contents
        / "Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-native-system-audio"
    )
    binary.parent.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    binary.write_bytes(MACHO + b"packaged-app-binary")
    helper.write_bytes(MACHO + b"packaged-system-audio-helper")
    binary.chmod(0o755)
    helper.chmod(0o755)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": gate.APP_IDENTIFIER,
                "CFBundleName": "Meeting Copilot",
            }
        )
    )
    hashes = {
        "app_identifier": gate.APP_IDENTIFIER,
        "app_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "helper_binary_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
    }
    return repo, app, hashes


def _common(
    *,
    schema: str,
    scope: str,
    source: str,
    hashes: dict[str, str],
    helper_only: bool,
    chain_id: str | None,
) -> dict[str, object]:
    return {
        "schema_version": schema,
        "evidence_scope": scope,
        "chain_id_sha256": chain_id,
        "candidate": dict(hashes),
        "provenance": {
            "source": source,
            "packaged_app": True,
            "fake_audio": False,
            "fake_asr": False,
            "fake_llm": False,
            "helper_only": helper_only,
            "direct_backend": False,
        },
        "privacy": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "user_content_in_evidence": False,
            "secrets_in_evidence": False,
        },
    }


def _helper_evidence(hashes: dict[str, str]) -> dict[str, object]:
    payload = _common(
        schema=gate.HELPER_SCHEMA,
        scope="packaged_helper_probe",
        source="packaged_native_helper_probe",
        hashes=hashes,
        helper_only=True,
        chain_id=None,
    )
    payload["observations"] = {
        "permission": "authorized",
        "capture_framework": "ScreenCaptureKit",
        "pcm_event_count": 12,
        "frame_count": 19200,
        "pcm_bytes": 76800,
        "nonzero_pcm_event_count": 12,
        "raw_nonzero_byte_count": 53120,
        "audible_pcm_event_count": 11,
        "peak_rms": 0.18,
        "metric_unavailable": False,
        "raw_audio_files_written": False,
        "remote_upload_attempted": False,
    }
    payload["decision"] = {
        "counts_as_real_packaged_helper_capture": True,
        "counts_as_tauri_ipc_backend_asr_recording": False,
        "counts_as_shared_ui_system_audio": False,
        "counts_as_product_acceptance": False,
    }
    return payload


def _tauri_evidence(
    hashes: dict[str, str], *, capture_mode: str = "dual_track"
) -> dict[str, object]:
    payload = _common(
        schema=gate.TAURI_SCHEMA,
        scope="tauri_ipc_backend_asr_recording",
        source="packaged_tauri_webview_ipc",
        hashes=hashes,
        helper_only=False,
        chain_id=CHAIN_HASH,
    )
    observations: dict[str, object] = {
        "capture_mode": capture_mode,
        "tauri_ipc_start_ok": True,
        "tauri_ipc_status_ok": True,
        "tauri_ipc_stop_ok": True,
        "helper_exit_ok": True,
        "transport_ready": True,
        "pcm_seen": True,
        "audible_pcm_seen": True,
        "pcm_event_count": 25,
        "audible_pcm_event_count": 23,
        "pcm_bytes_sent": 160000,
        "backend_authenticated_loopback": True,
        "backend_audio_event_count": 25,
        "asr_provider": "packaged_local_funasr",
        "asr_ready": True,
        "asr_final_count": 3,
        "recording_owner": "v2",
        "system_audio_track_chunk_count": 4,
        "system_audio_track_bytes": 160000,
        "recording_assembled": True,
        "system_audio_playback_range_ok": True,
        "capture_epoch_forwarded": True,
        "track_sequence_monotonic": True,
        "capture_timestamp_forwarded": True,
        "meeting_ended": True,
    }
    if capture_mode == "dual_track":
        observations.update(
            {
                "independent_track_ids": True,
                "independent_capture_epochs": True,
                "microphone_transport_ready": True,
                "microphone_pcm_seen": True,
                "microphone_audible_pcm_seen": True,
                "microphone_pcm_bytes_sent": 140000,
                "microphone_track_chunk_count": 4,
                "microphone_track_bytes": 140000,
                "microphone_playback_range_ok": True,
                "mixed_playback_range_ok": True,
                "dedup_evaluated": True,
                "duplicate_final_count": 0,
            }
        )
    payload["observations"] = observations
    payload["decision"] = {
        "counts_as_tauri_ipc_backend_asr_recording": True,
        "counts_as_direct_backend_evidence": False,
        "counts_as_shared_ui_system_audio": False,
        "counts_as_product_acceptance": False,
    }
    return payload


def _ui_evidence(
    hashes: dict[str, str], *, capture_mode: str = "dual_track"
) -> dict[str, object]:
    payload = _common(
        schema=gate.UI_SCHEMA,
        scope="packaged_shared_ui",
        source="packaged_tauri_shared_ui",
        hashes=hashes,
        helper_only=False,
        chain_id=CHAIN_HASH,
    )
    observations: dict[str, object] = {
        "capture_mode": capture_mode,
        "packaged_webview_origin": True,
        "system_audio_source_selected": True,
        "capture_started": True,
        "transport_ready_visible": True,
        "pcm_seen_visible": True,
        "audible_pcm_visible": True,
        "connected_silence_state_verified": True,
        "realtime_transcript_visible_during_capture": True,
        "realtime_correction_terminal_visible_during_capture": True,
        "realtime_ai_suggestion_visible_during_capture": True,
        "meeting_end_completed": True,
        "review_transcript_visible": True,
        "recording_playback_worked": True,
        "history_reopen_worked": True,
        "permission_denial_from_real_tcc": True,
        "permission_denial_message_visible": True,
        "permission_denial_no_microphone_fallback": True,
        "browser_error_count": 0,
        "http_5xx_count": 0,
        "screenshot_count": 6,
        "screenshots_redacted": True,
    }
    if capture_mode == "dual_track":
        observations.update(
            {
                "dual_track_source_selected": True,
                "both_track_statuses_visible": True,
                "single_track_failure_from_real_runtime": True,
                "single_track_failure_visible": True,
                "single_track_failure_not_shown_as_complete": True,
                "system_audio_playback_selected": True,
                "microphone_playback_selected": True,
                "mixed_playback_selected": True,
                "no_duplicate_transcript_visible": True,
            }
        )
    payload["observations"] = observations
    payload["decision"] = {
        "counts_as_shared_ui_system_audio": True,
        "counts_as_direct_backend_evidence": False,
        "counts_as_helper_only_evidence": False,
        "counts_as_product_acceptance": False,
    }
    return payload


def _write_evidence(repo: Path, name: str, payload: object) -> Path:
    path = repo / "artifacts/evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)
    return path


def _valid_inputs(
    tmp_path: Path, *, capture_mode: str = "dual_track"
) -> tuple[Path, Path, Path, Path, Path]:
    repo, app, hashes = _make_repo_and_app(tmp_path)
    helper = _write_evidence(repo, "helper.json", _helper_evidence(hashes))
    tauri = _write_evidence(
        repo, "tauri.json", _tauri_evidence(hashes, capture_mode=capture_mode)
    )
    ui = _write_evidence(
        repo, "ui.json", _ui_evidence(hashes, capture_mode=capture_mode)
    )
    return repo, app, helper, tauri, ui


def _report(
    repo: Path,
    app: Path,
    helper: Path | None,
    tauri: Path | None,
    ui: Path | None,
    *,
    target: str = "both",
) -> dict[str, object]:
    return gate.build_report(
        repo_root=repo,
        app_path=app,
        helper_evidence_path=helper,
        tauri_evidence_path=tauri,
        ui_evidence_path=ui,
        run_id="unit-run",
        target=target,
    )


def test_gate_is_evidence_only_and_missing_layers_fail_closed(tmp_path: Path) -> None:
    repo, app, _ = _make_repo_and_app(tmp_path)

    report = _report(repo, app, None, None, None)

    assert report["status"] == "no_go_packaged_system_audio"
    assert report["decision"]["passed"] is False
    assert (
        report["decision"]["counts_as_packaged_system_audio_three_layer_acceptance"]
        is False
    )
    assert report["privacy_cost_flags"] == {
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
    }
    assert "input:helper_evidence_missing" in report["blockers"]
    assert "input:tauri_evidence_missing" in report["blockers"]
    assert "input:ui_evidence_missing" in report["blockers"]


def test_all_three_real_dual_track_layers_can_pass_next001_and_next002(
    tmp_path: Path,
) -> None:
    repo, app, helper, tauri, ui = _valid_inputs(tmp_path)

    report = _report(repo, app, helper, tauri, ui)

    assert (
        report["status"]
        == "go_next001_next002_packaged_system_audio_not_public_release"
    )
    assert report["blockers"] == []
    assert report["decision"] == {
        "passed": True,
        "counts_as_real_packaged_helper_capture": True,
        "counts_as_tauri_ipc_backend_asr_recording": True,
        "counts_as_shared_ui_system_audio": True,
        "counts_as_next001_packaged_product_acceptance": True,
        "counts_as_next002_packaged_product_acceptance": True,
        "counts_as_requested_packaged_acceptance": True,
        "counts_as_packaged_system_audio_three_layer_acceptance": True,
        "counts_as_helper_only_product_acceptance": False,
        "counts_as_direct_backend_product_acceptance": False,
        "counts_as_fake_product_acceptance": False,
        "counts_as_public_release_evidence": False,
    }
    assert report["chain_binding"] == {
        "tauri_and_ui_chain_match": True,
        "chain_id_sha256": CHAIN_HASH,
    }


def test_system_audio_only_can_pass_next001_but_never_next002(tmp_path: Path) -> None:
    repo, app, helper, tauri, ui = _valid_inputs(tmp_path, capture_mode="system_audio")

    next001 = _report(repo, app, helper, tauri, ui, target="next001")
    both = _report(repo, app, helper, tauri, ui, target="both")

    assert next001["decision"]["counts_as_next001_packaged_product_acceptance"] is True
    assert next001["decision"]["counts_as_next002_packaged_product_acceptance"] is False
    assert next001["decision"]["passed"] is True
    assert both["decision"]["passed"] is False
    assert "tauri_chain:dual_track_capture_not_proven" in both["blockers"]
    assert "shared_ui:dual_track_ui_not_proven" in both["blockers"]


def test_silent_pcm_never_counts_even_when_tauri_and_ui_claim_success(
    tmp_path: Path,
) -> None:
    repo, app, hashes = _make_repo_and_app(tmp_path)
    helper_payload = _helper_evidence(hashes)
    helper_payload["observations"]["peak_rms"] = 0.0
    helper_payload["observations"]["audible_pcm_event_count"] = 0
    helper = _write_evidence(repo, "helper.json", helper_payload)
    tauri = _write_evidence(repo, "tauri.json", _tauri_evidence(hashes))
    ui = _write_evidence(repo, "ui.json", _ui_evidence(hashes))

    report = _report(repo, app, helper, tauri, ui)

    assert report["layers"]["helper_probe"]["status"] == "blocked"
    assert report["decision"]["counts_as_real_packaged_helper_capture"] is False
    assert report["decision"]["counts_as_requested_packaged_acceptance"] is False
    assert "helper_probe:silent_pcm_below_audible_threshold" in report["blockers"]


@pytest.mark.parametrize(
    ("layer", "field", "value", "expected_blocker"),
    [
        (
            "tauri",
            "direct_backend",
            True,
            "tauri_chain:provenance_invalid:direct_backend",
        ),
        ("tauri", "fake_asr", True, "tauri_chain:provenance_invalid:fake_asr"),
        ("ui", "fake_llm", True, "shared_ui:provenance_invalid:fake_llm"),
        ("ui", "helper_only", True, "shared_ui:provenance_invalid:helper_only"),
    ],
)
def test_fake_direct_backend_and_helper_only_claims_fail_closed(
    tmp_path: Path,
    layer: str,
    field: str,
    value: bool,
    expected_blocker: str,
) -> None:
    repo, app, hashes = _make_repo_and_app(tmp_path)
    helper_payload = _helper_evidence(hashes)
    tauri_payload = _tauri_evidence(hashes)
    ui_payload = _ui_evidence(hashes)
    selected = tauri_payload if layer == "tauri" else ui_payload
    selected["provenance"][field] = value
    helper = _write_evidence(repo, "helper.json", helper_payload)
    tauri = _write_evidence(repo, "tauri.json", tauri_payload)
    ui = _write_evidence(repo, "ui.json", ui_payload)

    report = _report(repo, app, helper, tauri, ui)

    assert report["decision"]["passed"] is False
    assert report["decision"]["counts_as_fake_product_acceptance"] is False
    assert report["decision"]["counts_as_direct_backend_product_acceptance"] is False
    assert report["decision"]["counts_as_helper_only_product_acceptance"] is False
    assert expected_blocker in report["blockers"]


def test_candidate_and_product_chain_mismatches_fail_closed(tmp_path: Path) -> None:
    repo, app, hashes = _make_repo_and_app(tmp_path)
    helper_payload = _helper_evidence(hashes)
    tauri_payload = _tauri_evidence(hashes)
    ui_payload = _ui_evidence(hashes)
    tauri_payload["candidate"]["helper_binary_sha256"] = "0" * 64
    ui_payload["chain_id_sha256"] = hashlib.sha256(b"different-chain").hexdigest()
    helper = _write_evidence(repo, "helper.json", helper_payload)
    tauri = _write_evidence(repo, "tauri.json", tauri_payload)
    ui = _write_evidence(repo, "ui.json", ui_payload)

    report = _report(repo, app, helper, tauri, ui)

    assert report["decision"]["passed"] is False
    assert (
        "tauri_chain:candidate_binding_invalid:helper_binary_sha256"
        in report["blockers"]
    )
    assert (
        "chain_binding:tauri_and_ui_product_chain_hash_mismatch" in report["blockers"]
    )
    assert report["chain_binding"]["chain_id_sha256"] is None


def test_unredacted_input_is_rejected_without_copying_secret_or_path(
    tmp_path: Path,
) -> None:
    repo, app, hashes = _make_repo_and_app(tmp_path)
    secret = "sk-private-unit-test-credential"
    helper_payload = _helper_evidence(hashes)
    helper_payload["debug"] = {
        "authorization": f"Bearer {secret}",
        "recording_path": "/Users/private-person/meeting.wav",
    }
    helper = _write_evidence(repo, "helper.json", helper_payload)
    tauri = _write_evidence(repo, "tauri.json", _tauri_evidence(hashes))
    ui = _write_evidence(repo, "ui.json", _ui_evidence(hashes))

    report = _report(repo, app, helper, tauri, ui)
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["decision"]["passed"] is False
    assert "input:helper_evidence_not_redacted" in report["blockers"]
    assert secret not in serialized
    assert "Bearer" not in serialized
    assert "/Users/private-person" not in serialized
    assert "meeting.wav" not in serialized


def test_report_writer_is_owner_only_and_output_is_restricted(tmp_path: Path) -> None:
    repo, app, helper, tauri, ui = _valid_inputs(tmp_path)
    report = _report(repo, app, helper, tauri, ui)

    path = gate.write_report(
        report,
        repo_root=repo,
        output_root=repo / "artifacts/tmp/system-audio-gate",
        run_id="unit-run",
    )

    assert json.loads(path.read_text(encoding="utf-8"))["decision"]["passed"] is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    with pytest.raises(ValueError, match="artifacts/tmp"):
        gate.write_report(
            report,
            repo_root=repo,
            output_root=tmp_path / "outside",
            run_id="unit-run",
        )


def test_cli_contract_has_no_permission_or_capture_switch() -> None:
    parser_args = gate.parse_args(["--run-id", "unit-run"])

    assert parser_args.app_path is None
    assert parser_args.helper_evidence is None
    assert parser_args.tauri_evidence is None
    assert parser_args.ui_evidence is None
    assert not hasattr(parser_args, "request_permission")
    assert not hasattr(parser_args, "run_helper")
    assert "subprocess" not in Path(gate.__file__).read_text(encoding="utf-8")
