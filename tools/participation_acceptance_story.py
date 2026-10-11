"""Exercise the isolated 20261007 candidate with synthetic, device-free evidence.

This script seeds transcript evidence through the same persistence contract,
then uses real HTTP, durable execution and the configured Pi provider. It is
not an ASR or microphone acceptance test. No credentials enter its report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code/web_mvp/backend"))
sys.path.insert(0, str(ROOT / "code/core"))
sys.path.insert(0, str(ROOT / "tools"))
RUNTIME = ROOT / "artifacts/realtime-participation-20261007/runtime"

from participation_value_acceptance import (  # noqa: E402
    create_synthetic_live_record,
    open_synthetic_store,
    provider_attempt_count,
    seed_synthetic_final,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/http-story.json")
    args = parser.parse_args()
    if args.base_url not in {"http://127.0.0.1:8768", "http://localhost:8768"}:
        raise ValueError("Only the named isolated acceptance port is allowed")
    if not (RUNTIME / "meeting_copilot.db").is_file():
        raise ValueError("Isolated acceptance database is missing")
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository

    report = {"scope": "synthetic transcript; real HTTP/durable/Pi; no microphone/ASR",
              "started_at_ms": time.time_ns() // 1_000_000, "stories": [],
              "seed_provenance": "synthetic text mirrored through existing live repository; no ASR result is fabricated",
              "budget": {"manual_request_limit": 3, "incidental_seed_model_lanes": "cancelled atomically", "provider_retries": 0}}
    client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=40)
    store = None
    live_repository = None

    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    try:
        material_set = call("POST", "/v2/material-sets", json={})["material_set"]
        set_id = material_set["id"]
        material = "# 订单重试需求\n\n用户付款后立即显示权益，通知允许稍后送达。\n\n# 约束\n不引入复杂基础设施，尽量复用现有服务。重复请求不能重复扣款。\n"
        upload = call("POST", f"/v2/material-sets/{set_id}/attachments", files={
            "file": ("订单重试需求.md", material.encode("utf-8"), "text/markdown")},
            data={"title": "订单重试需求", "purpose": "本场需求和边界"})
        attachment_id = upload["attachment"]["id"]
        for _ in range(45):
            detail = call("GET", f"/v2/material-sets/{set_id}/attachments/{attachment_id}")
            if detail.get("version") or (detail.get("job") or {}).get("state") in {"failed", "cancelled"}:
                break
            time.sleep(1)
        if not detail.get("version"):
            raise RuntimeError("Material conversion did not produce a preview")
        version_id = detail["version"]["id"]
        call("PUT", f"/v2/material-sets/{set_id}/attachments/{attachment_id}/usage", json={
            "version_id": version_id, "enabled": True, "selected_span_ids": None,
            "expected_revision": detail["attachment"]["revision"]})
        set_detail = call("GET", f"/v2/material-sets/{set_id}")
        meeting_id = f"participation_story_{uuid.uuid4().hex[:12]}"
        create_payload = {"meeting_id": meeting_id, "title": "模拟验收：权益与通知需求评审",
            "expected_duration_seconds": 60, "material_set_id": set_id,
            "expected_material_set_revision": set_detail["material_set"]["revision"]}
        headers = {"Idempotency-Key": meeting_id}
        created = call("POST", "/v2/meetings", json=create_payload, headers=headers)
        repeated = call("POST", "/v2/meetings", json=create_payload, headers=headers)
        report.update(meeting_id=meeting_id, material_set_id=set_id, attachment_id=attachment_id,
                      creation_replay_same_id=created["meeting"]["id"] == repeated["meeting"]["id"])
        store = open_synthetic_store(RUNTIME / "meeting_copilot.db")
        live_repository = SqliteAsrLiveSessionRepository(RUNTIME)
        create_synthetic_live_record(live_repository, meeting_id)
        text = "我们做付款成功后的权益发放。用户付款后要立即显示权益，通知可以稍后送达。现在计划把权益和通知放在同一个同步流程里，任何一步失败就整体重试。"
        report["seed"] = seed_synthetic_final(store, live_repository, meeting_id,
            {"segment_id": "synthetic-segment-1", "text": text, "started_at_ms": 0, "ended_at_ms": 15_000}, 1)
        for intent in ("ask_question", "compare_options", "formulate_response"):
            started = time.perf_counter()
            queued = call("POST", f"/v2/meetings/{meeting_id}/coach/request", json={"intent": intent},
                          headers={"Idempotency-Key": f"{meeting_id}:{intent}"})
            job_id = queued["job"]["id"]
            for _ in range(75):
                job = store.get_job(job_id)
                if job["status"] in {"done", "completed", "succeeded", "failed", "cancelled", "superseded"}:
                    break
                time.sleep(1)
            snapshot = call("GET", f"/v2/meetings/{meeting_id}/snapshot")
            story = {"intent": intent, "job_id": job_id, "status": job["status"],
                     "error_class": job.get("error_class"), "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                     "snapshot": snapshot}
            output = job.get("output") or {}
            own_decision = output.get("coach") or {}
            story["decision_status"] = own_decision.get("status")
            story["validation_error"] = (own_decision.get("agent_metrics") or {}).get("response_validation_error")
            story["provider_attempt_count"] = provider_attempt_count(job)
            story["snapshot_decision_status"] = (snapshot.get("coach_decision") or {}).get("status")
            story["has_contribution"] = bool(snapshot.get("coach_intervention"))
            story["decision_for_request"] = (snapshot.get("coach_decision") or {}).get("job_id") == job_id
            story["contribution_for_request"] = (snapshot.get("coach_intervention") or {}).get("job_id") == job_id
            story["typed_contribution"] = (snapshot.get("coach_intervention") or {}).get("analysis_kind") == "participation"
            report["stories"].append(story)
            # Failed execution is evidence, not a passed story.
            print(json.dumps({key: story[key] for key in ("intent", "job_id", "status", "decision_status", "has_contribution", "error_class", "elapsed_ms")}, ensure_ascii=False), flush=True)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
        print(json.dumps(report["failure"], ensure_ascii=False), flush=True)
    finally:
        if live_repository is not None:
            live_repository.close()
        if store is not None:
            if report.get("meeting_id"):
                report["observed_jobs"] = [{"id": job["id"], "kind": job["kind"], "status": job["status"],
                    "trigger_type": job.get("trigger_type"), "provider_attempt_count": provider_attempt_count(job)}
                    for job in store.list_jobs(meeting_id=report["meeting_id"])]
                report["observed_provider_attempts"] = sum(job["provider_attempt_count"] for job in report["observed_jobs"])
                report["observation_ended_at_ms"] = time.time_ns() // 1_000_000
            store.close()
        destination = ROOT / args.report
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        client.close()
    return int(bool(report.get("failure")) or len(report["stories"]) != 3 or any(
        item["status"] != "succeeded" or item["decision_status"] != "intervention" or not item["has_contribution"]
        or not item["decision_for_request"] or not item["contribution_for_request"] or not item["typed_contribution"]
        for item in report["stories"]))


if __name__ == "__main__":
    raise SystemExit(main())
