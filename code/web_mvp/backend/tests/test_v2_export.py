from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import re
from urllib.parse import unquote
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash


SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
MEETING_STARTED_AT_MS = int(
    datetime(2026, 7, 18, 9, 30, tzinfo=SHANGHAI_TIMEZONE).timestamp() * 1_000
)


def _final_event():
    return {
        "event_type": "final",
        "segment_id": "segment-1",
        "text": "先灰度百分之五，上线前确认回滚负责人。",
        "normalized_text": "先灰度 5%，上线前确认回滚负责人。",
        "start_ms": 1_000,
        "end_ms": 4_000,
    }


def _seed_export_meeting(app, meeting_id: str) -> None:
    persistence = app.state.v2_persistence
    persistence.create_meeting(
        meeting_id=meeting_id,
        title="支付服务发布评审",
        now_ms=MEETING_STARTED_AT_MS,
    )
    app.state.commit_v2_final(meeting_id, _final_event())
    persistence.end_meeting(meeting_id=meeting_id, now_ms=MEETING_STARTED_AT_MS + 4_500)
    minutes_job = next(
        job for job in persistence.list_jobs(meeting_id=meeting_id)
        if job["kind"] == "minutes"
    )
    persistence.save_minutes(
        meeting_id=meeting_id,
        job_id=minutes_job["id"],
        markdown="# 会议纪要\n\n## 已确认决策\n- 先灰度 5%",
        structured={
            "decisions": ["先灰度 5%"],
            "action_items": [{"item": "确认回滚负责人", "owner": "张三", "deadline": "上线前"}],
            "risks": [],
            "open_questions": [],
        },
        degraded=False,
        now_ms=MEETING_STARTED_AT_MS + 5_000,
    )


def _download_filename(response) -> str:
    disposition = response.headers["content-disposition"]
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    assert match is not None, disposition
    return unquote(match.group(1))


def _managed_file_hashes(root) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith("meeting_copilot.db")
    }


