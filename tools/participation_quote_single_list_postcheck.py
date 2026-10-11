"""Single bounded post-fix run; functional cards and first-turn efficiency stay separate."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

_RUNNER_PATH = Path(__file__).with_name("participation_quote_selection_postcheck.py")
_SPEC = importlib.util.spec_from_file_location("_single_list_selection_runner", _RUNNER_PATH)
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)
support = runner.support
PROFILE = "participation_quote_single_list.v1"
runner.PROFILE = runner.runner.PROFILE = runner.runner.base.PROFILE = PROFILE
runner.DEFAULT_RUN_DIR = support.ROOT / "artifacts/realtime-participation-20261007/quote-single-list-r01"
support.SCOPE_PREFIX = "participation_quote_single_list_"
support.BOUNDARY_SCHEMA = "participation_quote_single_list_boundary.v1"
runner.runner.base.TOOL_ERROR_CODES |= {
    f"quote_selection_{reason}" for reason in (
        "duplicate", "unknown_id", "no_transcript", "same_doc_source",
        "obsolete_contract", "budget", "source_changed")}
runner.PRIOR += (("quote-selection-r01", "be577efa0e9c807f98f1757c0f3fa832207ec2a1a56c34d053d2d707fbab8efa",
                  5, 0, "valid_cards_but_both_first_submissions_rejected"),)
_ORIGINAL_PLAN = runner.plan
_ORIGINAL_EXECUTE = support.execute


def plan(run_dir: Path) -> dict:
    result = _ORIGINAL_PLAN(run_dir)
    result.update(schema_version="participation_quote_single_list_acceptance.v1",
                  cumulative_scope_budget={"prior_actual_upstream_posts": 9, "new_maximum_posts": 5,
                                           "combined_maximum_posts": 14, "prior_ledgers_reset_or_reused": False},
                  source_contract="one citation choice list derives existing evidence and facts; original validators remain",
                  first_turn_efficiency_requirement="both intents use exactly one SDK turn, only submit_intervention, no tool errors",
                  entry_tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  inherited_entry_tool=str(_RUNNER_PATH.relative_to(support.ROOT)))
    return result


def first_turn_efficiency(requests: list[dict]) -> dict:
    checks = {"both_intents_observed": len(requests) == 2
              and {item.get("intent") for item in requests} == {"ask_question", "formulate_response"}}
    for item in requests:
        metrics = item.get("tool_diagnostics") or {}
        checks[item.get("intent", "unknown") + "_first_turn_no_tool_errors"] = (
            (item.get("own_outcome") or {}).get("own_decision_status") == "intervention"
            and metrics.get("turns") == 1 and metrics.get("tool_calls") == 1
            and metrics.get("tool_names") == ["submit_intervention"]
            and metrics.get("tool_errors") == [])
    return {"checks": checks, "passed": all(checks.values()),
            "functional_contract_independent": True, "no_latency_percentile_claim": True}


class SingleListObserver(runner.runner.SingleBasisObserver):
    def finish(self):
        try:
            super().finish()
        finally:
            self.report["first_turn_efficiency"] = first_turn_efficiency(self.report.get("requests", []))


def execute(run_dir: Path, report_path: Path, report: dict) -> int:
    outcome = _ORIGINAL_EXECUTE(run_dir, report_path, report)
    if outcome == 0 and not report.get("first_turn_efficiency", {}).get("passed"):
        report["stop_reason"] = "functional_cards_valid_first_turn_efficiency_failed_no_additional_paid_run"
        support.update_json(report_path, report)
        return 1
    return outcome


runner.plan = plan
support.ContinuousObserver = SingleListObserver
support.execute = execute


def main(argv=None):
    return runner.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
