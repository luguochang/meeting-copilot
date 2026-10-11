from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Lock, Timer
import time

import pytest


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("public_report_atomic_runner", ROOT / "tools/participation_continuous_acceptance.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="real Windows file sharing semantics")


@contextmanager
def _deny_delete_reader(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    guard, released = Lock(), False

    def release():
        nonlocal released
        with guard:
            if not released:
                assert kernel.CloseHandle(handle)
                released = True

    try:
        yield release
    finally:
        release()


def _public_report(tmp_path):
    path = tmp_path / "report.json"
    runner.fresh_json(path, {"public_phase": "old"})
    return path, path.read_bytes()


def _os_error(number=errno.EACCES, winerror=None):
    error = OSError(number, "private-message/body", "private-filename")
    if winerror is not None:
        error.winerror = winerror
    return error


@WINDOWS
def test_real_short_lived_deny_delete_reader_allows_one_atomic_publication_without_rewriting_bytes(tmp_path, monkeypatch):
    path, old = _public_report(tmp_path)
    temporary = path.with_suffix(".json.tmp")
    writes, old_observations = [], []
    write_text, replace = Path.write_text, Path.replace

    def write_once(target, text, *args, **kwargs):
        writes.append(target)
        return write_text(target, text, *args, **kwargs)

    def atomic_replace(target, destination):
        old_observations.append(path.read_bytes() == old)
        return replace(target, destination)

    monkeypatch.setattr(Path, "write_text", write_once)
    monkeypatch.setattr(Path, "replace", atomic_replace)
    with _deny_delete_reader(path) as release:
        timer = Timer(0.035, release)
        timer.start()
        try:
            runner.update_json(path, {"public_phase": "new"})
        finally:
            timer.join(timeout=1)
    assert writes == [temporary]
    assert len(old_observations) >= 2 and all(old_observations)
    assert json.loads(path.read_text(encoding="utf-8")) == {"public_phase": "new"}
    assert not temporary.exists()


@WINDOWS
def test_real_persistent_deny_delete_reader_exhausts_a_small_bound_and_keeps_the_old_valid_report(tmp_path, capsys):
    path, old = _public_report(tmp_path)
    with _deny_delete_reader(path):
        started = time.monotonic()
        with pytest.raises(runner.ReportFileSystemError) as failed:
            runner.update_json(path, {"public_phase": "new"})
        elapsed = time.monotonic() - started
        assert elapsed <= 0.200
        assert path.read_bytes() == old
    assert json.loads(path.with_suffix(".json.tmp").read_text(encoding="utf-8")) == {"public_phase": "new"}
    diagnostic = failed.value.diagnostic
    assert diagnostic["phase"] == "report_atomic_replace" and diagnostic["winerror"] in {5, 32, 33}
    assert json.loads(capsys.readouterr().out) == diagnostic
    runner.update_json(path, {"public_phase": "released"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"public_phase": "released"}


@WINDOWS
@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_recognized_replace_conflict_can_clear_without_replaying_the_write(tmp_path, monkeypatch, winerror):
    path, old = _public_report(tmp_path)
    original_replace, write_text = Path.replace, Path.write_text
    attempts, writes = [], []

    def replace_once(target, destination):
        attempts.append(target)
        assert path.read_bytes() == old
        if len(attempts) == 1:
            raise _os_error(winerror=winerror)
        return original_replace(target, destination)

    def write_once(target, text, *args, **kwargs):
        writes.append(target)
        return write_text(target, text, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", replace_once)
    monkeypatch.setattr(Path, "write_text", write_once)
    runner.update_json(path, {"public_phase": "new"})
    assert len(attempts) == 2 and len(writes) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"public_phase": "new"}


@WINDOWS
@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_persistent_windows_replace_failure_is_not_treated_as_success(tmp_path, monkeypatch, winerror):
    path, old = _public_report(tmp_path)
    attempts = []

    def permanently_fail(target, destination):
        attempts.append(target)
        raise _os_error(winerror=winerror)

    monkeypatch.setattr(Path, "replace", permanently_fail)
    started = time.monotonic()
    with pytest.raises(runner.ReportFileSystemError) as failed:
        runner.update_json(path, {"public_phase": "new"})
    assert time.monotonic() - started <= 0.200
    assert 1 < len(attempts) <= 5
    assert path.read_bytes() == old
    assert failed.value.diagnostic == {"code": "report_filesystem_failure", "phase": "report_atomic_replace",
                                       "errno": errno.EACCES, "winerror": winerror}


@pytest.mark.parametrize(("number", "winerror"), [
    (errno.EACCES, None), (errno.EIO, None), (errno.ENOENT, None),
    (errno.ENOSPC, 112), (errno.EIO, 23),
])
def test_permissions_paths_and_other_os_errors_fail_immediately_without_exposing_private_content(tmp_path, monkeypatch, capsys, number, winerror):
    path, old = _public_report(tmp_path)
    attempts = []

    def fail(target, destination):
        attempts.append(target)
        raise _os_error(number, winerror)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(runner.ReportFileSystemError) as failed:
        runner.update_json(path, {"public_phase": "new"})
    assert len(attempts) == 1 and path.read_bytes() == old
    expected = {"code": "report_filesystem_failure", "phase": "report_atomic_replace",
                "errno": number, "winerror": winerror}
    assert failed.value.diagnostic == expected
    output = capsys.readouterr().out
    assert json.loads(output) == expected
    assert "private" not in output + str(failed.value)
    assert str(path) not in output


def test_temporary_write_permission_failure_is_never_retried_or_misclassified_as_a_replace_conflict(tmp_path, monkeypatch, capsys):
    path, old = _public_report(tmp_path)
    writes = []

    def deny_write(target, text, *args, **kwargs):
        writes.append(target)
        raise _os_error(winerror=5)

    monkeypatch.setattr(Path, "write_text", deny_write)
    monkeypatch.setattr(Path, "replace", lambda *_: pytest.fail("replace after denied temporary write"))
    with pytest.raises(runner.ReportFileSystemError) as failed:
        runner.update_json(path, {"public_phase": "new"})
    assert len(writes) == 1 and path.read_bytes() == old
    assert not path.with_suffix(".json.tmp").exists()
    assert failed.value.diagnostic == {"code": "report_filesystem_failure", "phase": "report_temporary_write",
                                       "errno": errno.EACCES, "winerror": 5}
    assert json.loads(capsys.readouterr().out) == failed.value.diagnostic


def test_diagnostics_keep_only_fixed_phases_and_numeric_error_codes():
    error = _os_error()
    error.errno = "private-errno"
    error.winerror = True
    failure = runner.ReportFileSystemError("report_atomic_replace", error)
    assert failure.diagnostic == {"code": "report_filesystem_failure", "phase": "report_atomic_replace",
                                  "errno": None, "winerror": None}
    with pytest.raises(ValueError, match="phase is not allowed"):
        runner.ReportFileSystemError("private-operation/path", error)


@WINDOWS
def test_actual_emit_waits_for_the_report_lock_without_duplicate_journal_or_business_event(tmp_path, capsys):
    path, _ = _public_report(tmp_path)
    observer = runner.ContinuousObserver.__new__(runner.ContinuousObserver)
    observer.report_path, observer.report, observer.mid = path, {}, "public-fixture"
    with _deny_delete_reader(path) as release:
        timer = Timer(0.035, release)
        timer.start()
        try:
            observer.emit("public_stage", public_operation_complete=True)
        finally:
            timer.join(timeout=1)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 1 and events[0]["stage"] == "public_stage"
    assert observer.report["journal"] == events
    assert json.loads(path.read_text(encoding="utf-8"))["journal"] == events


def test_execute_preserves_a_safe_typed_report_failure_without_replaying_the_observer(tmp_path):
    path, _ = _public_report(tmp_path)
    report, actions = {}, []

    class Observer:
        def __init__(self, *_args):
            self.store = self.live = None
            self.mid = ""
            self.client = type("Client", (), {"close": lambda _self: actions.append("closed")})()

        def run(self):
            actions.append("one_business_operation")
            raise runner.ReportFileSystemError("report_atomic_replace", _os_error(winerror=5))

    assert runner.execute(tmp_path, path, report, observer_type=Observer) == 1
    assert actions == ["one_business_operation", "closed"]
    saved = json.loads((tmp_path / "report-filesystem-failure.json").read_text(encoding="utf-8"))
    assert saved == {"code": "report_filesystem_failure_scope_close_unconfirmed", "phase": "report_atomic_replace",
                     "errno": errno.EACCES, "winerror": 5}
    assert report["stop_diagnostic"] == {"code": "report_filesystem_failure", "phase": "report_atomic_replace",
                                        "errno": errno.EACCES, "winerror": 5}
    assert "private" not in path.read_text(encoding="utf-8")


def _owned_scope_observer(tmp_path, phase, actions):
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence

    run_dir = tmp_path / "owned-scope"
    runtime = run_dir / "runtime"
    runtime.mkdir(parents=True)
    database = runtime / "meeting_copilot.db"
    store = runner.guarded_persistence_type(V2Persistence)(database, semantic_projection_mode="llm_first")
    mid = runner.SCOPE_PREFIX + "public_file_lock"
    store.create_meeting(meeting_id=mid, title="公开文件锁回归", now_ms=1)
    runner.fresh_json(run_dir / "proxy-ledger.json", {"reserved_forward_attempts": 0, "events": []})
    path, old = _public_report(run_dir)

    class Observer(runner.ContinuousObserver):
        def __init__(self, run_dir, report_path, report):
            self.run_dir, self.report_path, self.report = run_dir, report_path, report
            self.store, self.live, self.mid = store, None, mid
            self.client = type("Client", (), {"close": lambda _self: actions.append("client_closed")})()

        def run(self):
            actions.append("one_business_operation")
            if phase == "during_emit":
                self.emit("public_stage", public_operation_complete=True)
            else:
                runner.close_scope(self.store, self.mid)
                self.report["scope_closed_without_paid_postprocessing"] = True
                self.report["automated_contract_passed"] = True

        def finish(self):
            actions.append("one_finish")
            super().finish()

    return run_dir, path, old, database, mid, Observer


@WINDOWS
@pytest.mark.parametrize("phase", ["during_emit", "final_report"])
def test_real_permanent_report_lock_still_closes_the_owned_sqlite_scope_once_and_records_safe_failure(tmp_path, phase):
    actions, report = [], {}
    run_dir, path, old, database, mid, observer_type = _owned_scope_observer(tmp_path, phase, actions)
    with _deny_delete_reader(path):
        assert runner.execute(run_dir, path, report, observer_type=observer_type) == 1
        assert path.read_bytes() == old
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT state FROM meetings WHERE id=?", (mid,)).fetchone()[0] == "ended"
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert actions.count("one_business_operation") == 1
    assert actions.count("one_finish") == (1 if phase == "during_emit" else 0)
    assert actions.count("client_closed") == 1
    assert report["scope_closed_without_paid_postprocessing"] is True
    assert report.get("automated_contract_passed") is False
    sidecar = json.loads((run_dir / "report-filesystem-failure.json").read_text(encoding="utf-8"))
    assert sidecar == {"code": "report_filesystem_failure_after_scope_close", "phase": "report_atomic_replace",
                       "errno": errno.EACCES, "winerror": 5}


@WINDOWS
@pytest.mark.parametrize("sidecar_failure", ["permissions", "existing_record"])
def test_terminal_record_failure_keeps_the_original_failure_status_and_never_overwrites_a_previous_record(tmp_path, monkeypatch, capsys, sidecar_failure):
    actions, report = [], {}
    run_dir, path, old, database, mid, observer_type = _owned_scope_observer(tmp_path, "final_report", actions)
    sidecar = run_dir / "report-filesystem-failure.json"
    previous_record = b'{"public_previous_failure":true}\n'
    if sidecar_failure == "existing_record":
        sidecar.write_bytes(previous_record)
    open_file, attempts = Path.open, []

    def attempt_record(target, *args, **kwargs):
        if target == sidecar:
            attempts.append(target)
            if sidecar_failure == "permissions":
                raise _os_error(winerror=5)
        return open_file(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", attempt_record)
    with _deny_delete_reader(path):
        assert runner.execute(run_dir, path, report, observer_type=observer_type) == 1
        assert path.read_bytes() == old
    assert attempts == [sidecar]
    assert sidecar.read_bytes() == previous_record if sidecar_failure == "existing_record" else not sidecar.exists()
    output = capsys.readouterr().out
    assert "private" not in output and str(sidecar) not in output
    diagnostics = [json.loads(line) for line in output.splitlines()]
    assert diagnostics[-1]["phase"] == "report_failure_record_write"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT state FROM meetings WHERE id=?", (mid,)).fetchone()[0] == "ended"
