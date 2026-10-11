from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Thread

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("quote_postcheck_runner", ROOT / "tools/participation_quote_postcheck.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_dry_run_new_profile_does_not_execute_or_change_original_budgets(tmp_path, monkeypatch):
    import participation_continuous_acceptance as original
    monkeypatch.setattr(runner.support, "execute", lambda *_: pytest.fail("dry plan executed"))
    directory = tmp_path / "new"
    assert runner.main(["--run-dir", str(directory)]) == 0
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert plan["budget"] == {"host_jobs": 3, "state": 1, "automatic": 0, "manual": 2,
        "forwarded_upstream_posts": 5, "attempts_per_host_job": 1, "retries": 0}
    assert plan["profile"] == "participation_quote_postcheck.v1" and not plan["executed"]
    assert plan["inspection_seconds"] == 0 and plan["ui_signal_contract"]["signals_are_not_dom_ack"]
    assert plan["runtime_flags"]["discussion_state_enabled"] and not plan["runtime_flags"]["discussion_automatic_enabled"]
    assert not plan["semantic_fixture_applied"] and not plan["timer_gates_overridden"] and not plan["clock_advanced"]
    assert not (directory / "runtime").exists() and not (directory / "proxy-ledger.json").exists()
    assert original.HOST_LIMIT == 9 and original.UPSTREAM_LIMIT == 14
    assert original.LIMITS == {"discussion_state": 4, "automatic": 2, "manual": 3}
    assert runner.PUBLIC_PAYMENT_TEXT == original.INPUTS[1]
    with pytest.raises(FileExistsError):
        runner.main(["--run-dir", str(directory)])


def test_proxy_five_attempts_count_cancel_unknown_and_reject_reset(tmp_path, monkeypatch):
    ledger = tmp_path / "proxy-ledger.json"
    budget = runner.QuoteAttemptBudget(limit=5, report=ledger)
    for ordinal in range(1, 6):
        assert budget.reserve(lane="unknown", endpoint="/v1/responses") == ordinal
        budget.finish(ordinal, status="client_disconnected" if ordinal == 2 else "completed", http_status=None if ordinal == 2 else 200)
    assert budget.reserve(lane="unknown", endpoint="/v1/responses") is None
    snapshot = budget.snapshot()
    assert snapshot["reserved_forward_attempts"] == 5 and snapshot["remaining"] == 0 and snapshot["budget_rejections"] == 1
    assert snapshot["events"][1]["http_status"] is None
    before = ledger.read_bytes()
    monkeypatch.setattr(runner.support.proxy, "make_server", lambda **_: pytest.fail("reset old ledger"))
    with pytest.raises(ValueError, match="old budget cannot be reset"):
        runner.support.serve_proxy(tmp_path)
    assert ledger.read_bytes() == before
    with pytest.raises(ValueError):
        runner.QuoteAttemptBudget(limit=14)


def test_real_admission_one_state_two_manual_single_attempt_and_unknown_intent_rejected(tmp_path):
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence
    p = runner.guarded_persistence_type(V2Persistence)(tmp_path / "meeting.db", semantic_projection_mode="llm_first")
    mid = runner.support.SCOPE_PREFIX + "guard"
    try:
        p.create_meeting(meeting_id=mid, title=None, now_ms=1_000)
        text = runner.PUBLIC_PAYMENT_TEXT
        p.commit_final_and_enqueue(meeting_id=mid, final_id="f1", segment_id="s1", text=text, normalized_text=text,
            started_at_ms=1_000, ended_at_ms=1_800, evidence_hash="h1", now_ms=2_000, enqueue_intelligence=False)
        state = p.enqueue_discussion_state(meeting_id=mid, now_ms=3_000, cooldown_ms=0, stable_ms=0,
            minimum_segments=1, minimum_characters=0)
        assert state["max_attempts"] == 1
        before = p.list_jobs(meeting_id=mid)
        with pytest.raises(RuntimeError, match="explicit participation intent"):
            p.enqueue_user_coach_request(meeting_id=mid, user_request="legacy request", idempotency_key="unknown", now_ms=3_010)
        assert p.list_jobs(meeting_id=mid) == before
        for index, intent in enumerate(("ask_question", "formulate_response")):
            job = p.enqueue_user_coach_request(meeting_id=mid, user_request="公开工具预算验收", intent=intent,
                idempotency_key=f"manual{index}", now_ms=3_020 + index)
            assert job["max_attempts"] == 1 and runner.support.lane(job) == "manual"
        counts = runner.support.host_budget(p.list_jobs(meeting_id=mid))
        assert counts["discussion_state"] == 1 and counts["manual"] == 2 and counts["host_jobs"] == 3
        before = p.list_jobs(meeting_id=mid)
        with pytest.raises(RuntimeError, match="budget exhausted"):
            p.enqueue_user_coach_request(meeting_id=mid, user_request="no fourth", intent="ask_question",
                idempotency_key="manual-extra", now_ms=3_030)
        assert p.list_jobs(meeting_id=mid) == before and p.acceptance_guard_activations == 1
    finally:
        p.close()


def test_real_second_state_rollback_is_a_budget_failure(tmp_path):
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence
    p = runner.guarded_persistence_type(V2Persistence)(tmp_path / "meeting.db", semantic_projection_mode="llm_first")
    mid = runner.support.SCOPE_PREFIX + "state-guard"
    try:
        p.create_meeting(meeting_id=mid, title=None, now_ms=1_000)
        for seq in (1, 2):
            text = runner.PUBLIC_PAYMENT_TEXT + str(seq)
            p.commit_final_and_enqueue(meeting_id=mid, final_id=f"f{seq}", segment_id=f"s{seq}", text=text,
                normalized_text=text, started_at_ms=seq * 1_000, ended_at_ms=seq * 1_000 + 800,
                evidence_hash=f"h{seq}", now_ms=2_000 + seq, enqueue_intelligence=False)
            before = p.list_jobs(meeting_id=mid)
            current_before = p.get_current_discussion(mid)
            job = p.enqueue_discussion_state(meeting_id=mid, now_ms=3_000 + seq, cooldown_ms=0, stable_ms=0,
                minimum_segments=1, minimum_characters=0)
            if seq == 1:
                claimed = p.claim_next_job(worker_id="test", lane="discussion_state", now_ms=3_001, lease_ms=5_000)
                assert claimed["id"] == job["id"]
                assert p.complete_job(job_id=job["id"], worker_id="test", now_ms=3_010, output={})
            else:
                assert job is None and p.acceptance_guard_activations == 1
                assert p.list_jobs(meeting_id=mid) == before and p.get_current_discussion(mid) == current_before
    finally:
        p.close()


def _close_app(app):
    app.state.v2_persistence.close()
    app.state.pi_coach_runtime.close()


def test_real_http_mutable_boundary_rejects_legacy_body_and_other_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "public-test-token")
    app = runner.build_app(tmp_path)
    try:
        client = TestClient(app)  # No lifespan: no worker/model or ASR startup.
        mid = runner.support.SCOPE_PREFIX + "http"
        assert client.post("/v2/meetings", json={"meeting_id": "outside"}).status_code == 403
        assert client.post("/v2/meetings", json={"meeting_id": mid}).status_code == 201
        assert client.post("/v2/meetings", json={"meeting_id": runner.support.SCOPE_PREFIX + "second"}).status_code == 403
        assert client.put(f"/v2/meetings/{mid}/preparation", json={"live_analysis_profile": "participation",
            "proactive_suggestion_policy": "off"}).status_code == 200
        for body in ({"request": "legacy missing intent"}, {"intent": "freeform", "request": "other"}):
            assert client.post(f"/v2/meetings/{mid}/coach/request", json=body).status_code == 403
        assert app.state.v2_persistence.list_jobs(meeting_id=mid) == []
        assert client.post(f"/v2/meetings/{mid}/minutes").status_code == 403
        assert client.post("/providers/config", json={}).status_code == 403
        assert client.post("/asr-live/sessions", json={}).status_code == 403
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(f"/asr-live/sessions/{mid}/audio"):
                pytest.fail("audio WebSocket accepted")
        assert caught.value.code == 1008
        assert client.post("/v2/traces/unknown/ui-rendered", json={"meeting_id": "other"}).status_code == 403
    finally:
        _close_app(app)


