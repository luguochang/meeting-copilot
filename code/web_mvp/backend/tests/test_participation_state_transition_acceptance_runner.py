from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Thread
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("state_transition_acceptance", ROOT / "tools/participation_state_transition_acceptance.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_dry_plan_has_new_two_call_profile_without_runtime_or_old_budget_mutation(tmp_path, monkeypatch):
    import participation_continuous_acceptance as original
    monkeypatch.setattr(runner.support, "execute", lambda *_args: pytest.fail("dry plan executed"))
    monkeypatch.setattr(runner.support.httpx, "Client", lambda **_kwargs: pytest.fail("dry plan accessed network"))
    directory = tmp_path / "new"
    assert runner.main(["--run-dir", str(directory)]) == 0
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert plan["profile"] == "state_transition_only.v1" and not plan["executed"]
    assert plan["budget"] == {"host_jobs": 2, "state": 2, "automatic": 0, "manual": 0,
        "forwarded_upstream_posts": 2, "attempts_per_host_job": 1, "retries": 0}
    assert not plan["runtime_flags"]["discussion_automatic_enabled"]
    assert plan["runtime_flags"]["discussion_state_enabled"] and plan["runtime_flags"]["preparation_policy"] == "off"
    assert not plan["semantic_fixture_applied"] and not plan["direct_state_or_automatic_admission"]
    assert not (directory / "runtime").exists() and not (directory / "proxy-ledger.json").exists()
    assert original.HOST_LIMIT == 9 and original.UPSTREAM_LIMIT == 14 and original.LIMITS["automatic"] == 2
    with pytest.raises(FileExistsError):
        runner.main(["--run-dir", str(directory)])


def test_proxy_third_call_is_rejected_and_old_ledger_cannot_be_reset(tmp_path, monkeypatch):
    ledger = tmp_path / "proxy-ledger.json"
    budget = runner.StateAttemptBudget(limit=2, report=ledger)
    assert budget.reserve(lane="unknown", endpoint="/v1/responses") == 1
    budget.finish(1, status="client_disconnected", http_status=None)
    assert budget.reserve(lane="unknown", endpoint="/v1/responses") == 2
    assert budget.reserve(lane="unknown", endpoint="/v1/responses") is None
    assert budget.snapshot()["reserved_forward_attempts"] == 2 and budget.snapshot()["budget_rejections"] == 1
    original = ledger.read_bytes()
    monkeypatch.setattr(runner.support.proxy, "make_server", lambda **_kwargs: pytest.fail("attempted old budget reset"))
    with pytest.raises(ValueError, match="old budget cannot be reset"):
        runner.support.serve_proxy(tmp_path)
    assert ledger.read_bytes() == original
    with pytest.raises(ValueError):
        runner.StateAttemptBudget(limit=14)


@pytest.mark.parametrize("profile_name", ["old", "natural"])
def test_actual_state_host_ceiling_rejects_third_atomically_and_manual_is_not_admitted(tmp_path, profile_name):
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence
    profile = runner.support if profile_name == "old" else runner.natural_support
    p = profile.guarded_persistence_type(V2Persistence)(tmp_path / "meeting.db", semantic_projection_mode="llm_first")
    mid = profile.SCOPE_PREFIX + "guard"
    try:
        p.create_meeting(meeting_id=mid, title=None, now_ms=1_000)
        for seq in range(1, 4):
            text = profile.INPUTS[0] + f"公开段落{seq}。"
            p.commit_final_and_enqueue(meeting_id=mid, final_id=f"f{seq}", segment_id=f"s{seq}", text=text, normalized_text=text,
                started_at_ms=seq * 1_000, ended_at_ms=seq * 1_000 + 800, evidence_hash=f"h{seq}", now_ms=2_000 + seq,
                enqueue_intelligence=False)
            before = p.list_jobs(meeting_id=mid)
            topic_before = p.get_current_discussion(mid)
            job = p.enqueue_discussion_state(meeting_id=mid, now_ms=3_000 + seq, cooldown_ms=0, stable_ms=0,
                minimum_segments=1, minimum_characters=0)
            if seq < 3:
                assert job["max_attempts"] == 1
                running = p.claim_next_job(worker_id="guard", lane="discussion_state", now_ms=3_000 + seq, lease_ms=5_000)
                assert running["id"] == job["id"]
                assert p.complete_job(job_id=job["id"], worker_id="guard", now_ms=3_010 + seq, output={})
            else:
                assert job is None and p.acceptance_guard_activations == 1
                assert p.list_jobs(meeting_id=mid) == before and p.get_current_discussion(mid) == topic_before
        before = p.list_jobs(meeting_id=mid)
        with pytest.raises(RuntimeError, match="budget exhausted"):
            p.enqueue_user_coach_request(meeting_id=mid, user_request="", intent="compare_options", idempotency_key="manual", now_ms=4_000)
        assert p.list_jobs(meeting_id=mid) == before
    finally:
        p.close()


def test_real_app_provider_canonical_get_and_state_only_mutation_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "public-test-token")
    app = runner.build_app(tmp_path)
    try:
        client = TestClient(app)
        identity = client.get("/__acceptance/identity").json()
        assert identity["schema_version"] == runner.support.BOUNDARY_SCHEMA
        assert identity["host_limit"] == 2 and identity["lane_limits"] == {"discussion_state": 2, "automatic": 0, "manual": 0}
        assert identity["automatic_enabled"] is False and identity["discussion_state_enabled"] is True
        provider = client.get("/providers/config").json()
        assert provider["base_url"] == "http://127.0.0.1:8791" and provider["api_style"] == "responses"
        assert client.post("/v2/meetings/unrelated/coach/request", json={"intent": "freeform"}).status_code == 403
        assert client.post("/asr-live/sessions").status_code == 403
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/asr-live/sessions/unrelated/audio"):
                pytest.fail("audio WebSocket accepted")
        assert caught.value.code == 1008
        with pytest.raises(ValueError, match="fresh empty runtime"):
            runner.build_app(tmp_path)
    finally:
        app.state.v2_persistence.close()
        app.state.pi_coach_runtime.close()


