from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("coach_material_policy_fixture", ROOT / "tools/coach_material_lifecycle_fixture.py")
fixture_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_tool
SPEC.loader.exec_module(fixture_tool)


@pytest.mark.parametrize("global_enabled", [True, False])
def test_snapshot_policy_off_is_distinct_from_global_disabled_and_keeps_manual_lifecycle(tmp_path, monkeypatch, global_enabled):
    # A normal typed host publication, real material evidence and HTTP snapshot;
    # the isolated Sidecar transport is fixed/no-network, not a paid model.
    with fixture_tool.build_fixture(tmp_path / "public-policy") as (app, controller):
        original = controller.snapshot()["coach_intervention"]
        original_jobs = [(job["id"], job["status"], job["attempts"]) for job in app.state.v2_persistence.list_jobs(meeting_id=controller.meeting_id)]
        monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1" if global_enabled else "0")
        snapshot = controller.snapshot()
        capability = snapshot["runtime"]["ai"]["capabilities"]["proactive_suggestions"]
        assert capability["state"] == "paused"
        if global_enabled:
            assert capability["label"] == "自动建议已关闭"
            assert capability["reason"] == "disabled_by_meeting_policy"
            assert "仍可主动提问" in capability["detail"]
            assert snapshot["coach_intervention"] == original
            assert snapshot["coach_decision"]["trigger_type"] == "user_request"
            assert snapshot["coach_history"]
        else:
            assert capability["label"] == "实时教练已关闭"
            assert capability["reason"] == "disabled_by_setting"
            assert capability["detail"] is None
            assert snapshot["coach_intervention"] is None
            assert snapshot["coach_decision"] is None
            assert snapshot["coach_history"] == []
        # Policy/global labeling neither suppresses the independent state wire
        # nor enqueues, cancels, retries or changes the published TTL/data.
        assert "discussion_state" in snapshot["runtime"]["ai"]["capabilities"]
        assert original_jobs == [(job["id"], job["status"], job["attempts"]) for job in app.state.v2_persistence.list_jobs(meeting_id=controller.meeting_id)]
        assert len(controller.evaluations) == 1
