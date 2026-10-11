from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("single_list_postcheck", ROOT / "tools/participation_quote_single_list_postcheck.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_dry_plan_uses_fresh_scope_and_does_not_reset_previous_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.support, "execute", lambda *_: pytest.fail("dry run dispatched"))
    directory = tmp_path / "fresh"
    assert runner.main(["--run-dir", str(directory)]) == 0
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert plan["profile"] == "participation_quote_single_list.v1" and not plan["executed"]
    assert plan["cumulative_scope_budget"]["prior_actual_upstream_posts"] == 9
    assert plan["cumulative_scope_budget"]["combined_maximum_posts"] == 14
    assert len(plan["prior_failures"]) == 3
    assert plan["prior_failures"][-1]["remaining_forwards_not_reused"] == 0
    assert runner.support.SCOPE_PREFIX == "participation_quote_single_list_"
    assert runner.support.HOST_LIMIT == 3 and runner.support.UPSTREAM_LIMIT == 5
    diagnostics = runner.runner.runner.base.tool_diagnostics({"output": {"coach": {"agent_metrics": {
        "tool_errors": [{"tool": "submit_intervention", "code": "quote_selection_unknown_id",
                         "arguments": "must not be saved"}]}}}})
    assert diagnostics["tool_errors"] == [{"tool": "submit_intervention", "code": "quote_selection_unknown_id"}]
    assert "must not be saved" not in json.dumps(diagnostics)
    assert not (directory / "runtime").exists() and not (directory / "proxy-ledger.json").exists()


def _requests():
    return [{"intent": intent, "own_outcome": {"own_decision_status": "intervention"},
             "tool_diagnostics": {"turns": 1, "tool_calls": 1,
                                  "tool_names": ["submit_intervention"], "tool_errors": []}}
            for intent in ("ask_question", "formulate_response")]


def test_two_first_turn_cards_pass_efficiency_without_claiming_latency():
    result = runner.first_turn_efficiency(_requests())
    assert result["passed"] and result["no_latency_percentile_claim"]


@pytest.mark.parametrize("kind", ["repair", "lookup", "tool_error", "missing_metrics", "missing_intent"])
def test_valid_final_cards_do_not_mask_incomplete_or_repaired_first_turn(kind):
    requests = _requests()
    if kind == "repair":
        requests[0]["tool_diagnostics"]["turns"] = 2
    elif kind == "lookup":
        requests[0]["tool_diagnostics"]["tool_names"] = ["search_project_documents", "submit_intervention"]
    elif kind == "tool_error":
        requests[0]["tool_diagnostics"]["tool_errors"] = [{"code": "quote_selection_invalid"}]
    elif kind == "missing_metrics":
        requests[0]["tool_diagnostics"] = {}
    else:
        requests.pop()
    result = runner.first_turn_efficiency(requests)
    assert not result["passed"] and result["functional_contract_independent"]


def test_efficiency_failure_preserves_functional_pass_and_does_not_dispatch_again(tmp_path, monkeypatch):
    calls = []
    report = {"automated_contract_passed": True, "first_turn_efficiency": {"passed": False}}
    monkeypatch.setattr(runner, "_ORIGINAL_EXECUTE", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(runner.support, "update_json", lambda *_: None)
    assert runner.execute(tmp_path, tmp_path / "report.json", report) == 1
    assert report["automated_contract_passed"] is True
    assert report["stop_reason"] == "functional_cards_valid_first_turn_efficiency_failed_no_additional_paid_run"
    assert len(calls) == 1
