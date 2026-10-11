from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
import participation_natural_review_profile as natural

SPEC = importlib.util.spec_from_file_location("natural_continuous_support", ROOT / "tools/participation_continuous_acceptance.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def frozen():
    return natural.read_fixture(natural.DEFAULT_FIXTURE)


def observer(tmp_path):
    cls = natural.observer_type(runner, frozen(), None)
    return cls(tmp_path, tmp_path / "progress.json", {"profile": natural.PROFILE})


def test_explicit_profile_plan_is_fresh_and_never_executes_or_mutates_old_default(tmp_path, monkeypatch):
    before = (runner.LIMITS.copy(), runner.HOST_LIMIT, runner.UPSTREAM_LIMIT, runner.SCOPE_PREFIX)
    monkeypatch.setattr(runner, "execute", lambda *_a, **_k: pytest.fail("dry-run executed"))
    assert runner.main(["--profile", natural.PROFILE, "--run-dir", str(tmp_path)]) == 0
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["budget"]["forwarded_upstream_posts"] == 14
    assert plan["stages"][1]["manual_intent"] == "ask_question"
    assert not plan["executed"] and not plan["audio_capture"]
    assert not (tmp_path / "runtime").exists()
    assert before == (runner.LIMITS, runner.HOST_LIMIT, runner.UPSTREAM_LIMIT, runner.SCOPE_PREFIX)
    assert runner.plan(tmp_path)["stages"][1]["id"] == "responded_and_inflight_manual"
    with pytest.raises(FileExistsError):
        runner.main(["--profile", natural.PROFILE, "--run-dir", str(tmp_path)])


def test_modified_fixture_or_disguised_pdf_rejected_before_observer_network(tmp_path):
    altered = tmp_path / "fixture.json"
    altered.write_bytes(natural.DEFAULT_FIXTURE.read_bytes() + b" ")
    with pytest.raises(ValueError, match="frozen"):
        natural.read_fixture(altered)
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen"):
        natural.read_pdf_receipt(receipt, frozen())


def test_missing_execution_asset_is_recorded_before_network_or_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "execute", lambda *_a, **_k: pytest.fail("asset failure dispatched"))
    code = runner.main(["--profile", natural.PROFILE, "--run-dir", str(tmp_path), "--execute",
        "--pdf-receipt", str(tmp_path / "absent.json")])
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert code == 1 and report["provider_calls_made"] == 0
    assert report["stop_diagnostic"]["code"] == "input_asset_preflight_failed"
    assert not (tmp_path / "runtime").exists() and not (tmp_path / "proxy-ledger.json").exists()


def test_real_pdf_formal_converter_preserves_two_pages_and_text_source_is_distinct():
    from meeting_copilot_web_mvp.document_import import extract_document
    pdf = natural.read_pdf_receipt(natural.DEFAULT_PDF_RECEIPT, frozen())
    converted = extract_document("public-import-product.pdf", pdf["bytes"], "application/pdf")
    assert converted.status == "extracted" and converted.format == "pdf"
    assert natural.sha(converted.content.encode()) != pdf["data"]["source"]["content_sha256"]
    for page in pdf["data"]["extraction"]["pages"]:
        spans = [span for span in converted.spans if span["locator"].get("page") == page["page"]]
        assert spans and all(span["locator"]["type"] == "pdf_page" for span in spans)
        assert "\n".join(span["text"] for span in spans).replace("\n", "") == page["text"].replace("\n", "")


def test_actual_seed_keeps_twelve_sources_without_sending_gold_or_action_fields(tmp_path):
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
    obs = observer(tmp_path)
    obs.store = runner.value.open_synthetic_store(tmp_path / "app.sqlite3")
    obs.live = SqliteAsrLiveSessionRepository(tmp_path / "live")
    obs.mid = runner.SCOPE_PREFIX + "natural_seed"
    obs.store.create_meeting(meeting_id=obs.mid, title="公开输入", now_ms=1)
    runner.value.create_synthetic_live_record(obs.live, obs.mid)
    try:
        for block in frozen()["data"]["blocks"]:
            obs.append_block(block)
        sources = obs.report["sources"]
        assert len(sources) == 12 and len({source["segment_id"] for source in sources}) == 12
        assert [source["text"] for source in sources] == [turn["text"] for block in frozen()["data"]["blocks"] for turn in block["turns"]]
        assert all(source["seed"]["live_record_mirrored"] for source in sources)
        assert all(source["segment_id"] != source["turn_id"] for source in sources)
        persisted = obs.store.get_snapshot(obs.mid)["segments"]
        assert len(persisted) == 12
        assert "ordered_operations" not in json.dumps(persisted)
        assert "expected_scoring" not in json.dumps(persisted)
    finally:
        obs.live.close()
        obs.store.close()
        obs.client.close()