@pytest.mark.parametrize("profile_name", ["old", "natural"])
def test_two_call_preflight_uses_real_flat_proxy_get_and_official_canonical_provider_get(tmp_path, monkeypatch, profile_name):
    profile = runner.support if profile_name == "old" else runner.natural_support
    budget = runner.StateAttemptBudget(limit=2)
    server = profile.proxy.make_server(port=0, upstream_base="https://synthetic.example", upstream_key="public-test-key",
        proxy_token="public-test-token", budget=budget)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(profile, "PROXY_PORT", server.server_port)
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "public-test-token")
    if profile_name == "old":
        app = runner.build_app(tmp_path)
    else:
        import participation_natural_review_profile as natural
        app = runner.build_natural_state_app(tmp_path, natural.read_fixture(natural.DEFAULT_FIXTURE), natural.DEFAULT_PDF_RECEIPT)
    pid = os.getpid()
    marker = profile.workbench_server.process_start_marker(pid)
    profile.fresh_json(tmp_path / "proxy-identity.json", {"schema_version": profile.BOUNDARY_SCHEMA,
        "role": "proxy", "port": server.server_port, "pid": pid, "process_start_marker": marker,
        "ledger": str(tmp_path / "proxy-ledger.json"), "maximum_forward_attempts": 2})
    original_owners = profile.listener_owners
    monkeypatch.setattr(profile, "listener_owners", lambda port: {pid} if port == profile.APP_PORT else original_owners(port))
    try:
        result = profile.preflight(tmp_path, TestClient(app))
        assert all(result["checks"].values())
        assert result["identity"]["automatic_enabled"] is False
        assert result["proxy_budget"]["maximum_forward_attempts"] == 2
        assert result["proxy_budget"]["reserved_forward_attempts"] == 0 and "budget" not in result["proxy_budget"]
        assert budget.snapshot()["reserved_forward_attempts"] == 0
    finally:
        app.state.v2_persistence.close()
        app.state.pi_coach_runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("case", ["valid", "all_old_answered", "new_topic_inherits_old", "old_quote_hash"])
