import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from tools import participation_state_effort_probe as tool
from meeting_copilot_web_mvp.realtime_intelligence import (
    RealtimeIntelligenceRequest, build_realtime_intelligence_messages,
)


def frozen(case_id="business_boundary"):
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="participation_global_test", state_revision=1, trigger_type="discussion_update",
        work_item_id="synthetic-state", new_paragraphs=[{"id": "p1", "revision": 1,
        "text": "当前候选是权益先于通知，尚未拍板。"}], context_paragraphs=[], rolling_state={"topic": None, "open_items": []},
        allow_paragraph_revisions=False)
    return tool.FrozenCase(case_id, "synthetic-state", request,
        build_realtime_intelligence_messages(request), [{"segment_id": "p1", "revision": 1, "evidence_hash": "hash"}],
        "metadata", "report")


def state_payload(*, empty=False, quote="当前候选是权益先于通知，尚未拍板。"):
    return {"paragraph_revisions": [], "topic_update": None, "follow_up": None,
        "state_changes": [] if empty else [{"type": "decision", "operation": "add", "item_id": "entitlement-first",
        "content": "权益先于通知", "status": "candidate", "owner": None, "deadline": None,
        "evidence_segment_ids": ["p1"], "evidence_quote": quote, "confidence": 0.9}]}


def response_for(payload):
    return httpx.Response(200, json={"id": "faux", "model": "gpt-6-sol", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text",
        "text": json.dumps(payload, ensure_ascii=False)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}})


def config(**changes):
    return SimpleNamespace(**{"model": "gpt-6-sol", "api_style": "responses", "is_mock": False,
        "base_url": "https://private-relay.example.test", "api_key": "PRIVATE_KEY", **changes})


def test_pairs_keep_exact_wire_except_reasoning_and_do_not_retry_or_expose_keys():
    bodies, keys = [], []
    def handler(request):
        bodies.append(json.loads(request.content))
        keys.append(request.headers["Idempotency-Key"])
        return response_for(state_payload())
    result = asyncio.run(tool.evaluate_pair([frozen(), frozen("attachment_tradeoff")], config(),
        inner=httpx.MockTransport(handler)))
    assert result["actual_upstream_POSTs"] == result["maximum_actual_POSTs"] == len(bodies) == 4
    assert result["all_host_evidence_validated"] and result["all_minimum_semantic_structure"]
    assert len(set(keys)) == 4  # A/B never shares a provider idempotency result.
    for a, b in (bodies[:2], bodies[2:]):
        assert {k:v for k,v in a.items() if k != "reasoning"} == {k:v for k,v in b.items() if k != "reasoning"}
        assert (a.get("reasoning"), b.get("reasoning")) in ((None,{"effort":"low"}), ({"effort":"low"},None))
        assert a["max_output_tokens"] == b["max_output_tokens"] >= 768
        assert "temperature" not in a  # Current official Responses adapter omits it.
    for item in result["results"]:
        assert item["actual_upstream_POSTs"] == 1
        assert item["exact_supported_quote_count"] == 1
        assert item["candidate_decision_count"] == 1
        assert item["provider_complete_ms"] >= 0
        assert item["usage"]["total_tokens"] == 150
    report = json.dumps(result)
    assert "PRIVATE_KEY" not in report and "private-relay" not in report
    assert "Authorization" not in report and "instructions" not in report


def test_empty_host_valid_state_is_not_a_quality_pass_and_stops_once():
    calls = []
    def handler(request):
        calls.append(request)
        return response_for(state_payload(empty=True))
    result = asyncio.run(tool.evaluate_pair([frozen()], config(), inner=httpx.MockTransport(handler)))
    assert len(calls) == result["actual_upstream_POSTs"] == 1
    assert result["all_host_evidence_validated"] is True
    assert result["all_minimum_semantic_structure"] is False
    assert result["results"][0]["state_change_count"] == 0


def test_rejected_quote_is_not_archived_and_has_fixed_field_diagnostics():
    result = asyncio.run(tool.evaluate_pair([frozen()], config(), inner=httpx.MockTransport(
        lambda request: response_for(state_payload(quote="REJECTED_RAW_MARKER")))))
    assert result["actual_upstream_POSTs"] == 1
    assert result["all_host_evidence_validated"] is False
    assert "REJECTED_RAW_MARKER" not in json.dumps(result)
    assert "accepted_state" not in result["results"][0]
    assert result["results"][0]["failure_diagnostics"]["rule"] == "quote_not_verbatim"