def test_bootstrap_timeout_does_not_publish_text_or_dispatch_provider(tmp_path, monkeypatch):
    obs = observer(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(natural.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(natural.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(obs, "collect", lambda: ([], {"reserved_forward_attempts": 0}))
    monkeypatch.setattr(obs, "append_block", lambda *_: pytest.fail("input before bootstrap"))
    try:
        with pytest.raises(runner.AcceptanceStop) as failed:
            obs.signal("bootstrap", "page_ready", maximum=60)
        assert failed.value.code == "ui_ready_timeout" and clock[0] >= 60
        assert not obs.report.get("sources") and not obs.report.get("requests")
    finally:
        obs.client.close()


def dom_fixture():
    receipt = {"meeting_id": "m", "job_id": "j", "decision_id": "d", "event_seq": 7, "input_version": 3, "draft_seq": 0}
    card = {"job_id": "j", "decision_id": "d", "ui_render_receipt": receipt}
    trace = {"meeting_id": "m", "job_id": "j", "stages": {"job_queued": {"monotonic_ns": 1_000_000},
        "ui_rendered": {"monotonic_ns": 9_000_000, "attributes": receipt}},
        "execution": {"provenance": {"ui_render_ack": {"status": "observed", "at_monotonic_ns": 9_000_000,
            "attributes": {"source": "formal_coach_ui.v1", "event_seq": 7, "input_version": 3}}}}}
    return card, trace


@pytest.mark.parametrize("field", ["meeting_id", "job_id", "decision_id", "event_seq", "input_version", "draft_seq"])
def test_every_receipt_field_must_match_actual_host_dom_and_monotonic(field):
    card, trace = dom_fixture()
    good = natural.dom_observation(card, [trace])
    assert good["exact_six_field_dom_ack"] and good["enqueue_to_dom_ms"] == 8
    changed = deepcopy(trace)
    changed["stages"]["ui_rendered"]["attributes"][field] = "wrong" if isinstance(card["ui_render_receipt"][field], str) else 99
    assert not natural.dom_observation(card, [changed])["exact_six_field_dom_ack"]
    assert not natural.dom_observation(card, [])["exact_six_field_dom_ack"]


def test_matching_inspection_signal_cannot_create_dom_receipt(tmp_path):
    payload = {"meeting_id": "m", "stage": "manual1", "signal": "inspected", "nonce": "n"}
    path = tmp_path / "signal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert natural.signal_state(path, payload) == "matched"
    card, _ = dom_fixture()
    assert not natural.dom_observation(card, [])["exact_six_field_dom_ack"]
    wrong = {**payload, "nonce": "old"}
    assert natural.signal_state(path, wrong) == "invalid"


@pytest.mark.parametrize("start", [None, -1, 99_000_000])
def test_dom_receipt_does_not_claim_valid_latency_when_enqueue_missing_or_reversed(start):
    card, trace = dom_fixture()
    trace["stages"]["job_queued"]["monotonic_ns"] = start
    actual = natural.dom_observation(card, [trace])
    assert actual["exact_six_field_dom_ack"]
    assert not actual["host_monotonic_interval_valid"] and actual["enqueue_to_dom_ms"] is None


@pytest.mark.parametrize("silent,turns,errors,expected", [(False, 1, [], True), (True, 1, [], True),
    (False, 2, [{"tool": "submit_intervention", "code": "quote_selection_invalid"}], False)])
def test_first_turn_gate_preserves_legal_silence_but_does_not_accept_repair(silent, turns, errors, expected):
    name = "keep_silent" if silent else "submit_intervention"
    metrics = {"turns": turns, "tool_calls": turns, "tool_names": [name] * turns, "tool_errors": errors}
    result = natural.efficiency({"output": {"coach": {"agent_metrics": metrics}}}, {"validated_silence": silent, "valid_intervention": not silent})
    assert result["first_turn_terminal_no_errors"] is expected
    assert result["repair_observed"] is bool(errors)


@pytest.mark.parametrize("calls,valid", [(None, True), (2, True), (1, False)])
def test_missing_or_contradictory_tool_count_and_invalid_own_outcome_cannot_pass(calls, valid):
    metrics = {"turns": 1, "tool_calls": calls, "tool_names": ["submit_intervention"], "tool_errors": []}
    result = natural.efficiency({"output": {"coach": {"agent_metrics": metrics}}}, {"valid_intervention": valid})
    assert not result["first_turn_terminal_no_errors"]


def test_repair_completes_own_result_then_stops_before_future_paid_stage(tmp_path, monkeypatch):
    obs = observer(tmp_path)
    card, trace = dom_fixture()
    card.update(analysis_kind="participation", topic_id="topic", analysis_basis_revision=1, valid_until_ms=100,
        supporting_facts=[{"source_ref": "public", "text": "原文"}])
    obs.public_texts = {"public": "原文"}
    snapshot = {"coach_intervention": card, "discussion": {"work_item_id": "topic", "context": {"analysis_basis_revision": 1}}}
    checkpoint = {"snapshot": snapshot, "traces": [trace], "observed_at_ms": 50,
        "own_outcome": {"valid_intervention": True, "validated_silence": False, "current_card_for_request": True},
        "fact_checks": [{"known_source": True, "verbatim": True}]}
    calls = []
    monkeypatch.setattr(obs, "checkpoint", lambda stage, **_: calls.append(stage) or checkpoint)
    monkeypatch.setattr(obs, "signal", lambda stage: calls.append(stage + "_signal"))
    job = {"id": "j", "request_context": {"topic_id": "topic", "analysis_basis_revision": 1},
        "output": {"coach": {"agent_metrics": {"turns": 2, "tool_calls": 2,
            "tool_names": ["submit_intervention"] * 2,
            "tool_errors": [{"tool": "submit_intervention", "code": "quote_selection_invalid"}]}}}}
    try:
        with pytest.raises(runner.AcceptanceStop) as failed:
            obs.observe_result(job, "manual1")
        assert failed.value.code == "first_turn_efficiency_failed"
        assert calls == ["manual1", "manual1_signal", "manual1_dom_observed"]
        assert obs.report["requests"][0]["dom"]["exact_six_field_dom_ack"]
    finally:
        obs.client.close()


def test_formal_material_save_only_and_usage_ack_use_real_revision_and_owner(tmp_path, monkeypatch):
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
    from meeting_copilot_web_mvp.meeting_materials import MeetingMaterialsRepository, MeetingMaterialImportService
    from meeting_copilot_web_mvp.meeting_material_jobs import MeetingMaterialJobQueue
    from meeting_copilot_web_mvp.document_import import extract_document
    library = KnowledgeRepository(tmp_path / "materials.sqlite3")
    repo = MeetingMaterialsRepository(library)
    importer = MeetingMaterialImportService(repo, tmp_path / "assets")
    queue = MeetingMaterialJobQueue(repo)
    obs = observer(tmp_path)
    material_set = repo.create_set()
    source = next(m for m in frozen()["data"]["materials"] if m["material_id"] == "M_PRODUCT")
    content = source["versions"][0]["content"]
    attachment = importer.stage(material_set["id"], "product.md", content.encode(), content_type="text/markdown")
    assert queue.finish(queue.claim(), extract_document("product.md", content.encode(), "text/markdown"))
    attachment = repo.get_attachment(material_set["id"], attachment["id"])
    repo.usage(material_set["id"], attachment["id"], version_id=attachment["latest_version_id"], enabled=True, expected_revision=attachment["revision"])
    obs.report["material_set_id"] = material_set["id"]
    path = f"/v2/material-sets/{material_set['id']}/attachments/{attachment['id']}"
    obs.materials["M_PRODUCT"] = {"path": path, "enabled": True, "active_version": 1,
        "version_ids": {1: attachment["latest_version_id"]}, "version_texts": {1: content}}
    operations = []

    def http(method, route, **kwargs):
        operations.append((method, route))
        if route == f"/v2/material-sets/{material_set['id']}":
            return {"material_set": repo.get_set(material_set["id"])}
        if method == "GET":
            return {"attachment": repo.get_attachment(material_set["id"], attachment["id"])}
        payload = kwargs["json"]
        if route.endswith("/versions"):
            version = repo.add_version(material_set["id"], attachment["id"], **payload)
            return {"version": version, "attachment": repo.get_attachment(material_set["id"], attachment["id"])}
        result = repo.usage(material_set["id"], attachment["id"], **payload)
        return {"attachment": result, "material_set": repo.get_set(material_set["id"])}

    monkeypatch.setattr(obs, "http", http)
    try:
        obs.save_version("M_PRODUCT", 2)
        assert obs.report["save_only_scope"]["passed"]
        assert repo.get_attachment(material_set["id"], attachment["id"])["active_version_id"] == attachment["latest_version_id"]
        obs.enable("M_PRODUCT", 2)
        assert repo.get_attachment(material_set["id"], attachment["id"])["active_version_id"] == obs.materials["M_PRODUCT"]["version_ids"][2]
        posts = [route for method, route in operations if method == "POST"]
        puts = [route for method, route in operations if method == "PUT"]
        assert posts == [path + "/versions"] and puts == [path + "/usage"]
        from meeting_copilot_web_mvp.meeting_materials import bind_material_set, read_attachment_source
        obs.mid = "public_bound"
        with repo._write():
            repo._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES(?,'live',1,1)", (obs.mid,))
            bind_material_set(repo._conn, material_set["id"], obs.mid, now_ms=1)
        obs.store = repo
        obs.materials["M_PRODUCT"]["attachment_id"] = attachment["id"]
        selected = repo.spans(material_set["id"], attachment["id"], obs.materials["M_PRODUCT"]["version_ids"][2])["items"][0]
        evidence = read_attachment_source(repo._conn, obs.mid, selected["id"])
        card = {"source_evidence": [evidence], "supporting_facts": [{"source_ref": evidence["source_id"], "text": evidence["quote"]}]}
        assert obs.verify_sources(card)
        evidence["content_hash"] = "wrong"
        assert not obs.verify_sources(card)
    finally:
        library.close()
        obs.client.close()


def test_disabled_or_old_material_cannot_count_as_current_v2_value():
    material = {"version_ids": {1: "v1", 2: "v2"}, "version_texts": {1: "候选", 2: "P2-v2：重新选择同一文件并确认。"},
        "active_version": 2, "enabled": True}
    card = {"analysis_kind": "participation", "topic_id": "topic", "analysis_basis_revision": 1,
        "source_evidence": [{"source_type": "meeting_attachment", "source_id": "ref", "version_id": "v1", "quote": "候选"}],
        "supporting_facts": [{"source_ref": "ref", "text": "候选"}]}
    job = {"request_context": {"topic_id": "topic", "analysis_basis_revision": 1}}
    snapshot = {"discussion": {"work_item_id": "topic", "context": {"analysis_basis_revision": 1}}}
    assert not natural.publication_checks(job, card, snapshot, {"M_PRODUCT": material})["only_explicit_active_material_versions"]
    assert not natural.v2_clause_evidence(card, material)["v2_clause_fact_present"]
    card["source_evidence"][0].update(version_id="v2", quote="重新选择同一文件并确认。")
    card["supporting_facts"][0]["text"] = "重新选择同一文件并确认。"
    assert all(natural.publication_checks(job, card, snapshot, {"M_PRODUCT": material}).values())
    assert natural.v2_clause_evidence(card, material)["v2_clause_fact_present"]
    material["enabled"] = False
    assert not natural.publication_checks(job, card, snapshot, {"M_PRODUCT": material})["only_explicit_active_material_versions"]


def test_non_contiguous_material_quote_cannot_borrow_valid_lines():
    text = "第一条原文。\n中间必须保留的限定。\n第三条原文。"
    material = {"version_ids": {1: "v1"}, "version_texts": {1: text}, "active_version": 1, "enabled": True}
    card = {"analysis_kind": "participation", "topic_id": "t", "analysis_basis_revision": 1,
        "source_evidence": [{"source_type": "meeting_attachment", "version_id": "v1", "quote": "第一条原文。\n第三条原文。"}]}
    job = {"request_context": {"topic_id": "t", "analysis_basis_revision": 1}}
    snapshot = {"discussion": {"work_item_id": "t", "context": {"analysis_basis_revision": 1}}}
    assert not natural.publication_checks(job, card, snapshot, {"m": material})["material_quotes_match_actual_conversion"]
    card["source_evidence"][0]["quote"] = text
    assert natural.publication_checks(job, card, snapshot, {"m": material})["material_quotes_match_actual_conversion"]


def test_signal_arriving_after_collect_crosses_deadline_is_not_accepted(tmp_path, monkeypatch):
    obs = observer(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(natural.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(natural, "signal_state", lambda *_: "matched")
    monkeypatch.setattr(obs, "collect", lambda: clock.__setitem__(0, 61.0))
    try:
        with pytest.raises(runner.AcceptanceStop) as failure:
            obs.signal("bootstrap", "page_ready", maximum=60)
        assert failure.value.code == "ui_ready_timeout"
    finally:
        obs.client.close()


@pytest.mark.parametrize("clock_start,expected_wait,expected_elapsed", [(10.0, 100_000, 100_000), (125.0, 0, 115_000)])
def test_frozen_release_is_earliest_real_clock_target_and_slow_stage_is_not_rewound(clock_start, expected_wait, expected_elapsed):
    clock = [clock_start]
    ticks = []
    result = natural.wait_release(100, origin=10.0, monotonic=lambda: clock[0], tick=lambda: ticks.append(clock[0]),
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert result["waited_ms"] == expected_wait and result["actual_release_elapsed_ms"] == expected_elapsed
    assert ticks and result["clock_advanced"] is False


def test_budget_or_source_failure_during_cadence_wait_stops_before_release():
    released = []

    def fail():
        raise runner.AcceptanceStop("budget_boundary_activated", "fixed budget failure")

    with pytest.raises(runner.AcceptanceStop):
        released.append(natural.wait_release(100, origin=0, tick=fail, monotonic=lambda: 5,
            sleep=lambda *_: pytest.fail("slept after boundary failure")))
    assert not released


def test_unknown_self_asserted_source_cannot_create_known_fact(tmp_path):
    obs = observer(tmp_path)
    obs.public_texts = {"real": "会议原文"}
    card = {"supporting_facts": [{"source_ref": "invented", "text": "自报引文"}],
        "source_evidence": [{"source_type": "document", "source_id": "invented", "quote": "自报引文"}]}
    try:
        assert not obs.verify_sources(card)
        card = {"supporting_facts": [{"source_ref": "real", "text": "会议原文"}]}
        assert obs.verify_sources(card)
    finally:
        obs.client.close()


def test_second_automatic_completed_before_manual_is_uncovered_without_extra_admission(tmp_path, monkeypatch):
    obs = observer(tmp_path)
    jobs = [{"id": f"auto{i}", "kind": "intelligence", "trigger_type": "discussion_update", "created_at_ms": i,
        "status": "succeeded", "output": {"applied": {"coach_decision": {"status": "protected_silent"}}}} for i in (1, 2)]
    monkeypatch.setattr(obs, "collect", lambda: (jobs, {"events": []}))
    monkeypatch.setattr(obs, "http", lambda *_a, **_k: {"traces": []})
    try:
        assert obs.wait_automatic2()["id"] == "auto2"
        assert obs.report["preemption_coverage"]["coverage"] == "uncovered_overlap_missed"
        assert obs.report["preemption_coverage"]["observed_provider_inflight"] is False
        assert not obs.report.get("requests")
    finally:
        obs.client.close()


def test_unknown_pending_job_is_failure_even_before_it_consumes_provider(tmp_path, monkeypatch):
    obs = observer(tmp_path)
    unexpected = {"id": "unknown", "kind": "answer", "status": "pending", "attempts": 0}
    monkeypatch.setattr(runner.ContinuousObserver, "collect", lambda *_: ([unexpected], {}))
    try:
        with pytest.raises(runner.AcceptanceStop) as failed:
            obs.collect()
        assert failed.value.code == "unexpected_lane"
        unexpected.update(status="cancelled", error_class="synthetic_harness_other_lanes_disabled")
        assert obs.collect()[0] == [unexpected]
    finally:
        obs.client.close()


def test_three_uploads_formally_convert_before_meeting_and_remain_disabled(tmp_path, monkeypatch):
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
    from meeting_copilot_web_mvp.meeting_materials import MeetingMaterialsRepository, MeetingMaterialImportService
    from meeting_copilot_web_mvp.meeting_material_jobs import MeetingMaterialJobQueue
    from meeting_copilot_web_mvp.document_import import extract_document
    library = KnowledgeRepository(tmp_path / "uploads.sqlite3")
    repo = MeetingMaterialsRepository(library)
    importer = MeetingMaterialImportService(repo, tmp_path / "assets")
    queue = MeetingMaterialJobQueue(repo)
    obs = observer(tmp_path)
    file_formats = []

    def http(method, route, **kwargs):
        if route == "/v2/material-sets":
            return {"material_set": repo.create_set()}
        parts = route.split("/")
        sid = parts[3]
        if method == "POST":
            filename, content, mime = kwargs["files"]["file"]
            file_formats.append((filename, mime, content.startswith(b"%PDF-")))
            attachment = importer.stage(sid, filename, content, content_type=mime)
            assert queue.finish(queue.claim(), extract_document(filename, content, mime))
            return {"attachment": attachment}
        aid = parts[5]
        if route.endswith("/spans"):
            return repo.spans(sid, aid, parts[7], **kwargs["params"])
        attachment = repo.get_attachment(sid, aid)
        return {"attachment": attachment, "version": repo.get_version(sid, aid, attachment["latest_version_id"])}

    monkeypatch.setattr(obs, "http", http)
    try:
        obs.upload_materials()
        assert file_formats == [("public-import-api.md", "text/markdown", False),
            ("public-import-product.pdf", "application/pdf", True), ("public-result-export.md", "text/markdown", False)]
        assert obs.mid == "" and len(obs.materials) == 3
        assert all(not item["enabled"] for item in obs.materials.values())
        assert obs.report["pdf_lineage"]["formal_conversion_page_locators_verified"]
        assert repo._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
        assert repo._conn.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0] == 0
    finally:
        library.close()
        obs.client.close()


def test_natural_real_http_boundary_allows_only_own_empty_intents_and_formal_ui_ack(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(runner.os, "environ", dict(runner.os.environ))
    runner.os.environ["MEETING_COPILOT_DATA_DIR"] = str(tmp_path / "import-only")
    runner.os.environ["MEETING_CONTINUOUS_PROXY_TOKEN"] = "test-only-loopback-token"
    from meeting_copilot_web_mvp import app as app_module
    monkeypatch.setattr(app_module.llm_service, "REPO_ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(app_module.llm_service, "_RUNTIME_CONFIG_PAYLOAD", None)
    application = runner.build_app(tmp_path, profile=natural.PROFILE, fixture_sha256=natural.FIXTURE_SHA)
    client = TestClient(application)  # No lifespan, workers or model requests.
    store = application.state.v2_persistence
    try:
        identity = client.get("/__acceptance/identity").json()
        assert identity["profile"] == natural.PROFILE and identity["fixture_sha256"] == natural.FIXTURE_SHA
        assert identity["boundary_tool_sha256"] == natural.sha(Path(runner.__file__).read_bytes())
        assert client.post("/v2/meetings", json={"meeting_id": "old"}).status_code == 403
        mid = runner.SCOPE_PREFIX + "natural_http"
        assert client.post("/v2/meetings", json={"meeting_id": mid}).status_code == 201
        assert client.post("/v2/meetings", json={"meeting_id": mid + "other"}).status_code == 403
        assert client.post(f"/v2/meetings/{mid}/coach/request", json={"request": ""}).status_code == 403
        assert client.post(f"/v2/meetings/{mid}/coach/request", json={"intent": "ask_question", "request": "hint"}).status_code == 403
        assert client.post(f"/v2/meetings/{mid}/end").status_code == 403
        assert client.post("/providers/asr/prewarm").status_code == 403
        assert client.post("/v2/traces/nonexistent/ui-rendered", json={"meeting_id": "wrong"}).status_code == 403
        assert store.list_jobs(meeting_id=mid) == []
        assert store._conn.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0] == 0
    finally:
        client.close()
        store.close()