def test_transition_requires_one_current_response_and_an_unanswered_old_point(case):
    first = {"discussion": {"work_item_id": "old", "context": {"open_points": [
        {"id": item_id, "entity_type": "open_question"} for item_id in ("q-answer", "q-still-open")]}}}
    response = {"id": "q-answer", "entity_type": "open_question", "source_refs": ["continuous-public-2"],
        "evidence": [{"segment_id": "continuous-public-2", "revision": 1, "evidence_hash": "current-hash"}]}
    owner = {"work_item_id": "old", "state": "superseded", "context": {"phase": "closed", "responded_points": [response],
        "open_points": [{"id": "q-still-open", "entity_type": "open_question"}]}}
    current = {"work_item_id": "new", "context": {"responded_points": [], "constraints": [], "open_points": [
        {"id": "q-new", "entity_type": "open_question", "source_refs": ["continuous-public-2"]}]}}
    if case == "all_old_answered":
        owner["context"]["open_points"] = []
    elif case == "new_topic_inherits_old":
        current["context"]["open_points"].append({"id": "q-still-open", "entity_type": "open_question", "source_refs": ["continuous-public-2"]})
    elif case == "old_quote_hash":
        response["evidence"][0]["evidence_hash"] = "obsolete-hash"
    checks, _ = runner.transition_checks(first, {"discussion": current, "all_topic_work_items": [owner, current]},
        {"segment_id": "continuous-public-2", "seed": {"source_hash": "current-hash"}})
    assert all(checks.values()) is (case == "valid")


def test_natural_diagnostic_dry_plan_keeps_three_frozen_uploads_and_two_states_without_old_profile_mutation(tmp_path, monkeypatch):
    import participation_continuous_acceptance as original
    import participation_natural_review_profile as natural
    before = (runner.support.SCOPE_PREFIX, runner.support.BOUNDARY_SCHEMA,
        runner.support.LIMITS.copy(), original.HOST_LIMIT, original.UPSTREAM_LIMIT)
    monkeypatch.setattr(runner.natural_support, "execute", lambda *_a, **_k: pytest.fail("dry-run executed"))
    monkeypatch.setattr(runner.natural_support.httpx, "Client", lambda **_k: pytest.fail("dry-run accessed network"))
    directory = tmp_path / "diagnostic"
    assert runner.main(["--profile", runner.NATURAL_STATE_PROFILE, "--run-dir", str(directory)]) == 0
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert plan["profile"] == runner.NATURAL_STATE_PROFILE and not plan["executed"]
    assert plan["budget"]["host_jobs"] == plan["budget"]["forwarded_upstream_posts"] == 2
    assert plan["budget"]["automatic"] == plan["budget"]["manual"] == 0
    assert plan["runtime_flags"]["realtime_coach_enabled"] and plan["runtime_flags"]["discussion_state_enabled"]
    assert not plan["runtime_flags"]["pi_bridge_prewarm_enabled"]
    assert [(item["block_id"], item["planned_offset_seconds"]) for item in plan["stages"]] == [("B1", 0), ("B2", 100)]
    assert plan["fixture"]["sha256"] == natural.FIXTURE_SHA and plan["inspection_seconds"] == 0
    assert not (directory / "runtime").exists() and not (directory / "proxy-ledger.json").exists()
    assert before == (runner.support.SCOPE_PREFIX, runner.support.BOUNDARY_SCHEMA,
        runner.support.LIMITS, original.HOST_LIMIT, original.UPSTREAM_LIMIT)
    with pytest.raises(FileExistsError):
        runner.main(["--profile", runner.NATURAL_STATE_PROFILE, "--run-dir", str(directory)])