def test_parameter_400_stops_once_without_non_streaming_retry():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"code": "UNSUPPORTED_PARAMETER", "message": "PRIVATE_RAW_ERROR"}})
    result = asyncio.run(tool.evaluate_pair([frozen()], config(), inner=httpx.MockTransport(handler)))
    assert len(calls) == result["actual_upstream_POSTs"] == 1
    assert result["results"][0]["wire"][0]["HTTP_status"] == 400
    assert "PRIVATE_RAW_ERROR" not in json.dumps(result)


def test_transport_timeout_is_counted_without_message_or_retry():
    def handler(request):
        raise httpx.ReadTimeout("PRIVATE_ERROR_AND_URL", request=request)
    result = asyncio.run(tool.evaluate_pair([frozen()], config(), inner=httpx.MockTransport(handler)))
    assert result["actual_upstream_POSTs"] == 1
    assert "PRIVATE_ERROR_AND_URL" not in json.dumps(result)
    assert result["results"][0]["wire"][0]["transport_error_class"] == "ReadTimeout"


def test_budget_is_atomic_under_concurrent_posts():
    async def exercise():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={})
        transport = tool.WireBudgetTransport(httpx.MockTransport(handler), 2)
        async with httpx.AsyncClient(transport=transport) as client:
            replies = await asyncio.gather(*[client.post("https://relay.example.test/v1/responses",
                json={"model":"gpt-6-sol", "stream":True}) for _ in range(4)], return_exceptions=True)
        assert len(calls) == transport.count == 2
        assert sum(isinstance(r,RuntimeError) for r in replies) == 2
    asyncio.run(exercise())


@pytest.mark.parametrize("changes", [{"model":"other"},{"api_style":"chat_completions"},{"is_mock":True}])
def test_other_provider_contract_is_rejected_before_any_call(changes):
    def forbidden(request):
        raise AssertionError("must not call")
    with pytest.raises(ValueError):
        asyncio.run(tool.evaluate_pair([frozen()], config(**changes), inner=httpx.MockTransport(forbidden)))


def test_default_dry_run_never_loads_config_or_calls_network(tmp_path, monkeypatch):
    report = tmp_path / "dry.json"
    monkeypatch.setattr(tool, "prepare_cases", lambda _db,_pairs: [frozen(),frozen()])
    monkeypatch.setattr(tool, "official_state_prompt", lambda: "current literal")
    monkeypatch.setattr(tool, "evaluate_pair", lambda *_a,**_kw: pytest.fail("no model execution"))
    monkeypatch.setattr(tool.sys, "argv", ["probe", "--report", str(report)])
    assert tool.main() == 0
    r = json.loads(report.read_text())
    assert r["executed"] is False and r["actual_upstream_POSTs"] == 0
    assert r["maximum_actual_POSTs"] == 4
    assert "当前候选" not in report.read_text()
    with pytest.raises(ValueError,match="refuse_to_overwrite"):
        tool.main()


def test_prompt_literal_tracks_production_without_importing_app(tmp_path):
    path = tmp_path / "source.py"
    path.write_text('class StateOnlyProvider:\n async def complete(self):\n  bounded_messages[0]["content"]="你是中文讨论工作记忆整理器。版本甲"\n',encoding="utf-8")
    assert tool.official_state_prompt(path).endswith("版本甲")
    path.write_text('class StateOnlyProvider: pass',encoding="utf-8")
    with pytest.raises(ValueError,match="prompt_contract_changed"):
        tool.official_state_prompt(path)


def test_source_reconstruction_refuses_invalid_pair_count_without_database_access():
    with pytest.raises(ValueError,match="pairs_must_be"):
        tool.prepare_cases(Path("must-not-open"), 1)


