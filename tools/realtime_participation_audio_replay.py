#!/usr/bin/env python3
"""Observe an existing WAV through the official ASR WebSocket, without devices.

This deliberately avoids the old release-incident goal and prewarm-only gate.
It records current-policy failures, never treats a file replay as a microphone
or UI latency acceptance, and sends at most one explicit participation request.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import quote

try:
    from .pi_stage0_production_replay import (
        JsonHttpClient, MEETING_ID_RE, ReplayFailure, collect_evidence, end_meeting_with_retry,
        extract_decisions, inspect_wav, sanitize, stream_wav, wait_for_intelligence,
    )
except ImportError:
    from pi_stage0_production_replay import (
        JsonHttpClient, MEETING_ID_RE, ReplayFailure, collect_evidence, end_meeting_with_retry,
        extract_decisions, inspect_wav, sanitize, stream_wav, wait_for_intelligence,
    )


def run(args):
    if not MEETING_ID_RE.fullmatch(args.meeting_id):
        raise ValueError("invalid replay meeting ID")
    info = inspect_wav(args.wav)
    if not 20 <= info.duration_seconds <= 40:
        raise ValueError("use a 20 to 40 second existing recording excerpt")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    token = str(os.environ.get("MEETING_COPILOT_LOCAL_API_TOKEN") or "")
    client = JsonHttpClient(args.base_url, token=token, timeout_seconds=30)
    summary = {"started_at": datetime.now(timezone.utc).isoformat(), "meeting_id": args.meeting_id,
               "fixture": asdict(info), "provenance": "existing_wav_only_no_device_no_playback",
               "concurrent_load": args.concurrent_load, "failures": [], "transport": {},
               "asr_accuracy": "not_measured_without_human_reference", "ui_latency": "not_measured_without_DOM_render_ack"}
    events, created = [], False
    safe_secrets = (token,) if token else ()

    def save(name, value):
        (output / name).write_text(json.dumps(sanitize(value, secret_values=safe_secrets), ensure_ascii=False, indent=2), encoding="utf-8")

    def progress(phase, **details):
        print(json.dumps({"phase": phase, **details}, ensure_ascii=False), flush=True)

    try:
        result = client.request("GET", "/health")
        if result.status != 200 or result.payload.get("status") != "ok":
            raise ReplayFailure("service_health", "service was not healthy")
        result = client.request("GET", "/providers/asr/runtime")
        runtime = result.payload
        resident = runtime.get("resident") or {}
        refinement = runtime.get("offline_refinement") or {}
        capability = refinement.get("capability") or {}
        policy = capability.get("realtime_policy") or {}
        summary["runtime_before"] = {"http_status": result.status, "resident_ready": resident.get("process_ready"),
                                     "resident_pid": resident.get("pid"), "refiner_policy": policy.get("mode"),
                                     "refiner_capability": capability.get("status"),
                                     "refiner_missing_components": capability.get("missing_components") or [],
                                     "refiner_worker_ready": (refinement.get("worker") or {}).get("process_ready")}
        save("asr-runtime-before.json", runtime)
        if result.status != 200 or not runtime.get("realtime_available") or not resident.get("process_ready"):
            raise ReplayFailure("asr_runtime", "resident ASR was unavailable")
        if policy.get("realtime_refinement_enabled") and capability.get("status") != "ready":
            raise ReplayFailure("offline_refiner_configuration", "enabled offline refiner has missing local components")
        result = client.request("POST", "/v2/meetings", payload={"meeting_id": args.meeting_id,
                                "title": "已有录音文件回放测试", "expected_duration_seconds": 120, "track_count": 1})
        if result.status != 201:
            raise ReplayFailure("meeting_create", f"create HTTP {result.status}")
        created = True
        encoded_id = quote(args.meeting_id, safe="")
        result = client.request("PUT", f"/v2/meetings/{encoded_id}/preparation", payload={
            "hotwords": [], "input_source": "microphone", "input_device_id": "existing-wav-replay",
            "input_device_name": info.filename, "notice_acknowledged": True, "preset_id": "general",
            "meeting_goal": None, "participant_role": None, "focus_points": [], "output_format": "standard",
            "proactive_suggestion_policy": "low_frequency"})
        if result.status != 200:
            raise ReplayFailure("preparation", f"preparation HTTP {result.status}")
        progress("stream_start", seconds=info.duration_seconds, provenance="simulated_realtime_wav")
        summary["transport"] = stream_wav(client, meeting_id=args.meeting_id, wav_path=args.wav.resolve(), wav_info=info,
            pace=1.0, chunk_seconds=0.3, tail_silence_seconds=9, ready_timeout_seconds=60,
            finalize_timeout_seconds=90, event_sink=events, audio_source="simulated_realtime_wav")
        progress("stream_finished", finals=summary["transport"].get("non_empty_final_count"),
                 termination=summary["transport"].get("termination"))
        snapshot = client.request("GET", f"/v2/meetings/{encoded_id}/snapshot?segment_limit=500").payload
        if summary["transport"].get("non_empty_final_count"):
            result = client.request("POST", f"/v2/meetings/{encoded_id}/coach/request", payload={"intent": "formulate_response", "request": ""})
            summary["participation_request"] = {"http_status": result.status,
                "job_id": (result.payload.get("job") or {}).get("id") if isinstance(result.payload.get("job"), dict) else None}
            if result.status not in {200, 202}:
                summary["failures"].append({"layer": "participation_request", "http_status": result.status})
        try:
            snapshot, _, _, _ = wait_for_intelligence(client, meeting_id=args.meeting_id, timeout_seconds=60, poll_interval_seconds=0.5)
        except ReplayFailure as error:
            summary["failures"].append({"layer": error.layer, "error": str(error)})
        save("before-end-snapshot.json", snapshot)
    except ReplayFailure as error:
        summary["failures"].append({"layer": error.layer, "error": str(error)})
        if error.transport_stats:
            summary["transport"] = error.transport_stats
    except Exception as error:
        summary["failures"].append({"layer": "harness", "error_class": type(error).__name__})
    finally:
        if created:
            result = end_meeting_with_retry(client, meeting_id=args.meeting_id, timeout_seconds=60, poll_interval_seconds=0.5)
            summary["end_http_status"] = result.status
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                snapshot = client.request("GET", f"/v2/meetings/{quote(args.meeting_id,safe='')}/snapshot?segment_limit=500").payload
                if not any(job.get("status") in {"pending", "running", "retry_wait"} for job in snapshot.get("jobs", [])):
                    break
                time.sleep(0.5)
            evidence = collect_evidence(client, args.meeting_id)
            segments = evidence["transcript"].get("segments") or []
            decisions = extract_decisions(evidence["events"])
            summary["transcript"] = {"segment_count": len(segments), "correction_status_counts": dict(Counter(str(item.get("correction_status")) for item in segments)),
                                      "changed_segment_count": sum(item.get("text") != item.get("normalized_text") for item in segments)}
            summary["jobs"] = dict(Counter(f"{job.get('kind')}:{job.get('status')}" for job in evidence["snapshot"].get("jobs", [])))
            summary["coach"] = []
            for item in decisions:
                decision = item.get("coach_decision") or {}
                summary["coach"].append({"job_id": item.get("job_id"), "outcome": item.get("outcome"),
                    **{key: decision.get(key) for key in ("status", "llm_called", "origin", "status_reason", "decision_reason", "ttft_ms", "decision_latency_ms")}})
            summary["pipeline_observed"] = bool(segments and summary["transport"].get("non_empty_final_count"))
            summary["acceptance_scope"] = "existing_file_ASR_transport_and_observed_downstream_execution"
            summary["coach_card_emitted"] = any(item.get("coach_intervention") for item in decisions)
            summary["correction_complete"] = not any(item.get("correction_status") == "pending" for item in segments)
            save("evidence.json", evidence)
        save("asr-ws-events.json", events)
        save("summary.json", summary)
        progress("complete", pipeline_observed=summary.get("pipeline_observed", False), failures=summary["failures"], output_dir=str(output))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--concurrent-load", default="none")
    args = parser.parse_args()
    result = run(args)
    return 0 if result.get("pipeline_observed") and not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
