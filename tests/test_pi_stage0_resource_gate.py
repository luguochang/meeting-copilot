from __future__ import annotations

from pathlib import Path
import json
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import pi_stage0_resource_gate as gate  # noqa: E402


def test_process_table_and_memory_tool_parsers():
    processes = gate.parse_process_table(
        """
  100     1  2048  1.5 /usr/bin/python3 python3 server.py --token=secret-value
  101   100  4096 12.0 /usr/bin/node node pi_coach_bridge/src/bridge.mjs
"""
    )

    assert processes[100].rss_bytes == 2 * gate.MIB
    assert processes[101].ppid == 100
    assert gate.descendant_pids(processes, 100) == {101}
    assert "secret-value" not in gate._safe_command_hint(processes[100])
    assert gate.parse_footprint_output(
        "    phys_footprint: 1073741824 B\n    phys_footprint_peak: 2147483648 B\n"
    ) == {
        "physical_footprint_bytes": 1_073_741_824,
        "physical_footprint_peak_bytes": 2_147_483_648,
    }
    assert gate.parse_vmmap_swap_bytes("TOTAL 5.0G 2.0G 1.0G 512.0M 0K\n") == 512 * gate.MIB


def _sample(
    *,
    phase: str,
    current_mib: int | None,
    peak_mib: int | None,
    idle_elapsed_seconds: float | None = None,
    unloaded: bool = False,
    server_pid: int = 100,
) -> dict:
    worker = {
        "process_running": not unloaded,
        "process_ready": not unloaded,
        "idle_unload_count": 1 if unloaded else 0,
        "last_stop_reason": "idle_timeout" if unloaded else None,
    }
    role_totals = {}
    if not unloaded:
        role_totals["offline_refiner"] = {
            "process_count": 1,
            "rss_bytes": (current_mib or 0) * gate.MIB,
            "physical_footprint_bytes": (current_mib or 0) * gate.MIB,
            "physical_footprint_peak_bytes": (peak_mib or 0) * gate.MIB,
            "swapped_bytes": 0,
            "physical_detail_complete": current_mib is not None and peak_mib is not None,
        }
    sample = {
        "phase": phase,
        "server_pid": server_pid,
        "role_totals": role_totals,
        "asr_runtime": {"offline_refinement": {"worker": worker}},
    }
    if idle_elapsed_seconds is not None:
        sample["idle_elapsed_seconds"] = idle_elapsed_seconds
    return sample


def _online_only_sample(
    *,
    phase: str,
    idle_elapsed_seconds: float | None = None,
) -> dict:
    sample = _sample(
        phase=phase,
        current_mib=None,
        peak_mib=None,
        idle_elapsed_seconds=idle_elapsed_seconds,
        unloaded=True,
    )
    sample["asr_runtime"]["offline_refinement"] = {
        "capability": {
            "status": "ready",
            "realtime_policy": {
                "schema_version": "realtime_refiner_policy.v1",
                "mode": "online_only",
                "source": "default_resource_guard",
                "realtime_refinement_enabled": False,
                "prewarm_enabled": False,
                "degradation_reason": gate.ONLINE_ONLY_REFINEMENT_REASON,
            },
        },
        "worker": {
            "spawned": False,
            "process_running": False,
            "process_ready": False,
            "process_start_count": 0,
            "idle_unload_count": 0,
            "last_stop_reason": None,
        },
    }
    return sample


def test_resource_gate_passes_only_with_measured_budget_and_idle_unload():
    report = gate.evaluate_resource_gate(
        [
            _sample(phase="workload", current_mib=1400, peak_mib=2800),
            _sample(
                phase="idle",
                current_mib=None,
                peak_mib=None,
                idle_elapsed_seconds=120,
                unloaded=True,
            ),
        ]
    )

    assert report["status"] == "go"
    assert report["passed"] is True
    assert report["observed"]["offline_refiner_steady_bytes"] == 1400 * gate.MIB
    assert report["observed"]["idle_unloaded"] is True


def test_resource_gate_accepts_only_audited_online_only_zero_process_contract():
    report = gate.evaluate_resource_gate(
        [
            _online_only_sample(phase="workload"),
            _online_only_sample(phase="idle", idle_elapsed_seconds=120),
        ]
    )

    assert report["status"] == "go"
    assert report["passed"] is True
    assert report["thresholds"] == {
        "offline_refiner_steady_mib": 1536.0,
        "offline_refiner_cold_peak_mib": 3072.0,
        "idle_unload_seconds": 120.0,
    }
    assert report["observed"]["resource_strategy"] == "online_only_zero_process"
    assert report["observed"]["offline_refiner_steady_bytes"] == 0
    assert report["observed"]["offline_refiner_cold_peak_bytes"] == 0
    assert report["observed"]["idle_unloaded"] is True