def test_journal_is_durable_before_forward_and_cannot_reset_budget(tmp_path):
    journal = tmp_path / "wire.json"
    def handler(request):
        saved = json.loads(journal.read_text())
        assert saved["reserved_attempts"] == saved["forward_dispatch_marked_attempts"] == 1
        assert saved["records"][0]["HTTP_status"] is None
        assert "PRIVATE" not in journal.read_text() and "Authorization" not in journal.read_text()
        return httpx.Response(400, json={"error": {"message": "PRIVATE"}})
    result = asyncio.run(tool.evaluate_pair([frozen()], config(),
        inner=httpx.MockTransport(handler), journal_path=journal))
    saved = json.loads(journal.read_text())
    assert saved["forward_started_attempts"] == result["actual_upstream_POSTs"] == 1
    assert saved["reserved_attempts"] == result["budget_reserved_attempts"] == 1
    assert saved["records"][0]["HTTP_status"] == 400
    assert saved["remote_receipt_or_charge_known"] is False
    original = journal.read_bytes()
    with pytest.raises(FileExistsError):
        tool.WireBudgetTransport(httpx.MockTransport(handler), 2, journal)
    assert journal.read_bytes() == original


def test_failed_budget_publish_never_forwards_or_claims_a_remote_call(tmp_path, monkeypatch):
    calls = []
    def broken_publish(self):
        raise OSError("PRIVATE filesystem detail")
    monkeypatch.setattr(tool.WireBudgetTransport, "_publish", broken_publish)
    result = asyncio.run(tool.evaluate_pair([frozen()], config(),
        inner=httpx.MockTransport(lambda request: calls.append(request)), journal_path=tmp_path / "wire.json"))
    assert calls == []
    assert result["actual_upstream_POSTs"] == 0 and result["budget_reserved_attempts"] == 1
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_main_escape_preserves_sent_count_and_failure_report(tmp_path, monkeypatch, error_type):
    from meeting_copilot_web_mvp.llm_service import LlmConfig
    report = tmp_path / "failure.json"
    monkeypatch.setattr(tool, "prepare_cases", lambda _db, _pairs: [frozen(), frozen()])
    monkeypatch.setattr(LlmConfig, "from_env", lambda: config())
    async def escaping(_cases, _config, *, journal_path):
        budget = tool.WireBudgetTransport(httpx.MockTransport(lambda request: httpx.Response(200)), 4, journal_path)
        async with httpx.AsyncClient(transport=budget) as client:
            await client.post("https://private-relay.example.test/v1/responses",
                json={"model": "gpt-6-sol", "stream": True})
        raise error_type("PRIVATE escape detail")
    monkeypatch.setattr(tool, "evaluate_pair", escaping)
    monkeypatch.setattr(tool.sys, "argv", ["probe", "--execute", "--report", str(report)])
    assert tool.main() == 1
    saved = json.loads(report.read_text())
    assert saved["actual_upstream_POSTs"] == saved["budget_reserved_attempts"] == 1
    assert saved["stop_reason"] == "unhandled_safe_failure" and saved["error_class"] == error_type.__name__
    assert saved["remote_receipt_or_charge_known"] is False
    assert "PRIVATE" not in report.read_text() and "private-relay" not in report.read_text()
    with pytest.raises(ValueError, match="refuse_to_overwrite"):
        tool.main()


def test_crash_gap_dispatch_intent_is_unknown_not_zero_and_never_reset(tmp_path, monkeypatch):
    from meeting_copilot_web_mvp.llm_service import LlmConfig
    report = tmp_path / "crash.json"
    monkeypatch.setattr(tool, "prepare_cases", lambda _db, _pairs: [frozen(), frozen()])
    monkeypatch.setattr(LlmConfig, "from_env", lambda: config())
    async def crash_gap(_cases, _config, *, journal_path):
        journal_path.write_text(json.dumps({"forward_started_attempts": 0,
            "forward_dispatch_marked_attempts": 1, "reserved_attempts": 1}), encoding="utf-8")
        raise RuntimeError("after durable dispatch marker")
    monkeypatch.setattr(tool, "evaluate_pair", crash_gap)
    monkeypatch.setattr(tool.sys, "argv", ["probe", "--execute", "--report", str(report)])
    assert tool.main() == 1
    saved = json.loads(report.read_text())
    assert saved["actual_upstream_POSTs"] is None
    assert saved["budget_reserved_attempts"] == saved["forward_dispatch_marked_attempts"] == 1
    assert saved["forward_started_attempts_recorded"] == 0


