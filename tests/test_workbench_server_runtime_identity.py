import importlib.util
import json
import os
import signal
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "workbench_server.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "workbench_server_runtime_identity", TOOL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install_runtime_routes(
    monkeypatch, tool, routes: dict[str, tuple[int, str, bytes]]
):
    requests: list[str] = []

    def local_response_bytes(*, port, path, timeout_seconds, max_bytes):
        del port, timeout_seconds
        requests.append(path)
        status, _content_type, body = routes.get(
            path,
            (404, "application/json", b'{"detail":"not found"}'),
        )
        if status != 200:
            raise OSError("local runtime probe returned a non-success status")
        if len(body) > max_bytes:
            raise OSError("local runtime probe exceeded its response limit")
        return body

    monkeypatch.setattr(tool, "_local_response_bytes", local_response_bytes)
    return requests


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _clean_runtime_routes(tool, *, health_extra: dict[str, object] | None = None):
    health = {"status": "ok", "service": tool.HEALTH_SERVICE, **(health_extra or {})}
    return {
        "/health": (200, "application/json", _json_bytes(health)),
        "/v2/diagnostics/application-schema": (
            200,
            "application/json",
            _json_bytes(
                {"schema_version": tool.APPLICATION_SCHEMA_VERSION, "status": "ready"}
            ),
        ),
        "/workbench": (
            200,
            "text/html; charset=utf-8",
            tool.FRONTEND_V2_INDEX.read_bytes(),
        ),
    }


def test_current_clean_workbench_runtime_identity_is_accepted(tmp_path, monkeypatch):
    tool = _load_tool()
    port = 18765
    pid_file = tmp_path / "workbench.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(tool, "process_start_marker", lambda pid: "a" * 64)
    requests = _install_runtime_routes(monkeypatch, tool, _clean_runtime_routes(tool))
    tool.write_runtime_identity(pid_file=pid_file, pid=os.getpid(), port=port)
    monkeypatch.setattr(
        tool.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("verified current runtime must be reused"),
    )

    report = tool.start_server(
        port=port,
        pid_file=pid_file,
        log_file=tmp_path / "workbench.log",
        data_dir=tmp_path / "data",
    )

    assert report["status"] == "already_running"
    assert report["health_ok"] is True
    assert report["runtime_identity_verified"] is True
    assert report["health"]["runtime_identity"]["verified"] is True
    assert report["health"]["runtime_identity"]["reason"] == "verified"
    assert requests == ["/health", "/v2/diagnostics/application-schema", "/workbench"]
    assert tool.runtime_identity_file(pid_file).stat().st_mode & 0o777 == 0o600


def test_old_meeting_copilot_health_is_rejected_without_termination(
    tmp_path, monkeypatch
):
    tool = _load_tool()
    port = 18766
    routes = {
        "/health": (
            200,
            "application/json",
            _json_bytes({"status": "ok", "service": tool.HEALTH_SERVICE}),
        ),
    }
    monkeypatch.setattr(
        tool.os,
        "kill",
        lambda *args, **kwargs: pytest.fail(
            "an old unowned runtime must never be signaled"
        ),
    )
    monkeypatch.setattr(
        tool.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail(
            "an occupied foreign port must not spawn a replacement"
        ),
    )
    monkeypatch.setattr(tool, "is_port_open", lambda candidate_port: True)
    requests = _install_runtime_routes(monkeypatch, tool, routes)

    report = tool.start_server(
        port=port,
        pid_file=tmp_path / "missing.pid",
        log_file=tmp_path / "workbench.log",
        data_dir=tmp_path / "data",
    )

    assert report["status"] == "blocked_port_in_use"
    assert report["safe_to_kill_existing_process"] is False
    assert report["runtime_identity"]["verified"] is False
    assert (
        report["runtime_identity"]["reason"]
        == "application_schema_contract_unavailable"
    )
    assert requests == ["/health", "/v2/diagnostics/application-schema"]


