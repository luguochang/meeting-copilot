#!/usr/bin/env python3
"""Plan, then explicitly replay existing audio at 1x through an isolated service.

Default plan mode only reads local provenance/WAV headers. Run mode uses no
device/playback, makes no manual coach/Ask request, and saves counts rather than
recorded text. The separately configured loopback proxy bounds every remote
model POST. This measures stability, not the value of a requirements review.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from threading import Event, Thread
import time
from urllib.parse import quote, urlsplit
import wave

try:
    from .pi_stage0_production_replay import (
        JsonHttpClient, MEETING_ID_RE, collect_evidence, end_meeting_with_retry,
        inspect_wav, snapshot_jobs_settled, stream_wav,
    )
except ImportError:
    from pi_stage0_production_replay import (
        JsonHttpClient, MEETING_ID_RE, collect_evidence, end_meeting_with_retry,
        inspect_wav, snapshot_jobs_settled, stream_wav,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVENANCE = REPO_ROOT / "artifacts/realtime-participation-20261007/audio-replay/excerpt-provenance.json"
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
SAFE_TRANSPORT_FIELDS = frozenset({
    "audio_source", "audio_provenance", "ready", "ready_wait_ms", "sent_audio_frames", "sent_audio_seconds",
    "sent_audio_chunk_count", "sent_silence_frames", "sent_silence_seconds", "pace", "stream_wall_ms",
    "non_empty_final_count", "final_sources", "event_counts", "termination", "eos_observed",
    "socket_close_code", "end_send_succeeded", "end_send_attempted", "socket_closed_by_server",
    "sent_audio_frames_before_ready", "sent_silence_frames_before_ready", "write_timeout_seconds",
    "send_error_elapsed_ms",
})


def safe_transport(stats: dict) -> dict:
    value = {key: stats[key] for key in SAFE_TRANSPORT_FIELDS if key in stats}
    for key in ("receive_error_class", "end_send_error_class", "send_error_class"):
        error_class = stats.get(key)
        if isinstance(error_class, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_class):
            value[key] = error_class
    operation = stats.get("send_error_operation")
    if operation in {"source_audio_binary_write", "tail_silence_binary_write", "end_text_write"}:
        value["send_error_operation"] = operation
    return value


def safe_failure(error: Exception, layer: str) -> dict:
    result = {"layer": layer, "error_class": type(error).__name__}
    classified = getattr(error, "layer", None)
    if isinstance(classified, str) and re.fullmatch(r"[a-z_]{1,60}", classified):
        result["classified_layer"] = classified
    # Messages/exception chains may contain provider bodies or recorded text.
    return result


def validate_provider_boundary(boundary: dict, *, allow_exhausted: bool) -> None:
    expected = 12 if allow_exhausted else 0
    if boundary.get("reserved_forward_attempts") != expected or boundary.get("maximum_forward_attempts") != 12 or boundary.get("model") != "gpt-6-sol":
        raise ValueError("explicit fresh or already exhausted 12-attempt model boundary is required")


def formal_source_counts(events: list[dict]) -> dict:
    counts = Counter()
    for event in events:
        kind = event.get("event_type")
        if kind not in {"final", "transcript_final"}:
            continue
        payload = event.get("payload") if kind == "transcript_final" else event
        if isinstance(payload, dict):
            counts[str(payload.get("final_source") or "unspecified")] += 1
    return dict(counts)


def task_metadata(job: dict) -> dict:
    metadata = job.get("request_context") or {}
    result = {}
    for key in ("profile", "task_mode", "trigger_type", "intent"):
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if key == "trigger_type" and value is None:
            value = job.get("trigger_type")
        result[key] = value if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,60}", value) else None
    return result


def durable_job_metadata(connection: sqlite3.Connection, meeting_id: str) -> list[dict]:
    jobs = []
    for row in connection.execute(
        "SELECT id,kind,status,attempts,error_class,request_context_json,trigger_type "
        "FROM jobs WHERE meeting_id=? ORDER BY created_at_ms,id", (meeting_id,),
    ):
        job = dict(row)
        metadata = json.loads(job.pop("request_context_json") or "{}")
        job.update(task_metadata({**job, "request_context": metadata}))
        jobs.append(job)
    return jobs


def participation_producer_violations(jobs: list[dict]) -> list[dict]:
    # This fixture is a fresh DB and issues no Ask or legacy Answer request.
    # Even an unexecuted/cancelled legacy producer is a wiring defect here.
    result = []
    for job in jobs:
        if job.get("kind") == "answer" or job.get("kind") == "intelligence" and (
            job.get("profile") != "participation" or job.get("trigger_type") != "discussion_update"
        ):
            result.append({key: job.get(key) for key in ("id", "kind", "status", "attempts", "profile", "task_mode", "trigger_type")})
    return result


def participation_producer_proof(*, durable: dict, formal_sources: dict, profile_verified: bool) -> dict:
    jobs = durable.get("jobs") or []
    reservations = durable.get("realtime_provider_reservations") or []
    legacy_intelligence = [job for job in jobs if job.get("kind") == "intelligence" and (
        job.get("profile") != "participation" or job.get("trigger_type") != "discussion_update")]
    legacy_answers = [job for job in jobs if job.get("kind") == "answer"]
    bad_reservations = [row for row in reservations if row.get("job_kind") != "intelligence"
        or row.get("job_profile") != "participation" or row.get("job_trigger_type") != "discussion_update"]
    conditions = {
        "explicit_participation_first_binary_locked": profile_verified,
        "canonical_local_finals_present": (formal_sources.get("local_offline_refinement") or 0) > 0
            and set(formal_sources) == {"local_offline_refinement"},
        "correction_jobs_created": any(job.get("kind") == "correction" for job in jobs),
        "actual_discussion_state_job_created": any(job.get("kind") == "intelligence" and job.get("profile") == "participation"
            and job.get("task_mode") == "discussion_state" and job.get("trigger_type") == "discussion_update" for job in jobs),
        "no_legacy_delta_transcript_delta_answer_ready_task_due_jobs": not legacy_intelligence,
        "no_legacy_answer_jobs": not legacy_answers,
        "no_legacy_or_orphan_provider_reservations": not bad_reservations,
    }
    return {"conditions": conditions, "structural_producer_proof": all(conditions.values()),
            "legacy_intelligence_job_count": len(legacy_intelligence), "legacy_answer_job_count": len(legacy_answers),
            "legacy_or_orphan_reservation_count": len(bad_reservations),
            "automatic_model_value_verified": False,
            "automatic_success_not_inferred_from_absence": True}


def save_participation_preparation(client: JsonHttpClient, meeting_id: str, filename: str) -> dict:
    encoded = quote(meeting_id, safe="")
    path = f"/v2/meetings/{encoded}/preparation"
    existing = client.request("GET", path)
    if existing.status != 200:
        raise ValueError("official preparation read failed before audio")
    allowed = {"hotwords", "input_source", "input_device_id", "input_device_name", "notice_acknowledged",
               "preset_id", "meeting_goal", "participant_role", "focus_points", "output_format", "proactive_suggestion_policy"}
    payload = {key: value for key, value in existing.payload.items() if key in allowed}
    payload.update({"input_source": "microphone", "input_device_id": "existing-wav-replay", "input_device_name": filename,
                    "notice_acknowledged": True, "live_analysis_profile": "participation"})
    written = client.request("PUT", path, payload=payload)
    if written.status != 200 or written.payload.get("live_analysis_profile") != "participation":
        raise ValueError("explicit participation preparation did not persist before audio")
    confirmed = client.request("GET", path)
    if confirmed.status != 200 or confirmed.payload.get("live_analysis_profile") != "participation":
        raise ValueError("participation preparation readback failed before audio")
    return {"profile": "participation", "version": confirmed.payload.get("version"),
            "confirmed_before_audio": True, "audio_source": "simulated_realtime_wav",
            "physical_device_started": False, "notice_prompt_operated": False}


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_plan(*, provenance: Path, start_seconds: int, duration_seconds: int) -> dict:
    record = json.loads(provenance.read_text(encoding="utf-8"))
    source = (REPO_ROOT / str(record["source_relative_path"])).resolve()
    if not source.is_relative_to(REPO_ROOT / "artifacts"):
        raise ValueError("source provenance must refer to an existing repository artifact")
    info = inspect_wav(source)
    if not 0 <= start_seconds or not 20 <= duration_seconds <= 1_800 or start_seconds + duration_seconds > info.duration_seconds:
        raise ValueError("requested existing-audio range is invalid")
    return {"schema_version": "long_audio_soak_plan.v1", "mode": "dry_run", "executed": False,
            "provenance_file": str(provenance.resolve()), "source_relative_path": str(source.relative_to(REPO_ROOT)),
            "source": asdict(info), "selected_start_seconds": start_seconds,
            "selected_duration_seconds": duration_seconds, "selected_end_seconds": start_seconds + duration_seconds,
            "selected_pcm_bytes": duration_seconds * 16_000 * 2,
            "transport_float32_bytes": duration_seconds * 16_000 * 4,
            "audio_chunk_count": (duration_seconds * 10 + 2) // 3, "chunk_seconds": 0.3,
            "tail_silence_seconds": 9, "pace": 1.0, "expected_duration_seconds": duration_seconds + 19,
            "privacy": {"device_started": False, "audio_played": False, "raw_audio_uploaded_to_remote_asr": False,
                        "recorded_text_saved_in_report": False},
            "scope": "third_party_existing_audio_stability_and_durable_recovery",
            "excluded_claims": ["physical microphone or speaker capture", "ASR word accuracy without a human reference",
                                "requirements-review value", "unlimited full-feature model coverage after budget exhaustion",
                                "midstream reconnect recovery"],
            "provider_boundary": {"maximum_forward_attempts": 12, "counter_unit": "actual upstream model HTTP POST attempt",
                                  "includes": ["correction", "state", "automatic", "answer", "minutes", "approach", "each Pi model turn"],
                                  "budget_denial_http_status": 503, "implicit_upstream_retries": 0,
                                  "lane_attribution": "unknown unless an optional test header is present",
                                  "limitation": "Existing durable jobs may locally retry a denied 503; proxy never forwards after exhaustion."},
            "before_run": ["Root finishes R03 and controls isolated 8769 lifecycle/data/configuration",
                           "Only the isolated runtime uses the loopback provider proxy; default 8768 configuration is unchanged",
                           "Private credentials passed through environment/memory only; never command arguments or artifacts",
                           "Proxy report is new with zero attempts, maximum 12, model gpt-6-sol",
                           "Local ASR resident and configured offline refinement are healthy; existing resource guard remains active",
                           "Isolated application database contains no meetings; source recording is not moved or deleted"],
            "acceptance": ["One paced selected-duration file stream with all expected PCM frames plus explicit tail silence",
                           "Nonempty authoritative local final events durably committed; source labels retained",
                           "Managed recording saved, actual sample count/duration/hash verified without playback",
                           "Every derived job terminal after bounded post-end wait, explicit budget failures preserved",
                           "At most 12 forwarded model attempts including failed upstream POSTs and Pi follow-up turns",
                           "After root-controlled restart, GET-only recovery preserves segment IDs/audio hash/count without re-triggering paid work"]}


def cut_existing_slice(plan: dict, target: Path) -> None:
    source = REPO_ROOT / plan["source_relative_path"]
    with wave.open(str(source), "rb") as reader, wave.open(str(target), "wb") as writer:
        writer.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        reader.setpos(plan["selected_start_seconds"] * 16_000)
        remaining = plan["selected_duration_seconds"] * 16_000
        while remaining:
            frames = min(remaining, 16_000 * 10)
            data = reader.readframes(frames)
            if len(data) != frames * 2:
                raise ValueError("existing recording ended before the selected slice")
            writer.writeframesraw(data)
            remaining -= frames


def safe_snapshot(snapshot: dict) -> dict:
    segments = snapshot.get("segments") or []
    jobs = snapshot.get("jobs") or []
    return {"collected_at_ms": time.time_ns() // 1_000_000, "last_seq": snapshot.get("last_seq"),
            "phase": (snapshot.get("runtime") or {}).get("phase"),
            "elapsed_ms": (snapshot.get("runtime") or {}).get("elapsed_ms"),
            "durable_segment_count": (snapshot.get("transcript_page") or {}).get("total", len(segments)),
            "returned_segment_count": len(segments),
            "correction_status_counts": dict(Counter(x.get("correction_status") for x in segments)),
            "job_status_counts": dict(Counter(str(x.get("kind")) + ":" + str(x.get("status")) for x in jobs)),
            "active_job_count": sum(x.get("status") not in TERMINAL for x in jobs),
            "job_request_profile_counts": dict(Counter(task_metadata(x)["profile"] or "absent" for x in jobs)),
            "job_task_mode_counts": dict(Counter(task_metadata(x)["task_mode"] or "absent" for x in jobs)),
            "job_executed_attempt_status_counts": dict(Counter(str(x.get("kind")) + ":" + str(x.get("status")) +
                ":" + ("attempted" if int(x.get("attempts") or 0) > 0 else "never_attempted") for x in jobs)),
            "live_analysis_profile": snapshot.get("live_analysis_profile"),
            "live_analysis_profile_locked": snapshot.get("live_analysis_profile_locked"),
            "audio": {k: v for k, v in (snapshot.get("audio") or {}).items()
                      if k in {"status", "duration_ms", "file_size_bytes", "chunk_count"}}}


def ensure_guarded_asr_ready(client: JsonHttpClient, output: Path) -> dict:
    """Warm an idle refiner once via its formal guarded endpoint, never a worker CLI."""
    runtime = client.request("GET", "/providers/asr/runtime").payload
    refinement = runtime.get("offline_refinement") or {}
    capability = refinement.get("capability") or {}
    policy = capability.get("realtime_policy") or {}
    observation = {"formal_prewarm_requested": False, "local_model_only": True,
                   "guard_bypassed": False, "actual_audio_sent_before_ready": False}
    if not (runtime.get("resident") or {}).get("process_ready"):
        raise ValueError("local ASR resident must be ready before this long run")
    worker = refinement.get("worker") or {}
    if policy.get("realtime_refinement_enabled") and not worker.get("process_ready"):
        # A prewarm worker normally unloads after 120 idle seconds. Recovery
        # of failed workers needs a separate diagnosis; do not silently retry.
        if not policy.get("prewarm_enabled") or capability.get("status") != "ready" or worker.get("requires_recovery"):
            save(output / "asr-preflight.json", {**observation, "ready": False})
            raise ValueError("configured offline refiner requires explicit diagnosis before the long run")
        observation["formal_prewarm_requested"] = True
        warmed = client.request("POST", "/providers/asr/prewarm", payload={}, timeout_seconds=200)
        worker = warmed.payload.get("worker") or {}
        observation.update({"http_status": warmed.status, "duration_ms": warmed.duration_ms,
                            "ready": warmed.status == 200 and all(worker.get(k) is True
                            for k in ("spawned", "process_running", "process_ready"))})
        save(output / "asr-preflight.json", observation)
        if not observation["ready"]:
            raise ValueError("formal guarded offline prewarm failed before audio")
    else:
        observation["ready"] = True
        save(output / "asr-preflight.json", observation)
    return observation


def durable_counts(data_dir: Path, meeting_id: str) -> dict:
    database = data_dir / "meeting_copilot.db"
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        segments = [dict(r) for r in connection.execute(
            "SELECT segment_id,revision,source_track,LENGTH(text) AS raw_characters,"
            "LENGTH(normalized_text) AS normalized_characters,correction_status FROM transcript_segments "
            "WHERE meeting_id=? ORDER BY transcript_seq", (meeting_id,))]
        recordings = [dict(r) for r in connection.execute(
            "SELECT track,epoch,status,sample_rate_hz,sample_count,duration_ms,output_relative_path,output_sha256 "
            "FROM recording_sessions WHERE meeting_id=? ORDER BY track,epoch", (meeting_id,))]
        for recording in recordings:
            relative = recording["output_relative_path"]
            path = (data_dir / relative).resolve() if relative else None
            recording["saved_file_verified"] = bool(path and path.is_relative_to(data_dir.resolve()) and path.is_file())
            if recording["saved_file_verified"]:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while block := handle.read(1_048_576):
                        digest.update(block)
                recording["actual_file_sha256"] = digest.hexdigest()
                recording["saved_file_verified"] = recording["actual_file_sha256"] == recording["output_sha256"]
        usage = [dict(r) for r in connection.execute(
            "SELECT purpose,model,prompt_tokens,completion_tokens,total_tokens,timestamp_ms FROM llm_usage_ledger "
            "WHERE session_id=? ORDER BY timestamp_ms", (meeting_id,))]
        mirror = connection.execute("SELECT record_json FROM asr_live_sessions WHERE session_id=?", (meeting_id,)).fetchone()
        record = json.loads(mirror[0]) if mirror else {}
        jobs = durable_job_metadata(connection, meeting_id)
        job_by_id = {job["id"]: job for job in jobs}
        reservations = []
        for row in connection.execute("SELECT seq,payload_json FROM meeting_events WHERE meeting_id=? AND type=? ORDER BY seq",
                                      (meeting_id, "meeting.realtime_provider.reservation")):
            payload = json.loads(row["payload_json"] or "{}")
            job = job_by_id.get(payload.get("job_id")) or {}
            reservations.append({"seq": row["seq"], "job_id": payload.get("job_id"),
                                 "job_kind": job.get("kind"), "job_profile": job.get("profile"),
                                 "job_trigger_type": job.get("trigger_type"), "job_task_mode": job.get("task_mode")})
        return {"segments": segments, "segment_count": len(segments), "recordings": recordings,
                "jobs": jobs, "active_job_count": sum(job["status"] not in TERMINAL for job in jobs),
                "realtime_provider_reservations": reservations,
                "job_attempt_count_is_remote_request_count": False,
                "raw_character_count": sum(x["raw_characters"] for x in segments),
                "normalized_character_count": sum(x["normalized_characters"] for x in segments),
                "usage_rows": usage, "usage_ledger_record_count": len(usage),
                "usage_total_tokens": sum(x["total_tokens"] for x in usage),
                "formal_asr_mirror": {"present": bool(mirror), "is_mock": record.get("is_mock"),
                                      "audio_source": record.get("audio_source"), "event_count": len(record.get("events") or [])}}
    finally:
        connection.close()


def recover(args) -> dict:
    """GET/read-only recovery check after a root-controlled isolated restart."""
    output = args.output_dir.resolve()
    data_dir = args.data_dir.resolve()
    if data_dir != output / "runtime":
        raise ValueError("recovery requires the same isolated output/runtime")
    for value in (args.base_url, args.proxy_url):
        url = urlsplit(value)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password or url.query or url.fragment:
            raise ValueError("recovery must use loopback URLs without credentials")
    if urlsplit(args.base_url).port != 8769:
        raise ValueError("recovery must use the isolated service on 8769")
    summary_path = output / "summary.json"
    original_summary_bytes = summary_path.read_bytes()
    summary = json.loads(original_summary_bytes)
    if summary.get("meeting_id") != args.meeting_id:
        raise ValueError("recovery meeting must match the completed run")
    client = JsonHttpClient(args.base_url, token=os.environ.get("MEETING_COPILOT_LOCAL_API_TOKEN", ""), timeout_seconds=30)
    proxy = JsonHttpClient(args.proxy_url, timeout_seconds=10)
    snapshot = client.request("GET", f"/v2/meetings/{quote(args.meeting_id, safe='')}/snapshot?segment_limit=500").payload
    durable = durable_counts(data_dir, args.meeting_id)
    boundary = proxy.request("GET", "/status").payload
    previous = summary.get("durable") or {}
    conditions = {
        "original_stability_passed": summary.get("stability_pass_before_recovery") is True,
        "meeting_ended": (snapshot.get("runtime") or {}).get("phase") == "ended",
        "same_segment_ids_revisions_and_counts": bool(previous.get("segments")) and durable["segments"] == previous["segments"],
        "same_saved_audio_metadata_and_hash": bool(previous.get("recordings")) and durable["recordings"] == previous["recordings"],
        "all_jobs_terminal": snapshot_jobs_settled(snapshot),
        "same_paid_usage_ledger": durable["usage_rows"] == previous.get("usage_rows"),
        "no_additional_forwarded_model_request": type(boundary.get("reserved_forward_attempts")) is int and
            boundary["reserved_forward_attempts"] == (summary.get("provider_boundary") or {}).get("reserved_forward_attempts"),
    }
    recovery = {"schema_version": "long_audio_soak_recovery.v1", "collected_at_ms": time.time_ns() // 1_000_000,
                "meeting_id": args.meeting_id, "scope": "GET_and_readonly_SQLite_no_model_retry",
                "conditions": conditions, "recovery_verified": all(conditions.values()),
                "snapshot": safe_snapshot(snapshot), "durable": durable,
                "provider_boundary": boundary, "recorded_text_saved": False}
    recovery["durable_records_recovered"] = all(value for key, value in conditions.items() if key != "original_stability_passed")
    recovery["original_summary_sha256"] = hashlib.sha256(original_summary_bytes).hexdigest()
    recovery["original_summary_preserved"] = summary_path.read_bytes() == original_summary_bytes
    save(output / "recovery.json", recovery)
    print(json.dumps({"phase": "recovery", "recovery_verified": recovery["recovery_verified"],
                      "forward_attempts": boundary.get("reserved_forward_attempts")}), flush=True)
    return recovery


def run(args, plan: dict) -> dict:
    # Explicit run only. Root owns process startup/restart and private config.
    output = args.output_dir.resolve()
    data_dir = args.data_dir.resolve()
    if data_dir != output / "runtime" or not MEETING_ID_RE.fullmatch(args.meeting_id):
        raise ValueError("run requires the named isolated output/runtime and a safe meeting ID")
    for value in (args.base_url, args.proxy_url):
        url = urlsplit(value)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password or url.query or url.fragment:
            raise ValueError("isolated test services must use loopback URLs without credentials")
    if urlsplit(args.base_url).port != 8769:
        raise ValueError("long audio test must use the isolated service on 8769")
    with sqlite3.connect((data_dir / "meeting_copilot.db").as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] != 0:
            raise ValueError("isolated database must have no existing meetings before this one run")
    client = JsonHttpClient(args.base_url, token=os.environ.get("MEETING_COPILOT_LOCAL_API_TOKEN", ""), timeout_seconds=30)
    proxy = JsonHttpClient(args.proxy_url, timeout_seconds=10)
    boundary = proxy.request("GET", "/status").payload
    validate_provider_boundary(boundary, allow_exhausted=args.allow_exhausted_proxy)
    preflight = ensure_guarded_asr_ready(client, output)
    fixture = output / "existing_recording_30min.wav"
    if fixture.exists():
        raise ValueError("refuse to overwrite a prior fixture/run")
    cut_existing_slice(plan, fixture)
    info = inspect_wav(fixture)
    encoded = quote(args.meeting_id, safe="")
    summary = {"schema_version": "long_audio_soak_observation.v1", "mode": "executed",
               "scope": plan["scope"], "meeting_id": args.meeting_id, "fixture": asdict(info),
               "started_at_ms": time.time_ns() // 1_000_000, "samples": [], "failures": [],
               "acceptance_scope": "ASR_audio_durability_budget_and_restart_reads_only",
               "requirements_review_value_verified": False, "physical_device_verified": False,
               "recorded_text_saved": False, "recovery_verified": False, "asr_preflight": preflight,
               "provider_boundary_at_start": boundary,
               "model_requests_already_exhausted_at_start": args.allow_exhausted_proxy}
    created = False
    stop = Event()
    monitor = None
    events = []
    snapshot = {}
    transport_progress = {"sent_audio_frames": 0, "sent_audio_seconds": 0,
                          "authoritative_local_final_count": 0, "non_empty_final_count": 0, "asr_error_event_count": 0}
    checked_profile_after_ready = False

    def update_transport_progress(counts):
        nonlocal checked_profile_after_ready
        transport_progress.update(counts)
        if counts.get("asr_ready") and not checked_profile_after_ready:
            # One read confirms WS initialization did not restore legacy mode.
            # This does not enqueue analysis or another provider request.
            prepared = client.request("GET", f"/v2/meetings/{encoded}/preparation")
            if prepared.status != 200 or prepared.payload.get("live_analysis_profile") != "participation":
                raise ValueError("participation profile changed during WebSocket initialization")
            summary["participation_preparation"]["profile_preserved_after_ready"] = True
            summary["participation_preparation"]["version_after_ready"] = prepared.payload.get("version")
            checked_profile_after_ready = True

    def sample_loop():
        while not stop.wait(15):
            try:
                sampled = safe_snapshot(client.request("GET", f"/v2/meetings/{encoded}/snapshot?segment_limit=500").payload)
                sampled["forward_attempts"] = proxy.request("GET", "/status").payload.get("reserved_forward_attempts")
                sampled["transport"] = dict(transport_progress)
                guard_codes = {"asr_capacity_unavailable", "asr_resource_probe_failed", "asr_resource_guard_triggered", "asr_cold_start_busy"}
                sampled["observed_guard_error_codes"] = sorted({
                    e.get("error_code") for e in events if e.get("error_code") in guard_codes
                })
                summary["samples"].append(sampled)
                save(output / "progress.json", {"mode": "executed", "samples": list(summary["samples"]), "recorded_text_saved": False})
                if len(summary["samples"]) % 4 == 0:
                    print(json.dumps({"phase": "streaming", "elapsed_seconds": len(summary["samples"]) * 15,
                                      "sent_audio_seconds": sampled["transport"]["sent_audio_seconds"],
                                      "authoritative_local_final_count": sampled["transport"]["authoritative_local_final_count"],
                                      "durable_segment_count": sampled["durable_segment_count"],
                                      "forward_attempts": sampled["forward_attempts"], "active_job_count": sampled["active_job_count"],
                                      "guard_error_codes": sampled["observed_guard_error_codes"]}), flush=True)
            except Exception as error:
                summary["samples"].append({"collected_at_ms": time.time_ns() // 1_000_000, "error_class": type(error).__name__})

    try:
        result = client.request("POST", "/v2/meetings", payload={"meeting_id": args.meeting_id,
                                "title": "已有第三方录音·30分钟持续收录测试",
                                "expected_duration_seconds": plan["expected_duration_seconds"], "track_count": 1})
        if result.status != 201:
            raise ValueError("isolated meeting creation failed")
        created = True
        summary["participation_preparation"] = save_participation_preparation(client, args.meeting_id, info.filename)
        monitor = Thread(target=sample_loop, daemon=True)
        monitor.start()
        stats = stream_wav(client, meeting_id=args.meeting_id, wav_path=fixture, wav_info=info, pace=1.0,
                           chunk_seconds=0.3, tail_silence_seconds=9, ready_timeout_seconds=60,
                           finalize_timeout_seconds=180, event_sink=events, audio_source="simulated_realtime_wav",
                           progress_callback=update_transport_progress,
                           write_timeout_seconds=getattr(args, "write_timeout_seconds", 5.0))
        summary["transport"] = safe_transport(stats)
        diagnostics = stats.get("asr_shutdown_diagnostics") or {}
        summary["transport"]["asr_shutdown_counts"] = {
            k: v for k, v in diagnostics.items()
            if k in {"audio_chunks_enqueued", "audio_chunks_written", "audio_bytes_enqueued", "audio_bytes_written",
                     "max_write_queue_depth", "write_queue_depth_at_end", "audio_chunks_not_written"}
            and isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    except Exception as error:
        summary["failures"].append(safe_failure(error, "stream_or_host"))
        stats = getattr(error, "transport_stats", {}) or {}
        summary.setdefault("transport", safe_transport(stats))
    finally:
        stop.set()
        if monitor:
            monitor.join(timeout=35)
        if created:
            try:
                ended = end_meeting_with_retry(client, meeting_id=args.meeting_id, timeout_seconds=60, poll_interval_seconds=1)
                summary["end_http_status"] = ended.status
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    snapshot = client.request("GET", f"/v2/meetings/{encoded}/snapshot?segment_limit=500").payload
                    if snapshot_jobs_settled(snapshot) and (snapshot.get("audio") or {}).get("status") not in {"recording", "assembling"}:
                        break
                    time.sleep(1)
                summary["final_snapshot"] = safe_snapshot(snapshot)
                # Atomic official capture is read in memory only, never archived raw.
                captured = collect_evidence(client, args.meeting_id)
                summary["formal_final_source_counts"] = formal_source_counts(captured.get("asr_live_events", {}).get("events", []))
                summary["durable"] = durable_counts(data_dir, args.meeting_id)
            except Exception as error:
                summary["failures"].append(safe_failure(error, "post_end_evidence"))
        try:
            summary["provider_boundary"] = proxy.request("GET", "/status").payload
        except Exception as error:
            summary["provider_boundary"] = {"available": False}
            summary["failures"].append({"layer": "provider_boundary_evidence", "error_class": type(error).__name__})
        summary["completed_at_ms"] = time.time_ns() // 1_000_000
        summary["participation_producer_violations"] = participation_producer_violations(summary.get("durable", {}).get("jobs") or [])
        summary["new_forward_attempts_this_run"] = (
            summary["provider_boundary"].get("reserved_forward_attempts", 0) - boundary["reserved_forward_attempts"]
        )
        summary["new_budget_rejections_this_run"] = (
            summary["provider_boundary"].get("budget_rejections", 0) - boundary.get("budget_rejections", 0)
        )
        transport = summary.get("transport") or {}
        summary["stability_conditions"] = {
            "full_source_sent": transport.get("sent_audio_frames") == info.frame_count,
            "eos_observed": transport.get("eos_observed") is True,
            "nonempty_finals": (transport.get("non_empty_final_count") or 0) > 0,
            "authoritative_local_finals": transport.get("final_sources") == ["local_offline_refinement"],
            "meeting_ended": (snapshot.get("runtime") or {}).get("phase") == "ended",
            "all_jobs_terminal": bool(snapshot) and snapshot_jobs_settled(snapshot),
            "managed_audio_verified": bool(summary.get("durable", {}).get("recordings")) and all(
                r["saved_file_verified"] for r in summary.get("durable", {}).get("recordings", [])),
            "saved_sample_count_matches_sent_source_and_tail": bool(summary.get("durable", {}).get("recordings")) and sum(
                r["sample_count"] for r in summary.get("durable", {}).get("recordings", [])
            ) == info.frame_count + (transport.get("sent_silence_frames") or 0),
            "participation_profile_verified": summary.get("participation_preparation", {}).get("confirmed_before_audio") is True
                and summary.get("participation_preparation", {}).get("profile_preserved_after_ready") is True
                and snapshot.get("live_analysis_profile") == "participation"
                and snapshot.get("live_analysis_profile_locked") is True,
            "no_legacy_live_producer_jobs": bool(summary.get("durable")) and not summary["participation_producer_violations"],
            "no_new_upstream_attempt_in_exhausted_mode": not args.allow_exhausted_proxy
                or summary["new_forward_attempts_this_run"] == 0 and summary["provider_boundary"].get("reserved_forward_attempts") == 12,
            "forward_budget_respected": type(summary["provider_boundary"].get("reserved_forward_attempts")) is int
                and summary["provider_boundary"]["reserved_forward_attempts"] <= 12}
        summary["participation_producer_proof"] = participation_producer_proof(
            durable=summary.get("durable") or {}, formal_sources=summary.get("formal_final_source_counts") or {},
            profile_verified=summary["stability_conditions"]["participation_profile_verified"])
        summary["stability_conditions"]["participation_producer_proof"] = summary["participation_producer_proof"]["structural_producer_proof"]
        summary["stability_pass_before_recovery"] = all(summary["stability_conditions"].values()) and not summary["failures"]
        summary["all_model_features_fully_covered"] = False
        summary["budget_exhausted"] = (summary["provider_boundary"].get("budget_rejections") or 0) > 0
        summary["recovery_next_step"] = "Root-controlled isolated restart, then GET/read-only SQLite compare; no model retry or second stream."
        save(output / "summary.json", summary)
        print(json.dumps({"phase": "complete", "stability_pass_before_recovery": summary["stability_pass_before_recovery"],
                          "forward_attempts": summary["provider_boundary"].get("reserved_forward_attempts"),
                          "budget_rejections": summary["provider_boundary"].get("budget_rejections"),
                          "recovery_verified": False}), flush=True)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "run", "recover"), default="plan")
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE)
    parser.add_argument("--start-seconds", type=int, default=1_275)
    parser.add_argument("--duration-seconds", type=int, default=1_800)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--meeting-id", default="long_audio_soak_20261008_r01")
    parser.add_argument("--base-url", default="http://127.0.0.1:8769")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8790")
    parser.add_argument("--allow-exhausted-proxy", action="store_true",
                        help="Explicit ASR-only durability recheck using the same already exhausted proxy; zero new upstream forwards")
    parser.add_argument("--write-timeout-seconds", type=float, default=5.0,
                        help="Test transport write budget only, finite1to10seconds; default5, no frame resend")
    args = parser.parse_args(argv)
    if not math.isfinite(args.write_timeout_seconds) or not 1 <= args.write_timeout_seconds <= 10:
        parser.error("--write-timeout-seconds must be finite and between1and10")
    if args.mode == "recover":
        if args.data_dir is None:
            parser.error("recovery requires --data-dir <output-dir>/runtime")
        return 0 if recover(args)["recovery_verified"] else 1
    plan = build_plan(provenance=args.provenance, start_seconds=args.start_seconds, duration_seconds=args.duration_seconds)
    save(args.output_dir / "plan.json", plan)
    if args.mode == "plan":
        print(json.dumps({"mode": "dry_run", "executed": False, "duration_seconds": plan["selected_duration_seconds"],
                          "selected_pcm_bytes": plan["selected_pcm_bytes"], "maximum_forward_attempts": 12}), flush=True)
        return 0
    if args.data_dir is None:
        parser.error("explicit run requires --data-dir <output-dir>/runtime")
    result = run(args, plan)
    return 0 if result["stability_pass_before_recovery"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
