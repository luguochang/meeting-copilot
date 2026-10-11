import json
import sqlite3
from types import SimpleNamespace
import wave

import pytest

from tools import long_audio_soak as tool


@pytest.fixture
def provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    audio = artifact / "existing.wav"
    with wave.open(str(audio), "wb") as writer:
        writer.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x00\x00" * 16_000 * 40)
    provenance = artifact / "provenance.json"
    provenance.write_text(json.dumps({"source_relative_path": "artifacts/existing.wav"}))
    return provenance


def test_default_plan_does_not_cut_audio_make_http_or_run(provenance, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry run attempted an external action")

    monkeypatch.setattr(tool, "run", forbidden)
    monkeypatch.setattr(tool, "cut_existing_slice", forbidden)
    monkeypatch.setattr(tool, "JsonHttpClient", forbidden)
    output = tmp_path / "plan-output"
    assert tool.main(["--provenance", str(provenance), "--start-seconds", "10", "--duration-seconds", "20",
                      "--output-dir", str(output)]) == 0
    report = json.loads((output / "plan.json").read_text())
    assert report["mode"] == "dry_run" and report["executed"] is False
    assert report["selected_pcm_bytes"] == 640_000
    assert report["provider_boundary"]["maximum_forward_attempts"] == 12
    assert list(output.iterdir()) == [output / "plan.json"]


@pytest.mark.parametrize("invalid_budget", ["0", "10.01", "nan", "inf"])
def test_invalid_write_budget_stops_before_plan_file_or_run(tmp_path, monkeypatch, invalid_budget):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid write budget attempted execution")

    monkeypatch.setattr(tool, "run", forbidden)
    output = tmp_path / "uncreated"
    with pytest.raises(SystemExit):
        tool.main(["--mode", "run", "--output-dir", str(output), "--write-timeout-seconds", invalid_budget])
    assert not output.exists()


@pytest.mark.parametrize("start,duration", [(-1, 20), (30, 20), (0, 19), (0, 1801)])
def test_invalid_slice_fails_before_any_run(provenance, start, duration):
    with pytest.raises(ValueError):
        tool.build_plan(provenance=provenance, start_seconds=start, duration_seconds=duration)


def test_structural_snapshot_does_not_serialize_private_text():
    snapshot = {"segments": [{"text": "private-transcript", "normalized_text": "private-correction",
                              "correction_status": "pending"}],
                "suggestions": [{"text": "private-model-answer"}], "jobs": [{"kind": "correction", "status": "running"}],
                "runtime": {"phase": "live", "elapsed_ms": 10}, "audio": {"status": "recording", "playback_url": "private-url"}}
    safe = tool.safe_snapshot(snapshot)
    assert safe["durable_segment_count"] == safe["active_job_count"] == 1
    assert "private-" not in json.dumps(safe)


def test_file_slice_is_exact_local_pcm_copy_without_inference(provenance, tmp_path):
    plan = tool.build_plan(provenance=provenance, start_seconds=10, duration_seconds=20)
    output = tmp_path / "selected.wav"
    tool.cut_existing_slice(plan, output)
    with wave.open(str(output), "rb") as reader:
        assert reader.getnframes() == 20 * 16_000
        assert reader.getframerate() == 16_000
        assert reader.readframes(reader.getnframes()) == b"\x00\x00" * 20 * 16_000


def test_recovery_uses_only_get_and_durable_read_without_model_retry(tmp_path, monkeypatch):
    output = tmp_path / "completed-run"
    output.mkdir()
    durable = {"segments": [{"segment_id": "s1", "revision": 1}],
               "recordings": [{"output_sha256": "a" * 64, "saved_file_verified": True}],
               "usage_rows": [{"total_tokens": 123}]}
    summary = {"meeting_id": "soak-test", "durable": durable, "stability_pass_before_recovery": True,
               "provider_boundary": {"reserved_forward_attempts": 12}}
    (output / "summary.json").write_text(json.dumps(summary))
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path):
            assert method == "GET"
            calls.append(path)
            if path == "/status":
                return SimpleNamespace(payload={"reserved_forward_attempts": 12})
            return SimpleNamespace(payload={"runtime": {"phase": "ended"}, "jobs": [], "segments": []})

    monkeypatch.setattr(tool, "JsonHttpClient", Client)
    monkeypatch.setattr(tool, "durable_counts", lambda *args: durable)
    result = tool.recover(SimpleNamespace(output_dir=output, data_dir=output / "runtime", meeting_id="soak-test",
                                         base_url="http://127.0.0.1:8769", proxy_url="http://127.0.0.1:8790"))
    assert result["recovery_verified"] is True
    assert result["durable_records_recovered"] is True
    assert result["original_summary_preserved"] is True
    assert (output / "summary.json").read_text() == json.dumps(summary)
    assert len(calls) == 2
    summary["stability_pass_before_recovery"] = False
    (output / "summary.json").write_text(json.dumps(summary))
    partial = tool.recover(SimpleNamespace(output_dir=output, data_dir=output / "runtime", meeting_id="soak-test",
                                          base_url="http://127.0.0.1:8769", proxy_url="http://127.0.0.1:8790"))
    assert partial["durable_records_recovered"] is True and partial["recovery_verified"] is False
    assert partial["original_summary_preserved"] is True
    assert (output / "summary.json").read_text() == json.dumps(summary)


