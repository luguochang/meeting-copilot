"""Serve a public, device-free, fake-provider receipt fixture on isolated 8770.

Dry-run by default. This tool is not a model quality or latency acceptance run.
It runs the normal typed participation handler and publication transaction once,
so its server memory owns a real trace; it never backfills a historical trace.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "code/web_mvp/backend"), str(ROOT / "code/core"), str(ROOT / "tools")]
SOURCE_TEXT = "用户付款后应立即看到权益，通知可以稍后发送，本轮先讨论方案边界和重试风险。"


def fixture_plan():
    return {"kind": "public_synthetic_no_model_ui_receipt_fixture", "port": 8770,
        "real_model_calls": 0, "audio_capture": False, "lifespan_background_workers": False,
        "input": {"characters": len(SOURCE_TEXT), "segments": 1, "is_mock": True},
        "policy": {"live_analysis_profile": "participation", "proactive_suggestion_policy": "off",
            "automatic_enabled": 0, "discussion_state_enabled": 0},
        "path": "normal typed handler -> canonical publication -> current HTTP snapshot -> actual DOM ACK",
        "measurement": "fresh fixture trace only; simulated provider stages are not real model timings",
        "history": "no historical event marker, receipt, ACK or source is modified"}


def audit_fixture(report_path: Path):
    """Audit existing evidence offline; never emit a trace acknowledgement."""
    fixture = json.loads(report_path.read_text(encoding="utf-8"))
    runtime = Path(fixture["runtime_dir"]).resolve()
    if not runtime.is_relative_to(ROOT / "artifacts/realtime-participation-20261007") or not runtime.name.startswith("ui-render-fixture-runtime-"):
        raise ValueError("Audit is limited to the public isolated fixture runtime")
    trace_path = report_path.with_name(report_path.stem + "-trace.json")
    release_path = report_path.with_name(report_path.stem + "-release.json")
    release = json.loads(release_path.read_text(encoding="utf-8-sig")) if release_path.is_file() else {"verification": "not recorded"}
    trace_response = json.loads(trace_path.read_text(encoding="utf-8-sig"))
    trace = next(item for item in trace_response["traces"] if item["job_id"] == fixture["job_id"])
    mark = trace["stages"].get("ui_rendered") or {}
    provenance = trace["execution"]["provenance"].get("ui_render_ack") or {}
    screenshot = ROOT / "docs/assets/realtime-participation-20261007/snapshot-render-receipt-fixture-p4.jpg"
    stdout_path = report_path.with_name(report_path.stem + ".stdout.log")
    request_log = []
    for line in stdout_path.read_text(encoding="utf-8").splitlines():
        try:
            request_log.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    ack_path = f"/v2/traces/{fixture['job_id']}/ui-rendered"
    ack_requests = [item for item in request_log if item.get("event") == "request.start" and item.get("path") == ack_path]
    ack_responses = [item for item in request_log if item.get("event") == "request.end" and item.get("path") == ack_path]
    with sqlite3.connect((runtime / "meeting_copilot.db").as_uri() + "?mode=ro", uri=True) as connection:
        state, ended_at_ms = connection.execute("SELECT state,ended_at_ms FROM meetings WHERE id=?", (fixture["meeting_id"],)).fetchone()
        jobs = connection.execute("SELECT id,kind,status,attempts FROM jobs WHERE meeting_id=?", (fixture["meeting_id"],)).fetchall()
        event_type, payload_json = connection.execute("SELECT type,payload_json FROM meeting_events WHERE meeting_id=? AND seq=?",
            (fixture["meeting_id"], fixture["receipt"]["event_seq"])).fetchone()
        meeting_count = connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    canonical = json.loads(payload_json)
    paths = [report_path, trace_path, screenshot, ROOT / "code/web_mvp/backend/meeting_copilot_web_mvp/app.py",
        ROOT / "code/web_mvp/backend/meeting_copilot_web_mvp/v2_persistence.py",
        ROOT / "code/web_mvp/backend/meeting_copilot_web_mvp/coach_ui_render_receipt.py",
        ROOT / "code/web_mvp/frontend_v2/dist/assets/index-CMolpiYr.js", Path(__file__).resolve()]
    if release_path.is_file():
        paths.append(release_path)
    hashes = [{"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
    passed = bool(mark.get("attributes") == fixture["receipt"]
        and mark.get("monotonic_ns") == provenance.get("at_monotonic_ns")
        and provenance.get("attributes", {}).get("source") == "formal_coach_ui.v1"
        and len(ack_requests) == len(ack_responses) == 1 and ack_responses[0]["status_code"] == 200
        and state == "ended" and meeting_count == 1 and event_type == "meeting.intelligence.applied"
        and release.get("owner_pid") == fixture["owner_pid"] and release.get("listener_absent") is True
        and canonical.get("ui_render_measurement_version") == "formal_coach_ui.v1"
        and canonical.get("ui_render_input_version") == fixture["receipt"]["input_version"])
    followup = {"kind": "public_no_model_current_dom_receipt_evidence", "contract_passed": passed,
        "meeting_id": fixture["meeting_id"], "job_id": fixture["job_id"], "receipt": fixture["receipt"],
        "actual_browser_ack_requests": len(ack_requests), "actual_browser_ack_response_codes": [item["status_code"] for item in ack_responses],
        "host_first_mark": mark, "matching_provenance": provenance, "real_model_calls": 0,
        "simulated_sidecar_evaluations": fixture["fake_sidecar_evaluations"],
        "simulated_provider_attempts_in_host_trace": len(trace["execution"]["provider_attempts"]),
        "browser_observation": "Root observed initial snapshot-first current card and matching recommendation DOM; actual screenshot retained",
        "event_transport_boundary": "An events GET occurred before ACK; its query/response body was not retained, so no claim of zero formal event delivery",
        "event_to_ui_ack_ms": (mark.get("monotonic_ns", 0) - trace["stages"]["event_emitted"]["monotonic_ns"]) / 1_000_000,
        "latency_boundary": "Includes manual wait before opening the tab; not Provider latency or product DOM SLO",
        "assistant_ack_post_calls": 0, "historical_ack_backfills": 0,
        "scope": {"state": state, "ended_at_ms": ended_at_ms, "meetings_in_isolated_runtime": meeting_count,
            "jobs": [{"id": row[0], "kind": row[1], "status": row[2], "attempts": row[3]} for row in jobs]},
        "owner_release": release,
        "hash_boundary": "Artifact and source hashes were read during this offline follow-up; no loaded-runtime tool source hash was captured before launch",
        "hashes": hashes}
    output_path = report_path.with_name(report_path.stem + "-followup.json")
    output_path.write_text(json.dumps(followup, ensure_ascii=False, indent=2), encoding="utf-8")
    return followup


@contextmanager
def build_fixture(data_dir: Path):
    from fastapi.testclient import TestClient
    from meeting_copilot_web_mvp import app as app_module
    from participation_value_acceptance import create_synthetic_live_record, seed_synthetic_final

    data_dir = data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("Fixture requires a new empty isolated data directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    meeting_id = f"synthetic_ui_receipt_{uuid.uuid4().hex[:12]}"
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid",
        api_key="synthetic-no-network", model="synthetic-model", timeout_seconds=25, is_mock=True)
    evaluations = []

    class FakeSidecar:
        available = True

        async def evaluate(self, payload):
            evaluations.append(payload["context"]["trigger_type"])
            paragraph = payload["context"]["context_paragraphs"][-1]
            return {"action": "intervention", "metrics": {"turns": 1, "tool_calls": 1,
                    "tool_names": ["submit_intervention"]},
                "intervention": {"event_type": "question_to_user", "title": "合成验收：核对通知失败边界",
                    "urgency": "low", "confidence": .95,
                    "recommendation": "建议先把权益与通知的失败处理边界分开，核对通知重试对权益发放结果的影响。",
                    "reason": "公开合成无模型夹具。原话允许通知稍后发送，可以单独讨论通知失败路径。",
                    "evidence_segment_ids": [paragraph["id"]], "evidence_quote": paragraph["text"],
                    "supporting_facts": [{"text": "通知可以稍后发送", "source_ref": paragraph["id"]}],
                    "assumptions": []}}

    async def no_semantic_call(**_kwargs):
        raise AssertionError("The no-model fixture must not invoke semantic analysis")

    env = {"MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED": "0",
        "MEETING_COPILOT_DISCUSSION_STATE_ENABLED": "0", "MEETING_COPILOT_PI_BRIDGE_PREWARM": "0",
        "MEETING_COPILOT_LOCAL_API_TOKEN": "",
        "MEETING_COPILOT_REALTIME_COACH_RUNTIME": "pi", "MEETING_COPILOT_REALTIME_COACH_ENABLED": "1",
        "MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST": "0", "LLM_GATEWAY_API_KEY": "synthetic-no-network",
        "LLM_GATEWAY_BASE_URL": "https://synthetic.invalid", "LLM_GATEWAY_MODEL": "synthetic-model"}
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("No remote outbound network in public UI fixture")
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("No remote outbound network in public UI fixture")
        return original_connect_ex(sock, address)

    with ExitStack() as stack:
        # Windows' asyncio self-pipe uses an internal loopback socket pair.
        stack.enter_context(patch("socket.socket.connect", guarded_connect))
        stack.enter_context(patch("socket.socket.connect_ex", guarded_connect_ex))
        stack.enter_context(patch.dict(os.environ, env))
        stack.enter_context(patch.object(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config)))
        # This isolated fixture has public synthetic text and a fake transport;
        # preserve its mock provenance rather than misrepresenting it as ASR.
        stack.enter_context(patch.object(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_k: None))
        stack.enter_context(patch.object(app_module, "run_realtime_intelligence", no_semantic_call))
        app = app_module.create_app(data_dir=data_dir, semantic_projection_mode="llm_first", prewarm_funasr=False)
        app.state.pi_coach_runtime = FakeSidecar()
        app.state.streaming_llm_client = object()  # Any unexpected network fallback fails closed.
        p = app.state.v2_persistence
        now = time.time_ns() // 1_000_000
        p.create_meeting(meeting_id=meeting_id, title="公开合成：呈现回执验收（无模型、无音频）", now_ms=now)
        client = TestClient(app)  # No lifespan: no workers, prewarm or scheduler.
        try:
            prep = client.put(f"/v2/meetings/{meeting_id}/preparation", json={
                "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"})
            prep.raise_for_status()
            repository = app.state.asr_live_repository
            create_synthetic_live_record(repository, meeting_id)
            # The source helper cancels incidental lanes in its transaction.
            # Its nesting support is needed only while seeding, not in production.
            from participation_value_acceptance import open_synthetic_store
            seed_store = open_synthetic_store(data_dir / "meeting_copilot.db")
            try:
                seeded = seed_synthetic_final(seed_store, repository, meeting_id,
                    {"segment_id": "public-fixture-source", "text": SOURCE_TEXT}, 1)
            finally:
                seed_store.close()
            queued = p.enqueue_user_coach_request(meeting_id=meeting_id, user_request="", intent="ask_question",
                idempotency_key=f"{meeting_id}:publication", now_ms=time.time_ns() // 1_000_000)
            running = p.claim_next_job(worker_id="public-no-model-fixture", lane="pi_deep",
                now_ms=time.time_ns() // 1_000_000, lease_ms=30_000)
            if running is None or running["id"] != queued["id"]:
                raise AssertionError("The isolated typed fixture job was not claimed")
            output = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
            if not p.complete_job(job_id=queued["id"], worker_id="public-no-model-fixture",
                    now_ms=time.time_ns() // 1_000_000, output=output):
                raise AssertionError("Fixture publication did not complete")
            snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()
            card = snapshot.get("coach_intervention") or {}
            receipt = card.get("ui_render_receipt")
            if not receipt or card.get("job_id") != queued["id"]:
                raise AssertionError("No current typed fixture publication receipt")
            trace = app.state.pipeline_traces.get(queued["id"])
            if "ui_rendered" in trace.stage_marks:
                raise AssertionError("The fixture must leave DOM acknowledgement to the actual browser")
            jobs = p.list_jobs(meeting_id=meeting_id)
            if evaluations != ["user_request"] or any(job["id"] in seeded["cancelled_job_ids"] and job["attempts"] != 0 for job in jobs):
                raise AssertionError("Unexpected fixture execution budget")
            report = {**fixture_plan(), "runtime_dir": str(data_dir), "owner_pid": os.getpid(), "owner_parent_pid": os.getppid(),
                "meeting_id": meeting_id, "job_id": queued["id"], "receipt": receipt,
                "url": f"http://127.0.0.1:8770/workbench?meeting_id={meeting_id}",
                "valid_until_ms": card["valid_until_ms"], "source_hash": seeded["source_hash"],
                "seed_jobs": [{"id": job["id"], "status": job["status"], "attempts": job["attempts"]}
                    for job in jobs if job["id"] in seeded["cancelled_job_ids"]],
                "fake_sidecar_evaluations": len(evaluations), "host_instrumentation": "simulated provider attempt; no upstream request",
                "trace_created_by": "normal typed handler", "ui_rendered_before_browser": False}
            yield app, report
        finally:
            client.close()
            p.close()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--audit", action="store_true")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/ui-render-fixture-plan.json")
    args = parser.parse_args()
    report_path = (ROOT / args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if args.audit:
        result = audit_fixture(report_path)
        print(json.dumps({"contract_passed": result["contract_passed"], "actual_browser_ack_requests": result["actual_browser_ack_requests"],
            "real_model_calls": 0, "scope_state": result["scope"]["state"]}), flush=True)
        return 0 if result["contract_passed"] else 1
    if not args.serve:
        report_path.write_text(json.dumps(fixture_plan(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(fixture_plan(), ensure_ascii=False), flush=True)
        return 0
    import uvicorn
    data_dir = ROOT / "artifacts/realtime-participation-20261007" / f"ui-render-fixture-runtime-{uuid.uuid4().hex[:8]}"
    with build_fixture(data_dir) as (app, report):
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        uvicorn.run(app, host="127.0.0.1", port=8770, lifespan="off", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
