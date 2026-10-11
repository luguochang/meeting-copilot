from __future__ import annotations

import json
import asyncio
from dataclasses import replace

import pytest

from meeting_copilot_web_mvp.knowledge_context import retrieve_meeting_knowledge, source_evidence_state
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError, RealtimeIntelligenceRequest, _ground_pi_intervention_reason, parse_realtime_coach_response, run_realtime_coach_via_pi
from meeting_copilot_web_mvp.source_library_provenance import source_snapshot
from meeting_copilot_web_mvp.realtime_answer_copilot import build_realtime_answer_messages
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded, StaleEvidenceError, V2Persistence


@pytest.fixture
def sources(tmp_path):
    persistence = V2Persistence(tmp_path / "sources.db", semantic_projection_mode="llm_first")
    persistence.commit_final_and_enqueue(meeting_id="meeting", final_id="final", segment_id="question",
        text="发布还需什么条件？", normalized_text="发布还需什么条件？", source_track="system_audio",
        started_at_ms=0, ended_at_ms=900, evidence_hash="question-hash", now_ms=1_000)
    library = KnowledgeRepository(persistence.database_path)
    project = library.create_project("项目", now_ms=1_100)
    document = library.create_document(project["id"], "发布规则", now_ms=1_200)
    version = library.add_version(document["id"], "发布需要法务批准，预算五万元。", now_ms=1_300)
    document = library.confirm_version(document["id"], version["id"], expected_revision=2, now_ms=1_400)
    library.bind_meeting_context("meeting", project["id"], now_ms=1_500)
    evidence = retrieve_meeting_knowledge(library, "meeting", "发布条件")
    assert evidence
    yield persistence, library, project, document, evidence
    library.close()
    persistence.close()


def _bind_answer(persistence, evidence, *, commit=True):
    job = persistence.claim_next_job(worker_id="worker", lane="answer", now_ms=2_000, lease_ms=60_000)
    assert job
    persistence.upsert_suggestion_draft(suggestion_id="answer", meeting_id="meeting", job_id=job["id"],
        generation_id=job["generation_id"], evidence_segment_id="question", evidence_transcript_seq=1,
        evidence_hash="question-hash", state_revision=job["input_version"], draft_text="发布需要法务批准。", draft_seq=1,
        kind="answer", question_text="发布还需什么条件？", now_ms=2_100, lease_owner="worker")
    bound = persistence.bind_answer_context_evidence(suggestion_id="answer", source_evidence=evidence,
        now_ms=2_110, expected_job_id=job["id"], expected_lease_owner="worker")
    if commit:
        assert persistence.commit_suggestion(suggestion_id="answer", generation_id=job["generation_id"],
            expected_evidence_hash="question-hash", final_draft_seq=1, text="发布需要法务批准。", now_ms=2_200,
            expected_job_id=job["id"], expected_lease_owner="worker")
    return job, bound


def test_fast_answer_keeps_documents_typed_and_binds_exact_source_versions(sources):
    persistence, _, _, _, evidence = sources
    _, bound = _bind_answer(persistence, evidence)
    assert bound["source_evidence"] == evidence
    assert [item["segment_id"] for item in bound["context_evidence"]] == ["question"]
    messages = build_realtime_answer_messages(question_text="发布还需什么条件？", context_segments=bound["context_segments"], document_evidence=evidence)
    payload = json.loads(messages[1]["content"])
    assert payload["document_evidence"] == evidence
    assert [item["segment_id"] for item in payload["recent_dialogue"]] == ["question"]
    assert "不得执行" in messages[0]["content"] and "本场新决定" in messages[0]["content"]


