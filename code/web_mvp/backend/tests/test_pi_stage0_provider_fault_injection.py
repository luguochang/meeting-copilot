from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import pi_stage0_provider_fault_injection as fault_runner  # noqa: E402


def _report_sha256(report: dict) -> str:
    unhashed = dict(report)
    unhashed.pop("report_sha256")
    return hashlib.sha256(
        json.dumps(
            unhashed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_loopback_socket_fault_matrix_is_classified_and_redacted() -> None:
    report = asyncio.run(
        fault_runner.run_fault_injection(
            concurrency=4,
            timeout_seconds=0.12,
            timeout_delay_seconds=0.35,
        )
    )

    assert report["schema_version"] == fault_runner.REPORT_SCHEMA_VERSION
    assert report["transport"] == "loopback_threading_http_server"
    assert report["passed"] is True
    assert report["fixture"]["request_count"] == len(fault_runner.DEFAULT_SCENARIOS)
    assert report["fixture"]["authenticated_request_count"] == len(
        fault_runner.DEFAULT_SCENARIOS
    )
    assert report["outcome_counts"] == {
        "cancelled": 1,
        "error": 6,
        "success": 1,
    }
    assert report["provider_category_counts"] == {
        "empty_response": 1,
        "protocol": 1,
        "provider_server": 1,
        "rate_limit": 1,
        "timeout": 1,
        "transport": 1,
    }
    observations = {item["scenario"]: item for item in report["observations"]}
    assert observations["rate_limit"]["status_code"] == 429
    assert observations["rate_limit"]["retry_after_ms"] == 1_000
    assert observations["server_error"]["status_code"] == 503
    assert observations["timeout"]["elapsed_ms"] < 500
    assert observations["cancel"]["elapsed_ms"] < 250
    assert report["report_sha256"] == _report_sha256(report)

    encoded = json.dumps(report, ensure_ascii=False).casefold()
    for forbidden in (
        "authorization",
        "bearer ",
        "api_key",
        "request_body",
        "response_body",
        "messages",
        "stage0 fixture",
    ):
        assert forbidden not in encoded


def test_fault_matrix_accepts_repeated_failures_without_pool_leak() -> None:
    scenarios = [
        "timeout",
        "transport",
        "rate_limit",
        "server_error",
    ] * 3
    report = asyncio.run(
        fault_runner.run_fault_injection(
            scenarios,
            concurrency=6,
            timeout_seconds=0.1,
            timeout_delay_seconds=0.3,
        )
    )

    assert report["passed"] is True
    assert report["fixture"]["request_count"] == len(scenarios)
    assert report["fixture"]["authenticated_request_count"] == len(scenarios)
    assert all(check["passed"] for check in report["checks"])
    assert report["provider_category_counts"] == {
        "provider_server": 3,
        "rate_limit": 3,
        "timeout": 3,
        "transport": 3,
    }
