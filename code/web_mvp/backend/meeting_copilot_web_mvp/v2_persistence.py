from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator, Mapping

from .meeting_state_extractor import extract_meeting_state
from .audio_assets import audio_chunk_journal_sha256


JOB_STATUSES = (
    "pending",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
)

DEFAULT_EVENT_PAGE_LIMIT = 200
MAX_EVENT_PAGE_LIMIT = 1_000


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('live', 'ending', 'ended', 'interrupted')),
    title TEXT,
    started_at_ms INTEGER,
    ended_at_ms INTEGER,
    latest_seq INTEGER NOT NULL DEFAULT 0 CHECK (latest_seq >= 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_events (
    meeting_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK (seq > 0),
    event_id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    occurred_at_ms INTEGER NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    published_at_ms INTEGER,
    PRIMARY KEY (meeting_id, seq),
    UNIQUE (meeting_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_meeting_events_unpublished
    ON meeting_events(published_at_ms, occurred_at_ms)
    WHERE published_at_ms IS NULL;

CREATE TABLE IF NOT EXISTS transcript_segments (
    meeting_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    final_id TEXT NOT NULL,
    transcript_seq INTEGER NOT NULL CHECK (transcript_seq > 0),
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    started_at_ms INTEGER,
    ended_at_ms INTEGER,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    evidence_hash TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, segment_id),
    UNIQUE (meeting_id, final_id),
    UNIQUE (meeting_id, transcript_seq)
);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_meeting_order
    ON transcript_segments(meeting_id, transcript_seq);

CREATE TABLE IF NOT EXISTS meeting_entities (
    meeting_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('current_topic', 'open_question')),
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    evidence_segment_ids_json TEXT NOT NULL,
    updated_at_ms INTEGER,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    first_seen_seq INTEGER NOT NULL DEFAULT 1 CHECK (first_seen_seq > 0),
    last_updated_seq INTEGER NOT NULL DEFAULT 1 CHECK (last_updated_seq > 0),
    PRIMARY KEY (meeting_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_meeting_entities_projection
    ON meeting_entities(meeting_id, kind, updated_at_ms);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')
    ),
    priority INTEGER NOT NULL DEFAULT 0,
    input_transcript_seq INTEGER NOT NULL CHECK (input_transcript_seq > 0),
    input_version INTEGER NOT NULL CHECK (input_version > 0),
    evidence_segment_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    generation_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    lease_owner TEXT,
    lease_until_ms INTEGER,
    next_attempt_at_ms INTEGER NOT NULL,
    deadline_at_ms INTEGER,
    output_json TEXT,
    error_class TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    FOREIGN KEY (meeting_id, evidence_segment_id)
        REFERENCES transcript_segments(meeting_id, segment_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs(kind, status, next_attempt_at_ms, priority DESC, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_jobs_expired_lease
    ON jobs(status, lease_until_ms)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS suggestions (
    suggestion_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    job_id TEXT UNIQUE,
    generation_id TEXT NOT NULL,
    evidence_segment_id TEXT NOT NULL,
    evidence_transcript_seq INTEGER NOT NULL CHECK (evidence_transcript_seq > 0),
    evidence_hash TEXT NOT NULL,
    state_revision INTEGER NOT NULL CHECK (state_revision > 0),
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'committed', 'rejected', 'superseded')
    ),
    draft_text TEXT NOT NULL DEFAULT '',
    draft_seq INTEGER NOT NULL DEFAULT 0 CHECK (draft_seq >= 0),
    text TEXT,
    final_draft_seq INTEGER,
    feedback TEXT CHECK (
        feedback IS NULL OR feedback IN ('kept', 'ignored', 'false_positive', 'too_late')
    ),
    feedback_at_ms INTEGER,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    committed_at_ms INTEGER,
    UNIQUE (meeting_id, generation_id),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
    FOREIGN KEY (meeting_id, evidence_segment_id)
        REFERENCES transcript_segments(meeting_id, segment_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_suggestions_meeting
    ON suggestions(meeting_id, evidence_transcript_seq, created_at_ms);

CREATE TABLE IF NOT EXISTS minutes (
    meeting_id TEXT PRIMARY KEY,
    job_id TEXT,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('ready', 'degraded')),
    markdown TEXT NOT NULL,
    structured_json TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS approach_artifacts (
    meeting_id TEXT PRIMARY KEY,
    job_id TEXT,
    cards_json TEXT NOT NULL,
    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS search_documents (
    meeting_id TEXT PRIMARY KEY,
    transcript_text TEXT NOT NULL,
    transcript_hash TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_chunks (
    meeting_id TEXT NOT NULL,
    track TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    chunk_seq INTEGER NOT NULL CHECK (chunk_seq >= 0),
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    sample_rate_hz INTEGER NOT NULL CHECK (sample_rate_hz > 0),
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    duration_ms INTEGER NOT NULL CHECK (duration_ms > 0),
    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes > 0),
    status TEXT NOT NULL CHECK (status IN ('committed', 'missing', 'corrupted')),
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, track, epoch, chunk_seq)
);

CREATE TABLE IF NOT EXISTS recording_sessions (
    meeting_id TEXT NOT NULL,
    track TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    source_type TEXT NOT NULL,
    capture_generation INTEGER NOT NULL DEFAULT 1 CHECK (capture_generation > 0),
    status TEXT NOT NULL CHECK (
        status IN ('active', 'sealed', 'exporting', 'ready', 'interrupted', 'failed')
    ),
    sample_rate_hz INTEGER NOT NULL CHECK (sample_rate_hz > 0),
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    file_size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (file_size_bytes >= 0),
    lease_owner TEXT,
    lease_until_ms INTEGER,
    output_relative_path TEXT,
    journal_sha256 TEXT,
    output_sha256 TEXT,
    output_file_size_bytes INTEGER,
    error_class TEXT,
    started_at_ms INTEGER NOT NULL,
    sealed_at_ms INTEGER,
    completed_at_ms INTEGER,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, track, epoch)
);

CREATE INDEX IF NOT EXISTS idx_recording_sessions_expired_lease
    ON recording_sessions(status, lease_until_ms)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS recording_exports (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    track TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')
    ),
    output_relative_path TEXT NOT NULL,
    input_chunk_count INTEGER NOT NULL CHECK (input_chunk_count > 0),
    input_sample_count INTEGER NOT NULL CHECK (input_sample_count > 0),
    input_journal_sha256 TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    lease_owner TEXT,
    lease_until_ms INTEGER,
    next_attempt_at_ms INTEGER NOT NULL,
    output_json TEXT,
    error_class TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    UNIQUE (meeting_id, track, epoch),
    FOREIGN KEY (meeting_id, track, epoch)
        REFERENCES recording_sessions(meeting_id, track, epoch)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recording_exports_claim
    ON recording_exports(status, next_attempt_at_ms, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_recording_exports_expired_lease
    ON recording_exports(status, lease_until_ms)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS meeting_tombstones (
    meeting_id TEXT PRIMARY KEY,
    deletion_job_id TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deletion_jobs (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    paths_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_class TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_deletion_jobs_pending
    ON deletion_jobs(status, updated_at_ms);
"""


def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def transcript_evidence_hash(segment_id: str, normalized_text: str) -> str:
    """Return the canonical evidence identity for one transcript revision."""

    segment_id = _required(segment_id, "segment_id")
    normalized_text = _required(normalized_text, "normalized_text")
    return hashlib.sha256(f"{segment_id}\x1f{normalized_text}".encode("utf-8")).hexdigest()


class StaleEvidenceError(RuntimeError):
    """A durable operation referenced a transcript revision that no longer exists."""

    retryable = False


class JobLeaseLostError(RuntimeError):
    """A streaming side effect arrived after its durable job lease was lost."""

    retryable = False


class MeetingDeletedError(RuntimeError):
    """A durable deletion fence rejected a late meeting write."""

    retryable = False


class RecordingRecoveryConflict(RuntimeError):
    """A capture resumed after recovery selected an expired lease snapshot."""

    retryable = True


class V2Persistence:
    """Additive normalized persistence and durable outbox/job storage.

    Normalized rows are the source of truth. ``meeting_events`` is an outbox
    used to notify consumers after the owning transaction commits; it is not a
    second event-sourced copy of meeting state.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._closed = False
        self._conn = sqlite3.connect(
            str(self.database_path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._ensure_additive_columns()
        except BaseException:
            self._conn.close()
            self._closed = True
            raise

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _ensure_additive_columns(self) -> None:
        columns = {str(row["name"]) for row in self._conn.execute("PRAGMA table_info(suggestions)").fetchall()}
        if "feedback" not in columns:
            self._conn.execute("ALTER TABLE suggestions ADD COLUMN feedback TEXT")
        if "feedback_at_ms" not in columns:
            self._conn.execute("ALTER TABLE suggestions ADD COLUMN feedback_at_ms INTEGER")
        entity_columns = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(meeting_entities)").fetchall()
        }
        for column in ("version", "first_seen_seq", "last_updated_seq"):
            if column not in entity_columns:
                self._conn.execute(f"ALTER TABLE meeting_entities ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1")
        recording_columns = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(recording_sessions)").fetchall()
        }
        if "journal_sha256" not in recording_columns:
            self._conn.execute("ALTER TABLE recording_sessions ADD COLUMN journal_sha256 TEXT")
        if "capture_generation" not in recording_columns:
            self._conn.execute(
                "ALTER TABLE recording_sessions ADD COLUMN capture_generation INTEGER NOT NULL DEFAULT 1"
            )
        export_columns = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(recording_exports)").fetchall()
        }
        if "input_journal_sha256" not in export_columns:
            self._conn.execute("ALTER TABLE recording_exports ADD COLUMN input_journal_sha256 TEXT")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise RuntimeError("V2Persistence is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
            try:
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _next_event_seq_locked(self, meeting_id: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM meeting_events WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()[0]
        )

    def _raise_if_tombstoned_locked(self, meeting_id: str) -> None:
        if (
            self._conn.execute(
                "SELECT 1 FROM meeting_tombstones WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            is not None
        ):
            raise MeetingDeletedError(f"meeting was deleted: {meeting_id}")

    def _append_event_locked(
        self,
        *,
        meeting_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        occurred_at_ms: int,
        idempotency_key: str,
        payload: Mapping[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> int:
        event_seq = self._next_event_seq_locked(meeting_id)
        event_id = _stable_id("evt", meeting_id, idempotency_key)
        self._conn.execute(
            "INSERT INTO meeting_events ("
            "meeting_id, seq, event_id, type, aggregate_type, aggregate_id, "
            "occurred_at_ms, correlation_id, causation_id, idempotency_key, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meeting_id,
                event_seq,
                event_id,
                event_type,
                aggregate_type,
                aggregate_id,
                occurred_at_ms,
                correlation_id,
                causation_id,
                idempotency_key,
                _json_dump(payload),
            ),
        )
        self._conn.execute(
            "UPDATE meetings SET latest_seq = ?, revision = revision + 1, updated_at_ms = ? WHERE id = ?",
            (event_seq, occurred_at_ms, meeting_id),
        )
        return event_seq

    def _meeting_state_locked(self, meeting_id: str) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT * FROM meeting_entities WHERE meeting_id = ? ORDER BY kind, updated_at_ms, entity_id",
            (meeting_id,),
        ).fetchall()
        topic = None
        questions: list[dict[str, Any]] = []
        for row in rows:
            entity = {
                "id": row["entity_id"],
                "text": row["text"],
                "status": row["status"],
                "evidence_segment_ids": json.loads(row["evidence_segment_ids_json"]),
                "updated_at_ms": row["updated_at_ms"],
                "version": int(row["version"]),
                "first_seen_seq": int(row["first_seen_seq"]),
                "last_updated_seq": int(row["last_updated_seq"]),
            }
            if row["kind"] == "current_topic":
                topic = entity
            else:
                questions.append(entity)
        return {"current_topic": topic, "open_questions": questions[-3:]}

    def _update_meeting_state_locked(
        self,
        *,
        meeting_id: str,
        segments: list[Mapping[str, Any]],
        now_ms: int,
        causation_id: str,
    ) -> None:
        projected = extract_meeting_state(
            segments,
            previous_state=self._meeting_state_locked(meeting_id),
        )
        current_transcript_seq = max(
            (int(segment.get("transcript_seq") or 0) for segment in segments),
            default=0,
        )
        if current_transcript_seq <= 0:
            raise ValueError("meeting state update requires a positive transcript_seq")
        topic = projected.get("current_topic")
        if isinstance(topic, Mapping):
            stored_topic = self._upsert_entity_locked(
                meeting_id,
                "current_topic",
                topic,
                current_transcript_seq=current_transcript_seq,
            )
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.topic.updated",
                aggregate_type="meeting_entity",
                aggregate_id=str(topic["id"]),
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.topic:{causation_id}:{topic['id']}",
                payload={"topic": stored_topic},
                correlation_id=meeting_id,
                causation_id=causation_id,
            )

        projected_questions = [
            question for question in projected.get("open_questions") or [] if isinstance(question, Mapping)
        ]
        for question in projected_questions:
            if not isinstance(question, Mapping):
                continue
            stored_question = self._upsert_entity_locked(
                meeting_id,
                "open_question",
                question,
                current_transcript_seq=current_transcript_seq,
            )
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.open_question.updated",
                aggregate_type="meeting_entity",
                aggregate_id=str(question["id"]),
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.question:{causation_id}:{question['id']}",
                payload={"question": stored_question},
                correlation_id=meeting_id,
                causation_id=causation_id,
            )

    def _upsert_entity_locked(
        self,
        meeting_id: str,
        kind: str,
        entity: Mapping[str, Any],
        *,
        current_transcript_seq: int,
    ) -> dict[str, Any]:
        entity_id = str(entity["id"])
        status = str(entity.get("status") or "unknown")
        text = str(entity["text"])
        updated_at_ms = entity.get("updated_at_ms")
        existing = self._conn.execute(
            "SELECT * FROM meeting_entities WHERE meeting_id = ? AND entity_id = ?",
            (meeting_id, entity_id),
        ).fetchone()
        evidence_segment_ids = list(entity.get("evidence_segment_ids") or [])
        if existing is not None and kind == "open_question":
            evidence_segment_ids = list(
                dict.fromkeys(
                    [
                        *json.loads(existing["evidence_segment_ids_json"]),
                        *evidence_segment_ids,
                    ]
                )
            )
        evidence_json = _json_dump(evidence_segment_ids)
        changed = existing is None or any(
            existing[field] != value
            for field, value in (
                ("kind", kind),
                ("status", status),
                ("text", text),
                ("evidence_segment_ids_json", evidence_json),
                ("updated_at_ms", updated_at_ms),
            )
        )
        version = 1 if existing is None else int(existing["version"]) + int(changed)
        first_seen_seq = current_transcript_seq if existing is None else int(existing["first_seen_seq"])
        last_updated_seq = current_transcript_seq if existing is None or changed else int(existing["last_updated_seq"])
        self._conn.execute(
            "INSERT INTO meeting_entities ("
            "meeting_id, entity_id, kind, status, text, evidence_segment_ids_json, updated_at_ms, "
            "version, first_seen_seq, last_updated_seq"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(meeting_id, entity_id) DO UPDATE SET "
            "kind = excluded.kind, status = excluded.status, text = excluded.text, "
            "evidence_segment_ids_json = excluded.evidence_segment_ids_json, "
            "updated_at_ms = excluded.updated_at_ms, version = excluded.version, "
            "first_seen_seq = excluded.first_seen_seq, last_updated_seq = excluded.last_updated_seq",
            (
                meeting_id,
                entity_id,
                kind,
                status,
                text,
                evidence_json,
                updated_at_ms,
                version,
                first_seen_seq,
                last_updated_seq,
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM meeting_entities WHERE meeting_id = ? AND entity_id = ?",
            (meeting_id, entity_id),
        ).fetchone()
        return self._entity_dict(row)

    def create_meeting(
        self,
        *,
        meeting_id: str,
        title: str | None,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        normalized_title = str(title or "").strip() or None
        if normalized_title is not None and len(normalized_title) > 200:
            raise ValueError("title must not exceed 200 characters")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            result = self._conn.execute(
                "INSERT OR IGNORE INTO meetings ("
                "id, state, title, started_at_ms, latest_seq, revision, "
                "created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, ?, 0, 1, ?, ?)",
                (meeting_id, normalized_title, now_ms, now_ms, now_ms),
            )
            if result.rowcount == 1:
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="meeting.started",
                    aggregate_type="meeting",
                    aggregate_id=meeting_id,
                    occurred_at_ms=now_ms,
                    idempotency_key="meeting.started",
                    payload={
                        "meeting_id": meeting_id,
                        "state": "live",
                        "title": normalized_title,
                        "started_at_ms": now_ms,
                    },
                    correlation_id=meeting_id,
                )
            elif normalized_title is not None:
                self._conn.execute(
                    "UPDATE meetings SET title = COALESCE(title, ?), updated_at_ms = ? WHERE id = ?",
                    (normalized_title, now_ms, meeting_id),
                )
            row = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
        return self._meeting_dict(row)

    def finalize_migration_only_meeting(
        self,
        *,
        meeting_id: str,
        source_checksum: str,
        now_ms: int,
    ) -> bool:
        meeting_id = _required(meeting_id, "meeting_id")
        source_checksum = _required(source_checksum, "source_checksum")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            meeting = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
            if meeting is None:
                return False
            final_events = self._conn.execute(
                "SELECT causation_id FROM meeting_events WHERE meeting_id = ? "
                "AND type = 'transcript.segment.finalized'",
                (meeting_id,),
            ).fetchall()
            if not final_events or any(
                not str(row["causation_id"] or "").startswith("migration:") for row in final_events
            ):
                return False
            self._conn.execute(
                "UPDATE meetings SET state = 'ended', "
                "started_at_ms = CASE WHEN COALESCE(started_at_ms, 0) < 1000000000000 "
                "THEN ? ELSE started_at_ms END, "
                "ended_at_ms = CASE WHEN COALESCE(ended_at_ms, 0) < 1000000000000 "
                "THEN ? ELSE ended_at_ms END, "
                "created_at_ms = CASE WHEN created_at_ms < 1000000000000 "
                "THEN ? ELSE created_at_ms END, "
                "revision = revision + 1, "
                "updated_at_ms = CASE WHEN updated_at_ms < 1000000000000 "
                "THEN ? ELSE MAX(updated_at_ms, ?) END WHERE id = ? AND ("
                "state != 'ended' OR COALESCE(started_at_ms, 0) < 1000000000000 OR "
                "COALESCE(ended_at_ms, 0) < 1000000000000 OR "
                "created_at_ms < 1000000000000 OR updated_at_ms < 1000000000000)",
                (now_ms, now_ms, now_ms, now_ms, now_ms, meeting_id),
            )
            idempotency_key = f"meeting.migration.completed:{source_checksum}"
            existing = self._conn.execute(
                "SELECT 1 FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, idempotency_key),
            ).fetchone()
            if existing is None:
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="meeting.migration.completed",
                    aggregate_type="meeting",
                    aggregate_id=meeting_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=idempotency_key,
                    payload={"meeting_id": meeting_id, "state": "ended"},
                    correlation_id=f"migration:{source_checksum}",
                )
        return True

    def commit_final_and_enqueue(
        self,
        *,
        meeting_id: str,
        final_id: str,
        segment_id: str,
        text: str,
        normalized_text: str,
        started_at_ms: int | None,
        ended_at_ms: int | None,
        evidence_hash: str,
        now_ms: int,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        max_attempts: int = 3,
        enqueue_jobs: bool = True,
    ) -> dict[str, Any]:
        """Commit one final and its two AI jobs as a single durable unit."""

        meeting_id = _required(meeting_id, "meeting_id")
        final_id = _required(final_id, "final_id")
        segment_id = _required(segment_id, "segment_id")
        text = _required(text, "text")
        normalized_text = _required(normalized_text, "normalized_text")
        evidence_hash = _required(evidence_hash, "evidence_hash")
        now_ms = max(0, int(now_ms))
        max_attempts = int(max_attempts)
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        event_id = _stable_id("evt", meeting_id, f"transcript.final:{final_id}")
        correction_job_id = _stable_id("job", meeting_id, final_id, "correction")
        suggestion_job_id = _stable_id("job", meeting_id, final_id, "suggestion")
        generation_id = f"suggestion:{meeting_id}:{final_id}"

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            self._conn.execute(
                "INSERT INTO meetings ("
                "id, state, started_at_ms, latest_seq, revision, created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, 0, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "state = CASE WHEN meetings.state = 'ended' THEN meetings.state ELSE 'live' END, "
                "updated_at_ms = excluded.updated_at_ms",
                (meeting_id, started_at_ms, now_ms, now_ms),
            )
            existing = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? AND final_id = ?",
                (meeting_id, final_id),
            ).fetchone()
            created = existing is None
            if existing is None:
                conflicting_segment = self._conn.execute(
                    "SELECT final_id FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                    (meeting_id, segment_id),
                ).fetchone()
                if conflicting_segment is not None:
                    raise ValueError(f"segment_id {segment_id!r} already belongs to a different final")
                transcript_seq = int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(transcript_seq), 0) + 1 FROM transcript_segments WHERE meeting_id = ?",
                        (meeting_id,),
                    ).fetchone()[0]
                )
                self._conn.execute(
                    "INSERT INTO transcript_segments ("
                    "meeting_id, segment_id, final_id, transcript_seq, text, "
                    "normalized_text, started_at_ms, ended_at_ms, revision, "
                    "evidence_hash, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        meeting_id,
                        segment_id,
                        final_id,
                        transcript_seq,
                        text,
                        normalized_text,
                        started_at_ms,
                        ended_at_ms,
                        evidence_hash,
                        now_ms,
                        now_ms,
                    ),
                )
                payload = {
                    "segment_id": segment_id,
                    "final_id": final_id,
                    "transcript_seq": transcript_seq,
                    "text": text,
                    "normalized_text": normalized_text,
                    "started_at_ms": started_at_ms,
                    "ended_at_ms": ended_at_ms,
                    "revision": 1,
                    "evidence_hash": evidence_hash,
                }
                event_seq = self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="transcript.segment.finalized",
                    aggregate_type="transcript_segment",
                    aggregate_id=segment_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"transcript.final:{final_id}",
                    payload=payload,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                self._update_meeting_state_locked(
                    meeting_id=meeting_id,
                    segments=[payload],
                    now_ms=now_ms,
                    causation_id=event_id,
                )
            else:
                transcript_seq = int(existing["transcript_seq"])
                event_row = self._conn.execute(
                    "SELECT seq FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                    (meeting_id, f"transcript.final:{final_id}"),
                ).fetchone()
                if event_row is None:
                    raise RuntimeError("committed transcript final is missing its outbox event")
                event_seq = int(event_row["seq"])
                expected = {
                    "segment_id": segment_id,
                    "text": text,
                    "normalized_text": normalized_text,
                    "started_at_ms": started_at_ms,
                    "ended_at_ms": ended_at_ms,
                    "evidence_hash": evidence_hash,
                }
                conflicts = {key: (existing[key], value) for key, value in expected.items() if existing[key] != value}
                if conflicts:
                    raise ValueError(f"final_id {final_id!r} was retried with conflicting content: {conflicts}")

            if enqueue_jobs:
                for kind, job_id, priority, job_generation_id in (
                    ("correction", correction_job_id, 100, None),
                    ("suggestion", suggestion_job_id, 90, generation_id),
                ):
                    self._conn.execute(
                        "INSERT OR IGNORE INTO jobs ("
                        "id, meeting_id, kind, status, priority, input_transcript_seq, "
                        "input_version, evidence_segment_id, evidence_hash, generation_id, "
                        "idempotency_key, attempts, max_attempts, next_attempt_at_ms, "
                        "created_at_ms, updated_at_ms"
                        ") VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                        (
                            job_id,
                            meeting_id,
                            kind,
                            priority,
                            transcript_seq,
                            transcript_seq,
                            segment_id,
                            evidence_hash,
                            job_generation_id,
                            f"{kind}:{meeting_id}:{final_id}",
                            max_attempts,
                            now_ms,
                            now_ms,
                            now_ms,
                        ),
                    )

        return {
            "created": created,
            "meeting_id": meeting_id,
            "segment_id": segment_id,
            "final_id": final_id,
            "event_id": event_id,
            "event_seq": event_seq,
            "transcript_seq": transcript_seq,
            "job_ids": (
                {
                    "correction": correction_job_id,
                    "suggestion": suggestion_job_id,
                }
                if enqueue_jobs
                else {}
            ),
        }

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lane: str,
        now_ms: int,
        lease_ms: int,
    ) -> dict[str, Any] | None:
        worker_id = _required(worker_id, "worker_id")
        lane = _required(lane, "lane")
        now_ms = max(0, int(now_ms))
        lease_ms = int(lease_ms)
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")

        with self._write_transaction():
            self._recover_expired_leases_locked(now_ms)
            candidate = self._conn.execute(
                "SELECT id FROM jobs "
                "WHERE kind = ? "
                "AND status IN ('pending', 'retry_wait') "
                "AND next_attempt_at_ms <= ? "
                "AND attempts < max_attempts "
                "ORDER BY priority DESC, next_attempt_at_ms, created_at_ms, id "
                "LIMIT 1",
                (lane, now_ms),
            ).fetchone()
            if candidate is None:
                return None
            result = self._conn.execute(
                "UPDATE jobs SET status = 'running', attempts = attempts + 1, "
                "lease_owner = ?, lease_until_ms = ?, error_class = NULL, updated_at_ms = ? "
                "WHERE id = ? "
                "AND status IN ('pending', 'retry_wait') "
                "AND next_attempt_at_ms <= ? "
                "AND attempts < max_attempts",
                (
                    worker_id,
                    now_ms + lease_ms,
                    now_ms,
                    candidate["id"],
                    now_ms,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (candidate["id"],)).fetchone()
        return self._job_dict(row)

    def heartbeat_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> bool:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        now_ms = max(0, int(now_ms))
        lease_ms = int(lease_ms)
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE jobs SET lease_until_ms = ?, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? "
                "AND lease_until_ms > ?",
                (now_ms + lease_ms, now_ms, job_id, worker_id, now_ms),
            )
        return result.rowcount == 1

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now_ms: int,
        output: Mapping[str, Any] | list[Any] | str | int | float | bool | None,
    ) -> dict[str, Any] | None:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE jobs SET status = 'succeeded', output_json = ?, "
                "lease_owner = NULL, lease_until_ms = NULL, error_class = NULL, "
                "completed_at_ms = ?, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? "
                "AND lease_until_ms > ?",
                (_json_dump(output), now_ms, now_ms, job_id, worker_id, now_ms),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_dict(row)

    def retry_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now_ms: int,
        next_attempt_at_ms: int,
        error_class: str,
    ) -> dict[str, Any] | None:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        error_class = _required(error_class, "error_class")
        now_ms = max(0, int(now_ms))
        next_attempt_at_ms = max(now_ms, int(next_attempt_at_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE jobs SET "
                "status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'retry_wait' END, "
                "next_attempt_at_ms = ?, lease_owner = NULL, lease_until_ms = NULL, "
                "error_class = ?, completed_at_ms = CASE "
                "WHEN attempts >= max_attempts THEN ? ELSE NULL END, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? "
                "AND lease_until_ms > ?",
                (
                    next_attempt_at_ms,
                    error_class,
                    now_ms,
                    now_ms,
                    job_id,
                    worker_id,
                    now_ms,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_dict(row)

    def fail_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now_ms: int,
        error_class: str,
    ) -> dict[str, Any] | None:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        error_class = _required(error_class, "error_class")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE jobs SET status = 'failed', lease_owner = NULL, "
                "lease_until_ms = NULL, error_class = ?, completed_at_ms = ?, "
                "updated_at_ms = ? WHERE id = ? AND status = 'running' "
                "AND lease_owner = ? AND lease_until_ms > ?",
                (error_class, now_ms, now_ms, job_id, worker_id, now_ms),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_dict(row)

    def recover_expired_leases(self, *, now_ms: int) -> int:
        with self._write_transaction():
            return self._recover_expired_leases_locked(max(0, int(now_ms)))

    def _assert_job_lease_locked(
        self,
        *,
        job_id: str,
        lease_owner: str | None,
        now_ms: int,
    ) -> None:
        row = self._conn.execute(
            "SELECT status, lease_owner, lease_until_ms FROM jobs WHERE id = ?",
            (_required(job_id, "job_id"),),
        ).fetchone()
        if (
            row is None
            or row["status"] != "running"
            or row["lease_owner"] != str(lease_owner or "").strip()
            or int(row["lease_until_ms"] or 0) <= max(0, int(now_ms))
        ):
            raise JobLeaseLostError(f"job lease is no longer active: {job_id}")

    def _recover_expired_leases_locked(self, now_ms: int) -> int:
        result = self._conn.execute(
            "UPDATE jobs SET "
            "status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'retry_wait' END, "
            "next_attempt_at_ms = CASE WHEN attempts >= max_attempts "
            "THEN next_attempt_at_ms ELSE ? END, "
            "lease_owner = NULL, lease_until_ms = NULL, error_class = 'lease_expired', "
            "completed_at_ms = CASE WHEN attempts >= max_attempts THEN ? ELSE NULL END, "
            "updated_at_ms = ? "
            "WHERE status = 'running' AND lease_until_ms <= ?",
            (now_ms, now_ms, now_ms, now_ms),
        )
        return int(result.rowcount)

    def commit_transcript_revision(
        self,
        *,
        meeting_id: str,
        segment_id: str,
        expected_evidence_hash: str,
        corrected_text: str,
        revision_id: str,
        now_ms: int,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        evidence_remap_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically replace canonical text and append its revision outbox event."""

        meeting_id = _required(meeting_id, "meeting_id")
        segment_id = _required(segment_id, "segment_id")
        expected_evidence_hash = _required(
            expected_evidence_hash,
            "expected_evidence_hash",
        )
        corrected_text = _required(corrected_text, "corrected_text")
        revision_id = _required(revision_id, "revision_id")
        if evidence_remap_reason is not None:
            evidence_remap_reason = _required(
                evidence_remap_reason,
                "evidence_remap_reason",
            )
        now_ms = max(0, int(now_ms))
        idempotency_key = f"transcript.revision:{revision_id}"

        with self._write_transaction():
            existing_event = self._conn.execute(
                "SELECT seq FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, idempotency_key),
            ).fetchone()
            if existing_event is not None:
                row = self._conn.execute(
                    "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                    (meeting_id, segment_id),
                ).fetchone()
                return self._segment_dict(row) if row is not None else None

            row = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                (meeting_id, segment_id),
            ).fetchone()
            if row is None or row["evidence_hash"] != expected_evidence_hash:
                return None
            original_text = str(row["normalized_text"])
            if original_text == corrected_text:
                return self._segment_dict(row)
            new_evidence_hash = transcript_evidence_hash(segment_id, corrected_text)

            result = self._conn.execute(
                "UPDATE transcript_segments SET normalized_text = ?, evidence_hash = ?, "
                "revision = revision + 1, updated_at_ms = ? "
                "WHERE meeting_id = ? AND segment_id = ? "
                "AND evidence_hash = ? AND revision = ?",
                (
                    corrected_text,
                    new_evidence_hash,
                    now_ms,
                    meeting_id,
                    segment_id,
                    expected_evidence_hash,
                    int(row["revision"]),
                ),
            )
            if result.rowcount != 1:
                return None
            updated_row = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                (meeting_id, segment_id),
            ).fetchone()
            updated = self._segment_dict(updated_row)
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="transcript.segment.revised",
                aggregate_type="transcript_segment",
                aggregate_id=segment_id,
                occurred_at_ms=now_ms,
                idempotency_key=idempotency_key,
                payload={
                    **updated,
                    "original_text": original_text,
                    "corrected_text": corrected_text,
                    "revision_id": revision_id,
                    "previous_evidence_hash": expected_evidence_hash,
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

            suggestion_rows = self._conn.execute(
                "SELECT * FROM suggestions WHERE meeting_id = ? "
                "AND evidence_segment_id = ? AND evidence_hash = ? "
                "AND status IN ('draft', 'committed')",
                (meeting_id, segment_id, expected_evidence_hash),
            ).fetchall()
            for suggestion_row in suggestion_rows:
                suggestion_id = str(suggestion_row["suggestion_id"])
                can_remap = evidence_remap_reason is not None and suggestion_row["status"] == "committed"
                if can_remap:
                    self._conn.execute(
                        "UPDATE suggestions SET evidence_hash = ?, state_revision = ?, "
                        "updated_at_ms = ? WHERE suggestion_id = ?",
                        (new_evidence_hash, int(updated["revision"]), now_ms, suggestion_id),
                    )
                    relation_event = "suggestion.evidence.remapped"
                else:
                    self._conn.execute(
                        "UPDATE suggestions SET status = 'superseded', updated_at_ms = ? WHERE suggestion_id = ?",
                        (now_ms, suggestion_id),
                    )
                    relation_event = "suggestion.superseded"
                relation_row = self._conn.execute(
                    "SELECT * FROM suggestions WHERE suggestion_id = ?",
                    (suggestion_id,),
                ).fetchone()
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type=relation_event,
                    aggregate_type="suggestion",
                    aggregate_id=suggestion_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"{relation_event}:{suggestion_id}:{revision_id}",
                    payload={
                        **self._suggestion_dict(relation_row),
                        "previous_evidence_hash": expected_evidence_hash,
                        "evidence_remap_reason": evidence_remap_reason,
                    },
                    correlation_id=correlation_id,
                    causation_id=revision_id,
                )

            active_suggestion_jobs = self._conn.execute(
                "SELECT id FROM jobs WHERE meeting_id = ? AND kind = 'suggestion' "
                "AND evidence_segment_id = ? AND evidence_hash = ? "
                "AND status IN ('pending', 'retry_wait', 'running')",
                (meeting_id, segment_id, expected_evidence_hash),
            ).fetchall()
            if active_suggestion_jobs:
                self._conn.execute(
                    "UPDATE jobs SET status = 'cancelled', lease_owner = NULL, "
                    "lease_until_ms = NULL, error_class = 'evidence_superseded', "
                    "completed_at_ms = ?, updated_at_ms = ? "
                    "WHERE meeting_id = ? AND kind = 'suggestion' "
                    "AND evidence_segment_id = ? AND evidence_hash = ? "
                    "AND status IN ('pending', 'retry_wait', 'running')",
                    (
                        now_ms,
                        now_ms,
                        meeting_id,
                        segment_id,
                        expected_evidence_hash,
                    ),
                )
                has_remapped_commit = any(
                    evidence_remap_reason is not None and row["status"] == "committed" for row in suggestion_rows
                )
                if not has_remapped_commit:
                    replacement_job_id = _stable_id("job", meeting_id, revision_id, "suggestion")
                    self._conn.execute(
                        "INSERT OR IGNORE INTO jobs ("
                        "id, meeting_id, kind, status, priority, input_transcript_seq, "
                        "input_version, evidence_segment_id, evidence_hash, generation_id, "
                        "idempotency_key, attempts, max_attempts, next_attempt_at_ms, "
                        "created_at_ms, updated_at_ms"
                        ") VALUES (?, ?, 'suggestion', 'pending', 90, ?, ?, ?, ?, ?, ?, 0, 3, ?, ?, ?)",
                        (
                            replacement_job_id,
                            meeting_id,
                            int(updated["transcript_seq"]),
                            int(updated["revision"]),
                            segment_id,
                            new_evidence_hash,
                            f"suggestion:{meeting_id}:{revision_id}",
                            f"suggestion:{meeting_id}:revision:{revision_id}",
                            now_ms,
                            now_ms,
                            now_ms,
                        ),
                    )
            self._update_meeting_state_locked(
                meeting_id=meeting_id,
                segments=[updated],
                now_ms=now_ms,
                causation_id=revision_id,
            )
        return updated

    def upsert_suggestion_draft(
        self,
        *,
        suggestion_id: str,
        meeting_id: str,
        job_id: str | None,
        generation_id: str,
        evidence_segment_id: str,
        evidence_transcript_seq: int,
        evidence_hash: str,
        state_revision: int,
        draft_text: str,
        draft_seq: int,
        now_ms: int,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        suggestion_id = _required(suggestion_id, "suggestion_id")
        meeting_id = _required(meeting_id, "meeting_id")
        generation_id = _required(generation_id, "generation_id")
        evidence_segment_id = _required(evidence_segment_id, "evidence_segment_id")
        evidence_hash = _required(evidence_hash, "evidence_hash")
        evidence_transcript_seq = int(evidence_transcript_seq)
        state_revision = int(state_revision)
        draft_seq = int(draft_seq)
        now_ms = max(0, int(now_ms))
        if evidence_transcript_seq <= 0 or state_revision <= 0 or draft_seq < 0:
            raise ValueError("suggestion versions must be positive and draft_seq non-negative")
        normalized_lease_owner = str(lease_owner or "").strip() or None
        if normalized_lease_owner is not None and not str(job_id or "").strip():
            raise ValueError("lease_owner requires job_id")

        with self._write_transaction():
            if normalized_lease_owner is not None:
                self._assert_job_lease_locked(
                    job_id=str(job_id),
                    lease_owner=normalized_lease_owner,
                    now_ms=now_ms,
                )
            evidence_row = self._conn.execute(
                "SELECT transcript_seq, evidence_hash FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                (meeting_id, evidence_segment_id),
            ).fetchone()
            if (
                evidence_row is None
                or int(evidence_row["transcript_seq"]) != evidence_transcript_seq
                or str(evidence_row["evidence_hash"]) != evidence_hash
            ):
                raise StaleEvidenceError("suggestion draft references a superseded transcript revision")
            existing = self._conn.execute(
                "SELECT * FROM suggestions WHERE suggestion_id = ?", (suggestion_id,)
            ).fetchone()
            changed = False
            event_type = "suggestion.draft.started"
            if existing is None:
                self._conn.execute(
                    "INSERT INTO suggestions ("
                    "suggestion_id, meeting_id, job_id, generation_id, evidence_segment_id, "
                    "evidence_transcript_seq, evidence_hash, state_revision, status, "
                    "draft_text, draft_seq, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
                    (
                        suggestion_id,
                        meeting_id,
                        job_id,
                        generation_id,
                        evidence_segment_id,
                        evidence_transcript_seq,
                        evidence_hash,
                        state_revision,
                        str(draft_text),
                        draft_seq,
                        now_ms,
                        now_ms,
                    ),
                )
                changed = True
            else:
                immutable = {
                    "meeting_id": meeting_id,
                    "job_id": job_id,
                    "generation_id": generation_id,
                    "evidence_segment_id": evidence_segment_id,
                    "evidence_transcript_seq": evidence_transcript_seq,
                    "evidence_hash": evidence_hash,
                    "state_revision": state_revision,
                }
                conflicts = {key: (existing[key], value) for key, value in immutable.items() if existing[key] != value}
                if conflicts:
                    raise ValueError(f"suggestion_id {suggestion_id!r} has conflicting identity: {conflicts}")
                if existing["status"] == "draft" and draft_seq > int(existing["draft_seq"]):
                    result = self._conn.execute(
                        "UPDATE suggestions SET draft_text = ?, draft_seq = ?, updated_at_ms = ? "
                        "WHERE suggestion_id = ? AND status = 'draft' AND draft_seq < ?",
                        (str(draft_text), draft_seq, now_ms, suggestion_id, draft_seq),
                    )
                    changed = result.rowcount == 1
                    event_type = "suggestion.draft.delta"
            row = self._conn.execute("SELECT * FROM suggestions WHERE suggestion_id = ?", (suggestion_id,)).fetchone()
            suggestion = self._suggestion_dict(row)
            if changed:
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type=event_type,
                    aggregate_type="suggestion",
                    aggregate_id=suggestion_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"suggestion.draft:{suggestion_id}:{draft_seq}",
                    payload=suggestion,
                    correlation_id=generation_id,
                    causation_id=job_id,
                )
        return suggestion

    def get_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        suggestion_id = _required(suggestion_id, "suggestion_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM suggestions WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
        return self._suggestion_dict(row) if row is not None else None

    def end_meeting(
        self,
        *,
        meeting_id: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            if row["state"] != "ended":
                self._conn.execute(
                    "UPDATE meetings SET state = 'ended', ended_at_ms = ?, "
                    "revision = revision + 1, updated_at_ms = ? WHERE id = ?",
                    (now_ms, now_ms, meeting_id),
                )
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="meeting.ended",
                    aggregate_type="meeting",
                    aggregate_id=meeting_id,
                    occurred_at_ms=now_ms,
                    idempotency_key="meeting.ended",
                    payload={"meeting_id": meeting_id, "state": "ended", "ended_at_ms": now_ms},
                    correlation_id=correlation_id,
                )
                latest_segment = self._conn.execute(
                    "SELECT * FROM transcript_segments WHERE meeting_id = ? ORDER BY transcript_seq DESC LIMIT 1",
                    (meeting_id,),
                ).fetchone()
                if latest_segment is not None:
                    for kind, priority in (
                        ("correction", 120),
                        ("minutes", 60),
                        ("approach", 50),
                        ("index", 40),
                    ):
                        job_id = _stable_id("job", meeting_id, "meeting.ended", kind)
                        self._conn.execute(
                            "INSERT OR IGNORE INTO jobs ("
                            "id, meeting_id, kind, status, priority, input_transcript_seq, "
                            "input_version, evidence_segment_id, evidence_hash, generation_id, "
                            "idempotency_key, attempts, max_attempts, next_attempt_at_ms, "
                            "created_at_ms, updated_at_ms"
                            ") VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL, ?, 0, 3, ?, ?, ?)",
                            (
                                job_id,
                                meeting_id,
                                kind,
                                priority,
                                int(latest_segment["transcript_seq"]),
                                int(latest_segment["revision"]),
                                str(latest_segment["segment_id"]),
                                str(latest_segment["evidence_hash"]),
                                f"{kind}:{meeting_id}:meeting.ended",
                                now_ms,
                                now_ms,
                                now_ms,
                            ),
                        )
            ended = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
        return self._meeting_dict(ended)

    def save_suggestion_feedback(
        self,
        *,
        meeting_id: str,
        suggestion_id: str,
        feedback: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        suggestion_id = _required(suggestion_id, "suggestion_id")
        feedback = _required(feedback, "feedback")
        if feedback not in {"kept", "ignored", "false_positive", "too_late"}:
            raise ValueError(f"unsupported suggestion feedback: {feedback}")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM suggestions WHERE meeting_id = ? AND suggestion_id = ?",
                (meeting_id, suggestion_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"suggestion not found: {suggestion_id}")
            if row["feedback"] != feedback:
                self._conn.execute(
                    "UPDATE suggestions SET feedback = ?, feedback_at_ms = ?, updated_at_ms = ? "
                    "WHERE meeting_id = ? AND suggestion_id = ?",
                    (feedback, now_ms, now_ms, meeting_id, suggestion_id),
                )
                updated_row = self._conn.execute(
                    "SELECT * FROM suggestions WHERE meeting_id = ? AND suggestion_id = ?",
                    (meeting_id, suggestion_id),
                ).fetchone()
                updated = self._suggestion_dict(updated_row)
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="suggestion.feedback.updated",
                    aggregate_type="suggestion",
                    aggregate_id=suggestion_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"suggestion.feedback:{suggestion_id}:{feedback}",
                    payload=updated,
                    correlation_id=correlation_id,
                )
            else:
                updated = self._suggestion_dict(row)
        return updated

    def begin_recording(
        self,
        *,
        meeting_id: str,
        track: str,
        epoch: int,
        source_type: str,
        sample_rate_hz: int,
        lease_owner: str,
        lease_ms: int,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        source_type = _required(source_type, "source_type")
        lease_owner = _required(lease_owner, "lease_owner")
        epoch = int(epoch)
        sample_rate_hz = int(sample_rate_hz)
        lease_ms = int(lease_ms)
        now_ms = max(0, int(now_ms))
        if epoch < 0:
            raise ValueError("recording epoch must be non-negative")
        if sample_rate_hz <= 0 or lease_ms <= 0:
            raise ValueError("sample_rate_hz and lease_ms must be positive")

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            self._conn.execute(
                "INSERT INTO meetings ("
                "id, state, started_at_ms, latest_seq, revision, created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, 0, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "state = CASE WHEN meetings.state = 'ended' THEN 'ended' ELSE 'live' END, "
                "updated_at_ms = excluded.updated_at_ms",
                (meeting_id, now_ms, now_ms, now_ms),
            )
            existing = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
            if existing is not None and existing["status"] == "exporting":
                raise RuntimeError("recording cannot resume while its export is running")
            if (
                existing is not None
                and existing["status"] == "active"
                and int(existing["lease_until_ms"] or 0) > now_ms
                and existing["lease_owner"] != lease_owner
            ):
                raise RuntimeError("recording lease is already owned by another capture")
            same_active_capture = bool(
                existing is not None
                and existing["status"] == "active"
                and existing["lease_owner"] == lease_owner
                and int(existing["lease_until_ms"] or 0) > now_ms
            )
            capture_generation = (
                1 if existing is None else int(existing["capture_generation"]) + (0 if same_active_capture else 1)
            )
            aggregate = self._recording_aggregate_locked(meeting_id, track, epoch)
            self._conn.execute(
                "INSERT INTO recording_sessions ("
                "meeting_id, track, epoch, source_type, capture_generation, status, "
                "sample_rate_hz, chunk_count, "
                "sample_count, duration_ms, file_size_bytes, lease_owner, lease_until_ms, "
                "journal_sha256, "
                "output_relative_path, started_at_ms, updated_at_ms"
                ") VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(meeting_id, track, epoch) DO UPDATE SET "
                "source_type = excluded.source_type, status = 'active', "
                "capture_generation = excluded.capture_generation, "
                "sample_rate_hz = excluded.sample_rate_hz, chunk_count = excluded.chunk_count, "
                "sample_count = excluded.sample_count, duration_ms = excluded.duration_ms, "
                "file_size_bytes = excluded.file_size_bytes, lease_owner = excluded.lease_owner, "
                "lease_until_ms = excluded.lease_until_ms, error_class = NULL, "
                "journal_sha256 = excluded.journal_sha256, "
                "output_relative_path = excluded.output_relative_path, "
                "completed_at_ms = NULL, updated_at_ms = excluded.updated_at_ms",
                (
                    meeting_id,
                    track,
                    epoch,
                    source_type,
                    capture_generation,
                    sample_rate_hz,
                    aggregate["chunk_count"],
                    aggregate["sample_count"],
                    aggregate["duration_ms"],
                    aggregate["file_size_bytes"],
                    lease_owner,
                    now_ms + lease_ms,
                    aggregate["journal_sha256"],
                    f"audio_assets/{meeting_id}/audio.wav",
                    now_ms if existing is None else int(existing["started_at_ms"]),
                    now_ms,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
            recording = self._recording_session_dict(row)
            if existing is None or not same_active_capture:
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="recording.started",
                    aggregate_type="recording_session",
                    aggregate_id=f"{track}:{epoch}",
                    occurred_at_ms=now_ms,
                    idempotency_key=(f"recording.started:{track}:{epoch}:generation:{capture_generation}"),
                    payload=recording,
                    correlation_id=meeting_id,
                )
        return recording

    def register_imported_recording(
        self,
        *,
        meeting_id: str,
        source_type: str,
        relative_path: str,
        sha256: str,
        sample_rate_hz: int,
        sample_count: int,
        duration_ms: int,
        file_size_bytes: int,
        started_at_ms: int,
        now_ms: int,
    ) -> dict[str, Any]:
        """Register an already assembled file as a durable V2 recording."""

        meeting_id = _required(meeting_id, "meeting_id")
        source_type = _required(source_type, "source_type")
        relative_path = _required(relative_path, "relative_path")
        sha256 = _required(sha256, "sha256")
        sample_rate_hz = int(sample_rate_hz)
        sample_count = int(sample_count)
        duration_ms = int(duration_ms)
        file_size_bytes = int(file_size_bytes)
        started_at_ms = max(0, int(started_at_ms))
        now_ms = max(0, int(now_ms))
        if min(sample_rate_hz, sample_count, duration_ms, file_size_bytes) <= 0:
            raise ValueError("imported recording dimensions must be positive")

        chunk = {
            "chunk_seq": 0,
            "name": Path(relative_path).name,
            "sample_count": sample_count,
            "file_size_bytes": file_size_bytes,
            "sha256": sha256,
        }
        journal_sha256 = audio_chunk_journal_sha256([chunk])
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            meeting = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
            if meeting is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            existing_chunk = self._conn.execute(
                "SELECT * FROM audio_chunks WHERE meeting_id = ? AND track = 'microphone' "
                "AND epoch = 0 AND chunk_seq = 0",
                (meeting_id,),
            ).fetchone()
            if existing_chunk is None:
                self._conn.execute(
                    "INSERT INTO audio_chunks ("
                    "meeting_id, track, epoch, chunk_seq, relative_path, sha256, "
                    "sample_rate_hz, sample_count, duration_ms, file_size_bytes, status, created_at_ms"
                    ") VALUES (?, 'microphone', 0, 0, ?, ?, ?, ?, ?, ?, 'committed', ?)",
                    (
                        meeting_id,
                        relative_path,
                        sha256,
                        sample_rate_hz,
                        sample_count,
                        duration_ms,
                        file_size_bytes,
                        now_ms,
                    ),
                )
            else:
                expected = {
                    "relative_path": relative_path,
                    "sha256": sha256,
                    "sample_rate_hz": sample_rate_hz,
                    "sample_count": sample_count,
                    "duration_ms": duration_ms,
                    "file_size_bytes": file_size_bytes,
                    "status": "committed",
                }
                conflicts = {
                    key: (existing_chunk[key], value)
                    for key, value in expected.items()
                    if existing_chunk[key] != value
                }
                if conflicts:
                    raise ValueError(f"imported recording was retried with conflicting content: {conflicts}")

            existing_recording = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = 'microphone' AND epoch = 0",
                (meeting_id,),
            ).fetchone()
            if existing_recording is None:
                self._conn.execute(
                    "INSERT INTO recording_sessions ("
                    "meeting_id, track, epoch, source_type, capture_generation, status, sample_rate_hz, "
                    "chunk_count, sample_count, duration_ms, file_size_bytes, lease_owner, lease_until_ms, "
                    "journal_sha256, output_relative_path, output_sha256, output_file_size_bytes, "
                    "started_at_ms, sealed_at_ms, completed_at_ms, updated_at_ms"
                    ") VALUES (?, 'microphone', 0, ?, 1, 'ready', ?, 1, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        source_type,
                        sample_rate_hz,
                        sample_count,
                        duration_ms,
                        file_size_bytes,
                        journal_sha256,
                        relative_path,
                        sha256,
                        file_size_bytes,
                        started_at_ms,
                        now_ms,
                        now_ms,
                        now_ms,
                    ),
                )
            else:
                if existing_recording["status"] != "ready" or existing_recording["output_sha256"] != sha256:
                    raise ValueError("imported recording was retried with conflicting recording state")

            event_key = f"recording.imported:{sha256}"
            if self._conn.execute(
                "SELECT 1 FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, event_key),
            ).fetchone() is None:
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="recording.imported",
                    aggregate_type="recording_session",
                    aggregate_id="microphone:0",
                    occurred_at_ms=now_ms,
                    idempotency_key=event_key,
                    payload={
                        "meeting_id": meeting_id,
                        "track": "microphone",
                        "epoch": 0,
                        "source_type": source_type,
                        "status": "ready",
                        "relative_path": relative_path,
                        "sha256": sha256,
                        "duration_ms": duration_ms,
                    },
                    correlation_id=meeting_id,
                )
            row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = 'microphone' AND epoch = 0",
                (meeting_id,),
            ).fetchone()
        return self._recording_session_dict(row)

    def heartbeat_recording(
        self,
        *,
        meeting_id: str,
        track: str,
        epoch: int,
        lease_owner: str,
        lease_ms: int,
        now_ms: int,
    ) -> bool:
        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        lease_owner = _required(lease_owner, "lease_owner")
        epoch = int(epoch)
        lease_ms = int(lease_ms)
        now_ms = max(0, int(now_ms))
        if epoch < 0 or lease_ms <= 0:
            raise ValueError("recording epoch must be non-negative and lease_ms positive")
        with self._write_transaction():
            if (
                self._conn.execute(
                    "SELECT 1 FROM meeting_tombstones WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()
                is not None
            ):
                return False
            result = self._conn.execute(
                "UPDATE recording_sessions SET lease_until_ms = ?, updated_at_ms = ? "
                "WHERE meeting_id = ? AND track = ? AND epoch = ? AND status = 'active' "
                "AND lease_owner = ? AND lease_until_ms > ?",
                (
                    now_ms + lease_ms,
                    now_ms,
                    meeting_id,
                    track,
                    epoch,
                    lease_owner,
                    now_ms,
                ),
            )
        return result.rowcount == 1

    def seal_recording_and_enqueue_export(
        self,
        *,
        meeting_id: str,
        track: str,
        epoch: int,
        lease_owner: str,
        output_relative_path: str,
        expected_journal_sha256: str | None = None,
        interrupted: bool,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        lease_owner = _required(lease_owner, "lease_owner")
        output_relative_path = _required(output_relative_path, "output_relative_path")
        epoch = int(epoch)
        now_ms = max(0, int(now_ms))
        if epoch < 0:
            raise ValueError("recording epoch must be non-negative")

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            existing = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
            if existing is None:
                raise KeyError(f"recording session not found: {meeting_id}/{track}/{epoch}")
            aggregate = self._recording_aggregate_locked(meeting_id, track, epoch)
            aggregate_journal = str(aggregate["journal_sha256"])
            expected_journal = str(expected_journal_sha256 or "").strip() or None
            if existing["status"] != "active":
                terminal_journal = str(existing["journal_sha256"] or aggregate_journal)
                candidate_journal = expected_journal or aggregate_journal
                if candidate_journal != terminal_journal:
                    raise RuntimeError("terminal recording journal does not match the late seal")
                export_row = self._conn.execute(
                    "SELECT * FROM recording_exports WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, track, epoch),
                ).fetchone()
                return {
                    "recording": self._recording_session_dict(existing),
                    "export": (self._recording_export_dict(export_row) if export_row is not None else None),
                }
            if existing["lease_owner"] != lease_owner or int(existing["lease_until_ms"] or 0) <= now_ms:
                raise RuntimeError("recording lease was lost before sealing")
            if expected_journal is not None and expected_journal != aggregate_journal:
                raise RuntimeError("recording journal changed before sealing")
            empty_recording = aggregate["chunk_count"] <= 0
            status = "failed" if empty_recording else "interrupted" if interrupted else "sealed"
            self._conn.execute(
                "UPDATE recording_sessions SET status = ?, chunk_count = ?, sample_count = ?, "
                "duration_ms = ?, file_size_bytes = ?, journal_sha256 = ?, "
                "lease_owner = NULL, lease_until_ms = NULL, "
                "output_relative_path = ?, error_class = ?, sealed_at_ms = COALESCE(sealed_at_ms, ?), "
                "updated_at_ms = ? WHERE meeting_id = ? AND track = ? AND epoch = ? "
                "AND status = 'active'",
                (
                    status,
                    aggregate["chunk_count"],
                    aggregate["sample_count"],
                    aggregate["duration_ms"],
                    aggregate["file_size_bytes"],
                    aggregate["journal_sha256"],
                    output_relative_path,
                    "no_audio_chunks" if empty_recording else None,
                    now_ms,
                    now_ms,
                    meeting_id,
                    track,
                    epoch,
                ),
            )
            if interrupted:
                self._conn.execute(
                    "UPDATE meetings SET state = CASE WHEN state = 'live' THEN 'interrupted' ELSE state END, "
                    "revision = revision + CASE WHEN state = 'live' THEN 1 ELSE 0 END, "
                    "updated_at_ms = ? WHERE id = ?",
                    (now_ms, meeting_id),
                )
            row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
            recording = self._recording_session_dict(row)
            event_key = (
                f"recording.sealed:{track}:{epoch}:generation:"
                f"{recording['capture_generation']}:{aggregate['journal_sha256']}:"
                f"{int(interrupted)}"
            )
            if (
                self._conn.execute(
                    "SELECT 1 FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                    (meeting_id, event_key),
                ).fetchone()
                is None
            ):
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type=(
                        "recording.failed"
                        if empty_recording
                        else "recording.interrupted"
                        if interrupted
                        else "recording.sealed"
                    ),
                    aggregate_type="recording_session",
                    aggregate_id=f"{track}:{epoch}",
                    occurred_at_ms=now_ms,
                    idempotency_key=event_key,
                    payload=recording,
                    correlation_id=meeting_id,
                )
            export = (
                None
                if empty_recording
                else self._enqueue_recording_export_locked(
                    recording=recording,
                    now_ms=now_ms,
                )
            )
        return {"recording": recording, "export": export}

    def _recording_aggregate_locked(
        self,
        meeting_id: str,
        track: str,
        epoch: int,
    ) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT * FROM audio_chunks WHERE meeting_id = ? AND track = ? AND epoch = ? "
            "AND status = 'committed' ORDER BY chunk_seq",
            (meeting_id, track, epoch),
        ).fetchall()
        return {
            "chunk_count": len(rows),
            "sample_count": sum(int(row["sample_count"]) for row in rows),
            "duration_ms": sum(int(row["duration_ms"]) for row in rows),
            "file_size_bytes": sum(int(row["file_size_bytes"]) for row in rows),
            "journal_sha256": audio_chunk_journal_sha256([self._audio_chunk_dict(row) for row in rows]),
        }

    def _enqueue_recording_export_locked(
        self,
        *,
        recording: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = str(recording["meeting_id"])
        track = str(recording["track"])
        epoch = int(recording["epoch"])
        chunk_count = int(recording["chunk_count"])
        sample_count = int(recording["sample_count"])
        journal_sha256 = _required(str(recording.get("journal_sha256") or ""), "journal_sha256")
        if chunk_count <= 0 or sample_count <= 0:
            raise ValueError("recording export requires committed audio")
        export_id = _stable_id("recording_export", meeting_id, track, str(epoch))
        existing = self._conn.execute(
            "SELECT * FROM recording_exports WHERE id = ?",
            (export_id,),
        ).fetchone()
        changed = existing is None or (
            int(existing["input_chunk_count"]) != chunk_count
            or int(existing["input_sample_count"]) != sample_count
            or str(existing["input_journal_sha256"] or "") != journal_sha256
        )
        if existing is None:
            self._conn.execute(
                "INSERT INTO recording_exports ("
                "id, meeting_id, track, epoch, status, output_relative_path, "
                "input_chunk_count, input_sample_count, input_journal_sha256, attempts, max_attempts, "
                "next_attempt_at_ms, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, 3, ?, ?, ?)",
                (
                    export_id,
                    meeting_id,
                    track,
                    epoch,
                    recording["output_relative_path"],
                    chunk_count,
                    sample_count,
                    journal_sha256,
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
        elif changed:
            if existing["status"] == "running" and int(existing["lease_until_ms"] or 0) > now_ms:
                raise RuntimeError("recording export is already running for an older journal revision")
            self._conn.execute(
                "UPDATE recording_exports SET status = 'pending', output_relative_path = ?, "
                "input_chunk_count = ?, input_sample_count = ?, input_journal_sha256 = ?, "
                "attempts = 0, lease_owner = NULL, "
                "lease_until_ms = NULL, next_attempt_at_ms = ?, output_json = NULL, error_class = NULL, "
                "completed_at_ms = NULL, updated_at_ms = ? WHERE id = ?",
                (
                    recording["output_relative_path"],
                    chunk_count,
                    sample_count,
                    journal_sha256,
                    now_ms,
                    now_ms,
                    export_id,
                ),
            )
        row = self._conn.execute(
            "SELECT * FROM recording_exports WHERE id = ?",
            (export_id,),
        ).fetchone()
        export = self._recording_export_dict(row)
        if existing is None or changed:
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="recording.export.queued",
                aggregate_type="recording_export",
                aggregate_id=export_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"recording.export.queued:{track}:{epoch}:{journal_sha256}",
                payload=export,
                correlation_id=meeting_id,
            )
        return export

    def record_audio_chunk(
        self,
        *,
        meeting_id: str,
        track: str,
        epoch: int,
        chunk_seq: int,
        relative_path: str,
        sha256: str,
        sample_rate_hz: int,
        sample_count: int,
        duration_ms: int,
        file_size_bytes: int,
        now_ms: int,
        lease_owner: str | None = None,
        lease_ms: int | None = None,
        expected_capture_generation: int | None = None,
        require_lease_expired_at_ms: int | None = None,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        relative_path = _required(relative_path, "relative_path")
        sha256 = _required(sha256, "sha256")
        epoch = int(epoch)
        chunk_seq = int(chunk_seq)
        sample_rate_hz = int(sample_rate_hz)
        sample_count = int(sample_count)
        duration_ms = int(duration_ms)
        file_size_bytes = int(file_size_bytes)
        now_ms = max(0, int(now_ms))
        normalized_lease_owner = str(lease_owner or "").strip() or None
        normalized_lease_ms = int(lease_ms or 0)
        recovery_generation = int(expected_capture_generation) if expected_capture_generation is not None else None
        recovery_expired_at_ms = (
            max(0, int(require_lease_expired_at_ms)) if require_lease_expired_at_ms is not None else None
        )
        if epoch < 0 or chunk_seq < 0:
            raise ValueError("audio epoch and chunk_seq must be non-negative")
        if min(sample_rate_hz, sample_count, duration_ms, file_size_bytes) <= 0:
            raise ValueError("audio chunk dimensions must be positive")
        if (normalized_lease_owner is None) != (normalized_lease_ms <= 0):
            raise ValueError("lease_owner and positive lease_ms must be provided together")
        if (recovery_generation is None) != (recovery_expired_at_ms is None):
            raise ValueError("expected_capture_generation and require_lease_expired_at_ms must be provided together")
        if recovery_generation is not None and recovery_generation <= 0:
            raise ValueError("expected_capture_generation must be positive")

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if recovery_generation is not None:
                recovery_lease = self._conn.execute(
                    "SELECT status, capture_generation, lease_until_ms "
                    "FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, track, epoch),
                ).fetchone()
                if (
                    recovery_lease is None
                    or recovery_lease["status"] != "active"
                    or int(recovery_lease["capture_generation"]) != recovery_generation
                    or int(recovery_lease["lease_until_ms"] or 0) > int(recovery_expired_at_ms)
                ):
                    raise RecordingRecoveryConflict("recording capture changed after the expired recovery scan")
            self._conn.execute(
                "INSERT INTO meetings ("
                "id, state, started_at_ms, latest_seq, revision, created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, 0, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "state = CASE WHEN meetings.state = 'ended' THEN 'ended' ELSE 'live' END, "
                "updated_at_ms = excluded.updated_at_ms",
                (meeting_id, now_ms, now_ms, now_ms),
            )
            if normalized_lease_owner is not None:
                lease = self._conn.execute(
                    "SELECT status, lease_owner, lease_until_ms FROM recording_sessions "
                    "WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, track, epoch),
                ).fetchone()
                if lease is None:
                    raise KeyError(f"recording session not found: {meeting_id}/{track}/{epoch}")
                if (
                    lease["status"] != "active"
                    or lease["lease_owner"] != normalized_lease_owner
                    or int(lease["lease_until_ms"] or 0) <= now_ms
                ):
                    raise RuntimeError("recording lease was lost before committing audio")
            existing = self._conn.execute(
                "SELECT * FROM audio_chunks WHERE meeting_id = ? AND track = ? AND epoch = ? AND chunk_seq = ?",
                (meeting_id, track, epoch, chunk_seq),
            ).fetchone()
            values = {
                "relative_path": relative_path,
                "sha256": sha256,
                "sample_rate_hz": sample_rate_hz,
                "sample_count": sample_count,
                "duration_ms": duration_ms,
                "file_size_bytes": file_size_bytes,
            }
            if existing is not None:
                conflicts = {
                    field: (existing[field], value) for field, value in values.items() if existing[field] != value
                }
                if conflicts:
                    raise ValueError(f"audio chunk was retried with conflicting content: {conflicts}")
                return self._audio_chunk_dict(existing)
            self._conn.execute(
                "INSERT INTO audio_chunks ("
                "meeting_id, track, epoch, chunk_seq, relative_path, sha256, sample_rate_hz, "
                "sample_count, duration_ms, file_size_bytes, status, created_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?)",
                (
                    meeting_id,
                    track,
                    epoch,
                    chunk_seq,
                    relative_path,
                    sha256,
                    sample_rate_hz,
                    sample_count,
                    duration_ms,
                    file_size_bytes,
                    now_ms,
                ),
            )
            aggregate = self._recording_aggregate_locked(meeting_id, track, epoch)
            self._conn.execute(
                "UPDATE recording_sessions SET chunk_count = ?, sample_count = ?, "
                "duration_ms = ?, file_size_bytes = ?, journal_sha256 = ?, "
                "lease_until_ms = CASE WHEN ? IS NULL THEN lease_until_ms ELSE ? END, "
                "updated_at_ms = ? WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (
                    aggregate["chunk_count"],
                    aggregate["sample_count"],
                    aggregate["duration_ms"],
                    aggregate["file_size_bytes"],
                    aggregate["journal_sha256"],
                    normalized_lease_owner,
                    now_ms + normalized_lease_ms,
                    now_ms,
                    meeting_id,
                    track,
                    epoch,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM audio_chunks WHERE meeting_id = ? AND track = ? AND epoch = ? AND chunk_seq = ?",
                (meeting_id, track, epoch, chunk_seq),
            ).fetchone()
            chunk = self._audio_chunk_dict(row)
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="recording.chunk.committed",
                aggregate_type="audio_chunk",
                aggregate_id=f"{track}:{epoch}:{chunk_seq}",
                occurred_at_ms=now_ms,
                idempotency_key=f"recording.chunk:{track}:{epoch}:{chunk_seq}",
                payload=chunk,
                correlation_id=meeting_id,
            )
        return chunk

    def recover_interrupted_recordings(self, *, now_ms: int) -> list[str]:
        """Mark meetings left live by a previous backend process as interrupted."""

        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            meeting_ids = [
                str(row["id"])
                for row in self._conn.execute("SELECT id FROM meetings WHERE state = 'live' ORDER BY id").fetchall()
            ]
            for meeting_id in meeting_ids:
                self._conn.execute(
                    "UPDATE meetings SET state = 'interrupted', revision = revision + 1, "
                    "updated_at_ms = ? WHERE id = ? AND state = 'live'",
                    (now_ms, meeting_id),
                )
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="recording.interrupted",
                    aggregate_type="meeting",
                    aggregate_id=meeting_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"recording.interrupted:{now_ms}",
                    payload={
                        "meeting_id": meeting_id,
                        "state": "interrupted",
                        "recoverable": True,
                    },
                    correlation_id=meeting_id,
                )
        return meeting_ids

    def claim_next_recording_export(
        self,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> dict[str, Any] | None:
        worker_id = _required(worker_id, "worker_id")
        now_ms = max(0, int(now_ms))
        lease_ms = int(lease_ms)
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        with self._write_transaction():
            self._recover_expired_recording_export_leases_locked(now_ms)
            candidate = self._conn.execute(
                "SELECT id FROM recording_exports "
                "WHERE status IN ('pending', 'retry_wait') AND next_attempt_at_ms <= ? "
                "AND attempts < max_attempts "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_exports.meeting_id) "
                "ORDER BY next_attempt_at_ms, created_at_ms, id LIMIT 1",
                (now_ms,),
            ).fetchone()
            if candidate is None:
                return None
            result = self._conn.execute(
                "UPDATE recording_exports SET status = 'running', attempts = attempts + 1, "
                "lease_owner = ?, lease_until_ms = ?, error_class = NULL, updated_at_ms = ? "
                "WHERE id = ? AND status IN ('pending', 'retry_wait') "
                "AND next_attempt_at_ms <= ? AND attempts < max_attempts "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_exports.meeting_id)",
                (worker_id, now_ms + lease_ms, now_ms, candidate["id"], now_ms),
            )
            if result.rowcount != 1:
                return None
            self._conn.execute(
                "UPDATE recording_sessions SET status = 'exporting', updated_at_ms = ? "
                "WHERE (meeting_id, track, epoch) = ("
                "SELECT meeting_id, track, epoch FROM recording_exports WHERE id = ?)",
                (now_ms, candidate["id"]),
            )
            row = self._conn.execute(
                "SELECT * FROM recording_exports WHERE id = ?",
                (candidate["id"],),
            ).fetchone()
        return self._recording_export_dict(row)

    def heartbeat_recording_export(
        self,
        *,
        export_id: str,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> bool:
        export_id = _required(export_id, "export_id")
        worker_id = _required(worker_id, "worker_id")
        now_ms = max(0, int(now_ms))
        lease_ms = int(lease_ms)
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE recording_exports SET lease_until_ms = ?, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_until_ms > ?",
                (now_ms + lease_ms, now_ms, export_id, worker_id, now_ms),
            )
        return result.rowcount == 1

    def complete_recording_export(
        self,
        *,
        export_id: str,
        worker_id: str,
        output: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, Any] | None:
        export_id = _required(export_id, "export_id")
        worker_id = _required(worker_id, "worker_id")
        output_sha256 = _required(str(output.get("sha256") or ""), "output.sha256")
        output_file_size_bytes = int(output.get("file_size_bytes") or 0)
        if output_file_size_bytes <= 0:
            raise ValueError("output.file_size_bytes must be positive")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE recording_exports SET status = 'succeeded', output_json = ?, "
                "lease_owner = NULL, lease_until_ms = NULL, error_class = NULL, "
                "completed_at_ms = ?, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_until_ms > ?",
                (_json_dump(output), now_ms, now_ms, export_id, worker_id, now_ms),
            )
            if result.rowcount != 1:
                return None
            export_row = self._conn.execute(
                "SELECT * FROM recording_exports WHERE id = ?",
                (export_id,),
            ).fetchone()
            self._conn.execute(
                "UPDATE recording_sessions SET status = 'ready', output_sha256 = ?, "
                "output_file_size_bytes = ?, error_class = NULL, completed_at_ms = ?, "
                "updated_at_ms = ? WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (
                    output_sha256,
                    output_file_size_bytes,
                    now_ms,
                    now_ms,
                    export_row["meeting_id"],
                    export_row["track"],
                    int(export_row["epoch"]),
                ),
            )
            recording_row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (export_row["meeting_id"], export_row["track"], int(export_row["epoch"])),
            ).fetchone()
            recording = self._recording_session_dict(recording_row)
            self._append_event_locked(
                meeting_id=str(export_row["meeting_id"]),
                event_type="recording.export.ready",
                aggregate_type="recording_export",
                aggregate_id=export_id,
                occurred_at_ms=now_ms,
                idempotency_key=(f"recording.export.ready:{export_id}:{export_row['input_journal_sha256']}"),
                payload={"recording": recording, "output": dict(output)},
                correlation_id=str(export_row["meeting_id"]),
            )
            completed_row = self._conn.execute(
                "SELECT * FROM recording_exports WHERE id = ?",
                (export_id,),
            ).fetchone()
        return self._recording_export_dict(completed_row)

    def retry_recording_export(
        self,
        *,
        export_id: str,
        worker_id: str,
        error_class: str,
        next_attempt_at_ms: int,
        now_ms: int,
    ) -> dict[str, Any] | None:
        export_id = _required(export_id, "export_id")
        worker_id = _required(worker_id, "worker_id")
        error_class = _required(error_class, "error_class")
        now_ms = max(0, int(now_ms))
        next_attempt_at_ms = max(now_ms, int(next_attempt_at_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE recording_exports SET "
                "status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'retry_wait' END, "
                "next_attempt_at_ms = ?, lease_owner = NULL, lease_until_ms = NULL, "
                "error_class = ?, completed_at_ms = CASE WHEN attempts >= max_attempts THEN ? ELSE NULL END, "
                "updated_at_ms = ? WHERE id = ? AND status = 'running' AND lease_owner = ? "
                "AND lease_until_ms > ?",
                (
                    next_attempt_at_ms,
                    error_class,
                    now_ms,
                    now_ms,
                    export_id,
                    worker_id,
                    now_ms,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM recording_exports WHERE id = ?",
                (export_id,),
            ).fetchone()
            self._conn.execute(
                "UPDATE recording_sessions SET status = ?, error_class = ?, updated_at_ms = ? "
                "WHERE (meeting_id, track, epoch) = (?, ?, ?)",
                (
                    "failed" if row["status"] == "failed" else "sealed",
                    error_class,
                    now_ms,
                    row["meeting_id"],
                    row["track"],
                    int(row["epoch"]),
                ),
            )
        return self._recording_export_dict(row)

    def recover_expired_recording_export_leases(self, *, now_ms: int) -> int:
        with self._write_transaction():
            return self._recover_expired_recording_export_leases_locked(max(0, int(now_ms)))

    def _recover_expired_recording_export_leases_locked(self, now_ms: int) -> int:
        rows = self._conn.execute(
            "SELECT * FROM recording_exports WHERE status = 'running' "
            "AND lease_until_ms IS NOT NULL AND lease_until_ms <= ?",
            (now_ms,),
        ).fetchall()
        for row in rows:
            terminal = int(row["attempts"]) >= int(row["max_attempts"])
            self._conn.execute(
                "UPDATE recording_exports SET status = ?, lease_owner = NULL, lease_until_ms = NULL, "
                "next_attempt_at_ms = ?, error_class = 'lease_expired', "
                "completed_at_ms = ?, updated_at_ms = ? WHERE id = ? AND status = 'running'",
                (
                    "failed" if terminal else "retry_wait",
                    now_ms,
                    now_ms if terminal else None,
                    now_ms,
                    row["id"],
                ),
            )
            self._conn.execute(
                "UPDATE recording_sessions SET status = ?, error_class = 'lease_expired', updated_at_ms = ? "
                "WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (
                    "failed" if terminal else "sealed",
                    now_ms,
                    row["meeting_id"],
                    row["track"],
                    int(row["epoch"]),
                ),
            )
        return len(rows)

    def recover_expired_recording_leases(self, *, now_ms: int) -> list[str]:
        """Recover only capture sessions whose durable lease has expired."""

        now_ms = max(0, int(now_ms))
        recovered_meeting_ids: list[str] = []
        with self._write_transaction():
            rows = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE status = 'active' "
                "AND lease_until_ms IS NOT NULL AND lease_until_ms <= ? "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_sessions.meeting_id) "
                "ORDER BY meeting_id, track, epoch",
                (now_ms,),
            ).fetchall()
            for row in rows:
                meeting_id = str(row["meeting_id"])
                self._conn.execute(
                    "UPDATE recording_sessions SET status = 'interrupted', lease_owner = NULL, "
                    "lease_until_ms = NULL, error_class = 'capture_lease_expired', sealed_at_ms = ?, "
                    "updated_at_ms = ? WHERE meeting_id = ? AND track = ? AND epoch = ? "
                    "AND status = 'active'",
                    (now_ms, now_ms, meeting_id, row["track"], int(row["epoch"])),
                )
                self._conn.execute(
                    "UPDATE meetings SET state = CASE WHEN state = 'live' THEN 'interrupted' ELSE state END, "
                    "revision = revision + CASE WHEN state = 'live' THEN 1 ELSE 0 END, "
                    "updated_at_ms = ? WHERE id = ?",
                    (now_ms, meeting_id),
                )
                updated_row = self._conn.execute(
                    "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, row["track"], int(row["epoch"])),
                ).fetchone()
                recording = self._recording_session_dict(updated_row)
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="recording.interrupted",
                    aggregate_type="recording_session",
                    aggregate_id=f"{row['track']}:{int(row['epoch'])}",
                    occurred_at_ms=now_ms,
                    idempotency_key=(
                        f"recording.interrupted:{row['track']}:{int(row['epoch'])}:"
                        f"generation:{int(row['capture_generation'])}:"
                        f"{recording.get('journal_sha256') or 'empty'}"
                    ),
                    payload={**recording, "recoverable": True},
                    correlation_id=meeting_id,
                )
                if int(recording["chunk_count"]) > 0:
                    self._enqueue_recording_export_locked(recording=recording, now_ms=now_ms)
                if meeting_id not in recovered_meeting_ids:
                    recovered_meeting_ids.append(meeting_id)
        return recovered_meeting_ids

    def list_expired_recording_sessions(self, *, now_ms: int) -> list[dict[str, Any]]:
        """Return capture leases eligible for journal reconciliation and recovery."""

        now_ms = max(0, int(now_ms))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE status = 'active' "
                "AND lease_until_ms IS NOT NULL AND lease_until_ms <= ? "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_sessions.meeting_id) "
                "ORDER BY meeting_id, track, epoch",
                (now_ms,),
            ).fetchall()
        return [self._recording_session_dict(row) for row in rows]

    def create_deletion_job(
        self,
        *,
        meeting_id: str,
        managed_paths: list[str],
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        paths = [_required(path, "managed_path") for path in managed_paths]
        now_ms = max(0, int(now_ms))
        job_id = _stable_id("delete", meeting_id)
        with self._write_transaction():
            self._conn.execute(
                "INSERT OR IGNORE INTO meeting_tombstones ("
                "meeting_id, deletion_job_id, created_at_ms) VALUES (?, ?, ?)",
                (meeting_id, job_id, now_ms),
            )
            self._conn.execute(
                "INSERT INTO deletion_jobs ("
                "id, meeting_id, status, paths_json, attempts, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, 'pending', ?, 0, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "status = CASE WHEN deletion_jobs.status = 'completed' "
                "THEN 'completed' ELSE 'pending' END, "
                "paths_json = excluded.paths_json, error_class = NULL, "
                "updated_at_ms = excluded.updated_at_ms",
                (job_id, meeting_id, _json_dump(paths), now_ms, now_ms),
            )
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._deletion_job_dict(row)

    def list_deletion_jobs(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        where = ""
        if statuses:
            normalized = tuple(_required(status, "status") for status in statuses)
            where = f" WHERE status IN ({','.join('?' for _ in normalized)})"
            parameters.extend(normalized)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM deletion_jobs" + where + " ORDER BY created_at_ms, id",
                parameters,
            ).fetchall()
        return [self._deletion_job_dict(row) for row in rows]

    def mark_deletion_running(self, *, job_id: str, now_ms: int) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE deletion_jobs SET status = 'running', attempts = attempts + 1, "
                "updated_at_ms = ?, error_class = NULL WHERE id = ? "
                "AND status IN ('pending', 'running', 'failed')",
                (now_ms, job_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"deletion job is not runnable: {job_id}")
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._deletion_job_dict(row)

    def fail_deletion_job(
        self,
        *,
        job_id: str,
        error_class: str,
        now_ms: int,
    ) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        error_class = _required(error_class, "error_class")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            self._conn.execute(
                "UPDATE deletion_jobs SET status = 'failed', error_class = ?, "
                "updated_at_ms = ? WHERE id = ? AND status = 'running'",
                (error_class, now_ms, job_id),
            )
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"deletion job not found: {job_id}")
        return self._deletion_job_dict(row)

    def complete_deletion_and_purge(
        self,
        *,
        job_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"deletion job not found: {job_id}")
            meeting_id = str(row["meeting_id"])
            for table in (
                "suggestions",
                "jobs",
                "meeting_entities",
                "minutes",
                "approach_artifacts",
                "search_documents",
                "recording_exports",
                "recording_sessions",
                "audio_chunks",
                "transcript_segments",
                "meeting_events",
                "meetings",
            ):
                self._conn.execute(
                    f"DELETE FROM {table} WHERE meeting_id = ?"
                    if table != "meetings"
                    else "DELETE FROM meetings WHERE id = ?",
                    (meeting_id,),
                )
            self._conn.execute(
                "UPDATE deletion_jobs SET status = 'completed', error_class = NULL, "
                "completed_at_ms = ?, updated_at_ms = ? WHERE id = ?",
                (now_ms, now_ms, job_id),
            )
            completed = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._deletion_job_dict(completed)

    def save_minutes(
        self,
        *,
        meeting_id: str,
        job_id: str,
        markdown: str,
        structured: Mapping[str, Any] | None,
        degraded: bool,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        job_id = _required(job_id, "job_id")
        markdown = _required(markdown, "markdown")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            existing = self._conn.execute(
                "SELECT version, created_at_ms FROM minutes WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            version = int(existing["version"]) + 1 if existing is not None else 1
            created_at_ms = int(existing["created_at_ms"]) if existing is not None else now_ms
            self._conn.execute(
                "INSERT INTO minutes (meeting_id, job_id, version, status, markdown, "
                "structured_json, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET job_id = excluded.job_id, "
                "version = excluded.version, status = excluded.status, markdown = excluded.markdown, "
                "structured_json = excluded.structured_json, updated_at_ms = excluded.updated_at_ms",
                (
                    meeting_id,
                    job_id,
                    version,
                    "degraded" if degraded else "ready",
                    markdown,
                    _json_dump(structured) if structured is not None else None,
                    created_at_ms,
                    now_ms,
                ),
            )
            artifact = self._minutes_locked(meeting_id)
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.minutes.ready",
                aggregate_type="minutes",
                aggregate_id=meeting_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.minutes:{job_id}:{version}",
                payload=artifact,
                correlation_id=meeting_id,
                causation_id=job_id,
            )
        return artifact

    def save_approach_cards(
        self,
        *,
        meeting_id: str,
        job_id: str,
        cards: list[Mapping[str, Any]],
        degraded: bool,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            existing = self._conn.execute(
                "SELECT created_at_ms FROM approach_artifacts WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            created_at_ms = int(existing["created_at_ms"]) if existing is not None else now_ms
            self._conn.execute(
                "INSERT INTO approach_artifacts (meeting_id, job_id, cards_json, degraded, "
                "created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(meeting_id) DO UPDATE SET job_id = excluded.job_id, "
                "cards_json = excluded.cards_json, degraded = excluded.degraded, "
                "updated_at_ms = excluded.updated_at_ms",
                (
                    meeting_id,
                    job_id,
                    _json_dump([dict(card) for card in cards]),
                    int(bool(degraded)),
                    created_at_ms,
                    now_ms,
                ),
            )
            artifact = {
                "meeting_id": meeting_id,
                "job_id": job_id,
                "cards": [dict(card) for card in cards],
                "degraded": bool(degraded),
                "updated_at_ms": now_ms,
            }
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.approach.ready",
                aggregate_type="approach",
                aggregate_id=meeting_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.approach:{job_id}",
                payload=artifact,
                correlation_id=meeting_id,
                causation_id=job_id,
            )
        return artifact

    def rebuild_search_document(
        self,
        *,
        meeting_id: str,
        job_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            rows = self._conn.execute(
                "SELECT normalized_text FROM transcript_segments WHERE meeting_id = ? ORDER BY transcript_seq",
                (meeting_id,),
            ).fetchall()
            transcript_text = "\n".join(str(row["normalized_text"]) for row in rows)
            transcript_hash = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
            self._conn.execute(
                "INSERT INTO search_documents (meeting_id, transcript_text, transcript_hash, updated_at_ms) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(meeting_id) DO UPDATE SET "
                "transcript_text = excluded.transcript_text, transcript_hash = excluded.transcript_hash, "
                "updated_at_ms = excluded.updated_at_ms",
                (meeting_id, transcript_text, transcript_hash, now_ms),
            )
            artifact = {
                "meeting_id": meeting_id,
                "job_id": job_id,
                "transcript_hash": transcript_hash,
                "character_count": len(transcript_text),
                "updated_at_ms": now_ms,
            }
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.index.ready",
                aggregate_type="search_document",
                aggregate_id=meeting_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.index:{job_id}:{transcript_hash}",
                payload=artifact,
                correlation_id=meeting_id,
                causation_id=job_id,
            )
        return artifact

    def _minutes_locked(self, meeting_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM minutes WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "meeting_id": row["meeting_id"],
            "job_id": row["job_id"],
            "version": int(row["version"]),
            "status": row["status"],
            "markdown": row["markdown"],
            "structured": json.loads(row["structured_json"]) if row["structured_json"] is not None else None,
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def commit_suggestion(
        self,
        *,
        suggestion_id: str,
        generation_id: str,
        expected_evidence_hash: str,
        final_draft_seq: int,
        text: str,
        now_ms: int,
        expected_job_id: str | None = None,
        expected_lease_owner: str | None = None,
    ) -> dict[str, Any] | None:
        suggestion_id = _required(suggestion_id, "suggestion_id")
        generation_id = _required(generation_id, "generation_id")
        expected_evidence_hash = _required(expected_evidence_hash, "expected_evidence_hash")
        text = _required(text, "text")
        final_draft_seq = int(final_draft_seq)
        now_ms = max(0, int(now_ms))
        normalized_job_id = str(expected_job_id or "").strip() or None
        normalized_lease_owner = str(expected_lease_owner or "").strip() or None
        if (normalized_job_id is None) != (normalized_lease_owner is None):
            raise ValueError("expected_job_id and expected_lease_owner must be provided together")
        with self._write_transaction():
            if normalized_job_id is not None:
                self._assert_job_lease_locked(
                    job_id=normalized_job_id,
                    lease_owner=normalized_lease_owner,
                    now_ms=now_ms,
                )
            result = self._conn.execute(
                "UPDATE suggestions SET status = 'committed', text = ?, final_draft_seq = ?, "
                "committed_at_ms = ?, updated_at_ms = ? "
                "WHERE suggestion_id = ? AND generation_id = ? AND status = 'draft' "
                "AND evidence_hash = ? AND draft_seq = ? "
                "AND EXISTS ("
                "SELECT 1 FROM transcript_segments AS segment "
                "WHERE segment.meeting_id = suggestions.meeting_id "
                "AND segment.segment_id = suggestions.evidence_segment_id "
                "AND segment.transcript_seq = suggestions.evidence_transcript_seq "
                "AND segment.evidence_hash = suggestions.evidence_hash"
                ")",
                (
                    text,
                    final_draft_seq,
                    now_ms,
                    now_ms,
                    suggestion_id,
                    generation_id,
                    expected_evidence_hash,
                    final_draft_seq,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM suggestions WHERE suggestion_id = ?", (suggestion_id,)).fetchone()
            suggestion = self._suggestion_dict(row)
            self._append_event_locked(
                meeting_id=str(row["meeting_id"]),
                event_type="suggestion.committed",
                aggregate_type="suggestion",
                aggregate_id=suggestion_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"suggestion.committed:{suggestion_id}:{final_draft_seq}",
                payload=suggestion,
                correlation_id=generation_id,
                causation_id=str(row["job_id"] or "") or None,
            )
        return suggestion

    def get_snapshot(
        self,
        meeting_id: str,
        *,
        segment_limit: int = 500,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        segment_limit = int(segment_limit)
        if not 1 <= segment_limit <= 1_000:
            raise ValueError("segment_limit must be between 1 and 1000")
        with self._lock:
            last_seq = int(
                self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM meeting_events WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()[0]
            )
            total_segments = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM transcript_segments WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()[0]
            )
            segment_rows = list(
                reversed(
                    self._conn.execute(
                        "SELECT * FROM transcript_segments WHERE meeting_id = ? ORDER BY transcript_seq DESC LIMIT ?",
                        (meeting_id, segment_limit),
                    ).fetchall()
                )
            )
            suggestion_rows = self._conn.execute(
                "SELECT * FROM suggestions WHERE meeting_id = ? "
                "ORDER BY evidence_transcript_seq, created_at_ms, suggestion_id",
                (meeting_id,),
            ).fetchall()
            meeting_row = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
            state = self._meeting_state_locked(meeting_id)
            jobs = self.list_jobs(meeting_id=meeting_id)
            minutes = self._minutes_locked(meeting_id)
            approach_row = self._conn.execute(
                "SELECT * FROM approach_artifacts WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            audio_rows = self._conn.execute(
                "SELECT * FROM audio_chunks WHERE meeting_id = ? ORDER BY track, epoch, chunk_seq",
                (meeting_id,),
            ).fetchall()
            recording_rows = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? ORDER BY track, epoch",
                (meeting_id,),
            ).fetchall()
        meeting = self._meeting_dict(meeting_row) if meeting_row is not None else None
        recording_statuses = {str(row["status"]) for row in recording_rows}
        audio_status = (
            "failed"
            if "failed" in recording_statuses
            else "recording"
            if "active" in recording_statuses
            else "assembling"
            if recording_statuses & {"sealed", "exporting", "interrupted"}
            else "saved"
            if "ready" in recording_statuses or (audio_rows and meeting is not None and meeting["state"] != "live")
            else "recording"
            if meeting is not None and meeting["state"] == "live"
            else "unknown"
        )
        recording_indicator = (
            {"state": "error", "label": "录音整理失败"}
            if audio_status == "failed"
            else {"state": "active", "label": "正在录音"}
            if audio_status == "recording"
            else {"state": "busy", "label": "正在整理录音"}
            if audio_status == "assembling"
            else {"state": "idle", "label": "录音已保存"}
            if audio_status == "saved"
            else {"state": "unknown", "label": "等待录音"}
        )
        ai_busy = any(job["status"] in {"pending", "running", "retry_wait"} for job in jobs)
        review_jobs: dict[str, dict[str, Any]] = {}
        for job in jobs:
            kind = str(job["kind"])
            if kind not in {"minutes", "approach", "index"}:
                continue
            current = review_jobs.get(kind)
            if current is not None and int(current["created_at_ms"]) > int(job["created_at_ms"]):
                continue
            review_jobs[kind] = {
                "id": job["id"],
                "kind": kind,
                "status": job["status"],
                "attempts": job["attempts"],
                "max_attempts": job["max_attempts"],
                "error_class": job["error_class"],
                "created_at_ms": job["created_at_ms"],
                "updated_at_ms": job["updated_at_ms"],
                "completed_at_ms": job["completed_at_ms"],
            }
        return {
            "meeting_id": meeting_id,
            "title": meeting["title"] if meeting is not None else None,
            "last_seq": last_seq,
            "segments": [self._segment_dict(row) for row in segment_rows],
            "transcript_page": {
                "returned": len(segment_rows),
                "total": total_segments,
                "has_more": total_segments > len(segment_rows),
                "first_seq": int(segment_rows[0]["transcript_seq"]) if segment_rows else None,
                "last_seq": int(segment_rows[-1]["transcript_seq"]) if segment_rows else None,
            },
            "suggestions": [self._suggestion_dict(row) for row in suggestion_rows],
            "jobs": [self._job_status_summary(job) for job in jobs],
            "current_topic": state["current_topic"],
            "open_questions": state["open_questions"],
            "active_partial": None,
            "minutes": minutes,
            "approach_cards": json.loads(approach_row["cards_json"]) if approach_row is not None else [],
            "review_jobs": review_jobs,
            "review": {
                "status": (
                    "failed"
                    if any(job["status"] == "failed" for job in review_jobs.values())
                    else "processing"
                    if any(job["status"] in {"pending", "running", "retry_wait"} for job in review_jobs.values())
                    else "ready"
                    if meeting is not None and meeting["state"] == "ended"
                    else "unavailable"
                ),
                "minutes_status": minutes["status"] if minutes is not None else None,
                "approach_status": ("degraded" if approach_row is not None and approach_row["degraded"] else "ready")
                if approach_row is not None
                else None,
                "indexed": any(job["kind"] == "index" and job["status"] == "succeeded" for job in review_jobs.values()),
            },
            "audio": {
                "chunk_count": len(audio_rows),
                "duration_ms": sum(int(row["duration_ms"]) for row in audio_rows),
                "file_size_bytes": sum(int(row["file_size_bytes"]) for row in audio_rows),
                "tracks": sorted({str(row["track"]) for row in audio_rows}),
                "status": audio_status,
            },
            "runtime": {
                "phase": meeting["state"] if meeting is not None else "unknown",
                "recording": recording_indicator,
                "input": {"state": "unknown", "label": "等待输入状态"},
                "ai": {
                    "state": "busy" if ai_busy else "idle",
                    "label": "AI 正在处理" if ai_busy else "AI 已同步",
                },
                "elapsed_ms": (
                    max(
                        0,
                        int(
                            (meeting["ended_at_ms"] or meeting["updated_at_ms"])
                            - (meeting["started_at_ms"] or meeting["created_at_ms"])
                        ),
                    )
                    if meeting is not None
                    else None
                ),
            },
        }

    def meeting_exists(self, meeting_id: str) -> bool:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM meetings WHERE id = ?",
                    (meeting_id,),
                ).fetchone()
                is not None
            )

    def is_meeting_tombstoned(self, meeting_id: str) -> bool:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM meeting_tombstones WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()
                is not None
            )

    def list_transcript_segments(
        self,
        meeting_id: str,
        *,
        after_transcript_seq: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        after_transcript_seq = max(0, int(after_transcript_seq))
        limit = int(limit)
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? "
                "AND transcript_seq > ? ORDER BY transcript_seq LIMIT ?",
                (meeting_id, after_transcript_seq, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        segments = [self._segment_dict(row) for row in visible]
        return {
            "meeting_id": meeting_id,
            "after_transcript_seq": after_transcript_seq,
            "segments": segments,
            "has_more": has_more,
            "next_after_transcript_seq": (segments[-1]["transcript_seq"] if segments else after_transcript_seq),
        }

    def get_transcript_segment(
        self,
        meeting_id: str,
        segment_id: str,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        segment_id = _required(segment_id, "segment_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                (meeting_id, segment_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"transcript segment not found: {meeting_id}/{segment_id}")
        return self._segment_dict(row)

    def list_meetings(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = int(limit)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.*, "
                "(SELECT COUNT(*) FROM transcript_segments t WHERE t.meeting_id = m.id) "
                "AS segment_count, "
                "(SELECT COUNT(*) FROM suggestions s WHERE s.meeting_id = m.id "
                "AND s.status = 'committed') AS suggestion_count, "
                "(SELECT COALESCE(SUM(a.duration_ms), 0) FROM audio_chunks a "
                "WHERE a.meeting_id = m.id) AS audio_duration_ms, "
                "EXISTS(SELECT 1 FROM minutes n WHERE n.meeting_id = m.id) AS has_minutes "
                "FROM meetings m ORDER BY m.updated_at_ms DESC, m.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                **self._meeting_dict(row),
                "segment_count": int(row["segment_count"]),
                "suggestion_count": int(row["suggestion_count"]),
                "audio_duration_ms": int(row["audio_duration_ms"]),
                "has_minutes": bool(row["has_minutes"]),
            }
            for row in rows
        ]

    def list_audio_chunks(self, meeting_id: str) -> list[dict[str, Any]]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audio_chunks WHERE meeting_id = ? ORDER BY track, epoch, chunk_seq",
                (meeting_id,),
            ).fetchall()
        return [self._audio_chunk_dict(row) for row in rows]

    def get_recording_session(
        self,
        meeting_id: str,
        *,
        track: str,
        epoch: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        epoch = int(epoch)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
        if row is None:
            raise KeyError(f"recording session not found: {meeting_id}/{track}/{epoch}")
        return self._recording_session_dict(row)

    def list_recording_sessions(self, meeting_id: str) -> list[dict[str, Any]]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? ORDER BY track, epoch",
                (meeting_id,),
            ).fetchall()
        return [self._recording_session_dict(row) for row in rows]

    def list_recording_exports(
        self,
        *,
        meeting_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if meeting_id is None:
                rows = self._conn.execute("SELECT * FROM recording_exports ORDER BY created_at_ms, id").fetchall()
            else:
                meeting_id = _required(meeting_id, "meeting_id")
                rows = self._conn.execute(
                    "SELECT * FROM recording_exports WHERE meeting_id = ? ORDER BY created_at_ms, id",
                    (meeting_id,),
                ).fetchall()
        return [self._recording_export_dict(row) for row in rows]

    def list_events(
        self,
        meeting_id: str,
        *,
        after_seq: int = 0,
        limit: int = DEFAULT_EVENT_PAGE_LIMIT,
    ) -> list[dict[str, Any]]:
        meeting_id = _required(meeting_id, "meeting_id")
        after_seq = max(0, int(after_seq))
        limit = int(limit)
        if not 1 <= limit <= MAX_EVENT_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_EVENT_PAGE_LIMIT}")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meeting_events WHERE meeting_id = ? AND seq > ? "
                "ORDER BY seq LIMIT ?",
                (meeting_id, after_seq, limit),
            ).fetchall()
        return [self._event_dict(row) for row in rows]

    def list_event_page(
        self,
        meeting_id: str,
        *,
        after_seq: int = 0,
        limit: int = DEFAULT_EVENT_PAGE_LIMIT,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        after_seq = max(0, int(after_seq))
        limit = int(limit)
        if not 1 <= limit <= MAX_EVENT_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_EVENT_PAGE_LIMIT}")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM meeting_events WHERE meeting_id = ? AND seq > ? "
                "ORDER BY seq LIMIT ?",
                (meeting_id, after_seq, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        events = [self._event_dict(row) for row in visible]
        next_after_seq = int(events[-1]["seq"]) if events else after_seq
        return {
            "meeting_id": meeting_id,
            "after_seq": after_seq,
            "last_seq": next_after_seq,
            "events": events,
            "has_more": has_more,
            "next_after_seq": next_after_seq,
        }

    def list_jobs(
        self,
        *,
        meeting_id: str | None = None,
        lane: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if meeting_id is not None:
            clauses.append("meeting_id = ?")
            parameters.append(_required(meeting_id, "meeting_id"))
        if lane is not None:
            clauses.append("kind = ?")
            parameters.append(_required(lane, "lane"))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs" + where + " ORDER BY input_transcript_seq, priority DESC, created_at_ms, id",
                parameters,
            ).fetchall()
        return [self._job_dict(row) for row in rows]

    def supersede_correction_jobs(
        self,
        *,
        meeting_id: str,
        segment_ids: list[str],
        except_job_id: str,
        now_ms: int,
    ) -> int:
        meeting_id = _required(meeting_id, "meeting_id")
        except_job_id = _required(except_job_id, "except_job_id")
        normalized_segment_ids = sorted(
            {_required(segment_id, "segment_id") for segment_id in segment_ids if str(segment_id or "").strip()}
        )
        if not normalized_segment_ids:
            return 0
        placeholders = ",".join("?" for _ in normalized_segment_ids)
        completed_at_ms = max(0, int(now_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE jobs SET status = 'cancelled', lease_owner = NULL, "
                "lease_until_ms = NULL, error_class = 'evidence_superseded', "
                "completed_at_ms = COALESCE(completed_at_ms, ?), updated_at_ms = ? "
                "WHERE meeting_id = ? AND kind = 'correction' AND id != ? "
                f"AND evidence_segment_id IN ({placeholders}) "
                "AND status IN ('pending', 'retry_wait', 'failed')",
                (
                    completed_at_ms,
                    completed_at_ms,
                    meeting_id,
                    except_job_id,
                    *normalized_segment_ids,
                ),
            )
        return int(result.rowcount)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job not found: {job_id}")
        return self._job_dict(row)

    @staticmethod
    def _entity_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["entity_id"],
            "text": row["text"],
            "status": row["status"],
            "evidence_segment_ids": json.loads(row["evidence_segment_ids_json"]),
            "updated_at_ms": row["updated_at_ms"],
            "version": int(row["version"]),
            "first_seen_seq": int(row["first_seen_seq"]),
            "last_updated_seq": int(row["last_updated_seq"]),
        }

    @staticmethod
    def _recording_session_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "track": row["track"],
            "epoch": int(row["epoch"]),
            "source_type": row["source_type"],
            "capture_generation": int(row["capture_generation"]),
            "status": row["status"],
            "sample_rate_hz": int(row["sample_rate_hz"]),
            "chunk_count": int(row["chunk_count"]),
            "sample_count": int(row["sample_count"]),
            "duration_ms": int(row["duration_ms"]),
            "file_size_bytes": int(row["file_size_bytes"]),
            "lease_owner": row["lease_owner"],
            "lease_until_ms": row["lease_until_ms"],
            "output_relative_path": row["output_relative_path"],
            "journal_sha256": row["journal_sha256"],
            "output_sha256": row["output_sha256"],
            "output_file_size_bytes": row["output_file_size_bytes"],
            "error_class": row["error_class"],
            "started_at_ms": int(row["started_at_ms"]),
            "sealed_at_ms": row["sealed_at_ms"],
            "completed_at_ms": row["completed_at_ms"],
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _recording_export_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "meeting_id": row["meeting_id"],
            "track": row["track"],
            "epoch": int(row["epoch"]),
            "status": row["status"],
            "output_relative_path": row["output_relative_path"],
            "input_chunk_count": int(row["input_chunk_count"]),
            "input_sample_count": int(row["input_sample_count"]),
            "input_journal_sha256": row["input_journal_sha256"],
            "attempts": int(row["attempts"]),
            "max_attempts": int(row["max_attempts"]),
            "lease_owner": row["lease_owner"],
            "lease_until_ms": row["lease_until_ms"],
            "next_attempt_at_ms": int(row["next_attempt_at_ms"]),
            "output": json.loads(row["output_json"]) if row["output_json"] is not None else None,
            "error_class": row["error_class"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _deletion_job_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "meeting_id": row["meeting_id"],
            "status": row["status"],
            "managed_paths": json.loads(row["paths_json"]),
            "attempts": int(row["attempts"]),
            "error_class": row["error_class"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _audio_chunk_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "track": row["track"],
            "epoch": int(row["epoch"]),
            "chunk_seq": int(row["chunk_seq"]),
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "sample_rate_hz": int(row["sample_rate_hz"]),
            "sample_count": int(row["sample_count"]),
            "duration_ms": int(row["duration_ms"]),
            "file_size_bytes": int(row["file_size_bytes"]),
            "status": row["status"],
            "created_at_ms": int(row["created_at_ms"]),
        }

    @staticmethod
    def _meeting_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "state": row["state"],
            "title": row["title"],
            "started_at_ms": row["started_at_ms"],
            "ended_at_ms": row["ended_at_ms"],
            "latest_seq": int(row["latest_seq"]),
            "revision": int(row["revision"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _segment_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "segment_id": row["segment_id"],
            "final_id": row["final_id"],
            "transcript_seq": int(row["transcript_seq"]),
            "text": row["text"],
            "normalized_text": row["normalized_text"],
            "started_at_ms": row["started_at_ms"],
            "ended_at_ms": row["ended_at_ms"],
            "revision": int(row["revision"]),
            "evidence_hash": row["evidence_hash"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "seq": int(row["seq"]),
            "event_id": row["event_id"],
            "type": row["type"],
            "aggregate_type": row["aggregate_type"],
            "aggregate_id": row["aggregate_id"],
            "occurred_at_ms": int(row["occurred_at_ms"]),
            "correlation_id": row["correlation_id"],
            "causation_id": row["causation_id"],
            "idempotency_key": row["idempotency_key"],
            "payload": json.loads(row["payload_json"]),
            "published_at_ms": row["published_at_ms"],
        }

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "meeting_id": row["meeting_id"],
            "kind": row["kind"],
            "status": row["status"],
            "priority": int(row["priority"]),
            "input_transcript_seq": int(row["input_transcript_seq"]),
            "input_version": int(row["input_version"]),
            "evidence_segment_id": row["evidence_segment_id"],
            "evidence_hash": row["evidence_hash"],
            "generation_id": row["generation_id"],
            "idempotency_key": row["idempotency_key"],
            "attempts": int(row["attempts"]),
            "max_attempts": int(row["max_attempts"]),
            "lease_owner": row["lease_owner"],
            "lease_until_ms": row["lease_until_ms"],
            "next_attempt_at_ms": int(row["next_attempt_at_ms"]),
            "deadline_at_ms": row["deadline_at_ms"],
            "output": json.loads(row["output_json"]) if row["output_json"] is not None else None,
            "error_class": row["error_class"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _job_status_summary(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job["id"],
            "kind": job["kind"],
            "status": job["status"],
            "attempts": job["attempts"],
            "max_attempts": job["max_attempts"],
            "error_class": job["error_class"],
            "created_at_ms": job["created_at_ms"],
            "updated_at_ms": job["updated_at_ms"],
            "completed_at_ms": job["completed_at_ms"],
        }

    @staticmethod
    def _suggestion_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "suggestion_id": row["suggestion_id"],
            "meeting_id": row["meeting_id"],
            "job_id": row["job_id"],
            "generation_id": row["generation_id"],
            "evidence_segment_id": row["evidence_segment_id"],
            "evidence_transcript_seq": int(row["evidence_transcript_seq"]),
            "evidence_hash": row["evidence_hash"],
            "state_revision": int(row["state_revision"]),
            "status": row["status"],
            "draft_text": row["draft_text"],
            "draft_seq": int(row["draft_seq"]),
            "text": row["text"],
            "final_draft_seq": row["final_draft_seq"],
            "feedback": row["feedback"],
            "feedback_at_ms": row["feedback_at_ms"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "committed_at_ms": row["committed_at_ms"],
        }