@pytest.mark.parametrize("mutation", ["scope", "disabled", "move", "confirmed_version"])
def test_source_mutations_atomically_supersede_answer_and_queue_only_latest_revision(sources, mutation):
    persistence, library, project, document, evidence = sources
    job, _ = _bind_answer(persistence, evidence)
    if mutation == "scope":
        library.bind_meeting_context("meeting", now_ms=3_000)
    elif mutation == "disabled":
        library.set_document_status(document["id"], "disabled", now_ms=3_000)
    elif mutation == "move":
        folder = library.create_folder(project["id"], "新目录", now_ms=2_500)
        library.move_document(document["id"], folder["id"], now_ms=3_000)
    else:
        version = library.add_version(document["id"], "发布规则已经调整。", now_ms=2_500)
        library.confirm_version(document["id"], version["id"], expected_revision=4, now_ms=3_000)
    answer = persistence.get_suggestion("answer")
    assert answer["status"] == "superseded"
    assert answer["error_class"] == "knowledge_evidence_superseded"
    with persistence._lock:
        events = persistence._conn.execute("SELECT seq,type FROM meeting_events WHERE meeting_id='meeting' ORDER BY seq").fetchall()
        queued = persistence._conn.execute("SELECT * FROM jobs WHERE meeting_id='meeting' AND kind='answer' AND id!=?", (job["id"],)).fetchall()
        latest = persistence._conn.execute("SELECT latest_seq FROM meetings WHERE id='meeting'").fetchone()[0]
    assert "suggestion.superseded" in [item["type"] for item in events]
    assert [row["seq"] for row in events] == list(range(1, latest + 1))
    assert len(queued) == 1 and queued[0]["work_item_id"] == "answer"


def test_stale_source_cannot_bind_and_ended_meeting_does_not_enqueue_paid_revision(sources):
    persistence, library, _, _, evidence = sources
    _, _ = _bind_answer(persistence, evidence)
    persistence.end_meeting(meeting_id="meeting", now_ms=2_600)
    library.bind_meeting_context("meeting", now_ms=3_000)
    with persistence._lock:
        assert persistence._conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='answer' AND work_item_id='answer'").fetchone()[0] == 0
    registry = PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=library)
    with pytest.raises(ValueError, match="live meeting"):
        registry.register_documents(evidence)


def test_document_registry_rejects_forged_quote_cross_scope_and_changed_versions(sources):
    persistence, library, _, _, evidence = sources
    registry = PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=library)
    found = registry.search_documents("发布")
    ref = {"source_id": found[0]["source_id"], "quote": "发布需要法务批准"}
    assert registry.validate_documents([ref])[0]["version_id"] == evidence[0]["version_id"]
    with pytest.raises(ValueError, match="verbatim"):
        registry.validate_documents([{**ref, "quote": "已获法务批准"}])
    library.bind_meeting_context("meeting", now_ms=3_000)
    with pytest.raises(KnowledgeScopeError):
        registry.validate_documents([ref])


def test_forged_document_binding_is_rejected_inside_answer_write_transaction(sources):
    persistence, _, _, _, evidence = sources
    job, _ = _bind_answer(persistence, [], commit=False)
    forged = [{**evidence[0], "quote": "未经确认的新事实"}]
    with pytest.raises(StaleEvidenceError):
        persistence.bind_answer_context_evidence(suggestion_id="answer", source_evidence=forged,
            now_ms=2_120, expected_job_id=job["id"], expected_lease_owner="worker")
    assert persistence.get_suggestion("answer")["source_evidence"] == []


def _coach_response(evidence):
    return {"paragraph_revisions": [], "state_changes": [], "follow_up": None,
        "coach_intervention": {"event_type": "commitment_risk", "recommendation": "先确认法务批准，再确定发布安排。",
            "reason": "需要先核对资料中的放行条件。", "evidence_segment_ids": ["question"],
            "evidence_quote": "发布还需什么条件？", "urgency": "medium"},
        "coach_decision": {"decision_id": "coach:documents", "status": "intervention", "lifecycle_action": "replace"},
        "source_evidence": evidence}


