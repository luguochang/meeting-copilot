from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Thread

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("single_basis_postcheck_runner", ROOT / "tools/participation_quote_single_basis_postcheck.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_new_dry_profile_is_isolated_and_old_tools_and_ledgers_are_unchanged(tmp_path, monkeypatch):
    import participation_continuous_acceptance as original
    import participation_quote_postcheck as old_quote
    frozen = {"tools/participation_quote_postcheck.py": "69655d725153b26bf3774ffe930db9174cae7ad858beededcc50309577ff4464",
        "artifacts/realtime-participation-20261007/quote-postcheck-r01/report.json": "ae41f30c48a4956272456c45bd45ae172438e3806c7d3189e61f6d9843ef38fd"}
    # The original run artifact is optional in a clean clone; if present it is
    # immutable. Source freeze checks always apply to this repository stage.
    before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in frozen if (ROOT / name).exists()}
    monkeypatch.setattr(runner.support, "execute", lambda *_: pytest.fail("dry-run dispatched models"))
    directory = tmp_path / "new"
    assert runner.main(["--run-dir", str(directory)]) == 0
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert plan["profile"] == "participation_quote_single_basis.v1" and not plan["executed"]
    assert plan["budget"] == {"host_jobs": 3, "state": 1, "automatic": 0, "manual": 2,
        "forwarded_upstream_posts": 5, "attempts_per_host_job": 1, "retries": 0}
    assert plan["ui_signal_contract"]["bootstrap_page_ready_seconds"] == 60
    assert plan["ui_signal_contract"]["post_publication_max_seconds"] == 60
    assert plan["ui_signal_contract"]["manual_page_ready_handshakes"] == 0 and not plan["material_version_change_tested"]
    assert plan["cumulative_scope_budget"]["combined_maximum_posts"] == 6
    assert not (directory / "runtime").exists() and not (directory / "proxy-ledger.json").exists()
    assert original.HOST_LIMIT == 9 and original.UPSTREAM_LIMIT == 14
    assert old_quote.PROFILE == "participation_quote_postcheck.v1" and "bootstrap" not in old_quote.STAGES
    assert old_quote.support.SCOPE_PREFIX == "participation_quote_postcheck_"
    assert {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in before} == before
    assert before == {name: frozen[name] for name in before}
    with pytest.raises(FileExistsError):
        runner.main(["--run-dir", str(directory)])


def _store(directory):
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence
    directory.mkdir(parents=True, exist_ok=True)
    return runner.base.guarded_persistence_type(V2Persistence)(directory / "meeting_copilot.db", semantic_projection_mode="llm_first")


def _actual_state_projection(p, mid):
    from discussion_attention_fixtures import state_response
    from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash
    text = runner.base.PUBLIC_PAYMENT_TEXT
    source_hash = transcript_evidence_hash("s1", text)
    p.create_meeting(meeting_id=mid, title=None, now_ms=1_000)
    p.commit_final_and_enqueue(meeting_id=mid, final_id="f1", segment_id="s1", text=text, normalized_text=text,
        started_at_ms=1_000, ended_at_ms=1_800, evidence_hash=source_hash, now_ms=2_000, enqueue_intelligence=False)
    # Formal defaults are retained; only the test clock is controlled.
    state = p.enqueue_discussion_state(meeting_id=mid, now_ms=5_000)
    assert state is not None and state["max_attempts"] == 1
    claimed = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=5_000, lease_ms=10_000)
    assert claimed["id"] == state["id"]
    response = {"paragraph_revisions": [], "state_changes": [], "follow_up": None,
        "input_context": state["request_context"]["input_context"],
        "topic_update": {"operation": "add", "title": "付款接口超时", "summary": "查询支付状态后决定后续动作",
            "evidence_segment_ids": ["s1"], "evidence_quote": "现在继续讨论付款接口超时"}}
    response = state_response(p, state, response, status="none")
    applied = p.apply_intelligence_response(meeting_id=mid, job_id=state["id"], response=response, now_ms=5_100)
    assert p.complete_job(job_id=state["id"], worker_id="state", now_ms=5_200, output={"applied": applied})
    context = p.get_current_discussion(mid)["context"]
    assert context["last_semantic_seq"] == 1 and context["phase"] == "discussing"
    return state, source_hash


