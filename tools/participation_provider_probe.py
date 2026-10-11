"""One device-free, synthetic-only Pi terminal diagnostic; never logs config."""
from __future__ import annotations

import asyncio
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "code/web_mvp/backend"), str(ROOT / "code/core")]


async def run(args):
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
    from meeting_copilot_web_mvp.llm_service import LlmConfig
    from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar, build_pi_coach_request
    from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
    from meeting_copilot_web_mvp.realtime_intelligence import RealtimeIntelligenceRequest, parse_realtime_coach_response
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence

    path = ROOT / "artifacts/realtime-participation-20261007/runtime/meeting_copilot.db"
    previous = json.loads((ROOT / args.story_report).read_text(encoding="utf-8"))
    meeting_id = previous["meeting_id"]
    if not meeting_id.startswith("participation_story_"):
        raise ValueError("Diagnostic only accepts the named synthetic story")
    store = V2Persistence(path, semantic_projection_mode="llm_first")
    knowledge = KnowledgeRepository(path)
    story = next(item for item in previous["stories"] if item["intent"] == args.intent)
    job = store.get_job(story["job_id"])
    metadata = job["request_context"]
    snapshot = store.get_snapshot(meeting_id)
    paragraphs = [{"id": segment["segment_id"], "text": segment["normalized_text"], "revision": segment["revision"]}
                  for segment in snapshot["segments"]]
    context = RealtimeIntelligenceRequest.from_payload(meeting_id=meeting_id, state_revision=snapshot["last_seq"],
        new_paragraphs=paragraphs[-1:], context_paragraphs=paragraphs[-4:-1], rolling_state={"discussion": metadata["discussion_context"]},
        trigger_type="user_request", user_request=job["user_request"], coach_profile="participation", request_metadata=metadata,
        document_evidence=metadata["source_evidence"], knowledge_context=metadata.get("material_context") or {})
    runtime = PiCoachSidecar(timeout_seconds=30, evidence_registry_factory=lambda mid: PiEvidenceRegistry(store, meeting_id=mid, knowledge_repository=knowledge))
    config = LlmConfig.from_env()
    payload = build_pi_coach_request(context, request_id=f"diagnostic:{time.time_ns()}", base_url=config.base_url,
        api_key=config.api_key, model=config.model, api_style=config.api_style, timeout_seconds=25, priority_mode="deep")
    report = {"scope": "single synthetic Pi diagnostic; not microphone/ASR", "model": config.model}
    try:
        result = await runtime.evaluate(payload)
        report["result"] = result
        updated = replace(context, document_evidence=tuple(result.get("host_source_evidence") or ()))
        raw = result.get("intervention") or {}
        try:
            accepted = parse_realtime_coach_response(json.dumps({"intervention": raw}, ensure_ascii=False), request=updated)
            report["accepted"] = accepted.to_dict() if accepted else None
        except ValueError as exc:
            report["validation_error"] = str(exc)
            ids = raw.get("evidence_segment_ids") or []
            sources = {paragraph.id: paragraph.text for paragraph in updated.paragraphs_by_id.values() if paragraph.id in ids}
            sources.update({source["source_id"]: source["quote"] for source in updated.document_evidence})
            report["source_comparison"] = [{"fact": fact, "known_ref": fact.get("source_ref") in sources,
                "verbatim": fact.get("text", "") in sources.get(fact.get("source_ref"), "")}
                for fact in raw.get("supporting_facts") or []]
        print(json.dumps({"action": result.get("action"), "validation_error": report.get("validation_error"),
            "source_comparison": report.get("source_comparison"), "accepted": report.get("accepted")}, ensure_ascii=False), flush=True)
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
        print(json.dumps(report["error"]), flush=True)
    finally:
        runtime.close()
        store.close()
        knowledge.close()
        (ROOT / args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", choices=("ask_question", "compare_options", "formulate_response"), default="ask_question")
    parser.add_argument("--story-report", default="artifacts/realtime-participation-20261007/http-story-r03.json")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/provider-terminal-probe-r02.json")
    asyncio.run(run(parser.parse_args()))
