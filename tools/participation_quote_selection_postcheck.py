"""Default dry plan: one bounded postcheck of the host quote-selection fix.

Reuse the fixed-basis runner and guards; keep both earlier failure ledgers intact.
Only public synthetic text is transmitted. No recording or automatic coaching.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

_RUNNER_PATH = Path(__file__).with_name("participation_quote_single_basis_postcheck.py")
_SPEC = importlib.util.spec_from_file_location("_selection_single_basis_runner", _RUNNER_PATH)
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)
support = runner.support
PROFILE = "participation_quote_selection.v1"
DEFAULT_RUN_DIR = support.ROOT / "artifacts/realtime-participation-20261007/quote-selection-r01"
support.SCOPE_PREFIX = "participation_quote_selection_"
support.BOUNDARY_SCHEMA = "participation_quote_selection_boundary.v1"
runner.base.PROFILE = PROFILE
runner.base.TOOL_ERROR_CODES |= {
    "quote_source_changed", "quote_selection_invalid", "quote_selection_budget"}
PRIOR = (
    ("quote-postcheck-r01", "ae41f30c48a4956272456c45bd45ae172438e3806c7d3189e61f6d9843ef38fd", 1, 4,
     "operator_ui_ready_timeout_before_pi"),
    ("quote-single-basis-r01", "b3c85123ed63bee4d1caa2b4e8bf7621139150c3e990ff1b8b896e87f85a9014", 3, 2,
     "pi_quote_error_then_validated_silence"),
)


def plan(run_dir: Path) -> dict:
    result = runner.plan(run_dir)
    result.pop("prior_operator_handshake_failure", None)
    result.update(schema_version="participation_quote_selection_acceptance.v1", profile=PROFILE,
        prior_failures=[{"report": f"artifacts/realtime-participation-20261007/{name}/report.json",
                         "report_sha256": digest, "actual_upstream_posts": count,
                         "remaining_forwards_not_reused": remaining, "classification": classification}
                        for name, digest, count, remaining, classification in PRIOR],
        cumulative_scope_budget={"prior_actual_upstream_posts": 4, "new_maximum_posts": 5,
                                 "combined_maximum_posts": 9, "prior_ledgers_reset_or_reused": False},
        evaluation_limits="One post-fix fixed-basis two-intent run; not a percentile or representative meeting value test.",
        source_contract="host-authorized quote selections hydrate the existing typed card; original validators remain")
    return result


def verify_prior_ledgers() -> None:
    for name, expected, count, remaining, _ in PRIOR:
        path = support.ROOT / f"artifacts/realtime-participation-20261007/{name}/report.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("prior_failure_report_changed_do_not_execute")
        report = json.loads(path.read_text(encoding="utf-8"))
        budget = report["proxy_budget"]
        if budget["reserved_forward_attempts"] != count or budget["remaining"] != remaining:
            raise RuntimeError("prior_failure_accounting_changed_do_not_execute")
        if not report["scope_closed_without_paid_postprocessing"]:
            raise RuntimeError("prior_scope_not_closed_do_not_execute")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--serve-app", action="store_true")
    modes.add_argument("--serve-proxy", action="store_true")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    if args.serve_proxy:
        support.serve_proxy(run_dir)
        return 0
    if args.serve_app:
        runner.base.serve_app(run_dir)
        return 0
    if args.execute:
        verify_prior_ledgers()
    output = (args.report or run_dir / ("report.json" if args.execute else "plan.json")).resolve()
    report = plan(run_dir)
    report.update(executed=bool(args.execute), tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  inherited_runner_sha256=hashlib.sha256(_RUNNER_PATH.read_bytes()).hexdigest(),
                  inherited_quote_tool_sha256=hashlib.sha256(runner._BASE_PATH.read_bytes()).hexdigest(),
                  support_tool_sha256=hashlib.sha256(runner.base._SUPPORT_PATH.read_bytes()).hexdigest())
    support.fresh_json(output, report)
    if args.execute:
        return support.execute(run_dir, output, report)
    print(json.dumps({"executed": False, "profile": PROFILE, "plan": str(output), "budget": report["budget"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
