from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SWIFT_SOURCE = (
    REPO_ROOT
    / "code/desktop_tauri/native_mic/Sources/MeetingCopilotNativeMic/main.swift"
)
RUST_SOURCE = (
    REPO_ROOT
    / "code/desktop_tauri/src-tauri/src/native_mic_capture_runtime.rs"
)


def test_swift_ready_waits_for_a_successfully_transported_complete_pcm_frame():
    source = SWIFT_SOURCE.read_text(encoding="utf-8")

    assert "try await waitForTransportedPCMAndWriteReady()" in source
    assert "if startup.transportReady && startup.pcmSeen" in source
    assert "try self.sendBinary(" in source
    assert "self.recordSuccessfulPCMSend(" in source
    assert source.index("try self.sendBinary(") < source.index(
        "self.recordSuccessfulPCMSend("
    )
    assert "writeReady()" not in source


def test_swift_ready_exposes_native_pcm_transport_and_signal_facts():
    source = SWIFT_SOURCE.read_text(encoding="utf-8")

    for field in (
        '"transport_ready": startup.transportReady',
        '"pcm_seen": startup.pcmSeen',
        '"audible_pcm_seen": startup.audiblePCMSeen',
        '"first_pcm_rms": startup.firstPCMRMS',
        '"pcm_bytes_sent": startup.pcmBytesSent',
        '"pcm_protocol": nativePCMProtocolName',
        '"capture_epoch": configuration.captureEpoch',
    ):
        assert field in source
    assert 'private let nativePCMProtocolName = "native_pcm_v2"' in source


def test_transport_failure_removes_ready_and_rust_rejects_legacy_ready_files():
    swift_source = SWIFT_SOURCE.read_text(encoding="utf-8")
    rust_source = RUST_SOURCE.read_text(encoding="utf-8")

    failure_body = swift_source.split("private func failTransport", maxsplit=1)[1].split(
        "private func waitForTransportedPCMAndWriteReady", maxsplit=1
    )[0]
    assert "transportFailed = true" in failure_body
    assert "removeReadyFile()" in failure_body
    assert "exit(70)" in failure_body

    for required in (
        "payload.transport_ready",
        "payload.pcm_seen",
        "payload.first_pcm_rms",
        "payload.pcm_bytes_sent >= 4_800",
        'payload.pcm_protocol == "native_pcm_v2"',
        "payload.capture_epoch == expected_capture_epoch",
    ):
        assert required in rust_source
