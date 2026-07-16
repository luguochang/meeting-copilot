from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Protocol
import unicodedata
from uuid import uuid4

from .canonical_transcript import project_canonical_transcript
from .v2_persistence import V2Persistence, transcript_evidence_hash


MIGRATION_NAME = "asr-live-record-json-to-v2.v1"
MARKER_TABLE = "v2_migration_markers"
CHECKSUM_ALGORITHM = "sha256-framed-v1"
DEFAULT_RESERVE_BYTES = 64 * 1024 * 1024


class _Persistence(Protocol):
    def commit_final_and_enqueue(self, **kwargs: Any) -> dict[str, Any]: ...

    def finalize_migration_only_meeting(self, **kwargs: Any) -> bool: ...

    def close(self) -> None: ...


class V2MigrationError(RuntimeError):
    """Base error for migration failures."""


class MigrationPreflightError(V2MigrationError):
    """Raised before any live database migration write is attempted."""


class MigrationExecutionError(V2MigrationError):
    """Raised after a migration marker exists and an operational failure occurs."""

    def __init__(self, message: str, *, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class _SourceSnapshot:
    backup_path: Path
    source_checksum: str
    source_rows: tuple[tuple[str, str], ...]
    has_legacy_table: bool
    source_bytes: int
    required_free_bytes: int
    available_free_bytes: int


@dataclass(frozen=True)
class _CanonicalFinal:
    final_id: str
    segment_id: str
    text: str
    normalized_text: str
    started_at_ms: int | None
    ended_at_ms: int | None
    evidence_hash: str
    now_ms: int


class V1ToV2ShadowMigrator:
    """Idempotently backfill legacy ASR records into normalized V2 facts.

    The legacy table is never updated. A consistent SQLite backup is created
    before V2 schema or marker writes, and that backup is the immutable source
    snapshot for the run. Provider execution is deliberately outside this
    class; backfill only commits facts, outbox events, and durable jobs.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        backup_dir: str | Path | None = None,
        reserve_bytes: int = DEFAULT_RESERVE_BYTES,
        disk_usage_fn: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
        persistence_factory: Callable[[Path], _Persistence] = V2Persistence,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.backup_dir = (
            Path(backup_dir)
            if backup_dir is not None
            else self.database_path.parent / "migration_backups"
        )
        self.reserve_bytes = int(reserve_bytes)
        if self.reserve_bytes < 0:
            raise ValueError("reserve_bytes must not be negative")
        self._disk_usage = disk_usage_fn
        self._persistence_factory = persistence_factory
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def run(self) -> dict[str, Any]:
        if not self.database_path.is_file():
            return self._empty_report(status="no_source_database")

        source = self._prepare_source_snapshot()
        report = self._base_report(source)
        if not source.has_legacy_table:
            report["status"] = "no_source_table"
            return report
        if not source.source_rows:
            report["status"] = "no_source_records"
            return report

        started_at_ms = max(0, int(self._clock_ms()))
        self._mark_started(source, started_at_ms=started_at_ms)
        persistence: _Persistence | None = None
        try:
            persistence = self._persistence_factory(self.database_path)
            self._migrate_rows(source, persistence, report)
            self._finish_report(report)
            self._mark_finished(
                source,
                report,
                status=str(report["status"]),
                completed_at_ms=max(started_at_ms, int(self._clock_ms())),
                error_text=None,
            )
            return report
        except BaseException as exc:
            report["status"] = "failed"
            report["error_class"] = type(exc).__name__
            report["error"] = _safe_error(exc)
            self._refresh_summary(report)
            try:
                self._mark_finished(
                    source,
                    report,
                    status="failed",
                    completed_at_ms=max(started_at_ms, int(self._clock_ms())),
                    error_text=_safe_error(exc),
                )
            except BaseException as marker_exc:
                report["marker_error"] = _safe_error(marker_exc)
            raise MigrationExecutionError(
                f"V1 to V2 migration failed: {_safe_error(exc)}",
                report=report,
            ) from exc
        finally:
            if persistence is not None:
                persistence.close()

    def _prepare_source_snapshot(self) -> _SourceSnapshot:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        source_connection: sqlite3.Connection | None = None
        try:
            source_connection = _open_read_only(self.database_path)
            _require_integrity(source_connection, label="source database")
            has_legacy_table = _has_table(source_connection, "asr_live_sessions")
            if has_legacy_table:
                _require_legacy_columns(source_connection)
            source_bytes = _database_snapshot_size(source_connection, self.database_path)
            available_free_bytes = int(self._disk_usage(self.backup_dir).free)
            required_free_bytes = source_bytes + self.reserve_bytes
            if available_free_bytes < required_free_bytes:
                raise MigrationPreflightError(
                    "insufficient free disk space for migration backup: "
                    f"required={required_free_bytes}, available={available_free_bytes}"
                )
            temporary_path = self.backup_dir / (
                f".{self.database_path.name}.{uuid4().hex}.backup.tmp"
            )
            try:
                _create_sqlite_backup(source_connection, temporary_path)
                with closing(sqlite3.connect(temporary_path)) as backup_connection:
                    _require_integrity(backup_connection, label="migration backup")
                    backup_has_legacy_table = _has_table(
                        backup_connection,
                        "asr_live_sessions",
                    )
                    if backup_has_legacy_table:
                        _require_legacy_columns(backup_connection)
                        source_rows = _read_source_rows(backup_connection)
                    else:
                        source_rows = ()
                source_checksum = _source_checksum(
                    source_rows,
                    has_legacy_table=backup_has_legacy_table,
                )
                backup_path = self.backup_dir / (
                    f"{self.database_path.stem}.pre-v2-shadow."
                    f"{source_checksum[:16]}.sqlite3"
                )
                if backup_path.exists():
                    _verify_existing_backup(
                        backup_path,
                        expected_checksum=source_checksum,
                        expected_has_legacy_table=backup_has_legacy_table,
                    )
                    temporary_path.unlink()
                else:
                    os.replace(temporary_path, backup_path)
                    _fsync_directory(self.backup_dir)
                return _SourceSnapshot(
                    backup_path=backup_path,
                    source_checksum=source_checksum,
                    source_rows=source_rows,
                    has_legacy_table=backup_has_legacy_table,
                    source_bytes=source_bytes,
                    required_free_bytes=required_free_bytes,
                    available_free_bytes=available_free_bytes,
                )
            finally:
                temporary_path.unlink(missing_ok=True)
        except MigrationPreflightError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise MigrationPreflightError(
                f"database integrity or backup preflight failed: {_safe_error(exc)}"
            ) from exc
        finally:
            if source_connection is not None:
                source_connection.close()

    def _mark_started(self, source: _SourceSnapshot, *, started_at_ms: int) -> None:
        with closing(_open_live_database(self.database_path)) as connection:
            with connection:
                _ensure_marker_table(connection)
                connection.execute(
                    f"INSERT INTO {MARKER_TABLE} ("
                    "migration_name, source_checksum, checksum_algorithm, status, "
                    "backup_path, source_row_count, started_at_ms, completed_at_ms, "
                    "attempts, report_json, error_text"
                    ") VALUES (?, ?, ?, 'running', ?, ?, ?, NULL, 1, NULL, NULL) "
                    "ON CONFLICT(migration_name, source_checksum) DO UPDATE SET "
                    "checksum_algorithm = excluded.checksum_algorithm, "
                    "status = 'running', backup_path = excluded.backup_path, "
                    "source_row_count = excluded.source_row_count, "
                    "started_at_ms = excluded.started_at_ms, completed_at_ms = NULL, "
                    "attempts = attempts + 1, report_json = NULL, error_text = NULL",
                    (
                        MIGRATION_NAME,
                        source.source_checksum,
                        CHECKSUM_ALGORITHM,
                        str(source.backup_path),
                        len(source.source_rows),
                        started_at_ms,
                    ),
                )

    def _mark_finished(
        self,
        source: _SourceSnapshot,
        report: Mapping[str, Any],
        *,
        status: str,
        completed_at_ms: int,
        error_text: str | None,
    ) -> None:
        with closing(_open_live_database(self.database_path)) as connection:
            with connection:
                result = connection.execute(
                    f"UPDATE {MARKER_TABLE} SET status = ?, completed_at_ms = ?, "
                    "report_json = ?, error_text = ? "
                    "WHERE migration_name = ? AND source_checksum = ?",
                    (
                        status,
                        completed_at_ms,
                        _json_dump(report),
                        error_text,
                        MIGRATION_NAME,
                        source.source_checksum,
                    ),
                )
                if result.rowcount != 1:
                    raise RuntimeError("migration marker disappeared during execution")

    def _migrate_rows(
        self,
        source: _SourceSnapshot,
        persistence: _Persistence,
        report: dict[str, Any],
    ) -> None:
        for meeting_id, record_json in source.source_rows:
            parsed = _parse_source_meeting(meeting_id, record_json)
            if isinstance(parsed, dict):
                report["meetings"].append(parsed)
                continue

            finals = list(parsed)
            meeting_report = _new_meeting_report(meeting_id, finals)
            report["meetings"].append(meeting_report)
            commit_errors: dict[str, str] = {}
            for final in finals:
                try:
                    result = persistence.commit_final_and_enqueue(
                        meeting_id=meeting_id,
                        final_id=final.final_id,
                        segment_id=final.segment_id,
                        text=final.text,
                        normalized_text=final.normalized_text,
                        started_at_ms=final.started_at_ms,
                        ended_at_ms=final.ended_at_ms,
                        evidence_hash=final.evidence_hash,
                        now_ms=final.now_ms,
                        correlation_id=meeting_id,
                        causation_id=f"migration:{source.source_checksum}",
                        enqueue_jobs=False,
                    )
                except ValueError as exc:
                    commit_errors[final.final_id] = _safe_error(exc)
                    continue
                except BaseException as exc:
                    _reconcile_meeting(
                        self.database_path,
                        meeting_report,
                        finals,
                        commit_errors=commit_errors,
                    )
                    meeting_report["status"] = "interrupted"
                    meeting_report["execution_error_class"] = type(exc).__name__
                    meeting_report["execution_error"] = _safe_error(exc)
                    raise
                if result.get("created"):
                    meeting_report["created_final_count"] += 1
                else:
                    meeting_report["idempotent_final_count"] += 1
            _reconcile_meeting(
                self.database_path,
                meeting_report,
                finals,
                commit_errors=commit_errors,
            )
            finalize = getattr(persistence, "finalize_migration_only_meeting", None)
            if callable(finalize) and meeting_report["status"] == "matched":
                finalize(
                    meeting_id=meeting_id,
                    source_checksum=source.source_checksum,
                    now_ms=max((final.now_ms for final in finals), default=int(self._clock_ms())),
                )

    def _finish_report(self, report: dict[str, Any]) -> None:
        self._refresh_summary(report)
        summary = report["summary"]
        report["status"] = (
            "completed_with_issues"
            if summary["invalid_meeting_count"]
            or summary["missing_final_count"]
            or summary["conflict_count"]
            else "completed"
        )

    def _refresh_summary(self, report: dict[str, Any]) -> None:
        meetings = list(report.get("meetings") or [])
        valid = [meeting for meeting in meetings if meeting.get("status") != "invalid_source"]
        report["summary"] = {
            "source_meeting_count": len(report.get("source_rows") or []),
            "valid_meeting_count": len(valid),
            "invalid_meeting_count": len(meetings) - len(valid),
            "source_final_count": sum(
                int(meeting.get("source_final_count") or 0) for meeting in valid
            ),
            "created_final_count": sum(
                int(meeting.get("created_final_count") or 0) for meeting in valid
            ),
            "idempotent_final_count": sum(
                int(meeting.get("idempotent_final_count") or 0) for meeting in valid
            ),
            "missing_final_count": sum(
                len(meeting.get("missing_final_ids") or []) for meeting in valid
            ),
            "conflict_count": sum(
                len(meeting.get("conflicts") or []) for meeting in valid
            ),
        }

    def _base_report(self, source: _SourceSnapshot) -> dict[str, Any]:
        return {
            "migration_name": MIGRATION_NAME,
            "status": "running",
            "database_path": str(self.database_path),
            "backup_path": str(source.backup_path),
            "source_checksum": source.source_checksum,
            "checksum_algorithm": CHECKSUM_ALGORITHM,
            "source_rows": [meeting_id for meeting_id, _payload in source.source_rows],
            "preflight": {
                "integrity_check": "ok",
                "source_bytes": source.source_bytes,
                "required_free_bytes": source.required_free_bytes,
                "available_free_bytes": source.available_free_bytes,
            },
            "meetings": [],
            "summary": {
                "source_meeting_count": len(source.source_rows),
                "valid_meeting_count": 0,
                "invalid_meeting_count": 0,
                "source_final_count": 0,
                "created_final_count": 0,
                "idempotent_final_count": 0,
                "missing_final_count": 0,
                "conflict_count": 0,
            },
        }

    def _empty_report(self, *, status: str) -> dict[str, Any]:
        return {
            "migration_name": MIGRATION_NAME,
            "status": status,
            "database_path": str(self.database_path),
            "backup_path": None,
            "source_checksum": None,
            "checksum_algorithm": CHECKSUM_ALGORITHM,
            "source_rows": [],
            "preflight": None,
            "meetings": [],
            "summary": {
                "source_meeting_count": 0,
                "valid_meeting_count": 0,
                "invalid_meeting_count": 0,
                "source_final_count": 0,
                "created_final_count": 0,
                "idempotent_final_count": 0,
                "missing_final_count": 0,
                "conflict_count": 0,
            },
        }


def migrate_v1_to_v2(
    database_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience entry point for one shadow migration run."""

    return V1ToV2ShadowMigrator(database_path, **kwargs).run()


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _open_live_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _require_integrity(connection: sqlite3.Connection, *, label: str) -> None:
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise MigrationPreflightError(
            f"{label} integrity check failed: {_safe_error(exc)}"
        ) from exc
    results = [str(row[0]) for row in rows]
    if results != ["ok"]:
        detail = "; ".join(results[:10]) or "no integrity result"
        raise MigrationPreflightError(f"{label} integrity check failed: {detail}")


def _has_table(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _require_legacy_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(asr_live_sessions)").fetchall()
    }
    missing = {"session_id", "record_json"} - columns
    if missing:
        raise MigrationPreflightError(
            "legacy asr_live_sessions table is missing required columns: "
            + ", ".join(sorted(missing))
        )


def _database_snapshot_size(connection: sqlite3.Connection, database_path: Path) -> int:
    page_count = max(0, int(connection.execute("PRAGMA page_count").fetchone()[0]))
    page_size = max(1, int(connection.execute("PRAGMA page_size").fetchone()[0]))
    return max(database_path.stat().st_size, page_count * page_size)


def _create_sqlite_backup(
    source_connection: sqlite3.Connection,
    destination_path: Path,
) -> None:
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(destination_path)
        source_connection.backup(destination)
    finally:
        if destination is not None:
            destination.close()
    os.chmod(destination_path, 0o600)
    with destination_path.open("rb") as backup_file:
        os.fsync(backup_file.fileno())


def _verify_existing_backup(
    backup_path: Path,
    *,
    expected_checksum: str,
    expected_has_legacy_table: bool,
) -> None:
    try:
        with closing(sqlite3.connect(backup_path)) as connection:
            _require_integrity(connection, label="existing migration backup")
            has_legacy_table = _has_table(connection, "asr_live_sessions")
            if has_legacy_table:
                _require_legacy_columns(connection)
                rows = _read_source_rows(connection)
            else:
                rows = ()
    except MigrationPreflightError:
        raise
    except sqlite3.DatabaseError as exc:
        raise MigrationPreflightError(
            f"existing migration backup integrity check failed: {_safe_error(exc)}"
        ) from exc
    actual_checksum = _source_checksum(
        rows,
        has_legacy_table=has_legacy_table,
    )
    if (
        has_legacy_table != expected_has_legacy_table
        or actual_checksum != expected_checksum
    ):
        raise MigrationPreflightError(
            "existing migration backup checksum does not match source snapshot"
        )


def _read_source_rows(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    rows = connection.execute(
        "SELECT session_id, record_json FROM asr_live_sessions ORDER BY session_id"
    ).fetchall()
    return tuple((str(row[0]), str(row[1])) for row in rows)


def _source_checksum(
    rows: tuple[tuple[str, str], ...],
    *,
    has_legacy_table: bool,
) -> str:
    digest = hashlib.sha256()
    digest.update(MIGRATION_NAME.encode("ascii"))
    digest.update(b"\x00table:")
    digest.update(b"1" if has_legacy_table else b"0")
    for session_id, record_json in rows:
        _update_framed_hash(digest, session_id)
        _update_framed_hash(digest, record_json)
    return digest.hexdigest()


def _parse_source_meeting(
    meeting_id: str,
    record_json: str,
) -> tuple[_CanonicalFinal, ...] | dict[str, Any]:
    try:
        record = json.loads(record_json)
    except (TypeError, json.JSONDecodeError) as exc:
        return _invalid_meeting(meeting_id, "invalid_json", exc)
    if not isinstance(record, dict):
        return _invalid_meeting(
            meeting_id,
            "invalid_record",
            ValueError("record_json must contain a JSON object"),
        )
    record_session_id = str(record.get("session_id") or meeting_id)
    if record_session_id != meeting_id:
        return _invalid_meeting(
            meeting_id,
            "session_id_mismatch",
            ValueError(
                f"record session_id {record_session_id!r} does not match table key"
            ),
        )
    events = record.get("events") or []
    if not isinstance(events, list):
        return _invalid_meeting(
            meeting_id,
            "invalid_events",
            ValueError("record events must be a list"),
        )
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            return _invalid_meeting(
                meeting_id,
                "invalid_event",
                ValueError(f"event at index {index} must be an object"),
            )
        payload = event.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return _invalid_meeting(
                meeting_id,
                "invalid_event_payload",
                ValueError(f"event payload at index {index} must be an object"),
            )
    try:
        canonical = project_canonical_transcript(
            session_id=meeting_id,
            events=events,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return _invalid_meeting(meeting_id, "canonical_projection_failed", exc)

    fallback_now_ms = _optional_nonnegative_int(
        record.get("last_activity_at_epoch_ms")
        or record.get("created_at_epoch_ms")
        or canonical.get("updated_at_ms")
    ) or 0
    finals: list[_CanonicalFinal] = []
    seen_segment_ids: set[str] = set()
    for index, segment in enumerate(canonical.get("segments") or []):
        if not isinstance(segment, Mapping):
            return _invalid_meeting(
                meeting_id,
                "invalid_canonical_segment",
                ValueError(f"canonical segment at index {index} must be an object"),
            )
        segment_id = str(segment.get("segment_id") or "").strip()
        normalized_text = _normalize_text(segment.get("display_text"))
        if not segment_id or not normalized_text:
            return _invalid_meeting(
                meeting_id,
                "invalid_canonical_segment",
                ValueError(
                    f"canonical segment at index {index} is missing identity or text"
                ),
            )
        if segment_id in seen_segment_ids:
            return _invalid_meeting(
                meeting_id,
                "duplicate_canonical_segment",
                ValueError(f"duplicate canonical segment_id {segment_id!r}"),
            )
        seen_segment_ids.add(segment_id)
        started_at_ms = _optional_nonnegative_int(segment.get("start_ms"))
        ended_at_ms = _optional_nonnegative_int(segment.get("end_ms"))
        evidence_hash = transcript_evidence_hash(segment_id, normalized_text)
        finals.append(
            _CanonicalFinal(
                final_id=f"final:{meeting_id}:{segment_id}",
                segment_id=segment_id,
                text=normalized_text,
                normalized_text=normalized_text,
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                evidence_hash=evidence_hash,
                now_ms=fallback_now_ms,
            )
        )
    return tuple(finals)


def _invalid_meeting(
    meeting_id: str,
    error_class: str,
    error: BaseException,
) -> dict[str, Any]:
    return {
        "meeting_id": meeting_id,
        "status": "invalid_source",
        "error_class": error_class,
        "error": _safe_error(error),
        "source_final_count": 0,
        "matched_v2_final_count": 0,
        "v2_total_final_count": 0,
        "created_final_count": 0,
        "idempotent_final_count": 0,
        "source_normalized_text_hash": None,
        "v2_normalized_text_hash": None,
        "missing_final_ids": [],
        "conflicts": [],
    }


def _new_meeting_report(
    meeting_id: str,
    finals: list[_CanonicalFinal],
) -> dict[str, Any]:
    return {
        "meeting_id": meeting_id,
        "status": "pending",
        "source_final_count": len(finals),
        "matched_v2_final_count": 0,
        "v2_total_final_count": 0,
        "created_final_count": 0,
        "idempotent_final_count": 0,
        "source_normalized_text_hash": _normalized_text_hash(
            final.normalized_text for final in finals
        ),
        "v2_normalized_text_hash": None,
        "missing_final_ids": [],
        "conflicts": [],
    }


def _reconcile_meeting(
    database_path: Path,
    meeting_report: dict[str, Any],
    expected_finals: list[_CanonicalFinal],
    *,
    commit_errors: Mapping[str, str],
) -> None:
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM transcript_segments WHERE meeting_id = ? "
            "ORDER BY transcript_seq",
            (meeting_report["meeting_id"],),
        ).fetchall()
    by_final_id = {str(row["final_id"]): row for row in rows}
    by_segment_id = {str(row["segment_id"]): row for row in rows}
    v2_texts: list[str] = []
    missing: list[str] = []
    conflicts: list[dict[str, Any]] = []

    for expected in expected_finals:
        row = by_final_id.get(expected.final_id)
        if row is None:
            missing.append(expected.final_id)
            segment_owner = by_segment_id.get(expected.segment_id)
            if segment_owner is not None or expected.final_id in commit_errors:
                conflict: dict[str, Any] = {
                    "kind": "identity_conflict"
                    if segment_owner is not None
                    else "commit_conflict",
                    "final_id": expected.final_id,
                    "segment_id": expected.segment_id,
                }
                if segment_owner is not None:
                    conflict["actual_final_id"] = str(segment_owner["final_id"])
                if expected.final_id in commit_errors:
                    conflict["message"] = commit_errors[expected.final_id]
                conflicts.append(conflict)
            continue

        actual_normalized_text = str(row["normalized_text"])
        v2_texts.append(actual_normalized_text)
        expected_values = {
            "segment_id": expected.segment_id,
            "text": expected.text,
            "normalized_text": expected.normalized_text,
            "started_at_ms": expected.started_at_ms,
            "ended_at_ms": expected.ended_at_ms,
            "evidence_hash": expected.evidence_hash,
        }
        differences = {
            field: {"expected": value, "actual": row[field]}
            for field, value in expected_values.items()
            if row[field] != value
        }
        if differences:
            conflict = {
                "kind": "content_conflict",
                "final_id": expected.final_id,
                "segment_id": expected.segment_id,
                "differences": differences,
            }
            if expected.final_id in commit_errors:
                conflict["message"] = commit_errors[expected.final_id]
            conflicts.append(conflict)

    meeting_report["matched_v2_final_count"] = len(expected_finals) - len(missing)
    meeting_report["v2_total_final_count"] = len(rows)
    meeting_report["v2_normalized_text_hash"] = _normalized_text_hash(v2_texts)
    meeting_report["missing_final_ids"] = missing
    meeting_report["conflicts"] = conflicts
    if conflicts:
        meeting_report["status"] = "conflict"
    elif missing:
        meeting_report["status"] = "missing"
    elif (
        meeting_report["source_normalized_text_hash"]
        != meeting_report["v2_normalized_text_hash"]
    ):
        meeting_report["status"] = "hash_mismatch"
    else:
        meeting_report["status"] = "matched"


def _normalized_text_hash(texts: Any) -> str:
    digest = hashlib.sha256()
    for text in texts:
        _update_framed_hash(digest, _normalize_text(text))
    return digest.hexdigest()


def _normalize_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _update_framed_hash(digest: Any, value: str) -> None:
    encoded = value.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _ensure_marker_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {MARKER_TABLE} ("
        "migration_name TEXT NOT NULL, "
        "source_checksum TEXT NOT NULL, "
        "checksum_algorithm TEXT NOT NULL, "
        "status TEXT NOT NULL, "
        "backup_path TEXT NOT NULL, "
        "source_row_count INTEGER NOT NULL, "
        "started_at_ms INTEGER NOT NULL, "
        "completed_at_ms INTEGER, "
        "attempts INTEGER NOT NULL DEFAULT 1, "
        "report_json TEXT, "
        "error_text TEXT, "
        "PRIMARY KEY (migration_name, source_checksum)"
        ")"
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_error(error: BaseException) -> str:
    message = str(error).strip() or type(error).__name__
    return message[:2_000]


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