def test_coach_source_binding_retracts_durably_and_cannot_reappear_in_snapshot(sources):
    persistence, library, _, _, evidence = sources
    with persistence._lock:
        job_id = persistence._conn.execute("SELECT id FROM jobs WHERE kind='intelligence'").fetchone()[0]
    applied = persistence.apply_intelligence_response(meeting_id="meeting", job_id=job_id,
        response=_coach_response(evidence), now_ms=2_000)
    assert applied["coach_intervention"]["source_evidence"] == evidence
    library.bind_meeting_context("meeting", now_ms=3_000)
    with persistence._lock:
        row = persistence._conn.execute("SELECT * FROM coach_source_bindings").fetchone()
        events = persistence._conn.execute("SELECT payload_json FROM meeting_events WHERE type='meeting.intelligence.applied' ORDER BY seq").fetchall()
    assert row["invalidated_at_ms"] == 3_000
    retract = json.loads(events[-1][0])
    assert retract["coach_intervention"] is None
    assert retract["coach_decision"]["supersedes_decision_id"] == "coach:documents"
    assert retract["coach_decision"]["lifecycle_action"] == "retract"
    reopened = V2Persistence(persistence.database_path, semantic_projection_mode="llm_first")
    try:
        assert reopened._latest_active_coach_intervention_decision_locked("meeting", excluding_decision_id="") is None
    finally:
        reopened.close()


def test_coach_source_revision_barrier_rolls_back_entire_projection(sources):
    persistence, library, _, document, evidence = sources
    with persistence._lock:
        job_id = persistence._conn.execute("SELECT id FROM jobs WHERE kind='intelligence'").fetchone()[0]
    library.set_document_status(document["id"], "disabled", now_ms=2_000)
    before = persistence.get_snapshot("meeting")
    with pytest.raises(IntelligenceEvidenceSuperseded):
        persistence.apply_intelligence_response(meeting_id="meeting", job_id=job_id,
            response=_coach_response(evidence), now_ms=2_100)
    assert persistence.get_snapshot("meeting") == before


@pytest.mark.parametrize("trusted", [True, False])
def test_pi_runner_only_preserves_registered_sources_from_real_sidecar_wrapper(sources, trusted):
    _, _, _, _, evidence = sources
    request = RealtimeIntelligenceRequest.from_payload(meeting_id="meeting", state_revision=1,
        new_paragraphs=[{"id": "question", "text": "发布还需什么条件？", "revision": 1, "source_track": "system_audio"}],
        context_paragraphs=[], rolling_state={}, document_evidence=evidence)
    response = {"action": "intervention", "intervention": {
        **_coach_response(evidence)["coach_intervention"], "title": "确认发布条件", "confidence": 0.9},
        "host_source_evidence": evidence, "metrics": {"elapsed_ms": 100}}

    class Fake:
        async def evaluate(self, payload):
            return response

    if trusted:
        delegate = PiCoachSidecar(command=["unused-test-command"])
        delegate.evaluate = Fake().evaluate
        class Wrapper:
            _delegate = delegate
            evaluate = Fake().evaluate
        runtime = Wrapper()
    else:
        runtime = Fake()
    result = asyncio.run(run_realtime_coach_via_pi(request=replace(request), pi_runtime=runtime,
        provider_config={"base_url": "https://example.test", "api_key": "test-key", "model": "test-model", "api_style": "chat_completions", "timeout_seconds": 10}))
    assert result["source_evidence"] == (evidence if trusted else [])
    assert result["intervention"].source_evidence == (tuple(evidence) if trusted else ())


def test_copied_note_revision_cannot_remain_current_document_evidence(sources):
    persistence, library, project, document, evidence = sources
    persistence.create_note(note_id="note:legal", meeting_id="meeting", title="发布规则",
        body="发布需要法务批准，预算五万元。", source_kind="manual", source_message_id=None, evidence=[], now_ms=1_700)
    with persistence._write_transaction():
        snapshot = source_snapshot(persistence._conn, "note", "note:legal")
        persistence._conn.execute("INSERT INTO knowledge_source_bindings VALUES('note','note:legal',?,?,?)",
            (project["id"], document["id"], 1_800))
        persistence._conn.execute("INSERT INTO knowledge_source_versions VALUES(?,?,'note','note:legal',?,?,?)",
            (document["active_version_id"], document["id"], snapshot["source_version"], snapshot["source_hash"], 1_800))
        assert source_evidence_state(persistence._conn, "meeting", evidence) == "valid"
    assert retrieve_meeting_knowledge(library, "meeting", "发布")
    _bind_answer(persistence, evidence)
    with persistence._lock:
        job_id = persistence._conn.execute("SELECT id FROM jobs WHERE kind='intelligence'").fetchone()[0]
    persistence.apply_intelligence_response(meeting_id="meeting", job_id=job_id,
        response=_coach_response(evidence), now_ms=2_300)
    persistence.update_note("note:legal", expected_version=1, body="发布不再使用旧条件。", now_ms=2_500)
    assert retrieve_meeting_knowledge(library, "meeting", "发布") == []
    assert persistence.get_suggestion("answer")["status"] == "superseded"
    with persistence._lock:
        assert source_evidence_state(persistence._conn, "meeting", evidence) == "stale"
        assert persistence._conn.execute("SELECT invalidated_at_ms FROM coach_source_bindings").fetchone()[0] == 2_500
    with pytest.raises(KnowledgeScopeError):
        PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=library).register_documents(evidence)


