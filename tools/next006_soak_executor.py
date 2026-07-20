#!/usr/bin/env python3
"""Run repeatable wall-clock soak and recovery scenarios for NEXT-006."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "next006-soak"
SCHEMA_VERSION = "meeting_copilot.next006_soak_report.v1"
SCHEMA_PATH = REPO_ROOT / "artifacts" / "schemas" / "next006-soak-report.schema.json"
ACCEPTANCE_DURATIONS = {3600.0: "one_hour", 10800.0: "three_hour"}
REQUIRED_FAULTS = ("network_disconnect", "backend_crash", "disk_write_failure")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


@dataclass(frozen=True)
class FaultSchedule:
    fault_type: str
    at_seconds: float
    hold_seconds: float


@dataclass
class SoakConfig:
    duration_seconds: float
    sample_interval_seconds: float
    backend_command: list[str]
    health_url: str
    metrics_url: str
    network_control_url: str
    network_probe_url: str
    disk_control_url: str
    disk_probe_url: str
    output_root: Path = DEFAULT_ARTIFACT_ROOT
    run_id: str | None = None
    mode: str = "test"
    system_under_test: str = "fault_fixture"
    evidence_kind: str = "fixture"
    queue_json_path: str = "queue_depth"
    latency_json_path: str = "latency_ms"
    startup_timeout_seconds: float = 15.0
    recovery_timeout_seconds: float = 15.0
    request_timeout_seconds: float = 2.0
    schedules: list[FaultSchedule] = field(default_factory=list)


@dataclass
class ManagedProcess:
    command: list[str]
    stdout_path: Path
    stderr_path: Path
    process: subprocess.Popen[bytes] | None = None
    starts: int = 0
    initial_pid: int | None = None

    def start(self) -> int:
        self.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout = self.stdout_path.open("ab")
        stderr = self.stderr_path.open("ab")
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        finally:
            stdout.close()
            stderr.close()
        self.starts += 1
        if self.initial_pid is None:
            self.initial_pid = self.process.pid
        return self.process.pid

    @property
    def pid(self) -> int | None:
        if self.process is None or self.process.poll() is not None:
            return None
        return self.process.pid

    def kill_for_fault(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        return self.process.poll() is not None

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)


def default_schedules(
    duration_seconds: float, hold_seconds: float | None = None
) -> list[FaultSchedule]:
    hold = (
        hold_seconds
        if hold_seconds is not None
        else min(15.0, max(0.1, duration_seconds * 0.03))
    )
    return [
        FaultSchedule("network_disconnect", duration_seconds * 0.20, hold),
        FaultSchedule("backend_crash", duration_seconds * 0.45, hold),
        FaultSchedule("disk_write_failure", duration_seconds * 0.70, hold),
    ]


def validate_config(config: SoakConfig) -> None:
    if config.duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if config.sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds must be positive")
    if config.mode not in {"test", "acceptance"}:
        raise ValueError("mode must be test or acceptance")
    if config.evidence_kind not in {"fixture", "system_under_test"}:
        raise ValueError("evidence_kind must be fixture or system_under_test")
    if not config.backend_command:
        raise ValueError("backend_command must not be empty")
    if config.run_id is not None and not RUN_ID_PATTERN.fullmatch(config.run_id):
        raise ValueError("run_id contains unsafe characters")
    if config.startup_timeout_seconds <= 0 or config.recovery_timeout_seconds <= 0:
        raise ValueError("startup and recovery timeouts must be positive")
    if config.request_timeout_seconds <= 0:
        raise ValueError("request timeout must be positive")
    seen: set[str] = set()
    for schedule in config.schedules:
        if schedule.fault_type not in REQUIRED_FAULTS:
            raise ValueError(f"unsupported fault_type: {schedule.fault_type}")
        if schedule.fault_type in seen:
            raise ValueError(f"duplicate fault_type: {schedule.fault_type}")
        seen.add(schedule.fault_type)
        if schedule.at_seconds < 0 or schedule.hold_seconds < 0:
            raise ValueError("fault schedule values must be non-negative")
        if schedule.at_seconds + schedule.hold_seconds >= config.duration_seconds:
            raise ValueError(
                f"fault must recover before duration ends: {schedule.fault_type}"
            )
    missing = sorted(set(REQUIRED_FAULTS) - seen)
    if missing:
        raise ValueError(f"missing required fault schedules: {', '.join(missing)}")


def run_soak(
    config: SoakConfig,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validate_config(config)
    run_id = config.run_id or datetime.now(timezone.utc).strftime(
        "%Y%m%d-%H%M%S-next006-soak"
    )
    run_root = _resolve_run_root(config.output_root, run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    manager = ManagedProcess(
        command=config.backend_command,
        stdout_path=run_root / "backend.stdout.log",
        stderr_path=run_root / "backend.stderr.log",
    )
    samples: list[dict[str, Any]] = []
    fault_records = [
        _new_fault_record(item)
        for item in sorted(config.schedules, key=lambda item: item.at_seconds)
    ]
    run_errors: list[str] = []
    started_at = _now()
    started_wall = time.time()
    start = 0.0
    elapsed = 0.0

    try:
        manager.start()
        if not _wait_for_available(
            config.health_url,
            config,
            timeout_seconds=config.startup_timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        ):
            run_errors.append("backend_startup_health_failed")
        start = monotonic()
        deadline = start + config.duration_seconds
        next_sample = start

        while True:
            now = monotonic()
            elapsed = max(0.0, now - start)
            for schedule, record in zip(
                sorted(config.schedules, key=lambda item: item.at_seconds),
                fault_records,
            ):
                if record["status"] == "pending" and elapsed >= schedule.at_seconds:
                    _inject_fault(
                        record, manager=manager, config=config, elapsed=elapsed
                    )
                if (
                    record["status"] == "active"
                    and elapsed >= schedule.at_seconds + schedule.hold_seconds
                ):
                    _recover_fault(
                        record,
                        manager=manager,
                        config=config,
                        elapsed=elapsed,
                        monotonic=monotonic,
                        sleep=sleep,
                    )

            if now >= next_sample:
                samples.append(
                    _collect_sample(
                        len(samples), elapsed, manager=manager, config=config
                    )
                )
                skipped = max(
                    1,
                    math.floor((now - next_sample) / config.sample_interval_seconds)
                    + 1,
                )
                next_sample += skipped * config.sample_interval_seconds

            if now >= deadline:
                break
            event_times = [next_sample, deadline]
            for schedule, record in zip(
                sorted(config.schedules, key=lambda item: item.at_seconds),
                fault_records,
            ):
                if record["status"] == "pending":
                    event_times.append(start + schedule.at_seconds)
                elif record["status"] == "active":
                    event_times.append(
                        start + schedule.at_seconds + schedule.hold_seconds
                    )
            wake_at = min(value for value in event_times if value > now)
            sleep(min(max(0.001, wake_at - now), 0.1))

        elapsed = max(0.0, monotonic() - start)
        samples.append(
            _collect_sample(len(samples), elapsed, manager=manager, config=config)
        )
    except Exception as exc:  # Keep a report when orchestration itself fails.
        run_errors.append(f"executor_exception:{type(exc).__name__}:{exc}")
        if start:
            elapsed = max(0.0, monotonic() - start)
    finally:
        final_health = _http_json(
            "GET", config.health_url, timeout=config.request_timeout_seconds
        )
        manager.stop()

    finished_wall = time.time()
    summary = _summarize(
        samples=samples,
        fault_records=fault_records,
        elapsed_seconds=elapsed,
        duration_seconds=config.duration_seconds,
        final_health=final_health,
    )
    acceptance = evaluate_acceptance(
        config=config, summary=summary, faults=fault_records, run_errors=run_errors
    )
    mechanisms_passed = not run_errors and all(
        item["status"] == "recovered" for item in fault_records
    )
    if acceptance["acceptance_eligible"]:
        verdict = "acceptance_candidate"
    elif mechanisms_passed:
        verdict = (
            "short_mode_pass_not_acceptance"
            if config.mode == "test"
            else "completed_not_acceptance_eligible"
        )
    else:
        verdict = "failed"
    report = {
        "$schema": _schema_reference(),
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": _now(),
        "mode": config.mode,
        "system_under_test": {
            "id": config.system_under_test,
            "evidence_kind": config.evidence_kind,
            "managed_subprocess": True,
            "command_executable": Path(config.backend_command[0]).name,
            "initial_pid": manager.initial_pid,
            "process_start_count": manager.starts,
        },
        "configuration": {
            "duration_seconds": config.duration_seconds,
            "sample_interval_seconds": config.sample_interval_seconds,
            "queue_json_path": config.queue_json_path,
            "latency_json_path": config.latency_json_path,
            "startup_timeout_seconds": config.startup_timeout_seconds,
            "recovery_timeout_seconds": config.recovery_timeout_seconds,
            "request_timeout_seconds": config.request_timeout_seconds,
            "required_faults": list(REQUIRED_FAULTS),
        },
        "timing": {
            "monotonic_elapsed_seconds": round(elapsed, 6),
            "wall_clock_elapsed_seconds": round(
                max(0.0, finished_wall - started_wall), 6
            ),
            "completed_full_duration": summary["completed_full_duration"],
        },
        "samples": samples,
        "faults": fault_records,
        "summary": summary,
        "run_errors": run_errors,
        "acceptance": acceptance,
        "acceptance_eligible": acceptance["acceptance_eligible"],
        "verdict": verdict,
    }
    _write_json_atomic(run_root / "report.json", report)
    return {
        "artifact_root": str(run_root),
        "report_path": str(run_root / "report.json"),
        "report": report,
    }


def evaluate_acceptance(
    *,
    config: SoakConfig,
    summary: dict[str, Any],
    faults: list[dict[str, Any]],
    run_errors: list[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    target = _acceptance_target(config.duration_seconds)
    if config.mode != "acceptance":
        reasons.append("test_mode_not_acceptance")
    if config.evidence_kind != "system_under_test":
        reasons.append("fixture_evidence_not_acceptance_eligible")
    if target is None:
        reasons.append("duration_is_not_exactly_1h_or_3h")
    if not summary.get("completed_full_duration"):
        reasons.append("wall_clock_duration_not_completed")
    fault_by_type = {str(item.get("fault_type")): item for item in faults}
    for fault_type in REQUIRED_FAULTS:
        if fault_by_type.get(fault_type, {}).get("status") != "recovered":
            reasons.append(f"required_fault_not_recovered:{fault_type}")
    coverage = summary.get("metric_coverage") or {}
    for metric in ("rss", "cpu", "queue_depth", "service_latency"):
        if float(coverage.get(metric, 0.0)) < 0.8:
            reasons.append(f"metric_coverage_below_80_percent:{metric}")
    if not summary.get("backend_healthy_at_end"):
        reasons.append("backend_unhealthy_at_end")
    if run_errors:
        reasons.append("executor_errors_present")
    reasons = sorted(set(reasons))
    return {
        "acceptance_eligible": not reasons,
        "target": target or "none",
        "required_duration_seconds": [3600, 10800],
        "minimum_metric_coverage": 0.8,
        "reasons": reasons,
    }


def _new_fault_record(schedule: FaultSchedule) -> dict[str, Any]:
    return {
        "fault_type": schedule.fault_type,
        "scheduled_at_seconds": round(schedule.at_seconds, 6),
        "hold_seconds": round(schedule.hold_seconds, 6),
        "status": "pending",
        "injected_at_seconds": None,
        "recovered_at_seconds": None,
        "injection": {"attempted": False, "observed": False, "detail": "not_run"},
        "recovery": {
            "attempted": False,
            "passed": False,
            "latency_ms": None,
            "detail": "not_run",
        },
    }


def _inject_fault(
    record: dict[str, Any],
    *,
    manager: ManagedProcess,
    config: SoakConfig,
    elapsed: float,
) -> None:
    record["injected_at_seconds"] = round(elapsed, 6)
    record["injection"]["attempted"] = True
    fault_type = record["fault_type"]
    if fault_type == "backend_crash":
        observed = manager.kill_for_fault()
        detail = (
            "backend_process_exited" if observed else "backend_process_did_not_exit"
        )
    elif fault_type == "network_disconnect":
        control = _post_control(
            config.network_control_url,
            enabled=False,
            timeout=config.request_timeout_seconds,
        )
        probe = _http_json(
            "GET", config.network_probe_url, timeout=config.request_timeout_seconds
        )
        observed = control["ok"] and not probe["ok"]
        detail = f"control_status={control['status_code']};probe_status={probe['status_code']}"
    elif fault_type == "disk_write_failure":
        control = _post_control(
            config.disk_control_url,
            enabled=False,
            timeout=config.request_timeout_seconds,
        )
        probe = _http_json(
            "POST",
            config.disk_probe_url,
            payload={"probe": "fault_injection"},
            timeout=config.request_timeout_seconds,
        )
        observed = control["ok"] and not probe["ok"]
        detail = f"control_status={control['status_code']};probe_status={probe['status_code']}"
    else:
        observed = False
        detail = "unsupported_fault"
    record["injection"].update({"observed": observed, "detail": detail})
    record["status"] = "active" if observed else "failed"


def _recover_fault(
    record: dict[str, Any],
    *,
    manager: ManagedProcess,
    config: SoakConfig,
    elapsed: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    recovery_started = monotonic()
    record["recovery"]["attempted"] = True
    fault_type = record["fault_type"]
    if fault_type == "backend_crash":
        manager.start()
        passed = _wait_for_available(
            config.health_url, config, monotonic=monotonic, sleep=sleep
        )
        detail = "backend_health_restored" if passed else "backend_health_not_restored"
    elif fault_type == "network_disconnect":
        control = _post_control(
            config.network_control_url,
            enabled=True,
            timeout=config.request_timeout_seconds,
        )
        passed = control["ok"] and _wait_for_available(
            config.network_probe_url,
            config,
            monotonic=monotonic,
            sleep=sleep,
        )
        detail = "network_probe_restored" if passed else "network_probe_not_restored"
    elif fault_type == "disk_write_failure":
        control = _post_control(
            config.disk_control_url,
            enabled=True,
            timeout=config.request_timeout_seconds,
        )
        probe = _http_json(
            "POST",
            config.disk_probe_url,
            payload={"probe": "recovery", "nonce": time.time_ns()},
            timeout=config.request_timeout_seconds,
        )
        passed = control["ok"] and probe["ok"]
        detail = "disk_write_restored" if passed else "disk_write_not_restored"
    else:
        passed = False
        detail = "unsupported_fault"
    latency_ms = round((monotonic() - recovery_started) * 1000, 3)
    record["recovered_at_seconds"] = round(elapsed + latency_ms / 1000.0, 6)
    record["recovery"].update(
        {"passed": passed, "latency_ms": latency_ms, "detail": detail}
    )
    record["status"] = "recovered" if passed else "failed"


def _collect_sample(
    sequence: int, elapsed: float, *, manager: ManagedProcess, config: SoakConfig
) -> dict[str, Any]:
    health = _http_json(
        "GET", config.health_url, timeout=config.request_timeout_seconds
    )
    metrics = _http_json(
        "GET", config.metrics_url, timeout=config.request_timeout_seconds
    )
    payload = metrics.get("payload") if isinstance(metrics.get("payload"), dict) else {}
    process = _read_process_tree_metrics(manager.pid)
    return {
        "sequence": sequence,
        "captured_at": _now(),
        "elapsed_seconds": round(elapsed, 6),
        "process": process,
        "health": {
            "ok": health["ok"],
            "status_code": health["status_code"],
            "probe_latency_ms": health["latency_ms"],
        },
        "metrics": {
            "endpoint_ok": metrics["ok"],
            "queue_depth": _extract_number(payload, config.queue_json_path),
            "service_latency_ms": _extract_number(payload, config.latency_json_path),
            "probe_latency_ms": metrics["latency_ms"],
        },
    }


def _read_process_tree_metrics(root_pid: int | None) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "root_pid": root_pid,
        "process_count": 0,
        "rss_bytes": None,
        "cpu_percent": None,
    }
    if root_pid is None:
        return unavailable
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,%cpu="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return unavailable
    rows: dict[int, tuple[int, int, float]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        try:
            pid, ppid, rss_kib = (int(fields[0]), int(fields[1]), int(fields[2]))
            cpu = float(fields[3].replace(",", "."))
        except ValueError:
            continue
        rows[pid] = (ppid, rss_kib, cpu)
    if root_pid not in rows:
        return unavailable
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _rss, _cpu) in rows.items():
            if ppid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return {
        "available": True,
        "root_pid": root_pid,
        "process_count": len(selected),
        "rss_bytes": sum(rows[pid][1] for pid in selected) * 1024,
        "cpu_percent": round(sum(rows[pid][2] for pid in selected), 3),
    }


def _summarize(
    *,
    samples: list[dict[str, Any]],
    fault_records: list[dict[str, Any]],
    elapsed_seconds: float,
    duration_seconds: float,
    final_health: dict[str, Any],
) -> dict[str, Any]:
    rss = _sample_values(samples, "process", "rss_bytes")
    cpu = _sample_values(samples, "process", "cpu_percent")
    queue_depth = _sample_values(samples, "metrics", "queue_depth")
    service_latency = _sample_values(samples, "metrics", "service_latency_ms")
    total = len(samples)
    return {
        "sample_count": total,
        "completed_full_duration": elapsed_seconds >= duration_seconds,
        "backend_healthy_at_end": bool(final_health.get("ok")),
        "healthy_sample_count": sum(1 for item in samples if item["health"]["ok"]),
        "faults_recovered": sum(
            1 for item in fault_records if item["status"] == "recovered"
        ),
        "faults_required": len(REQUIRED_FAULTS),
        "metric_coverage": {
            "rss": _coverage(len(rss), total),
            "cpu": _coverage(len(cpu), total),
            "queue_depth": _coverage(len(queue_depth), total),
            "service_latency": _coverage(len(service_latency), total),
        },
        "rss_bytes": _distribution(rss),
        "cpu_percent": _distribution(cpu),
        "queue_depth": _distribution(queue_depth),
        "service_latency_ms": _distribution(service_latency),
    }


def _sample_values(
    samples: list[dict[str, Any]], section: str, key: str
) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = sample.get(section, {}).get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            values.append(float(value))
    return values


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "average": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "average": round(sum(ordered) / len(ordered), 3),
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
    }


def _percentile(ordered: list[float], percentile: float) -> float:
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 3)


def _coverage(valid: int, total: int) -> float:
    return round(valid / total, 4) if total else 0.0


def _extract_number(payload: dict[str, Any], path: str) -> float | None:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _http_json(
    method: str, url: str, *, timeout: float, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if raw is not None else {}
    request = Request(url, data=raw, headers=headers, method=method)
    started = time.monotonic()
    status_code: int | None = None
    parsed: Any = None
    error: str | None = None
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.status
            body = response.read(1024 * 1024)
            if body:
                parsed = json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        status_code = exc.code
        error = f"HTTPError:{exc.code}"
    except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = f"{type(exc).__name__}:{exc}"
    return {
        "ok": status_code is not None and 200 <= status_code < 300,
        "status_code": status_code,
        "latency_ms": round((time.monotonic() - started) * 1000, 3),
        "payload": parsed,
        "error": error,
    }


def _post_control(url: str, *, enabled: bool, timeout: float) -> dict[str, Any]:
    return _http_json("POST", url, payload={"enabled": enabled}, timeout=timeout)


def _wait_for_available(
    url: str,
    config: SoakConfig,
    *,
    timeout_seconds: float | None = None,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    deadline = monotonic() + (
        config.recovery_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    while monotonic() < deadline:
        if _http_json("GET", url, timeout=config.request_timeout_seconds)["ok"]:
            return True
        sleep(min(0.05, max(0.001, deadline - monotonic())))
    return False


def _acceptance_target(duration_seconds: float) -> str | None:
    for required, target in ACCEPTANCE_DURATIONS.items():
        if math.isclose(duration_seconds, required, rel_tol=0.0, abs_tol=1e-9):
            return target
    return None


def _schema_reference() -> str:
    try:
        return SCHEMA_PATH.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(SCHEMA_PATH)


def _resolve_run_root(output_root: Path, run_id: str) -> Path:
    resolved = output_root.resolve()
    return resolved if resolved.name == run_id else resolved / run_id


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run NEXT-006 wall-clock soak and recovery scenarios."
    )
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--sample-interval-seconds", type=float, default=5.0)
    parser.add_argument("--mode", choices=("test", "acceptance"), default="test")
    parser.add_argument("--system-under-test", default="fault_fixture")
    parser.add_argument(
        "--evidence-kind", choices=("fixture", "system_under_test"), default="fixture"
    )
    parser.add_argument(
        "--backend-command",
        required=True,
        help="Managed backend command, parsed with shlex (no shell).",
    )
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--metrics-url", required=True)
    parser.add_argument("--network-control-url", required=True)
    parser.add_argument("--network-probe-url", required=True)
    parser.add_argument("--disk-control-url", required=True)
    parser.add_argument("--disk-probe-url", required=True)
    parser.add_argument("--queue-json-path", default="queue_depth")
    parser.add_argument("--latency-json-path", default="latency_ms")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--run-id")
    parser.add_argument("--fault-hold-seconds", type=float)
    parser.add_argument("--network-at-seconds", type=float)
    parser.add_argument("--backend-crash-at-seconds", type=float)
    parser.add_argument("--disk-failure-at-seconds", type=float)
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--recovery-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=2.0)
    return parser


def config_from_args(args: argparse.Namespace) -> SoakConfig:
    schedules = default_schedules(args.duration_seconds, args.fault_hold_seconds)
    overrides = {
        "network_disconnect": args.network_at_seconds,
        "backend_crash": args.backend_crash_at_seconds,
        "disk_write_failure": args.disk_failure_at_seconds,
    }
    schedules = [
        FaultSchedule(
            item.fault_type,
            overrides[item.fault_type]
            if overrides[item.fault_type] is not None
            else item.at_seconds,
            item.hold_seconds,
        )
        for item in schedules
    ]
    return SoakConfig(
        duration_seconds=args.duration_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        backend_command=shlex.split(args.backend_command),
        health_url=args.health_url,
        metrics_url=args.metrics_url,
        network_control_url=args.network_control_url,
        network_probe_url=args.network_probe_url,
        disk_control_url=args.disk_control_url,
        disk_probe_url=args.disk_probe_url,
        output_root=args.output_root,
        run_id=args.run_id,
        mode=args.mode,
        system_under_test=args.system_under_test,
        evidence_kind=args.evidence_kind,
        queue_json_path=args.queue_json_path,
        latency_json_path=args.latency_json_path,
        startup_timeout_seconds=args.startup_timeout_seconds,
        recovery_timeout_seconds=args.recovery_timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        schedules=schedules,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
        result = run_soak(config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(result["report_path"])
    return 0 if result["report"]["verdict"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