def test_answered_quality_requires_retry_resolution_and_known_identity():
    case = frozen("answered_progression")
    case.request.rolling_state["discussion"] = {"open_points": [{"id": "retry", "text": "超时是否重试？"}]}
    operations = state_payload()["state_changes"] + [
        {"type": "open_question", "operation": "resolve", "item_id": "retry", "content": "超时可以重试。",
         "evidence_segment_ids": ["value-response-2"], "evidence_quote": "超时可以重试。"},
        {"type": "open_question", "operation": "add", "item_id": "duplicate", "content": "如何防重复权益？",
         "evidence_segment_ids": ["value-response-2"], "evidence_quote": "如何防重复权益？"}]
    typed = {"state_changes": operations, "topic_update": None}
    good = tool.accepted_state_checks(case, typed)
    assert good["minimum_semantic_structure"] and good["prior_open_question_id_matches"] == ["retry"]
    operations[1]["item_id"] = "new-and-wrong-id"
    wrong = tool.accepted_state_checks(case, typed)
    assert wrong["retry_resolution_uses_latest_answer_evidence"]
    assert not wrong["retry_resolution_preserves_known_question_identity"]
    assert not wrong["minimum_semantic_structure"]
    operations[1]["item_id"] = "retry"
    operations[1]["evidence_segment_ids"] = ["older-answer"]
    assert not tool.accepted_state_checks(case, typed)["minimum_semantic_structure"]


@pytest.mark.parametrize("mutation", [None, "revision", "hash", "missing"])
def test_reconstructed_packets_read_only_verify_frozen_revision_and_official_hash(tmp_path, monkeypatch, mutation):
    from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash
    artifacts = tmp_path / "artifacts/realtime-participation-20261007"
    artifacts.mkdir(parents=True)
    database = tmp_path / "history.db"
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "official_state_prompt", lambda: "你是中文讨论工作记忆整理器。测试版")
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE jobs(id TEXT,meeting_id TEXT,status TEXT,request_context_json TEXT);"
            "CREATE TABLE transcript_segments(meeting_id TEXT,segment_id TEXT,transcript_seq INTEGER,revision INTEGER,text TEXT,normalized_text TEXT);")
        for case_id, report_name, report_case_id, job_id in (tool.CASES[0], tool.CASES[2]):
            meeting_id = "participation_global_fixture" if report_case_id is None else "participation_value_fixture"
            valid = {"valid_intervention": True, "own_decision_status": "intervention"}
            report = {"meeting_id": meeting_id, "automatic": valid} if report_case_id is None else {
                "cases": [{"case_id": report_case_id, "meeting_id": meeting_id, "requests": [valid]}]}
            (artifacts / report_name).write_text(json.dumps(report), encoding="utf-8")
            text = "PRIVATE_SOURCE_MARKER：暂定方案A，尚未拍板。"
            evidence_hash = transcript_evidence_hash("p1", text) if mutation != "hash" else hashlib.sha256(text.encode()).hexdigest()
            metadata = {"profile": "participation", "task_mode": "discussion_state", "anchor_seq": 1,
                "evidence": [{"segment_id": "p1", "revision": 1, "evidence_hash": evidence_hash}],
                "discussion_context": {"current_problem": "候选问题", "constraints": [], "open_points": []}}
            connection.execute("INSERT INTO jobs VALUES(?,?,?,?)", (job_id, meeting_id, "succeeded", json.dumps(metadata)))
            if mutation != "missing":
                connection.execute("INSERT INTO transcript_segments VALUES(?,?,?,?,?,?)",
                    (meeting_id, "p1", 1, 2 if mutation == "revision" else 1, text, text))
    original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    if mutation:
        with pytest.raises(ValueError, match="frozen_source"):
            tool.prepare_cases(database, 2)
    else:
        cases = tool.prepare_cases(database, 2)
        assert len(cases) == 2 and all(case.source_revision_hash_verified for case in cases)
        assert all(case.safe_plan()["source_revision_hash_verified"] for case in cases)
        assert "PRIVATE_SOURCE_MARKER" not in json.dumps([case.safe_plan() for case in cases])
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original_hash
