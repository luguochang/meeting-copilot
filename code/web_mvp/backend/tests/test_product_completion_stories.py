"""Four continuous user workflows through the full local application API.

No model responses, microphones, user database, or browser output are simulated.
These checks verify persistence and explicit confirmation, not model quality or UI.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.app import create_app


@pytest.fixture
def story(tmp_path):
    app = create_app(data_dir=tmp_path / "isolated-workspace", prewarm_funasr=False)
    with TestClient(app) as client:
        def call(method, path, *, status=200, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == status, response.text
            return response.json()
        yield call


def project(call, name="客户A"):
    return call("POST", "/v2/knowledge/projects", status=201, json={"name": name})["project"]


def meeting(call, identity, project_id=None, folder_id=None):
    call("POST", "/v2/meetings", status=201, json={"meeting_id": identity, "title": identity})
    if project_id:
        call("PUT", f"/v2/knowledge/meetings/{identity}/context",
             json={"project_id": project_id, "folder_id": folder_id})


def material(call, project_id, text, folder_id=None):
    return call("POST", "/v2/knowledge/documents/manual", status=201,
                json={"project_id": project_id, "folder_id": folder_id, "title": "核对素材", "content": text})


def confirm(call, draft):
    return call("POST", f"/v2/knowledge/documents/{draft['document']['id']}/confirm",
                json={"version_id": draft["version"]["id"], "expected_revision": draft["document"]["revision"]})["document"]


def search(call, identity, query):
    return call("POST", "/v2/knowledge/search", json={"meeting_id": identity, "query": query})["items"]


def test_prepare_scope_draft_confirmation_and_disabled_material(story):
    call = story
    a, b = project(call), project(call, "客户B")
    folders = [call("POST", "/v2/knowledge/folders", status=201,
                    json={"project_id": a["id"], "name": name})["folder"] for name in ("发布", "采购")]
    first = material(call, a["id"], "预算120元，压测通过后发布。", folders[0]["id"])
    confirm(call, material(call, a["id"], "预算480元。", folders[1]["id"]))
    confirm(call, material(call, b["id"], "预算900元。"))
    meeting(call, "scope", a["id"], folders[0]["id"])
    assert search(call, "scope", "预算") == []
    document = confirm(call, first)
    results = search(call, "scope", "预算")
    assert results and {item["document_id"] for item in results} == {document["id"]}
    assert all("480" not in item["quote"] and "900" not in item["quote"] for item in results)
    disabled = call("PATCH", f"/v2/knowledge/documents/{document['id']}",
                    json={"status": "disabled", "expected_revision": document["revision"]})["document"]
    assert search(call, "scope", "预算") == []
    assert disabled["status"] == "disabled"


def test_note_and_human_final_are_drafts_then_source_changes_require_review(story):
    call = story
    a = project(call)
    meeting(call, "review", a["id"])
    note = call("POST", "/v2/meetings/review/notes", status=201,
                json={"title": "会议摘记", "body": "压测结果待确认。", "source_kind": "manual"})["note"]
    imported = call("POST", f"/v2/knowledge/notes/{note['note_id']}/import", status=201,
                    json={"project_id": a["id"], "expected_source_version": note["version"]})
    assert imported["document"]["active_version_id"] is None
    document = confirm(call, imported)
    call("PATCH", f"/v2/notes/{note['note_id']}",
         json={"expected_version": note["version"], "body": "压测未通过，继续核对。"})
    assert call("GET", f"/v2/knowledge/documents/{document['id']}")["document"]["source_status"] == "needs_review"
    assert search(call, "review", "压测") == []
    final = call("PATCH", "/v2/meetings/review/documents/minutes",
                 json={"expected_revision": 0, "content_json": {"markdown": "人工核对：压测未通过。"}})["document"]
    final_draft = call("POST", "/v2/knowledge/meetings/review/import-final", status=201,
                       json={"expected_source_version": final["user_final"]["version"]})
    assert final_draft["document"]["active_version_id"] is None
    final_document = confirm(call, final_draft)
    assert search(call, "review", "压测")
    changed = call("PATCH", "/v2/meetings/review/documents/minutes",
                   json={"expected_revision": final["revision"], "content_json": {"markdown": "人工核对：压测复测通过。"}})["document"]
    assert "复测通过" in changed["user_final"]["content"]["markdown"]
    assert call("GET", f"/v2/knowledge/documents/{final_document['id']}")["document"]["source_status"] == "needs_review"
    assert search(call, "review", "压测") == []


def test_project_candidate_requires_confirmation_and_cancelled_task_does_not_return(story):
    call = story
    a = project(call)
    meeting(call, "first", a["id"])
    confirm(call, material(call, a["id"], "验收负责人仍未指定，需要下次明确。"))
    source = search(call, "first", "验收负责人")[0]
    state = call("GET", f"/v2/knowledge/projects/{a['id']}/state")
    proposed = call("POST", f"/v2/knowledge/projects/{a['id']}/proposals", status=201,
                    json={"meeting_id": "first", "expected_project_revision": state["project_revision"],
                          "changes": [{"type": "task", "text": "明确验收负责人", "source_refs": [source]}]})["proposal"]
    assert proposed["status"] == "pending"
    assert call("GET", f"/v2/knowledge/projects/{a['id']}/wiki")["items"] == []
    result = call("POST", f"/v2/knowledge/proposals/{proposed['id']}/confirm",
                  json={"expected_revision": proposed["revision"], "expected_project_revision": state["project_revision"]})
    task = result["items"][0]
    meeting(call, "second", a["id"])
    inherited = call("GET", "/v2/knowledge/meetings/second/project-state")
    assert [item["id"] for item in inherited["unfinished_tasks"]] == [task["id"]]
    assert task["owner"] is None and task["due"] is None
    current = call("GET", f"/v2/knowledge/projects/{a['id']}/state")
    cancelled = call("PATCH", f"/v2/knowledge/state/{task['id']}",
                     json={"status": "cancelled", "expected_revision": task["revision"],
                           "expected_project_revision": current["project_revision"]})
    meeting(call, "third", a["id"])
    assert call("GET", "/v2/knowledge/meetings/third/project-state")["unfinished_tasks"] == []
    call("PATCH", f"/v2/knowledge/state/{task['id']}", status=409,
         json={"status": "active", "expected_revision": cancelled["item"]["revision"],
               "expected_project_revision": cancelled["project_revision"]})


def test_memory_explicit_confirmation_scene_isolation_and_immediate_revocation(story):
    call = story
    a, b = project(call), project(call, "客户B")
    meeting(call, "memory-first", a["id"])
    meeting(call, "memory-next", a["id"])
    meeting(call, "outside", b["id"])
    candidate = call("POST", "/v2/knowledge/memories", status=201,
                     json={"scope": "project", "scope_id": a["id"], "kind": "term",
                           "memory_key": "talktrace", "value": "Talktrace", "aliases": ["托克吹斯"]})
    assert call("GET", "/v2/knowledge/meetings/memory-next/memory-context")["terms"] == []
    confirmed = call("PATCH", f"/v2/knowledge/memories/{candidate['id']}",
                     json={"expected_revision": candidate["revision"], "status": "active"})
    assert call("GET", "/v2/knowledge/meetings/memory-next/memory-context")["terms"] == [
        {"canonical": "Talktrace", "aliases": ["托克吹斯"]}]
    assert call("GET", "/v2/knowledge/meetings/outside/memory-context")["terms"] == []
    preference = call("POST", "/v2/knowledge/memories", status=201,
                      json={"scope": "project", "scope_id": a["id"], "scene": "interview", "kind": "preference",
                            "memory_key": "response_length", "value": "brief"})
    call("PATCH", f"/v2/knowledge/memories/{preference['id']}",
         json={"expected_revision": preference["revision"], "status": "active"})
    assert call("GET", "/v2/knowledge/meetings/memory-next/memory-context", params={"scene": "interview"})["preferences"] == {"response_length": "brief"}
    assert call("GET", "/v2/knowledge/meetings/memory-next/memory-context", params={"scene": "general"})["preferences"] == {}
    call("PATCH", f"/v2/knowledge/memories/{confirmed['id']}",
         json={"expected_revision": confirmed["revision"], "status": "disabled"})
    assert call("GET", "/v2/knowledge/meetings/memory-next/memory-context")["terms"] == []