def test_natural_diagnostic_actual_app_exact_upload_bind_usage_and_no_prewarm_audio_or_foreign_writes(tmp_path, monkeypatch):
    import participation_natural_review_profile as natural
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "public-test-token")
    monkeypatch.setenv("MEETING_COPILOT_PI_BRIDGE_PREWARM", "1")
    fixture = natural.read_fixture(natural.DEFAULT_FIXTURE)
    pdf = natural.read_pdf_receipt(natural.DEFAULT_PDF_RECEIPT, fixture)
    app = runner.build_natural_state_app(tmp_path, fixture, natural.DEFAULT_PDF_RECEIPT)
    monkeypatch.setattr(app.state.pi_coach_runtime, "prewarm", lambda: pytest.fail("state-only profile prewarmed Pi"))
    assert os.environ["MEETING_COPILOT_PI_BRIDGE_PREWARM"] == "0"
    try:
        with TestClient(app) as client:
            identity = client.get("/__acceptance/identity")
            assert identity.json()["profile"] == runner.NATURAL_STATE_PROFILE
            assert identity.json()["host_limit"] == 2 and identity.json()["lane_limits"] == {"discussion_state": 2, "automatic": 0, "manual": 0}
            assert identity.json()["pi_bridge_prewarm_enabled"] is False and identity.json()["realtime_coach_enabled"] is True
            assert "microphone=()" in identity.headers["Permissions-Policy"]
            material_set = client.post("/v2/material-sets").json()["material_set"]
            sid = material_set["id"]
            assert client.post("/v2/material-sets").status_code == 403
            uploaded = {}
            for material in fixture["data"]["materials"]:
                content = pdf["bytes"] if material["material_id"] == "M_PRODUCT" else material["versions"][0]["content"].encode()
                path = f"/v2/material-sets/{sid}/attachments"
                bad = client.post(path, files={"file": (material["filename"], content+b"wrong")})
                assert bad.status_code == 403
                response = client.post(path, files={"file": (material["filename"], content)},
                    data={"title": material["filename"], "purpose": "public fixture"})
                assert response.status_code == 202
                aid = response.json()["attachment"]["id"]
                detail_path = f"{path}/{aid}"
                for _ in range(200):
                    detail = client.get(detail_path).json()
                    if detail.get("version"):
                        break
                    time.sleep(.01)
                assert detail["version"]["number"] == 1
                uploaded[material["material_id"]] = (detail_path, detail)
                assert client.post(path, files={"file": (material["filename"], content)}).status_code == 403
                assert client.post(detail_path+"/versions", json={"content": "v2"}).status_code == 403
                assert client.put(detail_path+"/usage", json={}).status_code == 403  # Not bound yet.
            material_set = client.get(f"/v2/material-sets/{sid}").json()["material_set"]
            mid = runner.natural_support.SCOPE_PREFIX + "a"*12
            payload = {"meeting_id": mid, "title": runner.NATURAL_STATE_MEETING_TITLE,
                "material_set_id": sid, "expected_material_set_revision": material_set["revision"]}
            assert client.post("/v2/meetings", json={**payload, "meeting_id": "foreign"},
                headers={"Idempotency-Key": "foreign"}).status_code == 403
            created = client.post("/v2/meetings", json=payload, headers={"Idempotency-Key": mid})
            assert created.status_code == 201
            assert created.json()["meeting"]["title"] == runner.NATURAL_STATE_MEETING_TITLE
            prep = client.get(f"/v2/meetings/{mid}/preparation").json()
            assert client.put(f"/v2/meetings/{mid}/preparation", json={**prep,
                "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"}).status_code == 200
            assert client.put(f"/v2/meetings/{mid}/preparation", json={**prep,
                "live_analysis_profile": "participation", "proactive_suggestion_policy": "low_frequency"}).status_code == 403
            assert client.put("/v2/meetings/foreign/preparation", json=prep).status_code == 403
            for material_id, (path, detail) in uploaded.items():
                body = {"version_id": detail["version"]["id"], "enabled": True,
                    "selected_span_ids": None, "expected_revision": detail["attachment"]["revision"]}
                assert client.put(path+"/usage", json={**body, "version_id": "foreign-version"}).status_code == 403
                response = client.put(path+"/usage", json=body)
                assert response.status_code == (403 if material_id == "M_EXPORT" else 200)
                if material_id != "M_EXPORT":
                    assert client.put(path+"/usage", json=body).status_code == 403
            enabled = client.get(f"/v2/material-sets/{sid}").json()["attachments"]
            assert len(enabled) == 3 and sum(item["enabled"] for item in enabled) == 2
            for method, path in [("POST", f"/v2/meetings/{mid}/coach/request"),
                    ("POST", "/v2/traces/owned/ui-rendered"), ("PUT", "/providers/config"),
                    ("GET", "/asr/devices"), ("HEAD", "/devices"), ("OPTIONS", "/audio"),
                    ("POST", "/asr-live/sessions"), ("GET", f"/v2/meetings/{mid}/recordings")]:
                assert client.request(method, path).status_code == 403
            with pytest.raises(WebSocketDisconnect) as caught:
                with client.websocket_connect("/asr-live/sessions/foreign/audio"):
                    pytest.fail("audio socket accepted")
            assert caught.value.code == 1008
            assert app.state.v2_persistence._conn.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0] == 0
            assert app.state.v2_persistence.list_jobs(meeting_id=mid) == []
    finally:
        app.state.v2_persistence.close()
        app.state.pi_coach_runtime.close()