def test_idle_prewarm_uses_formal_guarded_endpoint_once(tmp_path):
    calls = []

    class Client:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return SimpleNamespace(payload={"resident": {"process_ready": True}, "offline_refinement": {
                    "capability": {"status": "ready", "realtime_policy": {
                        "realtime_refinement_enabled": True, "prewarm_enabled": True}}, "worker": {"process_ready": False}}})
            assert path == "/providers/asr/prewarm" and kwargs["timeout_seconds"] == 200
            return SimpleNamespace(status=200, duration_ms=123, payload={"worker": {
                "spawned": True, "process_running": True, "process_ready": True}})

    result = tool.ensure_guarded_asr_ready(Client(), tmp_path)
    assert result["ready"] is True and result["guard_bypassed"] is False
    assert len(calls) == 2


def test_failed_refiner_requires_diagnosis_without_automatic_rewarm(tmp_path):
    class Client:
        def request(self, method, path, **kwargs):
            assert method == "GET"
            return SimpleNamespace(payload={"resident": {"process_ready": True}, "offline_refinement": {
                "capability": {"status": "ready", "realtime_policy": {
                    "realtime_refinement_enabled": True, "prewarm_enabled": True}},
                "worker": {"process_ready": False, "requires_recovery": True}}})

    with pytest.raises(ValueError, match="explicit diagnosis"):
        tool.ensure_guarded_asr_ready(Client(), tmp_path)
    assert json.loads((tmp_path / "asr-preflight.json").read_text())["formal_prewarm_requested"] is False


def test_failure_transport_preserves_fixed_diagnostics_without_text_or_messages():
    stats = {"sent_audio_seconds": 472.8, "receive_error_class": "WebSocketTimeoutException",
             "receive_error_message": "private gateway or transcript", "socket_close_reason": "private reason",
             "socket_close_code": 1011, "eos_observed": False, "termination": "unknown",
             "non_empty_final_texts": ["private transcript"]}
    safe = tool.safe_transport(stats)
    assert safe["receive_error_class"] == "WebSocketTimeoutException"
    assert safe["eos_observed"] is False and safe["socket_close_code"] == 1011
    assert "private" not in json.dumps(safe)

    class Failure(RuntimeError):
        layer = "asr_stream"

    result = tool.safe_failure(Failure("private transcript/body"), "stream_or_host")
    assert result == {"layer": "stream_or_host", "error_class": "Failure", "classified_layer": "asr_stream"}


@pytest.mark.parametrize("exhausted,count", [(False, 0), (True, 12)])
def test_explicit_boundary_mode_does_not_reset_or_accept_part_used_budget(exhausted, count):
    boundary = {"reserved_forward_attempts": count, "maximum_forward_attempts": 12, "model": "gpt-6-sol"}
    tool.validate_provider_boundary(boundary, allow_exhausted=exhausted)
    for invalid in (1, 7, 13, 0 if exhausted else 12):
        with pytest.raises(ValueError):
            tool.validate_provider_boundary({**boundary, "reserved_forward_attempts": invalid}, allow_exhausted=exhausted)


def test_formal_source_counts_understands_mirror_payload_without_exporting_text():
    counts = tool.formal_source_counts([
        {"event_type": "transcript_final", "payload": {"final_source": "local_offline_refinement", "text": "private"}},
        {"event_type": "final", "final_source": "local_offline_refinement", "text": "private"},
        {"event_type": "transcript_partial", "payload": {"text": "private"}},
    ])
    assert counts == {"local_offline_refinement": 2}


def test_participation_profile_is_saved_and_readback_verified_before_stream():
    calls = []

    class Client:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if len(calls) == 1:
                return SimpleNamespace(status=200, payload={"hotwords": ["合成词"], "meeting_goal": None,
                    "proactive_suggestion_policy": "low_frequency", "version": 0})
            if method == "PUT":
                assert kwargs["payload"]["live_analysis_profile"] == "participation"
                assert kwargs["payload"]["hotwords"] == ["合成词"]
                assert "version" not in kwargs["payload"]
            return SimpleNamespace(status=200, payload={"live_analysis_profile": "participation", "version": 1})

    observed = tool.save_participation_preparation(Client(), "meeting-test", "fixture.wav")
    assert observed["confirmed_before_audio"] is True
    assert [call[0] for call in calls] == ["GET", "PUT", "GET"]


def test_old_backend_profile_silently_ignored_stops_before_websocket_audio():
    class Client:
        def request(self, method, path, **kwargs):
            assert method in {"GET", "PUT"}
            return SimpleNamespace(status=200, payload={"live_analysis_profile": "legacy"})

    with pytest.raises(ValueError, match="did not persist before audio"):
        tool.save_participation_preparation(Client(), "meeting-test", "fixture.wav")