def test_actual_synthetic_relay_card_grounds_explanation_but_keeps_material_recommendations_bound(sources):
    _, _, _, _, evidence = sources
    request = RealtimeIntelligenceRequest.from_payload(meeting_id="meeting", state_revision=1,
        trigger_type="user_request", user_request="读取发布资料后建议", document_evidence=evidence,
        new_paragraphs=[{"id": "question", "text": "发布还需什么条件？", "revision": 1, "source_track": "system_audio"}],
        context_paragraphs=[], rolling_state={})
    # Exact user-facing fields emitted by the real, isolated Responses/Pi SDK
    # sample on 2026-10-01. Only the synthetic transcript ID is normalized.
    card = {"confidence": 0.87, "event_type": "question_to_user", "title": "核对发布放行条件",
        "recommendation": "放行前，请确认法务批准和五万元预算是否都已落实；还有其他必须满足的条件吗？",
        "reason": "对方正在询问发布条件；资料列出法务批准和预算，适合请参会者核对是否还有其他门槛。",
        "evidence_quote": "发布还需什么条件？", "evidence_segment_ids": ["question"], "urgency": "medium"}
    grounded_card, diagnostic = _ground_pi_intervention_reason(card, request=request)
    parsed = parse_realtime_coach_response(json.dumps({"intervention": grounded_card}, ensure_ascii=False), request=request)
    assert parsed.recommendation == card["recommendation"]
    assert parsed.confidence == card["confidence"]
    assert parsed.reason == "原文依据：" + evidence[0]["quote"]
    assert diagnostic == {"mode": "source_quote_fallback", "removed_lines": 1, "retained_lines": 0, "source_type": "document"}
    for recommendation, reason, violation in [
        (card["recommendation"].replace("五万元", "八万元"), card["reason"], "number"),
        (card["recommendation"], "核对是否还有其他门槛。发布必须达到门槛。", "material_term"),
        (card["recommendation"], "法务批准已经通过。", "state"),
        ("请在明天放行前确认法务批准。", card["reason"], "deadline"),
        ("请由李工负责核对法务批准。", card["reason"], "owner"),
    ]:
        with pytest.raises(IntelligenceResponseValidationError, match=violation):
            parse_realtime_coach_response(json.dumps({"intervention": {**card, "recommendation": recommendation, "reason": reason}}, ensure_ascii=False), request=request)


def _release_request(evidence):
    return RealtimeIntelligenceRequest.from_payload(meeting_id="meeting", state_revision=1,
        trigger_type="user_request", user_request="读取发布资料后建议", document_evidence=evidence,
        new_paragraphs=[{"id": "question", "text": "发布还需什么条件？", "revision": 1, "source_track": "system_audio"}],
        context_paragraphs=[], rolling_state={})


def _release_card(**changes):
    return {"confidence": 0.77, "event_type": "question_to_user", "title": "核对发布放行条件",
        "recommendation": "请问法务批准和五万元预算是否都已落实？还有其他放行条件吗？",
        "reason": "对方正询问发布条件；资料列出法务批准和预算，适合追问两项是否落实及还有无其他门槛。",
        "evidence_quote": "发布还需什么条件？", "evidence_segment_ids": ["question"], "urgency": "medium", **changes}