def test_actual_flat_proxy_and_native_provider_get_preflight_with_five_new_posts(tmp_path, monkeypatch):
    budget = runner.QuoteAttemptBudget(limit=5)
    server = runner.support.proxy.make_server(port=0, upstream_base="https://synthetic.example", upstream_key="public-test-key",
        proxy_token="public-test-token", budget=budget)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(runner.support, "PROXY_PORT", server.server_port)
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "public-test-token")
    app = runner.build_app(tmp_path)
    pid = os.getpid()
    runner.support.fresh_json(tmp_path / "proxy-identity.json", {"schema_version": runner.support.BOUNDARY_SCHEMA,
        "role": "proxy", "port": server.server_port, "pid": pid,
        "process_start_marker": runner.support.workbench_server.process_start_marker(pid),
        "ledger": str(tmp_path / "proxy-ledger.json"), "maximum_forward_attempts": 5})
    actual_owners = runner.support.listener_owners
    monkeypatch.setattr(runner.support, "listener_owners", lambda port: {pid} if port == runner.support.APP_PORT else actual_owners(port))
    try:
        client = TestClient(app)
        assert client.get("/providers/config").json()["base_url"] == f"http://127.0.0.1:{server.server_port}"
        result = runner.preflight(tmp_path, client)
        assert all(result["checks"].values()) and result["identity"]["automatic_enabled"] is False
        assert result["proxy_budget"]["maximum_forward_attempts"] == 5 and "budget" not in result["proxy_budget"]
        assert budget.snapshot()["reserved_forward_attempts"] == 0
    finally:
        _close_app(app)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("signal_case", ["matched", "foreign", "stale_nonce", "bad_json", "over_limit"])