def test_job_task_metadata_does_not_export_input_context_or_cancelled_as_execution():
    observed = tool.safe_snapshot({"jobs": [{"kind": "intelligence", "status": "cancelled", "attempts": 0,
        "request_context": {"profile": "participation", "task_mode": "discussion_state", "input_context": {"text": "private"}}}]})
    assert observed["job_task_mode_counts"] == {"discussion_state": 1}
    assert observed["job_executed_attempt_status_counts"] == {"intelligence:cancelled:never_attempted": 1}
    assert "private" not in json.dumps(observed)


def test_durable_sql_reads_top_level_trigger_for_participation_producer_proof():
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE jobs(id TEXT,meeting_id TEXT,kind TEXT,status TEXT,attempts INTEGER,"
                           "error_class TEXT,request_context_json TEXT,trigger_type TEXT,created_at_ms INTEGER)")
        metadata = json.dumps({"profile": "participation", "task_mode": "discussion_state",
                               "input_context": {"private_text": "PRIVATE_RECORDED_TEXT"}})
        connection.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)", [
            ("c", "test", "correction", "failed", 1, "quota", "{}", None, 1),
            ("s", "test", "intelligence", "failed", 1, "quota", metadata, "discussion_update", 2),
        ])
        jobs = tool.durable_job_metadata(connection, "test")
        assert jobs[1]["trigger_type"] == "discussion_update"
        assert jobs[1]["task_mode"] == "discussion_state"
        assert "PRIVATE_RECORDED_TEXT" not in json.dumps(jobs)
        proof = tool.participation_producer_proof(
            durable={"jobs": jobs}, formal_sources={"local_offline_refinement": 1}, profile_verified=True)
        assert proof["structural_producer_proof"] is True
        connection.execute("UPDATE jobs SET trigger_type='transcript_delta' WHERE id='s'")
        jobs = tool.durable_job_metadata(connection, "test")
        proof = tool.participation_producer_proof(
            durable={"jobs": jobs}, formal_sources={"local_offline_refinement": 1}, profile_verified=True)
        assert proof["legacy_intelligence_job_count"] == 1
        assert proof["structural_producer_proof"] is False


def test_fresh_participation_run_does_not_treat_cancelled_legacy_jobs_as_correct_wiring():
    correct = [{"kind": "correction", "status": "failed", "attempts": 1},
               {"kind": "intelligence", "profile": "participation", "trigger_type": "discussion_update", "task_mode": "discussion_state"},
               {"kind": "intelligence", "profile": "participation", "trigger_type": "discussion_update", "task_mode": None}]
    assert tool.participation_producer_violations(correct) == []
    invalid = [{"id": "old1", "kind": "answer", "status": "cancelled", "attempts": 0},
               {"id": "old2", "kind": "intelligence", "status": "cancelled", "attempts": 0, "profile": None}]
    assert len(tool.participation_producer_violations(correct + invalid)) == 2


def test_participation_producer_proof_requires_finals_correction_and_rejects_old_reservations():
    durable = {"jobs": [{"kind": "correction", "status": "failed", "attempts": 1},
                        {"kind": "intelligence", "profile": "participation", "trigger_type": "discussion_update", "task_mode": "discussion_state"}],
               "realtime_provider_reservations": []}
    args = {"durable": durable, "formal_sources": {"local_offline_refinement": 6}, "profile_verified": True}
    correct = tool.participation_producer_proof(**args)
    assert correct["structural_producer_proof"] is True
    assert correct["automatic_model_value_verified"] is False
    assert tool.participation_producer_proof(**{**args, "formal_sources": {}})["structural_producer_proof"] is False
    assert tool.participation_producer_proof(**{**args, "durable": {**durable, "jobs": durable["jobs"][:1]}})["structural_producer_proof"] is False
    assert tool.participation_producer_proof(**{**args, "durable": {"jobs": []}})["structural_producer_proof"] is False
    for trigger in ("delta", "transcript_delta", "answer_ready", "task_due"):
        bad = {**durable, "jobs": durable["jobs"] + [{"kind": "intelligence", "profile": "legacy", "trigger_type": trigger,
                                                      "status": "cancelled", "attempts": 0}]}
        assert tool.participation_producer_proof(**{**args, "durable": bad})["structural_producer_proof"] is False
    bad = {**durable, "realtime_provider_reservations": [{"job_id": "old", "job_kind": "intelligence", "job_profile": "legacy", "job_trigger_type": "delta"}]}
    assert tool.participation_producer_proof(**{**args, "durable": bad})["legacy_or_orphan_reservation_count"] == 1
    orphan = {**durable, "realtime_provider_reservations": [{"job_id": "missing"}]}
    assert tool.participation_producer_proof(**{**args, "durable": orphan})["structural_producer_proof"] is False
