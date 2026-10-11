"""Device-free, no-model UI check for an explicitly requested failed contribution.

Reuse the established typed publication fixture. This is not model quality,
latency, ASR, or real-user acceptance evidence. No lifespan workers are started.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from coach_ui_render_fixture import ROOT, build_fixture
import workbench_server


def save(path: Path, value: dict, *, fresh: bool = False) -> None:
    with path.open("x" if fresh else "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    if not run.is_relative_to(ROOT / "artifacts/realtime-participation-20261007"):
        raise ValueError("Only the named isolated acceptance artifact tree is allowed")
    plan = {"kind": "public_no_model_requested_failure_ui_fixture", "port": 8770,
            "real_provider_calls": 0, "audio_capture": False, "device_enumeration": False,
            "playback": False, "background_workers": False, "fake_failure_limit": 1}
    if not args.serve:
        print(json.dumps(plan), flush=True)
        return 0
    run.mkdir(parents=True, exist_ok=True)
    if (run / "report.json").exists():
        raise ValueError("Fixture report cannot be restarted or overwritten")
    import uvicorn
    from fastapi import HTTPException

    with build_fixture(run / "runtime") as (app, baseline):
        report = {**plan, "baseline": baseline, "meeting_id": baseline["meeting_id"],
                  "owner_pid": os.getpid(),
                  "process_start_marker": workbench_server.process_start_marker(os.getpid()),
                  "source_fingerprint": workbench_server.runtime_source_fingerprint(),
                  "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "runtime_dir": str(run / "runtime"), "fake_failures": 0,
                  "scope_closed": False, "guarded_device_requests": 0,
                  "guarded_mutations": 0, "explicit_requests": 0}
        mid = report["meeting_id"]
        report["url"] = f"http://127.0.0.1:8770/workbench?meeting_id={mid}"
        path = run / "report.json"
        save(path, report, fresh=True)
        persistence = app.state.v2_persistence

        class RejectedSidecar:
            available = True

            async def evaluate(self, payload):
                paragraph = payload["context"]["context_paragraphs"][-1]
                return {"action": "intervention", "metrics": {"turns": 1, "tool_calls": 1,
                        "tool_names": ["submit_intervention"]}, "intervention": {
                    "event_type": "question_to_user", "title": "公开合成拒绝场景",
                    "urgency": "low", "confidence": .95,
                    "recommendation": "建议核对通知与权益的失败处理边界。",
                    "reason": "公开合成无模型故障模拟，用于核验请求归属与失败提示。",
                    "evidence_segment_ids": [paragraph["id"]], "evidence_quote": paragraph["text"],
                    "supporting_facts": [{"text": paragraph["text"], "source_ref": paragraph["id"]}],
                    "assumptions": ["下载权限校验已经通过。"]}}

        app.state.pi_coach_runtime = RejectedSidecar()

        @app.get("/__fixture/identity")
        def identity():
            return {key: report[key] for key in ("kind", "port", "owner_pid", "process_start_marker",
                    "source_fingerprint", "runtime_dir", "meeting_id", "real_provider_calls", "fake_failures")}

        @app.post("/__fixture/process-next")
        async def process_next():
            if report["fake_failures"] or report["scope_closed"]:
                raise HTTPException(409, "The single simulated failure budget is closed")
            pending = [job for job in persistence.list_jobs(meeting_id=mid)
                       if job["status"] == "pending" and job["trigger_type"] == "user_request"]
            if len(pending) != 1:
                raise HTTPException(409, "Exactly one explicit browser request is required")
            worker = "public-outcome-ui-fixture"
            running = persistence.claim_next_job(worker_id=worker, lane="pi_deep",
                        now_ms=time.time_ns() // 1_000_000, lease_ms=30_000)
            if running is None or running["id"] != pending[0]["id"]:
                raise HTTPException(409, "The queued request identity did not match")
            report["fake_failures"] = 1
            output = await app.state.v2_intelligence_job_handler_impl(running)
            if not persistence.complete_job(job_id=running["id"], worker_id=worker,
                    now_ms=time.time_ns() // 1_000_000, output=output):
                raise HTTPException(409, "The typed fixture request did not settle")
            # No generated/rejected text is copied to this report.
            report["failed_job_id"] = running["id"]
            save(path, report)
            return {"job_id": running["id"], "fake_failures": 1, "real_provider_calls": 0}

        @app.post("/__fixture/close-scope")
        def close_scope():
            # This fixture has no lifespan/automatic consumers or end processing.
            with persistence._write_transaction():
                persistence._conn.execute("UPDATE meetings SET state='ended', ended_at_ms=? WHERE id=?",
                                          (time.time_ns() // 1_000_000, mid))
            report["scope_closed"] = True
            save(path, report)
            return {"scope_closed": True, "real_provider_calls": 0}

        class DeviceGuard:
            async def __call__(self, scope, receive, send):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                route = scope.get("path", "").casefold()
                if scope["type"] != "http":
                    await app(scope, receive, send)
                    return

                async def protected_send(message):
                    if message["type"] == "http.response.start":
                        headers = [(key, value) for key, value in message.get("headers", [])
                                   if key.lower() != b"permissions-policy"]
                        headers.append((b"permissions-policy",
                                        b"microphone=(), camera=(), display-capture=()"))
                        message = {**message, "headers": headers}
                    await send(message)

                async def reject(counter, reason):
                    report[counter] += 1
                    save(path, report)
                    await protected_send({"type": "http.response.start", "status": 403, "headers": []})
                    await protected_send({"type": "http.response.body", "body": reason})

                if ("audio" in route or "device" in route or route.startswith("/live/asr/")
                        or route.startswith("/providers/asr")):
                    await reject("guarded_device_requests", b"Device and ASR access is disabled in this fixture")
                    return
                if scope.get("method", "GET") not in {"GET", "HEAD", "OPTIONS"}:
                    owned_receipts = {baseline["job_id"], report.get("failed_job_id")}
                    allowed = (scope["method"] == "POST" and (
                        route in {"/__fixture/process-next", "/__fixture/close-scope"}
                        or any(route == f"/v2/traces/{job}/ui-rendered" for job in owned_receipts if job)))
                    if scope["method"] == "POST" and route == f"/v2/meetings/{mid}/coach/request":
                        allowed = not report["explicit_requests"] and not report["scope_closed"]
                        if allowed:
                            report["explicit_requests"] = 1
                            save(path, report)
                    if not allowed:
                        await reject("guarded_mutations", b"Only the single owned UI request and receipt are allowed")
                        return
                await app(scope, receive, protected_send)

        print(json.dumps({"url": report["url"], "owner_pid": report["owner_pid"],
                          "runtime_dir": report["runtime_dir"], "real_provider_calls": 0}), flush=True)
        uvicorn.run(DeviceGuard(), host="127.0.0.1", port=8770, lifespan="off", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