def test_local_signals_are_bound_and_have_bounded_wait_without_fixed_sleep(tmp_path, signal_case):
    expected = {"meeting_id": "new", "stage": "manual1", "signal": "inspected", "nonce": "current"}
    payload = dict(expected)
    if signal_case == "foreign":
        payload["meeting_id"] = "old"
    elif signal_case == "stale_nonce":
        payload["nonce"] = "old"
    path = tmp_path / "signal.json"
    path.write_text("{bad" if signal_case == "bad_json" else "x" * 1025 if signal_case == "over_limit" else json.dumps(payload))
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    result = runner.wait_local_signal(path, expected, max_seconds=.6, tick=lambda: None,
        monotonic=lambda: clock[0], sleep=sleep)
    assert result["matched"] is (signal_case == "matched")
    assert result["elapsed_ms"] == (0 if signal_case == "matched" else 600)
    assert not sleeps if signal_case == "matched" else max(sleeps) <= .2


def test_budget_failure_wins_over_root_local_signal(tmp_path):
    expected = {"meeting_id": "new", "stage": "manual1", "signal": "inspected", "nonce": "current"}
    path = tmp_path / "signal.json"
    path.write_text(json.dumps(expected))

    def tick():
        raise runner.support.AcceptanceStop("budget_boundary_activated", "public bounded stop")
    with pytest.raises(runner.support.AcceptanceStop):
        runner.wait_local_signal(path, expected, max_seconds=20, tick=tick)


def _request_fixture(stage="manual1"):
    text = runner.support.MATERIAL_V1 if stage == "manual1" else runner.support.MATERIAL_V2
    fact = "禁止直接提示支付失败。" if stage == "manual1" else "必须由用户重新确认付款。"
    receipt = {"meeting_id": "meeting", "job_id": "job", "decision_id": "decision", "event_seq": 14, "input_version": 1, "draft_seq": 0}
    source = {"source_id": "attachment-source", "source_type": "meeting_attachment", "version_id": "version",
        "quote": text}
    card = {"job_id": "job", "decision_id": "decision", "analysis_kind": "participation", "proposal_type": "professional_proposal",
        "topic_id": "discussion", "analysis_basis_revision": 2,
        "recommendation": "公开提议", "supporting_facts": [{"text": fact, "source_ref": "attachment-source"}],
        "source_evidence": [source], "ui_render_receipt": receipt, "valid_until_ms": 90_000,
        "material_context": {"attachment_catalog": [{"coverage": {"provided_characters": len(text)}}]}}
    decision = {"job_id": "job", "decision_id": "decision", "status": "intervention", "pi_provider_attempted": True}
    metrics = {"provider_attempt_count": 1, "provider_attempted": True, "turns": 1, "tool_calls": 1,
        "tool_names": ["submit_intervention"], "tool_errors": [], "context_reads": 0}
    job = {"id": "job", "meeting_id": "meeting", "input_version": 1, "status": "succeeded",
        "request_context": {"intent": "ask_question" if stage == "manual1" else "formulate_response",
            "topic_id": "discussion", "analysis_basis_revision": 2},
        "output": {"applied": {"coach_decision": decision, "coach_intervention": card},
            "coach": {"agent_metrics": metrics}, "provider_attempt_count": 1}}
    return job, {"coach_intervention": card, "coach_decision": decision,
        "discussion": {"work_item_id": "discussion", "context": {"analysis_basis_revision": 2}}}, text