def test_foreign_process_mimicking_public_runtime_contract_is_rejected_without_termination(
    tmp_path,
    monkeypatch,
):
    tool = _load_tool()
    port = 18767
    private_health_value = "must-not-escape-public-diagnostics"
    routes = _clean_runtime_routes(
        tool,
        health_extra={
            "instance_proof": private_health_value,
            "unexpected_content": private_health_value,
        },
    )
    monkeypatch.setattr(
        tool.os,
        "kill",
        lambda *args, **kwargs: pytest.fail(
            "an unowned runtime must never be signaled"
        ),
    )
    monkeypatch.setattr(
        tool.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail(
            "an occupied foreign port must not spawn a replacement"
        ),
    )
    monkeypatch.setattr(tool, "is_port_open", lambda candidate_port: True)
    requests = _install_runtime_routes(monkeypatch, tool, routes)

    report = tool.start_server(
        port=port,
        pid_file=tmp_path / "missing.pid",
        log_file=tmp_path / "workbench.log",
        data_dir=tmp_path / "data",
    )

    assert report["status"] == "blocked_port_in_use"
    assert report["runtime_identity"]["reason"] == "managed_pid_missing"
    assert report["runtime_identity"]["checks"]["workbench_asset_provenance"] is True
    assert report["runtime_identity"]["checks"]["managed_launch_record"] is False
    assert private_health_value not in json.dumps(report)
    assert requests == [
        "/health",
        "/v2/diagnostics/application-schema",
        "/workbench",
    ]


def test_stop_rejects_foreign_pid_record_without_sending_termination_signal(
    tmp_path, monkeypatch
):
    tool = _load_tool()
    pid_file = tmp_path / "foreign.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    real_kill = os.kill
    observed_signals: list[int] = []

    def guarded_kill(pid: int, sent_signal: int):
        observed_signals.append(sent_signal)
        if sent_signal != 0:
            pytest.fail(
                "stop must not terminate a PID without the managed launch record"
            )
        return real_kill(pid, sent_signal)

    monkeypatch.setattr(tool.os, "kill", guarded_kill)

    report = tool.stop_server(pid_file=pid_file, timeout_seconds=0)

    assert report["status"] == "blocked_foreign_process"
    assert report["safe_to_kill_existing_process"] is False
    assert (
        report["runtime_identity"]["reason"]
        == "managed_launch_record_missing_or_invalid"
    )
    assert observed_signals and set(observed_signals) == {0}
    assert pid_file.exists()


def test_start_failure_terminates_owned_child_and_removes_its_records(
    tmp_path, monkeypatch
):
    tool = _load_tool()
    pid_file = tmp_path / "failed.pid"

    class StartedProcess:
        pid = 43210
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -signal.SIGTERM

        def wait(self, timeout):
            assert timeout == tool.STOP_TIMEOUT_SECONDS
            return self.returncode

        def kill(self):
            pytest.fail("a responsive owned child must not require a forced kill")

    process = StartedProcess()
    monkeypatch.setattr(tool.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(tool, "check_health", lambda *args, **kwargs: {"ok": False})
    monkeypatch.setattr(tool, "is_port_open", lambda port: False)
    monkeypatch.setattr(tool, "process_start_marker", lambda pid: "a" * 64)
    timestamps = iter((0, 21))
    monkeypatch.setattr(tool.time, "time", lambda: next(timestamps))

    report = tool.start_server(
        port=18768,
        pid_file=pid_file,
        log_file=tmp_path / "workbench.log",
        data_dir=tmp_path / "data",
    )

    assert report["status"] == "blocked_start_failed"
    assert report["owned_process_cleanup"] == "complete"
    assert process.terminated is True
    assert not pid_file.exists()
    assert not tool.runtime_identity_file(pid_file).exists()


def test_stop_allows_owned_process_after_current_source_changes(tmp_path, monkeypatch):
    tool = _load_tool()
    pid = os.getpid()
    pid_file = tmp_path / "source-stale.pid"
    pid_file.write_text(str(pid), encoding="utf-8")
    monkeypatch.setattr(tool, "process_start_marker", lambda candidate_pid: "a" * 64)
    tool.write_runtime_identity(pid_file=pid_file, pid=pid, port=18769)
    monkeypatch.setattr(tool, "runtime_source_fingerprint", lambda: "b" * 64)
    terminated = False
    observed_signals: list[int] = []

    def owned_kill(candidate_pid: int, sent_signal: int):
        nonlocal terminated
        assert candidate_pid == pid
        observed_signals.append(sent_signal)
        if sent_signal == signal.SIGTERM:
            terminated = True
        elif sent_signal == 0 and terminated:
            raise ProcessLookupError

    monkeypatch.setattr(tool.os, "kill", owned_kill)

    report = tool.stop_server(pid_file=pid_file, timeout_seconds=1)

    assert report["status"] == "stopped"
    assert signal.SIGTERM in observed_signals
    assert not pid_file.exists()
    assert not tool.runtime_identity_file(pid_file).exists()
