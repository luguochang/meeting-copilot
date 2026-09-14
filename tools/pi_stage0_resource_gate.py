#!/usr/bin/env python3
"""Measure the Stage 0 Pi/FunASR process tree and enforce resource gates."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCHEMA_VERSION = "meeting_copilot.pi_stage0_resource_gate.v1"
MIB = 1024 * 1024
DEFAULT_STEADY_LIMIT_MIB = 1536.0
DEFAULT_COLD_PEAK_LIMIT_MIB = 3072.0
DEFAULT_IDLE_SECONDS = 120.0
ONLINE_ONLY_REFINEMENT_REASON = "offline_refinement_bypassed_by_resource_policy"
SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization|api[_-]?key|token|secret)(?:=|\s+)[^\s]+|bearer\s+[^\s]+|sk-[A-Za-z0-9._-]{8,}"
)


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    rss_bytes: int
    cpu_percent: float
    executable: str
    command: str


def parse_process_table(raw: str) -> dict[int, ProcessRow]:
    rows: dict[int, ProcessRow] = {}
    for line in raw.splitlines():
        fields = line.strip().split(None, 5)
        if len(fields) < 5:
            continue
        try:
            pid = int(fields[0])
            ppid = int(fields[1])
            rss_bytes = max(0, int(fields[2])) * 1024
            cpu_percent = max(0.0, float(fields[3]))
        except ValueError:
            continue
        executable = fields[4]
        command = fields[5] if len(fields) == 6 else executable
        rows[pid] = ProcessRow(
            pid=pid,
            ppid=ppid,
            rss_bytes=rss_bytes,
            cpu_percent=cpu_percent,
            executable=executable,
            command=command,
        )
    return rows


def read_process_table() -> dict[int, ProcessRow]:
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,rss=,%cpu=,comm=,args="],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return parse_process_table(completed.stdout)


def descendant_pids(processes: Mapping[int, ProcessRow], root_pid: int) -> set[int]:
    descendants: set[int] = set()
    frontier = {root_pid}
    while frontier:
        children = {
            row.pid
            for row in processes.values()
            if row.ppid in frontier and row.pid not in descendants
        }
        descendants.update(children)
        frontier = children
    return descendants


def _runtime_worker_pids(runtime: Mapping[str, Any]) -> tuple[set[int], set[int]]:
    resident = runtime.get("resident") if isinstance(runtime.get("resident"), Mapping) else {}
    realtime_pids = {
        int(pid)
        for pid in [
            resident.get("pid"),
            *[
                worker.get("pid")
                for worker in resident.get("workers") or []
                if isinstance(worker, Mapping)
            ],
        ]
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
    }
    offline = (
        runtime.get("offline_refinement")
        if isinstance(runtime.get("offline_refinement"), Mapping)
        else {}
    )
    worker = offline.get("worker") if isinstance(offline.get("worker"), Mapping) else {}
    offline_pid = worker.get("pid")
    offline_pids = {
        int(offline_pid)
        for _ in (0,)
        if isinstance(offline_pid, int) and not isinstance(offline_pid, bool) and offline_pid > 0
    }
    return realtime_pids, offline_pids


def classify_processes(
    processes: Mapping[int, ProcessRow],
    *,
    server_pid: int,
    runtime: Mapping[str, Any],
) -> dict[int, str]:
    realtime_pids, offline_pids = _runtime_worker_pids(runtime)
    descendants = descendant_pids(processes, server_pid)
    roles: dict[int, str] = {}
    if server_pid in processes:
        roles[server_pid] = "backend"
    for pid in sorted(descendants | realtime_pids | offline_pids):
        row = processes.get(pid)
        if pid in realtime_pids:
            roles[pid] = "realtime_asr"
        elif pid in offline_pids:
            roles[pid] = "offline_refiner"
        elif row is not None and (
            "pi_coach_bridge" in row.command
            or "pi-coach-bridge" in row.command
            or (Path(row.executable).name.startswith("node") and "bridge.mjs" in row.command)
        ):
            roles[pid] = "pi_node"
        elif row is not None:
            roles[pid] = "backend_child_other"
    return roles


def parse_footprint_output(raw: str) -> dict[str, int | None]:
    current = re.search(r"(?m)^\s*phys_footprint:\s*(\d+)\s+B\s*$", raw)
    peak = re.search(r"(?m)^\s*phys_footprint_peak:\s*(\d+)\s+B\s*$", raw)
    return {
        "physical_footprint_bytes": int(current.group(1)) if current else None,
        "physical_footprint_peak_bytes": int(peak.group(1)) if peak else None,
    }


def _size_to_bytes(value: str) -> int | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGTP])", value.strip(), re.IGNORECASE)
    if not match:
        return None
    scale = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5}[match.group(2).upper()]
    return round(float(match.group(1)) * (1024**scale))


def parse_vmmap_swap_bytes(raw: str) -> int | None:
    for line in raw.splitlines():
        fields = line.split()
        if fields and fields[0] == "TOTAL" and len(fields) >= 5:
            return _size_to_bytes(fields[4])
    return None


def collect_memory_detail(pid: int) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "physical_footprint_bytes": None,
        "physical_footprint_peak_bytes": None,
        "swapped_bytes": None,
        "errors": [],
    }
    try:
        footprint = subprocess.run(
            [
                "/usr/bin/footprint",
                "-p",
                str(pid),
                "-f",
                "bytes",
                "--noCategories",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        detail.update(parse_footprint_output(footprint.stdout))
    except (OSError, subprocess.SubprocessError) as exc:
        detail["errors"].append(f"footprint:{type(exc).__name__}")
    try:
        vmmap = subprocess.run(
            ["/usr/bin/vmmap", "-summary", str(pid)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        detail["swapped_bytes"] = parse_vmmap_swap_bytes(vmmap.stdout)
    except (OSError, subprocess.SubprocessError) as exc:
        detail["errors"].append(f"vmmap:{type(exc).__name__}")
    return detail


def _safe_command_hint(row: ProcessRow) -> str:
    sanitized = SECRET_PATTERN.sub("[REDACTED]", row.command)
    return " ".join(sanitized.split())[:240]


def _role_totals(processes: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for process in processes:
        role = str(process.get("role") or "unknown")
        target = totals.setdefault(
            role,
            {
                "process_count": 0,
                "rss_bytes": 0,
                "physical_footprint_bytes": 0,
                "physical_footprint_peak_bytes": 0,
                "swapped_bytes": 0,
                "physical_detail_complete": True,
            },
        )
        target["process_count"] += 1
        target["rss_bytes"] += int(process.get("rss_bytes") or 0)
        for field in (
            "physical_footprint_bytes",
            "physical_footprint_peak_bytes",
            "swapped_bytes",
        ):
            value = process.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                target[field] += value
            else:
                target["physical_detail_complete"] = False
    return totals


def build_sample(
    *,
    elapsed_seconds: float,
    phase: str,
    server_pid: int,
    runtime: Mapping[str, Any],
    process_table: Mapping[int, ProcessRow],
    memory_details: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    roles = classify_processes(
        process_table,
        server_pid=server_pid,
        runtime=runtime,
    )
    details = memory_details or {}
    process_samples: list[dict[str, Any]] = []
    for pid, role in sorted(roles.items(), key=lambda item: (item[1], item[0])):
        row = process_table.get(pid)
        if row is None:
            continue
        memory = details.get(pid, {})
        process_samples.append(
            {
                "pid": pid,
                "ppid": row.ppid,
                "role": role,
                "executable": Path(row.executable).name,
                "command_hint": _safe_command_hint(row),
                "command_sha256": hashlib.sha256(row.command.encode("utf-8")).hexdigest(),
                "rss_bytes": row.rss_bytes,
                "cpu_percent": row.cpu_percent,
                "physical_footprint_bytes": memory.get("physical_footprint_bytes"),
                "physical_footprint_peak_bytes": memory.get("physical_footprint_peak_bytes"),
                "swapped_bytes": memory.get("swapped_bytes"),
                "measurement_errors": list(memory.get("errors") or []),
            }
        )
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        "phase": phase,
        "server_pid": server_pid,
        "asr_runtime": runtime,
        "processes": process_samples,
        "role_totals": _role_totals(process_samples),
    }


def _latest_measured_role(
    samples: Sequence[Mapping[str, Any]],
    role: str,
) -> Mapping[str, Any] | None:
    for sample in reversed(samples):
        role_totals = sample.get("role_totals")
        if not isinstance(role_totals, Mapping):
            continue
        value = role_totals.get(role)
        if isinstance(value, Mapping) and value.get("physical_detail_complete"):
            return value
    return None


def _realtime_refiner_policy(sample: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = sample.get("asr_runtime")
    offline = runtime.get("offline_refinement") if isinstance(runtime, Mapping) else {}
    capability = (
        offline.get("capability")
        if isinstance(offline, Mapping) and isinstance(offline.get("capability"), Mapping)
        else {}
    )
    policy = (
        capability.get("realtime_policy")
        if isinstance(capability, Mapping)
        and isinstance(capability.get("realtime_policy"), Mapping)
        else {}
    )
    return policy


def _offline_refiner_worker(sample: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = sample.get("asr_runtime")
    offline = runtime.get("offline_refinement") if isinstance(runtime, Mapping) else {}
    worker = (
        offline.get("worker")
        if isinstance(offline, Mapping) and isinstance(offline.get("worker"), Mapping)
        else {}
    )
    return worker


def _online_only_policy_contract_valid(sample: Mapping[str, Any]) -> bool:
    policy = _realtime_refiner_policy(sample)
    return (
        policy.get("schema_version") == "realtime_refiner_policy.v1"
        and policy.get("mode") == "online_only"
        and bool(str(policy.get("source") or "").strip())
        and policy.get("realtime_refinement_enabled") is False
        and policy.get("prewarm_enabled") is False
        and policy.get("degradation_reason") == ONLINE_ONLY_REFINEMENT_REASON
    )


def _online_only_worker_absent(sample: Mapping[str, Any]) -> bool:
    role_totals = sample.get("role_totals")
    refiner_process_count = int(
        ((role_totals or {}).get("offline_refiner") or {}).get("process_count") or 0
    ) if isinstance(role_totals, Mapping) else 0
    worker = _offline_refiner_worker(sample)
    return (
        refiner_process_count == 0
        and not bool(worker.get("spawned"))
        and not bool(worker.get("process_running"))
        and not bool(worker.get("process_ready"))
        and int(worker.get("process_start_count") or 0) == 0
    )


def evaluate_resource_gate(
    samples: Sequence[Mapping[str, Any]],
    *,
    steady_limit_mib: float = DEFAULT_STEADY_LIMIT_MIB,
    cold_peak_limit_mib: float = DEFAULT_COLD_PEAK_LIMIT_MIB,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
) -> dict[str, Any]:
    workload_samples = [sample for sample in samples if sample.get("phase") == "workload"]
    idle_samples = [sample for sample in samples if sample.get("phase") == "idle"]
    blockers: list[str] = []

    server_pids = {
        int(sample["server_pid"])
        for sample in samples
        if isinstance(sample.get("server_pid"), int)
        and not isinstance(sample.get("server_pid"), bool)
        and int(sample["server_pid"]) > 0
    }
    if len(server_pids) > 1:
        blockers.append("measurement_identity_changed:server_pid")

    workload_policy_modes = {
        str(_realtime_refiner_policy(sample).get("mode") or "")
        for sample in workload_samples
        if _realtime_refiner_policy(sample)
    }
    online_only_strategy = bool(workload_samples) and workload_policy_modes == {"online_only"}
    resource_strategy = (
        "online_only_zero_process"
        if online_only_strategy
        else "resident_refiner_budget"
    )
    if online_only_strategy:
        if not all(
            _online_only_policy_contract_valid(sample)
            for sample in workload_samples
        ):
            blockers.append("measurement_contract_invalid:online_only_resource_policy")
        workload_worker_absent = all(
            _online_only_worker_absent(sample) for sample in workload_samples
        )
        if not workload_worker_absent:
            blockers.append("offline_refiner_spawned_under_online_only_policy")
        steady = _latest_measured_role(workload_samples, "offline_refiner")
        measured_workload = [
            role
            for sample in workload_samples
            for role in [
                (sample.get("role_totals") or {}).get("offline_refiner")
                if isinstance(sample.get("role_totals"), Mapping)
                else None
            ]
            if isinstance(role, Mapping) and role.get("physical_detail_complete")
        ]
        steady_bytes = (
            0
            if workload_worker_absent
            else int(steady.get("physical_footprint_bytes") or 0)
            if steady is not None
            else None
        )
        cold_peak_bytes = (
            0
            if workload_worker_absent
            else max(
                (
                    int(role.get("physical_footprint_peak_bytes") or 0)
                    for role in measured_workload
                ),
                default=None,
            )
        )
    else:
        if "online_only" in workload_policy_modes:
            blockers.append("measurement_identity_changed:realtime_refiner_policy")
        measured_workload = [
            role
            for sample in workload_samples
            for role in [
                (sample.get("role_totals") or {}).get("offline_refiner")
                if isinstance(sample.get("role_totals"), Mapping)
                else None
            ]
            if isinstance(role, Mapping) and role.get("physical_detail_complete")
        ]
        steady = _latest_measured_role(workload_samples, "offline_refiner")
        cold_peak_bytes = max(
            (
                int(role.get("physical_footprint_peak_bytes") or 0)
                for role in measured_workload
            ),
            default=None,
        )
        steady_bytes = (
            int(steady.get("physical_footprint_bytes") or 0)
            if steady is not None
            else None
        )
        if steady_bytes is None:
            blockers.append("measurement_missing:offline_refiner_steady_physical_footprint")
        elif steady_bytes > steady_limit_mib * MIB:
            blockers.append("offline_refiner_steady_limit_exceeded")
        if cold_peak_bytes is None:
            blockers.append("measurement_missing:offline_refiner_cold_peak_physical_footprint")
        elif cold_peak_bytes > cold_peak_limit_mib * MIB:
            blockers.append("offline_refiner_cold_peak_limit_exceeded")

    idle_evidence = next(
        (
            sample
            for sample in reversed(idle_samples)
            if float(sample.get("idle_elapsed_seconds") or 0) >= idle_seconds
        ),
        None,
    )
    idle_unloaded = False
    idle_worker_status: Mapping[str, Any] = {}
    if idle_evidence is None:
        blockers.append("measurement_missing:idle_120s")
    else:
        idle_worker_status = _offline_refiner_worker(idle_evidence)
        if online_only_strategy:
            if not _online_only_policy_contract_valid(idle_evidence):
                blockers.append("measurement_contract_invalid:online_only_idle_policy")
            idle_unloaded = _online_only_worker_absent(idle_evidence)
            if not idle_unloaded:
                blocker = "offline_refiner_spawned_under_online_only_policy"
                if blocker not in blockers:
                    blockers.append(blocker)
        else:
            role_totals = idle_evidence.get("role_totals")
            refiner_process_count = int(
                ((role_totals or {}).get("offline_refiner") or {}).get("process_count") or 0
            ) if isinstance(role_totals, Mapping) else 0
            idle_unloaded = (
                not bool(idle_worker_status.get("process_running"))
                and not bool(idle_worker_status.get("process_ready"))
                and refiner_process_count == 0
                and int(idle_worker_status.get("idle_unload_count") or 0) >= 1
                and idle_worker_status.get("last_stop_reason") == "idle_timeout"
            )
            if not idle_unloaded:
                blockers.append("offline_refiner_idle_unload_failed")

    status = (
        "blocked"
        if any(blocker.startswith("measurement_") for blocker in blockers)
        else "no_go"
        if blockers
        else "go"
    )
    return {
        "status": status,
        "passed": status == "go",
        "blockers": blockers,
        "thresholds": {
            "offline_refiner_steady_mib": steady_limit_mib,
            "offline_refiner_cold_peak_mib": cold_peak_limit_mib,
            "idle_unload_seconds": idle_seconds,
        },
        "observed": {
            "resource_strategy": resource_strategy,
            "realtime_refiner_policy_modes": sorted(workload_policy_modes),
            "offline_refiner_steady_bytes": steady_bytes,
            "offline_refiner_cold_peak_bytes": cold_peak_bytes,
            "idle_unloaded": idle_unloaded,
            "idle_worker_status": dict(idle_worker_status),
        },
    }


def _fetch_json(base_url: str, path: str, token: str) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers)
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not return an object")
    return payload


def _read_pid(pid_file: Path) -> int:
    value = int(pid_file.read_text(encoding="utf-8").strip())
    if value <= 0:
        raise ValueError("PID must be positive")
    return value


def _load_prior_workload_samples(
    report_path: Path,
    *,
    server_pid: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = report_path.expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("--prior-workload-report must contain a JSON object")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("--prior-workload-report has no samples array")
    accepted = [
        dict(sample)
        for sample in raw_samples
        if isinstance(sample, Mapping)
        and sample.get("phase") == "workload"
        and sample.get("server_pid") == server_pid
    ]
    if not accepted:
        raise ValueError(
            "--prior-workload-report has no workload samples for pinned server PID "
            f"{server_pid}"
        )
    for sample in accepted:
        sample["seeded_from_prior_report"] = True
    return accepted, {
        "path": str(resolved),
        "accepted_sample_count": len(accepted),
        "rejected_sample_count": len(raw_samples) - len(accepted),
        "report_schema_version": payload.get("schema_version"),
        "report_created_at": payload.get("created_at"),
    }


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    idle_marker = args.idle_marker.expanduser().resolve()
    if idle_marker.exists():
        raise ValueError("--idle-marker must be absent when the probe starts")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    token = str(os.environ.get(args.token_env) or "").strip()
    pid_file = args.pid_file.expanduser().resolve() if args.pid_file is not None else None
    pinned_server_pid = args.server_pid or _read_pid(pid_file)
    samples: list[dict[str, Any]] = []
    prior_workload: dict[str, Any] | None = None
    if args.prior_workload_report is not None:
        samples, prior_workload = _load_prior_workload_samples(
            args.prior_workload_report,
            server_pid=pinned_server_pid,
        )
    started = time.monotonic()
    idle_started: float | None = None
    last_detail_at = -math.inf
    errors: list[dict[str, Any]] = []
    while time.monotonic() - started <= args.max_seconds:
        now = time.monotonic()
        elapsed = now - started
        if idle_marker.exists() and idle_started is None:
            idle_started = now
            last_detail_at = -math.inf
        phase = "idle" if idle_started is not None else "workload"
        try:
            if pid_file is not None:
                current_server_pid = _read_pid(pid_file)
                if current_server_pid != pinned_server_pid:
                    raise ValueError(
                        "server PID changed during measurement: "
                        f"expected {pinned_server_pid}, observed {current_server_pid}"
                    )
            server_pid = pinned_server_pid
            runtime = _fetch_json(args.base_url, "/providers/asr/runtime", token)
            process_table = read_process_table()
            should_measure_detail = (
                not samples
                or now - last_detail_at >= args.footprint_interval
                or (idle_started is not None and not any(sample.get("phase") == "idle" for sample in samples))
                or (idle_started is not None and now - idle_started >= args.idle_seconds)
            )
            memory_details: dict[int, Mapping[str, Any]] = {}
            if should_measure_detail:
                roles = classify_processes(
                    process_table,
                    server_pid=server_pid,
                    runtime=runtime,
                )
                memory_details = {
                    pid: collect_memory_detail(pid)
                    for pid in roles
                    if pid in process_table
                }
                last_detail_at = now
            sample = build_sample(
                elapsed_seconds=elapsed,
                phase=phase,
                server_pid=server_pid,
                runtime=runtime,
                process_table=process_table,
                memory_details=memory_details,
            )
            if idle_started is not None:
                sample["idle_elapsed_seconds"] = round(now - idle_started, 3)
            samples.append(sample)
        except (OSError, ValueError, subprocess.SubprocessError, HTTPError, URLError) as exc:
            errors.append(
                {
                    "elapsed_seconds": round(elapsed, 3),
                    "phase": phase,
                    "error_class": type(exc).__name__,
                    "message": SECRET_PATTERN.sub("[REDACTED]", str(exc))[:300],
                }
            )
        if idle_started is not None and now - idle_started >= args.idle_seconds:
            break
        time.sleep(max(0.05, args.sample_interval))

    gate = evaluate_resource_gate(
        samples,
        steady_limit_mib=args.steady_limit_mib,
        cold_peak_limit_mib=args.cold_peak_limit_mib,
        idle_seconds=args.idle_seconds,
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "base_url": args.base_url,
            "pid_file": str(pid_file) if pid_file is not None else None,
            "server_pid": pinned_server_pid,
            "idle_marker": str(idle_marker),
            "prior_workload_report": prior_workload,
        },
        "gate": gate,
        "sample_count": len(samples),
        "errors": errors,
        "samples": samples,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    pid_group = parser.add_mutually_exclusive_group(required=True)
    pid_group.add_argument("--pid-file", type=Path)
    pid_group.add_argument("--server-pid", type=int)
    parser.add_argument("--idle-marker", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--prior-workload-report",
        type=Path,
        help=(
            "Optional earlier probe report whose workload samples for the pinned backend PID "
            "are reused; current-process idle evidence is still measured live"
        ),
    )
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--footprint-interval", type=float, default=15.0)
    parser.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--steady-limit-mib", type=float, default=DEFAULT_STEADY_LIMIT_MIB)
    parser.add_argument("--cold-peak-limit-mib", type=float, default=DEFAULT_COLD_PEAK_LIMIT_MIB)
    parser.add_argument("--token-env", default="MEETING_COPILOT_LOCAL_API_TOKEN")
    args = parser.parse_args(argv)
    for name in (
        "sample_interval",
        "footprint_interval",
        "idle_seconds",
        "max_seconds",
        "steady_limit_mib",
        "cold_peak_limit_mib",
    ):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.server_pid is not None and args.server_pid <= 0:
        parser.error("--server-pid must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = run_probe(parse_args(argv))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "status": report["gate"]["status"],
                "passed": report["gate"]["passed"],
                "blockers": report["gate"]["blockers"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["gate"]["passed"] else 2 if report["gate"]["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
