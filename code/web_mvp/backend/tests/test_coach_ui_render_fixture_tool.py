from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import sys

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("coach_ui_render_fixture", ROOT / "tools/coach_ui_render_fixture.py")
fixture_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_tool
SPEC.loader.exec_module(fixture_tool)


def test_plan_discloses_fake_provider_and_prevents_background_or_capture():
    plan = fixture_tool.fixture_plan()
    assert plan["real_model_calls"] == 0 and plan["audio_capture"] is False
    assert plan["lifespan_background_workers"] is False
    assert plan["input"]["characters"] < 120 and plan["input"]["segments"] == 1
    assert plan["policy"] == {"live_analysis_profile": "participation", "proactive_suggestion_policy": "off",
        "automatic_enabled": 0, "discussion_state_enabled": 0}


def test_isolated_normal_handler_has_current_receipt_but_no_ack_or_outbound_model(tmp_path):
    with fixture_tool.build_fixture(tmp_path / "runtime") as (app, report):
        assert report["real_model_calls"] == 0 and report["fake_sidecar_evaluations"] == 1
        assert report["ui_rendered_before_browser"] is False and report["trace_created_by"] == "normal typed handler"
        assert report["seed_jobs"] and all(job["status"] == "cancelled" and job["attempts"] == 0 for job in report["seed_jobs"])
        with socket.socket() as outgoing:
            with pytest.raises(AssertionError, match="No remote outbound network"):
                outgoing.connect(("203.0.113.1", 12345))
        client = TestClient(app)
        snapshot = client.get(f"/v2/meetings/{report['meeting_id']}/snapshot").json()
        assert snapshot["coach_intervention"]["ui_render_receipt"] == report["receipt"]
        preparation = client.get(f"/v2/meetings/{report['meeting_id']}/preparation").json()
        assert preparation["proactive_suggestion_policy"] == "off"
        trace = app.state.pipeline_traces.get(report["job_id"])
        assert "ui_rendered" not in trace.stage_marks
        client.close()


def test_nonempty_runtime_is_never_reused(tmp_path):
    (tmp_path / "existing.txt").write_text("retained", encoding="utf-8")
    with pytest.raises(ValueError, match="new empty isolated"):
        with fixture_tool.build_fixture(tmp_path):
            pytest.fail("existing runtime should be rejected")
