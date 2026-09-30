#!/usr/bin/env python3
"""Exercise real Answer + Pi through the durable executor, without audio.

Run with the backend venv. Credentials stay in a private provider settings file;
the output contains only synthetic transcript and public result metadata.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "code/web_mvp/backend"), str(ROOT / "code/core")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    args.data_dir = args.data_dir.resolve()
    if args.data_dir.exists():
        parser.error("data-dir must be new; never run acceptance against a user's meeting database")
    config = json.loads(args.provider_config.read_text())
    for field, env in {
        "base_url": "LLM_GATEWAY_BASE_URL", "api_key": "LLM_GATEWAY_API_KEY",
        "model": "LLM_GATEWAY_MODEL", "realtime_model": "LLM_GATEWAY_REALTIME_MODEL",
        "api_style": "LLM_GATEWAY_API_STYLE",
    }.items():
        if config.get(field):
            os.environ[env] = config[field]
    os.environ.update({
        "MEETING_COPILOT_DATA_DIR": str(args.data_dir),
        "MEETING_COPILOT_REALTIME_COACH_RUNTIME": "pi",
        "MEETING_COPILOT_REALTIME_COACH_ENABLED": "1",
        "MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST": "0",
        "MEETING_COPILOT_PI_BRIDGE_PREWARM": "1",
        "MEETING_COPILOT_REALTIME_REFINER_POLICY": "online_only",
        "LLM_CORRECTION_PRICING_MODE": "unmetered",
    })
    from fastapi.testclient import TestClient
    from meeting_copilot_web_mvp.app import app

    meeting = "accept_pi_core_silent_20260930"
    speech = [
        "财政贴息可以降低居民购房的实际融资成本，但贴息对象和适用范围目前还没有明确。银行也可能受益于贷款需求恢复。",
        "目前讨论主要围绕居民负担以及银行收益，居民利息减少和银行利润增加之间仍然存在传导条件，还要关注补贴承担方以及新增贷款的信用风险。",
        "接下来继续讨论贷款风险，需要区分新增需求与原有贷款的置换，不应直接把贷款规模增长等同于利润改善。",
    ]
    with TestClient(app) as client:
        def commit(index: int, text: str):
            return client.portal.call(lambda: app.state.commit_v2_final(meeting, {
                "segment_id": f"speech-{index}", "text": text,
                "start_ms": index * 15_000, "end_ms": (index + 1) * 15_000,
                "source_track": "system_audio",
            }))

        def wait_results(answer_count: int, timeout: float = 55):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                snapshot = app.state.v2_persistence.get_snapshot(meeting, segment_limit=100)
                answers = [a for a in snapshot.get("suggestions", []) if a["kind"] == "answer" and a["status"] == "committed"]
                jobs = app.state.v2_persistence.list_jobs(meeting_id=meeting)
                deep = [job for job in jobs if job.get("trigger_type") == "answer_ready"]
                if len(answers) >= answer_count and len(deep) >= answer_count and all(j["status"] in {"succeeded", "failed", "cancelled"} for j in deep):
                    return
                time.sleep(0.25)

        commit(0, speech[0])
        commit(1, speech[1])
        # Append while the first response/deep task may be in flight.
        time.sleep(2)
        commit(2, speech[2])
        wait_results(1)
        commit(3, "那么银行实际受益需要满足哪些前提？请给一个有依据的回应。")
        time.sleep(2)
        commit(4, "我们还要把居民是否得到实际减负作为政策效果的判断依据。")
        wait_results(2)
        committed = [item for item in app.state.v2_persistence.get_snapshot(meeting).get("suggestions", [])
                     if item["kind"] == "answer" and item["status"] == "committed"]
        revision_job = None
        if committed:
            response = client.post(f"/v2/meetings/{meeting}/coach/request", json={
                "answer_id": committed[-1]["suggestion_id"],
                "request": "请更具体地补充一个追问角度：区分银行体系净新增贷款与银行之间转贷，给我可以直接说出口的追问。不要重复当前回答。",
            })
            response.raise_for_status()
            revision_job = response.json()["job"]["id"]
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if app.state.v2_persistence.get_job(revision_job)["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.25)
        snapshot = client.get(f"/v2/meetings/{meeting}/snapshot").json()
        jobs = app.state.v2_persistence.list_jobs(meeting_id=meeting)
        report = {
            "meeting_id": meeting, "input_mode": "synthetic_text_no_audio", "model": config.get("realtime_model") or config["model"],
            "answers": [{key: a.get(key) for key in ("suggestion_id", "status", "question_text", "text", "ttft_ms", "error_class")} for a in snapshot.get("suggestions", []) if a["kind"] == "answer"],
            "coach_history": snapshot.get("coach_history", []),
            "jobs": [{key: j.get(key) for key in ("id", "kind", "status", "trigger_type", "error_class", "output")} for j in jobs if j["kind"] in {"answer", "intelligence"}],
        }
        (args.data_dir / "acceptance-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"report": str(args.data_dir / "acceptance-report.json"), "answers": report["answers"], "pi_history_count": len(report["coach_history"])}, ensure_ascii=False))
        pi_cards = [item for item in report["coach_history"] if item.get("origin") == "pi" and item.get("prompt_profile") == "deep_answer"]
        success = (len([a for a in report["answers"] if a["status"] == "committed"]) >= 2
                   and len(pi_cards) >= 2 and any(item.get("trigger_type") == "user_request" for item in pi_cards))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