def test_natural_diagnostic_observer_uses_only_original_b1_b2_wait_and_closes_on_second_failure(tmp_path, monkeypatch):
    import participation_natural_review_profile as natural
    fixture = natural.read_fixture(natural.DEFAULT_FIXTURE)
    cls = runner.natural_state_observer_type(fixture, natural.DEFAULT_PDF_RECEIPT)
    obs = cls(tmp_path, tmp_path/"progress.json", {"profile": runner.NATURAL_STATE_PROFILE})
    events = []
    monkeypatch.setattr(runner.natural_support, "preflight", lambda *_: {"identity": {}})
    def upload():
        obs.report["material_set_id"] = "own-set"
        obs.materials = {key: {"attachment_id": key, "version_ids": {1: key+"-v1"}} for key in ("M_API", "M_PRODUCT", "M_EXPORT")}
        events.append("upload3")
    monkeypatch.setattr(obs, "upload_materials", upload)
    def http(method, path, **kwargs):
        if method == "POST":
            obs.store.create_meeting(meeting_id=kwargs["json"]["meeting_id"], title=None, now_ms=1)
            return {}
        if path.endswith("/preparation"):
            return kwargs.get("json", {})
        return {"material_set": {"id": "own-set", "revision": 1}, "attachments": []}
    monkeypatch.setattr(obs, "http", http)
    def wait_job(kind, ordinal):
        events.append((kind, ordinal))
        if ordinal == 2:
            raise runner.natural_support.AcceptanceStop("own_job_failed", "local safe failure fixture")
        return {"id": "new-state1", "status": "succeeded"}
    monkeypatch.setattr(obs, "wait_job", wait_job)
    monkeypatch.setattr(obs, "checkpoint", lambda stage, **_: events.append(stage))
    monkeypatch.setattr(obs, "enable", lambda material, version: events.append(("enable", material, version)))
    def release(offset, *, origin, tick):
        assert offset == 100 and isinstance(origin, float)
        events.append(("normal_release", offset))
        return {"planned_offset_seconds": offset, "clock_advanced": False}
    monkeypatch.setattr(natural, "wait_release", release)
    monkeypatch.setattr(obs, "finish", lambda: events.append("finish"))
    monkeypatch.setattr(obs, "manual", lambda *_: pytest.fail("manual path called"))
    try:
        with pytest.raises(runner.natural_support.AcceptanceStop) as failed:
            obs.run()
        assert failed.value.code == "own_job_failed"
        assert events == ["upload3", ("discussion_state", 1), "state1_natural_success", ("normal_release", 100),
            ("enable", "M_API", 1), ("enable", "M_PRODUCT", 1), ("discussion_state", 2), "finish"]
        sources = obs.report["sources"]
        assert len(sources) == 6 and [item["text"] for item in sources] == [
            turn["text"] for block in fixture["data"]["blocks"][:2] for turn in block["turns"]]
        assert len({item["segment_id"] for item in sources}) == 6
        assert all(item["segment_id"] != item["turn_id"] and item["seed"]["live_record_mirrored"] for item in sources)
        assert not obs.report.get("requests")
    finally:
        obs.live.close()
        obs.store.close()
        obs.client.close()
