from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "selection_postcheck_runner", ROOT / "tools/participation_quote_selection_postcheck.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_dry_plan_does_not_dispatch_or_resume_prior_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.support, "execute", lambda *_: pytest.fail("dry plan dispatched"))
    directory = tmp_path / "fresh"
    assert runner.main(["--run-dir", str(directory)]) == 0
    result = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    assert not result["executed"] and result["profile"] == "participation_quote_selection.v1"
    assert result["cumulative_scope_budget"] == {
        "prior_actual_upstream_posts": 4, "new_maximum_posts": 5,
        "combined_maximum_posts": 9, "prior_ledgers_reset_or_reused": False}
    assert result["budget"]["automatic"] == 0 and result["budget"]["attempts_per_host_job"] == 1
    assert result["ui_signal_contract"]["bootstrap_page_ready_seconds"] == 60
    assert runner.support.LIMITS == {"discussion_state": 1, "automatic": 0, "manual": 2}
    assert runner.support.SCOPE_PREFIX == "participation_quote_selection_"
    diagnostics = runner.runner.base.tool_diagnostics({"output": {"coach": {"agent_metrics": {
        "tool_errors": [{"tool": "submit_intervention", "code": "quote_selection_invalid", "raw": "do not log"},
                        {"tool": "unknown", "code": "untrusted free text"}]}}}})
    assert diagnostics["tool_errors"] == [
        {"tool": "submit_intervention", "code": "quote_selection_invalid"},
        {"tool": "unclassified", "code": "unclassified"}]
    assert "do not log" not in json.dumps(diagnostics)
    assert not (directory / "runtime").exists() and not (directory / "proxy-ledger.json").exists()
    with pytest.raises(FileExistsError):
        runner.main(["--run-dir", str(directory)])


def _prior(tmp_path, monkeypatch, *, actual_count=3, remaining=2, closed=True):
    path = tmp_path / "artifacts/realtime-participation-20261007/previous/report.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"proxy_budget": {"reserved_forward_attempts": actual_count,
                                               "remaining": remaining},
                                "scope_closed_without_paid_postprocessing": closed}), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(runner.support, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "PRIOR", (("previous", digest, 3, 2, "failure"),))
    return path


def test_changed_prior_report_stops_before_dispatch_or_new_output(tmp_path, monkeypatch):
    prior = _prior(tmp_path, monkeypatch)
    prior.write_text(prior.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setattr(runner.support, "execute", lambda *_: pytest.fail("changed prior dispatched"))
    destination = tmp_path / "new"
    with pytest.raises(RuntimeError, match="prior_failure_report_changed"):
        runner.main(["--execute", "--run-dir", str(destination)])
    assert not destination.exists()


@pytest.mark.parametrize("values", [{"actual_count": 4}, {"remaining": 3}, {"closed": False}])
def test_inconsistent_prior_accounting_or_open_scope_never_dispatches(tmp_path, monkeypatch, values):
    prior = _prior(tmp_path, monkeypatch, **values)
    before = prior.read_bytes()
    monkeypatch.setattr(runner.support, "execute", lambda *_: pytest.fail("unsafe lineage dispatched"))
    destination = tmp_path / "new"
    with pytest.raises(RuntimeError, match="prior_failure_accounting_changed|prior_scope_not_closed"):
        runner.main(["--execute", "--run-dir", str(destination)])
    assert not destination.exists() and prior.read_bytes() == before


def test_closed_exact_prior_reports_are_verified_without_mutation(tmp_path, monkeypatch):
    prior = _prior(tmp_path, monkeypatch)
    before = prior.read_bytes()
    runner.verify_prior_ledgers()
    assert prior.read_bytes() == before