def _manual_projection(p, mid, *, now=5_300):
    job = p.enqueue_user_coach_request(meeting_id=mid, intent="ask_question", user_request="公开本地测试",
        idempotency_key="manual", now_ms=now)
    running = p.claim_next_job(worker_id="manual", lane="pi_deep", now_ms=now, lease_ms=10_000)
    assert running["id"] == job["id"]
    response = {"paragraph_revisions": [], "state_changes": [], "topic_update": None, "follow_up": None,
        "input_context": job["request_context"]["input_context"],
        "coach_decision": {"decision_id": "local-test-contribution", "run_id": "local-test-run", "status": "intervention", "runtime_used": "pi"},
        "coach_intervention": {"event_type": "question_to_user", "proposal_type": "professional_proposal",
            "recommendation": "建议补充查询不可用时页面提示的边界。", "reason": "当前这一条件尚未确定。",
            "evidence_segment_ids": ["s1"], "evidence_quote": "还没有确定查询暂时不可用时页面如何提示",
            "supporting_facts": [{"text": "还没有确定查询暂时不可用时页面如何提示", "source_ref": "s1"}]}}
    applied = p.apply_intelligence_response(meeting_id=mid, job_id=job["id"], response=response, now_ms=now + 100)
    assert p.complete_job(job_id=job["id"], worker_id="manual", now_ms=now + 200, output={"applied": applied})
    return job


def test_formal_success_and_manual_publication_do_not_reopen_same_input_after_120_seconds(tmp_path):
    p = _store(tmp_path)
    mid = runner.support.SCOPE_PREFIX + "no-new-text"
    try:
        state, _ = _actual_state_projection(p, mid)
        initial = p.get_current_discussion(mid)["context"]
        before = p.list_jobs(meeting_id=mid)
        assert p.enqueue_discussion_state(meeting_id=mid, now_ms=125_000) is None
        assert p.list_jobs(meeting_id=mid) == before
        manual = _manual_projection(p, mid, now=125_100)
        context = p.get_current_discussion(mid)["context"]
        assert context["proposals"] and context["analysis_basis_revision"] == initial["analysis_basis_revision"]
        assert context["last_semantic_seq"] == initial["last_semantic_seq"] == 1
        before = p.list_jobs(meeting_id=mid)
        assert p.enqueue_discussion_state(meeting_id=mid, now_ms=185_000) is None
        assert p.list_jobs(meeting_id=mid) == before and getattr(p, "acceptance_guard_activations", 0) == 0
        second = p.enqueue_user_coach_request(meeting_id=mid, intent="formulate_response", user_request="公开本地回应",
            idempotency_key="response", now_ms=185_100)
        assert second["request_context"]["topic_id"] == manual["request_context"]["topic_id"]
        assert second["request_context"]["analysis_basis_revision"] == initial["analysis_basis_revision"]
        assert second["request_context"]["evidence"] == manual["request_context"]["evidence"]
        assert second["input_version"] == manual["input_version"] == state["input_version"]
    finally:
        p.close()


def test_real_source_revision_needs_review_attempts_extra_state_and_guard_rolls_back(tmp_path):
    p = _store(tmp_path)
    mid = runner.support.SCOPE_PREFIX + "revision"
    try:
        _, source_hash = _actual_state_projection(p, mid)
        p.commit_transcript_revision(meeting_id=mid, segment_id="s1", expected_evidence_hash=source_hash,
            corrected_text=runner.base.PUBLIC_PAYMENT_TEXT + "新增需要核对的页面边界。", revision_id="revision2", now_ms=120_000)
        assert p.get_current_discussion(mid)["context"]["phase"] == "needs_review"
        before = p.list_jobs(meeting_id=mid)
        context = p.get_current_discussion(mid)
        assert p.enqueue_discussion_state(meeting_id=mid, now_ms=185_000) is None
        assert p.acceptance_guard_activations == 1 and p.list_jobs(meeting_id=mid) == before
        assert p.get_current_discussion(mid) == context
    finally:
        p.close()


