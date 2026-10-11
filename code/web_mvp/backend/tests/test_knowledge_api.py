from __future__ import annotations

from io import BytesIO
import sqlite3
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from zipfile import ZipFile

from meeting_copilot_web_mvp.knowledge_api import mount_knowledge_api
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository


@pytest.fixture
def api(tmp_path):
    repository = KnowledgeRepository(tmp_path / "library.db")
    app = FastAPI()
    mount_knowledge_api(app, repository, tmp_path / "private")
    with TestClient(app) as client:
        yield client, repository, app
    assert app.state.knowledge_job_runner._task is None
    repository.close()


def _create(client):
    response = client.post("/v2/knowledge/projects", json={"name": "客户 A"})
    assert response.status_code == 201, response.text
    return response.json()["project"]


def _poll(client, job_id):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        job = client.get(f"/v2/knowledge/jobs/{job_id}").json()["job"]
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    pytest.fail("knowledge job did not complete")


def _manual(client, project, content="预算3万", **extra):
    response = client.post("/v2/knowledge/documents/manual", json={"project_id": project["id"], "title": "会议材料", "content": content, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_api_upload_preview_correct_confirm_move_disable_delete_and_sources(api):
    client, repository, _ = api
    project = _create(client)
    folder = client.post("/v2/knowledge/folders", json={"project_id": project["id"], "name": "合同"}).json()["folder"]
    response = client.post("/v2/knowledge/documents", data={"project_id": project["id"], "folder_id": folder["id"]},
                           files={"file": ("中文材料.txt", "预算30万元".encode(), "text/plain")})
    assert response.status_code == 202, response.text
    assert "original_relative_path" not in response.text and "lease_token" not in response.text
    assert _poll(client, response.json()["job"]["id"])["status"] == "succeeded"
    document_id = response.json()["document"]["id"]
    preview = client.get(f"/v2/knowledge/documents/{document_id}").json()
    assert preview["document"]["status"] == "draft" and preview["version"]["content"] == "预算30万元"
    assert preview["latest_draft_version_id"] == preview["version"]["id"]
    assert client.post("/v2/knowledge/search", json={"query": "预算", "project_id": project["id"]}).json()["items"] == []
    edited = client.post(f"/v2/knowledge/documents/{document_id}/versions", json={"content": "预算3万元", "expected_revision": preview["document"]["revision"]}).json()
    confirm = client.post(f"/v2/knowledge/documents/{document_id}/confirm", json={"version_id": edited["version"]["id"], "expected_revision": edited["document"]["revision"]})
    assert confirm.status_code == 200, confirm.text
    document = confirm.json()["document"]
    stale = client.post(f"/v2/knowledge/documents/{document_id}/confirm", json={"version_id": edited["version"]["id"], "expected_revision": edited["document"]["revision"]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "revision_conflict"
    evidence = client.post("/v2/knowledge/search", json={"query": "预算", "project_id": project["id"]}).json()["items"][0]
    assert "预算3万元" == evidence["quote"] and evidence["version"] == 2
    source = client.get(f"/v2/knowledge/sources/{evidence['source_id']}", params={"project_id": project["id"]})
    assert source.status_code == 200 and source.json()["source"]["locator"]["type"] == "converted_text"
    moved = client.patch(f"/v2/knowledge/documents/{document_id}", json={"folder_id": None, "title": "更名", "expected_revision": document["revision"]}).json()["document"]
    disabled = client.patch(f"/v2/knowledge/documents/{document_id}", json={"status": "disabled", "expected_revision": moved["revision"]}).json()["document"]
    assert client.post("/v2/knowledge/search", json={"query": "预算", "project_id": project["id"]}).json()["items"] == []
    assert client.get(f"/v2/knowledge/sources/{evidence['source_id']}", params={"project_id": project["id"]}).status_code == 409
    original = repository.get_document(document_id)["original_relative_path"]
    deleted = client.patch(f"/v2/knowledge/documents/{document_id}", json={"status": "deleted", "expected_revision": disabled["revision"]}).json()
    assert _poll(client, deleted["job"]["id"])["status"] == "succeeded"
    repeated = client.patch(f"/v2/knowledge/documents/{document_id}", json={"status": "deleted", "expected_revision": disabled["revision"]})
    assert repeated.status_code == 200 and repeated.json()["job"]["id"] == deleted["job"]["id"]
    assert repository.get_document(document_id)["original_relative_path"] is None
    assert not (repository.database_path.parent / "private" / original).exists()


def test_meeting_context_scope_revision_and_explicit_empty_selection(api):
    client, repository, _ = api
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','live',1,1)")
    project = _create(client)
    draft = _manual(client, project)
    document = client.post(f"/v2/knowledge/documents/{draft['document']['id']}/confirm",
                           json={"version_id": draft["version"]["id"], "expected_revision": draft["document"]["revision"]}).json()["document"]
    context = client.put("/v2/knowledge/meetings/meeting/context", json={"project_id": project["id"], "document_ids": [document["id"]]}).json()["context"]
    assert context["selected_document_ids"] == [document["id"]]
    assert client.post("/v2/knowledge/search", json={"meeting_id": "meeting", "query": "预算"}).json()["items"]
    assert client.post("/v2/knowledge/search", json={"meeting_id": "meeting", "project_id": project["id"], "query": "预算"}).status_code == 400
    assert client.put("/v2/knowledge/meetings/meeting/context", json={"project_id": project["id"], "document_ids": [], "expected_revision": context["context_revision"]}).status_code == 200
    assert client.post("/v2/knowledge/search", json={"meeting_id": "meeting", "query": "预算"}).json()["items"] == []
    other = _create(client)
    assert client.put("/v2/knowledge/meetings/meeting/context", json={"project_id": other["id"], "document_ids": [document["id"]]}).status_code == 409


def test_api_301_documents_paginate_and_versions_metadata(api):
    client, _, _ = api
    project = _create(client)
    for index in range(301):
        _manual(client, project, title=f"资料{index}")
    ids, cursor = [], None
    while True:
        response = client.get("/v2/knowledge/documents", params={"project_id": project["id"], "limit": 100, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        ids.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 301
    history = client.get(f"/v2/knowledge/documents/{ids[0]}/versions").json()
    assert len(history["items"]) == 1 and "content" not in history["items"][0]
    assert client.get("/v2/knowledge/documents", params={"project_id": project["id"], "cursor": "nonsense"}).status_code == 400


def test_upload_unsafe_type_size_missing_and_failed_parse_visible(api):
    client, _, _ = api
    project = _create(client)
    response = client.post("/v2/knowledge/documents", data={"project_id": project["id"]}, files={"file": ("../bad.txt", b"abc", "text/plain")})
    assert response.status_code == 400
    response = client.post("/v2/knowledge/documents", content=b"", headers={"content-length": str(22 * 1024 * 1024)})
    assert response.status_code == 413 and response.json()["detail"]["code"] == "upload_too_large"
    response = client.post("/v2/knowledge/documents", data={"project_id": project["id"]}, files={"file": ("bad.docx", b"broken", "application/octet-stream")})
    job = _poll(client, response.json()["job"]["id"])
    assert job["status"] == "failed" and job["error_code"] == "invalid_docx_document" and "Word" in job["error_message"]
    assert client.get("/v2/knowledge/documents/missing").status_code == 404
    assert client.post(f"/v2/knowledge/jobs/{job['id']}/retry").status_code == 200
    assert client.post(f"/v2/knowledge/jobs/{job['id']}/cancel").status_code == 200


def test_real_txt_md_docx_pdf_import_endpoints(api):
    client, _, _ = api
    project = _create(client)
    word = BytesIO()
    with ZipFile(word, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>材料正文</w:t></w:r></w:p></w:body></w:document>')
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Knowledge source.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    pdf = BytesIO()
    writer.write(pdf)
    for filename, data in (("文本.txt", b"hello"), ("笔记.md", b"# note"), ("材料.docx", word.getvalue()), ("材料.pdf", pdf.getvalue())):
        response = client.post("/v2/knowledge/documents", data={"project_id": project["id"]}, files={"file": (filename, data, "application/octet-stream")})
        assert response.status_code == 202, response.text
        job = _poll(client, response.json()["job"]["id"])
        assert job["status"] == "succeeded", job
        assert client.get(f"/v2/knowledge/documents/{job['document_id']}").json()["version"]["content"]


def test_memory_only_runtime_reports_unavailable():
    app = FastAPI()
    mount_knowledge_api(app, None, None)
    with TestClient(app) as client:
        response = client.get("/v2/knowledge/projects")
        assert response.status_code == 503 and response.json()["detail"]["code"] == "knowledge_storage_unavailable"


def test_production_create_app_mount_lifecycle_and_restart_preserve_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_PI_BRIDGE_PREWARM", "0")
    from meeting_copilot_web_mvp import app as app_module

    monkeypatch.setattr(app_module.asr_refiner, "refinement_capability", lambda: {"status": "unavailable"})
    directory = tmp_path / "runtime"
    first = app_module.create_app(data_dir=directory)
    with TestClient(first) as client:
        project = _create(client)
        response = client.post("/v2/knowledge/documents", data={"project_id": project["id"]},
                               files={"file": ("project.md", b"# retained", "text/markdown")})
        assert response.status_code == 202, response.text
        job = _poll(client, response.json()["job"]["id"])
        assert job["status"] == "succeeded"
        document_id = job["document_id"]
    assert first.state.knowledge_job_runner._task is None
    second = app_module.create_app(data_dir=directory)
    with TestClient(second) as client:
        preview = client.get(f"/v2/knowledge/documents/{document_id}")
        assert preview.status_code == 200 and preview.json()["version"]["content"] == "# retained"
        assert preview.json()["document"]["status"] == "draft"
    assert second.state.knowledge_job_runner._task is None
