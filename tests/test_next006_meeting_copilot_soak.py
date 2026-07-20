from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import sys
import threading
import wave

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import next006_meeting_copilot_soak as soak  # noqa: E402


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("content-length", "0"))
        self.rfile.read(size)
        body = json.dumps(
            {
                "id": "response-real-upstream",
                "output": [{"content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def upstream_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_parser_exposes_only_meeting_copilot_targets_and_no_arbitrary_command():
    parser = soak.build_parser()
    help_text = parser.format_help()

    assert "packaged-app" in help_text
    assert "runtime-bundle" in help_text
    assert "--audio-path" in help_text
    assert "--backend-command" not in help_text
    assert "--evidence-kind" not in help_text
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "fixture"])


def test_packaged_target_provenance_binds_binary_manifest_and_package_evidence(
    tmp_path,
):
    app = tmp_path / "Meeting Copilot.app"
    binary = app / "Contents/MacOS/meeting-copilot-desktop"
    manifest = (
        app
        / "Contents/Resources/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
    )
    binary.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    binary.write_bytes(b"real-meeting-copilot-binary")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "meeting_copilot.runtime_bundle.v1",
                "runtimes": {"backend": {"executable": "runtime/python"}},
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "meeting_copilot.tauri_runtime_package.v1",
                "app_path": str(app),
                "app_binary": {
                    "path": "Contents/MacOS/meeting-copilot-desktop",
                    "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                },
                "packaged_runtime_manifest": json.loads(manifest.read_text()),
            }
        ),
        encoding="utf-8",
    )

    proof = soak.verify_packaged_target(app, evidence, verify_code_signature=False)

    assert proof["target_kind"] == "meeting_copilot_packaged_app"
    assert proof["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert (
        proof["runtime_manifest_sha256"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert (
        proof["package_evidence_sha256"]
        == hashlib.sha256(evidence.read_bytes()).hexdigest()
    )
    assert proof["provenance_verified"] is True

    evidence.write_text(evidence.read_text().replace(proof["binary_sha256"], "0" * 64))
    with pytest.raises(ValueError, match="binary hash"):
        soak.verify_packaged_target(app, evidence, verify_code_signature=False)

    evidence_payload = json.loads(evidence.read_text())
    evidence_payload["app_binary"]["sha256"] = proof["binary_sha256"]
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "meeting_copilot.runtime_bundle.v1",
                "runtimes": {"backend": {"executable": "runtime/changed-python"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="runtime manifest"):
        soak.verify_packaged_target(app, evidence, verify_code_signature=False)


def test_provider_fault_relay_uses_real_upstream_and_records_disconnect_429_5xx(
    upstream_url,
):
    relay = soak.ProviderFaultRelay(upstream_base_url=upstream_url)
    relay.start()
    try:
        client = soak.JsonHttpClient(relay.base_url, token=None)
        relay.set_mode("disconnect")
        disconnected = client.request("POST", "/v1/responses", {"model": "real"})
        assert disconnected.status_code is None

        relay.set_mode("429")
        assert (
            client.request("POST", "/v1/responses", {"model": "real"}).status_code
            == 429
        )
        relay.set_mode("500")
        assert (
            client.request("POST", "/v1/responses", {"model": "real"}).status_code
            == 500
        )
        relay.set_mode("503")
        assert (
            client.request("POST", "/v1/responses", {"model": "real"}).status_code
            == 503
        )

        relay.set_mode("passthrough")
        recovered = client.request("POST", "/v1/responses", {"model": "real"})
        assert recovered.status_code == 200
        assert recovered.json_body["usage"]["total_tokens"] == 4

        status = relay.snapshot()
        assert status["mode_counts"] == {
            "disconnect": 1,
            "429": 1,
            "500": 1,
            "503": 1,
            "passthrough": 1,
        }
        assert status["forwarded_request_count"] == 1
        assert status["canned_success_response_count"] == 0
    finally:
        relay.stop()


def test_acceptance_keeps_sleep_and_microphone_switch_as_independent_blockers():
    report = soak.new_report_skeleton(
        run_id="next006-real-sut",
        duration_seconds=3_600,
        target_provenance={"provenance_verified": True},
    )
    report["automated_gates"] = {
        name: {"status": "passed"} for name in soak.REQUIRED_AUTOMATED_GATES
    }
    report["wall_clock_elapsed_seconds"] = 3_600
    report["soak_wall_clock_elapsed_seconds"] = 3_600
    report["faults"] = [
        {"fault": name, "status": "passed", "observed": True, "recovered": True}
        for name in soak.FAULT_NAMES
    ]

    evaluated = soak.evaluate_report(report)

    assert evaluated["automated_acceptance_eligible"] is True
    assert evaluated["next006_overall_eligible"] is False
    assert (
        evaluated["independent_gates"]["mac_sleep_wake"]["status"]
        == "blocked_manual_required"
    )
    assert (
        evaluated["independent_gates"]["microphone_device_switch"]["status"]
        == "blocked_manual_required"
    )
    assert set(evaluated["overall_blockers"]) == {
        "independent_gate_not_passed:mac_sleep_wake",
        "independent_gate_not_passed:microphone_device_switch",
    }


def test_short_or_fixture_shaped_reports_cannot_be_automated_acceptance():
    report = soak.new_report_skeleton(
        run_id="short",
        duration_seconds=30,
        target_provenance={"provenance_verified": False},
    )
    report["automated_gates"] = {
        name: {"status": "passed"} for name in soak.REQUIRED_AUTOMATED_GATES
    }

    evaluated = soak.evaluate_report(report)

    assert evaluated["automated_acceptance_eligible"] is False
    assert "duration_is_not_1h_or_3h" in evaluated["automated_blockers"]
    assert "target_provenance_not_verified" in evaluated["automated_blockers"]


def test_recording_gate_requires_growth_continuous_input_chunks_journal_and_assembled_file():
    stream = {
        "bootstrap_status": 303,
        "continuous": True,
        "ready_stream_count": 120,
        "non_empty_final_count": 120,
        "estimated_audio_sent_seconds": 3_590,
    }
    baseline = {
        "status_code": 200,
        "chunk_count": 0,
        "durable_duration_ms": 0,
        "journal_sha256": [],
    }
    recording = {
        "status_code": 200,
        "status": "saved",
        "assembled": True,
        "file_size_bytes": 64_044,
        "chunk_count": 3,
        "duration_ms": 3_590_000,
        "durable_duration_ms": 3_590_000,
        "journal_sha256": ["a" * 64],
        "export_statuses": ["succeeded"],
    }

    assert (
        soak._recording_gate(baseline, recording, stream, 3_600)["status"] == "passed"
    )

    empty = {**recording, "chunk_count": 0, "journal_sha256": []}
    failed = soak._recording_gate(baseline, empty, stream, 3_600)
    assert failed["status"] == "failed"
    assert "durable_audio_chunks_missing" in failed["blockers"]
    assert "recording_journal_hash_missing" in failed["blockers"]

    non_continuous = {**stream, "continuous": False, "estimated_audio_sent_seconds": 30}
    failed = soak._recording_gate(baseline, recording, non_continuous, 3_600)
    assert failed["status"] == "failed"
    assert "continuous_audio_input_not_observed" in failed["blockers"]


def test_queue_gate_rejects_empty_active_or_failed_durable_jobs():
    empty = {
        "contract_present": True,
        "total": 0,
        "by_status": {},
        "kinds": [],
        "review_kinds": [],
        "job_ids": [],
    }
    assert soak._queue_gate(empty)["status"] == "failed"

    active = {
        **empty,
        "total": 3,
        "by_status": {"running": 1, "succeeded": 2},
        "kinds": ["minutes", "approach", "index"],
        "review_kinds": ["minutes", "approach", "index"],
        "job_ids": ["minutes-1", "approach-1", "index-1"],
    }
    assert soak._queue_gate(active)["status"] == "failed"

    completed = {
        **active,
        "by_status": {"succeeded": 3},
    }
    assert soak._queue_gate(completed)["status"] == "passed"


def test_crash_recovery_requires_non_regressing_persistent_state():
    before = {
        "persistence": {
            "meeting_id": "meeting-1",
            "meeting_id_matches": True,
            "title": "Persistent title",
            "last_seq": 12,
        },
        "recording": {
            "chunk_count": 4,
            "durable_duration_ms": 8_000,
            "journal_sha256": ["a" * 64],
        },
        "queue": {"total": 2, "job_ids": ["job-1", "job-2"]},
    }
    preserved = {
        "persistence": {**before["persistence"], "last_seq": 13},
        "recording": {**before["recording"], "chunk_count": 5},
        "queue": {"total": 2, "job_ids": ["job-1", "job-2"]},
    }

    assert soak._durable_state_checks(before, preserved)["passed"] is True

    regressed = {
        "persistence": {**before["persistence"], "last_seq": 11},
        "recording": {**before["recording"], "chunk_count": 0},
        "queue": {"total": 1, "job_ids": ["job-2"]},
    }
    checks = soak._durable_state_checks(before, regressed)
    assert checks["passed"] is False
    assert checks["last_seq_non_regressing"] is False
    assert checks["recording_chunks_non_regressing"] is False
    assert checks["job_ids_preserved"] is False


def test_evaluate_report_rejects_short_wall_clock_and_unobserved_faults():
    report = soak.new_report_skeleton(
        run_id="forged-gates",
        duration_seconds=3_600,
        target_provenance={"provenance_verified": True},
    )
    report["automated_gates"] = {
        name: {"status": "passed"} for name in soak.REQUIRED_AUTOMATED_GATES
    }
    report["wall_clock_elapsed_seconds"] = 30
    report["soak_wall_clock_elapsed_seconds"] = 30
    report["faults"] = [
        {"fault": name, "status": "passed", "observed": True, "recovered": True}
        for name in soak.FAULT_NAMES
        if name != "backend-crash"
    ] + [
        {
            "fault": "backend-crash",
            "status": "passed",
            "observed": False,
            "recovered": True,
        }
    ]

    evaluated = soak.evaluate_report(report)

    assert evaluated["automated_acceptance_eligible"] is False
    assert (
        "wall_clock_elapsed_shorter_than_requested" in evaluated["automated_blockers"]
    )
    assert (
        "required_fault_not_observed:backend-crash" in evaluated["automated_blockers"]
    )


def _tiny_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * 5)


class _FakeWebSocket:
    def __init__(self, *, disconnect_on_first_chunk: bool = False) -> None:
        self.disconnect_on_first_chunk = disconnect_on_first_chunk
        self.binary_chunks: list[bytes] = []
        self.text_messages: list[str] = []
        self.closed = False
        self.ready_sent = False
        self.end_sent = False
        self.final_sent = False
        self.driver = None

    def settimeout(self, _timeout: float) -> None:
        return

    def recv(self) -> str:
        if not self.ready_sent:
            self.ready_sent = True
            return json.dumps({"event_type": "asr_ready", "ready": True})
        if self.end_sent and not self.final_sent:
            self.final_sent = True
            return json.dumps(
                {"event_type": "final", "normalized_text": "受控测试文本"}
            )
        raise soak.websocket.WebSocketTimeoutException()

    def send_binary(self, chunk: bytes) -> None:
        if self.disconnect_on_first_chunk:
            raise soak.websocket.WebSocketConnectionClosedException(
                "injected disconnect"
            )
        self.binary_chunks.append(chunk)
        if len(self.binary_chunks) == 5:
            self.driver._stop.set()

    def send(self, message: str) -> None:
        self.text_messages.append(message)
        self.end_sent = message == "END"

    def close(self) -> None:
        self.closed = True


def test_continuous_audio_generation_loops_pcm_and_sends_end_only_once_at_soak_stop(
    monkeypatch, tmp_path
):
    audio_path = tmp_path / "tiny.wav"
    _tiny_wav(audio_path)
    driver = soak.ContinuousAudioDriver(
        target=object(),
        meeting_id="next006_continuous",
        audio_path=audio_path,
        requested_duration_seconds=1,
    )
    socket = _FakeWebSocket()
    socket.driver = driver
    monkeypatch.setattr(
        soak.websocket, "create_connection", lambda *_args, **_kwargs: socket
    )

    driver._stream_generation(12345, "meeting_copilot_session=test")

    assert len(socket.binary_chunks) == 5
    assert socket.text_messages == ["END"]
    assert socket.closed is True
    assert driver._ready_stream_count == 1
    assert driver._estimated_audio_sent_seconds == pytest.approx(25 / 16_000)


def test_continuous_audio_disconnect_does_not_send_end_or_count_unsent_pcm(
    monkeypatch, tmp_path
):
    audio_path = tmp_path / "tiny.wav"
    _tiny_wav(audio_path)
    driver = soak.ContinuousAudioDriver(
        target=object(),
        meeting_id="next006_disconnect",
        audio_path=audio_path,
        requested_duration_seconds=1,
    )
    socket = _FakeWebSocket(disconnect_on_first_chunk=True)
    monkeypatch.setattr(
        soak.websocket, "create_connection", lambda *_args, **_kwargs: socket
    )

    with pytest.raises(soak.websocket.WebSocketConnectionClosedException):
        driver._stream_generation(12345, "meeting_copilot_session=test")

    assert socket.binary_chunks == []
    assert socket.text_messages == []
    assert socket.closed is True
    assert driver._estimated_audio_sent_seconds == 0


def test_continuous_audio_preserves_sanitized_server_error_category(tmp_path):
    audio_path = tmp_path / "tiny.wav"
    _tiny_wav(audio_path)
    driver = soak.ContinuousAudioDriver(
        target=object(),
        meeting_id="next006_error_category",
        audio_path=audio_path,
        requested_duration_seconds=1,
    )

    class ErrorWebSocket:
        def settimeout(self, _timeout):
            return None

        def recv(self):
            return json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": "recording_resume_failed",
                }
            )

    with pytest.raises(soak.ContinuousAudioProtocolError) as raised:
        driver._drain_ws(ErrorWebSocket(), timeout=0.1)

    assert raised.value.category == "asr_stream_provider_error_recording_resume_failed"


def test_backend_crash_gate_requires_post_fault_asr_and_audio_growth(monkeypatch):
    state = {"persistence": {"last_seq": 7}}
    monkeypatch.setattr(soak, "_probe_state", lambda _target, _meeting_id: state)
    monkeypatch.setattr(
        soak,
        "_durable_state_checks",
        lambda _before, _after: {"passed": True},
    )
    monkeypatch.setattr(soak, "pid_exists", lambda _pid: False)

    class Target:
        recovery_owner = "tauri_backend_supervisor"

        def kill_backend_for_fault(self):
            return 100, 100

        def recover_backend(self, _old_pid):
            return {"pid": 200}

    class AudioDriver:
        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls == 1:
                return {
                    "ready_stream_count": 1,
                    "estimated_audio_sent_seconds": 4.0,
                }
            return {
                "ready_stream_count": 2,
                "estimated_audio_sent_seconds": 4.3,
            }

    result = soak._run_backend_crash(Target(), "meeting", AudioDriver())

    assert result["status"] == "passed"
    assert result["post_fault_asr_ready_observed"] is True
    assert result["post_fault_audio_growth_observed"] is True
