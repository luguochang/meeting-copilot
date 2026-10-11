from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.knowledge_api import mount_knowledge_api
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.project_outcomes_api import mount_project_outcomes_api


def test_api_explicit_fact_candidate_confirm_reject_wiki_and_history(tmp_path):
    repository = KnowledgeRepository(tmp_path / "state.db")
    app = FastAPI()
    mount_knowledge_api(app, repository, tmp_path / "private")
    mount_project_outcomes_api(app, repository)
    try:
        with TestClient(app) as client:
            project = client.post("/v2/knowledge/projects", json={"name": "客户 A"}).json()["project"]
            created = client.post(f"/v2/knowledge/projects/{project['id']}/state", json={"type": "task", "text": "用户输入待办",
                                  "status": "unknown", "expected_project_revision": project["revision"]})
            assert created.status_code == 201, created.text
            item = created.json()["item"]
            cancelled = client.patch(f"/v2/knowledge/state/{item['id']}", json={"status": "cancelled", "expected_revision": item["revision"],
                                     "expected_project_revision": created.json()["project_revision"]})
            assert cancelled.status_code == 200, cancelled.text
            assert len(client.get(f"/v2/knowledge/state/{item['id']}/history").json()["items"]) == 2
            proposal = client.post(f"/v2/knowledge/projects/{project['id']}/proposals", json={"origin": "user", "expected_project_revision": cancelled.json()["project_revision"],
                                    "changes": [{"type": "decision", "text": "用户明确决定"}]})
            assert proposal.status_code == 201 and proposal.json()["proposal"]["status"] == "pending"
            assert "用户明确决定" not in client.get(f"/v2/knowledge/projects/{project['id']}/wiki").json()["markdown"]
            pending = proposal.json()["proposal"]
            confirm = client.post(f"/v2/knowledge/proposals/{pending['id']}/confirm", json={"expected_revision": pending["revision"], "expected_project_revision": pending["base_revision"]})
            assert confirm.status_code == 200, confirm.text
            wiki = client.get(f"/v2/knowledge/projects/{project['id']}/wiki").json()
            assert "用户明确决定" in wiki["markdown"] and "用户输入待办" not in wiki["markdown"]
            stale = client.post(f"/v2/knowledge/proposals/{pending['id']}/confirm", json={"expected_revision": 1, "expected_project_revision": pending["base_revision"]})
            assert stale.status_code == 409
            declined = client.post(f"/v2/knowledge/projects/{project['id']}/proposals", json={"origin": "user", "expected_project_revision": wiki["project_revision"],
                                    "changes": [{"type": "question", "text": "未确认问题"}]}).json()["proposal"]
            assert client.post(f"/v2/knowledge/proposals/{declined['id']}/reject", json={"expected_revision": 1}).json()["proposal"]["status"] == "rejected"
            page = client.get(f"/v2/knowledge/projects/{project['id']}/state", params={"limit": 1}).json()
            assert page["has_more"] and isinstance(page["next_cursor"], str)
            assert client.get(f"/v2/knowledge/projects/{project['id']}/state", params={"limit": 1, "cursor": page["next_cursor"]}).status_code == 200
    finally:
        repository.close()