@pytest.mark.parametrize("case", ["valid", "other_job", "other_receipt", "wrong_version", "expired", "bad_quote", "repair", "old_basis"])
def test_request_requires_own_current_exact_facts_and_real_receipt_identity(case):
    job, snapshot, text = _request_fixture()
    card = snapshot["coach_intervention"]
    if case == "other_job":
        card["job_id"] = "old-job"
    elif case == "other_receipt":
        card["ui_render_receipt"]["decision_id"] = "old-decision"
    elif case == "wrong_version":
        card["ui_render_receipt"]["input_version"] = 2
    elif case == "expired":
        card["valid_until_ms"] = 1
    elif case == "bad_quote":
        card["source_evidence"][0]["quote"] = "unsupported source"
    elif case == "repair":
        job["output"]["coach"]["agent_metrics"]["tool_errors"] = [{"tool": "submit_intervention", "code": "document_quote_mismatch"}]
    elif case == "old_basis":
        snapshot["discussion"]["context"]["analysis_basis_revision"] = 3
    checks, details = runner.request_checks("manual1", job, snapshot, material_version="version", material_text=text,
        public_texts={}, observed_at_ms=1_000)
    assert all(checks.values()) is (case in {"valid", "repair"})
    assert details["quote_efficiency"]["no_main_quote_repair"] is (case != "repair")
    assert details["material_context"] == card["material_context"]


def test_manual2_requires_visible_fact_unique_to_v2_and_does_not_infer_dom_from_signal():
    job, snapshot, text = _request_fixture("manual2")
    checks, _ = runner.request_checks("manual2", job, snapshot, material_version="version", material_text=text,
        public_texts={}, observed_at_ms=1_000)
    assert all(checks.values()) and checks["manual2_visible_fact_unique_to_v2"]
    assert runner.dom_receipt_observation(snapshot["coach_intervention"], [])["exact_six_field_dom_ack"] == "unknown"


@pytest.mark.parametrize("case", ["valid", "other_card", "other_meeting", "bad_provenance", "bad_clock"])
def test_dom_receipt_requires_six_fields_card_owner_and_formal_provenance(case):
    _, snapshot, _ = _request_fixture()
    card = snapshot["coach_intervention"]
    receipt = dict(card["ui_render_receipt"])
    trace = {"job_id": "job", "meeting_id": "meeting", "stages": {"ui_rendered": {"monotonic_ns": 100,
        "attributes": receipt}}, "execution": {"provenance": {"ui_render_ack": {"status": "observed",
            "at_monotonic_ns": 100, "attributes": {"source": "formal_coach_ui.v1", "event_seq": 14, "input_version": 1}}}}}
    if case == "other_card":
        card["decision_id"] = "another"
    elif case == "other_meeting":
        trace["meeting_id"] = "another"
    elif case == "bad_provenance":
        trace["execution"]["provenance"]["ui_render_ack"]["attributes"]["source"] = "legacy"
    elif case == "bad_clock":
        trace["execution"]["provenance"]["ui_render_ack"]["at_monotonic_ns"] = 101
    outcome = runner.dom_receipt_observation(card, [trace])
    assert outcome["exact_six_field_dom_ack"] == (True if case == "valid" else "unknown")


def test_fixed_tool_diagnostics_preserve_real_counts_but_omit_unknown_raw():
    job, _, _ = _request_fixture()
    metrics = job["output"]["coach"]["agent_metrics"]
    metrics.update(turns=2, tool_calls=3, context_reads=1,
        tool_names=["read_realtime_context", "private tool raw", "submit_intervention"],
        tool_errors=[{"tool": "private tool raw", "code": "private refused quote"}])
    result = runner.tool_diagnostics(job)
    assert result["turns"] == 2 and result["tool_calls"] == 3 and result["context_reads"] == 1
    assert result["tool_names"] == ["read_realtime_context", "unclassified", "submit_intervention"]
    assert "private" not in json.dumps(result)


def test_failure_dispatch_is_not_retried_or_saved_as_raw(tmp_path, monkeypatch):
    class Client:
        def close(self):
            pass

    class FailingObserver:
        live = store = None
        mid = ""
        client = Client()
        calls = 0

        def __init__(self, *_):
            pass

        def run(self):
            FailingObserver.calls += 1
            raise runner.support.AcceptanceStop("own_job_failed", "refused private body must not be saved")
    monkeypatch.setattr(runner.support, "ContinuousObserver", FailingObserver)
    path = tmp_path / "report.json"
    report = runner.plan(tmp_path)
    runner.support.fresh_json(path, report)
    assert runner.support.execute(tmp_path, path, report) == 1
    assert FailingObserver.calls == 1
    saved = path.read_text(encoding="utf-8")
    assert "private body" not in saved and json.loads(saved)["stop_diagnostic"]["code"] == "own_job_failed"
