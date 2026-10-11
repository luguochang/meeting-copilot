from __future__ import annotations

import ast
from contextlib import contextmanager
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import sys
from threading import RLock, Thread

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("continuous_acceptance", ROOT / "tools/participation_continuous_acceptance.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_default_plan_has_no_runtime_or_network_and_is_not_overwritten(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("plan attempted execution/network")
    monkeypatch.setattr(runner, "execute", forbidden)
    monkeypatch.setattr(runner.httpx, "Client", forbidden)
    run_dir = tmp_path / "fresh"
    assert runner.main(["--run-dir", str(run_dir)]) == 0
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    assert not plan["executed"] and not plan["audio_capture"] and not plan["semantic_fixture_applied"]
    assert not plan["timer_gates_overridden"] and not plan["clock_advanced"] and not plan["ttl_shortened"]
    assert plan["budget"] == {"host_jobs": 9, "state": 4, "automatic": 2, "manual": 3,
        "forwarded_upstream_posts": 14, "attempts_per_host_job": 1, "retries": 0}
    assert all(item["input_characters"] >= 120 for item in plan["stages"] if "input_characters" in item)
    assert not (run_dir / "runtime").exists() and not (run_dir / "proxy-ledger.json").exists()
    original = (run_dir / "plan.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner.main(["--run-dir", str(run_dir)])
    assert (run_dir / "plan.json").read_bytes() == original


def test_new_proxy_fourteen_reservations_include_failed_or_unfinished_upstream(tmp_path):
    ledger = tmp_path / "new-ledger.json"
    budget = runner.ContinuousAttemptBudget(limit=14, report=ledger)
    for number in range(1, 15):
        assert budget.reserve(lane="unknown", endpoint="/v1/responses") == number
        if number != 14:
            budget.finish(number, status="client_disconnected" if number == 2 else "upstream_error", http_status=None)
    assert budget.reserve(lane="state", endpoint="/v1/responses") is None
    data = json.loads(ledger.read_text())
    assert data["reserved_forward_attempts"] == 14 and data["budget_rejections"] == 1
    assert data["events"][-1]["status"] == "reserved" and data["upstream_retries"] == 0
    assert not data["headers_or_bodies_logged"]
    with pytest.raises(ValueError):
        runner.proxy.AttemptBudget(limit=14)
    with pytest.raises(ValueError):
        runner.ContinuousAttemptBudget(limit=True)


def test_old_proxy_ledger_is_rejected_before_server_or_budget_reset(tmp_path, monkeypatch):
    ledger = tmp_path / "proxy-ledger.json"
    ledger.write_text('{"reserved_forward_attempts":12}', encoding="utf-8")
    monkeypatch.setattr(runner.proxy, "make_server", lambda **_kwargs: pytest.fail("reused a consumed proxy budget"))
    with pytest.raises(ValueError, match="cannot be reset"):
        runner.serve_proxy(tmp_path)
    assert json.loads(ledger.read_text())["reserved_forward_attempts"] == 12


def _job(name, kind, *, attempts=1, status="succeeded"):
    return {"id": name, "kind": "intelligence", "status": status, "attempts": attempts,
        "trigger_type": "user_request" if kind == "manual" else "discussion_update",
        "request_context": {"profile": "participation", **({"task_mode": "discussion_state"} if kind == "discussion_state" else {})}}


@pytest.mark.parametrize("case", ["owned_response", "closed_only", "new_topic_only", "unknown_question", "old_quote", "wrong_revision", "still_open", "copied_new_topic"])
def test_response_check_requires_current_evidence_on_the_existing_question_owner(case):
    before = {"discussion": {"work_item_id": "old-topic", "context": {"open_points": [
        {"id": "known-question", "entity_type": "open_question"}]}}}
    response = {"id": "known-question", "entity_type": "open_question", "source_refs": ["current-response"],
        "evidence": [{"segment_id": "current-response", "revision": 1, "evidence_hash": "current-hash"}]}
    owner = {"work_item_id": "old-topic", "state": "superseded", "context": {"phase": "closed", "open_points": [], "responded_points": [response]}}
    current = {"work_item_id": "new-topic", "context": {"open_points": [], "responded_points": []}}
    if case == "closed_only":
        owner["context"]["responded_points"] = []
    elif case == "new_topic_only":
        owner["context"]["responded_points"] = []
        current["context"]["responded_points"] = [response]
    elif case == "unknown_question":
        response["id"] = "not-previously-known"
    elif case == "old_quote":
        response["evidence"][0]["evidence_hash"] = "obsolete-hash"
    elif case == "wrong_revision":
        response["evidence"][0]["revision"] = 2
    elif case == "still_open":
        owner["context"]["open_points"] = [{"id": "known-question"}]
    elif case == "copied_new_topic":
        current["context"]["responded_points"] = [response]
    after = {"discussion": current, "all_topic_work_items": [current, owner]}
    result = runner.responded_point_owner_check(before, after, {"segment_id": "current-response", "seed": {"source_hash": "current-hash"}})
    assert result["passed"] is (case == "owned_response")
    assert result["response_owner_topic_id"] == "old-topic" and result["current_topic_id"] == "new-topic"


def test_host_ceiling_counts_cancelled_and_pending_admissions_and_retries():
    jobs = [_job(f"state{i}", "discussion_state") for i in range(4)]
    jobs += [_job("auto1", "automatic"), _job("auto2", "automatic", status="cancelled")]
    jobs += [_job(f"manual{i}", "manual", attempts=0, status="pending") for i in range(3)]
    assert runner.host_budget(jobs)["host_jobs"] == 9
    assert not runner.over_budget(runner.host_budget(jobs))
    assert runner.over_budget(runner.host_budget(jobs + [_job("extra", "automatic", attempts=0)]))
    jobs[0]["attempts"] = 2
    assert runner.over_budget(runner.host_budget(jobs))
    seed = {"id": "seed", "kind": "correction", "status": "cancelled", "attempts": 0}
    assert runner.host_budget([seed])["host_jobs"] == 0
    seed["attempts"] = 1
    assert runner.over_budget(runner.host_budget([seed]))


def test_real_manual_admission_wrapper_is_atomic_idempotent_and_rejects_fourth(tmp_path):
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
    persistence = runner.guarded_persistence_type(V2Persistence)(tmp_path / "meeting_copilot.db", semantic_projection_mode="llm_first")
    mid = runner.SCOPE_PREFIX + "guard_test"
    persistence.create_meeting(meeting_id=mid, title=None, now_ms=1000)
    live = SqliteAsrLiveSessionRepository(tmp_path)
    runner.value.create_synthetic_live_record(live, mid)
    runner.value.seed_synthetic_final(persistence, live, mid, {"segment_id": "s1", "text": runner.INPUTS[0]}, 1)
    requests = {"meeting_id": mid, "user_request": "", "intent": "compare_options", "now_ms": 2000}
    try:
        accepted = [persistence.enqueue_user_coach_request(**requests, idempotency_key=f"manual:{i}") for i in range(3)]
        assert all(job["max_attempts"] == 1 and job["request_context"]["profile"] == "participation" for job in accepted)
        before = persistence.list_jobs(meeting_id=mid)
        topic = persistence.get_current_discussion(mid)
        with pytest.raises(RuntimeError, match="budget exhausted"):
            persistence.enqueue_user_coach_request(**requests, idempotency_key="manual:4")
        assert persistence.list_jobs(meeting_id=mid) == before
        assert persistence.get_current_discussion(mid) == topic
        assert persistence.acceptance_guard_activations == 1
        assert persistence.enqueue_user_coach_request(**requests, idempotency_key="manual:2")["id"] == accepted[2]["id"]
        assert len([job for job in persistence.list_jobs(meeting_id=mid) if runner.lane(job)]) == 3
    finally:
        live.close()
        persistence.close()


def test_existing_scope_preflight_stops_before_any_http_or_mutable_repository(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = runtime / "meeting_copilot.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE meetings(id TEXT PRIMARY KEY,state TEXT)")
        conn.execute("CREATE TABLE llm_usage_ledger(id TEXT)")
        conn.execute("INSERT INTO meetings VALUES('existing-user-scope','live')")
    class ForbiddenClient:
        def get(self, *_args, **_kwargs):
            pytest.fail("HTTP before empty-runtime read-only guard")
    with pytest.raises(RuntimeError, match="existing scopes untouched"):
        runner.preflight(tmp_path, ForbiddenClient())
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT id,state FROM meetings").fetchall() == [("existing-user-scope", "live")]


@pytest.mark.parametrize("attempt", [None, {},
    {"started_at_monotonic_ns": 10, "completed_at_monotonic_ns": 11, "outcome": "succeeded"},
    {"started_at_monotonic_ns": True, "completed_at_monotonic_ns": None, "outcome": None}])
def test_running_without_observed_unfinished_provider_is_not_overlap(attempt):
    trace = None if attempt is None else {"execution": {"provider_attempts": [attempt]}}
    assert not runner.in_flight(trace)


def test_actual_provider_open_trace_is_overlap_and_own_timeout_does_not_borrow_old_card():
    assert runner.in_flight({"execution": {"provider_attempts": [
        {"started_at_monotonic_ns": 10, "completed_at_monotonic_ns": None, "outcome": None}]}})
    timed_out = {**_job("new", "manual"), "output": {"applied": {"coach_decision": {"status": "timed_out"}}}}
    assert runner.own_failure(timed_out)
    outcome = runner.value.own_job_outcome(timed_out, {"coach_intervention": {"job_id": "old", "decision_id": "old"}})
    assert not outcome["valid_intervention"] and not outcome["current_card_for_request"]


def test_missed_second_overlap_stops_without_extra_manual_or_retry(tmp_path, monkeypatch):
    observer = runner.ContinuousObserver(tmp_path, tmp_path / "report.json", {})
    job = {**_job("automatic2", "automatic"), "created_at_ms": 2000, "output": {"applied": {"coach_decision": {"status": "protected_silent"}}}}
    monkeypatch.setattr(observer, "collect", lambda: ([{**job, "id": "automatic1", "created_at_ms": 1000}, job], {"events": []}))
    monkeypatch.setattr(observer, "http", lambda *_args, **_kwargs: {"traces": []})
    try:
        with pytest.raises(RuntimeError, match="overlap was observed"):
            observer.wait_job("automatic", 2, inflight=True)
        assert observer.report["preemption_coverage"] == "uncovered_overlap_missed"
    finally:
        observer.client.close()


def test_typed_history_must_keep_same_identity_facts_and_frozen_material_context():
    card = {"job_id": "j", "decision_id": "d", "say_this": "建议查询待确认", "analysis_kind": "participation",
        "proposal_type": "professional_proposal", "topic_id": "topic", "analysis_basis_revision": 2,
        "supporting_facts": [{"text": "禁止直接提示支付失败", "source_ref": "v1-span"}], "assumptions": [],
        "material_context": {"material_context_revision": 2}}
    assert runner.history_contains({"coach_history": [card], "coach_intervention": None}, card)
    assert not runner.history_contains({"coach_history": [{**card, "job_id": "another"}]}, card)
    assert not runner.history_contains({"coach_history": [{**card, "material_context": {"material_context_revision": 3}}]}, card)
    assert not runner.history_contains({"coach_history": [{**card, "supporting_facts": []}]}, card)


def test_observer_never_admits_semantic_or_automatic_directly_and_policy_off_follows_overlap():
    source = inspect.getsource(runner.ContinuousObserver)
    tree = ast.parse(source)
    forbidden = {"enqueue_discussion_state", "enqueue_discussion_update", "_refresh_due_coach_items",
        "_run_v2_discussion_state_job", "_run_v2_intelligence_job", "advance_time", "set_clock"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
            assert name not in forbidden
    run_source = inspect.getsource(runner.ContinuousObserver.run)
    assert run_source.index('self.wait_job("automatic", 2, inflight=True)') < run_source.index('"proactive_suggestion_policy": "off"')
    assert run_source.index('self.wait_job("manual", 1)') < run_source.index('"proactive_suggestion_policy": "off"')
    assert run_source.count("self.manual(") == 3


def test_scope_close_cannot_touch_an_old_or_different_scope(tmp_path):
    store = runner.value.open_synthetic_store(tmp_path / "meeting_copilot.db")
    store.create_meeting(meeting_id="old", title=None, now_ms=1000)
    mid = runner.SCOPE_PREFIX + "cleanup"
    store.create_meeting(meeting_id=mid, title=None, now_ms=1000)
    try:
        with pytest.raises(ValueError):
            runner.close_scope(store, "old")
        runner.close_scope(store, mid)
        assert store.get_meeting("old")["state"] == "live"
        assert store.get_meeting(mid)["state"] == "ended"
    finally:
        store.close()


def test_failed_preflight_record_does_not_contain_exception_text_or_secrets(tmp_path, monkeypatch):
    def forbidden(*_args):
        raise RuntimeError("private-token/failed-provider-body")
    monkeypatch.setattr(runner, "preflight", forbidden)
    report = runner.plan(tmp_path)
    path = tmp_path / "report.json"
    runner.fresh_json(path, report)
    assert runner.execute(tmp_path, path, report) == 1
    encoded = path.read_text(encoding="utf-8")
    assert "private-token" not in encoded and "failed-provider-body" not in encoded
    assert report["error_class"] == "RuntimeError"


def test_nonempty_app_runtime_rejected_before_module_import_or_environment_change(tmp_path, monkeypatch):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/existing").write_text("preserve", encoding="utf-8")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "sentinel")
    with pytest.raises(ValueError, match="fresh empty runtime"):
        runner.build_app(tmp_path)
    assert runner.os.environ["LLM_GATEWAY_MODEL"] == "sentinel"


def test_atomic_guard_all_three_lanes_share_nine_and_roll_back_excess_event():
    class SqliteAdmissionBase:
        def __init__(self):
            self._lock = RLock()
            self._conn = sqlite3.connect(":memory:")
            self._conn.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,meeting_id TEXT,kind TEXT,trigger_type TEXT,request_context_json TEXT,max_attempts INT)")
            self._conn.execute("CREATE TABLE publications(job_id TEXT)")

        @contextmanager
        def _write_transaction(self):
            with self._conn:
                yield

        def list_jobs(self, *, meeting_id):
            return [{"id": row[0], "kind": row[1], "trigger_type": row[2], "request_context": json.loads(row[3]), "attempts": 0}
                for row in self._conn.execute("SELECT id,kind,trigger_type,request_context_json FROM jobs WHERE meeting_id=?", (meeting_id,))]

        def get_job(self, job_id):
            return next(job for job in self.list_jobs(meeting_id=mid) if job["id"] == job_id)

        def _insert(self, meeting_id, idempotency_key, lane):
            if self._conn.execute("SELECT 1 FROM jobs WHERE id=?", (idempotency_key,)).fetchone():
                return self.get_job(idempotency_key)
            metadata = {"profile": "participation", **({"task_mode": "discussion_state"} if lane == "discussion_state" else {})}
            self._conn.execute("INSERT INTO jobs VALUES(?,?,'intelligence',?,?,3)",
                (idempotency_key, meeting_id, "user_request" if lane == "manual" else "discussion_update", json.dumps(metadata)))
            self._conn.execute("INSERT INTO publications VALUES(?)", (idempotency_key,))
            return self.get_job(idempotency_key)

        def enqueue_discussion_state(self, **kwargs):
            return self._insert(**kwargs, lane="discussion_state")

        def enqueue_discussion_update(self, **kwargs):
            return self._insert(**kwargs, lane="automatic")

        def enqueue_user_coach_request(self, **kwargs):
            return self._insert(**kwargs, lane="manual")

    store = runner.guarded_persistence_type(SqliteAdmissionBase)()
    mid = runner.SCOPE_PREFIX + "atomic"
    try:
        for mode, count in (("discussion_state", 4), ("discussion_update", 2), ("user_coach_request", 3)):
            for number in range(count):
                assert getattr(store, "enqueue_" + mode)(meeting_id=mid, idempotency_key=f"{mode}{number}")
        assert runner.host_budget(store.list_jobs(meeting_id=mid))["host_jobs"] == 9
        assert store._conn.execute("SELECT DISTINCT max_attempts FROM jobs").fetchall() == [(1,)]
        assert store.enqueue_discussion_state(meeting_id=mid, idempotency_key="state10") is None
        assert store.acceptance_guard_activations == 1
        assert store._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 9
        assert store._conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 9
        assert store.enqueue_discussion_state(meeting_id=mid, idempotency_key="discussion_state0")["id"] == "discussion_state0"
        assert store.acceptance_guard_activations == 1
    finally:
        store._conn.close()


def test_real_http_isolated_app_identity_preparation_and_no_audio_no_provider(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    # TestClient is intentionally not entered: no lifespan worker/model runs.
    # Preserve all process env/config globals around test-only app construction.
    monkeypatch.setattr(runner.os, "environ", dict(runner.os.environ))
    runner.os.environ["MEETING_COPILOT_DATA_DIR"] = str(tmp_path / "import-only")
    runner.os.environ["MEETING_CONTINUOUS_PROXY_TOKEN"] = "test-only-loopback-token"
    from meeting_copilot_web_mvp import app as app_module
    monkeypatch.setattr(app_module.llm_service, "REPO_ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(app_module.llm_service, "_RUNTIME_CONFIG_PAYLOAD", None)
    application = runner.build_app(tmp_path)
    persistence = application.state.v2_persistence
    client = TestClient(application)
    try:
        identity = client.get("/__acceptance/identity").json()
        assert identity["instrumented_test_wrapper"] and identity["guard_activations"] == 0
        assert identity["data_dir"] == str((tmp_path / "runtime").resolve())
        mid = runner.SCOPE_PREFIX + "http"
        assert client.post("/v2/meetings", json={"meeting_id": mid}).status_code == 201
        prep = client.get(f"/v2/meetings/{mid}/preparation").json()
        changed = client.put(f"/v2/meetings/{mid}/preparation", json={**prep,
            "live_analysis_profile": "participation", "proactive_suggestion_policy": "low_frequency"})
        assert changed.status_code == 200 and changed.json()["live_analysis_profile"] == "participation"
        assert client.put("/providers/config", json={}).status_code == 403
        assert client.post(f"/v2/meetings/{mid}/recordings", json={}).status_code == 403
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/asr-live"):
                pytest.fail("isolated fixture allowed an audio websocket")
        assert closed.value.code == 1008
        assert persistence.list_jobs(meeting_id=mid) == []
        with persistence._lock:
            assert persistence._conn.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0] == 0
    finally:
        client.close()
        persistence.close()


def test_real_flat_proxy_http_contract_and_preflight_zero_model_then_consumed_budget_rejected(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(runner.os, "environ", dict(runner.os.environ))
    runner.os.environ["MEETING_COPILOT_DATA_DIR"] = str(tmp_path / "import-only")
    runner.os.environ["MEETING_CONTINUOUS_PROXY_TOKEN"] = "test-only-loopback-token"
    from meeting_copilot_web_mvp import app as app_module
    monkeypatch.setattr(app_module.llm_service, "REPO_ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(app_module.llm_service, "_RUNTIME_CONFIG_PAYLOAD", None)
    budget = runner.ContinuousAttemptBudget(limit=14, report=tmp_path / "proxy-ledger.json")
    server = runner.proxy.make_server(port=0, upstream_base="https://unused.invalid/v1",
        upstream_key="no-upstream-call-test", proxy_token="local-test-only", budget=budget)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    # Actual proxy GET over ephemeral TCP + actual build_app/FastAPI GETs,
    # including canonical provider URL normalization. No lifespan/model calls.
    # Both listener checks use this test's ephemeral proxy listener; this is
    # explicitly not a two-process deployed-app ownership acceptance.
    monkeypatch.setattr(runner, "APP_PORT", server.server_port)
    monkeypatch.setattr(runner, "PROXY_PORT", server.server_port)
    pid = runner.os.getpid()
    marker = runner.workbench_server.process_start_marker(pid)
    application = runner.build_app(tmp_path)
    persistence = application.state.v2_persistence
    client = TestClient(application)
    runtime = tmp_path / "runtime"
    proxy_identity = {"schema_version": runner.BOUNDARY_SCHEMA, "role": "proxy", "port": server.server_port,
        "pid": pid, "process_start_marker": marker, "ledger": str(tmp_path / "proxy-ledger.json")}
    runner.fresh_json(tmp_path / "proxy-identity.json", proxy_identity)
    try:
        provider = client.get("/providers/config").json()
        assert provider["base_url"] == f"http://127.0.0.1:{server.server_port}"
        result = runner.preflight(tmp_path, client)
        assert "budget" not in result["proxy_budget"]  # Real make_server is flat.
        assert result["proxy_budget"]["maximum_forward_attempts"] == 14
        assert result["proxy_budget"]["reserved_forward_attempts"] == 0
        assert result["schema_version"] == 26 and result["empty_runtime"]
        assert all(result["checks"].values())
        assert runner.listener_owners(server.server_port) == {pid}
        budget.reserve(lane="unknown", endpoint="/v1/responses")  # No upstream HTTP; simulate spent slot.
        with pytest.raises(runner.AcceptanceStop, match="boundary identity/config/budget mismatch") as rejected:
            runner.preflight(tmp_path, client)
        assert rejected.value.code == "preflight_boundary_mismatch" and rejected.value.checks["fresh_budget"] is False
        with sqlite3.connect(runtime / "meeting_copilot.db") as conn:
            assert conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0] == 0
    finally:
        client.close()
        persistence.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_classified_stop_preserves_safe_checks_not_exception_text(tmp_path, monkeypatch):
    def stop(*_args):
        raise runner.AcceptanceStop("preflight_boundary_mismatch", "private-token/provider-body", checks={
            "provider_config": False, "fresh_budget": True, "private-token/quote": False})
    monkeypatch.setattr(runner, "preflight", stop)
    report = runner.plan(tmp_path)
    path = tmp_path / "report.json"
    runner.fresh_json(path, report)
    assert runner.execute(tmp_path, path, report) == 1
    assert report["stop_diagnostic"] == {"code": "preflight_boundary_mismatch",
        "checks": {"provider_config": False, "fresh_budget": True}, "http_status": None}
    assert "private-token" not in path.read_text(encoding="utf-8")
    assert "provider-body" not in path.read_text(encoding="utf-8")