def _evaluate_release_card(request, card, *, host_sources=()):
    # Real Sidecar trust wrapper and actual host merge; evaluate is an explicitly
    # offline provider-output fixture. The command is never launched.
    runtime = PiCoachSidecar(command=["unused-offline-test"])
    async def evaluate(payload):
        return {"action": "intervention", "intervention": card, "host_source_evidence": list(host_sources),
            "decision_reason": card["reason"], "metrics": {"turns": 2, "tool_calls": 2, "elapsed_ms": 100}}
    runtime.evaluate = evaluate
    return asyncio.run(run_realtime_coach_via_pi(request=request, pi_runtime=runtime, priority_mode="deep",
        provider_config={"base_url": "https://offline.invalid", "api_key": "unused-synthetic", "model": "offline", "timeout_seconds": 10}))


@pytest.mark.parametrize(("recommendation", "reason", "confidence"), [
    ("放行前，请确认法务批准和五万元预算是否都已落实；还有其他必须满足的条件吗？",
     "对方正在询问发布条件；资料列出法务批准和预算，适合请参会者核对是否还有其他门槛。", 0.87),
    (_release_card()["recommendation"], _release_card()["reason"], 0.77),
])
def test_captured_synthetic_pi_cards_use_actual_host_quote_without_changing_recommendation_or_confidence(sources, recommendation, reason, confidence):
    _, _, _, _, evidence = sources
    card = _release_card(recommendation=recommendation, reason=reason, confidence=confidence,
        say_this=recommendation, why_now=reason)
    result = _evaluate_release_card(_release_request(()), card, host_sources=evidence)
    assert result["intervention"].recommendation == recommendation
    assert result["intervention"].confidence == confidence
    assert result["intervention"].reason == "原文依据：" + evidence[0]["quote"]
    assert result["intervention"].to_dict()["why_now"] == result["intervention"].reason
    assert result["decision_reason"] == result["intervention"].reason[:160]
    assert result["agent_metrics"]["reason_grounding"] == {"mode": "source_quote_fallback",
        "removed_lines": 1, "retained_lines": 0, "source_type": "document"}
    assert "门槛" not in json.dumps(result, ensure_ascii=False, default=str)


@pytest.mark.parametrize("unsupported", ["预算已增加到八万元。", "法务批准已经通过。", "李工负责在明天放行。"])
def test_pi_reason_keeps_grounded_line_and_drops_unsupported_explanation(sources, unsupported):
    _, _, _, _, evidence = sources
    reason = "对方正在询问发布条件。\n" + unsupported
    result = _evaluate_release_card(_release_request(()), _release_card(reason=reason, why_now=reason), host_sources=evidence)
    assert result["intervention"].reason == "对方正在询问发布条件。"
    assert result["agent_metrics"]["reason_grounding"] == {"mode": "filtered", "removed_lines": 1,
        "retained_lines": 1, "source_type": None}
    assert unsupported not in json.dumps(result, ensure_ascii=False, default=str)


@pytest.mark.parametrize(("changes", "violation"), [
    ({"recommendation": "请确认八万元预算是否已落实。"}, "number"),
    ({"recommendation": "请由李工负责核对法务批准。"}, "owner"),
    ({"recommendation": "请在明天确认法务批准再放行。"}, "deadline"),
    ({"recommendation": "法务批准已经通过，现在可直接放行。"}, "state"),
    ({"title": "确认八万元预算"}, "number"),
])
def test_pi_source_quote_explanation_never_rescues_an_unsupported_recommendation_or_title(sources, changes, violation):
    _, _, _, _, evidence = sources
    with pytest.raises(IntelligenceResponseValidationError, match=violation) as caught:
        _evaluate_release_card(_release_request(()), _release_card(**changes), host_sources=evidence)
    assert caught.value.metrics["turns"] == 2
    assert "reason_grounding" not in caught.value.metrics


def test_pi_reason_fallback_requires_valid_evidence_and_can_quote_the_current_transcript():
    request = _release_request(())
    card = _release_card(recommendation="请问发布还需哪些条件？先确认再安排。", reason="预算八万元已经批准。")
    result = _evaluate_release_card(request, card)
    assert result["intervention"].reason == "原文依据：发布还需什么条件？"
    assert result["agent_metrics"]["reason_grounding"]["source_type"] == "transcript"
    for changes in ({"evidence_quote": "发布已经批准。"}, {"evidence_segment_ids": ["unregistered"]}, {"reason": ""}):
        with pytest.raises(IntelligenceResponseValidationError):
            _evaluate_release_card(request, {**card, **changes})