def _observer(tmp_path, p, mid, report):
    observer = runner.SingleBasisObserver.__new__(runner.SingleBasisObserver)
    observer.run_dir = tmp_path
    observer.report_path = tmp_path / "report.json"
    observer.report = report
    observer.store = p
    observer.live = None
    observer.mid = mid
    observer.seen = {}
    observer.public_texts = {}
    observer.client = type("LocalClient", (), {"close": lambda self: None})()
    runner.support.fresh_json(observer.report_path, report)
    return observer


def test_bootstrap_timeout_closes_scope_with_zero_canonical_semantic_jobs_usage_or_calls(tmp_path, monkeypatch):
    p = _store(tmp_path / "runtime")
    mid = runner.support.SCOPE_PREFIX + "bootstrap"
    p.create_meeting(meeting_id=mid, title=None, now_ms=1_000)
    runner.support.fresh_json(tmp_path / "proxy-ledger.json", runner.base.QuoteAttemptBudget(limit=5).snapshot())
    report = runner.plan(tmp_path)
    observer = _observer(tmp_path, p, mid, report)
    monkeypatch.setattr(runner.base, "wait_local_signal", lambda *_a, **_k: {"matched": False, "elapsed_ms": 60_000,
        "invalid_signal_seen": False})
    observer.run = lambda: observer.signal("bootstrap", "page_ready")
    monkeypatch.setattr(runner.support, "ContinuousObserver", lambda *_: observer)
    assert p.enqueue_discussion_state(meeting_id=mid, now_ms=125_000) is None
    assert runner.support.execute(tmp_path, observer.report_path, report) == 1
    saved = json.loads(observer.report_path.read_text(encoding="utf-8"))
    assert saved["stop_diagnostic"]["code"] == "bootstrap_ui_ready_timeout"
    assert saved["pre_input_paid_work_proof"] == {"canonical_segments": 0, "semantic_paragraphs": 0,
        "durable_jobs": 0, "usage_rows": 0, "actual_upstream_posts": 0, "zero_paid_work": True}
    assert saved["scope_closed_without_paid_postprocessing"] and not saved["automated_contract_passed"]


