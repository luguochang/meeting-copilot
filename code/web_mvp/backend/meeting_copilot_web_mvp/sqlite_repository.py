from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
import time
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Callable

from meeting_copilot_core.session_snapshot import build_session_snapshot
from meeting_copilot_web_mvp.asr_live_repository import (
    _coerce_epoch_ms,
    _stamp_created_record,
    _stamp_updated_record,
)
from meeting_copilot_web_mvp.application_schema import (
    APPLICATION_SCHEMA_VERSION,
    bootstrap_application_schema,
)
from meeting_copilot_web_mvp.repository import (
    SESSION_ID_PATTERN,
    SessionRecord,
    _validate_card_status_transition,
)
from meeting_copilot_web_mvp.storage_governance import (
    ensure_private_directory,
    harden_private_file,
    harden_sqlite_files,
)


SQLITE_SCHEMA_VERSION = APPLICATION_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_asr_live_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Extract indexable metadata columns from an ASR live session record."""
    source = str(record.get("source") or "")
    minutes = record.get("minutes") or {}
    has_minutes = 1 if (
        minutes.get("minutes_md") or minutes.get("minutes_json")
    ) else 0
    has_audio = 1 if (record.get("audio") or {}).get("saved") else 0
    suggestion_count = len(record.get("suggestion_cards") or [])
    created_at_ms = _coerce_epoch_ms(record.get("created_at_epoch_ms"), 0)
    last_activity_ms = _coerce_epoch_ms(
        record.get("last_activity_at_epoch_ms"), created_at_ms
    )
    return {
        "source": source,
        "has_minutes": has_minutes,
        "has_audio": has_audio,
        "suggestion_count": suggestion_count,
        "created_at_ms": created_at_ms,
        "last_activity_ms": last_activity_ms,
    }


def _serialize(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _hydrate_legacy_live_timestamps(record: dict[str, Any], path: Path) -> dict[str, Any]:
    if record.get("created_at_epoch_ms") and record.get("last_activity_at_epoch_ms"):
        return record
    fallback_ms = max(1, path.stat().st_mtime_ns // 1_000_000)
    hydrated = deepcopy(record)
    created_at_ms = _coerce_epoch_ms(
        hydrated.get("created_at_epoch_ms"),
        fallback_ms,
    )
    hydrated["created_at_epoch_ms"] = created_at_ms
    hydrated["last_activity_at_epoch_ms"] = max(
        created_at_ms,
        _coerce_epoch_ms(hydrated.get("last_activity_at_epoch_ms"), fallback_ms),
    )
    return hydrated


def _validate_session_id(session_id: str) -> str:
    value = str(session_id or "")
    if not SESSION_ID_PATTERN.fullmatch(value):
        raise ValueError(f"unsafe session_id: {value}")
    return value


class _SqliteRepositoryBase:
    def _open_database(self, data_dir: str | Path) -> None:
        self._data_dir = ensure_private_directory(data_dir)
        self._db_path = self._data_dir / "meeting_copilot.db"
        bootstrap_application_schema(self._db_path)
        harden_private_file(self._db_path)
        self._lock = RLock()
        self._closed = False
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0,
        )
        try:
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            harden_sqlite_files(self._db_path)
        except BaseException:
            self._conn.close()
            self._closed = True
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    @contextmanager
    def _locked(self):
        with self._lock:
            yield

    def _rollback_if_needed(self) -> BaseException | None:
        try:
            in_transaction = bool(self._conn.in_transaction)
        except (AttributeError, sqlite3.Error):
            in_transaction = True
        if not in_transaction:
            return None
        try:
            self._conn.execute("ROLLBACK")
        except BaseException as rollback_error:
            try:
                self._conn.close()
            finally:
                self._closed = True
            return rollback_error
        return None

    @contextmanager
    def _write_transaction(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException as exc:
                rollback_error = self._rollback_if_needed()
                if rollback_error is not None:
                    exc.add_note(
                        f"SQLite rollback failed and connection was closed: "
                        f"{type(rollback_error).__name__}"
                    )
                raise
            try:
                self._conn.execute("COMMIT")
            except BaseException as exc:
                rollback_error = self._rollback_if_needed()
                if rollback_error is not None:
                    exc.add_note(
                        f"SQLite rollback after COMMIT failure also failed; "
                        f"connection was closed: {type(rollback_error).__name__}"
                    )
                raise


# ---------------------------------------------------------------------------
# SqliteAsrLiveSessionRepository
# ---------------------------------------------------------------------------

_ASR_LIVE_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS asr_live_sessions ("
    "session_id TEXT PRIMARY KEY, "
    "record_json TEXT NOT NULL, "
    "created_at_ms INTEGER, "
    "last_activity_ms INTEGER, "
    "source TEXT, "
    "has_minutes INTEGER DEFAULT 0, "
    "has_audio INTEGER DEFAULT 0, "
    "suggestion_count INTEGER DEFAULT 0"
    ")"
)

_ASR_LIVE_INSERT_SQL = (
    "INSERT INTO asr_live_sessions "
    "(session_id, record_json, created_at_ms, last_activity_ms, "
    "source, has_minutes, has_audio, suggestion_count) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_ASR_LIVE_UPDATE_SQL = (
    "UPDATE asr_live_sessions SET "
    "record_json = ?, created_at_ms = ?, last_activity_ms = ?, "
    "source = ?, has_minutes = ?, has_audio = ?, suggestion_count = ? "
    "WHERE session_id = ?"
)


class SqliteAsrLiveSessionRepository(_SqliteRepositoryBase):
    """SQLite-backed drop-in replacement for ``JsonFileAsrLiveSessionRepository``."""

    def __init__(self, data_dir: str | Path) -> None:
        self._open_database(data_dir)
        self._ensure_table()

    # -- schema -----------------------------------------------------------

    def _ensure_table(self) -> None:
        self._conn.execute(_ASR_LIVE_CREATE_TABLE_SQL)
        self._conn.execute(_DELETED_SESSIONS_CREATE_TABLE_SQL)

    # -- public API -------------------------------------------------------

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_transaction():
            session_id = _validate_session_id(record["session_id"])
            if self._conn.execute(
                "SELECT 1 FROM deleted_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone() is not None:
                raise RuntimeError(f"ASR live session was deleted: {session_id}")
            stamped = _stamp_created_record(record)
            meta = _extract_asr_live_metadata(stamped)
            try:
                self._conn.execute(
                    _ASR_LIVE_INSERT_SQL,
                    (
                        session_id,
                        _serialize(stamped),
                        meta["created_at_ms"],
                        meta["last_activity_ms"],
                        meta["source"],
                        meta["has_minutes"],
                        meta["has_audio"],
                        meta["suggestion_count"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"ASR live session already exists: {session_id}"
                ) from exc
            return deepcopy(stamped)

    def get(self, session_id: str) -> dict[str, Any]:
        with self._locked():
            session_id = _validate_session_id(session_id)
            row = self._conn.execute(
                "SELECT record_json FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"ASR live session not found: {session_id}")
            return json.loads(row[0])

    def list(self) -> list[dict[str, Any]]:
        with self._locked():
            rows = self._conn.execute(
                "SELECT record_json FROM asr_live_sessions ORDER BY session_id"
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def replace(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_transaction():
            session_id = _validate_session_id(record["session_id"])
            row = self._conn.execute(
                "SELECT record_json FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"ASR live session not found: {session_id}")
            current = json.loads(row[0])
            stamped = _stamp_updated_record(current, record)
            meta = _extract_asr_live_metadata(stamped)
            self._conn.execute(
                _ASR_LIVE_UPDATE_SQL,
                (
                    _serialize(stamped),
                    meta["created_at_ms"],
                    meta["last_activity_ms"],
                    meta["source"],
                    meta["has_minutes"],
                    meta["has_audio"],
                    meta["suggestion_count"],
                    session_id,
                ),
            )
            return deepcopy(stamped)

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            row = self._conn.execute(
                "SELECT record_json FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"ASR live session not found: {session_id}")
            current = json.loads(row[0])
            updated = mutator(deepcopy(current))
            if str(updated.get("session_id") or "") != session_id:
                raise ValueError("ASR live session update cannot change session_id")
            stamped = _stamp_updated_record(current, updated)
            meta = _extract_asr_live_metadata(stamped)
            self._conn.execute(
                _ASR_LIVE_UPDATE_SQL,
                (
                    _serialize(stamped),
                    meta["created_at_ms"],
                    meta["last_activity_ms"],
                    meta["source"],
                    meta["has_minutes"],
                    meta["has_audio"],
                    meta["suggestion_count"],
                    session_id,
                ),
            )
            return deepcopy(stamped)

    def delete(self, session_id: str) -> bool:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            cursor = self._conn.execute(
                "DELETE FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            )
            if cursor.rowcount > 0:
                _write_deletion_tombstone(self._conn, session_id)
            return cursor.rowcount > 0

# ---------------------------------------------------------------------------
# SqliteSessionRepository
# ---------------------------------------------------------------------------

_SESSION_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS sessions ("
    "session_id TEXT PRIMARY KEY, "
    "record_json TEXT NOT NULL"
    ")"
)

_SESSION_UPSERT_SQL = (
    "INSERT INTO sessions (session_id, record_json) VALUES (?, ?) "
    "ON CONFLICT(session_id) DO UPDATE SET record_json = excluded.record_json"
)


class SqliteSessionRepository(_SqliteRepositoryBase):
    """SQLite-backed drop-in replacement for ``JsonFileSessionRepository``."""

    def __init__(self, data_dir: str | Path) -> None:
        self._open_database(data_dir)
        self._ensure_table()

    # -- schema -----------------------------------------------------------

    def _ensure_table(self) -> None:
        self._conn.execute(_SESSION_CREATE_TABLE_SQL)

    # -- public API -------------------------------------------------------

    def create(
        self,
        *,
        session_id: str,
        transcript_report: dict[str, Any],
        analysis: dict[str, Any],
        state_events: list[dict[str, Any]],
        llm_usage: dict[str, Any] | None = None,
        degradation_reasons: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            session_id = _validate_session_id(session_id)
            record = SessionRecord(
                session_id=session_id,
                transcript_report=deepcopy(transcript_report),
                analysis=deepcopy(analysis),
                state_events=deepcopy(state_events),
                llm_usage=deepcopy(llm_usage),
                degradation_reasons=list(degradation_reasons or []),
                metadata=deepcopy(metadata or {}),
            )
            snapshot = self._snapshot_from_record(record)
            self._write_record(record)
            return snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        with self._locked():
            session_id = _validate_session_id(session_id)
            return self._snapshot_from_record(self._record(session_id))

    def metadata(self, session_id: str) -> dict[str, Any]:
        with self._locked():
            session_id = _validate_session_id(session_id)
            return deepcopy(self._record(session_id).metadata)

    def set_card_status(self, session_id: str, card_id: str, status: str) -> dict[str, Any]:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            record = self._record(session_id)
            current_snapshot = self._snapshot_from_record(record)
            card_ids = {
                str(card.get("id", ""))
                for card in current_snapshot.get("suggestion_cards", [])
            }
            if card_id not in card_ids:
                raise KeyError(f"card not found: {card_id}")
            card = next(
                card
                for card in current_snapshot.get("suggestion_cards", [])
                if str(card.get("id", "")) == card_id
            )
            if str(card.get("show_or_silence_decision", "show")) != "show":
                raise ValueError(f"silenced suggestion card cannot be updated: {card_id}")
            candidate = SessionRecord(
                session_id=record.session_id,
                transcript_report=deepcopy(record.transcript_report),
                analysis=deepcopy(record.analysis),
                state_events=deepcopy(record.state_events),
                llm_usage=deepcopy(record.llm_usage),
                degradation_reasons=list(record.degradation_reasons),
                card_statuses={**record.card_statuses, card_id: status},
                metadata=deepcopy(record.metadata),
            )
            snapshot = self._snapshot_from_record(candidate)
            _validate_card_status_transition(
                card_id=card_id,
                current_status=str(card.get("status", "new")),
                next_status=status,
            )
            self._write_record(candidate)
            return snapshot

    def delete(self, session_id: str) -> bool:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            if cursor.rowcount > 0:
                _write_deletion_tombstone(self._conn, session_id)
            return cursor.rowcount > 0

    def exists(self, session_id: str) -> bool:
        with self._locked():
            session_id = _validate_session_id(session_id)
            row = self._conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row is not None

    # -- internals --------------------------------------------------------

    def _snapshot_from_record(self, record: SessionRecord) -> dict[str, Any]:
        return build_session_snapshot(
            session_id=record.session_id,
            transcript_report=record.transcript_report,
            analysis=record.analysis,
            state_events=record.state_events,
            llm_usage=record.llm_usage,
            card_statuses=record.card_statuses,
            degradation_reasons=record.degradation_reasons,
        )

    def _record(self, session_id: str) -> SessionRecord:
        session_id = _validate_session_id(session_id)
        row = self._conn.execute(
            "SELECT record_json FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"session not found: {session_id}")
        data = json.loads(row[0])
        return SessionRecord(
            session_id=str(data["session_id"]),
            transcript_report=deepcopy(data["transcript_report"]),
            analysis=deepcopy(data["analysis"]),
            state_events=deepcopy(data["state_events"]),
            llm_usage=deepcopy(data.get("llm_usage")),
            degradation_reasons=list(data.get("degradation_reasons") or []),
            card_statuses=deepcopy(data.get("card_statuses") or {}),
            metadata=deepcopy(data.get("metadata") or {}),
        )

    def _write_record(self, record: SessionRecord) -> None:
        payload = {
            "session_id": record.session_id,
            "transcript_report": record.transcript_report,
            "analysis": record.analysis,
            "state_events": record.state_events,
            "llm_usage": record.llm_usage,
            "degradation_reasons": record.degradation_reasons,
            "card_statuses": record.card_statuses,
            "metadata": record.metadata,
        }
        self._conn.execute(
            _SESSION_UPSERT_SQL,
            (
                record.session_id,
                _serialize(payload),
            ),
        )

# ---------------------------------------------------------------------------
# Atomic session deletion and durable audio cleanup
# ---------------------------------------------------------------------------

_PENDING_AUDIO_CLEANUP_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS pending_audio_cleanup ("
    "session_id TEXT PRIMARY KEY, "
    "audio_json TEXT NOT NULL"
    ")"
)

_DELETED_SESSIONS_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS deleted_sessions ("
    "session_id TEXT PRIMARY KEY, "
    "deleted_at_ms INTEGER NOT NULL"
    ")"
)


def _write_deletion_tombstone(
    connection: sqlite3.Connection,
    session_id: str,
) -> None:
    connection.execute(
        "INSERT INTO deleted_sessions (session_id, deleted_at_ms) VALUES (?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET deleted_at_ms = excluded.deleted_at_ms",
        (session_id, time.time_ns() // 1_000_000),
    )


def _validated_audio_cleanup_metadata(
    session_id: str,
    audio: dict[str, Any] | None,
) -> dict[str, str] | None:
    session_id = _validate_session_id(session_id)
    relative_path = str((audio or {}).get("relative_path") or "").strip()
    if not relative_path:
        return None
    path = PurePosixPath(relative_path.replace("\\", "/"))
    parts = path.parts
    if (
        path.is_absolute()
        or ".." in parts
        or len(parts) < 3
        or parts[0] != "audio_assets"
        or parts[1] != session_id
    ):
        raise ValueError("audio cleanup relative_path is not owned by session")
    return {"relative_path": path.as_posix()}


class SqlitePersistenceCoordinator(_SqliteRepositoryBase):
    """Coordinates cross-table deletes and durable filesystem cleanup jobs."""

    def __init__(self, data_dir: str | Path) -> None:
        self._open_database(data_dir)
        self._conn.execute(_ASR_LIVE_CREATE_TABLE_SQL)
        self._conn.execute(_SESSION_CREATE_TABLE_SQL)
        self._conn.execute(_PENDING_AUDIO_CLEANUP_CREATE_TABLE_SQL)
        self._conn.execute(_DELETED_SESSIONS_CREATE_TABLE_SQL)

    def pending_audio_cleanup(self, session_id: str) -> dict[str, str] | None:
        with self._locked():
            session_id = _validate_session_id(session_id)
            return self._pending_audio_cleanup(session_id)

    def clear_pending_audio_cleanup(self, session_id: str) -> bool:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            cursor = self._conn.execute(
                "DELETE FROM pending_audio_cleanup WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0

    def delete_live_session(self, session_id: str) -> dict[str, Any]:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            pending = self._pending_audio_cleanup(session_id)
            row = self._conn.execute(
                "SELECT record_json FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                if pending is None:
                    raise KeyError(f"ASR live session not found: {session_id}")
                return {
                    "session_id": session_id,
                    "live_session_record_deleted": False,
                    "audio_cleanup": pending,
                    "cleanup_retry": True,
                }
            _write_deletion_tombstone(self._conn, session_id)
            record = json.loads(row[0])
            audio_cleanup = _validated_audio_cleanup_metadata(
                session_id,
                dict(record.get("audio") or {}),
            )
            if audio_cleanup is not None:
                self._stage_audio_cleanup(session_id, audio_cleanup)
            elif pending is not None:
                audio_cleanup = pending
            cursor = self._conn.execute(
                "DELETE FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            )
            return {
                "session_id": session_id,
                "live_session_record_deleted": cursor.rowcount > 0,
                "audio_cleanup": audio_cleanup,
                "cleanup_retry": False,
            }

    def delete_session_bundle(self, session_id: str) -> dict[str, Any]:
        with self._write_transaction():
            session_id = _validate_session_id(session_id)
            pending = self._pending_audio_cleanup(session_id)
            live_row = self._conn.execute(
                "SELECT record_json FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            session_exists = self._conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone() is not None
            if not session_exists and live_row is None and pending is None:
                raise KeyError(f"session not found: {session_id}")
            if session_exists or live_row is not None:
                _write_deletion_tombstone(self._conn, session_id)
            audio_cleanup = pending
            if live_row is not None:
                record = json.loads(live_row[0])
                staged = _validated_audio_cleanup_metadata(
                    session_id,
                    dict(record.get("audio") or {}),
                )
                if staged is not None:
                    self._stage_audio_cleanup(session_id, staged)
                    audio_cleanup = staged
            session_cursor = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            live_cursor = self._conn.execute(
                "DELETE FROM asr_live_sessions WHERE session_id = ?",
                (session_id,),
            )
            return {
                "session_id": session_id,
                "session_record_deleted": session_cursor.rowcount > 0,
                "live_session_record_deleted": live_cursor.rowcount > 0,
                "audio_cleanup": audio_cleanup,
                "cleanup_retry": not session_exists and live_row is None,
            }

    def _pending_audio_cleanup(self, session_id: str) -> dict[str, str] | None:
        row = self._conn.execute(
            "SELECT audio_json FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return _validated_audio_cleanup_metadata(session_id, json.loads(row[0]))

    def _stage_audio_cleanup(
        self,
        session_id: str,
        audio_cleanup: dict[str, str],
    ) -> None:
        self._conn.execute(
            "INSERT INTO pending_audio_cleanup (session_id, audio_json) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET audio_json = excluded.audio_json",
            (session_id, _serialize(audio_cleanup)),
        )


# ---------------------------------------------------------------------------
# Settings and LLM usage ledger
# ---------------------------------------------------------------------------

_SETTINGS_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS app_settings ("
    "singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1), "
    "settings_json TEXT NOT NULL"
    ")"
)

_LLM_USAGE_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS llm_usage_ledger ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "session_id TEXT NOT NULL, "
    "purpose TEXT NOT NULL, "
    "provider TEXT NOT NULL, "
    "model TEXT NOT NULL, "
    "prompt_tokens INTEGER NOT NULL, "
    "completion_tokens INTEGER NOT NULL, "
    "total_tokens INTEGER NOT NULL, "
    "timestamp_ms INTEGER NOT NULL"
    ")"
)

_LLM_USAGE_TIME_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp "
    "ON llm_usage_ledger(timestamp_ms)"
)

_LLM_USAGE_SESSION_TIME_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_session_timestamp "
    "ON llm_usage_ledger(session_id, timestamp_ms)"
)


class InMemorySettingsUsageRepository:
    """Per-app settings and usage storage used when no data directory is configured."""

    def __init__(self, default_settings: dict[str, Any]) -> None:
        self._settings = deepcopy(default_settings)
        self._usage: list[dict[str, Any]] = []
        self._lock = RLock()

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def replace_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._settings = deepcopy(settings)
            return deepcopy(self._settings)

    def record_usage(
        self,
        *,
        session_id: str,
        purpose: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        timestamp_ms: int,
    ) -> dict[str, Any]:
        record = _usage_record(
            session_id=session_id,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            timestamp_ms=timestamp_ms,
        )
        with self._lock:
            self._usage.append(record)
        return deepcopy(record)

    def list_usage(
        self,
        *,
        since_ms: int | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = [
                deepcopy(record)
                for record in self._usage
                if (since_ms is None or int(record["timestamp_ms"]) >= since_ms)
                and (session_id is None or record["session_id"] == session_id)
            ]
        return sorted(records, key=lambda record: int(record["timestamp_ms"]))


class SqliteSettingsUsageRepository(_SqliteRepositoryBase):
    """SQLite persistence for strict non-sensitive settings and paid LLM usage."""

    def __init__(self, data_dir: str | Path, default_settings: dict[str, Any]) -> None:
        self._default_settings = deepcopy(default_settings)
        self._open_database(data_dir)
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        self._conn.execute(_SETTINGS_CREATE_TABLE_SQL)
        self._conn.execute(_LLM_USAGE_CREATE_TABLE_SQL)
        self._conn.execute(_LLM_USAGE_TIME_INDEX_SQL)
        self._conn.execute(_LLM_USAGE_SESSION_TIME_INDEX_SQL)

    def get_settings(self) -> dict[str, Any]:
        with self._locked():
            row = self._conn.execute(
                "SELECT settings_json FROM app_settings WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                return deepcopy(self._default_settings)
            return deepcopy(json.loads(row[0]))

    def replace_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(settings)
        with self._locked():
            self._conn.execute(
                "INSERT INTO app_settings (singleton_id, settings_json) VALUES (1, ?) "
                "ON CONFLICT(singleton_id) DO UPDATE SET settings_json = excluded.settings_json",
                (_serialize(payload),),
            )
        return payload

    def record_usage(
        self,
        *,
        session_id: str,
        purpose: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        timestamp_ms: int,
    ) -> dict[str, Any]:
        record = _usage_record(
            session_id=session_id,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            timestamp_ms=timestamp_ms,
        )
        with self._locked():
            self._conn.execute(
                "INSERT INTO llm_usage_ledger "
                "(session_id, purpose, provider, model, prompt_tokens, "
                "completion_tokens, total_tokens, timestamp_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["session_id"],
                    record["purpose"],
                    record["provider"],
                    record["model"],
                    record["prompt_tokens"],
                    record["completion_tokens"],
                    record["total_tokens"],
                    record["timestamp_ms"],
                ),
            )
        return deepcopy(record)

    def list_usage(
        self,
        *,
        since_ms: int | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        parameters: list[Any] = []
        if since_ms is not None:
            where.append("timestamp_ms >= ?")
            parameters.append(max(0, int(since_ms)))
        if session_id is not None:
            where.append("session_id = ?")
            parameters.append(str(session_id))
        query = (
            "SELECT session_id, purpose, provider, model, prompt_tokens, "
            "completion_tokens, total_tokens, timestamp_ms FROM llm_usage_ledger"
        )
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY timestamp_ms, id"
        with self._locked():
            rows = self._conn.execute(query, parameters).fetchall()
        return [
            {
                "session_id": row[0],
                "purpose": row[1],
                "provider": row[2],
                "model": row[3],
                "prompt_tokens": int(row[4]),
                "completion_tokens": int(row[5]),
                "total_tokens": int(row[6]),
                "timestamp_ms": int(row[7]),
            }
            for row in rows
        ]

def _usage_record(
    *,
    session_id: str,
    purpose: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    timestamp_ms: int,
) -> dict[str, Any]:
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    total = max(prompt + completion, int(total_tokens or 0))
    return {
        "session_id": _validate_session_id(session_id),
        "purpose": str(purpose),
        "provider": str(provider),
        "model": str(model),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "timestamp_ms": max(0, int(timestamp_ms)),
    }


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def _safe_legacy_path(path: Path, data_dir: Path) -> str:
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return path.name


def _read_legacy_json(path: Path, data_dir: Path) -> dict[str, Any]:
    safe_path = _safe_legacy_path(path, data_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"legacy JSON migration failed for {safe_path}: "
            f"unable to read file ({type(exc).__name__})"
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"legacy JSON migration failed for {safe_path}: invalid JSON"
        ) from exc


def _validated_legacy_session_id(
    record: dict[str, Any],
    path: Path,
    data_dir: Path,
) -> str:
    try:
        return _validate_session_id(str(record.get("session_id") or path.stem))
    except ValueError as exc:
        raise ValueError(
            f"legacy JSON migration failed for {_safe_legacy_path(path, data_dir)}: "
            "invalid session_id"
        ) from exc


def migrate_json_to_sqlite(data_dir: str | Path, db_path: str | Path) -> int:
    """Migrate existing JSON file repository data into SQLite.

    Reads ASR live session records from ``{data_dir}/live_asr_sessions/*.json``
    and session records from ``{data_dir}/sessions/*.json``, then inserts them
    into the SQLite database at *db_path*. Existing SQLite rows always win over
    legacy JSON so later startups cannot overwrite newer data. Returns the total
    number of valid legacy records processed.
    """
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    bootstrap_application_schema(db_path)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        prepared_live_records: list[tuple[str, dict[str, Any]]] = []
        asr_dir = data_dir / "live_asr_sessions"
        if asr_dir.exists():
            for path in sorted(asr_dir.glob("*.json")):
                record = _hydrate_legacy_live_timestamps(
                    _read_legacy_json(path, data_dir),
                    path,
                )
                session_id = _validated_legacy_session_id(record, path, data_dir)
                prepared_live_records.append((session_id, record))

        prepared_session_records: list[tuple[str, dict[str, Any]]] = []
        sessions_dir = data_dir / "sessions"
        if sessions_dir.exists():
            for path in sorted(sessions_dir.glob("*.json")):
                record = _read_legacy_json(path, data_dir)
                session_id = _validated_legacy_session_id(record, path, data_dir)
                prepared_session_records.append((session_id, record))

        conn.execute("BEGIN IMMEDIATE")
        try:
            deleted_session_ids = {
                str(row[0])
                for row in conn.execute(
                    "SELECT session_id FROM deleted_sessions"
                ).fetchall()
            }
            for session_id, record in prepared_live_records:
                if session_id in deleted_session_ids:
                    continue
                meta = _extract_asr_live_metadata(record)
                conn.execute(
                    "INSERT OR IGNORE INTO asr_live_sessions "
                    "(session_id, record_json, created_at_ms, last_activity_ms, "
                    "source, has_minutes, has_audio, suggestion_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        _serialize(record),
                        meta["created_at_ms"],
                        meta["last_activity_ms"],
                        meta["source"],
                        meta["has_minutes"],
                        meta["has_audio"],
                        meta["suggestion_count"],
                    ),
                )
            for session_id, record in prepared_session_records:
                if session_id in deleted_session_ids:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (session_id, record_json) "
                    "VALUES (?, ?)",
                    (session_id, _serialize(record)),
                )
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        try:
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

        return len(prepared_live_records) + len(prepared_session_records)
    finally:
        conn.close()
