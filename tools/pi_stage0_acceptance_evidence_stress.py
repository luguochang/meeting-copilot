#!/usr/bin/env python3
"""Stress revision-pinned acceptance evidence across SQLite processes.

This runner exercises the production ``V2Persistence`` implementation.  It
starts independent spawned writer and reader processes against one WAL
database, verifies every captured source hash and high-water mark, and then
performs a deterministic transaction-boundary probe: a child commits a new
final after the reader snapshot begins, the pinned capture must exclude it,
and a fresh capture must include it.

Only counts, hashes, process exit codes, and bounded error classes are written
to the report.  Transcript content and local paths are deliberately omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
from pathlib import Path
import queue
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"
for source_root in (BACKEND_ROOT, CORE_ROOT):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from meeting_copilot_web_mvp.sqlite_repository import (  # noqa: E402
    SqliteAsrLiveSessionRepository,
    SqliteSettingsUsageRepository,
)
from meeting_copilot_web_mvp.v2_persistence import (  # noqa: E402
    V2Persistence,
    transcript_evidence_hash,
)


REPORT_SCHEMA_VERSION = "meeting_copilot.pi_stage0_acceptance_evidence_stress.v1"
MEETING_ID = "stage0-acceptance-evidence-stress"
DEFAULT_WRITER_PROCESSES = 3
DEFAULT_READER_PROCESSES = 2
DEFAULT_WRITES_PER_PROCESS = 8
PROCESS_TIMEOUT_SECONDS = 30.0


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def verify_capture(capture: Mapping[str, Any]) -> list[str]:
    """Independently verify one acceptance capture without trusting its flag."""

    errors: list[str] = []
    if capture.get("schema_version") != "meeting_copilot.acceptance_evidence.v1":
        errors.append("schema_version_invalid")
    unhashed = dict(capture)
    claimed_capture_sha256 = str(unhashed.pop("capture_sha256", ""))
    unhashed.pop("captured_at_ms", None)
    if not claimed_capture_sha256 or claimed_capture_sha256 != _sha256(unhashed):
        errors.append("capture_sha256_mismatch")

    consistency = capture.get("consistency")
    consistency = consistency if isinstance(consistency, Mapping) else {}
    if consistency.get("acceptance_eligible") is not True:
        errors.append("capture_not_acceptance_eligible")
    if list(consistency.get("errors") or []):
        errors.append("capture_consistency_errors_present")

    lineage = capture.get("lineage")
    lineage = lineage if isinstance(lineage, Mapping) else {}
    snapshot = capture.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    events = capture.get("events")
    events = events if isinstance(events, list) else []
    transcript = capture.get("transcript")
    transcript = transcript if isinstance(transcript, Mapping) else {}
    segments = transcript.get("segments")
    segments = segments if isinstance(segments, list) else []
    live_session = capture.get("live_session")
    usage = capture.get("usage_ledger")
    usage = usage if isinstance(usage, list) else []
    jobs = snapshot.get("jobs")
    jobs = jobs if isinstance(jobs, list) else []

    def integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    event_high_water = max(
        (integer(item.get("seq")) or 0 for item in events if isinstance(item, Mapping)),
        default=0,
    )
    transcript_high_water = max(
        (
            integer(item.get("transcript_seq")) or 0
            for item in segments
            if isinstance(item, Mapping)
        ),
        default=0,
    )
    if integer(lineage.get("event_high_water_mark")) != event_high_water:
        errors.append("event_high_water_mismatch")
    if integer(snapshot.get("last_seq")) != event_high_water:
        errors.append("snapshot_event_high_water_mismatch")
    if integer(lineage.get("transcript_high_water_mark")) != transcript_high_water:
        errors.append("transcript_high_water_mismatch")
    if integer(lineage.get("event_count")) != len(events):
        errors.append("event_count_mismatch")
    if str(lineage.get("event_sha256") or "") != _sha256(events):
        errors.append("event_sha256_mismatch")
    if str(lineage.get("transcript_segments_sha256") or "") != _sha256(segments):
        errors.append("transcript_segments_sha256_mismatch")

    canonical_segments = [
        item
        for item in segments
        if isinstance(item, Mapping) and item.get("duplicate_of_segment_id") is None
    ]
    transcript_material = [
        {
            "segment_id": item.get("segment_id"),
            "transcript_seq": integer(item.get("transcript_seq")),
            "revision": integer(item.get("revision")),
            "text": item.get("normalized_text"),
        }
        for item in canonical_segments
    ]
    transcript_revision = sum(
        integer(item.get("revision")) or 0 for item in canonical_segments
    )
    if integer(lineage.get("transcript_revision")) != transcript_revision:
        errors.append("transcript_revision_mismatch")
    if str(lineage.get("transcript_sha256") or "") != _sha256(transcript_material):
        errors.append("transcript_sha256_mismatch")

    expected_live_hash = _sha256(live_session) if live_session is not None else None
    if lineage.get("live_session_sha256") != expected_live_hash:
        errors.append("live_session_sha256_mismatch")
    if integer(lineage.get("usage_count")) != len(usage):
        errors.append("usage_count_mismatch")
    if str(lineage.get("usage_sha256") or "") != _sha256(usage):
        errors.append("usage_sha256_mismatch")
    usage_high_water = max(
        (integer(item.get("id")) or 0 for item in usage if isinstance(item, Mapping)),
        default=0,
    )
    if integer(lineage.get("usage_high_water_id")) != usage_high_water:
        errors.append("usage_high_water_mismatch")
    if integer(lineage.get("job_count")) != len(jobs):
        errors.append("job_count_mismatch")
    if str(lineage.get("job_state_sha256") or "") != _sha256(jobs):
        errors.append("job_state_sha256_mismatch")
    return list(dict.fromkeys(errors))


def _safe_process_result(
    *,
    role: str,
    worker_id: int,
    status: str,
    count: int = 0,
    error: BaseException | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": role,
        "worker_id": worker_id,
        "status": status,
        "count": max(0, int(count)),
        "error_class": type(error).__name__ if error is not None else None,
    }
    if details:
        result.update(dict(details))
    return result


def _writer_process(
    database_path: str,
    worker_id: int,
    writes: int,
    start_event: Any,
    ready_queue: Any,
    result_queue: Any,
) -> None:
    persistence: V2Persistence | None = None
    try:
        persistence = V2Persistence(Path(database_path))
        ready_queue.put(("writer", worker_id))
        if not start_event.wait(PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("writer start barrier timed out")
        for item_index in range(writes):
            final_id = f"writer-{worker_id}-final-{item_index}"
            segment_id = f"writer-{worker_id}-segment-{item_index}"
            text = f"Stage zero acceptance record {worker_id} item {item_index}."
            logical_index = worker_id * writes + item_index + 1
            persistence.commit_final_and_enqueue(
                meeting_id=MEETING_ID,
                final_id=final_id,
                segment_id=segment_id,
                text=text,
                normalized_text=text,
                started_at_ms=logical_index * 1_000,
                ended_at_ms=logical_index * 1_000 + 800,
                evidence_hash=transcript_evidence_hash(segment_id, text),
                now_ms=logical_index * 1_000 + 900,
                source_track="microphone",
            )
            # Keep the write window open long enough for readers to sample
            # several independent WAL revisions.
            time.sleep(0.003)
        result_queue.put(
            _safe_process_result(
                role="writer",
                worker_id=worker_id,
                status="passed",
                count=writes,
            )
        )
    except BaseException as exc:
        result_queue.put(
            _safe_process_result(
                role="writer",
                worker_id=worker_id,
                status="failed",
                error=exc,
            )
        )
    finally:
        if persistence is not None:
            persistence.close()


def _reader_process(
    database_path: str,
    worker_id: int,
    start_event: Any,
    stop_event: Any,
    ready_queue: Any,
    result_queue: Any,
) -> None:
    persistence: V2Persistence | None = None
    capture_count = 0
    invalid_count = 0
    high_water_marks: set[int] = set()
    try:
        persistence = V2Persistence(Path(database_path))
        ready_queue.put(("reader", worker_id))
        if not start_event.wait(PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("reader start barrier timed out")
        while not stop_event.is_set() or capture_count < 3:
            capture = persistence.capture_acceptance_evidence(MEETING_ID)
            verification_errors = verify_capture(capture)
            if verification_errors:
                invalid_count += 1
            high_water_marks.add(
                int(capture["lineage"]["transcript_high_water_mark"])
            )
            capture_count += 1
            time.sleep(0.001)
        result_queue.put(
            _safe_process_result(
                role="reader",
                worker_id=worker_id,
                status="passed" if invalid_count == 0 else "failed",
                count=capture_count,
                details={
                    "invalid_capture_count": invalid_count,
                    "distinct_transcript_high_water_count": len(high_water_marks),
                    "minimum_transcript_high_water": min(high_water_marks, default=0),
                    "maximum_transcript_high_water": max(high_water_marks, default=0),
                },
            )
        )
    except BaseException as exc:
        result_queue.put(
            _safe_process_result(
                role="reader",
                worker_id=worker_id,
                status="failed",
                count=capture_count,
                error=exc,
                details={"invalid_capture_count": invalid_count},
            )
        )
    finally:
        if persistence is not None:
            persistence.close()


def _single_writer_process(
    database_path: str,
    start_event: Any,
    ready_queue: Any,
    result_queue: Any,
) -> None:
    persistence: V2Persistence | None = None
    try:
        persistence = V2Persistence(Path(database_path))
        ready_queue.put(("pinned_writer", 0))
        if not start_event.wait(PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("pinned writer start barrier timed out")
        segment_id = "pinned-boundary-segment"
        text = "Stage zero pinned boundary record."
        persistence.commit_final_and_enqueue(
            meeting_id=MEETING_ID,
            final_id="pinned-boundary-final",
            segment_id=segment_id,
            text=text,
            normalized_text=text,
            started_at_ms=90_000_000,
            ended_at_ms=90_000_800,
            evidence_hash=transcript_evidence_hash(segment_id, text),
            now_ms=90_000_900,
            source_track="microphone",
        )
        result_queue.put(
            _safe_process_result(
                role="pinned_writer",
                worker_id=0,
                status="passed",
                count=1,
            )
        )
    except BaseException as exc:
        result_queue.put(
            _safe_process_result(
                role="pinned_writer",
                worker_id=0,
                status="failed",
                error=exc,
            )
        )
    finally:
        if persistence is not None:
            persistence.close()


def _queue_get(result_queue: Any, timeout: float = PROCESS_TIMEOUT_SECONDS) -> Any:
    try:
        return result_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("process result queue timed out") from exc


def _join_processes(processes: Sequence[Any]) -> list[int | None]:
    deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
    for process in processes:
        process.join(timeout=max(0.0, deadline - time.monotonic()))
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    return [process.exitcode for process in processes]


def _initialize_database(work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    database_path = work_dir / "meeting_copilot.db"
    if database_path.exists():
        raise ValueError("work directory already contains a meeting database")
    persistence = V2Persistence(database_path)
    try:
        persistence.create_meeting(
            meeting_id=MEETING_ID,
            title="Stage 0 acceptance evidence stress",
            now_ms=1,
        )
    finally:
        persistence.close()
    live_repository = SqliteAsrLiveSessionRepository(work_dir)
    usage_repository = SqliteSettingsUsageRepository(work_dir, {})
    try:
        live_repository.create(
            {
                "session_id": MEETING_ID,
                "source": "stage0_loopback_fixture",
                "events": [],
                "acceptance_eligible": True,
            }
        )
        usage_repository.record_usage(
            session_id=MEETING_ID,
            purpose="stage0_fixture",
            provider="loopback_fixture",
            model="fixture-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            timestamp_ms=1,
        )
    finally:
        live_repository.close()
        usage_repository.close()
    return database_path


def run_stress(
    work_dir: Path,
    *,
    writer_processes: int = DEFAULT_WRITER_PROCESSES,
    reader_processes: int = DEFAULT_READER_PROCESSES,
    writes_per_process: int = DEFAULT_WRITES_PER_PROCESS,
) -> dict[str, Any]:
    if writer_processes < 1 or reader_processes < 1 or writes_per_process < 1:
        raise ValueError("writer, reader, and write counts must be positive")
    database_path = _initialize_database(Path(work_dir).expanduser().resolve())
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    stop_event = context.Event()
    ready_queue = context.Queue()
    result_queue = context.Queue()
    readers = [
        context.Process(
            target=_reader_process,
            args=(
                str(database_path),
                worker_id,
                start_event,
                stop_event,
                ready_queue,
                result_queue,
            ),
            name=f"stage0-evidence-reader-{worker_id}",
        )
        for worker_id in range(reader_processes)
    ]
    writers = [
        context.Process(
            target=_writer_process,
            args=(
                str(database_path),
                worker_id,
                writes_per_process,
                start_event,
                ready_queue,
                result_queue,
            ),
            name=f"stage0-evidence-writer-{worker_id}",
        )
        for worker_id in range(writer_processes)
    ]
    processes = [*readers, *writers]
    for process in processes:
        process.start()

    ready_records = [_queue_get(ready_queue) for _ in processes]
    start_event.set()
    writer_exit_codes = _join_processes(writers)
    stop_event.set()
    reader_exit_codes = _join_processes(readers)
    process_results = [_queue_get(result_queue) for _ in processes]

    persistence = V2Persistence(database_path)
    try:
        concurrent_final_capture = persistence.capture_acceptance_evidence(MEETING_ID)
    finally:
        persistence.close()
    concurrent_final_errors = verify_capture(concurrent_final_capture)
    expected_concurrent_segments = writer_processes * writes_per_process

    # Deterministic cross-process pinning proof.  Establish the reader's WAL
    # snapshot, allow a child process to commit, and reuse that transaction in
    # capture_acceptance_evidence().
    pinned_reader = V2Persistence(database_path)
    boundary_start = context.Event()
    boundary_ready = context.Queue()
    boundary_result = context.Queue()
    boundary_writer = context.Process(
        target=_single_writer_process,
        args=(
            str(database_path),
            boundary_start,
            boundary_ready,
            boundary_result,
        ),
        name="stage0-evidence-pinned-writer",
    )
    before_capture: dict[str, Any] | None = None
    pinned_capture: dict[str, Any] | None = None
    after_capture: dict[str, Any] | None = None
    try:
        before_capture = pinned_reader.capture_acceptance_evidence(MEETING_ID)
        pinned_reader._conn.execute("BEGIN")
        pinned_reader._conn.execute(
            "SELECT COUNT(*) FROM meeting_events WHERE meeting_id = ?",
            (MEETING_ID,),
        ).fetchone()
        boundary_writer.start()
        _queue_get(boundary_ready)
        boundary_start.set()
        boundary_exit_codes = _join_processes([boundary_writer])
        boundary_process_result = _queue_get(boundary_result)
        pinned_capture = pinned_reader.capture_acceptance_evidence(MEETING_ID)
        pinned_reader._conn.execute("ROLLBACK")
        after_capture = pinned_reader.capture_acceptance_evidence(MEETING_ID)
    finally:
        if pinned_reader._conn.in_transaction:
            pinned_reader._conn.execute("ROLLBACK")
        pinned_reader.close()

    before_lineage = before_capture["lineage"]
    pinned_lineage = pinned_capture["lineage"]
    after_lineage = after_capture["lineage"]
    pinned_boundary = {
        "writer_exit_codes": boundary_exit_codes,
        "writer_result": boundary_process_result,
        "before_transcript_high_water": before_lineage["transcript_high_water_mark"],
        "pinned_transcript_high_water": pinned_lineage["transcript_high_water_mark"],
        "after_transcript_high_water": after_lineage["transcript_high_water_mark"],
        "before_capture_sha256": before_capture["capture_sha256"],
        "pinned_capture_sha256": pinned_capture["capture_sha256"],
        "after_capture_sha256": after_capture["capture_sha256"],
        "before_verification_errors": verify_capture(before_capture),
        "pinned_verification_errors": verify_capture(pinned_capture),
        "after_verification_errors": verify_capture(after_capture),
    }
    pinned_boundary["passed"] = (
        boundary_exit_codes == [0]
        and boundary_process_result.get("status") == "passed"
        and not pinned_boundary["before_verification_errors"]
        and not pinned_boundary["pinned_verification_errors"]
        and not pinned_boundary["after_verification_errors"]
        and pinned_boundary["before_transcript_high_water"]
        == pinned_boundary["pinned_transcript_high_water"]
        and pinned_boundary["before_capture_sha256"]
        == pinned_boundary["pinned_capture_sha256"]
        and pinned_boundary["after_transcript_high_water"]
        == pinned_boundary["before_transcript_high_water"] + 1
        and pinned_boundary["after_capture_sha256"]
        != pinned_boundary["pinned_capture_sha256"]
    )

    reader_results = [item for item in process_results if item.get("role") == "reader"]
    writer_results = [item for item in process_results if item.get("role") == "writer"]
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "process_start_method": context.get_start_method(),
        "configuration": {
            "writer_processes": writer_processes,
            "reader_processes": reader_processes,
            "writes_per_process": writes_per_process,
        },
        "ready_process_count": len(ready_records),
        "writer_exit_codes": writer_exit_codes,
        "reader_exit_codes": reader_exit_codes,
        "writer_results": sorted(writer_results, key=lambda item: item["worker_id"]),
        "reader_results": sorted(reader_results, key=lambda item: item["worker_id"]),
        "concurrent_final_capture": {
            "verification_errors": concurrent_final_errors,
            "acceptance_eligible": concurrent_final_capture["consistency"]["acceptance_eligible"],
            "segment_count": concurrent_final_capture["transcript"]["segment_count"],
            "event_count": concurrent_final_capture["lineage"]["event_count"],
            "job_count": concurrent_final_capture["lineage"]["job_count"],
            "usage_count": concurrent_final_capture["lineage"]["usage_count"],
            "live_session_present": concurrent_final_capture["live_session"] is not None,
            "capture_sha256": concurrent_final_capture["capture_sha256"],
        },
        "pinned_boundary": pinned_boundary,
    }
    report["passed"] = (
        writer_exit_codes == [0] * writer_processes
        and reader_exit_codes == [0] * reader_processes
        and len(writer_results) == writer_processes
        and len(reader_results) == reader_processes
        and all(item.get("status") == "passed" for item in process_results)
        and all(int(item.get("invalid_capture_count") or 0) == 0 for item in reader_results)
        and all(
            int(item.get("distinct_transcript_high_water_count") or 0) >= 2
            for item in reader_results
        )
        and not concurrent_final_errors
        and concurrent_final_capture["transcript"]["segment_count"]
        == expected_concurrent_segments
        and pinned_boundary["passed"]
    )
    report["report_sha256"] = _sha256(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--writer-processes", type=int, default=DEFAULT_WRITER_PROCESSES)
    parser.add_argument("--reader-processes", type=int, default=DEFAULT_READER_PROCESSES)
    parser.add_argument("--writes-per-process", type=int, default=DEFAULT_WRITES_PER_PROCESS)
    args = parser.parse_args(argv)
    work_dir = (
        args.work_dir.expanduser().resolve()
        if args.work_dir is not None
        else Path(tempfile.mkdtemp(prefix="meeting-copilot-stage0-evidence-"))
    )
    try:
        report = run_stress(
            work_dir,
            writer_processes=args.writer_processes,
            reader_processes=args.reader_processes,
            writes_per_process=args.writes_per_process,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        parser.error(f"{type(exc).__name__}: {exc}")
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(encoded)
    else:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
        sys.stdout.write(json.dumps({"output": str(output), "passed": report["passed"]}) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - covered by integration invocation
    raise SystemExit(main())
