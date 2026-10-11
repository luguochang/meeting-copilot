from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


TOOLS = Path(__file__).resolve().parents[4] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("automatic_acceptance", TOOLS / "participation_automatic_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_automatic_plan_does_not_open_database_or_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.value, "ROOT", tmp_path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run attempted actual execution")
    monkeypatch.setattr(runner, "execute", forbidden)
    assert runner.main(["--report", "artifacts/automatic-plan.json"]) == 0
    report = json.loads((tmp_path / "artifacts/automatic-plan.json").read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["budget"] == {"state_attempts": 1, "automatic_pi_attempts": 1, "manual_attempts": 0, "retry": 0}
    assert any("global timer is not verified" in text for text in report["harness_overrides"])


def test_automatic_called_silence_is_not_a_useful_contribution_pass():
    report = {"state": {"status": "succeeded", "discussion": {"context": {"current_solution": "候选方案"}}},
        "automatic": {"valid_intervention": False, "validated_silence": True, "current_card_for_request": False, "fact_checks": []},
        "same_basis_zero_job": True, "cas_retracted_card": True}
    result = runner.review(report)
    assert result["automation_contract_passed"] is False
    assert "own_automatic_contribution_missing" in result["hard_failures"]
    assert result["global_timer_verified"] is False


def test_useful_schema_card_cannot_hide_missing_coalescing_or_retraction():
    report = {"state": {"status": "succeeded", "discussion": {"context": {"current_solution": "候选方案"}}},
        "automatic": {"valid_intervention": True, "current_card_for_request": True, "fact_checks": [{"verbatim": True}]},
        "same_basis_zero_job": False, "cas_retracted_card": False}
    assert runner.review(report)["hard_failures"] == ["same_basis_not_coalesced", "corrected_basis_did_not_retract_card"]
