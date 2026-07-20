import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code/desktop_tauri"
NATIVE_ROOT = DESKTOP_ROOT / "native_system_audio"
SOURCE = NATIVE_ROOT / "Sources/MeetingCopilotNativeSystemAudio/main.swift"
INFO_PLIST = NATIVE_ROOT / "Info.plist"
BUILD_SCRIPT = NATIVE_ROOT / "build.sh"
PACKAGED_GATE = NATIVE_ROOT / "packaged_gate.py"
BUNDLE_TOOL = REPO_ROOT / "tools/macos_bundled_runtime_spike.py"


def _load_packaged_gate():
    spec = importlib.util.spec_from_file_location("next001_packaged_gate", PACKAGED_GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bundle_tool():
    spec = importlib.util.spec_from_file_location("next001_bundle_tool", BUNDLE_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built_helper(tmp_path_factory):
    if platform.system() != "Darwin":
        pytest.skip("native system-audio helper builds only on macOS")
    output = tmp_path_factory.mktemp("next001-system-audio") / "meeting-copilot-native-system-audio"
    completed = subprocess.run(
        [str(BUILD_SCRIPT), str(output)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    assert os.access(output, os.X_OK)
    return output


def test_native_helper_describes_real_screencapturekit_pcm_contract_without_permission(built_helper):
    completed = subprocess.run(
        [str(built_helper), "--describe"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "accepts_remote_websocket": False,
        "capture_framework": "ScreenCaptureKit",
        "channels": 1,
        "excludes_current_process_audio": True,
        "fallback_source": None,
        "frame_samples": 4800,
        "minimum_macos": "13.0",
        "pcm_envelope_header_bytes": 44,
        "pcm_envelope_track": "system_audio",
        "pcm_protocol": "native_pcm_v2",
        "permission": "screen_recording",
        "raw_audio_files_written": False,
        "sample_format": "pcm_f32le",
        "sample_rate_hz": 16000,
        "schema_version": "meeting_copilot.native_system_audio_protocol.v1",
        "source": "system_audio",
        "transport": "authenticated_loopback_websocket",
    }


def test_native_helper_blocks_remote_transport_before_permission_or_capture(built_helper, tmp_path):
    completed = subprocess.run(
        [
            str(built_helper),
            "--ws-url",
            "wss://example.com/live/asr/stream/ws/meeting_remote",
            "--session-id",
            "meeting_remote",
            "--ready-file",
            str(tmp_path / "ready.json"),
            "--no-request-permission",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )

    assert completed.returncode != 0
    error = json.loads(completed.stderr.strip().splitlines()[-1])
    assert error["error_code"] == "non_loopback_transport_blocked"
    assert error["captured_audio"] is False
    assert error["fallback_source"] is None
    assert not (tmp_path / "ready.json").exists()


def test_helper_and_app_plists_bind_screen_audio_permission_to_fixed_product_identity():
    helper_plist = plistlib.loads(INFO_PLIST.read_bytes())
    app_plist = plistlib.loads((DESKTOP_ROOT / "src-tauri/Info.plist").read_bytes())

    assert helper_plist["CFBundleIdentifier"] == "com.meetingcopilot.desktop.native-system-audio"
    assert helper_plist["LSMinimumSystemVersion"] == "13.0"
    assert helper_plist["NSAudioCaptureUsageDescription"]
    assert helper_plist["NSScreenCaptureUsageDescription"]
    assert app_plist["NSAudioCaptureUsageDescription"]
    assert app_plist["NSScreenCaptureUsageDescription"]


def test_runtime_manifest_and_tauri_register_one_system_audio_source_runtime():
    manifest = json.loads((DESKTOP_ROOT / "runtime-bundle-manifest.json").read_text(encoding="utf-8"))
    lib_source = (DESKTOP_ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    command_manifest = (DESKTOP_ROOT / "src-tauri/src/app_command_manifest.rs").read_text(
        encoding="utf-8"
    )

    assert "bin/meeting-copilot-native-system-audio" in manifest["required_files"]
    assert "pub mod native_system_audio_capture_runtime;" in lib_source
    assert 'runtime_bundle.join("bin/meeting-copilot-native-system-audio")' in lib_source
    assert "system_audio_adapter_start" in command_manifest
    assert "system_audio_adapter_collect_events" in command_manifest
    assert "system_audio_adapter_stop" in command_manifest
    assert "system_audio.is_active()" in lib_source
    assert "microphone.status()" in lib_source
    assert "app.manage(native_system_audio);" in lib_source


def test_packaged_gate_rejects_an_executable_script_in_place_of_packaged_macho(tmp_path):
    gate = _load_packaged_gate()
    app = tmp_path / "Meeting Copilot.app"
    contents = app / "Contents"
    helper = contents / "Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-native-system-audio"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
    helper.chmod(0o755)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.meetingcopilot.desktop",
                "CFBundleName": "Meeting Copilot",
            }
        )
    )

    with pytest.raises(ValueError, match="Mach-O"):
        gate.inspect_packaged_helper(app)


def test_runtime_bundle_builder_compiles_and_protocol_probes_the_real_helper(tmp_path):
    if platform.system() != "Darwin":
        pytest.skip("native system-audio helper builds only on macOS")
    tool = _load_bundle_tool()
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"

    command = tool.native_system_audio_build_command(REPO_ROOT, bundle)
    assert command[:2] == ["xcrun", "swiftc"]
    assert "ScreenCaptureKit" in command
    assert command[-1].endswith("bin/meeting-copilot-native-system-audio")

    tool.build_native_system_audio_helper(REPO_ROOT, bundle)
    helper = bundle / "bin/meeting-copilot-native-system-audio"
    assert helper.read_bytes()[:4] in {
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }
    probe = tool.probe_native_system_audio(bundle)
    assert probe["status"] == "passed"
    assert probe["protocol"]["capture_framework"] == "ScreenCaptureKit"
    assert probe["protocol"]["accepts_remote_websocket"] is False


def test_swift_source_has_real_content_selection_pcm_events_stop_and_no_file_sink():
    source = SOURCE.read_text(encoding="utf-8")

    for required in (
        "SCShareableContent",
        "SCContentFilter",
        "SCStreamConfiguration",
        "SCStreamOutput",
        "AVAudioConverter",
        '"event_type": "pcm"',
        '"input_peak_sample": frame.inputPeakSample',
        '"input_peak_metric_status": frame.inputPeakMetricStatus',
        '"nonzero_pcm_event_count": result.nonzeroPCMEventCount',
        '"input_common_format": result.inputCommonFormat',
        '"input_byte_count": result.inputByteCount',
        '"input_buffer_byte_counts": result.inputBufferByteCounts',
        '"input_format_id_fourcc": result.inputFormatIDFourCC',
        '"input_format_flags": result.inputFormatFlags',
        '"input_bits_per_channel": result.inputBitsPerChannel',
        '"input_bytes_per_frame": result.inputBytesPerFrame',
        '"raw_nonzero_byte_count": result.rawNonzeroByteCount',
        '"raw_byte_metric_status": result.rawByteMetricStatus',
        "stopCapture()",
        'task.send(.data(envelope))',
        'task.send(.string("END"))',
        'host == "127.0.0.1"',
    ):
        assert required in source
    assert "AVAudioFile" not in source
    assert "URLSession.shared.upload" not in source


def test_ready_file_requires_a_successfully_transported_complete_pcm_frame():
    source = SOURCE.read_text(encoding="utf-8")

    assert "try await waitForTransportedPCMAndWriteReady(selection)" in source
    assert "recordSuccessfulPCMSend(byteCount: data.count, rms: frame.rms)" in source
    assert '"transport_ready": startup.transportReady' in source
    assert '"pcm_seen": startup.pcmSeen' in source
    assert '"audible_pcm_seen": startup.audiblePCMSeen' in source
    assert '"first_pcm_rms": startup.firstPCMRMS' in source
    assert '"pcm_bytes_sent": startup.pcmBytesSent' in source
    assert "if snapshot.transportReady && snapshot.pcmSeen" in source
    assert "try writeReady(selection)" not in source


def test_websocket_binary_transport_failure_is_fatal_instead_of_log_only():
    source = SOURCE.read_text(encoding="utf-8")

    assert "private func sendBinary(" in source
    assert "sequence: UInt64" in source
    assert "finalPartial: Bool" in source
    assert "let envelope = try nativePCMEnvelope(" in source
    assert 'throw NativeSystemAudioError.websocket("binary PCM send timed out")' in source
    assert 'throw NativeSystemAudioError.websocket("binary PCM send failed:' in source
    assert "self.captureFailed(error)" in source
    assert "removeReadyFile()" in source
    assert 'diagnostic("stage=websocket_send_timeout")' not in source


def test_raw_pcm_diagnostics_distinguish_unavailable_metrics_from_zero():
    source = SOURCE.read_text(encoding="utf-8")

    assert "CMAudioFormatDescriptionGetStreamBasicDescription" in source
    assert "UnsafeRawBufferPointer" in source
    assert 'status: metricAvailable ? "available" : "metric_unavailable"' in source
    assert 'PeakSampleMetric(value: nil, status: "metric_unavailable")' in source
    assert 'frame.inputPeakSample ?? NSNull()' in source
    assert 'frame.rawNonzeroByteCount ?? NSNull()' in source