def test_pi_reason_fallback_remains_bounded_and_verbatim(sources):
    _, _, _, _, evidence = sources
    long_quote = "发布需要法务批准，预算五万元。" + "资料属于合成发布规则。" * 50
    grounded_card, diagnostic = _ground_pi_intervention_reason(_release_card(),
        request=_release_request([{**evidence[0], "quote": long_quote}]))
    assert grounded_card["reason"] == "原文依据：" + long_quote[:294]
    assert len(grounded_card["reason"]) <= 300
    assert diagnostic["mode"] == "source_quote_fallback"


@pytest.mark.parametrize("coherent_package", [True, False])
def test_pi_reason_repair_preserves_deep_package_alias_contract(sources, coherent_package):
    _, _, _, _, evidence = sources
    request = replace(_release_request(()), rolling_state={"current_answer": {"answer": "先核对资料，再确认发布条件。"}})
    card = _release_card()
    card["coaching_package"] = {
        "headline": card["title"], "question_intent": "核对发布条件",
        "core_judgement": "请确认法务批准和五万元预算。",
        "why_it_matters": card["reason"] if coherent_package else "另一份不一致说明。",
        "say_this_addition": card["recommendation"], "missing_points": [], "constraints": [],
        "risks": [], "next_actions": [], "likely_follow_ups": [],
        "evidence_refs": [{"segment_id": "question", "quote": card["evidence_quote"]}],
        "confidence": card["confidence"],
    }
    if coherent_package:
        result = _evaluate_release_card(request, card, host_sources=evidence)
        assert result["intervention"].coaching_package["why_it_matters"] == result["intervention"].reason
        assert result["intervention"].coaching_package["say_this_addition"] == card["recommendation"]
    else:
        unchanged, diagnostic = _ground_pi_intervention_reason(card, request=_release_request(evidence))
        assert unchanged is card and diagnostic == {}
        with pytest.raises(IntelligenceResponseValidationError):
            _evaluate_release_card(request, card, host_sources=evidence)


@pytest.mark.parametrize("reference", ["这两项", "上述2个条件", "前述两项", "以上2项", "列出的两项"])
def test_pi_quantity_reference_removes_only_the_anaphoric_count_after_verified_list(sources, reference):
    _, _, _, _, evidence = sources
    recommendation = f"按发布规则，需要法务批准和五万元预算；{reference}目前是否都已落实？"
    result = _evaluate_release_card(_release_request(()), _release_card(recommendation=recommendation,
        say_this=recommendation, reason="对方正询问发布条件，适合核对放行状态。"), host_sources=evidence)
    assert result["intervention"].recommendation == recommendation.replace(reference, "")
    assert result["intervention"].to_dict()["say_this"] == recommendation.replace(reference, "")
    assert result["agent_metrics"]["quantity_reference_normalization"] == {"mode": "remove_anaphoric_count", "removed_references": 1}
    assert result["intervention"].source_evidence == tuple(evidence)
    assert result["intervention"].confidence == 0.77


@pytest.mark.parametrize(("recommendation", "violation"), [
    ("预算新增两项支出，请确认是否可接受。", "number"),
    ("共两项任务，请确认下一步执行。", "number"),
    ("两项已经完成，现在可直接放行。", "state"),
    ("请确认这两项条件是否已经落实。", "number"),
    ("请确认这八项条件是否已经落实。", "number"),
    ("需要法务批准和八万元预算；这两项目前是否都已落实？", "number"),
    ("需要法务批准和五万元预算；这两项已经通过，可以直接放行。", "state"),
    ("需要法务批准和五万元预算；这八项目前是否都已落实？", "number"),
])
def test_pi_quantity_reference_normalization_never_exempts_new_business_quantities_or_state(sources, recommendation, violation):
    _, _, _, _, evidence = sources
    with pytest.raises(IntelligenceResponseValidationError, match=violation) as caught:
        _evaluate_release_card(_release_request(()), _release_card(recommendation=recommendation), host_sources=evidence)
    assert "quantity_reference_normalization" not in caught.value.metrics
