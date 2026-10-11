"""Native automatic/manual freezes read authorized meeting material, without live IO.

All document text and State replies are constructed fixtures. State uses the
real handler/parser/transaction with a local protocol fake; Pi is never run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.document_import import extract_document
from meeting_copilot_web_mvp.knowledge_context import source_evidence_state
from test_discussion_short_tail_catchup import append
from test_discussion_state_background import Provider


TOPIC = "账户付款短信职责"
SPEECH = "账户付款短信职责如何划分？"
PREREQUISITE = "继续操作前必须由本人重新授权读取原始文件，并核对服务端确认位置；缺少确认不得自动恢复。"
_INTRO = "页面图标颜色采用纯色，按钮排列保持左右一致，表格每行展示编号及名称，滚动时边框固定。"
LONG_DOCUMENT = (_INTRO * 30)[:1050] + PREREQUISITE
LONG_DOCUMENT += "图标说明保持清楚。" * 20
LONG_DOCUMENT = LONG_DOCUMENT[:1200]


@pytest.fixture
def material_app(tmp_path, monkeypatch):
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-test-key",
        model="synthetic-model", timeout_seconds=25, is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_kw: None)
    for key in ("MEETING_COPILOT_REALTIME_COACH_ENABLED", "MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED",
                "MEETING_COPILOT_DISCUSSION_STATE_ENABLED"):
        monkeypatch.setenv(key, "1")
    app = app_module.create_app(data_dir=tmp_path, prewarm_funasr=False, semantic_projection_mode="llm_first")
    now = time.time_ns() // 1_000_000
    app.state.v2_persistence.create_meeting(meeting_id="review", title=None, now_ms=now - 100_000)
    app.state.meeting_preparation_store.save("review", live_analysis_profile="participation", updated_at_ms=now)
    monkeypatch.setattr(app_module.time, "time_ns", lambda: now * 1_000_000)
    yield app, now
    for repository in reversed(app.state.sqlite_repositories):
        repository.close()


def import_text(app, content, *, set_id=None, filename="需求资料.txt", enabled=True, selected=None):
    repository = app.state.meeting_materials_repository
    material_set = repository.get_set(set_id) if set_id else repository.create_set()
    attachment = app.state.meeting_material_importer.stage(material_set["id"], filename,
        content.encode("utf-8"), content_type="text/plain")
    job = app.state.meeting_material_jobs.claim()
    assert job is not None
    assert app.state.meeting_material_jobs.finish(job, extract_document(filename, content.encode("utf-8"), "text/plain"))
    attachment = repository.get_attachment(material_set["id"], attachment["id"])
    version = repository.get_version(material_set["id"], attachment["id"], attachment["latest_version_id"])
    spans = repository.spans(material_set["id"], attachment["id"], version["id"])["items"]
    if enabled:
        attachment = repository.usage(material_set["id"], attachment["id"], enabled=True,
            version_id=version["id"], selected_span_ids=None if selected is None else [spans[i]["id"] for i in selected],
            expected_revision=attachment["revision"])
    return repository.get_set(material_set["id"]), attachment, version, spans


def bind(app, set_id, *, meeting_id="review"):
    repository = app.state.meeting_materials_repository
    return repository.bind(meeting_id, set_id, expected_revision=repository.get_set(set_id)["revision"])


def understand(app, now, monkeypatch):
    persistence = app.state.v2_persistence
    for seq in range(1, 9):
        append(persistence, seq, SPEECH, now - 9000 + seq)
    queued = persistence.enqueue_discussion_state(meeting_id="review", now_ms=now - 3000)
    assert queued is not None
    running = persistence.claim_next_job(worker_id="native-state", lane="discussion_state", now_ms=now - 3000,
        lease_ms=30_000)
    assert running["id"] == queued["id"]
    provider = Provider(content=json.dumps({"paragraph_revisions": [], "state_changes": [], "follow_up": None,
        "topic_update": {"operation": "add", "title": TOPIC, "summary": "核对候选方案的职责划分",
            "evidence_segment_ids": ["s8"], "evidence_quote": SPEECH}}, ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: provider)
    output = asyncio.run(app.state.v2_discussion_state_job_handler_impl(running))
    assert output["provider_attempt_count"] == len(provider.calls) == 1
    assert persistence.complete_job(job_id=running["id"], worker_id="native-state", now_ms=now - 2900, output=output)
    context = persistence.get_current_discussion("review")["context"]
    assert context["current_attention"]["contribution_opportunity"]["status"] == "present"
    assert context["current_problem"] == TOPIC


def freeze_pair(app):
    persistence = app.state.v2_persistence
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    jobs = [job for job in persistence.list_jobs(meeting_id="review") if job["trigger_type"] == "discussion_update"
        and job["request_context"].get("task_mode") != "discussion_state"]
    assert len(jobs) == 1
    automatic = jobs[0]["request_context"]
    assert automatic["automatic_direct"] is True and automatic["current_need_contract"] == "participation_need.v1"
    assert persistence.discussion_request_state("review", automatic) == "valid"
    # ASGI only, without lifespan: no listener, worker, browser or model call.
    client = TestClient(app)
    response = client.post("/v2/meetings/review/coach/request", json={"intent": "freeform", "request": TOPIC},
        headers={"idempotency-key": "constructed-material-comparison"})
    assert response.status_code == 202, response.text
    manual_jobs = [job for job in persistence.list_jobs(meeting_id="review") if job["trigger_type"] == "user_request"]
    assert len(manual_jobs) == 1
    manual = manual_jobs[0]["request_context"]
    assert persistence.discussion_request_state("review", manual) == "valid"
    return automatic, manual


def record(case, automatic, manual, **extra):
    directory = os.environ.get("MC140_EVIDENCE_DIR")
    if directory:
        destination = Path(directory) / f"{case}.json"
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"constructed_not_recorded": True, "automatic": {
                "source_evidence": automatic["source_evidence"], "material_context": automatic["material_context"]},
                "manual": {"source_evidence": manual["source_evidence"], "material_context": manual["material_context"]},
                **extra}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def test_automatic_freeze_reads_no_keyword_match_1200_character_prerequisite(material_app, monkeypatch):
    app, now = material_app
    material_set, attachment, version, spans = import_text(app, LONG_DOCUMENT)
    assert len(LONG_DOCUMENT) == 1200 and LONG_DOCUMENT.index(PREREQUISITE) == 1050
    assert len(spans) == 1 and spans[0]["text"] == LONG_DOCUMENT
    bind(app, material_set["id"])
    assert app.state.meeting_materials_repository.search_for_meeting("review", TOPIC) == []
    understand(app, now, monkeypatch)
    automatic, manual = freeze_pair(app)
    record("long-no-keyword", automatic, manual, document=LONG_DOCUMENT,
        document_sha256=hashlib.sha256(LONG_DOCUMENT.encode("utf-8")).hexdigest(),
        attachment_id=attachment["id"], version_id=version["id"], prerequisite_offset=1050)
    assert [item["quote"] for item in manual["source_evidence"]] == [LONG_DOCUMENT]
    assert manual["material_context"]["attachment_catalog"][0]["coverage"]["complete_read"] is True
    assert [item["quote"] for item in automatic["source_evidence"]] == [LONG_DOCUMENT]
    assert automatic["material_context"]["attachment_catalog"][0]["coverage"]["complete_read"] is True
    assert source_evidence_state(app.state.v2_persistence._conn, "review", automatic["source_evidence"]) == "valid"


@pytest.mark.parametrize("case,paragraph_lengths,full", [
    ("8000", [[4000, 4000]], True), ("8001", [[4000, 4000, 1]], False),
    ("12-sources", [[500, 500]] * 6, True), ("13-sources", [[500, 500]] * 5 + [[500, 500, 500]], False),
    ("6-files", [[100]] * 6, True), ("7-files", [[100]] * 7, False),
])
def test_native_freezes_preserve_existing_full_read_limits(material_app, monkeypatch, case, paragraph_lengths, full):
    app, now = material_app
    set_id = None
    expected_sources = []
    for index, lengths in enumerate(paragraph_lengths):
        paragraphs = [chr(0x4E00 + index * 10 + offset) * length for offset, length in enumerate(lengths)]
        material_set, _, _, spans = import_text(app, "\n\n".join(paragraphs), set_id=set_id, filename=f"资料{index}.txt")
        set_id = material_set["id"]
        expected_sources.extend(spans)
    bind(app, set_id)
    assert app.state.meeting_materials_repository.search_for_meeting("review", TOPIC) == []
    understand(app, now, monkeypatch)
    automatic, manual = freeze_pair(app)
    record(case, automatic, manual, authorized_source_count=len(expected_sources),
        authorized_characters=sum(len(span["text"]) for span in expected_sources))
    for packet in (manual, automatic):
        sources = packet["source_evidence"]
        if full:
            assert {item["source_id"] for item in sources} == {item["id"] for item in expected_sources}
            assert sum(len(item["quote"]) for item in sources) == sum(len(item["text"]) for item in expected_sources)
            assert all(item["coverage"]["complete_read"] for item in packet["material_context"]["attachment_catalog"])
        else:
            assert sources == []  # No keyword hit and the existing bounded fallback cannot claim a full read.
            assert not all(item["coverage"]["complete_read"] for item in packet["material_context"]["attachment_catalog"])


def test_native_freezes_only_enabled_selected_spans_from_this_meeting(material_app, monkeypatch):
    app, now = material_app
    material_set, _, _, spans = import_text(app, "界" * 1000 + "\n\n" + "未授权私密段。", selected=[0])
    import_text(app, "未启用资料不得进入上下文。", set_id=material_set["id"], enabled=False, filename="禁用.txt")
    bind(app, material_set["id"])
    app.state.v2_persistence.create_meeting(meeting_id="other", title=None, now_ms=now - 100_000)
    other_set, _, _, _ = import_text(app, "另一场会议资料不得进入本场。", filename="他场.txt")
    bind(app, other_set["id"], meeting_id="other")
    understand(app, now, monkeypatch)
    automatic, manual = freeze_pair(app)
    record("authorized-selection", automatic, manual, authorized_source_id=spans[0]["id"])
    for packet in (manual, automatic):
        assert len(packet["source_evidence"]) == 1
        assert packet["source_evidence"][0]["source_id"] == spans[0]["id"]
        assert packet["source_evidence"][0]["quote"] == "界" * 1000
        assert packet["material_context"]["attachment_catalog"][0]["coverage"]["selection"] == "selected_spans"


@pytest.mark.parametrize("activate_new", [False, True])
def test_saved_version_requires_explicit_cas_enable_before_native_freeze(material_app, monkeypatch, activate_new):
    app, now = material_app
    repository = app.state.meeting_materials_repository
    material_set, attachment, v1, _ = import_text(app, "框" * 250)
    v2 = repository.add_version(material_set["id"], attachment["id"], LONG_DOCUMENT,
        expected_revision=attachment["revision"], base_version_id=v1["id"])
    current = repository.get_attachment(material_set["id"], attachment["id"])
    assert current["active_version_id"] == v1["id"] and current["latest_version_id"] == v2["id"]
    if activate_new:
        repository.usage(material_set["id"], attachment["id"], enabled=True, version_id=v2["id"],
            expected_revision=current["revision"])
    bind(app, material_set["id"])
    understand(app, now, monkeypatch)
    automatic, manual = freeze_pair(app)
    record(f"version-{activate_new}", automatic, manual, v1=v1["id"], v2=v2["id"])
    expected = v2 if activate_new else v1
    for packet in (manual, automatic):
        assert len(packet["source_evidence"]) == 1
        assert packet["source_evidence"][0]["version_id"] == expected["id"]
        assert packet["source_evidence"][0]["quote"] == (LONG_DOCUMENT if activate_new else "框" * 250)


def test_native_scheduler_refuses_scope_change_between_retrieval_and_freeze(material_app, monkeypatch):
    app, now = material_app
    repository = app.state.meeting_materials_repository
    material_set, attachment, _, _ = import_text(app, "图" * 400)
    bind(app, material_set["id"])
    understand(app, now, monkeypatch)
    original = app_module.retrieve_meeting_knowledge
    obtained = []

    def disable_after_retrieval(*args, **kwargs):
        evidence = original(*args, **kwargs)
        assert evidence and evidence[0]["quote"] == "图" * 400
        obtained.extend(evidence)
        repository.usage(material_set["id"], attachment["id"], enabled=False, expected_revision=attachment["revision"])
        return evidence

    monkeypatch.setattr(app_module, "retrieve_meeting_knowledge", disable_after_retrieval)
    asyncio.run(app.state.refresh_due_coach_items())
    assert obtained and source_evidence_state(app.state.v2_persistence._conn, "review", obtained) == "stale"
    assert not [job for job in app.state.v2_persistence.list_jobs(meeting_id="review")
        if job["trigger_type"] == "discussion_update" and job["request_context"].get("task_mode") != "discussion_state"]