def test_resource_gate_rejects_refiner_process_under_online_only_policy():
    workload = _online_only_sample(phase="workload")
    workload["asr_runtime"]["offline_refinement"]["worker"].update(
        {
            "spawned": True,
            "process_running": True,
            "process_ready": True,
            "process_start_count": 1,
        }
    )
    workload["role_totals"]["offline_refiner"] = {
        "process_count": 1,
        "rss_bytes": 1400 * gate.MIB,
        "physical_footprint_bytes": 1400 * gate.MIB,
        "physical_footprint_peak_bytes": 2800 * gate.MIB,
        "swapped_bytes": 0,
        "physical_detail_complete": True,
    }

    report = gate.evaluate_resource_gate(
        [
            workload,
            _online_only_sample(phase="idle", idle_elapsed_seconds=120),
        ]
    )

    assert report["status"] == "no_go"
    assert report["passed"] is False
    assert report["blockers"] == [
        "offline_refiner_spawned_under_online_only_policy"
    ]
    assert report["observed"]["offline_refiner_steady_bytes"] == 1400 * gate.MIB
    assert report["observed"]["offline_refiner_cold_peak_bytes"] == 2800 * gate.MIB


def test_resource_gate_blocks_incomplete_online_only_policy_evidence():
    workload = _online_only_sample(phase="workload")
    del workload["asr_runtime"]["offline_refinement"]["capability"][
        "realtime_policy"
    ]["degradation_reason"]

    report = gate.evaluate_resource_gate(
        [
            workload,
            _online_only_sample(phase="idle", idle_elapsed_seconds=120),
        ]
    )

    assert report["status"] == "blocked"
    assert report["passed"] is False
    assert report["blockers"] == [
        "measurement_contract_invalid:online_only_resource_policy"
    ]


def test_resource_gate_reports_threshold_and_idle_failures():
    report = gate.evaluate_resource_gate(
        [
            _sample(phase="workload", current_mib=1800, peak_mib=3400),
            _sample(
                phase="idle",
                current_mib=1800,
                peak_mib=3400,
                idle_elapsed_seconds=120,
                unloaded=False,
            ),
        ]
    )

    assert report["status"] == "no_go"
    assert report["blockers"] == [
        "offline_refiner_steady_limit_exceeded",
        "offline_refiner_cold_peak_limit_exceeded",
        "offline_refiner_idle_unload_failed",
    ]


def test_resource_gate_is_blocked_without_machine_readable_measurements():
    report = gate.evaluate_resource_gate([])

    assert report["status"] == "blocked"
    assert report["passed"] is False
    assert set(report["blockers"]) == {
        "measurement_missing:offline_refiner_steady_physical_footprint",
        "measurement_missing:offline_refiner_cold_peak_physical_footprint",
        "measurement_missing:idle_120s",
    }


def test_resource_gate_rejects_samples_from_more_than_one_backend_process():
    report = gate.evaluate_resource_gate(
        [
            _sample(phase="workload", current_mib=1400, peak_mib=2800, server_pid=100),
            _sample(
                phase="idle",
                current_mib=None,
                peak_mib=None,
                idle_elapsed_seconds=120,
                unloaded=True,
                server_pid=200,
            ),
        ]
    )

    assert report["status"] == "blocked"
    assert report["passed"] is False
    assert "measurement_identity_changed:server_pid" in report["blockers"]


def test_prior_workload_loader_pins_samples_to_current_backend_pid(tmp_path):
    report_path = tmp_path / "mixed-report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": gate.SCHEMA_VERSION,
                "created_at": "2026-08-20T00:00:00Z",
                "samples": [
                    _sample(phase="workload", current_mib=1400, peak_mib=2800, server_pid=100),
                    _sample(phase="workload", current_mib=1500, peak_mib=2900, server_pid=200),
                    _sample(
                        phase="idle",
                        current_mib=None,
                        peak_mib=None,
                        idle_elapsed_seconds=120,
                        unloaded=True,
                        server_pid=100,
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )

    samples, provenance = gate._load_prior_workload_samples(report_path, server_pid=100)

    assert len(samples) == 1
    assert samples[0]["server_pid"] == 100
    assert samples[0]["phase"] == "workload"
    assert samples[0]["seeded_from_prior_report"] is True
    assert provenance["accepted_sample_count"] == 1
    assert provenance["rejected_sample_count"] == 2
