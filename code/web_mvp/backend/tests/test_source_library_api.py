from __future__ import annotations

import base64
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.knowledge_api import mount_knowledge_api
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.source_library_api import mount_source_library_api
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def api(tmp_path):
    persistence = V2Persistence(tmp_path / "sources.db")
    persistence.commit_final_and_enqueue(meeting_id="meeting", final_id="final", segment_id="segment", text="待核对",
        normalized_text="待核对", source_track="microphone", started_at_ms=0, ended_at_ms=100,
        evidence_hash="hash", now_ms=100, enqueue_answer=False)
    library = KnowledgeRepository(persistence.database_path)
    project = library.create_project("客户")
    library.bind_meeting_context("meeting", project["id"])
    app = FastAPI()
    mount_knowledge_api(app, library, tmp_path / "private")
    mount_source_library_api(app, library, persistence)
    @app.get("/v2/notes/{note_id}")
    def note(note_id: str):
        return {"note": persistence.get_note(note_id)}
    try:
        with TestClient(app) as client:
            yield client, persistence, library, project
    finally:
        library.close()
        persistence.close()


def test_paged_notes_route_wins_over_dynamic_get_and_returns_stable_cursor(api):
    client, persistence, _, _ = api
    for index in range(3):
        persistence.create_note(note_id=f"note:{index}", meeting_id="meeting", title=f"访谈{index}", body="资料",
            source_kind="manual", source_message_id=None, evidence=[], now_ms=500)
    first = client.get("/v2/notes/page", params={"limit": 2, "query": "访谈"})
    assert first.status_code == 200, first.text
    assert [note["note_id"] for note in first.json()["items"]] == ["note:2", "note:1"]
    assert first.json()["has_more"] and isinstance(first.json()["next_cursor"], str)
    last = client.get("/v2/notes/page", params={"limit": 2, "cursor": first.json()["next_cursor"]}).json()
    assert [note["note_id"] for note in last["items"]] == ["note:0"] and last["has_more"] is False
    assert client.get("/v2/notes/note:0").status_code == 200
    cursor = base64.urlsafe_b64encode(json.dumps({"id": "x", "updated_at_ms": True}).encode()).decode()
    assert client.get("/v2/notes/page", params={"cursor": cursor}).status_code == 400


def test_final_import_error_codes_and_actual_content_version_confirmation(api):
    client, persistence, _, project = api
    response = client.post("/v2/knowledge/meetings/meeting/import-final", json={"expected_source_version": 1})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "human_final_required"
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0,
        content={"markdown": "法务确认后发布。"}, now_ms=500)
    persistence.update_meeting_title(meeting_id="meeting", title="合同需求评审", title_source="user", now_ms=510)
    imported = client.post("/v2/knowledge/meetings/meeting/import-final", json={"expected_source_version": final["user_final"]["version"]})
    assert imported.status_code == 201, imported.text
    result = imported.json()
    assert "original_relative_path" not in imported.text
    assert result["document"]["project_id"] == project["id"]
    assert result["document"]["title"] == "合同需求评审·会议终稿·会议纪要"
    assert result["version"]["content"] == "法务确认后发布。" and result["version"]["source_status"] == "current"
    assert result["document"]["active_version_id"] is None
    document_id = result["document"]["id"]
    confirmed = client.post(f"/v2/knowledge/documents/{document_id}/confirm", json={"version_id": result["version"]["id"], "expected_revision": result["document"]["revision"]})
    assert confirmed.status_code == 200, confirmed.text
    document = confirmed.json()["document"]
    persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=final["revision"],
        content={"markdown": "董事会确认后发布。"}, now_ms=1000)
    assert client.get(f"/v2/knowledge/documents/{document_id}").json()["document"]["source_status"] == "needs_review"
    assert client.get("/v2/knowledge/documents", params={"project_id": project["id"]}).json()["items"][0]["source_status"] == "needs_review"
    assert client.post(f"/v2/knowledge/documents/{document_id}/confirm", json={"version_id": result["version"]["id"], "expected_revision": document["revision"]}).status_code == 409


def test_note_import_new_source_requires_current_document_revision_and_explicit_base(api):
    client, persistence, _, project = api
    persistence.create_note(note_id="note", meeting_id="meeting", title="素材", body="预算五万元。",
        source_kind="manual", source_message_id=None, evidence=[], now_ms=500)
    old = client.post("/v2/knowledge/notes/note/import", json={"project_id": project["id"], "expected_source_version": 1}).json()
    assert old["document"]["status"] == "draft"
    persistence.update_note("note", expected_version=1, body="预算八万元。", now_ms=1000)
    body = {"project_id": project["id"], "expected_source_version": 2}
    assert client.post("/v2/knowledge/notes/note/import", json=body).status_code == 409
    new = client.post("/v2/knowledge/notes/note/import", json={**body, "expected_document_revision": old["document"]["revision"]}).json()
    edited = client.post(f"/v2/knowledge/documents/{old['document']['id']}/versions", json={"content": "预算八万元（已核对）。",
        "expected_revision": new["document"]["revision"], "base_version_id": new["version"]["id"]})
    assert edited.status_code == 201, edited.text
    result = edited.json()
    confirmed = client.post(f"/v2/knowledge/documents/{old['document']['id']}/confirm", json={"version_id": result["version"]["id"], "expected_revision": result["document"]["revision"]})
    assert confirmed.status_code == 200, confirmed.text


def test_structured_final_import_api_returns_readable_candidate_without_confirming_library(api):
    client, persistence, _, _project = api
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="decisions", expected_revision=0,
        content={"decisions": [{"id": "decision-internal-id", "text": "同步通知仍是候选。", "status": "candidate",
                               "evidenceSegmentId": "segment-internal-id"}]}, now_ms=500)
    imported = client.post("/v2/knowledge/meetings/meeting/import-final", json={
        "document_kind": "decisions", "expected_source_version": final["user_final"]["version"]})
    assert imported.status_code == 201, imported.text
    result = imported.json()
    assert result["version"]["content"] == "## 决策记录\n\n- 同步通知仍是候选。\n  状态：待确认"
    assert result["document"]["status"] == "draft" and result["document"]["active_version_id"] is None
    assert result["version"]["confirmed_at_ms"] is None
    repeated = client.post("/v2/knowledge/meetings/meeting/import-final", json={"document_kind": "decisions", "expected_source_version": 1})
    assert repeated.status_code == 201 and repeated.json()["already_imported"] is True
    assert repeated.json()["version"]["id"] == result["version"]["id"]


def test_sources_api_without_sqlite_returns_clear_error():
    app = FastAPI()
    mount_source_library_api(app, None, None)
    with TestClient(app) as client:
        assert client.get("/v2/notes/page").status_code == 503
        assert client.post("/v2/knowledge/notes/x/import", json={"project_id": "p", "expected_source_version": 1}).status_code == 503