@pytest.mark.parametrize("broken", ["missing_ledger", "proof_sqlite"])
def test_cleanup_proof_failure_preserves_first_stop_closes_and_never_claims_zero(tmp_path, monkeypatch, broken):
    p = _store(tmp_path / "runtime")
    mid = runner.support.SCOPE_PREFIX + "cleanup"
    p.create_meeting(meeting_id=mid, title=None, now_ms=1_000)
    if broken == "proof_sqlite":
        runner.support.fresh_json(tmp_path / "proxy-ledger.json", runner.base.QuoteAttemptBudget(limit=5).snapshot())
        original = p.list_jobs
        calls = [0]

        def one_failure(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise sqlite3.OperationalError("private local proof failure")
            return original(*args, **kwargs)
        monkeypatch.setattr(p, "list_jobs", one_failure)
    report = runner.plan(tmp_path)
    observer = _observer(tmp_path, p, mid, report)

    def fail():
        raise runner.support.AcceptanceStop("runtime_source_changed", "original private error must not be saved")
    observer.run = fail
    observer.collect = lambda: pytest.fail("cleanup retried network collection")
    monkeypatch.setattr(runner.support, "ContinuousObserver", lambda *_: observer)
    assert runner.support.execute(tmp_path, observer.report_path, report) == 1
    saved = json.loads(observer.report_path.read_text(encoding="utf-8"))
    assert saved["stop_diagnostic"]["code"] == "runtime_source_changed"
    assert saved["pre_input_paid_work_proof"]["zero_paid_work"] == "unknown_not_zero"
    assert saved["scope_closed_without_paid_postprocessing"] and "private" not in json.dumps(saved)
    with sqlite3.connect(tmp_path / "runtime/meeting_copilot.db") as db:
        assert db.execute("SELECT state FROM meetings WHERE id=?", (mid,)).fetchone()[0] == "ended"


def _basis_fixture():
    metadata = {"topic_id": "topic", "analysis_basis_revision": 2, "anchor_seq": 1,
        "evidence": [{"segment_id": "s1", "revision": 1, "evidence_hash": "source-hash"}],
        "input_context": {"material_set_id": "set", "material_context_revision": 1}}
    job = {"input_transcript_seq": 1, "input_version": 1, "evidence_segment_id": "s1", "evidence_hash": "source-hash",
        "request_context": metadata, "output": {"applied": {"coach_intervention": {"source_evidence": [
            {"source_type": "meeting_attachment", "version_id": "v1", "content_hash": "material-hash"}]}}}}
    first, second = json.loads(json.dumps(job)), json.loads(json.dumps(job))
    return first, second


@pytest.mark.parametrize("case", ["valid", "both_new_basis", "both_new_version", "changed_source", "changed_scope", "wrong_material"])
def test_two_self_consistent_jobs_cannot_drift_from_natural_state_basis(case):
    first, second = _basis_fixture()
    if case == "both_new_basis":
        first["request_context"]["analysis_basis_revision"] = second["request_context"]["analysis_basis_revision"] = 3
    elif case == "both_new_version":
        first["input_version"] = second["input_version"] = 2
    elif case == "changed_source":
        second["evidence_hash"] = "changed"
    elif case == "changed_scope":
        second["request_context"]["input_context"]["material_context_revision"] = 2
    elif case == "wrong_material":
        second["output"]["applied"]["coach_intervention"]["source_evidence"][0]["version_id"] = "v2"
    checks, proof = runner.fixed_basis_checks(first, second, source={"segment_id": "s1", "seed": {"source_hash": "source-hash"}},
        material_version="v1", initial_discussion={"work_item_id": "topic", "context": {"analysis_basis_revision": 2}},
        initial_state_job={"input_version": 1})
    assert all(checks.values()) is (case == "valid")
    assert proof["natural_state_basis"] == 2 and proof["whole_discussion_context_or_packet_hash_compared"] is False


def test_fixed_v1_manual2_keeps_all_quote_fact_owner_checks_without_claiming_v2(monkeypatch):
    called = []

    def checks(*args, **kwargs):
        called.append(kwargs)
        return {"manual2_own_current_formal_intervention": True, "manual2_typed_facts_verbatim": True,
            "manual2_visible_fact_unique_to_v2": False}, {"quote_efficiency": {"no_main_quote_repair": False}}
    monkeypatch.setattr(runner.base, "request_checks", checks)
    result, details = runner.fixed_v1_request_checks("manual2", {}, {}, material_version="v1", public_texts={}, observed_at_ms=1)
    assert all(result.values()) and "manual2_visible_fact_unique_to_v2" not in result
    assert called[0]["material_text"] == runner.support.MATERIAL_V1
    assert not details["quote_efficiency"]["no_main_quote_repair"] and "version-change coverage not claimed" in details["material_version_policy"]


def test_native_new_profile_preflight_flat_proxy_and_canonical_provider_get(tmp_path, monkeypatch):
    budget = runner.base.QuoteAttemptBudget(limit=5)
    server = runner.support.proxy.make_server(port=0, upstream_base="https://synthetic.example", upstream_key="public-test-key",
        proxy_token="public-test-token", budget=budget)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(runner.support, "PROXY_PORT", server.server_port)
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "public-test-token")
    app = runner.base.build_app(tmp_path)
    pid = os.getpid()
    runner.support.fresh_json(tmp_path / "proxy-identity.json", {"schema_version": runner.support.BOUNDARY_SCHEMA,
        "role": "proxy", "port": server.server_port, "pid": pid,
        "process_start_marker": runner.support.workbench_server.process_start_marker(pid),
        "ledger": str(tmp_path / "proxy-ledger.json"), "maximum_forward_attempts": 5})
    actual_owners = runner.support.listener_owners
    monkeypatch.setattr(runner.support, "listener_owners", lambda port: {pid} if port == runner.support.APP_PORT else actual_owners(port))
    try:
        client = TestClient(app)
        result = runner.support.preflight(tmp_path, client)
        assert all(result["checks"].values()) and result["identity"]["schema_version"] == runner.support.BOUNDARY_SCHEMA
        assert result["identity"]["automatic_enabled"] is False and result["identity"]["discussion_state_enabled"] is True
        assert result["proxy_budget"]["maximum_forward_attempts"] == 5 and budget.snapshot()["reserved_forward_attempts"] == 0
    finally:
        app.state.v2_persistence.close()
        app.state.pi_coach_runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
