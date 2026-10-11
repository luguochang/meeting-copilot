"""Isolated, public no-model fixture for attachment revision and reader lifecycle.

Dry-run is the default. No production application/frontend routes are changed.
Serving creates one new public scope; it never replays or repairs old jobs.
The normal attachment API/converter, typed handler and publication are used.
Fixed synthetic suggestions prove UI wiring only, never model value or speed.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
import copy
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "code/web_mvp/backend"), str(ROOT / "code/core"), str(ROOT / "tools")]
SOURCE_TEXT = "这次讨论付款后的权益与通知。先核对需求附件里的通知条件，再讨论失败恢复与重复处理的边界。"
MATERIAL_V1 = "# 公开合成需求\n\n用户付款后立即显示权益。\n\n通知允许在权益成功后补发。\n\n重试不能重复发放权益。\n"
MATERIAL_V2 = "# 公开合成需求\n\n用户付款后立即显示权益。\n\n通知必须在权益成功后及时发送。\n\n重试不能重复发放权益。\n"
MAX_PUBLICATIONS = 4


def fixture_plan():
    return {
        "kind": "public_synthetic_no_model_material_lifecycle_fixture", "port": 8770,
        "real_model_calls": 0, "audio_capture": False, "asr_started": False,
        "lifespan_background_workers": False, "production_code_modified": False,
        "policy": {"live_analysis_profile": "participation", "proactive_suggestion_policy": "off",
                   "automatic_enabled": 0, "discussion_state_enabled": 0},
        "input": {"segments": 1, "is_mock": True, "material_format": "markdown",
                  "material_characters": len(MATERIAL_V1)},
        "publication_budget": {"initial": 1, "explicit_user_requests_only_afterwards": MAX_PUBLICATIONS - 1},
        "clock": "normal unmodified publication TTL; no refresh or extension of old jobs",
        "path": "official upload/conversion/usage/create+bind -> manual route -> typed fake Sidecar -> normal publication",
        "scope": "one new isolated meeting/set only; remote sockets fail closed; no automatic scheduler",
        "acceptance": "material/source/coverage/reading lifecycle only, not model quality, audio or response latency",
        "suggested_v2_content": MATERIAL_V2,
        "browser_steps": [
            "Open initial card, expand facts and source; record exact v1 and frozen coverage.",
            "While reading the first card, explicitly ask for another suggestion; switch only via the new-result control.",
            "Edit and save the supplied v2 text without enabling; v1 stays active and the latest card stays valid.",
            "Enable v2 explicitly; v1-based current suggestions withdraw, history keeps original v1 coverage and stale source.",
            "Explicitly ask for a v2 suggestion if needed; inspect the read hold and source version, then refresh.",
        ],
    }


class FixtureController:
    """Consume only explicit participation requests in this single fake scope."""

    def __init__(self, app, client, meeting_id, set_id, attachment_id, evaluations):
        self.app, self.client = app, client
        self.meeting_id, self.set_id, self.attachment_id = meeting_id, set_id, attachment_id
        self.evaluations = evaluations
        self.publications = []
        self.error = None
        self._lock = threading.Lock()

    def process_once(self):
        with self._lock:
            persistence = self.app.state.v2_persistence
            with persistence._lock:
                rows = persistence._conn.execute("SELECT id FROM meetings").fetchall()
            if len(rows) != 1 or rows[0][0] != self.meeting_id:
                raise AssertionError("Fixture pump refuses another meeting scope")
            jobs = persistence.list_jobs(meeting_id=self.meeting_id)
            pending = [job for job in jobs if job["status"] in {"pending", "retry_wait"}
                       and job["kind"] == "intelligence" and job.get("trigger_type") == "user_request"]
            if not pending:
                return None
            if len(self.publications) >= MAX_PUBLICATIONS:
                raise AssertionError("Fixture explicit publication budget exhausted")
            worker = "public-material-lifecycle-fake-only"
            now = time.time_ns() // 1_000_000
            job = persistence.claim_next_job(worker_id=worker, lane="pi_deep", now_ms=now, lease_ms=30_000)
            if job is None:
                return None
            if job["meeting_id"] != self.meeting_id or job.get("trigger_type") != "user_request":
                raise AssertionError("Fixture must never execute an automatic, state or foreign job")
            output = asyncio.run(self.app.state.v2_intelligence_job_handler_impl(job))
            if not persistence.complete_job(job_id=job["id"], worker_id=worker,
                                           now_ms=time.time_ns() // 1_000_000, output=output):
                raise AssertionError("Fixture explicit request did not complete normally")
            snapshot = self.client.get(f"/v2/meetings/{self.meeting_id}/snapshot").json()
            card = snapshot.get("coach_intervention") or {}
            if card.get("job_id") != job["id"] or not card.get("ui_render_receipt"):
                raise AssertionError("Fixture did not publish a current typed material contribution")
            sources = card.get("source_evidence") or []
            if not sources or any(source.get("source_type") != "meeting_attachment"
                                  or source.get("set_id") != self.set_id
                                  or source.get("attachment_id") != self.attachment_id for source in sources):
                raise AssertionError("Fixture publication lost its real attachment source identity")
            coverage = card.get("material_context") or {}
            if not coverage.get("attachment_catalog"):
                raise AssertionError("Fixture publication has no frozen material coverage")
            publication = {"job_id": job["id"], "decision_id": card["decision_id"],
                           "receipt": card["ui_render_receipt"], "valid_until_ms": card["valid_until_ms"],
                           "source_versions": sorted({source["version_id"] for source in sources}),
                           "material_context": coverage}
            self.publications.append(publication)
            return publication

    def snapshot(self):
        return self.client.get(f"/v2/meetings/{self.meeting_id}/snapshot").json()

    def report(self):
        return {**fixture_plan(), "runtime_dir": str(self.app.state.fixture_runtime_dir),
                "owner_pid": os.getpid(), "owner_parent_pid": os.getppid(), "meeting_id": self.meeting_id,
                "material_set_id": self.set_id, "attachment_id": self.attachment_id,
                "url": f"http://127.0.0.1:8770/workbench?meeting_id={self.meeting_id}",
                "publications": copy.deepcopy(self.publications),
                "fake_sidecar_evaluations": copy.deepcopy(self.evaluations), "pump_error": self.error,
                "host_instrumentation": "simulated provider stages/attempts; no upstream requests",
                "historical_jobs_replayed": 0, "assistant_ui_ack_posts": 0}


@contextmanager
def build_fixture(data_dir: Path):
    from fastapi.testclient import TestClient
    from meeting_copilot_web_mvp import app as app_module
    from meeting_copilot_web_mvp.knowledge_jobs import CONVERSION_WORKER_LOCK
    from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar
    from participation_value_acceptance import create_synthetic_live_record, open_synthetic_store, seed_synthetic_final

    data_dir = data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("Fixture requires a new empty isolated data directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    meeting_id = f"synthetic_material_lifecycle_{uuid.uuid4().hex[:12]}"
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-no-network",
                                             model="synthetic-model", timeout_seconds=25, is_mock=True)
    evaluations = []

    def no_bridge_process(*_args, **_kwargs):
        raise AssertionError("No bridge process or provider request in the public material fixture")

    class FakeSidecar(PiCoachSidecar):
        def __init__(self, evidence_registry_factory):
            super().__init__(command=["synthetic-fake-transport"], process_factory=no_bridge_process,
                             evidence_registry_factory=evidence_registry_factory)

        async def evaluate(self, payload):
            context = payload["context"]
            if context["trigger_type"] != "user_request" or len(evaluations) >= MAX_PUBLICATIONS:
                raise AssertionError("No-model fixture only permits bounded explicit participation requests")
            sources = copy.deepcopy(context.get("document_evidence") or [])
            if not sources or any(item.get("source_type") != "meeting_attachment"
                                  or item.get("meeting_id") != meeting_id for item in sources):
                raise AssertionError("No-model fixture requires genuine frozen meeting attachment evidence")
            if payload.get("session_id") != meeting_id or self._evidence_registry_factory is None:
                raise AssertionError("No-model fixture requires its own host registry factory")
            registry = self._evidence_registry_factory(payload["session_id"])
            registry.register_documents(sources, max_sources=12, max_characters=8000)
            sources = registry.validate_documents([{"source_id": item["source_id"], "quote": item["quote"]}
                                                   for item in sources])
            paragraph = context["context_paragraphs"][-1]
            recommendations = [
                "建议先确认通知补发与权益确认的失败边界，避免通知故障引发权益重复处理。",
                "建议把权益确认与通知发送分别作为恢复范围，比较各自重复处理的影响。",
                "建议根据当前通知约束重新核对失败恢复，再比较主流程重试与独立恢复的取舍。",
                "建议先核对当前资料与恢复方案的约束，再确认通知失败是否影响权益结果。",
            ]
            evaluations.append({"trigger": "user_request", "source_version_ids": sorted({s["version_id"] for s in sources}),
                                "source_ids": [s["source_id"] for s in sources]})
            return {"action": "intervention", "host_source_evidence": sources,
                    "metrics": {"turns": 1, "tool_calls": 1,
                        "tool_names": ["submit_intervention"]},
                    "intervention": {"event_type": "question_to_user", "title": "公开合成：核对通知恢复边界",
                        "urgency": "low", "confidence": .95, "recommendation": recommendations[len(evaluations) - 1],
                        "reason": "公开无模型夹具：按本场启用资料核对通知条件与恢复边界。",
                        "evidence_segment_ids": [paragraph["id"]], "evidence_quote": paragraph["text"],
                        "source_evidence": sources,
                        "supporting_facts": [{"text": source["quote"], "source_ref": source["source_id"]} for source in sources],
                        "assumptions": []}}

    async def no_semantic_call(**_kwargs):
        raise AssertionError("The no-model fixture must not invoke semantic analysis")

    env = {"MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED": "0", "MEETING_COPILOT_DISCUSSION_STATE_ENABLED": "0",
           "MEETING_COPILOT_PI_BRIDGE_PREWARM": "0", "MEETING_COPILOT_LOCAL_API_TOKEN": "",
           "MEETING_COPILOT_REALTIME_COACH_RUNTIME": "pi", "MEETING_COPILOT_REALTIME_COACH_ENABLED": "1",
           "MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST": "0", "LLM_GATEWAY_API_KEY": "synthetic-no-network",
           "LLM_GATEWAY_BASE_URL": "https://synthetic.invalid", "LLM_GATEWAY_MODEL": "synthetic-model"}
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("No remote outbound network in public material fixture")
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("No remote outbound network in public material fixture")
        return original_connect_ex(sock, address)

    with ExitStack() as stack:
        stack.enter_context(patch("socket.socket.connect", guarded_connect))
        stack.enter_context(patch("socket.socket.connect_ex", guarded_connect_ex))
        stack.enter_context(patch.dict(os.environ, env))
        stack.enter_context(patch.object(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config)))
        # Isolated public mock data and fake transport are explicitly disclosed.
        stack.enter_context(patch.object(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_k: None))
        stack.enter_context(patch.object(app_module, "run_realtime_intelligence", no_semantic_call))
        app = app_module.create_app(data_dir=data_dir, semantic_projection_mode="llm_first", prewarm_funasr=False)
        registry_factory = app.state.pi_coach_runtime._options.get("evidence_registry_factory")
        app.state.pi_coach_runtime = FakeSidecar(registry_factory)
        app.state.streaming_llm_client = object()  # Fail closed for any unexpected network fallback.
        app.state.fixture_runtime_dir = data_dir
        client = TestClient(app)  # No lifespan/ASR/model/scheduler workers.
        persistence = app.state.v2_persistence
        try:
            material_set = client.post("/v2/material-sets", json={}).json()["material_set"]
            set_id = material_set["id"]
            uploaded = client.post(f"/v2/material-sets/{set_id}/attachments", files={
                "file": ("公开合成通知需求.md", MATERIAL_V1.encode("utf-8"), "text/markdown")},
                data={"title": "公开合成通知需求", "purpose": "用于资料版本与阅读生命周期验收"})
            uploaded.raise_for_status()
            attachment_id = uploaded.json()["attachment"]["id"]
            runner = app.state.meeting_material_job_runner
            with CONVERSION_WORKER_LOCK:
                conversion = runner.queue.claim()
                if conversion is None or conversion["id"] != uploaded.json()["job"]["id"]:
                    raise AssertionError("Fixture did not claim its own formal local conversion")
                result = runner._convert(conversion)
                if not runner.queue.finish(conversion, result):
                    raise AssertionError("Fixture local conversion did not commit")
            base = f"/v2/material-sets/{set_id}/attachments/{attachment_id}"
            detail = client.get(base).json()
            if not detail.get("version"):
                raise AssertionError("Fixture official converter did not produce v1")
            used = client.put(f"{base}/usage", json={"version_id": detail["version"]["id"], "enabled": True,
                "selected_span_ids": None, "expected_revision": detail["attachment"]["revision"]})
            used.raise_for_status()
            payload = {"meeting_id": meeting_id, "title": "公开合成：资料版本与阅读验收（无模型、无音频）",
                       "material_set_id": set_id, "expected_material_set_revision": used.json()["material_set"]["revision"]}
            created = client.post("/v2/meetings", json=payload, headers={"Idempotency-Key": meeting_id})
            created.raise_for_status()
            prep = client.put(f"/v2/meetings/{meeting_id}/preparation", json={
                "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"})
            prep.raise_for_status()
            repository = app.state.asr_live_repository
            create_synthetic_live_record(repository, meeting_id)
            seed_store = open_synthetic_store(data_dir / "meeting_copilot.db")
            try:
                seed_synthetic_final(seed_store, repository, meeting_id,
                                     {"segment_id": "public-material-fixture-source", "text": SOURCE_TEXT}, 1)
            finally:
                seed_store.close()
            controller = FixtureController(app, client, meeting_id, set_id, attachment_id, evaluations)
            queued = client.post(f"/v2/meetings/{meeting_id}/coach/request", json={"intent": "ask_question"},
                                 headers={"Idempotency-Key": f"{meeting_id}:initial"})
            queued.raise_for_status()
            if controller.process_once() is None:
                raise AssertionError("Fixture initial explicit publication not processed")
            yield app, controller
        finally:
            client.close()
            persistence.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/material-lifecycle-fixture-plan.json")
    args = parser.parse_args()
    report_path = (ROOT / args.report).resolve()
    if not report_path.is_relative_to(ROOT / "artifacts/realtime-participation-20261007"):
        raise ValueError("Fixture reports must stay within its isolated artifact directory")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.serve:
        report_path.write_text(json.dumps(fixture_plan(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(fixture_plan(), ensure_ascii=False), flush=True)
        return 0
    import uvicorn

    runtime = ROOT / "artifacts/realtime-participation-20261007" / f"material-lifecycle-fixture-runtime-{uuid.uuid4().hex[:8]}"
    with build_fixture(runtime) as (app, controller):
        stopped = threading.Event()

        def pump():
            while not stopped.wait(.15):
                try:
                    if controller.process_once():
                        report_path.write_text(json.dumps(controller.report(), ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as exc:
                    controller.error = type(exc).__name__  # No raw data/provider errors.
                    report_path.write_text(json.dumps(controller.report(), ensure_ascii=False, indent=2), encoding="utf-8")
                    stopped.set()

        report_path.write_text(json.dumps(controller.report(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(controller.report(), ensure_ascii=False), flush=True)
        worker = threading.Thread(target=pump, name="public-material-fake-only", daemon=True)
        worker.start()
        try:
            uvicorn.run(app, host="127.0.0.1", port=8770, lifespan="off", access_log=False)
        finally:
            stopped.set()
            worker.join(timeout=5)
            report_path.write_text(json.dumps(controller.report(), ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
