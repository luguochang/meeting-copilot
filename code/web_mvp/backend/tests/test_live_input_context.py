from __future__ import annotations

import asyncio
import json

import pytest

from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes
from meeting_copilot_web_mvp.realtime_answer_copilot import build_realtime_answer_messages
from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError, RealtimeIntelligenceRequest, run_realtime_coach_via_pi,
)
from meeting_copilot_web_mvp.user_memory import UserMemory
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded
import test_live_document_evidence as document_tests
from test_live_document_evidence import _bind_answer, _coach_response

sources = document_tests.sources


def _publish_coach(persistence, evidence):
    snapshot = persistence.get_coach_input_context("meeting")
    with persistence._lock:
        job = persistence._conn.execute("SELECT id FROM jobs WHERE kind='intelligence'").fetchone()[0]
    response = {**_coach_response(evidence), "input_context": snapshot["input_context"]}
    persistence.apply_intelligence_response(meeting_id="meeting", job_id=job, response=response, now_ms=2_400)
    return job, response


@pytest.mark.parametrize("change", ["project", "memory"])
def test_independent_input_revision_retracts_answer_and_coach_without_changing_document_version(sources, change):
    persistence, library, project, _, evidence = sources
    _bind_answer(persistence, evidence)
    _publish_coach(persistence, evidence)
    before = library.get_meeting_context("meeting")["context_revision"]
    if change == "project":
        state = ProjectOutcomes(library)
        state.create(project["id"], kind="task", text="核对发布条件", expected_project_revision=library.get_project(project["id"])["revision"])
        expected = "project_state_superseded"
    else:
        memory = UserMemory(library)
        candidate = memory.create(scope="meeting", scope_id="meeting", kind="preference", memory_key="response_length", value="brief")
        assert persistence.get_suggestion("answer")["status"] == "committed"  # Unconfirmed candidates have no model effect.
        memory.update(candidate["id"], expected_revision=1, status="active")
        expected = "user_memory_superseded"
    assert library.get_meeting_context("meeting")["context_revision"] == before
    answer = persistence.get_suggestion("answer")
    assert answer["status"] == "superseded" and answer["error_class"] == expected
    with persistence._lock:
        payload = json.loads(persistence._conn.execute("SELECT payload_json FROM meeting_events WHERE type='meeting.intelligence.applied' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        assert persistence._conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='answer' AND work_item_id='answer'").fetchone()[0] == 1
    assert payload["coach_intervention"] is None
    assert payload["coach_decision"]["status_reason"] == expected
    assert "术语" in payload["coach_decision"]["decision_reason"] if change == "memory" else "项目状态" in payload["coach_decision"]["decision_reason"]


def test_changed_input_context_blocks_late_projection_before_any_event(sources):
    persistence, library, project, _, evidence = sources
    snapshot = persistence.get_coach_input_context("meeting")
    response = {**_coach_response(evidence), "input_context": snapshot["input_context"]}
    with persistence._lock:
        job_id = persistence._conn.execute("SELECT id FROM jobs WHERE kind='intelligence'").fetchone()[0]
    ProjectOutcomes(library).create(project["id"], kind="decision", text="先核对条件", expected_project_revision=library.get_project(project["id"])["revision"])
    before = persistence.get_snapshot("meeting")
    with pytest.raises(IntelligenceEvidenceSuperseded, match="project state or memory"):
        persistence.apply_intelligence_response(meeting_id="meeting", job_id=job_id, response=response, now_ms=3_000)
    assert persistence.get_snapshot("meeting") == before


def test_confirmed_memory_enters_fast_prompt_as_style_and_aliases_not_business_facts(sources):
    persistence, library, _, _, evidence = sources
    memory = UserMemory(library)
    for key, value, kind, aliases in [("release", "发布", "term", ["release"]), ("response_length", "brief", "preference", [])]:
        candidate = memory.create(scope="meeting", scope_id="meeting", kind=kind, memory_key=key, value=value, aliases=aliases)
        memory.update(candidate["id"], expected_revision=1, status="active")
    _, bound = _bind_answer(persistence, evidence)
    messages = build_realtime_answer_messages(question_text="发布条件？", context_segments=bound["context_segments"],
        user_memory=bound["user_memory"], document_evidence=evidence)
    user = json.loads(messages[1]["content"])
    assert user["user_memory"]["preferences"]["response_length"] == "brief"
    assert user["user_memory"]["terms"] == [{"canonical": "发布", "aliases": ["release"]}]
    assert "不能提供业务事实" in messages[0]["content"]
    assert "1 到 2 个短句" in messages[0]["content"] and "不超过 100 字" in messages[0]["content"]
    assert "没有最低字数" in messages[0]["content"]


def test_read_project_state_returns_only_selected_confirmed_items_and_read_only_version(sources):
    persistence, library, project, _, _ = sources
    outcomes = ProjectOutcomes(library)
    current = outcomes.create(project["id"], kind="task", text="核对放行条件", expected_project_revision=library.get_project(project["id"])["revision"])
    registry = PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=library, project_outcomes=outcomes)
    result = registry.read_project_state()
    assert result[0]["source_type"] == "project_state" and len(result[0]["snapshot_hash"]) == 64
    assert [item["id"] for item in result[0]["items"]] == [current["id"]]
    assert registry.validate_project_state(result[0]) == result[0]
    assert registry._searches == 1  # Publication revalidation is not another paid tool call.
    outcomes.create(project["id"], kind="decision", text="发布前必须复核", expected_project_revision=library.get_project(project["id"])["revision"])
    with pytest.raises(ValueError, match="changed before publication"):
        registry.validate_project_state(result[0])
    library.bind_meeting_context("meeting")
    assert registry.read_project_state() == []


@pytest.mark.parametrize("trusted", [True, False])
def test_pi_project_state_grounding_only_accepts_host_validated_sidecar_snapshot(sources, trusted):
    persistence, library, project, _, _ = sources
    outcomes = ProjectOutcomes(library)
    outcomes.create(project["id"], kind="decision", text="发布预算八万元", expected_project_revision=library.get_project(project["id"])["revision"])
    state = PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=library, project_outcomes=outcomes).read_project_state()[0]
    request = RealtimeIntelligenceRequest.from_payload(meeting_id="meeting", state_revision=1,
        new_paragraphs=[{"id": "question", "text": "发布还需什么条件？", "revision": 1, "source_track": "system_audio"}],
        context_paragraphs=[], rolling_state={})
    response = {"action": "intervention", "intervention": {
        **_coach_response([])["coach_intervention"], "title": "确认发布预算", "confidence": 0.9,
        "recommendation": "先确认八万元的发布预算是否适用。", "reason": "项目的发布预算为八万元。"},
        "host_project_state": state, "metrics": {"elapsed_ms": 100}}

    class Fake:
        async def evaluate(self, payload):
            return response

    runtime = PiCoachSidecar(command=["unused-test-command"]) if trusted else Fake()
    if trusted:
        runtime.evaluate = Fake().evaluate
    coroutine = run_realtime_coach_via_pi(request=request, pi_runtime=runtime,
        provider_config={"base_url": "https://example.test", "api_key": "test-key", "model": "test-model", "timeout_seconds": 10})
    if trusted:
        assert asyncio.run(coroutine)["intervention"].recommendation == "先确认八万元的发布预算是否适用。"
    else:
        with pytest.raises(IntelligenceResponseValidationError, match="number"):
            asyncio.run(coroutine)
