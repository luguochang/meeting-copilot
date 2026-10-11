from __future__ import annotations

import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes
from meeting_copilot_web_mvp.project_reconcile_api import mount_project_reconcile_api
from meeting_copilot_web_mvp.project_reconcile_jobs import ProjectReconcileRegistry
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def api(tmp_path):
    persistence = V2Persistence(tmp_path / "api.db")
    with persistence._write_transaction():
        persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','ended',100,100)")
    persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0, content={"markdown": "预算改为八万元。"}, now_ms=200)
    library = KnowledgeRepository(persistence.database_path)
    project = library.create_project("项目")
    library.bind_meeting_context("meeting", project["id"])
    outcomes = ProjectOutcomes(library)
    app = FastAPI()
    mount_project_reconcile_api(app, library, outcomes)
    try:
        with TestClient(app) as client:
            yield client, app, library, outcomes, project
        assert app.state.project_reconcile_runner._task is None
        assert app.state.project_reconcile_pi.runtime._process is None
    finally:
        library.close()
        persistence.close()


def test_explicit_api_job_background_result_is_only_pending_and_private_payload_stays_hidden(api):
    client, app, library, outcomes, project = api
    calls = []
    async def admission(callback, payload, job):
        calls.append(job["id"])
        app.state.project_reconcile_queue.mark_started(job)
        ref = json.loads(job["source_refs_json"])[0]
        terminal = {"base_project_revision": job["base_project_revision"], "changes": [{"action": "create", "type": "decision", "text": "预算八万元。", "status": "active", "source_refs": [ref]}]}
        return {"host_project_changes": ProjectReconcileRegistry(app.state.project_reconcile_queue, job).validate_project_changes(terminal)}
    app.state.run_project_reconcile_with_admission = admission
    response = client.post("/v2/knowledge/meetings/meeting/project-reconcile", json={"expected_project_revision": library.get_project(project["id"])["revision"], "expected_source_version": 1})
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert job["source_version"] == 1 and job["context_truncated"] is False
    assert "source_payload_json" not in response.text and "lease_owner" not in response.text
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = client.get(f"/v2/knowledge/project-reconcile/jobs/{job['id']}").json()["job"]
        if current["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert current["status"] == "succeeded"
    assert len(calls) == 1 and outcomes.get_proposal(current["result_proposal_id"])["status"] == "pending"
    assert outcomes.list(project["id"])["items"] == []
    page = client.get("/v2/knowledge/project-reconcile/jobs", params={"project_id": project["id"]}).json()
    assert len(page["items"]) == 1 and page["items"][0]["id"] == job["id"]


def test_no_admission_defers_without_model_call_and_cancel_retry_is_explicit(api):
    client, app, library, _, project = api
    body = {"expected_project_revision": library.get_project(project["id"])["revision"], "expected_source_version": 1}
    original = client.post("/v2/knowledge/meetings/meeting/project-reconcile", json=body).json()["job"]
    cancelled = client.post(f"/v2/knowledge/project-reconcile/jobs/{original['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["job"]["status"] == "cancelled"
    assert cancelled.json()["job"]["attempts"] == 0
    retried = client.post(f"/v2/knowledge/project-reconcile/jobs/{original['id']}/retry", json=body)
    assert retried.status_code == 200 and retried.json()["job"]["id"] == original["id"]
    assert app.state.project_reconcile_pi.runtime._process is None


def test_reconcile_routes_require_local_storage_and_valid_user_source():
    app = FastAPI()
    mount_project_reconcile_api(app, None, None)
    with TestClient(app) as client:
        assert client.post("/v2/knowledge/meetings/x/project-reconcile", json={"expected_project_revision": 1, "expected_source_version": 1}).status_code == 503
        assert client.get("/v2/knowledge/project-reconcile/jobs", params={"project_id": "project"}).status_code == 503