def _save_user_document(client, meeting_id: str, kind: str, content) -> dict:
    snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()
    response = client.patch(
        f"/v2/meetings/{meeting_id}/documents/{kind}",
        json={
            "expected_revision": snapshot["documents"][kind]["revision"],
            "content_json": content,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]


def test_v2_json_export_contains_only_portable_meeting_artifacts(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    _seed_export_meeting(app, "meeting-export-json")

    response = client.get("/v2/meetings/meeting-export-json/export?format=json")

    assert response.status_code == 200
    assert _download_filename(response) == "支付服务发布评审-2026-07-18.json"
    body = response.json()
    assert body["schema_version"] == "meeting_copilot.meeting_export.v1"
    assert body["meeting"]["title"] == "支付服务发布评审"
    assert body["transcript"][0]["normalized_text"] == "先灰度 5%，上线前确认回滚负责人。"
    assert body["minutes"]["structured"]["decisions"] == ["先灰度 5%"]
    assert "api_key" not in response.text.lower()
    persistence = app.state.v2_persistence
    persistence.close()


def test_v2_markdown_export_is_readable_and_downloadable(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    _seed_export_meeting(app, "meeting-export-md")

    response = client.get("/v2/meetings/meeting-export-md/export?format=markdown")

    assert response.status_code == 200
    assert _download_filename(response) == "支付服务发布评审-2026-07-18.md"
    assert "# 支付服务发布评审" in response.text
    assert "## 完整会议文字" in response.text
    assert "[00:01-00:04] 先灰度 5%，上线前确认回滚负责人。" in response.text
    app.state.v2_persistence.close()


def test_v2_docx_export_is_a_local_valid_ooxml_download(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    _seed_export_meeting(app, "meeting-export-docx")

    response = client.get("/v2/meetings/meeting-export-docx/export?format=docx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert _download_filename(response) == "支付服务发布评审-2026-07-18.docx"
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names
        document = archive.read("word/document.xml").decode("utf-8")
        assert "支付服务发布评审" in document
        assert "先灰度 5%" in document
    app.state.v2_persistence.close()


def test_v2_export_filename_is_sanitized_and_stable_without_path_disclosure(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    meeting_id = "meeting-export-safe-name"
    _seed_export_meeting(app, meeting_id)
    renamed = client.patch(
        f"/v2/meetings/{meeting_id}",
        json={"title": '.. 财务:Q3? "复盘" <最终>|..'},
    )
    assert renamed.status_code == 200
    assert renamed.json()["snapshot"]["title_source"] == "user"

    first = client.get(f"/v2/meetings/{meeting_id}/export?format=docx")
    second = client.get(f"/v2/meetings/{meeting_id}/export?format=docx")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["content-disposition"] == second.headers["content-disposition"]
    filename = _download_filename(first)
    assert filename == "财务-Q3-复盘-最终-2026-07-18.docx"
    assert ".." not in filename
    assert not any(character in filename for character in '/\\<>:"|?*')
    assert meeting_id not in first.headers["content-disposition"]
    assert str(tmp_path) not in first.headers["content-disposition"]
    app.state.v2_persistence.close()


def test_v2_export_uses_each_user_final_document_for_markdown_and_docx(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    meeting_id = "meeting-export-user-final"
    _seed_export_meeting(app, meeting_id)
    persistence = app.state.v2_persistence
    index_job = next(job for job in persistence.list_jobs(meeting_id=meeting_id) if job["kind"] == "index")
    persistence.rebuild_search_document(meeting_id=meeting_id, job_id=index_job["id"], now_ms=6_000)

    user_documents = {
        "minutes": {"markdown": "# 用户纪要\n\n只保留人工确认的版本。"},
        "decisions": {"decisions": [{"text": "人工确认的决策", "status": "confirmed"}]},
        "action_items": {"action_items": [{"text": "人工确认的待办", "owner": "李四", "deadline": "周五"}]},
        "risks": {"risks": [{"text": "人工确认的风险", "mitigation": "人工处理"}]},
        "transcript": {"segments": [{"segment_id": "segment-1", "text": "人工修订后的完整原话", "started_at_ms": 1_000}]},
    }
    snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()
    for kind, content in user_documents.items():
        revision = snapshot["documents"][kind]["revision"]
        saved = client.patch(
            f"/v2/meetings/{meeting_id}/documents/{kind}",
            json={"expected_revision": revision, "content_json": content},
        )
        assert saved.status_code == 200, (kind, saved.text)

    markdown = client.get(f"/v2/meetings/{meeting_id}/export?format=markdown")
    assert markdown.status_code == 200
    assert "用户纪要" in markdown.text
    assert "人工确认的决策" in markdown.text
    assert "人工确认的待办" in markdown.text
    assert "人工确认的风险" in markdown.text
    assert "人工修订后的完整原话" in markdown.text
    assert "先灰度 5%，上线前确认回滚负责人。" not in markdown.text

    docx = client.get(f"/v2/meetings/{meeting_id}/export?format=docx")
    assert docx.status_code == 200
    with ZipFile(BytesIO(docx.content)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert "人工确认的决策" in document
    assert "人工确认的待办" in document
    assert "人工确认的风险" in document
    assert "人工修订后的完整原话" in document
    assert "先灰度 5%，上线前确认回滚负责人。" not in document
    persistence.close()


def test_v2_json_export_prefers_user_final_and_retains_source_ai_and_audit_versions(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    meeting_id = "meeting-export-json-versions"
    persistence = app.state.v2_persistence
    persistence.create_meeting(
        meeting_id=meeting_id,
        title="中文技术会议",
        now_ms=MEETING_STARTED_AT_MS,
    )
    initial_text = "我们先布数服务。"
    initial_normalized = "我们先部署服务。"
    evidence_hash = transcript_evidence_hash("segment-versioned", initial_normalized)
    persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="final-versioned",
        segment_id="segment-versioned",
        text=initial_text,
        normalized_text=initial_normalized,
        started_at_ms=2_000,
        ended_at_ms=6_500,
        speaker_id="cluster-backend",
        speaker_confidence=0.94,
        evidence_hash=evidence_hash,
        now_ms=MEETING_STARTED_AT_MS + 6_500,
        enqueue_jobs=False,
    )
    persistence.rename_meeting_speaker(
        meeting_id=meeting_id,
        speaker_id="cluster-backend",
        speaker_label="后端同学",
        now_ms=MEETING_STARTED_AT_MS + 7_000,
    )
    corrected = persistence.commit_transcript_revision(
        meeting_id=meeting_id,
        segment_id="segment-versioned",
        expected_evidence_hash=evidence_hash,
        corrected_text="我们先部署服务，并确认回滚方案。",
        revision_id="revision-ai-1",
        now_ms=MEETING_STARTED_AT_MS + 8_000,
    )
    assert corrected is not None
    persistence.end_meeting(
        meeting_id=meeting_id,
        now_ms=MEETING_STARTED_AT_MS + 10_000,
    )
    persistence.save_ai_generated_document(
        meeting_id=meeting_id,
        document_kind="transcript",
        content={"segments": [corrected]},
        author="test:index",
        now_ms=MEETING_STARTED_AT_MS + 11_000,
    )
    _save_user_document(
        client,
        meeting_id,
        "transcript",
        {
            "segments": [
                {
                    "segment_id": "segment-versioned",
                    "text": "人工最终稿：先部署服务，并由后端同学确认回滚方案。",
                    "started_at_ms": 2_000,
                    "ended_at_ms": 6_500,
                    "speaker_id": "cluster-backend",
                    "speaker_label": "后端同学",
                }
            ]
        },
    )

    response = client.get(f"/v2/meetings/{meeting_id}/export?format=json")

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"][0]["text"].startswith("人工最终稿")
    source = body["transcript_versions"][0]
    assert source["raw_asr"]["text"] == initial_text
    assert source["ai_corrected"]["text"] == "我们先部署服务，并确认回滚方案。"
    assert source["user_final"]["text"].startswith("人工最终稿")
    assert source["effective"]["source"] == "user_final"
    assert source["speaker"] == {
        "id": "cluster-backend",
        "label": "后端同学",
        "confidence": 0.94,
    }
    assert source["timing"] == {"started_at_ms": 2_000, "ended_at_ms": 6_500}
    assert source["evidence"]["hash"] == corrected["evidence_hash"]
    assert body["documents"]["transcript"]["ai_generated"]["content"] is not None
    assert body["documents"]["transcript"]["user_final"]["content"] is not None
    transcript_audit = body["audit"]["review_document_revisions"]["transcript"]
    assert {entry["version_kind"] for entry in transcript_audit} == {
        "ai_generated",
        "user_final",
    }
    assert body["audit"]["meeting_revision"] == body["meeting"]["revision"]
    persistence.close()


def test_v2_docx_contains_local_review_metadata_sections_speaker_and_timing(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    meeting_id = "meeting-export-docx-complete"
    persistence = app.state.v2_persistence
    persistence.create_meeting(
        meeting_id=meeting_id,
        title="架构评审",
        now_ms=MEETING_STARTED_AT_MS,
    )
    persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id="final-docx",
        segment_id="segment-docx",
        text="原始识别",
        normalized_text="确认缓存失效策略。",
        started_at_ms=61_000,
        ended_at_ms=65_000,
        speaker_id="cluster-architect",
        speaker_confidence=0.91,
        evidence_hash=transcript_evidence_hash("segment-docx", "确认缓存失效策略。"),
        now_ms=MEETING_STARTED_AT_MS + 65_000,
        enqueue_jobs=False,
    )
    persistence.rename_meeting_speaker(
        meeting_id=meeting_id,
        speaker_id="cluster-architect",
        speaker_label="架构师",
        now_ms=MEETING_STARTED_AT_MS + 66_000,
    )
    persistence.end_meeting(
        meeting_id=meeting_id,
        now_ms=MEETING_STARTED_AT_MS + 125_000,
    )
    for kind, content in {
        "minutes": {"markdown": "# 复盘结论\n\n缓存策略需要压测。"},
        "decisions": {"decisions": [{"text": "采用双层缓存", "status": "confirmed"}]},
        "action_items": {"action_items": [{"text": "补充压测", "owner": "王工", "deadline": "周五"}]},
        "risks": {"risks": [{"text": "缓存击穿", "mitigation": "增加互斥锁"}]},
        "transcript": {
            "segments": [
                {
                    "segment_id": "segment-docx",
                    "text": "确认缓存失效策略。",
                    "started_at_ms": 61_000,
                    "ended_at_ms": 65_000,
                    "speaker_id": "cluster-architect",
                    "speaker_label": "架构师",
                }
            ]
        },
    }.items():
        persistence.save_user_final_document(
            meeting_id=meeting_id,
            document_kind=kind,
            expected_revision=0,
            content=content,
            now_ms=MEETING_STARTED_AT_MS + 130_000,
        )

    def forbid_remote_call(*args, **kwargs):
        raise AssertionError("DOCX export must not call a remote service")

    monkeypatch.setattr(app_module.httpx, "request", forbid_remote_call)
    response = client.get(f"/v2/meetings/{meeting_id}/export?format=docx")

    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    for expected in (
        "架构评审",
        "会议日期：2026-07-18 09:30",
        "会议时长：02:05",
        "会议复盘",
        "复盘结论",
        "决策",
        "采用双层缓存",
        "待办",
        "补充压测",
        "风险",
        "缓存击穿",
        "完整会议文字",
        "[01:01-01:05] 架构师：确认缓存失效策略。",
    ):
        assert expected in document
    persistence.close()


def test_v2_exports_follow_repeated_edits_without_writing_server_documents(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    meeting_id = "meeting-export-repeat-edit"
    _seed_export_meeting(app, meeting_id)
    first_document = _save_user_document(
        client,
        meeting_id,
        "minutes",
        {"markdown": "# 第一版人工纪要"},
    )
    before_first_export = _managed_file_hashes(tmp_path)

    first = {
        export_format: client.get(f"/v2/meetings/{meeting_id}/export?format={export_format}")
        for export_format in ("markdown", "docx", "json")
    }

    assert all(response.status_code == 200 for response in first.values())
    assert _managed_file_hashes(tmp_path) == before_first_export
    second_document = client.patch(
        f"/v2/meetings/{meeting_id}/documents/minutes",
        json={
            "expected_revision": first_document["revision"],
            "content_json": {"markdown": "# 第二版人工纪要"},
        },
    )
    assert second_document.status_code == 200
    before_second_export = _managed_file_hashes(tmp_path)
    second = {
        export_format: client.get(f"/v2/meetings/{meeting_id}/export?format={export_format}")
        for export_format in ("markdown", "docx", "json")
    }

    assert all(response.status_code == 200 for response in second.values())
    assert _managed_file_hashes(tmp_path) == before_second_export
    assert "第二版人工纪要" in second["markdown"].text
    assert "第一版人工纪要" not in second["markdown"].text
    assert second["json"].json()["minutes"]["markdown"] == "# 第二版人工纪要"
    with ZipFile(BytesIO(second["docx"].content)) as archive:
        second_docx = archive.read("word/document.xml").decode("utf-8")
    assert "第二版人工纪要" in second_docx
    assert first["markdown"].content != second["markdown"].content
    assert first["docx"].content != second["docx"].content
    assert first["json"].content != second["json"].content
    assert not list(tmp_path.rglob("*.md"))
    assert not list(tmp_path.rglob("*.docx"))
    assert not list(tmp_path.rglob("*.json"))
    app.state.v2_persistence.close()


@pytest.mark.parametrize(
    ("fault", "expected_status", "expected_code"),
    [
        ("unsupported", 422, "unsupported_export_format"),
        ("storage", 503, "export_storage_unavailable"),
        ("generation", 500, "export_document_generation_failed"),
        ("download", 500, "export_download_contract_failed"),
    ],
)
def test_v2_export_errors_are_typed_and_do_not_leak_paths(
    tmp_path,
    monkeypatch,
    fault,
    expected_status,
    expected_code,
):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    meeting_id = "meeting-export-errors"
    _seed_export_meeting(app, meeting_id)
    secret_error = f"failure at {tmp_path}/private/export.docx with arbitrary provider response"
    export_format = "docx"
    if fault == "unsupported":
        export_format = "pdf"
    elif fault == "storage":
        monkeypatch.setattr(
            app.state.v2_persistence,
            "get_meeting",
            lambda meeting_id: (_ for _ in ()).throw(OSError(secret_error)),
        )
    elif fault == "generation":
        monkeypatch.setattr(
            app_module,
            "render_docx",
            lambda payload: (_ for _ in ()).throw(RuntimeError(secret_error)),
        )
    elif fault == "download":
        monkeypatch.setattr(
            app_module,
            "_v2_export_content_disposition",
            lambda meeting, extension: 'attachment; filename="../private.docx"',
        )

    response = client.get(f"/v2/meetings/{meeting_id}/export?format={export_format}")

    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert detail["error"] == expected_code
    assert str(tmp_path) not in response.text
    assert "arbitrary provider response" not in response.text
    app.state.v2_persistence.close()


def test_v2_export_returns_not_found_for_unknown_meeting(tmp_path):
    app = create_app(data_dir=tmp_path)
    response = TestClient(app).get("/v2/meetings/missing/export?format=json")

    assert response.status_code == 404
    app.state.v2_persistence.close()
