from __future__ import annotations

import copy
import asyncio
import importlib.util
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("coach_material_lifecycle_fixture", ROOT / "tools/coach_material_lifecycle_fixture.py")
fixture_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_tool
SPEC.loader.exec_module(fixture_tool)


def test_dry_plan_discloses_no_model_no_device_and_unmodified_clock():
    plan = fixture_tool.fixture_plan()
    assert plan["real_model_calls"] == 0 and plan["audio_capture"] is False and plan["asr_started"] is False
    assert plan["lifespan_background_workers"] is False and plan["production_code_modified"] is False
    assert plan["policy"] == {"live_analysis_profile": "participation", "proactive_suggestion_policy": "off",
                              "automatic_enabled": 0, "discussion_state_enabled": 0}
    assert plan["publication_budget"]["initial"] == 1
    assert "unmodified" in plan["clock"] and "no refresh" in plan["clock"]
    assert fixture_tool.MATERIAL_V1 != fixture_tool.MATERIAL_V2


def test_formal_material_v1_save_v2_enable_withdraw_history_and_new_explicit_publication(tmp_path):
    with fixture_tool.build_fixture(tmp_path / "runtime") as (app, controller):
        client = controller.client
        prefix = f"/v2/material-sets/{controller.set_id}/attachments/{controller.attachment_id}"
        first = controller.snapshot()["coach_intervention"]
        old_decision, old_job = first["decision_id"], first["job_id"]
        old_coverage = copy.deepcopy(first["material_context"])
        source = first["source_evidence"][0]
        v1_id = source["version_id"]
        assert source["meeting_id"] == controller.meeting_id and source["attachment_id"] == controller.attachment_id
        assert first["supporting_facts"][0]["source_ref"] == source["source_id"]
        assert first["supporting_facts"][0]["text"] == source["quote"]
        coverage = old_coverage["attachment_catalog"][0]["coverage"]
        assert coverage["provided_span_count"] == coverage["authorized_span_count"] > 0
        assert coverage["provided_characters"] == coverage["authorized_characters"] > 0
        assert coverage["complete_read"] is True
        assert app.state.meeting_material_job_runner._task is None
        assert "ui_rendered" not in app.state.pipeline_traces.get(old_job).stage_marks

        detail = client.get(prefix).json()
        saved = client.post(f"{prefix}/versions", json={"content": fixture_tool.MATERIAL_V2,
                            "expected_revision": detail["attachment"]["revision"], "base_version_id": v1_id})
        assert saved.status_code == 201, saved.text
        v2 = saved.json()
        assert v2["version"]["number"] == 2 and v2["attachment"]["active_version_id"] == v1_id
        assert controller.snapshot()["coach_intervention"]["decision_id"] == old_decision
        assert controller.snapshot()["coach_intervention"]["material_context"] == old_coverage

        enabled = client.put(f"{prefix}/usage", json={"enabled": True, "version_id": v2["version"]["id"],
                             "selected_span_ids": None, "expected_revision": v2["attachment"]["revision"]})
        assert enabled.status_code == 200, enabled.text
        after = controller.snapshot()
        assert after["coach_intervention"] is None
        old_history = next(item for item in after["coach_history"] if item["decision_id"] == old_decision)
        assert old_history["lifecycle_action"] == "retract" or old_history["status"] == "stale"
        assert old_history["material_context"] == old_coverage
        assert "ui_render_receipt" not in old_history
        historical = client.get(f"/v2/meetings/{controller.meeting_id}/sources/{source['source_id']}",
                                params={"allow_historical": "true"})
        assert historical.status_code == 200, historical.text
        assert historical.json()["source"]["source_state"] == "stale"
        assert historical.json()["source"]["version_id"] == v1_id
        assert historical.json()["source"]["quote"] == source["quote"]

        queued = client.post(f"/v2/meetings/{controller.meeting_id}/coach/request", json={"intent": "compare_options"},
                             headers={"Idempotency-Key": "explicit-material-v2"})
        assert queued.status_code == 202, queued.text
        controller.process_once()
        latest = controller.snapshot()["coach_intervention"]
        assert latest["decision_id"] != old_decision
        assert latest["source_evidence"][0]["version_id"] == v2["version"]["id"]
        assert latest["material_context"]["material_context_revision"] > old_coverage["material_context_revision"]
        assert len(controller.publications) == len(controller.evaluations) == 2
        assert controller.process_once() is None
        assert len(controller.publications) == 2
        assert "ui_rendered" not in app.state.pipeline_traces.get(latest["job_id"]).stage_marks
        assert all(job["attempts"] == 0 for job in app.state.v2_persistence.list_jobs(meeting_id=controller.meeting_id)
                   if job.get("error_class") == "synthetic_harness_other_lanes_disabled")
        with socket.socket() as outgoing:
            with pytest.raises(AssertionError, match="No remote outbound network"):
                outgoing.connect(("203.0.113.1", 12345))


def test_explicit_new_v1_card_keeps_distinct_normal_publication_identity(tmp_path):
    with fixture_tool.build_fixture(tmp_path / "runtime") as (app, controller):
        before = controller.snapshot()["coach_intervention"]
        queued = controller.client.post(f"/v2/meetings/{controller.meeting_id}/coach/request",
                                        json={"intent": "compare_options"}, headers={"Idempotency-Key": "explicit-v1-update"})
        assert queued.status_code == 202, queued.text
        controller.process_once()
        after = controller.snapshot()["coach_intervention"]
        assert after["job_id"] != before["job_id"] and after["say_this"] != before["say_this"]
        assert after["source_evidence"] == before["source_evidence"]
        assert after["material_context"] == before["material_context"]
        assert after["ui_render_receipt"]["event_seq"] > before["ui_render_receipt"]["event_seq"]
        assert all("ui_rendered" not in app.state.pipeline_traces.get(item["job_id"]).stage_marks for item in controller.publications)


def test_fake_transport_still_uses_real_registry_and_rejects_a_forged_attachment_version(tmp_path):
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeScopeError

    with fixture_tool.build_fixture(tmp_path / "runtime") as (app, controller):
        source = copy.deepcopy(controller.snapshot()["coach_intervention"]["source_evidence"][0])
        source["version_id"] = "foreign-version"
        payload = {"session_id": controller.meeting_id,
                   "context": {"trigger_type": "user_request", "document_evidence": [source]}}
        with pytest.raises((KnowledgeScopeError, ValueError)):
            asyncio.run(app.state.pi_coach_runtime.evaluate(payload))
        assert len(controller.evaluations) == 1


def test_existing_data_and_foreign_scope_are_never_replayed(tmp_path):
    used = tmp_path / "used"
    used.mkdir()
    (used / "retained.txt").write_text("retained", encoding="utf-8")
    with pytest.raises(ValueError, match="new empty isolated"):
        with fixture_tool.build_fixture(used):
            pytest.fail("Existing data must be rejected")
    with fixture_tool.build_fixture(tmp_path / "new") as (app, controller):
        app.state.v2_persistence.create_meeting(meeting_id="foreign-synthetic", title="foreign", now_ms=1)
        with pytest.raises(AssertionError, match="refuses another meeting scope"):
            controller.process_once()
        assert len(controller.evaluations) == 1
