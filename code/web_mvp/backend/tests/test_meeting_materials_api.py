from __future__ import annotations

from io import BytesIO
import time
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.meeting_materials_api import mount_meeting_materials_api


@pytest.fixture
def api(tmp_path):
    library = KnowledgeRepository(tmp_path / "app.sqlite3")
    app = FastAPI()
    mount_meeting_materials_api(app, library, tmp_path / "private")
    with TestClient(app) as client:
        yield client, app, library
    assert app.state.meeting_material_job_runner._task is None
    library.close()


def poll(client, set_id, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/v2/material-sets/{set_id}/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    pytest.fail("material worker did not finish")


def test_no_project_upload_preview_edit_one_click_use_bind_sources_disable_delete(api):
    client, app, library = api
    material_set = client.post("/v2/material-sets").json()["material_set"]
    set_id = material_set["id"]
    response = client.post(f"/v2/material-sets/{set_id}/attachments", files={"file": ("需求.md", "# 范围\n预算 30 万元。".encode(), "text/markdown")})
    assert response.status_code == 202, response.text
    assert "original_relative_path" not in response.text and "lease_token" not in response.text
    upload = response.json()
    assert upload["material_set"]["revision"] > material_set["revision"]
    assert poll(client, set_id, upload["job"]["id"])["status"] == "succeeded"
    attachment_id = upload["attachment"]["id"]
    base = f"/v2/material-sets/{set_id}/attachments/{attachment_id}"
    preview = client.get(base).json()
    version_id = preview["version"]["id"]
    assert "content" not in preview["version"] and preview["version"]["structure"]["sections"][0]["title"] == "范围"
    spans = client.get(f"{base}/versions/{version_id}/spans").json()
    assert spans["items"][0]["text"] == "# 范围\n预算 30 万元。"
    edit = client.post(f"{base}/versions", json={"content": "# 范围\n预算 3 万元。", "base_version_id": version_id,
                                               "expected_revision": preview["attachment"]["revision"]})
    assert edit.status_code == 201, edit.text
    edited = edit.json()
    assert edited["attachment"]["enabled"] is False
    used = client.put(f"{base}/usage", json={"enabled": True, "version_id": edited["version"]["id"], "expected_revision": edited["attachment"]["revision"]})
    assert used.status_code == 200, used.text
    assert client.put(f"{base}/usage", json={"enabled": True, "version_id": edited["version"]["id"], "expected_revision": edited["attachment"]["revision"]}).status_code == 409
    assert library._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
    with library._write():
        library._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','live',1,1)")
    assert client.get("/v2/meetings/meeting/material-set").json() == {"material_set": None}
    bound = client.post("/v2/meetings/meeting/material-set", json={"material_set_id": set_id, "expected_revision": used.json()["material_set"]["revision"]})
    assert bound.status_code == 200, bound.text
    assert client.post("/v2/meetings/meeting/material-set", json={"material_set_id": set_id, "expected_revision": 1}).status_code == 200
    evidence = app.state.meeting_materials_repository.search_for_meeting("meeting", "预算")[0]
    source_url = f"/v2/meetings/meeting/sources/{evidence['source_id']}"
    source = client.get(source_url).json()["source"]
    assert source["quote"] == "# 范围\n预算 3 万元。" and source["ordinal"] == 0
    attachment = used.json()["attachment"]
    disabled = client.put(f"{base}/usage", json={"enabled": False, "expected_revision": attachment["revision"]}).json()["attachment"]
    assert client.get(source_url).status_code == 409
    assert client.get(source_url, params={"allow_historical": "true"}).json()["source"]["source_state"] == "stale"
    deleted = client.delete(base, params={"expected_revision": disabled["revision"]})
    assert deleted.status_code == 202, deleted.text
    assert poll(client, set_id, deleted.json()["job"]["id"])["status"] == "succeeded"
    assert client.get(source_url, params={"allow_historical": "true"}).json()["source"]["quote"] == ""


def test_upload_formats_partial_pdf_and_docx_structure_are_honest(api):
    client, _, _ = api
    set_id = client.post("/v2/material-sets").json()["material_set"]["id"]
    word = BytesIO()
    with ZipFile(word, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                         '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>接口</w:t></w:r></w:p>'
                         '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>预算 30000</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        archive.writestr("word/media/image.png", b"not used")
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Budget 30000.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_blank_page(width=612, height=792)
    pdf = BytesIO()
    writer.write(pdf)
    for filename, data in (("说明.txt", b"hello"), ("范围.md", b"# Scope\nhello"), ("接口.docx", word.getvalue()), ("部分.pdf", pdf.getvalue())):
        response = client.post(f"/v2/material-sets/{set_id}/attachments", files={"file": (filename, data, "application/octet-stream")})
        assert response.status_code == 202, response.text
        assert poll(client, set_id, response.json()["job"]["id"])["status"] == "succeeded"
        base = f"/v2/material-sets/{set_id}/attachments/{response.json()['attachment']['id']}"
        preview = client.get(base).json()
        version = preview["version"]
        assert version["content_characters"] > 0
        if filename.endswith(".docx"):
            assert version["structure"]["sections"][0]["title"] == "接口"
            assert "docx_images_headers_footers_not_extracted" in version["warnings"]
            spans = client.get(f"{base}/versions/{version['id']}/spans").json()["items"]
            assert spans[-1]["locator"]["type"] == "docx_table_cell"
        if filename.endswith(".pdf"):
            assert preview["attachment"]["import_status"] == "needs_ocr"
            assert version["structure"]["missing_text_pages"] == [2]
            assert version["warnings"] == ["pdf_partial_ocr_required"]


def test_cross_owner_get_version_usage_jobs_and_binding_are_rejected(api):
    client, app, library = api
    left = client.post("/v2/material-sets").json()["material_set"]
    right = client.post("/v2/material-sets").json()["material_set"]
    uploaded = client.post(f"/v2/material-sets/{left['id']}/attachments", files={"file": ("test.txt", b"budget 30000", "text/plain")}).json()
    poll(client, left["id"], uploaded["job"]["id"])
    wrong_base = f"/v2/material-sets/{right['id']}/attachments/{uploaded['attachment']['id']}"
    assert client.get(wrong_base).status_code == 404
    assert client.get(f"/v2/material-sets/{right['id']}/jobs/{uploaded['job']['id']}").status_code == 404
    correct = client.get(f"/v2/material-sets/{left['id']}/attachments/{uploaded['attachment']['id']}").json()
    assert client.put(f"{wrong_base}/usage", json={"enabled": True, "version_id": correct["version"]["id"],
                                               "expected_revision": correct["attachment"]["revision"]}).status_code == 404
    with library._write():
        for meeting_id in ("first", "second"):
            library._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES(?,'live',1,1)", (meeting_id,))
    assert client.post("/v2/meetings/first/material-set", json={"material_set_id": left["id"]}).status_code == 200
    assert client.post("/v2/meetings/second/material-set", json={"material_set_id": left["id"]}).status_code == 409
    assert client.delete(f"/v2/material-sets/{left['id']}", params={"expected_revision": 1}).status_code == 409
    assert app.state.meeting_materials_repository.list_sets()["items"][0]["id"] == right["id"]


def test_get_never_starts_parse_and_explicit_draft_delete_cancels_pending(tmp_path):
    library = KnowledgeRepository(tmp_path / "app.sqlite3")
    app = FastAPI()
    mount_meeting_materials_api(app, library, tmp_path / "private")
    client = TestClient(app)  # No lifespan: GET must not kick off an ad hoc worker.
    try:
        set_id = client.post("/v2/material-sets").json()["material_set"]["id"]
        uploaded = client.post(f"/v2/material-sets/{set_id}/attachments", files={"file": ("test.txt", b"budget 30000", "text/plain")}).json()
        for _ in range(3):
            assert client.get(f"/v2/material-sets/{set_id}").status_code == 200
            assert client.get(f"/v2/material-sets/{set_id}/jobs/{uploaded['job']['id']}").json()["job"]["status"] == "queued"
        assert app.state.meeting_material_job_runner._task is None
        material_set = client.get(f"/v2/material-sets/{set_id}").json()["material_set"]
        response = client.delete(f"/v2/material-sets/{set_id}", params={"expected_revision": material_set["revision"]})
        assert response.status_code == 202 and response.json()["material_set"]["state"] == "deleted"
        assert client.get(f"/v2/material-sets/{set_id}/jobs/{uploaded['job']['id']}").status_code == 409
        assert client.get("/v2/material-sets").json()["items"] == []
        assert app.state.meeting_material_jobs.get(uploaded["job"]["id"])["status"] == "cancelled"
    finally:
        client.close()
        library.close()


def test_unsafe_size_failure_and_retry_cancel_feedback(api):
    client, _, _ = api
    set_id = client.post("/v2/material-sets").json()["material_set"]["id"]
    url = f"/v2/material-sets/{set_id}/attachments"
    assert client.post(url, files={"file": ("../unsafe.txt", b"hello", "text/plain")}).status_code == 400
    assert client.post(url, content=b"", headers={"content-length": str(22 * 1024 * 1024)}).status_code == 413
    failed = client.post(url, files={"file": ("broken.docx", b"invalid", "application/octet-stream")}).json()
    job = poll(client, set_id, failed["job"]["id"])
    assert job["error_code"] == "invalid_docx_document" and "Word" in job["error_message"]
    assert client.post(f"/v2/material-sets/{set_id}/jobs/{job['id']}/retry").status_code == 200
    assert client.post(f"/v2/material-sets/{set_id}/jobs/{job['id']}/cancel").status_code == 200


def test_unavailable_storage_is_an_explicit_503():
    app = FastAPI()
    mount_meeting_materials_api(app, None, None)
    with TestClient(app) as client:
        response = client.get("/v2/material-sets")
        assert response.status_code == 503 and response.json()["detail"]["code"] == "materials_storage_unavailable"


def test_streamed_multipart_without_content_length_is_bounded(api, monkeypatch):
    client, _, library = api
    monkeypatch.setattr("meeting_copilot_web_mvp.meeting_materials_api.MAX_UPLOAD_BYTES", 1024)
    set_id = client.post("/v2/material-sets").json()["material_set"]["id"]
    boundary = "material-test-boundary"
    first = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="data.txt"\r\n'
             'Content-Type: text/plain\r\n\r\n').encode()
    content = iter([first, b"x" * (1024 * 1024 + 2048), f"\r\n--{boundary}--\r\n".encode()])
    response = client.post(f"/v2/material-sets/{set_id}/attachments", content=content,
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert response.status_code == 413, response.text
    assert library._conn.execute("SELECT COUNT(*) FROM meeting_attachments").fetchone()[0] == 0
