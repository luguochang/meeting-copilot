import importlib.util
import json
from pathlib import Path
import socket
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_PATH = REPO_ROOT / "tools/next006_soak_executor.py"
FIXTURE_PATH = REPO_ROOT / "tools/next006_fault_fixture.py"
SCHEMA_PATH = REPO_ROOT / "artifacts/schemas/next006-soak-report.schema.json"


def load_executor():
    spec = importlib.util.spec_from_file_location(
        "next006_soak_executor", EXECUTOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def make_config(
    tool,
    tmp_path: Path,
    *,
    duration: float = 1.3,
    mode: str = "test",
    evidence_kind: str = "fixture",
):
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    return tool.SoakConfig(
        duration_seconds=duration,
        sample_interval_seconds=0.1,
        backend_command=[
            sys.executable,
            str(FIXTURE_PATH),
            "--port",
            str(port),
            "--data-dir",
            str(tmp_path / "fixture-data"),
            "--queue-depth",
            "3",
            "--latency-ms",
            "8.5",
        ],
        health_url=f"{base_url}/health",
        metrics_url=f"{base_url}/metrics",
        network_control_url=f"{base_url}/control/network",
        network_probe_url=f"{base_url}/network/dependency",
        disk_control_url=f"{base_url}/control/disk",
        disk_probe_url=f"{base_url}/disk/write",
        output_root=tmp_path / "artifacts",
        run_id="short-recovery-test",
        mode=mode,
        system_under_test="next006_fault_fixture",
        evidence_kind=evidence_kind,
        startup_timeout_seconds=3.0,
        recovery_timeout_seconds=3.0,
        request_timeout_seconds=0.5,
        schedules=[
            tool.FaultSchedule("network_disconnect", 0.20, 0.08),
            tool.FaultSchedule("backend_crash", 0.50, 0.08),
            tool.FaultSchedule("disk_write_failure", 0.82, 0.08),
        ],
    )


def test_short_soak_exercises_faults_collects_metrics_and_is_not_acceptance(tmp_path):
    tool = load_executor()
    result = tool.run_soak(make_config(tool, tmp_path))
    report = result["report"]

    assert report["verdict"] == "short_mode_pass_not_acceptance"
    assert report["acceptance_eligible"] is False
    assert report["acceptance"]["acceptance_eligible"] is False
    assert report["acceptance"]["target"] == "none"
    assert "test_mode_not_acceptance" in report["acceptance"]["reasons"]
    assert "duration_is_not_exactly_1h_or_3h" in report["acceptance"]["reasons"]
    assert "fixture_evidence_not_acceptance_eligible" in report["acceptance"]["reasons"]
    assert report["timing"]["monotonic_elapsed_seconds"] >= 1.3
    assert report["timing"]["completed_full_duration"] is True

    assert [item["fault_type"] for item in report["faults"]] == list(
        tool.REQUIRED_FAULTS
    )
    assert all(item["status"] == "recovered" for item in report["faults"])
    assert all(item["injection"]["observed"] is True for item in report["faults"])
    assert all(item["recovery"]["passed"] is True for item in report["faults"])
    assert report["system_under_test"]["process_start_count"] == 2

    summary = report["summary"]
    assert summary["sample_count"] >= 10
    assert summary["rss_bytes"]["count"] > 0
    assert summary["rss_bytes"]["max"] > 0
    assert summary["cpu_percent"]["count"] > 0
    assert summary["queue_depth"]["average"] == 3.0
    assert summary["service_latency_ms"]["average"] == 8.5
    assert summary["backend_healthy_at_end"] is True
    assert (tmp_path / "fixture-data/disk-probe.json").is_file()

    written = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert written == report
    assert Path(result["artifact_root"], "backend.stdout.log").is_file()
    assert Path(result["artifact_root"], "backend.stderr.log").is_file()


def test_acceptance_evaluator_requires_mode_exact_duration_coverage_and_recovery(
    tmp_path,
):
    tool = load_executor()
    faults = [
        {"fault_type": fault_type, "status": "recovered"}
        for fault_type in tool.REQUIRED_FAULTS
    ]
    summary = {
        "completed_full_duration": True,
        "backend_healthy_at_end": True,
        "metric_coverage": {
            "rss": 1.0,
            "cpu": 1.0,
            "queue_depth": 1.0,
            "service_latency": 1.0,
        },
    }

    short = make_config(
        tool,
        tmp_path,
        duration=10.0,
        mode="acceptance",
        evidence_kind="system_under_test",
    )
    short_acceptance = tool.evaluate_acceptance(
        config=short,
        summary=summary,
        faults=faults,
        run_errors=[],
    )
    assert short_acceptance["acceptance_eligible"] is False
    assert short_acceptance["target"] == "none"
    assert short_acceptance["reasons"] == ["duration_is_not_exactly_1h_or_3h"]

    one_hour_test_mode = make_config(
        tool,
        tmp_path,
        duration=3600.0,
        mode="test",
        evidence_kind="system_under_test",
    )
    test_mode_acceptance = tool.evaluate_acceptance(
        config=one_hour_test_mode,
        summary=summary,
        faults=faults,
        run_errors=[],
    )
    assert test_mode_acceptance["acceptance_eligible"] is False
    assert test_mode_acceptance["target"] == "one_hour"
    assert test_mode_acceptance["reasons"] == ["test_mode_not_acceptance"]

    fixture_one_hour = make_config(tool, tmp_path, duration=3600.0, mode="acceptance")
    fixture_acceptance = tool.evaluate_acceptance(
        config=fixture_one_hour,
        summary=summary,
        faults=faults,
        run_errors=[],
    )
    assert fixture_acceptance["acceptance_eligible"] is False
    assert fixture_acceptance["reasons"] == ["fixture_evidence_not_acceptance_eligible"]

    one_hour_acceptance = make_config(
        tool,
        tmp_path,
        duration=3600.0,
        mode="acceptance",
        evidence_kind="system_under_test",
    )
    eligible = tool.evaluate_acceptance(
        config=one_hour_acceptance,
        summary=summary,
        faults=faults,
        run_errors=[],
    )
    assert eligible == {
        "acceptance_eligible": True,
        "target": "one_hour",
        "required_duration_seconds": [3600, 10800],
        "minimum_metric_coverage": 0.8,
        "reasons": [],
    }

    summary["metric_coverage"]["queue_depth"] = 0.79
    faults[1]["status"] = "failed"
    rejected = tool.evaluate_acceptance(
        config=one_hour_acceptance,
        summary=summary,
        faults=faults,
        run_errors=["sample_error"],
    )
    assert rejected["acceptance_eligible"] is False
    assert "metric_coverage_below_80_percent:queue_depth" in rejected["reasons"]
    assert "required_fault_not_recovered:backend_crash" in rejected["reasons"]
    assert "executor_errors_present" in rejected["reasons"]


def test_config_validation_rejects_incomplete_or_out_of_bounds_fault_plan(tmp_path):
    tool = load_executor()
    config = make_config(tool, tmp_path)
    config.schedules = config.schedules[:2]
    with pytest.raises(ValueError, match="missing required fault schedules"):
        tool.validate_config(config)

    config = make_config(tool, tmp_path)
    config.schedules[-1] = tool.FaultSchedule("disk_write_failure", 1.25, 0.08)
    with pytest.raises(ValueError, match="recover before duration ends"):
        tool.validate_config(config)

    config = make_config(tool, tmp_path)
    config.run_id = "../escape"
    with pytest.raises(ValueError, match="run_id"):
        tool.validate_config(config)

    config = make_config(tool, tmp_path)
    config.evidence_kind = "claimed_real_without_contract"
    with pytest.raises(ValueError, match="evidence_kind"):
        tool.validate_config(config)


def test_default_fault_plan_and_cli_are_duration_parameterized(tmp_path):
    tool = load_executor()
    schedules = tool.default_schedules(3600.0)
    assert [(item.fault_type, item.at_seconds) for item in schedules] == [
        ("network_disconnect", 720.0),
        ("backend_crash", 1620.0),
        ("disk_write_failure", 2520.0),
    ]
    assert all(item.hold_seconds == 15.0 for item in schedules)

    args = tool.build_parser().parse_args(
        [
            "--duration-seconds",
            "10800",
            "--sample-interval-seconds",
            "10",
            "--mode",
            "acceptance",
            "--evidence-kind",
            "system_under_test",
            "--backend-command",
            f"{sys.executable} {FIXTURE_PATH} --port 12345 --data-dir {tmp_path}",
            "--health-url",
            "http://127.0.0.1:12345/health",
            "--metrics-url",
            "http://127.0.0.1:12345/metrics",
            "--network-control-url",
            "http://127.0.0.1:12345/control/network",
            "--network-probe-url",
            "http://127.0.0.1:12345/network/dependency",
            "--disk-control-url",
            "http://127.0.0.1:12345/control/disk",
            "--disk-probe-url",
            "http://127.0.0.1:12345/disk/write",
        ]
    )
    config = tool.config_from_args(args)
    assert config.duration_seconds == 10800
    assert config.sample_interval_seconds == 10
    assert config.mode == "acceptance"
    assert config.evidence_kind == "system_under_test"
    assert tool._acceptance_target(config.duration_seconds) == "three_hour"


def test_artifact_schema_requires_explicit_acceptance_boundary():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == (
        "meeting_copilot.next006_soak_report.v1"
    )
    assert "acceptance_eligible" in schema["required"]
    assert "acceptance" in schema["required"]
    assert len(schema["allOf"]) == 3
    assert schema["$defs"]["acceptance"]["properties"]["required_duration_seconds"][
        "const"
    ] == [
        3600,
        10800,
    ]
    assert schema["$defs"]["configuration"]["properties"]["required_faults"][
        "const"
    ] == [
        "network_disconnect",
        "backend_crash",
        "disk_write_failure",
    ]
