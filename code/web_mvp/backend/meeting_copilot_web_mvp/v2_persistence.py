from __future__ import annotations

from contextlib import contextmanager
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
from threading import RLock
from typing import Any, Iterator, Mapping
import unicodedata

from .application_schema import bootstrap_application_schema, fallback_meeting_title
from .meeting_state_extractor import extract_meeting_state
from .audio_assets import audio_chunk_journal_sha256
from .storage_governance import (
    ensure_private_directory,
    harden_private_file,
    harden_sqlite_files,
)
from .next006_failpoints import storage_write_failpoint


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
MAX_REVIEW_DOCUMENT_BYTES = 2 * 1024 * 1024
REVIEW_JOB_KINDS = frozenset({"minutes", "approach", "index"})
REVIEW_DOCUMENT_KINDS = frozenset({"minutes", "decisions", "action_items", "risks", "transcript"})
INTELLIGENCE_DEBOUNCE_MS = 2_000
INTELLIGENCE_MAX_WAIT_MS = 8_000
IMPORT_JOB_STAGES = (
    "reading",
    "normalizing",
    "transcribing",
    "correcting",
    "reviewing",
    "completed",
)
TITLE_SOURCES = frozenset({"ai", "fallback", "import", "migration", "user"})
DATA_DELETION_SCOPES = frozenset({"recording", "derived", "transcript", "all"})
DATA_DELETION_TRIGGERS = frozenset({"user", "retention", "system"})
RETENTION_POLICIES = frozenset(
    {
        "local_until_user_deletes",
        "manual_only",
        "30_days",
        "90_days",
        "365_days",
    }
)
RETENTION_POLICY_DAYS = {
    "30_days": 30,
    "90_days": 90,
    "365_days": 365,
}
DEFAULT_RETENTION_POLICY = "local_until_user_deletes"
DUAL_RECORDING_TRACKS = frozenset({"microphone", "system_audio"})
TRANSCRIPT_SOURCE_TRACKS = frozenset({*DUAL_RECORDING_TRACKS, "uploaded_file"})
SOURCE_DUPLICATE_MIN_TEXT_CHARS = 6
SOURCE_DUPLICATE_MIN_SIMILARITY = 0.88
SOURCE_DUPLICATE_MAX_TIMELINE_SKEW_MS = 1_500
MIN_RETENTION_RUN_INTERVAL_MS = 24 * 60 * 60 * 1_000
_DOCUMENT_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TITLE_FORBIDDEN_PATTERN = re.compile(r"[\x00-\x1f\x7f/\\]")
_MANAGED_MEETING_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SPEAKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SPEAKER_LABEL_FORBIDDEN_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
PUBLIC_JOB_ERROR_CLASSES = frozenset(
    {
        "ConnectionError",
        "CorrectionProjectionFailed",
        "DeferredCorrection",
        "NonRetryableProviderError",
        "ProviderRuntimeNotConfiguredDeferred",
        "ReservationChanged",
        "TranscriptRevisionIdentityConflict",
        "TimeoutError",
        "evidence_superseded",
        "intelligence_validation_evidence",
        "intelligence_validation_semantic_safety",
        "intelligence_validation_stale",
        "intelligence_validation_structural",
        "intelligence_validation_truncated",
        "job_failed",
        "lease_expired",
        "provider_429",
        "provider_not_synced",
    }
)




def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _validated_speaker_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not _SPEAKER_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "speaker_id must be 1-128 characters using letters, numbers, dot, underscore, colon, or hyphen"
        )
    return normalized


def _validated_speaker_confidence(
    value: float | None,
    *,
    speaker_id: str | None,
) -> float | None:
    if value is None:
        return None
    if speaker_id is None:
        raise ValueError("speaker_confidence requires speaker_id")
    if isinstance(value, bool):
        raise ValueError("speaker_confidence must be a number between 0 and 1")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("speaker_confidence must be a number between 0 and 1") from exc
    if not math.isfinite(normalized) or not 0 <= normalized <= 1:
        raise ValueError("speaker_confidence must be a number between 0 and 1")
    return normalized


def _validated_transcript_source_track(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in TRANSCRIPT_SOURCE_TRACKS:
        raise ValueError(f"source_track must be one of {sorted(TRANSCRIPT_SOURCE_TRACKS)}")
    return normalized


def _source_duplicate_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _source_duplicate_similarity(left: str, right: str) -> float:
    normalized_left = _source_duplicate_text(left)
    normalized_right = _source_duplicate_text(right)
    if min(len(normalized_left), len(normalized_right)) < SOURCE_DUPLICATE_MIN_TEXT_CHARS:
        return 0.0
    ratio = SequenceMatcher(None, normalized_left, normalized_right, autojunk=False).ratio()
    shorter, longer = sorted((normalized_left, normalized_right), key=len)
    containment = len(shorter) / len(longer) if shorter in longer else 0.0
    return max(ratio, containment)


def _source_duplicate_timeline_matches(
    *,
    started_at_ms: int | None,
    ended_at_ms: int | None,
    candidate_started_at_ms: int | None,
    candidate_ended_at_ms: int | None,
) -> bool:
    if None in (
        started_at_ms,
        ended_at_ms,
        candidate_started_at_ms,
        candidate_ended_at_ms,
    ):
        return False
    start = int(started_at_ms)
    end = max(start, int(ended_at_ms))
    candidate_start = int(candidate_started_at_ms)
    candidate_end = max(candidate_start, int(candidate_ended_at_ms))
    return (
        start <= candidate_end + SOURCE_DUPLICATE_MAX_TIMELINE_SKEW_MS
        and candidate_start <= end + SOURCE_DUPLICATE_MAX_TIMELINE_SKEW_MS
    )


def _validated_speaker_label(value: str) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise ValueError("speaker_label must not be empty")
    if len(normalized) > 80:
        raise ValueError("speaker_label must not exceed 80 characters")
    if _SPEAKER_LABEL_FORBIDDEN_PATTERN.search(normalized):
        raise ValueError("speaker_label contains forbidden control characters")
    return normalized


def _validated_title(value: Any) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise ValueError("title must not be empty")
    if len(normalized) > 200:
        raise ValueError("title must not exceed 200 characters")
    if _TITLE_FORBIDDEN_PATTERN.search(normalized):
        raise ValueError("title contains path-unsafe or control characters")
    return normalized


def _validated_title_source(value: Any) -> str:
    normalized = _required(str(value or ""), "title_source").lower()
    if normalized not in TITLE_SOURCES:
        raise ValueError(f"title_source must be one of {sorted(TITLE_SOURCES)}")
    return normalized


def _validated_document_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not _DOCUMENT_KIND_PATTERN.fullmatch(normalized):
        raise ValueError("document_kind must use lowercase letters, digits, and underscores")
    if normalized not in REVIEW_DOCUMENT_KINDS:
        raise ValueError(f"document_kind must be one of {sorted(REVIEW_DOCUMENT_KINDS)}")
    return normalized


def _validated_import_stage(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in IMPORT_JOB_STAGES:
        raise ValueError(f"stage must be one of {list(IMPORT_JOB_STAGES)}")
    return normalized


def _validated_deletion_scope(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in DATA_DELETION_SCOPES:
        raise ValueError(f"deletion_scope must be one of {sorted(DATA_DELETION_SCOPES)}")
    return normalized


def _validated_deletion_trigger(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in DATA_DELETION_TRIGGERS:
        raise ValueError(f"requested_by must be one of {sorted(DATA_DELETION_TRIGGERS)}")
    return normalized


def _validated_retention_policy(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in RETENTION_POLICIES:
        raise ValueError(f"retention_policy must be one of {sorted(RETENTION_POLICIES)}")
    return normalized


def _validated_managed_paths(
    *,
    meeting_id: str,
    deletion_scope: str,
    managed_paths: list[str],
) -> list[str]:
    if not _MANAGED_MEETING_ID_PATTERN.fullmatch(meeting_id):
        raise ValueError("meeting_id is unsafe for managed deletion paths")
    normalized_paths: list[str] = []
    for value in managed_paths:
        path = _required(value, "managed_path")
        if "\\" in path:
            raise ValueError("managed_path must use POSIX separators")
        raw = PurePosixPath(path)
        parts = raw.parts
        is_audio_root = parts == ("audio_assets", meeting_id)
        is_import_root = parts == ("imports", meeting_id)
        is_preparation = parts == ("meeting_preparation", f"{meeting_id}.json")
        if (
            raw.is_absolute()
            or "." in parts
            or ".." in parts
            or not (is_audio_root or is_import_root or is_preparation)
        ):
            raise ValueError("managed_path is outside the meeting-owned data roots")
        normalized_paths.append(raw.as_posix())
    normalized_paths = sorted(set(normalized_paths))
    if deletion_scope in {"derived", "transcript"} and normalized_paths:
        raise ValueError(f"{deletion_scope} deletion must not include managed file paths")
    if deletion_scope in {"recording", "all"}:
        unexpected = [
            path
            for path in normalized_paths
            if path
            not in {
                f"audio_assets/{meeting_id}",
                f"imports/{meeting_id}",
                f"meeting_preparation/{meeting_id}.json",
            }
        ]
        if unexpected:
            raise ValueError("recording deletion contains an unsupported managed path")
    return normalized_paths


def _public_job_error_class(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized if normalized in PUBLIC_JOB_ERROR_CLASSES else "job_failed"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _positive_int_or_error(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise IntelligenceProjectionError(f"{field} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise IntelligenceProjectionError(f"{field} must be a positive integer") from exc
    if normalized <= 0:
        raise IntelligenceProjectionError(f"{field} must be a positive integer")
    return normalized


def _compact_evidence(value: Any) -> str:
    return "".join(str(value or "").split())


def _entity_content_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    normalized = "".join(normalized.split())
    return re.sub(r"[。！？!?；;，,、]+$", "", normalized)


def _merge_semantic_checkpoint_text(existing: str, incoming: str) -> str:
    left = str(existing or "").strip()
    right = str(incoming or "").strip()
    if not left:
        return right
    if not right:
        return left
    maximum_overlap = min(len(left), len(right), 80)
    for size in range(maximum_overlap, 1, -1):
        if left[-size:] == right[:size]:
            return f"{left}{right[size:]}"
    separator = " " if left[-1].isascii() and right[0].isascii() and left[-1].isalnum() and right[0].isalnum() else ""
    return f"{left}{separator}{right}"


def _bounded_confidence(value: Any) -> float:
    if isinstance(value, bool):
        raise IntelligenceProjectionError("confidence must be between 0 and 1")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise IntelligenceProjectionError("confidence must be between 0 and 1") from exc
    if not 0 <= normalized <= 1:
        raise IntelligenceProjectionError("confidence must be between 0 and 1")
    return normalized


def _review_content_json(value: Any) -> str:
    try:
        serialized = _json_dump(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("document content must be JSON serializable") from exc
    if len(serialized.encode("utf-8")) > MAX_REVIEW_DOCUMENT_BYTES:
        raise ValueError(f"document content must not exceed {MAX_REVIEW_DOCUMENT_BYTES} bytes")
    return serialized


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


class TranscriptRevisionIdentityConflict(RuntimeError):
    """One revision id was reused for a different target, version, or result."""

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


class IntelligenceProjectionError(RuntimeError):
    """A structured intelligence result failed the durable evidence barrier."""

    retryable = False


class ReviewDocumentConflict(RuntimeError):
    def __init__(self, *, expected_revision: int, current_document: Mapping[str, Any] | None) -> None:
        current_revision = int(current_document["revision"]) if current_document is not None else 0
        super().__init__(f"review document revision conflict: expected {expected_revision}, current {current_revision}")
        self.expected_revision = int(expected_revision)
        self.current_revision = current_revision
        self.current_document = dict(current_document) if current_document is not None else None


class SpeakerLabelConflict(ValueError):
    """A manual speaker label is already assigned within the same meeting."""


class SpeakerAttributionConflict(ValueError):
    """A speaker attribution identity was replayed with different content."""


class SpeakerAttributionRevisionConflict(ValueError):
    """A speaker attribution arrived after a newer segment projection exists."""


class V2Persistence:
    """Additive normalized persistence and durable outbox/job storage.

    Normalized rows are the source of truth. ``meeting_events`` is an outbox
    used to notify consumers after the owning transaction commits; it is not a
    second event-sourced copy of meeting state.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        semantic_projection_mode: str = "legacy",
    ) -> None:
        normalized_semantic_projection_mode = str(semantic_projection_mode or "").strip().lower()
        if normalized_semantic_projection_mode not in {"legacy", "llm_first"}:
            raise ValueError("semantic_projection_mode must be legacy or llm_first")
        self.semantic_projection_mode = normalized_semantic_projection_mode
        self.database_path = Path(database_path).expanduser().resolve()
        ensure_private_directory(self.database_path.parent)
        self._lock = RLock()
        self._closed = False
        bootstrap_application_schema(self.database_path)
        harden_private_file(self.database_path)
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
            harden_sqlite_files(self.database_path)
        except BaseException:
            self._conn.close()
            self._closed = True
            raise

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    @contextmanager
    def _write_transaction(self, *, next006_scope: str = "sqlite_transaction") -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise RuntimeError("V2Persistence is closed")
            storage_write_failpoint.maybe_raise(next006_scope)
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
                "SELECT MAX("
                "COALESCE((SELECT latest_seq FROM meetings WHERE id = ?), 0), "
                "COALESCE(MAX(seq), 0)"
                ") + 1 FROM meeting_events WHERE meeting_id = ?",
                (meeting_id, meeting_id),
            ).fetchone()[0]
        )

    def _append_governance_audit_locked(
        self,
        *,
        event_type: str,
        requested_by: str,
        occurred_at_ms: int,
        idempotency_key: str,
        payload: Mapping[str, Any],
        meeting_id: str | None = None,
        deletion_job_id: str | None = None,
        deletion_scope: str | None = None,
        retention_policy: str | None = None,
    ) -> None:
        audit_id = _stable_id("audit", idempotency_key)
        self._conn.execute(
            "INSERT OR IGNORE INTO data_governance_audit_events ("
            "id, event_type, meeting_id, deletion_job_id, deletion_scope, requested_by, "
            "retention_policy, occurred_at_ms, idempotency_key, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                _required(event_type, "event_type"),
                meeting_id,
                deletion_job_id,
                deletion_scope,
                requested_by,
                retention_policy,
                max(0, int(occurred_at_ms)),
                _required(idempotency_key, "idempotency_key"),
                _json_dump(dict(payload)),
            ),
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
        decisions: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        risks: list[dict[str, Any]] = []
        for row in rows:
            entity = self._entity_dict(row)
            if row["kind"] == "current_topic":
                topic = entity
            elif row["kind"] == "open_question":
                questions.append(entity)
            elif row["kind"] == "decision_candidate":
                decisions.append(entity)
            elif row["kind"] == "action_item":
                actions.append(entity)
            elif row["kind"] == "risk":
                risks.append(entity)
        return {
            "current_topic": topic,
            "open_questions": questions[-3:],
            "decision_candidates": decisions,
            "action_items": actions,
            "risks": risks,
        }

    def _update_meeting_state_locked(
        self,
        *,
        meeting_id: str,
        segments: list[Mapping[str, Any]],
        now_ms: int,
        causation_id: str,
    ) -> None:
        if self.semantic_projection_mode == "llm_first":
            # Raw ASR is the source of truth. Formal meeting semantics are
            # committed only through apply_intelligence_response(), after the
            # structured LLM evidence barrier has accepted the response.
            return
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

        for kind, facts, event_type in (
            ("decision_candidate", projected.get("decision_candidates") or [], "meeting.decision.updated"),
            ("action_item", projected.get("action_items") or [], "meeting.action_item.updated"),
            ("risk", projected.get("risks") or [], "meeting.risk.updated"),
        ):
            for fact in facts:
                if not isinstance(fact, Mapping):
                    continue
                stored_fact = self._upsert_entity_locked(
                    meeting_id,
                    kind,
                    fact,
                    current_transcript_seq=current_transcript_seq,
                )
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type=event_type,
                    aggregate_type="meeting_entity",
                    aggregate_id=str(fact["id"]),
                    occurred_at_ms=now_ms,
                    idempotency_key=f"meeting.{kind}:{causation_id}:{fact['id']}",
                    payload={"entity": stored_fact, "kind": kind},
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
        confidence = float(entity.get("confidence") or 0.0)
        evidence = entity.get("evidence") if isinstance(entity.get("evidence"), Mapping) else {}
        evidence_items = entity.get("evidence_items")
        if not isinstance(evidence_items, list):
            evidence_items = [evidence] if evidence else []
        updated_at_ms = entity.get("updated_at_ms")
        existing = self._conn.execute(
            "SELECT * FROM meeting_entities WHERE meeting_id = ? AND entity_id = ?",
            (meeting_id, entity_id),
        ).fetchone()
        evidence_segment_ids = list(entity.get("evidence_segment_ids") or [])
        if existing is not None:
            evidence_segment_ids = list(
                dict.fromkeys(
                    [
                        *json.loads(existing["evidence_segment_ids_json"]),
                        *evidence_segment_ids,
                    ]
                )
            )
            old_evidence = json.loads(existing["evidence_json"] or "{}")
            old_evidence_items = [old_evidence] if old_evidence else []
            if isinstance(evidence_items, list):
                evidence_items = [*old_evidence_items, *evidence_items]
            if existing["status"] in {"confirmed", "dismissed"}:
                status = str(existing["status"])
        evidence_items = list(dict.fromkeys(_json_dump(item) for item in evidence_items))
        evidence_items_value = [json.loads(item) for item in evidence_items]
        if evidence_items_value:
            evidence = evidence_items_value[-1]
        evidence_json = _json_dump(evidence_segment_ids)
        structured_evidence_json = _json_dump(evidence)
        owner = entity.get("owner")
        deadline = entity.get("deadline")
        mitigation = entity.get("mitigation")
        if existing is not None:
            owner = owner if owner is not None else existing["owner"]
            deadline = deadline if deadline is not None else existing["deadline"]
            mitigation = mitigation if mitigation is not None else existing["mitigation"]
        changed = existing is None or any(
            existing[field] != value
            for field, value in (
                ("kind", kind),
                ("status", status),
                ("text", text),
                ("confidence", confidence),
                ("evidence_json", structured_evidence_json),
                ("evidence_segment_ids_json", evidence_json),
                ("owner", owner),
                ("deadline", deadline),
                ("mitigation", mitigation),
                ("updated_at_ms", updated_at_ms),
            )
        )
        version = 1 if existing is None else int(existing["version"]) + int(changed)
        first_seen_seq = current_transcript_seq if existing is None else int(existing["first_seen_seq"])
        last_updated_seq = current_transcript_seq if existing is None or changed else int(existing["last_updated_seq"])
        self._conn.execute(
            "INSERT INTO meeting_entities ("
            "meeting_id, entity_id, kind, status, text, confidence, evidence_json, "
            "evidence_segment_ids_json, owner, deadline, mitigation, updated_at_ms, "
            "version, first_seen_seq, last_updated_seq"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(meeting_id, entity_id) DO UPDATE SET "
            "kind = excluded.kind, status = excluded.status, text = excluded.text, "
            "confidence = excluded.confidence, evidence_json = excluded.evidence_json, "
            "evidence_segment_ids_json = excluded.evidence_segment_ids_json, "
            "owner = excluded.owner, deadline = excluded.deadline, mitigation = excluded.mitigation, "
            "updated_at_ms = excluded.updated_at_ms, version = excluded.version, "
            "first_seen_seq = excluded.first_seen_seq, last_updated_seq = excluded.last_updated_seq",
            (
                meeting_id,
                entity_id,
                kind,
                status,
                text,
                confidence,
                structured_evidence_json,
                evidence_json,
                owner,
                deadline,
                mitigation,
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

    def set_entity_status(
        self,
        *,
        meeting_id: str,
        entity_id: str,
        status: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a human confirmation or dismissal without changing evidence."""

        meeting_id = _required(meeting_id, "meeting_id")
        entity_id = _required(entity_id, "entity_id")
        status = _required(status, "status")
        if status not in {"candidate", "confirmed", "dismissed"}:
            raise ValueError("entity status must be candidate, confirmed, or dismissed")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            row = self._conn.execute(
                "SELECT * FROM meeting_entities WHERE meeting_id = ? AND entity_id = ?",
                (meeting_id, entity_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"meeting entity not found: {entity_id}")
            if str(row["status"]) == status:
                return self._entity_dict(row)
            self._conn.execute(
                "UPDATE meeting_entities SET status = ?, version = version + 1, updated_at_ms = ? "
                "WHERE meeting_id = ? AND entity_id = ?",
                (status, now_ms, meeting_id, entity_id),
            )
            updated = self._conn.execute(
                "SELECT * FROM meeting_entities WHERE meeting_id = ? AND entity_id = ?",
                (meeting_id, entity_id),
            ).fetchone()
            event_type = {
                "current_topic": "meeting.topic.updated",
                "open_question": "meeting.open_question.updated",
                "decision_candidate": "meeting.decision.updated",
                "action_item": "meeting.action_item.updated",
                "risk": "meeting.risk.updated",
            }[str(updated["kind"])]
            entity = self._entity_dict(updated)
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type=event_type,
                aggregate_type="meeting_entity",
                aggregate_id=entity_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.entity.status:{entity_id}:{int(updated['version'])}",
                payload={"entity": entity, "status": status},
                correlation_id=correlation_id or meeting_id,
                causation_id=entity_id,
            )
            return entity

    def confirm_entity(
        self,
        *,
        meeting_id: str,
        entity_id: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.set_entity_status(
            meeting_id=meeting_id,
            entity_id=entity_id,
            status="confirmed",
            now_ms=now_ms,
            correlation_id=correlation_id,
        )

    def dismiss_entity(
        self,
        *,
        meeting_id: str,
        entity_id: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.set_entity_status(
            meeting_id=meeting_id,
            entity_id=entity_id,
            status="dismissed",
            now_ms=now_ms,
            correlation_id=correlation_id,
        )

    def ignore_entity(
        self,
        *,
        meeting_id: str,
        entity_id: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.dismiss_entity(
            meeting_id=meeting_id,
            entity_id=entity_id,
            now_ms=now_ms,
            correlation_id=correlation_id,
        )

    def confirm_meeting_entity(
        self,
        *,
        meeting_id: str,
        entity_id: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.confirm_entity(
            meeting_id=meeting_id,
            entity_id=entity_id,
            now_ms=now_ms,
            correlation_id=correlation_id,
        )

    def dismiss_meeting_entity(
        self,
        *,
        meeting_id: str,
        entity_id: str,
        now_ms: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.dismiss_entity(
            meeting_id=meeting_id,
            entity_id=entity_id,
            now_ms=now_ms,
            correlation_id=correlation_id,
        )

    def create_meeting(
        self,
        *,
        meeting_id: str,
        title: str | None,
        now_ms: int,
        title_source: str | None = None,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        now_ms = max(0, int(now_ms))
        normalized_title = _validated_title(title) if str(title or "").strip() else fallback_meeting_title(now_ms)
        normalized_source = _validated_title_source(
            title_source or ("user" if str(title or "").strip() else "fallback")
        )
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            result = self._conn.execute(
                "INSERT OR IGNORE INTO meetings ("
                "id, state, title, title_source, started_at_ms, latest_seq, revision, "
                "created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, ?, ?, 0, 1, ?, ?)",
                (meeting_id, normalized_title, normalized_source, now_ms, now_ms, now_ms),
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
                        "title_source": normalized_source,
                        "started_at_ms": now_ms,
                    },
                    correlation_id=meeting_id,
                )
            row = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
            return self._meeting_dict(row)

    def update_meeting_title(
        self,
        *,
        meeting_id: str,
        title: str,
        title_source: str,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        normalized_title = _validated_title(title)
        normalized_source = _validated_title_source(title_source)
        now_ms = max(0, int(now_ms))
        with self._write_transaction(next006_scope="meeting_title_transaction"):
            row = self._conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if row is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            if normalized_source == "ai" and row["title_source"] == "user":
                return {**self._meeting_dict(row), "updated": False}
            if row["title"] == normalized_title and row["title_source"] == normalized_source:
                return {**self._meeting_dict(row), "updated": False}
            self._conn.execute(
                "UPDATE meetings SET title = ?, title_source = ?, updated_at_ms = ? WHERE id = ?",
                (normalized_title, normalized_source, now_ms, meeting_id),
            )
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.title.updated",
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.title:{int(row['revision']) + 1}:{hashlib.sha256(normalized_title.encode()).hexdigest()[:12]}",
                payload={"title": normalized_title, "title_source": normalized_source},
                correlation_id=meeting_id,
            )
            updated = self._conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return {**self._meeting_dict(updated), "updated": True}

    def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"meeting not found: {meeting_id}")
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

    def _ensure_meeting_speaker_locked(
        self,
        *,
        meeting_id: str,
        speaker_id: str,
        now_ms: int,
    ) -> sqlite3.Row:
        existing = self._conn.execute(
            "SELECT * FROM meeting_speakers WHERE meeting_id = ? AND speaker_id = ?",
            (meeting_id, speaker_id),
        ).fetchone()
        if existing is not None:
            return existing
        ordinal = int(
            self._conn.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM meeting_speakers WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()[0]
        )
        self._conn.execute(
            "INSERT INTO meeting_speakers ("
            "meeting_id, speaker_id, speaker_label, label_source, label_locked, ordinal, "
            "created_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, 'auto', 0, ?, ?, ?)",
            (meeting_id, speaker_id, f"Speaker {ordinal}", ordinal, now_ms, now_ms),
        )
        row = self._conn.execute(
            "SELECT * FROM meeting_speakers WHERE meeting_id = ? AND speaker_id = ?",
            (meeting_id, speaker_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("speaker mapping insert did not produce a durable row")
        return row

    def create_or_update_speaker_run(
        self,
        *,
        meeting_id: str,
        run_id: str,
        source: str,
        model: str,
        status: str = "running",
        metadata: Mapping[str, Any] | None = None,
        now_ms: int,
        completed_at_ms: int | None = None,
    ) -> dict[str, Any]:
        """Create one diarization run or update its lifecycle state idempotently."""

        meeting_id = _required(meeting_id, "meeting_id")
        run_id = _required(run_id, "run_id")
        source = _required(source, "source")
        model = _required(model, "model")
        status = str(status or "").strip().lower()
        if status not in {"pending", "running", "completed", "failed"}:
            raise ValueError("speaker run status must be pending, running, completed, or failed")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise ValueError("speaker run metadata must be an object")
        metadata_json = _json_dump(dict(metadata or {}))
        now_ms = max(0, int(now_ms))
        normalized_completed_at_ms = (
            max(0, int(completed_at_ms)) if completed_at_ms is not None else now_ms if status == "completed" else None
        )
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            existing = self._conn.execute(
                "SELECT * FROM speaker_runs WHERE meeting_id = ? AND run_id = ?",
                (meeting_id, run_id),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO speaker_runs ("
                    "meeting_id, run_id, source, model, status, metadata_json, created_at_ms, "
                    "updated_at_ms, completed_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        run_id,
                        source,
                        model,
                        status,
                        metadata_json,
                        now_ms,
                        now_ms,
                        normalized_completed_at_ms,
                    ),
                )
            else:
                identity = {
                    "source": source,
                    "model": model,
                    "metadata_json": metadata_json,
                }
                conflicts = {
                    key: (existing[key], value)
                    for key, value in identity.items()
                    if existing[key] != value
                }
                if conflicts:
                    raise ValueError(f"speaker run was retried with conflicting fields: {conflicts}")
                if existing["status"] != status or existing["completed_at_ms"] != normalized_completed_at_ms:
                    self._conn.execute(
                        "UPDATE speaker_runs SET status = ?, completed_at_ms = ?, "
                        "updated_at_ms = MAX(updated_at_ms, ?) WHERE meeting_id = ? AND run_id = ?",
                        (status, normalized_completed_at_ms, now_ms, meeting_id, run_id),
                    )
            row = self._conn.execute(
                "SELECT * FROM speaker_runs WHERE meeting_id = ? AND run_id = ?",
                (meeting_id, run_id),
            ).fetchone()
        return self._speaker_run_dict(row)

    def upsert_speaker_run(self, **kwargs: Any) -> dict[str, Any]:
        """Compatibility spelling for callers that use upsert terminology."""

        return self.create_or_update_speaker_run(**kwargs)

    def append_speaker_turn(
        self,
        *,
        meeting_id: str,
        run_id: str,
        turn_id: str,
        start_ms: int,
        end_ms: int,
        cluster_label: str | None = None,
        speaker_id: str | None = None,
        confidence: float | None = None,
        is_stable: bool = True,
        window_ids: list[str] | tuple[str, ...] | None = None,
        now_ms: int,
    ) -> dict[str, Any]:
        """Append one immutable diarization turn, accepting exact replays."""

        meeting_id = _required(meeting_id, "meeting_id")
        run_id = _required(run_id, "run_id")
        turn_id = _required(turn_id, "turn_id")
        start_ms = max(0, int(start_ms))
        end_ms = int(end_ms)
        if end_ms <= start_ms:
            raise ValueError("speaker turn end_ms must be greater than start_ms")
        if not isinstance(is_stable, bool):
            raise ValueError("speaker turn is_stable must be a boolean")
        normalized_cluster_label = (
            " ".join(str(cluster_label).split()) if cluster_label is not None else None
        )
        if normalized_cluster_label == "":
            normalized_cluster_label = None
        normalized_speaker_id = _validated_speaker_id(speaker_id)
        normalized_confidence = _validated_speaker_confidence(
            confidence,
            speaker_id=normalized_speaker_id,
        )
        normalized_window_ids = [
            _required(window_id, "window_id") for window_id in tuple(window_ids or ())
        ]
        window_ids_json = _json_dump(normalized_window_ids)
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute(
                "SELECT 1 FROM speaker_runs WHERE meeting_id = ? AND run_id = ?",
                (meeting_id, run_id),
            ).fetchone() is None:
                raise KeyError(f"speaker run not found: {meeting_id}/{run_id}")
            if normalized_speaker_id is not None:
                self._ensure_meeting_speaker_locked(
                    meeting_id=meeting_id,
                    speaker_id=normalized_speaker_id,
                    now_ms=now_ms,
                )
            existing = self._conn.execute(
                "SELECT * FROM speaker_turns WHERE meeting_id = ? AND run_id = ? AND turn_id = ?",
                (meeting_id, run_id, turn_id),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO speaker_turns ("
                    "meeting_id, run_id, turn_id, start_ms, end_ms, cluster_label, speaker_id, "
                    "confidence, is_stable, window_ids_json, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        run_id,
                        turn_id,
                        start_ms,
                        end_ms,
                        normalized_cluster_label,
                        normalized_speaker_id,
                        normalized_confidence,
                        int(is_stable),
                        window_ids_json,
                        now_ms,
                        now_ms,
                    ),
                )
            else:
                identity = {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "cluster_label": normalized_cluster_label,
                    "speaker_id": normalized_speaker_id,
                    "confidence": normalized_confidence,
                    "is_stable": int(is_stable),
                    "window_ids_json": window_ids_json,
                }
                conflicts = {
                    key: (existing[key], value)
                    for key, value in identity.items()
                    if existing[key] != value
                }
                if conflicts:
                    raise ValueError(f"speaker turn was retried with conflicting fields: {conflicts}")
            row = self._conn.execute(
                "SELECT * FROM speaker_turns WHERE meeting_id = ? AND run_id = ? AND turn_id = ?",
                (meeting_id, run_id, turn_id),
            ).fetchone()
        return self._speaker_turn_dict(row)

    def append_turn(self, **kwargs: Any) -> dict[str, Any]:
        """Compatibility spelling for callers that use the shorter turn name."""

        return self.append_speaker_turn(**kwargs)

    def apply_segment_speaker_attribution(
        self,
        *,
        meeting_id: str,
        run_id: str,
        segment_id: str,
        attribution_revision: int,
        speaker_id: str | None,
        confidence: float | None,
        source: str,
        reason: str,
        now_ms: int,
    ) -> dict[str, Any]:
        """Persist an attribution and advance only the segment speaker projection."""

        meeting_id = _required(meeting_id, "meeting_id")
        run_id = _required(run_id, "run_id")
        segment_id = _required(segment_id, "segment_id")
        if isinstance(attribution_revision, bool):
            raise SpeakerAttributionRevisionConflict("speaker attribution is stale: invalid revision")
        try:
            attribution_revision = int(attribution_revision)
        except (TypeError, ValueError) as exc:
            raise SpeakerAttributionRevisionConflict("speaker attribution is stale: invalid revision") from exc
        if attribution_revision <= 0:
            raise SpeakerAttributionRevisionConflict(
                f"speaker attribution is stale: {attribution_revision}"
            )
        normalized_speaker_id = _validated_speaker_id(speaker_id)
        normalized_confidence = _validated_speaker_confidence(
            confidence,
            speaker_id=normalized_speaker_id,
        )
        source = _required(source, "source")
        reason = _required(reason, "reason")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute(
                "SELECT 1 FROM speaker_runs WHERE meeting_id = ? AND run_id = ?",
                (meeting_id, run_id),
            ).fetchone() is None:
                raise KeyError(f"speaker run not found: {meeting_id}/{run_id}")
            segment = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                (meeting_id, segment_id),
            ).fetchone()
            if segment is None:
                raise KeyError(f"transcript segment not found: {meeting_id}/{segment_id}")
            current_revision = int(segment["speaker_attribution_revision"] or 0)
            if attribution_revision < current_revision:
                raise SpeakerAttributionRevisionConflict(
                    f"speaker attribution is stale: {attribution_revision} < {current_revision}"
                )
            if normalized_speaker_id is not None:
                speaker_row = self._ensure_meeting_speaker_locked(
                    meeting_id=meeting_id,
                    speaker_id=normalized_speaker_id,
                    now_ms=now_ms,
                )
                speaker_label = str(speaker_row["speaker_label"])
            else:
                speaker_label = None
            existing = self._conn.execute(
                "SELECT * FROM speaker_attributions WHERE meeting_id = ? AND segment_id = ? "
                "AND attribution_revision = ?",
                (meeting_id, segment_id, attribution_revision),
            ).fetchone()
            identity = {
                "run_id": run_id,
                "speaker_id": normalized_speaker_id,
                "confidence": normalized_confidence,
                "source": source,
                "reason": reason,
            }
            if existing is not None:
                conflicts = {
                    key: (existing[key], value)
                    for key, value in identity.items()
                    if existing[key] != value
                }
                if conflicts:
                    raise SpeakerAttributionConflict(
                        f"speaker attribution was replayed with conflicting fields: {conflicts}"
                    )
                if attribution_revision == current_revision:
                    return self._speaker_attribution_dict(existing)
            elif attribution_revision == current_revision:
                raise SpeakerAttributionConflict(
                    "current speaker attribution is missing its durable history row"
                )
            if existing is None:
                self._conn.execute(
                    "INSERT INTO speaker_attributions ("
                    "meeting_id, segment_id, attribution_revision, run_id, speaker_id, confidence, "
                    "source, reason, created_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        segment_id,
                        attribution_revision,
                        run_id,
                        normalized_speaker_id,
                        normalized_confidence,
                        source,
                        reason,
                        now_ms,
                    ),
                )
            self._conn.execute(
                "UPDATE transcript_segments SET speaker_id = ?, speaker_label = ?, speaker_confidence = ?, "
                "speaker_attribution_revision = ?, speaker_attribution_source = ?, "
                "speaker_attribution_reason = ?, updated_at_ms = MAX(updated_at_ms, ?) "
                "WHERE meeting_id = ? AND segment_id = ? AND speaker_attribution_revision < ?",
                (
                    normalized_speaker_id,
                    speaker_label,
                    normalized_confidence,
                    attribution_revision,
                    source,
                    reason,
                    now_ms,
                    meeting_id,
                    segment_id,
                    attribution_revision,
                ),
            )
            event_idempotency_key = f"transcript.segment.speaker_revised:{segment_id}:{attribution_revision}"
            event_row = self._conn.execute(
                "SELECT 1 FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, event_idempotency_key),
            ).fetchone()
            if event_row is None:
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="transcript.segment.speaker_revised",
                    aggregate_type="transcript_segment",
                    aggregate_id=segment_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=event_idempotency_key,
                    payload={
                        "meeting_id": meeting_id,
                        "segment_id": segment_id,
                        "attribution_revision": attribution_revision,
                        "run_id": run_id,
                        "speaker_id": normalized_speaker_id,
                        "speaker_label": speaker_label,
                        "speaker_confidence": normalized_confidence,
                        "source": source,
                        "reason": reason,
                    },
                    correlation_id=run_id,
                )
            stored = self._conn.execute(
                "SELECT * FROM speaker_attributions WHERE meeting_id = ? AND segment_id = ? "
                "AND attribution_revision = ?",
                (meeting_id, segment_id, attribution_revision),
            ).fetchone()
        return self._speaker_attribution_dict(stored)

    def list_meeting_speakers(self, meeting_id: str) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            if (
                self._conn.execute(
                    "SELECT 1 FROM meetings WHERE id = ?",
                    (meeting_id,),
                ).fetchone()
                is None
            ):
                raise KeyError(f"meeting not found: {meeting_id}")
            rows = self._conn.execute(
                "SELECT * FROM meeting_speakers WHERE meeting_id = ? ORDER BY ordinal",
                (meeting_id,),
            ).fetchall()
        return {
            "meeting_id": meeting_id,
            "speakers": [self._speaker_dict(row) for row in rows],
        }

    def rename_meeting_speaker(
        self,
        *,
        meeting_id: str,
        speaker_id: str,
        speaker_label: str,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        normalized_speaker_id = _validated_speaker_id(speaker_id)
        if normalized_speaker_id is None:
            raise ValueError("speaker_id must not be empty")
        normalized_label = _validated_speaker_label(speaker_label)
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM meeting_speakers WHERE meeting_id = ? AND speaker_id = ?",
                (meeting_id, normalized_speaker_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"speaker not found: {meeting_id}/{normalized_speaker_id}")
            label_owner = self._conn.execute(
                "SELECT speaker_id FROM meeting_speakers WHERE meeting_id = ? "
                "AND speaker_label = ? AND speaker_id != ?",
                (meeting_id, normalized_label, normalized_speaker_id),
            ).fetchone()
            if label_owner is not None:
                raise SpeakerLabelConflict("speaker_label is already used by another speaker in this meeting")
            self._conn.execute(
                "UPDATE meeting_speakers SET speaker_label = ?, label_source = 'user', label_locked = 1, "
                "updated_at_ms = MAX(updated_at_ms, ?) "
                "WHERE meeting_id = ? AND speaker_id = ?",
                (normalized_label, now_ms, meeting_id, normalized_speaker_id),
            )
            segment_result = self._conn.execute(
                "UPDATE transcript_segments SET speaker_label = ?, updated_at_ms = MAX(updated_at_ms, ?) "
                "WHERE meeting_id = ? AND speaker_id = ?",
                (normalized_label, now_ms, meeting_id, normalized_speaker_id),
            )
            paragraph_result = self._conn.execute(
                "UPDATE semantic_paragraphs SET speaker_label = ?, updated_at_ms = MAX(updated_at_ms, ?) "
                "WHERE meeting_id = ? AND speaker_id = ?",
                (normalized_label, now_ms, meeting_id, normalized_speaker_id),
            )
            updated = self._conn.execute(
                "SELECT * FROM meeting_speakers WHERE meeting_id = ? AND speaker_id = ?",
                (meeting_id, normalized_speaker_id),
            ).fetchone()
        result = self._speaker_dict(updated)
        result["backfilled_segment_count"] = int(segment_result.rowcount)
        result["backfilled_paragraph_count"] = int(paragraph_result.rowcount)
        return result

    def _rebuild_semantic_paragraph_locked(
        self,
        *,
        meeting_id: str,
        paragraph_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        paragraph = self._conn.execute(
            "SELECT * FROM semantic_paragraphs WHERE meeting_id = ? AND paragraph_id = ?",
            (meeting_id, paragraph_id),
        ).fetchone()
        if paragraph is None:
            raise KeyError(f"semantic paragraph not found: {meeting_id}/{paragraph_id}")
        checkpoints = self._conn.execute(
            "SELECT c.*, segment.speaker_id AS speaker_id, "
            "segment.speaker_label AS speaker_label, "
            "segment.speaker_confidence AS speaker_confidence FROM asr_checkpoints c "
            "JOIN semantic_paragraph_checkpoints pc ON pc.meeting_id = c.meeting_id "
            "AND pc.checkpoint_id = c.checkpoint_id "
            "JOIN transcript_segments segment ON segment.meeting_id = c.meeting_id "
            "AND segment.segment_id = c.checkpoint_id "
            "WHERE pc.meeting_id = ? AND pc.paragraph_id = ? "
            "ORDER BY pc.ordinal",
            (meeting_id, paragraph_id),
        ).fetchall()
        if not checkpoints:
            raise RuntimeError(f"semantic paragraph has no checkpoints: {paragraph_id}")
        text = ""
        for checkpoint in checkpoints:
            text = _merge_semantic_checkpoint_text(text, checkpoint["normalized_text"] or checkpoint["text"])
        end_ms = max(int(checkpoint["ended_at_ms"] or checkpoint["started_at_ms"] or 0) for checkpoint in checkpoints)
        first_speaker_id = checkpoints[0]["speaker_id"]
        has_one_speaker = all(checkpoint["speaker_id"] == first_speaker_id for checkpoint in checkpoints)
        speaker_id = first_speaker_id if has_one_speaker else None
        speaker_label = checkpoints[0]["speaker_label"] if speaker_id is not None else None
        confidence_values = [checkpoint["speaker_confidence"] for checkpoint in checkpoints]
        speaker_confidence = (
            min(float(value) for value in confidence_values)
            if speaker_id is not None and all(value is not None for value in confidence_values)
            else None
        )
        changed = (
            paragraph["text"] != text
            or paragraph["end_ms"] != end_ms
            or paragraph["speaker_id"] != speaker_id
            or paragraph["speaker_label"] != speaker_label
            or paragraph["speaker_confidence"] != speaker_confidence
        )
        self._conn.execute(
            "UPDATE semantic_paragraphs SET text = ?, end_ms = ?, speaker_id = ?, "
            "speaker_label = ?, speaker_confidence = ?, revision = revision + ?, "
            "updated_at_ms = ? WHERE meeting_id = ? AND paragraph_id = ?",
            (
                text,
                end_ms,
                speaker_id,
                speaker_label,
                speaker_confidence,
                int(changed),
                now_ms,
                meeting_id,
                paragraph_id,
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM semantic_paragraphs WHERE meeting_id = ? AND paragraph_id = ?",
            (meeting_id, paragraph_id),
        ).fetchone()
        return self._semantic_paragraph_dict(row, checkpoint_ids=[str(item["checkpoint_id"]) for item in checkpoints])

    def _project_semantic_paragraph_locked(
        self,
        *,
        meeting_id: str,
        checkpoint: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        checkpoint_id = _required(str(checkpoint.get("segment_id") or ""), "checkpoint.segment_id")
        final_id = _required(str(checkpoint.get("final_id") or ""), "checkpoint.final_id")
        transcript_seq = _positive_int_or_error(checkpoint.get("transcript_seq"), "checkpoint.transcript_seq")
        text = _required(str(checkpoint.get("text") or ""), "checkpoint.text")
        normalized_text = _required(
            str(checkpoint.get("normalized_text") or text),
            "checkpoint.normalized_text",
        )
        started_at_ms = checkpoint.get("started_at_ms")
        ended_at_ms = checkpoint.get("ended_at_ms")
        speaker_id = checkpoint.get("speaker_id")
        speaker_label = checkpoint.get("speaker_label")
        speaker_confidence = checkpoint.get("speaker_confidence")
        self._conn.execute(
            "INSERT INTO asr_checkpoints ("
            "meeting_id, checkpoint_id, final_id, transcript_seq, text, normalized_text, "
            "started_at_ms, ended_at_ms, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(meeting_id, checkpoint_id) DO UPDATE SET "
            "text = excluded.text, normalized_text = excluded.normalized_text, "
            "started_at_ms = excluded.started_at_ms, ended_at_ms = excluded.ended_at_ms, "
            "updated_at_ms = excluded.updated_at_ms",
            (
                meeting_id,
                checkpoint_id,
                final_id,
                transcript_seq,
                text,
                normalized_text,
                started_at_ms,
                ended_at_ms,
                now_ms,
                now_ms,
            ),
        )
        existing_mapping = self._conn.execute(
            "SELECT paragraph_id FROM semantic_paragraph_checkpoints WHERE meeting_id = ? AND checkpoint_id = ?",
            (meeting_id, checkpoint_id),
        ).fetchone()
        if existing_mapping is not None:
            return self._rebuild_semantic_paragraph_locked(
                meeting_id=meeting_id,
                paragraph_id=str(existing_mapping["paragraph_id"]),
                now_ms=now_ms,
            )

        active = self._conn.execute(
            "SELECT * FROM semantic_paragraphs WHERE meeting_id = ? AND status = 'active' "
            "ORDER BY start_ms DESC, paragraph_id DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
        should_stabilize = False
        if active is not None:
            last_checkpoint = self._conn.execute(
                "SELECT c.* FROM asr_checkpoints c "
                "JOIN semantic_paragraph_checkpoints pc ON pc.meeting_id = c.meeting_id "
                "AND pc.checkpoint_id = c.checkpoint_id "
                "WHERE pc.meeting_id = ? AND pc.paragraph_id = ? "
                "ORDER BY pc.ordinal DESC LIMIT 1",
                (meeting_id, active["paragraph_id"]),
            ).fetchone()
            gap_ms = (
                int(started_at_ms) - int(last_checkpoint["ended_at_ms"])
                if last_checkpoint is not None
                and started_at_ms is not None
                and last_checkpoint["ended_at_ms"] is not None
                else 0
            )
            duration_ms = int(ended_at_ms or started_at_ms or 0) - int(active["start_ms"] or 0)
            should_stabilize = gap_ms >= 1_800 or duration_ms > 60_000 or active["speaker_id"] != speaker_id
            if should_stabilize:
                self._conn.execute(
                    "UPDATE semantic_paragraphs SET status = 'stable', updated_at_ms = ? "
                    "WHERE meeting_id = ? AND paragraph_id = ? AND status = 'active'",
                    (now_ms, meeting_id, active["paragraph_id"]),
                )

        if active is None or should_stabilize:
            paragraph_id = _stable_id("paragraph", meeting_id, checkpoint_id)
            self._conn.execute(
                "INSERT INTO semantic_paragraphs ("
                "meeting_id, paragraph_id, revision, text, start_ms, end_ms, speaker_id, "
                "speaker_label, speaker_confidence, status, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    meeting_id,
                    paragraph_id,
                    normalized_text,
                    started_at_ms,
                    ended_at_ms,
                    speaker_id,
                    speaker_label,
                    speaker_confidence,
                    now_ms,
                    now_ms,
                ),
            )
        else:
            paragraph_id = str(active["paragraph_id"])
        ordinal = int(
            self._conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM semantic_paragraph_checkpoints "
                "WHERE meeting_id = ? AND paragraph_id = ?",
                (meeting_id, paragraph_id),
            ).fetchone()[0]
        )
        self._conn.execute(
            "INSERT INTO semantic_paragraph_checkpoints (meeting_id, paragraph_id, checkpoint_id, ordinal) "
            "VALUES (?, ?, ?, ?)",
            (meeting_id, paragraph_id, checkpoint_id, ordinal),
        )
        return self._rebuild_semantic_paragraph_locked(
            meeting_id=meeting_id,
            paragraph_id=paragraph_id,
            now_ms=now_ms,
        )

    def _find_source_duplicate_locked(
        self,
        *,
        meeting_id: str,
        source_track: str | None,
        normalized_text: str,
        started_at_ms: int | None,
        ended_at_ms: int | None,
    ) -> tuple[sqlite3.Row, float] | None:
        if source_track not in DUAL_RECORDING_TRACKS:
            return None
        candidates = self._conn.execute(
            "SELECT * FROM transcript_segments WHERE meeting_id = ? "
            "AND source_track IN ('microphone', 'system_audio') AND source_track != ? "
            "AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 24",
            (meeting_id, source_track),
        ).fetchall()
        best: tuple[sqlite3.Row, float] | None = None
        for candidate in candidates:
            if not _source_duplicate_timeline_matches(
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                candidate_started_at_ms=candidate["started_at_ms"],
                candidate_ended_at_ms=candidate["ended_at_ms"],
            ):
                continue
            similarity = _source_duplicate_similarity(
                normalized_text,
                str(candidate["normalized_text"]),
            )
            if similarity < SOURCE_DUPLICATE_MIN_SIMILARITY:
                continue
            if best is None or similarity > best[1]:
                best = (candidate, similarity)
        return best

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
        speaker_id: str | None = None,
        speaker_confidence: float | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        max_attempts: int = 3,
        enqueue_jobs: bool = True,
        source_track: str | None = None,
    ) -> dict[str, Any]:
        """Commit one final and its two AI jobs as a single durable unit."""

        meeting_id = _required(meeting_id, "meeting_id")
        final_id = _required(final_id, "final_id")
        segment_id = _required(segment_id, "segment_id")
        text = _required(text, "text")
        normalized_text = _required(normalized_text, "normalized_text")
        evidence_hash = _required(evidence_hash, "evidence_hash")
        normalized_speaker_id = _validated_speaker_id(speaker_id)
        normalized_speaker_confidence = _validated_speaker_confidence(
            speaker_confidence,
            speaker_id=normalized_speaker_id,
        )
        normalized_source_track = _validated_transcript_source_track(source_track)
        now_ms = max(0, int(now_ms))
        max_attempts = int(max_attempts)
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        event_id = _stable_id("evt", meeting_id, f"transcript.final:{final_id}")
        correction_job_id = _stable_id("job", meeting_id, final_id, "correction")
        suggestion_job_id = _stable_id("job", meeting_id, final_id, "suggestion")
        generation_id = f"suggestion:{meeting_id}:{final_id}"
        intelligence_job_id: str | None = None

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            self._conn.execute(
                "INSERT INTO meetings ("
                "id, state, title, title_source, started_at_ms, latest_seq, revision, created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, 'fallback', ?, 0, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "state = CASE WHEN meetings.state = 'ended' THEN meetings.state ELSE 'live' END, "
                "updated_at_ms = excluded.updated_at_ms",
                (meeting_id, fallback_meeting_title(now_ms), started_at_ms, now_ms, now_ms),
            )
            speaker_label: str | None = None
            if normalized_speaker_id is not None:
                speaker_row = self._ensure_meeting_speaker_locked(
                    meeting_id=meeting_id,
                    speaker_id=normalized_speaker_id,
                    now_ms=now_ms,
                )
                speaker_label = str(speaker_row["speaker_label"])
            existing = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? AND final_id = ?",
                (meeting_id, final_id),
            ).fetchone()
            created = existing is None
            duplicate_match = (
                self._find_source_duplicate_locked(
                    meeting_id=meeting_id,
                    source_track=normalized_source_track,
                    normalized_text=normalized_text,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
                if existing is None
                else None
            )
            duplicate_of_segment_id = (
                str(duplicate_match[0]["segment_id"])
                if duplicate_match is not None
                else str(existing["duplicate_of_segment_id"])
                if existing is not None and existing["duplicate_of_segment_id"] is not None
                else None
            )
            source_duplicate_similarity = (
                float(duplicate_match[1])
                if duplicate_match is not None
                else float(existing["source_duplicate_similarity"])
                if existing is not None and existing["source_duplicate_similarity"] is not None
                else None
            )
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
                    "normalized_text, started_at_ms, ended_at_ms, source_track, "
                    "duplicate_of_segment_id, source_duplicate_similarity, speaker_id, speaker_label, "
                    "speaker_confidence, revision, evidence_hash, correction_status, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        segment_id,
                        final_id,
                        transcript_seq,
                        text,
                        normalized_text,
                        started_at_ms,
                        ended_at_ms,
                        normalized_source_track,
                        duplicate_of_segment_id,
                        source_duplicate_similarity,
                        normalized_speaker_id,
                        speaker_label,
                        normalized_speaker_confidence,
                        evidence_hash,
                        "no_change" if duplicate_of_segment_id is not None else "pending",
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
                    "source_track": normalized_source_track,
                    "duplicate_of_segment_id": duplicate_of_segment_id,
                    "source_duplicate_similarity": source_duplicate_similarity,
                    "speaker_id": normalized_speaker_id,
                    "speaker_label": speaker_label,
                    "speaker_confidence": normalized_speaker_confidence,
                    "revision": 1,
                    "evidence_hash": evidence_hash,
                }
                event_seq = self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type=(
                        "transcript.segment.source_duplicate"
                        if duplicate_of_segment_id is not None
                        else "transcript.segment.finalized"
                    ),
                    aggregate_type="transcript_segment",
                    aggregate_id=segment_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"transcript.final:{final_id}",
                    payload=payload,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                if duplicate_of_segment_id is None:
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
                    "source_track": normalized_source_track,
                    "speaker_id": normalized_speaker_id,
                    "speaker_label": speaker_label,
                    "speaker_confidence": normalized_speaker_confidence,
                    "evidence_hash": evidence_hash,
                }
                conflicts = {key: (existing[key], value) for key, value in expected.items() if existing[key] != value}
                if conflicts:
                    raise ValueError(f"final_id {final_id!r} was retried with conflicting content: {conflicts}")

            checkpoint_row = (
                self._conn.execute(
                    "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                    (meeting_id, segment_id),
                ).fetchone()
                if duplicate_of_segment_id is None
                else None
            )
            paragraph_projection: dict[str, Any] | None = None
            if checkpoint_row is not None:
                paragraph_projection = self._project_semantic_paragraph_locked(
                    meeting_id=meeting_id,
                    checkpoint={
                        "segment_id": checkpoint_row["segment_id"],
                        "final_id": checkpoint_row["final_id"],
                        "transcript_seq": checkpoint_row["transcript_seq"],
                        "text": checkpoint_row["text"],
                        "normalized_text": checkpoint_row["normalized_text"],
                        "started_at_ms": checkpoint_row["started_at_ms"],
                        "ended_at_ms": checkpoint_row["ended_at_ms"],
                        "speaker_id": checkpoint_row["speaker_id"],
                        "speaker_label": checkpoint_row["speaker_label"],
                        "speaker_confidence": checkpoint_row["speaker_confidence"],
                    },
                    now_ms=now_ms,
                )

            effective_enqueue_jobs = enqueue_jobs and duplicate_of_segment_id is None
            if effective_enqueue_jobs:
                job_specs: list[tuple[str, str, int, str | None, int]] = [
                    ("correction", correction_job_id, 100, None, transcript_seq),
                    ("suggestion", suggestion_job_id, 90, generation_id, transcript_seq),
                ]
                if self.semantic_projection_mode == "llm_first":
                    if paragraph_projection is None:
                        raise RuntimeError("LLM-first intelligence requires a semantic paragraph")
                    paragraph_id = str(paragraph_projection["paragraph_id"])
                    paragraph_revision = int(paragraph_projection["revision"])
                    coalescible_job = self._conn.execute(
                        "SELECT job.id FROM jobs job "
                        "JOIN semantic_paragraph_checkpoints checkpoint "
                        "ON checkpoint.meeting_id = job.meeting_id "
                        "AND checkpoint.checkpoint_id = job.evidence_segment_id "
                        "WHERE job.meeting_id = ? AND job.kind = 'intelligence' "
                        "AND job.status IN ('pending', 'retry_wait') "
                        "AND checkpoint.paragraph_id = ? "
                        "ORDER BY job.created_at_ms DESC, job.id DESC LIMIT 1",
                        (meeting_id, paragraph_id),
                    ).fetchone()
                    intelligence_job_id = (
                        str(coalescible_job["id"])
                        if coalescible_job is not None
                        else _stable_id(
                            "job",
                            meeting_id,
                            paragraph_id,
                            str(paragraph_revision),
                            "intelligence",
                        )
                    )
                    job_specs.append(
                        (
                            "intelligence",
                            intelligence_job_id,
                            110,
                            f"intelligence:{meeting_id}:{paragraph_id}:{paragraph_revision}",
                            paragraph_revision,
                        )
                    )
                for kind, job_id, priority, job_generation_id, input_version in job_specs:
                    next_attempt_at_ms = now_ms + INTELLIGENCE_DEBOUNCE_MS if kind == "intelligence" else now_ms
                    deadline_at_ms = now_ms + INTELLIGENCE_MAX_WAIT_MS if kind == "intelligence" else None
                    self._conn.execute(
                        "INSERT OR IGNORE INTO jobs ("
                        "id, meeting_id, kind, status, priority, input_transcript_seq, "
                        "input_version, evidence_segment_id, evidence_hash, generation_id, "
                        "idempotency_key, attempts, max_attempts, next_attempt_at_ms, "
                        "deadline_at_ms, created_at_ms, updated_at_ms"
                        ") VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                        (
                            job_id,
                            meeting_id,
                            kind,
                            priority,
                            transcript_seq,
                            input_version,
                            segment_id,
                            evidence_hash,
                            job_generation_id,
                            f"{kind}:{meeting_id}:{final_id}",
                            max_attempts,
                            next_attempt_at_ms,
                            deadline_at_ms,
                            now_ms,
                            now_ms,
                        ),
                    )
                    if kind == "intelligence":
                        self._conn.execute(
                            "UPDATE jobs SET input_transcript_seq = ?, input_version = ?, "
                            "evidence_segment_id = ?, evidence_hash = ?, generation_id = ?, "
                            "next_attempt_at_ms = MIN(?, COALESCE(deadline_at_ms, created_at_ms + ?)), "
                            "deadline_at_ms = COALESCE(deadline_at_ms, created_at_ms + ?), updated_at_ms = ? "
                            "WHERE id = ? AND status IN ('pending', 'retry_wait')",
                            (
                                transcript_seq,
                                input_version,
                                segment_id,
                                evidence_hash,
                                job_generation_id,
                                next_attempt_at_ms,
                                INTELLIGENCE_MAX_WAIT_MS,
                                INTELLIGENCE_MAX_WAIT_MS,
                                now_ms,
                                job_id,
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
            "source_track": normalized_source_track,
            "source_duplicate": duplicate_of_segment_id is not None,
            "duplicate_of_segment_id": duplicate_of_segment_id,
            "source_duplicate_similarity": source_duplicate_similarity,
            "job_ids": (
                {
                    "correction": correction_job_id,
                    "suggestion": suggestion_job_id,
                    **(
                        {"intelligence": intelligence_job_id}
                        if self.semantic_projection_mode == "llm_first" and intelligence_job_id is not None
                        else {}
                    ),
                }
                if effective_enqueue_jobs
                else {}
            ),
        }

    def _job_evidence_segment_ids_locked(self, job_row: sqlite3.Row) -> list[str]:
        evidence_segment_id = str(job_row["evidence_segment_id"])
        if str(job_row["kind"]) != "intelligence":
            return [evidence_segment_id]
        paragraph = self._conn.execute(
            "SELECT paragraph_id FROM semantic_paragraph_checkpoints WHERE meeting_id = ? AND checkpoint_id = ?",
            (job_row["meeting_id"], evidence_segment_id),
        ).fetchone()
        if paragraph is None:
            return [evidence_segment_id]
        checkpoint_rows = self._conn.execute(
            "SELECT mapping.checkpoint_id FROM semantic_paragraph_checkpoints mapping "
            "JOIN transcript_segments segment ON segment.meeting_id = mapping.meeting_id "
            "AND segment.segment_id = mapping.checkpoint_id "
            "WHERE mapping.meeting_id = ? AND mapping.paragraph_id = ? "
            "AND segment.transcript_seq <= ? ORDER BY mapping.ordinal",
            (
                job_row["meeting_id"],
                paragraph["paragraph_id"],
                int(job_row["input_transcript_seq"]),
            ),
        ).fetchall()
        segment_ids = [str(row["checkpoint_id"]) for row in checkpoint_rows]
        if evidence_segment_id not in segment_ids:
            segment_ids.append(evidence_segment_id)
        return segment_ids

    def _sync_correction_status_for_job_locked(
        self,
        *,
        job_row: sqlite3.Row,
        status: str,
        now_ms: int,
        error_class: str | None = None,
    ) -> None:
        job_kind = str(job_row["kind"])
        if job_kind not in {"correction", "intelligence"}:
            return
        if job_kind == "correction" and self.semantic_projection_mode == "llm_first":
            return
        normalized_status = {
            "pending": "pending",
            "retry_wait": "pending",
            "running": "processing",
            "succeeded": "succeeded",
            "failed": "failed_preserved_original",
        }.get(status)
        if normalized_status is None:
            return
        segment_ids = self._job_evidence_segment_ids_locked(job_row)
        placeholders = ",".join("?" for _ in segment_ids)
        if normalized_status == "succeeded":
            self._conn.execute(
                "UPDATE transcript_segments SET correction_status = CASE "
                "WHEN correction_before_text IS NOT NULL AND correction_after_text IS NOT NULL "
                "AND correction_before_text != correction_after_text THEN 'changed' ELSE 'no_change' END, "
                "correction_before_text = COALESCE(correction_before_text, normalized_text), "
                "correction_after_text = COALESCE(correction_after_text, normalized_text), "
                "correction_error_class = NULL, correction_updated_at_ms = ? "
                "WHERE meeting_id = ? AND segment_id IN (" + placeholders + ")",
                (now_ms, job_row["meeting_id"], *segment_ids),
            )
            return
        self._conn.execute(
            "UPDATE transcript_segments SET correction_status = ?, correction_error_class = ?, "
            "correction_updated_at_ms = ? WHERE meeting_id = ? AND segment_id IN (" + placeholders + ")",
            (
                normalized_status,
                _public_job_error_class(error_class) if normalized_status == "failed_preserved_original" else None,
                now_ms,
                job_row["meeting_id"],
                *segment_ids,
            ),
        )

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
            self._sync_correction_status_for_job_locked(
                job_row=row,
                status="running",
                now_ms=now_ms,
            )
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
            self._sync_correction_status_for_job_locked(
                job_row=row,
                status="succeeded",
                now_ms=now_ms,
            )
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
            self._sync_correction_status_for_job_locked(
                job_row=row,
                status=str(row["status"]),
                now_ms=now_ms,
                error_class=row["error_class"],
            )
            self._reject_failed_suggestion_drafts_locked(job_row=row, now_ms=now_ms)
        return self._job_dict(row)

    def defer_job(
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
        error_class = _public_job_error_class(_required(error_class, "error_class"))
        now_ms = max(0, int(now_ms))
        next_attempt_at_ms = max(now_ms, int(next_attempt_at_ms))
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE jobs SET status = 'retry_wait', "
                "attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END, "
                "next_attempt_at_ms = ?, lease_owner = NULL, lease_until_ms = NULL, "
                "error_class = ?, completed_at_ms = NULL, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? "
                "AND lease_until_ms > ?",
                (
                    next_attempt_at_ms,
                    error_class,
                    now_ms,
                    job_id,
                    worker_id,
                    now_ms,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self._sync_correction_status_for_job_locked(
                job_row=row,
                status=str(row["status"]),
                now_ms=now_ms,
                error_class=row["error_class"],
            )
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
            self._sync_correction_status_for_job_locked(
                job_row=row,
                status="failed",
                now_ms=now_ms,
                error_class=error_class,
            )
            self._reject_failed_suggestion_drafts_locked(job_row=row, now_ms=now_ms)
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
        expired_jobs = self._conn.execute(
            "SELECT id FROM jobs WHERE status = 'running' AND lease_until_ms <= ?",
            (now_ms,),
        ).fetchall()
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
        for expired_job in expired_jobs:
            job_row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (expired_job["id"],),
            ).fetchone()
            if job_row is not None:
                self._sync_correction_status_for_job_locked(
                    job_row=job_row,
                    status=str(job_row["status"]),
                    now_ms=now_ms,
                    error_class=job_row["error_class"],
                )
                self._reject_failed_suggestion_drafts_locked(job_row=job_row, now_ms=now_ms)
        return int(result.rowcount)

    def _reject_failed_suggestion_drafts_locked(
        self,
        *,
        job_row: sqlite3.Row | None,
        now_ms: int,
    ) -> None:
        if job_row is None or job_row["kind"] != "suggestion" or job_row["status"] != "failed":
            return
        draft_rows = self._conn.execute(
            "SELECT * FROM suggestions WHERE job_id = ? AND status = 'draft' ORDER BY created_at_ms, suggestion_id",
            (job_row["id"],),
        ).fetchall()
        for draft_row in draft_rows:
            suggestion_id = str(draft_row["suggestion_id"])
            updated = self._conn.execute(
                "UPDATE suggestions SET status = 'rejected', updated_at_ms = ? "
                "WHERE suggestion_id = ? AND job_id = ? AND status = 'draft'",
                (now_ms, suggestion_id, job_row["id"]),
            )
            if updated.rowcount != 1:
                continue
            rejected_row = self._conn.execute(
                "SELECT * FROM suggestions WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
            rejected = self._suggestion_dict(rejected_row)
            self._append_event_locked(
                meeting_id=str(rejected_row["meeting_id"]),
                event_type="suggestion.rejected",
                aggregate_type="suggestion",
                aggregate_id=suggestion_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"suggestion.rejected:{suggestion_id}:{job_row['id']}",
                payload={
                    **rejected,
                    "error_class": _public_job_error_class(job_row["error_class"]),
                },
                correlation_id=str(rejected_row["generation_id"]),
                causation_id=str(job_row["id"]),
            )

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
                "SELECT aggregate_id, payload_json FROM meeting_events "
                "WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, idempotency_key),
            ).fetchone()
            if existing_event is not None:
                existing_payload = json.loads(existing_event["payload_json"] or "{}")
                if (
                    str(existing_event["aggregate_id"]) != segment_id
                    or str(existing_payload.get("previous_evidence_hash") or "") != expected_evidence_hash
                    or str(existing_payload.get("corrected_text") or "") != corrected_text
                ):
                    raise TranscriptRevisionIdentityConflict(
                        "transcript revision idempotency key conflicts with its evidence version"
                    )
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
                "revision = revision + 1, correction_status = 'changed', "
                "correction_before_text = ?, correction_after_text = ?, "
                "correction_error_class = NULL, correction_updated_at_ms = ?, updated_at_ms = ? "
                "WHERE meeting_id = ? AND segment_id = ? "
                "AND evidence_hash = ? AND revision = ?",
                (
                    corrected_text,
                    new_evidence_hash,
                    original_text,
                    corrected_text,
                    now_ms,
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
            self._conn.execute(
                "UPDATE asr_checkpoints SET normalized_text = ?, updated_at_ms = ? "
                "WHERE meeting_id = ? AND checkpoint_id = ?",
                (corrected_text, now_ms, meeting_id, segment_id),
            )
            paragraph_rows = self._conn.execute(
                "SELECT paragraph_id FROM semantic_paragraph_checkpoints WHERE meeting_id = ? AND checkpoint_id = ?",
                (meeting_id, segment_id),
            ).fetchall()
            for paragraph_row in paragraph_rows:
                self._rebuild_semantic_paragraph_locked(
                    meeting_id=meeting_id,
                    paragraph_id=str(paragraph_row["paragraph_id"]),
                    now_ms=now_ms,
                )
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

    def apply_intelligence_response(
        self,
        *,
        meeting_id: str,
        job_id: str,
        response: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        """Apply one validated LLM-first response exactly once.

        The response has already passed the provider-facing schema parser. This
        method repeats the evidence check against SQLite because the transcript
        may have changed while the remote request was in flight.
        """

        meeting_id = _required(meeting_id, "meeting_id")
        job_id = _required(job_id, "job_id")
        if not isinstance(response, Mapping):
            raise IntelligenceProjectionError("intelligence response must be an object")
        now_ms = max(0, int(now_ms))
        response_event_key = f"meeting.intelligence.applied:{job_id}"
        raw_revisions = response.get("paragraph_revisions") or []
        raw_changes = response.get("state_changes") or []
        raw_topic = response.get("topic_update")
        raw_follow_up = response.get("follow_up")
        if not isinstance(raw_revisions, list) or not isinstance(raw_changes, list):
            raise IntelligenceProjectionError("intelligence response arrays are invalid")

        def idempotent_result(event_row: sqlite3.Row) -> dict[str, Any]:
            payload = json.loads(event_row["payload_json"] or "{}")
            return {
                "meeting_id": meeting_id,
                "job_id": job_id,
                "idempotent": True,
                "revision_count": int(payload.get("revision_count") or 0),
                "state_change_count": int(payload.get("state_change_count") or 0),
                "follow_up": payload.get("follow_up"),
            }

        with self._lock:
            applied_event = self._conn.execute(
                "SELECT payload_json FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, response_event_key),
            ).fetchone()
            if applied_event is not None:
                return idempotent_result(applied_event)
            job_row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND meeting_id = ? AND kind = 'intelligence'",
                (job_id, meeting_id),
            ).fetchone()
            if job_row is None:
                raise IntelligenceProjectionError("intelligence response references an unknown job")
            covered_segment_ids = set(self._job_evidence_segment_ids_locked(job_row))
            own_revision_rows = self._conn.execute(
                "SELECT aggregate_id, payload_json FROM meeting_events "
                "WHERE meeting_id = ? AND type = 'transcript.segment.revised' AND causation_id = ?",
                (meeting_id, job_id),
            ).fetchall()
            own_revisions = [
                (str(row["aggregate_id"]), json.loads(row["payload_json"] or "{}"))
                for row in own_revision_rows
                if str(row["aggregate_id"]) in covered_segment_ids
            ]
            evidence_row = self._conn.execute(
                "SELECT transcript_seq, evidence_hash FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                (meeting_id, job_row["evidence_segment_id"]),
            ).fetchone()
            evidence_hash_is_current = evidence_row is not None and str(evidence_row["evidence_hash"]) == str(
                job_row["evidence_hash"]
            )
            evidence_hash_was_advanced_by_job = evidence_row is not None and any(
                segment_id == str(job_row["evidence_segment_id"])
                and str(payload.get("previous_evidence_hash") or "") == str(job_row["evidence_hash"])
                and str(payload.get("evidence_hash") or "") == str(evidence_row["evidence_hash"])
                for segment_id, payload in own_revisions
            )
            if (
                evidence_row is None
                or int(evidence_row["transcript_seq"]) != int(job_row["input_transcript_seq"])
                or not (evidence_hash_is_current or evidence_hash_was_advanced_by_job)
            ):
                raise IntelligenceProjectionError("intelligence job evidence is stale")
            paragraph_row = self._conn.execute(
                "SELECT paragraph.revision FROM semantic_paragraphs paragraph "
                "JOIN semantic_paragraph_checkpoints mapping "
                "ON mapping.meeting_id = paragraph.meeting_id "
                "AND mapping.paragraph_id = paragraph.paragraph_id "
                "WHERE mapping.meeting_id = ? AND mapping.checkpoint_id = ?",
                (meeting_id, job_row["evidence_segment_id"]),
            ).fetchone()
            expected_paragraph_revision = int(job_row["input_version"]) + len(own_revisions)
            if paragraph_row is None or int(paragraph_row["revision"]) != expected_paragraph_revision:
                raise IntelligenceProjectionError("intelligence paragraph evidence is stale")

        # Transcript revisions use their existing CAS implementation. Do this
        # before entity projection so stale evidence can never produce facts.
        revision_count = 0
        no_change_revisions: list[tuple[str, int]] = []
        for index, raw_revision in enumerate(raw_revisions):
            if not isinstance(raw_revision, Mapping):
                raise IntelligenceProjectionError(f"paragraph revision {index} is invalid")
            segment_id = _required(str(raw_revision.get("target_id") or ""), "target_id")
            if segment_id not in covered_segment_ids:
                raise IntelligenceProjectionError(f"intelligence revision is outside the job evidence: {segment_id}")
            corrected_text = _required(str(raw_revision.get("corrected_text") or ""), "corrected_text")
            expected_revision = _positive_int_or_error(
                raw_revision.get("expected_revision"),
                "expected_revision",
            )
            if not bool(raw_revision.get("changed")) and int(raw_revision.get("change_count") or 0) == 0:
                with self._lock:
                    row = self._conn.execute(
                        "SELECT revision FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                        (meeting_id, segment_id),
                    ).fetchone()
                if row is None or int(row["revision"]) != expected_revision:
                    raise IntelligenceProjectionError(f"intelligence revision is stale: {segment_id}")
                no_change_revisions.append((segment_id, expected_revision))
                continue
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                    (meeting_id, segment_id),
                ).fetchone()
                revision_id = f"intelligence:{job_id}:{segment_id}:{expected_revision}"
                existing_revision = self._conn.execute(
                    "SELECT 1 FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                    (meeting_id, f"transcript.revision:{revision_id}"),
                ).fetchone()
            if existing_revision is not None:
                revision_count += 1
                continue
            if row is None:
                raise IntelligenceProjectionError(f"intelligence revision references unknown segment: {segment_id}")
            if int(row["revision"]) != expected_revision:
                raise IntelligenceProjectionError(f"intelligence revision is stale: {segment_id}")
            committed = self.commit_transcript_revision(
                meeting_id=meeting_id,
                segment_id=segment_id,
                expected_evidence_hash=str(row["evidence_hash"]),
                corrected_text=corrected_text,
                revision_id=revision_id,
                now_ms=now_ms,
                correlation_id=meeting_id,
                causation_id=job_id,
            )
            if committed is None:
                raise IntelligenceProjectionError(f"intelligence revision lost its evidence: {segment_id}")
            revision_count += 1

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            meeting = self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if meeting is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            already_applied = self._conn.execute(
                "SELECT payload_json FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                (meeting_id, response_event_key),
            ).fetchone()
            if already_applied is not None:
                return idempotent_result(already_applied)

            for segment_id, expected_revision in no_change_revisions:
                result = self._conn.execute(
                    "UPDATE transcript_segments SET correction_status = 'no_change', "
                    "correction_before_text = normalized_text, correction_after_text = normalized_text, "
                    "correction_error_class = NULL, correction_updated_at_ms = ? "
                    "WHERE meeting_id = ? AND segment_id = ? AND revision = ?",
                    (now_ms, meeting_id, segment_id, expected_revision),
                )
                if result.rowcount != 1:
                    raise IntelligenceProjectionError(f"intelligence no-change target is stale: {segment_id}")

            state_change_count = 0
            current_seq = int(
                self._conn.execute(
                    "SELECT COALESCE(MAX(transcript_seq), 0) FROM transcript_segments WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()[0]
            )
            if current_seq <= 0:
                raise IntelligenceProjectionError("intelligence response requires transcript evidence")

            for index, raw_change in enumerate(raw_changes):
                if not isinstance(raw_change, Mapping):
                    raise IntelligenceProjectionError(f"state change {index} is invalid")
                kind = {
                    "decision": "decision_candidate",
                    "action_item": "action_item",
                    "risk": "risk",
                    "open_question": "open_question",
                }.get(str(raw_change.get("type") or "").strip())
                if kind is None:
                    raise IntelligenceProjectionError(f"state change {index} has unsupported type")
                operation = str(raw_change.get("operation") or "").strip()
                if operation not in {"add", "update", "resolve"}:
                    raise IntelligenceProjectionError(f"state change {index} has unsupported operation")
                requested_entity_id = _required(str(raw_change.get("item_id") or ""), "item_id")
                entity_id = requested_entity_id
                text = _required(str(raw_change.get("content") or ""), "content")
                evidence_ids = raw_change.get("evidence_segment_ids")
                if not isinstance(evidence_ids, list) or not evidence_ids:
                    raise IntelligenceProjectionError(f"state change {index} has no evidence IDs")
                quote = _required(str(raw_change.get("evidence_quote") or ""), "evidence_quote")
                normalized_quote = _compact_evidence(quote)
                evidence_rows = []
                for evidence_id in evidence_ids:
                    row = self._conn.execute(
                        "SELECT * FROM transcript_segments WHERE meeting_id = ? AND segment_id = ?",
                        (meeting_id, str(evidence_id)),
                    ).fetchone()
                    if row is None:
                        raise IntelligenceProjectionError(
                            f"state change {index} references unknown evidence: {evidence_id}"
                        )
                    evidence_rows.append(row)
                # The intelligence prompt uses canonical normalized_text. The
                # durable barrier must therefore accept a verbatim quote from
                # that exact model-visible text while still allowing a quote
                # from the preserved raw ASR text for backwards compatibility.
                if not any(
                    normalized_quote in _compact_evidence(str(row["normalized_text"]))
                    or normalized_quote in _compact_evidence(str(row["text"]))
                    for row in evidence_rows
                ):
                    raise IntelligenceProjectionError(f"state change {index} evidence quote is not present")
                deduplicated = False
                if operation == "add":
                    content_key = _entity_content_key(text)
                    for candidate in self._conn.execute(
                        "SELECT * FROM meeting_entities WHERE meeting_id = ? AND kind = ? "
                        "ORDER BY first_seen_seq, entity_id",
                        (meeting_id, kind),
                    ).fetchall():
                        if _entity_content_key(candidate["text"]) == content_key:
                            entity_id = str(candidate["entity_id"])
                            deduplicated = entity_id != requested_entity_id
                            break
                existing = self._conn.execute(
                    "SELECT status FROM meeting_entities WHERE meeting_id = ? AND entity_id = ?",
                    (meeting_id, entity_id),
                ).fetchone()
                status = str(raw_change.get("status") or "").strip() or (
                    "done"
                    if operation == "resolve" and kind == "action_item"
                    else "answered"
                    if operation == "resolve" and kind == "open_question"
                    else "resolved"
                    if operation == "resolve"
                    else str(existing["status"])
                    if existing is not None
                    else "candidate"
                )
                confidence = _bounded_confidence(raw_change.get("confidence"))
                entity = {
                    "id": entity_id,
                    "text": text,
                    "status": status,
                    "confidence": confidence,
                    "evidence": {
                        "segment_id": str(evidence_ids[0]),
                        "segment_ids": [str(item) for item in evidence_ids],
                        "quote": quote,
                        "source": "llm_first",
                    },
                    "evidence_segment_ids": [str(item) for item in evidence_ids],
                    "owner": raw_change.get("owner"),
                    "deadline": raw_change.get("deadline"),
                    "updated_at_ms": now_ms,
                }
                stored = self._upsert_entity_locked(
                    meeting_id,
                    kind,
                    entity,
                    current_transcript_seq=current_seq,
                )
                event_type = {
                    "current_topic": "meeting.topic.updated",
                    "open_question": "meeting.open_question.updated",
                    "decision_candidate": "meeting.decision.updated",
                    "action_item": "meeting.action_item.updated",
                    "risk": "meeting.risk.updated",
                }[kind]
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type=event_type,
                    aggregate_type="meeting_entity",
                    aggregate_id=entity_id,
                    occurred_at_ms=now_ms,
                    idempotency_key=f"meeting.intelligence.{kind}:{job_id}:{entity_id}:{operation}",
                    payload={
                        "entity": stored,
                        "operation": operation,
                        "deduplicated": deduplicated,
                        "requested_item_id": requested_entity_id,
                        "evidence_quote": quote,
                        "source": "llm_first",
                    },
                    correlation_id=meeting_id,
                    causation_id=job_id,
                )
                state_change_count += 1

            if isinstance(raw_topic, Mapping) and str(raw_topic.get("operation") or "").strip() != "noop":
                topic_title = _required(str(raw_topic.get("title") or ""), "topic.title")
                topic_summary = _required(str(raw_topic.get("summary") or ""), "topic.summary")
                topic = self._upsert_entity_locked(
                    meeting_id,
                    "current_topic",
                    {
                        "id": "current-topic",
                        "text": topic_title,
                        "status": "active",
                        "confidence": 1.0,
                        "evidence": {"summary": topic_summary, "source": "llm_first"},
                        "evidence_segment_ids": [],
                        "updated_at_ms": now_ms,
                    },
                    current_transcript_seq=current_seq,
                )
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="meeting.topic.updated",
                    aggregate_type="meeting_entity",
                    aggregate_id="current-topic",
                    occurred_at_ms=now_ms,
                    idempotency_key=f"meeting.intelligence.topic:{job_id}",
                    payload={"topic": topic, "summary": topic_summary, "source": "llm_first"},
                    correlation_id=meeting_id,
                    causation_id=job_id,
                )

            follow_up_payload = dict(raw_follow_up) if isinstance(raw_follow_up, Mapping) else None
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.intelligence.applied",
                aggregate_type="meeting_intelligence",
                aggregate_id=job_id,
                occurred_at_ms=now_ms,
                idempotency_key=response_event_key,
                payload={
                    "job_id": job_id,
                    "revision_count": revision_count,
                    "state_change_count": state_change_count,
                    "follow_up": follow_up_payload,
                    "source": "llm_first",
                },
                correlation_id=meeting_id,
                causation_id=job_id,
            )
        return {
            "meeting_id": meeting_id,
            "job_id": job_id,
            "idempotent": False,
            "revision_count": revision_count,
            "state_change_count": state_change_count,
            "follow_up": follow_up_payload,
        }

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
                active_paragraphs = self._conn.execute(
                    "SELECT paragraph_id FROM semantic_paragraphs WHERE meeting_id = ? AND status = 'active'",
                    (meeting_id,),
                ).fetchall()
                if active_paragraphs:
                    self._conn.execute(
                        "UPDATE semantic_paragraphs SET status = 'stable', updated_at_ms = ? "
                        "WHERE meeting_id = ? AND status = 'active'",
                        (now_ms, meeting_id),
                    )
                latest_segment = self._conn.execute(
                    "SELECT * FROM transcript_segments WHERE meeting_id = ? "
                    "AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 1",
                    (meeting_id,),
                ).fetchone()
                if latest_segment is not None:
                    existing_correction = self._conn.execute(
                        "SELECT 1 FROM jobs WHERE meeting_id = ? AND kind = 'correction' "
                        "AND input_transcript_seq = ? AND input_version = ? "
                        "AND evidence_segment_id = ? AND evidence_hash = ? "
                        "AND status IN ('pending', 'running', 'retry_wait', 'succeeded') LIMIT 1",
                        (
                            meeting_id,
                            int(latest_segment["transcript_seq"]),
                            int(latest_segment["revision"]),
                            str(latest_segment["segment_id"]),
                            str(latest_segment["evidence_hash"]),
                        ),
                    ).fetchone()
                    for kind, priority in (
                        ("correction", 120),
                        ("minutes", 60),
                        ("approach", 50),
                        ("index", 40),
                    ):
                        if kind == "correction" and existing_correction is not None:
                            continue
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

    def create_import_job(
        self,
        *,
        meeting_id: str,
        source_relative_path: str,
        original_filename: str,
        file_size_bytes: int,
        now_ms: int,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        source_relative_path = _required(source_relative_path, "source_relative_path")
        original_filename = _required(original_filename, "original_filename")
        file_size_bytes = int(file_size_bytes)
        max_attempts = int(max_attempts)
        now_ms = max(0, int(now_ms))
        if file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        job_id = _stable_id("recording_import", meeting_id)

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if (
                self._conn.execute(
                    "SELECT 1 FROM meetings WHERE id = ?",
                    (meeting_id,),
                ).fetchone()
                is None
            ):
                raise KeyError(f"meeting not found: {meeting_id}")
            existing = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            if existing is not None:
                conflicts = [
                    field
                    for field, expected in (
                        ("source_relative_path", source_relative_path),
                        ("original_filename", original_filename),
                        ("file_size_bytes", file_size_bytes),
                        ("max_attempts", max_attempts),
                    )
                    if existing[field] != expected
                ]
                if conflicts:
                    raise ValueError(
                        "recording import job was retried with conflicting fields: " + ", ".join(conflicts)
                    )
                return self._import_job_dict(existing)
            self._conn.execute(
                "INSERT INTO recording_import_jobs ("
                "id, meeting_id, status, stage, progress, source_relative_path, "
                "original_filename, file_size_bytes, attempts, max_attempts, "
                "next_attempt_at_ms, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, 'pending', 'reading', 0, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    job_id,
                    meeting_id,
                    source_relative_path,
                    original_filename,
                    file_size_bytes,
                    max_attempts,
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._import_job_dict(row)

    def get_import_job(self, job_id: str) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"recording import job not found: {job_id}")
        return self._import_job_dict(row)

    def list_import_jobs(
        self,
        *,
        meeting_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if meeting_id is not None:
            clauses.append("meeting_id = ?")
            parameters.append(_required(meeting_id, "meeting_id"))
        if statuses:
            normalized_statuses = tuple(_required(status, "status") for status in statuses)
            invalid = sorted(set(normalized_statuses) - set(JOB_STATUSES))
            if invalid:
                raise ValueError(f"unsupported import job statuses: {invalid}")
            clauses.append(f"status IN ({','.join('?' for _ in normalized_statuses)})")
            parameters.extend(normalized_statuses)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recording_import_jobs" + where + " ORDER BY created_at_ms, id",
                parameters,
            ).fetchall()
        return [self._import_job_dict(row) for row in rows]

    def claim_import_job(
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
            self._recover_interrupted_import_jobs_locked(now_ms)
            candidate = self._conn.execute(
                "SELECT id FROM recording_import_jobs "
                "WHERE status IN ('pending', 'retry_wait') AND next_attempt_at_ms <= ? "
                "AND attempts < max_attempts "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_import_jobs.meeting_id) "
                "ORDER BY next_attempt_at_ms, created_at_ms, id LIMIT 1",
                (now_ms,),
            ).fetchone()
            if candidate is None:
                return None
            result = self._conn.execute(
                "UPDATE recording_import_jobs SET status = 'running', attempts = attempts + 1, "
                "lease_owner = ?, lease_until_ms = ?, error_class = NULL, error_message = NULL, "
                "completed_at_ms = NULL, updated_at_ms = ? WHERE id = ? "
                "AND status IN ('pending', 'retry_wait') AND next_attempt_at_ms <= ? "
                "AND attempts < max_attempts "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_import_jobs.meeting_id)",
                (worker_id, now_ms + lease_ms, now_ms, candidate["id"], now_ms),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (candidate["id"],),
            ).fetchone()
        return self._import_job_dict(row)

    def update_import_job_stage(
        self,
        *,
        job_id: str,
        worker_id: str,
        stage: str,
        progress: int,
        now_ms: int,
        lease_ms: int | None = None,
    ) -> dict[str, Any] | None:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        stage = _validated_import_stage(stage)
        progress = int(progress)
        now_ms = max(0, int(now_ms))
        if stage == "completed":
            raise ValueError("use complete_import_job to mark the completed stage")
        if not 0 <= progress < 100:
            raise ValueError("progress must be between 0 and 99 while an import job is running")
        if lease_ms is not None and int(lease_ms) <= 0:
            raise ValueError("lease_ms must be positive when provided")

        with self._write_transaction():
            current = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if (
                current is None
                or current["status"] != "running"
                or current["lease_owner"] != worker_id
                or int(current["lease_until_ms"] or 0) <= now_ms
            ):
                return None
            if IMPORT_JOB_STAGES.index(stage) < IMPORT_JOB_STAGES.index(str(current["stage"])):
                raise ValueError("import job stage cannot move backwards")
            if progress < int(current["progress"]):
                raise ValueError("import job progress cannot move backwards")
            renewed_until_ms = now_ms + int(lease_ms) if lease_ms is not None else None
            result = self._conn.execute(
                "UPDATE recording_import_jobs SET stage = ?, progress = ?, "
                "lease_until_ms = CASE WHEN ? IS NULL THEN lease_until_ms ELSE ? END, "
                "updated_at_ms = ? WHERE id = ? AND status = 'running' "
                "AND lease_owner = ? AND lease_until_ms > ?",
                (
                    stage,
                    progress,
                    renewed_until_ms,
                    renewed_until_ms,
                    now_ms,
                    job_id,
                    worker_id,
                    now_ms,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._import_job_dict(row)

    def complete_import_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now_ms: int,
    ) -> dict[str, Any] | None:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            current = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if current is not None and current["status"] == "succeeded":
                return self._import_job_dict(current)
            result = self._conn.execute(
                "UPDATE recording_import_jobs SET status = 'succeeded', stage = 'completed', "
                "progress = 100, lease_owner = NULL, lease_until_ms = NULL, "
                "error_class = NULL, error_message = NULL, completed_at_ms = ?, updated_at_ms = ? "
                "WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_until_ms > ?",
                (now_ms, now_ms, job_id, worker_id, now_ms),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._import_job_dict(row)

    def fail_import_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_class: str,
        now_ms: int,
        error_message: str | None = None,
        retryable: bool = False,
        next_attempt_at_ms: int | None = None,
    ) -> dict[str, Any] | None:
        job_id = _required(job_id, "job_id")
        worker_id = _required(worker_id, "worker_id")
        error_class = _required(error_class, "error_class")
        normalized_error_message = str(error_message or "").strip() or None
        now_ms = max(0, int(now_ms))
        retry_at_ms = max(now_ms, int(next_attempt_at_ms or now_ms))
        with self._write_transaction():
            current = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] in {"retry_wait", "failed"}:
                if current["error_class"] == error_class and current["error_message"] == normalized_error_message:
                    return self._import_job_dict(current)
                return None
            terminal = not retryable or int(current["attempts"]) >= int(current["max_attempts"])
            result = self._conn.execute(
                "UPDATE recording_import_jobs SET status = ?, next_attempt_at_ms = ?, "
                "lease_owner = NULL, lease_until_ms = NULL, error_class = ?, error_message = ?, "
                "completed_at_ms = ?, updated_at_ms = ? WHERE id = ? AND status = 'running' "
                "AND lease_owner = ? AND lease_until_ms > ?",
                (
                    "failed" if terminal else "retry_wait",
                    retry_at_ms,
                    error_class,
                    normalized_error_message,
                    now_ms if terminal else None,
                    now_ms,
                    job_id,
                    worker_id,
                    now_ms,
                ),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._import_job_dict(row)

    def retry_import_job(
        self,
        *,
        job_id: str,
        now_ms: int,
    ) -> dict[str, Any] | None:
        """Start a fresh user-requested attempt without re-uploading the source."""

        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            current = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if current is None:
                return None
            if current["status"] != "failed":
                raise ValueError("only failed recording import jobs can be retried")
            if (
                self._conn.execute(
                    "SELECT 1 FROM meeting_tombstones WHERE meeting_id = ?",
                    (current["meeting_id"],),
                ).fetchone()
                is not None
            ):
                return None
            result = self._conn.execute(
                "UPDATE recording_import_jobs SET status = 'pending', stage = 'reading', "
                "progress = 0, attempts = 0, next_attempt_at_ms = ?, lease_owner = NULL, "
                "lease_until_ms = NULL, error_class = NULL, error_message = NULL, "
                "completed_at_ms = NULL, updated_at_ms = ? WHERE id = ? AND status = 'failed'",
                (now_ms, now_ms, job_id),
            )
            if result.rowcount != 1:
                return None
            row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._import_job_dict(row)

    def recover_interrupted_import_jobs(self, *, now_ms: int) -> int:
        with self._write_transaction():
            return self._recover_interrupted_import_jobs_locked(max(0, int(now_ms)))

    def _recover_interrupted_import_jobs_locked(self, now_ms: int) -> int:
        rows = self._conn.execute(
            "SELECT j.*, EXISTS(SELECT 1 FROM meeting_tombstones t "
            "WHERE t.meeting_id = j.meeting_id) AS tombstoned "
            "FROM recording_import_jobs j WHERE j.status = 'running' "
            "AND j.lease_until_ms IS NOT NULL AND j.lease_until_ms <= ?",
            (now_ms,),
        ).fetchall()
        for row in rows:
            cancelled = bool(row["tombstoned"])
            terminal = cancelled or int(row["attempts"]) >= int(row["max_attempts"])
            self._conn.execute(
                "UPDATE recording_import_jobs SET status = ?, next_attempt_at_ms = ?, "
                "lease_owner = NULL, lease_until_ms = NULL, error_class = ?, error_message = ?, "
                "completed_at_ms = ?, updated_at_ms = ? WHERE id = ? AND status = 'running' "
                "AND lease_until_ms IS NOT NULL AND lease_until_ms <= ?",
                (
                    "cancelled" if cancelled else "failed" if terminal else "retry_wait",
                    now_ms,
                    "meeting_deleted" if cancelled else "lease_expired",
                    (
                        "meeting deletion cancelled the interrupted import"
                        if cancelled
                        else "the import worker lease expired before completion"
                    ),
                    now_ms if terminal else None,
                    now_ms,
                    row["id"],
                    now_ms,
                ),
            )
        return len(rows)

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
                    key: (existing_chunk[key], value) for key, value in expected.items() if existing_chunk[key] != value
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
            if (
                self._conn.execute(
                    "SELECT 1 FROM meeting_events WHERE meeting_id = ? AND idempotency_key = ?",
                    (meeting_id, event_key),
                ).fetchone()
                is None
            ):
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

    def abort_recording_setup(
        self,
        *,
        meeting_id: str,
        track: str,
        epoch: int,
        lease_owner: str,
        capture_generation: int,
        now_ms: int,
    ) -> dict[str, Any] | None:
        """Release only the exact capture lease whose writer setup failed."""

        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        lease_owner = _required(lease_owner, "lease_owner")
        epoch = int(epoch)
        capture_generation = int(capture_generation)
        now_ms = max(0, int(now_ms))
        if epoch < 0 or capture_generation <= 0:
            raise ValueError("recording setup identity is invalid")

        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? "
                "AND epoch = ? AND status = 'active' AND capture_generation = ? "
                "AND lease_owner = ?",
                (
                    meeting_id,
                    track,
                    epoch,
                    capture_generation,
                    lease_owner,
                ),
            ).fetchone()
            if row is None:
                return None
            interrupted_id = self._interrupt_recording_row_locked(
                row,
                now_ms=now_ms,
                error_class="recording_setup_failed",
            )
            if interrupted_id is None:
                return None
            current = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
        return self._recording_session_dict(current)

    def fail_recording_track(
        self,
        *,
        meeting_id: str,
        track: str,
        epoch: int,
        error_class: str,
        now_ms: int,
    ) -> dict[str, Any]:
        """Fail one input track without misrepresenting the other track as failed."""

        meeting_id = _required(meeting_id, "meeting_id")
        track = _required(track, "track")
        epoch = int(epoch)
        error_class = _required(error_class, "error_class")
        now_ms = max(0, int(now_ms))
        if epoch < 0:
            raise ValueError("recording epoch must be non-negative")
        if len(error_class) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", error_class):
            raise ValueError("recording error_class is invalid")

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
            if row is None:
                raise KeyError(f"recording session not found: {meeting_id}/{track}/{epoch}")
            if row["status"] == "ready":
                raise RuntimeError("a ready recording track cannot be overwritten as failed")
            if row["status"] == "failed" and row["error_class"] == error_class:
                return self._recording_session_dict(row)
            self._conn.execute(
                "UPDATE recording_sessions SET status = 'failed', lease_owner = NULL, "
                "lease_until_ms = NULL, error_class = ?, completed_at_ms = ?, updated_at_ms = ? "
                "WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (error_class, now_ms, now_ms, meeting_id, track, epoch),
            )
            failed_row = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                (meeting_id, track, epoch),
            ).fetchone()
            failed = self._recording_session_dict(failed_row)
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="recording.track.failed",
                aggregate_type="recording_session",
                aggregate_id=f"{track}:{epoch}",
                occurred_at_ms=now_ms,
                idempotency_key=f"recording.track.failed:{track}:{epoch}:{error_class}",
                payload=failed,
                correlation_id=meeting_id,
            )
        return failed

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
        captured_at_ms: int | None = None,
        source_sequence_start: int | None = None,
        source_sequence_end: int | None = None,
        source_timestamp_start_ms: int | None = None,
        source_timestamp_end_ms: int | None = None,
        lease_owner: str | None = None,
        lease_ms: int | None = None,
        expected_capture_generation: int | None = None,
        require_lease_expired_at_ms: int | None = None,
        expected_capture_lease_owner: str | None = None,
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
        normalized_captured_at_ms = max(0, int(captured_at_ms)) if captured_at_ms is not None else None
        source_range = self._normalize_audio_chunk_source_range(
            source_sequence_start=source_sequence_start,
            source_sequence_end=source_sequence_end,
            source_timestamp_start_ms=source_timestamp_start_ms,
            source_timestamp_end_ms=source_timestamp_end_ms,
        )
        normalized_lease_owner = str(lease_owner or "").strip() or None
        normalized_lease_ms = int(lease_ms or 0)
        recovery_generation = int(expected_capture_generation) if expected_capture_generation is not None else None
        recovery_expired_at_ms = (
            max(0, int(require_lease_expired_at_ms)) if require_lease_expired_at_ms is not None else None
        )
        recovery_lease_owner = str(expected_capture_lease_owner or "").strip() or None
        if epoch < 0 or chunk_seq < 0:
            raise ValueError("audio epoch and chunk_seq must be non-negative")
        if min(sample_rate_hz, sample_count, duration_ms, file_size_bytes) <= 0:
            raise ValueError("audio chunk dimensions must be positive")
        if (normalized_lease_owner is None) != (normalized_lease_ms <= 0):
            raise ValueError("lease_owner and positive lease_ms must be provided together")
        if recovery_generation is None and (recovery_expired_at_ms is not None or recovery_lease_owner is not None):
            raise ValueError("recording recovery fence requires expected_capture_generation")
        if recovery_generation is not None and ((recovery_expired_at_ms is None) == (recovery_lease_owner is None)):
            raise ValueError("recording recovery requires exactly one lease-expiry or lease-owner fence")
        if recovery_generation is not None and recovery_generation <= 0:
            raise ValueError("expected_capture_generation must be positive")

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if recovery_generation is not None:
                recovery_lease = self._conn.execute(
                    "SELECT status, capture_generation, lease_owner, lease_until_ms "
                    "FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, track, epoch),
                ).fetchone()
                if (
                    recovery_lease is None
                    or recovery_lease["status"] != "active"
                    or int(recovery_lease["capture_generation"]) != recovery_generation
                    or (
                        recovery_expired_at_ms is not None
                        and int(recovery_lease["lease_until_ms"] or 0) > recovery_expired_at_ms
                    )
                    or (recovery_lease_owner is not None and recovery_lease["lease_owner"] != recovery_lease_owner)
                ):
                    raise RecordingRecoveryConflict("recording capture changed after the recovery scan")
            self._conn.execute(
                "INSERT INTO meetings ("
                "id, state, started_at_ms, latest_seq, revision, created_at_ms, updated_at_ms"
                ") VALUES (?, 'live', ?, 0, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "state = CASE WHEN meetings.state = 'ended' THEN 'ended' ELSE 'live' END, "
                "updated_at_ms = excluded.updated_at_ms",
                (meeting_id, now_ms, now_ms, now_ms),
            )
            if normalized_captured_at_ms is None:
                recording_clock = self._conn.execute(
                    "SELECT started_at_ms FROM recording_sessions WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, track, epoch),
                ).fetchone()
                if recording_clock is not None:
                    timeline_start_ms = int(recording_clock["started_at_ms"])
                else:
                    meeting_clock = self._conn.execute(
                        "SELECT started_at_ms FROM meetings WHERE id = ?",
                        (meeting_id,),
                    ).fetchone()
                    timeline_start_ms = int(meeting_clock["started_at_ms"] or now_ms)
                prior_duration_ms = int(
                    self._conn.execute(
                        "SELECT COALESCE(SUM(duration_ms), 0) FROM audio_chunks "
                        "WHERE meeting_id = ? AND track = ? AND epoch = ? AND chunk_seq < ?",
                        (meeting_id, track, epoch, chunk_seq),
                    ).fetchone()[0]
                )
                normalized_captured_at_ms = timeline_start_ms + prior_duration_ms
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
                "captured_at_ms": normalized_captured_at_ms,
                **source_range,
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
                "sample_count, duration_ms, file_size_bytes, status, captured_at_ms, created_at_ms, "
                "source_sequence_start, source_sequence_end, source_timestamp_start_ms, "
                "source_timestamp_end_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?, ?, ?, ?)",
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
                    normalized_captured_at_ms,
                    now_ms,
                    source_range["source_sequence_start"],
                    source_range["source_sequence_end"],
                    source_range["source_timestamp_start_ms"],
                    source_range["source_timestamp_end_ms"],
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
                meeting_id = self._interrupt_recording_row_locked(
                    row,
                    now_ms=now_ms,
                    error_class="capture_lease_expired",
                )
                if meeting_id is not None and meeting_id not in recovered_meeting_ids:
                    recovered_meeting_ids.append(meeting_id)
        return recovered_meeting_ids

    def recover_abandoned_recording_leases(
        self,
        *,
        expected_recordings: list[Mapping[str, Any]],
        now_ms: int,
    ) -> list[str]:
        """Interrupt exact capture generations abandoned by a prior runtime."""

        now_ms = max(0, int(now_ms))
        recovered_meeting_ids: list[str] = []
        with self._write_transaction():
            for expected in expected_recordings:
                meeting_id = _required(str(expected.get("meeting_id") or ""), "meeting_id")
                track = _required(str(expected.get("track") or ""), "track")
                lease_owner = _required(
                    str(expected.get("lease_owner") or ""),
                    "lease_owner",
                )
                epoch = int(expected.get("epoch") or 0)
                capture_generation = int(expected.get("capture_generation") or 0)
                if epoch < 0 or capture_generation <= 0:
                    raise ValueError("recording recovery identity is invalid")
                row = self._conn.execute(
                    "SELECT * FROM recording_sessions WHERE meeting_id = ? AND track = ? "
                    "AND epoch = ? AND status = 'active' AND capture_generation = ? "
                    "AND lease_owner = ? AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                    "WHERE t.meeting_id = recording_sessions.meeting_id)",
                    (meeting_id, track, epoch, capture_generation, lease_owner),
                ).fetchone()
                if row is None:
                    continue
                recovered_id = self._interrupt_recording_row_locked(
                    row,
                    now_ms=now_ms,
                    error_class="runtime_restarted",
                )
                if recovered_id is not None and recovered_id not in recovered_meeting_ids:
                    recovered_meeting_ids.append(recovered_id)
        return recovered_meeting_ids

    def _interrupt_recording_row_locked(
        self,
        row: sqlite3.Row,
        *,
        now_ms: int,
        error_class: str,
    ) -> str | None:
        meeting_id = str(row["meeting_id"])
        updated = self._conn.execute(
            "UPDATE recording_sessions SET status = 'interrupted', lease_owner = NULL, "
            "lease_until_ms = NULL, error_class = ?, sealed_at_ms = ?, updated_at_ms = ? "
            "WHERE meeting_id = ? AND track = ? AND epoch = ? AND status = 'active' "
            "AND capture_generation = ? AND lease_owner = ?",
            (
                error_class,
                now_ms,
                now_ms,
                meeting_id,
                row["track"],
                int(row["epoch"]),
                int(row["capture_generation"]),
                row["lease_owner"],
            ),
        )
        if updated.rowcount != 1:
            return None
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
        return meeting_id

    def list_active_recording_sessions(self) -> list[dict[str, Any]]:
        """Return active captures so a fresh packaged runtime can fence its predecessor."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recording_sessions WHERE status = 'active' "
                "AND lease_owner IS NOT NULL AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t "
                "WHERE t.meeting_id = recording_sessions.meeting_id) "
                "ORDER BY meeting_id, track, epoch"
            ).fetchall()
        return [self._recording_session_dict(row) for row in rows]

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
        deletion_scope: str = "all",
        requested_by: str = "user",
        retention_policy: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        deletion_scope = _validated_deletion_scope(deletion_scope)
        requested_by = _validated_deletion_trigger(requested_by)
        normalized_retention_policy = (
            _validated_retention_policy(retention_policy) if retention_policy is not None else None
        )
        if requested_by == "retention" and normalized_retention_policy not in RETENTION_POLICY_DAYS:
            raise ValueError("retention deletion requires an automatic retention policy")
        paths = _validated_managed_paths(
            meeting_id=meeting_id,
            deletion_scope=deletion_scope,
            managed_paths=managed_paths,
        )
        now_ms = max(0, int(now_ms))
        normalized_idempotency_key = _required(
            idempotency_key or f"meeting.delete:{deletion_scope}:{meeting_id}",
            "idempotency_key",
        )
        job_id = (
            _stable_id("delete", meeting_id)
            if deletion_scope == "all" and normalized_idempotency_key == f"meeting.delete:all:{meeting_id}"
            else _stable_id("delete", meeting_id, deletion_scope, normalized_idempotency_key)
        )
        with self._write_transaction():
            existing = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE idempotency_key = ?",
                (normalized_idempotency_key,),
            ).fetchone()
            if existing is not None:
                expected_identity = {
                    "meeting_id": meeting_id,
                    "deletion_scope": deletion_scope,
                    "requested_by": requested_by,
                    "retention_policy": normalized_retention_policy,
                    "paths_json": _json_dump(paths),
                }
                conflicts = {
                    field: (existing[field], value)
                    for field, value in expected_identity.items()
                    if existing[field] != value
                }
                if conflicts:
                    raise ValueError(f"deletion idempotency key was reused with conflicting data: {conflicts}")
                return self._deletion_job_dict(existing)
            if deletion_scope == "all":
                self._conn.execute(
                    "INSERT OR IGNORE INTO meeting_tombstones ("
                    "meeting_id, deletion_job_id, created_at_ms) VALUES (?, ?, ?)",
                    (meeting_id, job_id, now_ms),
                )
            self._conn.execute(
                "INSERT INTO deletion_jobs ("
                "id, meeting_id, deletion_scope, requested_by, retention_policy, "
                "idempotency_key, status, paths_json, attempts, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)",
                (
                    job_id,
                    meeting_id,
                    deletion_scope,
                    requested_by,
                    normalized_retention_policy,
                    normalized_idempotency_key,
                    _json_dump(paths),
                    now_ms,
                    now_ms,
                ),
            )
            self._append_governance_audit_locked(
                event_type="data_deletion.requested",
                meeting_id=meeting_id,
                deletion_job_id=job_id,
                deletion_scope=deletion_scope,
                requested_by=requested_by,
                retention_policy=normalized_retention_policy,
                occurred_at_ms=now_ms,
                idempotency_key=f"data_deletion.requested:{normalized_idempotency_key}",
                payload={"managed_path_count": len(paths)},
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
        deletion_scopes: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if statuses:
            normalized = tuple(_required(status, "status") for status in statuses)
            invalid = sorted(set(normalized) - {"pending", "running", "completed", "failed"})
            if invalid:
                raise ValueError(f"unsupported deletion job statuses: {invalid}")
            clauses.append(f"status IN ({','.join('?' for _ in normalized)})")
            parameters.extend(normalized)
        if deletion_scopes:
            normalized_scopes = tuple(_validated_deletion_scope(scope) for scope in deletion_scopes)
            clauses.append(f"deletion_scope IN ({','.join('?' for _ in normalized_scopes)})")
            parameters.extend(normalized_scopes)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM deletion_jobs" + where + " ORDER BY created_at_ms, id",
                parameters,
            ).fetchall()
        return [self._deletion_job_dict(row) for row in rows]

    def get_deletion_job(self, job_id: str) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"deletion job not found: {job_id}")
        return self._deletion_job_dict(row)

    def mark_deletion_running(self, *, job_id: str, now_ms: int) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            existing = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"deletion job not found: {job_id}")
            if existing["status"] in {"running", "completed"}:
                return self._deletion_job_dict(existing)
            result = self._conn.execute(
                "UPDATE deletion_jobs SET status = 'running', attempts = attempts + 1, "
                "updated_at_ms = ?, error_class = NULL WHERE id = ? "
                "AND status IN ('pending', 'failed')",
                (now_ms, job_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"deletion job is not runnable: {job_id}")
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            self._append_governance_audit_locked(
                event_type="data_deletion.started",
                meeting_id=str(row["meeting_id"]),
                deletion_job_id=job_id,
                deletion_scope=str(row["deletion_scope"]),
                requested_by=str(row["requested_by"]),
                retention_policy=row["retention_policy"],
                occurred_at_ms=now_ms,
                idempotency_key=f"data_deletion.started:{job_id}:{int(row['attempts'])}",
                payload={"attempt": int(row["attempts"])},
            )
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
            existing = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"deletion job not found: {job_id}")
            if existing["status"] == "completed":
                return self._deletion_job_dict(existing)
            self._conn.execute(
                "UPDATE deletion_jobs SET status = 'failed', error_class = ?, "
                "updated_at_ms = ? WHERE id = ? AND status = 'running'",
                (error_class, now_ms, job_id),
            )
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row["status"] == "failed":
                self._append_governance_audit_locked(
                    event_type="data_deletion.failed",
                    meeting_id=str(row["meeting_id"]),
                    deletion_job_id=job_id,
                    deletion_scope=str(row["deletion_scope"]),
                    requested_by=str(row["requested_by"]),
                    retention_policy=row["retention_policy"],
                    occurred_at_ms=now_ms,
                    idempotency_key=f"data_deletion.failed:{job_id}:{int(row['attempts'])}",
                    payload={"attempt": int(row["attempts"]), "error_class": error_class},
                )
        return self._deletion_job_dict(row)

    def _purge_derived_data_locked(self, meeting_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        counts["review_document_revisions"] = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM review_document_revisions WHERE document_id IN ("
                "SELECT document_id FROM review_documents WHERE meeting_id = ?) ",
                (meeting_id,),
            ).fetchone()[0]
        )
        for table in (
            "suggestions",
            "minutes",
            "approach_artifacts",
            "search_documents",
            "review_documents",
            "meeting_entities",
            "jobs",
            "speaker_attributions",
            "speaker_turns",
            "speaker_runs",
        ):
            result = self._conn.execute(
                f"DELETE FROM {table} WHERE meeting_id = ?",
                (meeting_id,),
            )
            counts[table] = int(result.rowcount)
        result = self._conn.execute(
            "DELETE FROM meeting_events WHERE meeting_id = ? AND ("
            "aggregate_type IN ('suggestion', 'meeting_entity', 'meeting_intelligence', "
            "'review_document') OR type LIKE 'suggestion.%' OR "
            "type LIKE 'review_document.%' OR type IN ("
            "'meeting.topic.updated', 'meeting.open_question.updated', "
            "'meeting.decision.updated', 'meeting.action_item.updated', "
            "'meeting.risk.updated', 'meeting.intelligence.applied', "
            "'meeting.review_job.retry_requested', 'meeting.minutes.ready', "
            "'meeting.approach.ready', 'meeting.index.ready', "
            "'transcript.segment.speaker_revised'))",
            (meeting_id,),
        )
        counts["derived_events"] = int(result.rowcount)
        return counts

    def _purge_recording_data_locked(self, meeting_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in (
            "recording_import_jobs",
            "recording_derivations",
            "recording_exports",
            "recording_sessions",
            "audio_chunks",
        ):
            result = self._conn.execute(
                f"DELETE FROM {table} WHERE meeting_id = ?",
                (meeting_id,),
            )
            counts[table] = int(result.rowcount)
        result = self._conn.execute(
            "DELETE FROM meeting_events WHERE meeting_id = ? AND type LIKE 'recording.%'",
            (meeting_id,),
        )
        counts["recording_events"] = int(result.rowcount)
        return counts

    def _purge_transcript_data_locked(self, meeting_id: str) -> dict[str, int]:
        counts = self._purge_derived_data_locked(meeting_id)
        for table in (
            "semantic_paragraph_checkpoints",
            "semantic_paragraphs",
            "asr_checkpoints",
            "speaker_attributions",
            "transcript_segments",
            "meeting_speakers",
        ):
            result = self._conn.execute(
                f"DELETE FROM {table} WHERE meeting_id = ?",
                (meeting_id,),
            )
            counts[table] = int(result.rowcount)
        result = self._conn.execute(
            "DELETE FROM meeting_events WHERE meeting_id = ? AND type LIKE 'transcript.%'",
            (meeting_id,),
        )
        counts["transcript_events"] = int(result.rowcount)
        return counts

    def _purge_all_meeting_data_locked(self, meeting_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        counts["review_document_revisions"] = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM review_document_revisions WHERE document_id IN ("
                "SELECT document_id FROM review_documents WHERE meeting_id = ?)",
                (meeting_id,),
            ).fetchone()[0]
        )
        for table in (
            "suggestions",
            "jobs",
            "recording_import_jobs",
            "meeting_entities",
            "minutes",
            "approach_artifacts",
            "search_documents",
            "review_documents",
            "recording_derivations",
            "recording_exports",
            "recording_sessions",
            "audio_chunks",
            "semantic_paragraph_checkpoints",
            "semantic_paragraphs",
            "asr_checkpoints",
            "speaker_attributions",
            "speaker_turns",
            "speaker_runs",
            "transcript_segments",
            "meeting_speakers",
            "meeting_events",
        ):
            result = self._conn.execute(
                f"DELETE FROM {table} WHERE meeting_id = ?",
                (meeting_id,),
            )
            counts[table] = int(result.rowcount)
        result = self._conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        counts["meetings"] = int(result.rowcount)
        return counts

    def complete_deletion_and_purge(
        self,
        *,
        job_id: str,
        now_ms: int,
        managed_path_results: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        job_id = _required(job_id, "job_id")
        now_ms = max(0, int(now_ms))
        normalized_path_results = {
            _required(path, "managed_path"): _required(status, "managed_path_status")
            for path, status in dict(managed_path_results or {}).items()
        }
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"deletion job not found: {job_id}")
            if row["status"] == "completed":
                return self._deletion_job_dict(row)
            meeting_id = str(row["meeting_id"])
            deletion_scope = _validated_deletion_scope(row["deletion_scope"])
            expected_managed_paths = set(json.loads(row["paths_json"]))
            unexpected_results = set(normalized_path_results) - expected_managed_paths
            if unexpected_results:
                raise ValueError(
                    f"managed_path_results contains paths outside the deletion job: {sorted(unexpected_results)}"
                )
            if deletion_scope == "recording":
                deleted_counts = self._purge_recording_data_locked(meeting_id)
            elif deletion_scope == "derived":
                deleted_counts = self._purge_derived_data_locked(meeting_id)
            elif deletion_scope == "transcript":
                deleted_counts = self._purge_transcript_data_locked(meeting_id)
            else:
                deleted_counts = self._purge_all_meeting_data_locked(meeting_id)
            if deletion_scope != "all":
                self._conn.execute(
                    "UPDATE meetings SET revision = revision + 1, updated_at_ms = ? WHERE id = ?",
                    (now_ms, meeting_id),
                )
            result_payload = {
                "deletion_scope": deletion_scope,
                "deleted_counts": deleted_counts,
                "managed_path_results": normalized_path_results,
            }
            self._conn.execute(
                "UPDATE deletion_jobs SET status = 'completed', error_class = NULL, "
                "result_json = ?, completed_at_ms = ?, updated_at_ms = ? WHERE id = ?",
                (_json_dump(result_payload), now_ms, now_ms, job_id),
            )
            self._append_governance_audit_locked(
                event_type="data_deletion.completed",
                meeting_id=meeting_id,
                deletion_job_id=job_id,
                deletion_scope=deletion_scope,
                requested_by=str(row["requested_by"]),
                retention_policy=row["retention_policy"],
                occurred_at_ms=now_ms,
                idempotency_key=f"data_deletion.completed:{job_id}",
                payload=result_payload,
            )
            completed = self._conn.execute(
                "SELECT * FROM deletion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._deletion_job_dict(completed)

    def get_data_governance_settings(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT retention_policy, updated_at_ms FROM data_governance_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"retention_policy": DEFAULT_RETENTION_POLICY, "updated_at_ms": 0}
        return {
            "retention_policy": str(row["retention_policy"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def get_retention_policy(self) -> str:
        return str(self.get_data_governance_settings()["retention_policy"])

    def set_retention_policy(
        self,
        *,
        retention_policy: str,
        now_ms: int,
        requested_by: str = "user",
    ) -> dict[str, Any]:
        retention_policy = _validated_retention_policy(retention_policy)
        requested_by = _validated_deletion_trigger(requested_by)
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT retention_policy, updated_at_ms FROM data_governance_settings WHERE id = 1"
            ).fetchone()
            previous_policy = str(row["retention_policy"]) if row is not None else DEFAULT_RETENTION_POLICY
            if previous_policy == retention_policy:
                return {
                    "retention_policy": retention_policy,
                    "updated_at_ms": int(row["updated_at_ms"]) if row is not None else 0,
                    "updated": False,
                }
            self._conn.execute(
                "INSERT INTO data_governance_settings (id, retention_policy, updated_at_ms) "
                "VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "retention_policy = excluded.retention_policy, "
                "updated_at_ms = excluded.updated_at_ms",
                (retention_policy, now_ms),
            )
            self._append_governance_audit_locked(
                event_type="retention_policy.updated",
                requested_by=requested_by,
                retention_policy=retention_policy,
                occurred_at_ms=now_ms,
                idempotency_key=(f"retention_policy.updated:{previous_policy}:{retention_policy}:{now_ms}"),
                payload={
                    "previous_policy": previous_policy,
                    "retention_policy": retention_policy,
                },
            )
        return {
            "retention_policy": retention_policy,
            "updated_at_ms": now_ms,
            "updated": True,
        }

    def list_data_governance_audit_events(
        self,
        *,
        meeting_id: str | None = None,
        deletion_job_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = int(limit)
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        parameters: list[Any] = []
        if meeting_id is not None:
            clauses.append("meeting_id = ?")
            parameters.append(_required(meeting_id, "meeting_id"))
        if deletion_job_id is not None:
            clauses.append("deletion_job_id = ?")
            parameters.append(_required(deletion_job_id, "deletion_job_id"))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM data_governance_audit_events" + where + " ORDER BY occurred_at_ms, rowid LIMIT ?",
                parameters,
            ).fetchall()
        return [self._data_governance_audit_dict(row) for row in rows]

    def list_meetings_due_for_retention(
        self,
        *,
        retention_policy: str,
        now_ms: int,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        retention_policy = _validated_retention_policy(retention_policy)
        if retention_policy not in RETENTION_POLICY_DAYS:
            return []
        now_ms = max(0, int(now_ms))
        limit = int(limit)
        if not 1 <= limit <= 5_000:
            raise ValueError("limit must be between 1 and 5000")
        cutoff_at_ms = now_ms - RETENTION_POLICY_DAYS[retention_policy] * 24 * 60 * 60 * 1_000
        with self._lock:
            rows = self._conn.execute(
                "SELECT *, COALESCE(ended_at_ms, updated_at_ms, created_at_ms) AS retention_anchor_ms "
                "FROM meetings WHERE state IN ('ended', 'interrupted') "
                "AND COALESCE(ended_at_ms, updated_at_ms, created_at_ms) <= ? "
                "AND NOT EXISTS (SELECT 1 FROM meeting_tombstones t WHERE t.meeting_id = meetings.id) "
                "ORDER BY retention_anchor_ms, id LIMIT ?",
                (cutoff_at_ms, limit),
            ).fetchall()
        return [
            {
                **self._meeting_dict(row),
                "retention_anchor_ms": int(row["retention_anchor_ms"]),
                "retention_cutoff_ms": cutoff_at_ms,
            }
            for row in rows
        ]

    def claim_retention_run(self, *, now_ms: int) -> dict[str, Any]:
        """Claim one real retention pass, with a hard 24-hour persisted cadence."""

        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            settings = self._conn.execute(
                "SELECT retention_policy FROM data_governance_settings WHERE id = 1"
            ).fetchone()
            retention_policy = str(settings["retention_policy"] if settings is not None else DEFAULT_RETENTION_POLICY)
            if retention_policy not in RETENTION_POLICY_DAYS:
                return {
                    "claimed": False,
                    "reason": "automatic_retention_disabled",
                    "retention_policy": retention_policy,
                }
            latest = self._conn.execute(
                "SELECT * FROM retention_runs ORDER BY created_at_ms DESC, id DESC LIMIT 1"
            ).fetchone()
            if latest is not None:
                elapsed_ms = now_ms - int(latest["created_at_ms"])
                if elapsed_ms < MIN_RETENTION_RUN_INTERVAL_MS:
                    return {
                        "claimed": False,
                        "reason": (
                            "already_running" if latest["status"] == "running" else "minimum_interval_not_elapsed"
                        ),
                        "retention_policy": retention_policy,
                        "next_run_at_ms": int(latest["created_at_ms"]) + MIN_RETENTION_RUN_INTERVAL_MS,
                        "last_run": self._retention_run_dict(latest),
                    }
                if latest["status"] == "running":
                    self._conn.execute(
                        "UPDATE retention_runs SET status = 'failed', error_count = MAX(error_count, 1), "
                        "completed_at_ms = ?, updated_at_ms = ? WHERE id = ? AND status = 'running'",
                        (now_ms, now_ms, latest["id"]),
                    )
                    self._append_governance_audit_locked(
                        event_type="retention_run.expired",
                        requested_by="retention",
                        retention_policy=str(latest["retention_policy"]),
                        occurred_at_ms=now_ms,
                        idempotency_key=f"retention_run.expired:{latest['id']}",
                        payload={"run_id": str(latest["id"])},
                    )
            cutoff_at_ms = now_ms - RETENTION_POLICY_DAYS[retention_policy] * 24 * 60 * 60 * 1_000
            run_id = _stable_id(
                "retention_run",
                retention_policy,
                str(now_ms // MIN_RETENTION_RUN_INTERVAL_MS),
            )
            self._conn.execute(
                "INSERT INTO retention_runs ("
                "id, retention_policy, status, cutoff_at_ms, candidate_count, "
                "deletion_job_count, error_count, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, 'running', ?, 0, 0, 0, ?, ?)",
                (run_id, retention_policy, cutoff_at_ms, now_ms, now_ms),
            )
            self._append_governance_audit_locked(
                event_type="retention_run.started",
                requested_by="retention",
                retention_policy=retention_policy,
                occurred_at_ms=now_ms,
                idempotency_key=f"retention_run.started:{run_id}",
                payload={"run_id": run_id, "cutoff_at_ms": cutoff_at_ms},
            )
            row = self._conn.execute(
                "SELECT * FROM retention_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return {"claimed": True, "run": self._retention_run_dict(row)}

    def complete_retention_run(
        self,
        *,
        run_id: str,
        candidate_count: int,
        deletion_job_count: int,
        error_count: int,
        now_ms: int,
    ) -> dict[str, Any]:
        run_id = _required(run_id, "run_id")
        candidate_count = max(0, int(candidate_count))
        deletion_job_count = max(0, int(deletion_job_count))
        error_count = max(0, int(error_count))
        now_ms = max(0, int(now_ms))
        status = "failed" if error_count else "completed"
        with self._write_transaction():
            existing = self._conn.execute(
                "SELECT * FROM retention_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"retention run not found: {run_id}")
            if existing["status"] in {"completed", "failed"}:
                return self._retention_run_dict(existing)
            self._conn.execute(
                "UPDATE retention_runs SET status = ?, candidate_count = ?, "
                "deletion_job_count = ?, error_count = ?, completed_at_ms = ?, "
                "updated_at_ms = ? WHERE id = ? AND status = 'running'",
                (
                    status,
                    candidate_count,
                    deletion_job_count,
                    error_count,
                    now_ms,
                    now_ms,
                    run_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM retention_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            self._append_governance_audit_locked(
                event_type=f"retention_run.{status}",
                requested_by="retention",
                retention_policy=str(row["retention_policy"]),
                occurred_at_ms=now_ms,
                idempotency_key=f"retention_run.{status}:{run_id}",
                payload={
                    "run_id": run_id,
                    "candidate_count": candidate_count,
                    "deletion_job_count": deletion_job_count,
                    "error_count": error_count,
                },
            )
        return self._retention_run_dict(row)

    def _canonical_transcript_state_locked(self, meeting_id: str) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT segment_id, transcript_seq, revision, normalized_text FROM transcript_segments "
            "WHERE meeting_id = ? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq",
            (meeting_id,),
        ).fetchall()
        material = [
            {
                "segment_id": row["segment_id"],
                "transcript_seq": int(row["transcript_seq"]),
                "revision": int(row["revision"]),
                "text": row["normalized_text"],
            }
            for row in rows
        ]
        return {
            "revision": sum(item["revision"] for item in material),
            "hash": hashlib.sha256(_json_dump(material).encode("utf-8")).hexdigest(),
            "latest": rows[-1] if rows else None,
        }

    @staticmethod
    def _review_document_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "document_id": row["document_id"],
            "meeting_id": row["meeting_id"],
            "document_kind": row["document_kind"],
            "source_transcript_revision": int(row["source_transcript_revision"]),
            "revision": int(row["revision"]),
            "ai_generated": {
                "version": int(row["ai_version"]),
                "content": json.loads(row["ai_content_json"]) if row["ai_content_json"] is not None else None,
            },
            "user_final": {
                "version": int(row["user_version"]),
                "content": json.loads(row["user_content_json"]) if row["user_content_json"] is not None else None,
                "modified": bool(row["user_modified"]),
            },
            "dirty_state": row["dirty_state"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def get_review_document(self, meeting_id: str, document_kind: str) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        document_kind = _validated_document_kind(document_kind)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_documents WHERE meeting_id = ? AND document_kind = ?",
                (meeting_id, document_kind),
            ).fetchone()
        if row is None:
            if not self.meeting_exists(meeting_id):
                raise KeyError(f"meeting not found: {meeting_id}")
            raise KeyError(f"review document not found: {meeting_id}/{document_kind}")
        return self._review_document_dict(row)

    def list_review_documents(self, meeting_id: str) -> dict[str, dict[str, Any]]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            rows = self._conn.execute(
                "SELECT * FROM review_documents WHERE meeting_id = ? "
                "AND document_kind IN ('minutes', 'decisions', 'action_items', 'risks', 'transcript') "
                "ORDER BY document_kind",
                (meeting_id,),
            ).fetchall()
        return {str(row["document_kind"]): self._review_document_dict(row) for row in rows}

    def list_review_document_revisions(
        self,
        meeting_id: str,
        document_kind: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        document = self.get_review_document(meeting_id, document_kind)
        limit = int(limit)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM review_document_revisions WHERE document_id = ? ORDER BY revision DESC LIMIT ?",
                (document["document_id"], limit),
            ).fetchall()
        return [
            {
                "document_id": row["document_id"],
                "revision": int(row["revision"]),
                "version_kind": row["version_kind"],
                "version": int(row["version"]),
                "author": row["author"],
                "source_transcript_revision": int(row["source_transcript_revision"]),
                "content": json.loads(row["content_json"]),
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        ]

    def save_user_final_document(
        self,
        *,
        meeting_id: str,
        document_kind: str,
        expected_revision: int,
        content: Any,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        document_kind = _validated_document_kind(document_kind)
        expected_revision = int(expected_revision)
        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        content_json = _review_content_json(content)
        now_ms = max(0, int(now_ms))
        document_id = _stable_id("review_document", meeting_id, document_kind)
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            row = self._conn.execute(
                "SELECT * FROM review_documents WHERE meeting_id = ? AND document_kind = ?",
                (meeting_id, document_kind),
            ).fetchone()
            current_revision = int(row["revision"]) if row is not None else 0
            if expected_revision != current_revision:
                raise ReviewDocumentConflict(
                    expected_revision=expected_revision,
                    current_document=self._review_document_dict(row) if row is not None else None,
                )
            source_revision = int(self._canonical_transcript_state_locked(meeting_id)["revision"])
            revision = current_revision + 1
            user_version = int(row["user_version"]) + 1 if row is not None else 1
            created_at_ms = int(row["created_at_ms"]) if row is not None else now_ms
            self._conn.execute(
                "INSERT INTO review_documents (document_id, meeting_id, document_kind, "
                "source_transcript_revision, revision, ai_version, user_version, ai_content_json, "
                "user_content_json, user_modified, dirty_state, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, NULL, ?, 1, 'saved', ?, ?) "
                "ON CONFLICT(meeting_id, document_kind) DO UPDATE SET "
                "source_transcript_revision = excluded.source_transcript_revision, "
                "revision = excluded.revision, user_version = excluded.user_version, "
                "user_content_json = excluded.user_content_json, user_modified = 1, "
                "dirty_state = 'saved', updated_at_ms = excluded.updated_at_ms",
                (
                    document_id,
                    meeting_id,
                    document_kind,
                    source_revision,
                    revision,
                    user_version,
                    content_json,
                    created_at_ms,
                    now_ms,
                ),
            )
            self._conn.execute(
                "INSERT INTO review_document_revisions (document_id, revision, version_kind, version, "
                "author, source_transcript_revision, content_json, created_at_ms) "
                "VALUES (?, ?, 'user_final', ?, 'user', ?, ?, ?)",
                (document_id, revision, user_version, source_revision, content_json, now_ms),
            )
            stored = self._conn.execute(
                "SELECT * FROM review_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="review_document.user_final.saved",
                aggregate_type="review_document",
                aggregate_id=document_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"review_document.user_final:{document_id}:{revision}",
                payload={"document_kind": document_kind, "revision": revision, "user_version": user_version},
                correlation_id=meeting_id,
            )
        return self._review_document_dict(stored)

    def _save_ai_generated_document_locked(
        self,
        *,
        meeting_id: str,
        document_kind: str,
        content: Any,
        author: str,
        now_ms: int,
    ) -> dict[str, Any]:
        content_json = _review_content_json(content)
        document_id = _stable_id("review_document", meeting_id, document_kind)
        row = self._conn.execute(
            "SELECT * FROM review_documents WHERE meeting_id = ? AND document_kind = ?",
            (meeting_id, document_kind),
        ).fetchone()
        if row is not None:
            replay = self._conn.execute(
                "SELECT 1 FROM review_document_revisions WHERE document_id = ? "
                "AND version_kind = 'ai_generated' AND author = ?",
                (document_id, author),
            ).fetchone()
            if replay is not None:
                return self._review_document_dict(row)
        source_revision = int(self._canonical_transcript_state_locked(meeting_id)["revision"])
        created_at_ms = int(row["created_at_ms"]) if row is not None else now_ms
        current_revision = int(row["revision"]) if row is not None else 0
        ai_version = int(row["ai_version"]) + 1 if row is not None else 1
        initialize_user = row is None or row["user_content_json"] is None
        user_version = (int(row["user_version"]) if row is not None else 0) + int(initialize_user)
        ai_revision = current_revision + 1
        final_revision = ai_revision + int(initialize_user)
        self._conn.execute(
            "INSERT INTO review_documents (document_id, meeting_id, document_kind, "
            "source_transcript_revision, revision, ai_version, user_version, ai_content_json, "
            "user_content_json, user_modified, dirty_state, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'saved', ?, ?) "
            "ON CONFLICT(meeting_id, document_kind) DO UPDATE SET "
            "source_transcript_revision = excluded.source_transcript_revision, "
            "revision = excluded.revision, ai_version = excluded.ai_version, "
            "user_version = excluded.user_version, ai_content_json = excluded.ai_content_json, "
            "user_content_json = COALESCE(review_documents.user_content_json, excluded.user_content_json), "
            "updated_at_ms = excluded.updated_at_ms",
            (
                document_id,
                meeting_id,
                document_kind,
                source_revision,
                final_revision,
                ai_version,
                user_version,
                content_json,
                content_json if initialize_user else row["user_content_json"],
                created_at_ms,
                now_ms,
            ),
        )
        self._conn.execute(
            "INSERT INTO review_document_revisions (document_id, revision, version_kind, version, "
            "author, source_transcript_revision, content_json, created_at_ms) "
            "VALUES (?, ?, 'ai_generated', ?, ?, ?, ?, ?)",
            (document_id, ai_revision, ai_version, author, source_revision, content_json, now_ms),
        )
        if initialize_user:
            self._conn.execute(
                "INSERT INTO review_document_revisions (document_id, revision, version_kind, version, "
                "author, source_transcript_revision, content_json, created_at_ms) "
                "VALUES (?, ?, 'user_final', ?, 'system:initial_ai_copy', ?, ?, ?)",
                (document_id, final_revision, user_version, source_revision, content_json, now_ms),
            )
        stored = self._conn.execute("SELECT * FROM review_documents WHERE document_id = ?", (document_id,)).fetchone()
        return self._review_document_dict(stored)

    def save_ai_generated_document(
        self,
        *,
        meeting_id: str,
        document_kind: str,
        content: Any,
        author: str,
        now_ms: int,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        document_kind = _validated_document_kind(document_kind)
        author = _required(author, "author")
        now_ms = max(0, int(now_ms))
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            return self._save_ai_generated_document_locked(
                meeting_id=meeting_id,
                document_kind=document_kind,
                content=content,
                author=author,
                now_ms=now_ms,
            )

    def enqueue_review_job(
        self,
        *,
        meeting_id: str,
        kind: str,
        now_ms: int,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        kind = _required(kind, "kind").lower()
        if kind not in REVIEW_JOB_KINDS:
            raise ValueError(f"kind must be one of {sorted(REVIEW_JOB_KINDS)}")
        now_ms = max(0, int(now_ms))
        max_attempts = int(max_attempts)
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            transcript = self._canonical_transcript_state_locked(meeting_id)
            latest = transcript["latest"]
            if latest is None:
                raise ValueError("review job requires at least one transcript segment")
            active = self._conn.execute(
                "SELECT * FROM jobs WHERE meeting_id = ? AND kind = ? AND input_version = ? "
                "AND evidence_hash = ? AND status IN ('pending', 'running', 'retry_wait') "
                "ORDER BY created_at_ms DESC, id DESC LIMIT 1",
                (meeting_id, kind, transcript["revision"], transcript["hash"]),
            ).fetchone()
            if active is not None:
                return {"created": False, "job": self._job_dict(active)}
            generation = int(
                self._conn.execute(
                    "SELECT COUNT(*) + 1 FROM jobs WHERE meeting_id = ? AND kind = ?",
                    (meeting_id, kind),
                ).fetchone()[0]
            )
            job_id = _stable_id("job", meeting_id, kind, "manual", str(generation))
            self._conn.execute(
                "INSERT INTO jobs (id, meeting_id, kind, status, priority, input_transcript_seq, "
                "input_version, evidence_segment_id, evidence_hash, generation_id, idempotency_key, "
                "attempts, max_attempts, next_attempt_at_ms, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    job_id,
                    meeting_id,
                    kind,
                    {"minutes": 60, "approach": 50, "index": 40}[kind],
                    int(latest["transcript_seq"]),
                    int(transcript["revision"]),
                    str(latest["segment_id"]),
                    str(transcript["hash"]),
                    f"review:{kind}:{generation}",
                    f"review.retry:{meeting_id}:{kind}:{generation}",
                    max_attempts,
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
            self._append_event_locked(
                meeting_id=meeting_id,
                event_type="meeting.review_job.retry_requested",
                aggregate_type="job",
                aggregate_id=job_id,
                occurred_at_ms=now_ms,
                idempotency_key=f"meeting.review_job.retry_requested:{job_id}",
                payload={"job_id": job_id, "kind": kind, "input_version": transcript["revision"]},
                correlation_id=meeting_id,
            )
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return {"created": True, "job": self._job_dict(row)}

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
        structured_payload = dict(structured) if structured is not None else {}
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
            self._save_ai_generated_document_locked(
                meeting_id=meeting_id,
                document_kind="minutes",
                content={
                    "markdown": markdown,
                    "structured": dict(structured) if structured is not None else None,
                    "status": "degraded" if degraded else "ready",
                },
                author=f"ai:{job_id}",
                now_ms=now_ms,
            )
            self._save_ai_generated_document_locked(
                meeting_id=meeting_id,
                document_kind="decisions",
                content={
                    "decisions": [
                        {
                            "id": f"decision-ai-{index}",
                            "text": str(value).strip(),
                            "status": "confirmed",
                            "evidence_segment_id": None,
                        }
                        for index, value in enumerate(structured_payload.get("decisions") or [])
                        if str(value).strip()
                    ],
                },
                author=f"ai:{job_id}:decisions",
                now_ms=now_ms,
            )
            self._save_ai_generated_document_locked(
                meeting_id=meeting_id,
                document_kind="action_items",
                content={
                    "action_items": [
                        {
                            "id": f"action-ai-{index}",
                            "text": str(item.get("item") or item.get("text") or "").strip(),
                            "status": "open",
                            "evidence_segment_id": None,
                            "owner": item.get("owner"),
                            "deadline": item.get("deadline"),
                        }
                        for index, item in enumerate(structured_payload.get("action_items") or [])
                        if isinstance(item, Mapping) and str(item.get("item") or item.get("text") or "").strip()
                    ],
                },
                author=f"ai:{job_id}:action_items",
                now_ms=now_ms,
            )
            self._save_ai_generated_document_locked(
                meeting_id=meeting_id,
                document_kind="risks",
                content={
                    "risks": [
                        {
                            "id": f"risk-ai-{index}",
                            "text": str(value).strip(),
                            "status": "open",
                            "evidence_segment_id": None,
                            "mitigation": None,
                        }
                        for index, value in enumerate(structured_payload.get("risks") or [])
                        if str(value).strip()
                    ],
                },
                author=f"ai:{job_id}:risks",
                now_ms=now_ms,
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
                "SELECT * FROM transcript_segments WHERE meeting_id = ? "
                "AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq",
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
            self._save_ai_generated_document_locked(
                meeting_id=meeting_id,
                document_kind="transcript",
                content={
                    "segments": [
                        {
                            "segment_id": row["segment_id"],
                            "text": row["normalized_text"],
                            "started_at_ms": row["started_at_ms"],
                            "ended_at_ms": row["ended_at_ms"],
                        }
                        for row in rows
                    ],
                },
                author=f"ai:{job_id}",
                now_ms=now_ms,
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
                    "SELECT COUNT(*) FROM transcript_segments WHERE meeting_id = ? AND duplicate_of_segment_id IS NULL",
                    (meeting_id,),
                ).fetchone()[0]
            )
            source_duplicate_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM transcript_segments WHERE meeting_id = ? "
                    "AND duplicate_of_segment_id IS NOT NULL",
                    (meeting_id,),
                ).fetchone()[0]
            )
            segment_rows = list(
                reversed(
                    self._conn.execute(
                        "SELECT * FROM transcript_segments WHERE meeting_id = ? "
                        "AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT ?",
                        (meeting_id, segment_limit),
                    ).fetchall()
                )
            )
            paragraph_rows = self._conn.execute(
                "SELECT * FROM semantic_paragraphs WHERE meeting_id = ? ORDER BY start_ms, paragraph_id",
                (meeting_id,),
            ).fetchall()
            paragraph_checkpoint_rows = self._conn.execute(
                "SELECT paragraph_id, checkpoint_id FROM semantic_paragraph_checkpoints "
                "WHERE meeting_id = ? ORDER BY paragraph_id, ordinal",
                (meeting_id,),
            ).fetchall()
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
            review_document_rows = self._conn.execute(
                "SELECT * FROM review_documents WHERE meeting_id = ? "
                "AND document_kind IN ('minutes', 'decisions', 'action_items', 'risks', 'transcript') "
                "ORDER BY document_kind",
                (meeting_id,),
            ).fetchall()
            intelligence_event = self._conn.execute(
                "SELECT payload_json FROM meeting_events WHERE meeting_id = ? "
                "AND type = 'meeting.intelligence.applied' ORDER BY seq DESC LIMIT 1",
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
            import_job_row = self._conn.execute(
                "SELECT * FROM recording_import_jobs WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        meeting = self._meeting_dict(meeting_row) if meeting_row is not None else None
        follow_up: dict[str, Any] | None = None
        if intelligence_event is not None:
            intelligence_payload = json.loads(intelligence_event["payload_json"] or "{}")
            raw_follow_up = intelligence_payload.get("follow_up")
            if isinstance(raw_follow_up, Mapping):
                follow_up = dict(raw_follow_up)
        paragraph_checkpoint_ids: dict[str, list[str]] = {}
        for mapping in paragraph_checkpoint_rows:
            paragraph_checkpoint_ids.setdefault(str(mapping["paragraph_id"]), []).append(str(mapping["checkpoint_id"]))
        semantic_paragraphs = [
            self._semantic_paragraph_dict(
                row,
                checkpoint_ids=paragraph_checkpoint_ids.get(str(row["paragraph_id"]), []),
            )
            for row in paragraph_rows
        ]
        review_documents = {str(row["document_kind"]): self._review_document_dict(row) for row in review_document_rows}
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
        failed_generation_jobs = [
            job for job in jobs if job["kind"] in {"correction", "suggestion"} and job["status"] == "failed"
        ]
        latest_failed_generation_job = (
            max(
                failed_generation_jobs,
                key=lambda job: (int(job["updated_at_ms"]), str(job["id"])),
            )
            if failed_generation_jobs
            else None
        )
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
                "error_class": _public_job_error_class(job["error_class"]),
                "created_at_ms": job["created_at_ms"],
                "updated_at_ms": job["updated_at_ms"],
                "completed_at_ms": job["completed_at_ms"],
            }
        return {
            "meeting_id": meeting_id,
            "title": meeting["title"] if meeting is not None else None,
            "last_seq": last_seq,
            "segments": [self._segment_dict(row) for row in segment_rows],
            "semantic_paragraphs": semantic_paragraphs,
            "active_paragraph": next(
                (paragraph for paragraph in semantic_paragraphs if paragraph["status"] == "active"),
                None,
            ),
            "transcript_page": {
                "returned": len(segment_rows),
                "total": total_segments,
                "source_duplicate_count": source_duplicate_count,
                "has_more": total_segments > len(segment_rows),
                "first_seq": int(segment_rows[0]["transcript_seq"]) if segment_rows else None,
                "last_seq": int(segment_rows[-1]["transcript_seq"]) if segment_rows else None,
            },
            "suggestions": [self._suggestion_dict(row) for row in suggestion_rows],
            "jobs": [self._job_status_summary(job) for job in jobs],
            "current_topic": state["current_topic"],
            "open_questions": state["open_questions"],
            "follow_up": follow_up,
            "decision_candidates": state["decision_candidates"],
            "action_items": state["action_items"],
            "risks": state["risks"],
            "active_partial": None,
            "minutes": minutes,
            "approach_cards": json.loads(approach_row["cards_json"]) if approach_row is not None else [],
            "review_jobs": review_jobs,
            "import_job": self._import_job_dict(import_job_row) if import_job_row is not None else None,
            "documents": review_documents,
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
                    "state": "busy" if ai_busy else "error" if latest_failed_generation_job else "idle",
                    "label": (
                        "AI 正在处理" if ai_busy else "AI 处理失败" if latest_failed_generation_job else "AI 已同步"
                    ),
                    "error_class": (
                        _public_job_error_class(latest_failed_generation_job["error_class"])
                        if not ai_busy and latest_failed_generation_job is not None
                        else None
                    ),
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
        include_source_duplicates: bool = False,
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        after_transcript_seq = max(0, int(after_transcript_seq))
        limit = int(limit)
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._lock:
            duplicate_filter = "" if include_source_duplicates else "AND duplicate_of_segment_id IS NULL "
            rows = self._conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? "
                "AND transcript_seq > ? " + duplicate_filter + "ORDER BY transcript_seq LIMIT ?",
                (meeting_id, after_transcript_seq, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        segments = [self._segment_dict(row) for row in visible]
        return {
            "meeting_id": meeting_id,
            "after_transcript_seq": after_transcript_seq,
            "segments": segments,
            "include_source_duplicates": bool(include_source_duplicates),
            "has_more": has_more,
            "next_after_transcript_seq": (segments[-1]["transcript_seq"] if segments else after_transcript_seq),
        }

    def list_semantic_paragraphs(self, meeting_id: str) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            rows = self._conn.execute(
                "SELECT * FROM semantic_paragraphs WHERE meeting_id = ? ORDER BY start_ms, paragraph_id",
                (meeting_id,),
            ).fetchall()
            mappings = self._conn.execute(
                "SELECT paragraph_id, checkpoint_id FROM semantic_paragraph_checkpoints "
                "WHERE meeting_id = ? ORDER BY paragraph_id, ordinal",
                (meeting_id,),
            ).fetchall()
        checkpoint_ids: dict[str, list[str]] = {}
        for mapping in mappings:
            checkpoint_ids.setdefault(str(mapping["paragraph_id"]), []).append(str(mapping["checkpoint_id"]))
        return {
            "meeting_id": meeting_id,
            "paragraphs": [
                self._semantic_paragraph_dict(
                    row,
                    checkpoint_ids=checkpoint_ids.get(str(row["paragraph_id"]), []),
                )
                for row in rows
            ],
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

    def list_meetings_page(
        self,
        *,
        limit: int = 100,
        query: str = "",
        status: str = "all",
        before_updated_at_ms: int | None = None,
        before_meeting_id: str | None = None,
    ) -> dict[str, Any]:
        limit = int(limit)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        normalized_query = " ".join(str(query or "").split())
        normalized_status = str(status or "all").strip().lower()
        if normalized_status not in {"all", "live", "processing", "ready", "failed"}:
            raise ValueError("status must be all, live, processing, ready, or failed")
        clauses: list[str] = []
        parameters: list[Any] = []
        if normalized_query:
            escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("m.title LIKE ? ESCAPE '\\'")
            parameters.append(f"%{escaped}%")
        active_review = (
            "EXISTS(SELECT 1 FROM jobs j WHERE j.meeting_id = m.id "
            "AND j.kind IN ('minutes', 'approach', 'index') "
            "AND j.status IN ('pending', 'running', 'retry_wait'))"
        )
        failed_review = (
            "EXISTS(SELECT 1 FROM jobs j WHERE j.meeting_id = m.id "
            "AND j.kind IN ('minutes', 'approach', 'index') "
            "AND j.status IN ('failed', 'cancelled'))"
        )
        active_import = (
            "EXISTS(SELECT 1 FROM recording_import_jobs i WHERE i.meeting_id = m.id "
            "AND i.status IN ('pending', 'running', 'retry_wait'))"
        )
        failed_import = (
            "EXISTS(SELECT 1 FROM recording_import_jobs i WHERE i.meeting_id = m.id "
            "AND i.status IN ('failed', 'cancelled'))"
        )
        if normalized_status == "live":
            clauses.append("m.state = 'live'")
        elif normalized_status == "processing":
            clauses.append(f"({active_review} OR {active_import})")
        elif normalized_status == "failed":
            clauses.append(f"({failed_review} OR {failed_import})")
        elif normalized_status == "ready":
            clauses.append("m.state != 'live'")
            clauses.append(f"NOT ({active_review})")
            clauses.append(f"NOT ({failed_review})")
            clauses.append(f"NOT ({active_import})")
            clauses.append(f"NOT ({failed_import})")
        if before_updated_at_ms is not None or before_meeting_id is not None:
            if before_updated_at_ms is None or not str(before_meeting_id or "").strip():
                raise ValueError("history cursor requires both timestamp and meeting id")
            clauses.append("(m.updated_at_ms < ? OR (m.updated_at_ms = ? AND m.id < ?))")
            parameters.extend(
                [
                    max(0, int(before_updated_at_ms)),
                    max(0, int(before_updated_at_ms)),
                    _required(str(before_meeting_id), "before_meeting_id"),
                ]
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.*, "
                "(SELECT COUNT(*) FROM transcript_segments t WHERE t.meeting_id = m.id "
                "AND t.duplicate_of_segment_id IS NULL) "
                "AS segment_count, "
                "(SELECT COUNT(*) FROM suggestions s WHERE s.meeting_id = m.id "
                "AND s.status = 'committed') AS suggestion_count, "
                "(SELECT COALESCE(SUM(a.duration_ms), 0) FROM audio_chunks a "
                "WHERE a.meeting_id = m.id) AS audio_duration_ms, "
                "EXISTS(SELECT 1 FROM minutes n WHERE n.meeting_id = m.id) AS has_minutes "
                f"FROM meetings m {where} ORDER BY m.updated_at_ms DESC, m.id DESC LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
            visible = rows[:limit]
            meeting_ids = [str(row["id"]) for row in visible]
            job_rows = (
                self._conn.execute(
                    "SELECT * FROM jobs WHERE kind IN ('minutes', 'approach', 'index') "
                    f"AND meeting_id IN ({','.join('?' for _ in meeting_ids)}) "
                    "ORDER BY meeting_id, kind, created_at_ms DESC, id DESC",
                    meeting_ids,
                ).fetchall()
                if meeting_ids
                else []
            )
            import_job_rows = (
                self._conn.execute(
                    "SELECT * FROM recording_import_jobs "
                    f"WHERE meeting_id IN ({','.join('?' for _ in meeting_ids)}) "
                    "ORDER BY meeting_id",
                    meeting_ids,
                ).fetchall()
                if meeting_ids
                else []
            )
        review_jobs: dict[str, dict[str, dict[str, Any]]] = {}
        for job_row in job_rows:
            meeting_jobs = review_jobs.setdefault(str(job_row["meeting_id"]), {})
            kind = str(job_row["kind"])
            if kind not in meeting_jobs:
                meeting_jobs[kind] = self._job_status_summary(self._job_dict(job_row))
        import_jobs = {
            str(import_job_row["meeting_id"]): self._import_job_dict(import_job_row)
            for import_job_row in import_job_rows
        }
        meetings = [
            {
                **self._meeting_dict(row),
                "segment_count": int(row["segment_count"]),
                "suggestion_count": int(row["suggestion_count"]),
                "audio_duration_ms": int(row["audio_duration_ms"]),
                "has_minutes": bool(row["has_minutes"]),
                "review_jobs": review_jobs.get(str(row["id"]), {}),
                **({"import_job": import_jobs[str(row["id"])]} if str(row["id"]) in import_jobs else {}),
            }
            for row in visible
        ]
        last = visible[-1] if visible else None
        return {
            "meetings": meetings,
            "has_more": len(rows) > limit,
            "next_cursor": (
                {
                    "before_updated_at_ms": int(last["updated_at_ms"]),
                    "before_meeting_id": str(last["id"]),
                }
                if last is not None and len(rows) > limit
                else None
            ),
        }

    def list_meetings(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.list_meetings_page(limit=limit)["meetings"]

    @staticmethod
    def mixed_recording_identity(
        meeting_id: str,
        sources: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        normalized_sources = []
        for source in sorted(sources, key=lambda item: str(item.get("track_id") or item.get("track") or "")):
            track_id = _required(
                str(source.get("track_id") or source.get("track") or ""),
                "source.track_id",
            )
            normalized_sources.append(
                {
                    "track_id": track_id,
                    "source": track_id,
                    "epoch": int(source["epoch"]),
                    "started_at_ms": int(source["started_at_ms"]),
                    "duration_ms": int(source["duration_ms"]),
                    "sample_rate_hz": int(source["sample_rate_hz"]),
                    "output_relative_path": _required(
                        str(source.get("output_relative_path") or ""),
                        "source.output_relative_path",
                    ),
                    "output_sha256": _required(
                        str(source.get("output_sha256") or ""),
                        "source.output_sha256",
                    ),
                }
            )
        if {source["track_id"] for source in normalized_sources} != DUAL_RECORDING_TRACKS:
            raise ValueError("mixed recording requires microphone and system_audio sources")
        source_fingerprint = hashlib.sha256(_json_dump(normalized_sources).encode("utf-8")).hexdigest()
        asset_id = f"mixed_{source_fingerprint[:24]}"
        return {
            "asset_id": asset_id,
            "source_fingerprint": source_fingerprint,
            "sources": normalized_sources,
            "output_relative_path": (f"audio_assets/{meeting_id}/derived/mixed/{asset_id}.wav"),
        }

    def register_mixed_recording_derivation(
        self,
        *,
        meeting_id: str,
        sources: list[Mapping[str, Any]],
        output: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        identity = self.mixed_recording_identity(meeting_id, sources)
        now_ms = max(0, int(now_ms))
        output_relative_path = _required(str(output.get("relative_path") or ""), "output.relative_path")
        if output_relative_path != identity["output_relative_path"]:
            raise ValueError("mixed output path does not match its stable asset identity")
        output_sha256 = _required(str(output.get("sha256") or ""), "output.sha256")
        if not re.fullmatch(r"[a-f0-9]{64}", output_sha256):
            raise ValueError("mixed output sha256 is invalid")
        sample_rate_hz = int(output.get("sample_rate_hz") or 0)
        duration_ms = int(output.get("duration_ms") or 0)
        file_size_bytes = int(output.get("file_size_bytes") or 0)
        timeline_start_ms = max(0, int(output.get("timeline_start_ms") or 0))
        if min(sample_rate_hz, duration_ms, file_size_bytes) <= 0:
            raise ValueError("mixed output dimensions must be positive")

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            if self._conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise KeyError(f"meeting not found: {meeting_id}")
            for source in identity["sources"]:
                current = self._conn.execute(
                    "SELECT status, output_relative_path, output_sha256 FROM recording_sessions "
                    "WHERE meeting_id = ? AND track = ? AND epoch = ?",
                    (meeting_id, source["track_id"], source["epoch"]),
                ).fetchone()
                if (
                    current is None
                    or current["status"] != "ready"
                    or current["output_relative_path"] != source["output_relative_path"]
                    or current["output_sha256"] != source["output_sha256"]
                ):
                    raise RuntimeError(f"source track changed before mixed asset registration: {source['track_id']}")
            existing = self._conn.execute(
                "SELECT * FROM recording_derivations WHERE asset_id = ?",
                (identity["asset_id"],),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO recording_derivations ("
                    "asset_id, meeting_id, kind, derivation, status, source_fingerprint, "
                    "sources_json, output_relative_path, output_sha256, sample_rate_hz, duration_ms, "
                    "file_size_bytes, timeline_start_ms, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, 'mixed', 'local_pcm16_timeline_mix', 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        identity["asset_id"],
                        meeting_id,
                        identity["source_fingerprint"],
                        _json_dump(identity["sources"]),
                        output_relative_path,
                        output_sha256,
                        sample_rate_hz,
                        duration_ms,
                        file_size_bytes,
                        timeline_start_ms,
                        now_ms,
                        now_ms,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM recording_derivations WHERE asset_id = ?",
                    (identity["asset_id"],),
                ).fetchone()
                derivation = self._recording_derivation_dict(row)
                self._append_event_locked(
                    meeting_id=meeting_id,
                    event_type="recording.mixed.ready",
                    aggregate_type="recording_derivation",
                    aggregate_id=identity["asset_id"],
                    occurred_at_ms=now_ms,
                    idempotency_key=f"recording.mixed.ready:{identity['source_fingerprint']}",
                    payload=derivation,
                    correlation_id=meeting_id,
                )
            else:
                if (
                    existing["status"] != "ready"
                    or existing["output_relative_path"] != output_relative_path
                    or existing["output_sha256"] != output_sha256
                ):
                    raise ValueError("mixed recording identity conflicts with stored output")
                derivation = self._recording_derivation_dict(existing)
        return derivation

    def list_recording_derivations(self, meeting_id: str) -> list[dict[str, Any]]:
        meeting_id = _required(meeting_id, "meeting_id")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recording_derivations WHERE meeting_id = ? ORDER BY created_at_ms, asset_id",
                (meeting_id,),
            ).fetchall()
        return [self._recording_derivation_dict(row) for row in rows]

    def get_recording_derivation(self, meeting_id: str, asset_id: str) -> dict[str, Any]:
        meeting_id = _required(meeting_id, "meeting_id")
        asset_id = _required(asset_id, "asset_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recording_derivations WHERE meeting_id = ? AND asset_id = ?",
                (meeting_id, asset_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"recording derivation not found: {meeting_id}/{asset_id}")
        return self._recording_derivation_dict(row)

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
                "SELECT * FROM meeting_events WHERE meeting_id = ? AND seq > ? ORDER BY seq LIMIT ?",
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
                "SELECT * FROM meeting_events WHERE meeting_id = ? AND seq > ? ORDER BY seq LIMIT ?",
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
                "AND status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed')",
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
        kind = str(row["kind"])
        entity.update(
            {
                "confidence": float(row["confidence"] or 0.0),
                "evidence": json.loads(row["evidence_json"] or "{}"),
            }
        )
        if kind == "action_item":
            entity.update({"owner": row["owner"], "deadline": row["deadline"]})
        if kind == "risk":
            entity["mitigation"] = row["mitigation"]
        return entity

    @staticmethod
    def _recording_session_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "track": row["track"],
            "track_id": row["track"],
            "source": row["track"],
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
    def _import_job_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "meeting_id": row["meeting_id"],
            "status": row["status"],
            "stage": row["stage"],
            "progress": int(row["progress"]),
            "source_relative_path": row["source_relative_path"],
            "original_filename": row["original_filename"],
            "file_size_bytes": int(row["file_size_bytes"]),
            "attempts": int(row["attempts"]),
            "max_attempts": int(row["max_attempts"]),
            "next_attempt_at_ms": int(row["next_attempt_at_ms"]),
            "lease_owner": row["lease_owner"],
            "lease_until_ms": row["lease_until_ms"],
            "error_class": row["error_class"],
            "error_message": row["error_message"],
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _recording_export_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "meeting_id": row["meeting_id"],
            "track": row["track"],
            "track_id": row["track"],
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
    def _recording_derivation_dict(row: sqlite3.Row) -> dict[str, Any]:
        meeting_id = str(row["meeting_id"])
        asset_id = str(row["asset_id"])
        return {
            "asset_id": asset_id,
            "meeting_id": meeting_id,
            "kind": row["kind"],
            "derivation": row["derivation"],
            "status": row["status"],
            "source_fingerprint": row["source_fingerprint"],
            "sources": json.loads(row["sources_json"]),
            "output_relative_path": row["output_relative_path"],
            "output_sha256": row["output_sha256"],
            "sample_rate_hz": row["sample_rate_hz"],
            "duration_ms": row["duration_ms"],
            "file_size_bytes": row["file_size_bytes"],
            "timeline_start_ms": row["timeline_start_ms"],
            "error_class": row["error_class"],
            "playback_url": (
                f"/v2/meetings/{meeting_id}/audio/mixed/{asset_id}/content" if row["status"] == "ready" else None
            ),
            "retention_policy": "local_until_user_deletes",
            "remote_upload_used": False,
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _data_governance_audit_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "meeting_id": row["meeting_id"],
            "deletion_job_id": row["deletion_job_id"],
            "deletion_scope": row["deletion_scope"],
            "requested_by": row["requested_by"],
            "retention_policy": row["retention_policy"],
            "occurred_at_ms": int(row["occurred_at_ms"]),
            "idempotency_key": row["idempotency_key"],
            "payload": json.loads(row["payload_json"]),
        }

    @staticmethod
    def _retention_run_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "retention_policy": row["retention_policy"],
            "status": row["status"],
            "cutoff_at_ms": int(row["cutoff_at_ms"]),
            "candidate_count": int(row["candidate_count"]),
            "deletion_job_count": int(row["deletion_job_count"]),
            "error_count": int(row["error_count"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _deletion_job_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "meeting_id": row["meeting_id"],
            "deletion_scope": row["deletion_scope"],
            "requested_by": row["requested_by"],
            "retention_policy": row["retention_policy"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "managed_paths": json.loads(row["paths_json"]),
            "attempts": int(row["attempts"]),
            "error_class": row["error_class"],
            "result": json.loads(row["result_json"]) if row["result_json"] is not None else None,
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _normalize_audio_chunk_source_range(
        *,
        source_sequence_start: object,
        source_sequence_end: object,
        source_timestamp_start_ms: object,
        source_timestamp_end_ms: object,
    ) -> dict[str, int | None]:
        values = {
            "source_sequence_start": source_sequence_start,
            "source_sequence_end": source_sequence_end,
            "source_timestamp_start_ms": source_timestamp_start_ms,
            "source_timestamp_end_ms": source_timestamp_end_ms,
        }
        present = {name for name, value in values.items() if value is not None}
        if not present:
            return dict.fromkeys(values)
        if len(present) != len(values):
            raise ValueError(
                "native PCM source range requires sequence and timestamp start/end values"
            )
        try:
            normalized = {name: int(value) for name, value in values.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("native PCM source range values must be integers") from exc
        if any(value < 0 for value in normalized.values()):
            raise ValueError("native PCM source range values must be non-negative")
        if normalized["source_sequence_start"] > normalized["source_sequence_end"]:
            raise ValueError("native PCM source sequence range is reversed")
        if normalized["source_timestamp_start_ms"] > normalized["source_timestamp_end_ms"]:
            raise ValueError("native PCM source timestamp range is reversed")
        return normalized

    @staticmethod
    def _audio_chunk_dict(row: sqlite3.Row) -> dict[str, Any]:
        source_range = V2Persistence._normalize_audio_chunk_source_range(
            source_sequence_start=row["source_sequence_start"],
            source_sequence_end=row["source_sequence_end"],
            source_timestamp_start_ms=row["source_timestamp_start_ms"],
            source_timestamp_end_ms=row["source_timestamp_end_ms"],
        )
        return {
            "meeting_id": row["meeting_id"],
            "track": row["track"],
            "track_id": row["track"],
            "epoch": int(row["epoch"]),
            "chunk_seq": int(row["chunk_seq"]),
            "sequence": int(row["chunk_seq"]),
            "timestamp_ms": int(row["captured_at_ms"] or row["created_at_ms"]),
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "sample_rate_hz": int(row["sample_rate_hz"]),
            "sample_count": int(row["sample_count"]),
            "duration_ms": int(row["duration_ms"]),
            "file_size_bytes": int(row["file_size_bytes"]),
            "status": row["status"],
            "created_at_ms": int(row["created_at_ms"]),
            **source_range,
        }

    @staticmethod
    def _meeting_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "state": row["state"],
            "title": row["title"],
            "title_source": row["title_source"],
            "started_at_ms": row["started_at_ms"],
            "ended_at_ms": row["ended_at_ms"],
            "latest_seq": int(row["latest_seq"]),
            "revision": int(row["revision"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _speaker_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "speaker_id": row["speaker_id"],
            "speaker_label": row["speaker_label"],
            "ordinal": int(row["ordinal"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _speaker_run_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "run_id": row["run_id"],
            "source": row["source"],
            "model": row["model"],
            "status": row["status"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "completed_at_ms": row["completed_at_ms"],
        }

    @staticmethod
    def _speaker_turn_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "run_id": row["run_id"],
            "turn_id": row["turn_id"],
            "start_ms": int(row["start_ms"]),
            "end_ms": int(row["end_ms"]),
            "cluster_label": row["cluster_label"],
            "speaker_id": row["speaker_id"],
            "confidence": row["confidence"],
            "is_stable": bool(row["is_stable"]),
            "window_ids": json.loads(row["window_ids_json"] or "[]"),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _speaker_attribution_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "segment_id": row["segment_id"],
            "attribution_revision": int(row["attribution_revision"]),
            "run_id": row["run_id"],
            "speaker_id": row["speaker_id"],
            "confidence": row["confidence"],
            "source": row["source"],
            "reason": row["reason"],
            "created_at_ms": int(row["created_at_ms"]),
        }

    @staticmethod
    def _semantic_paragraph_dict(
        row: sqlite3.Row,
        *,
        checkpoint_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "meeting_id": row["meeting_id"],
            "paragraph_id": row["paragraph_id"],
            "revision": int(row["revision"]),
            "text": row["text"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "speaker_id": row["speaker_id"],
            "speaker_label": row["speaker_label"],
            "speaker_confidence": row["speaker_confidence"],
            "status": row["status"],
            "checkpoint_ids": list(checkpoint_ids),
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
            "source_track": row["source_track"],
            "duplicate_of_segment_id": row["duplicate_of_segment_id"],
            "source_duplicate_similarity": row["source_duplicate_similarity"],
            "speaker_id": row["speaker_id"],
            "speaker_label": row["speaker_label"],
            "speaker_confidence": row["speaker_confidence"],
            "speaker_attribution_revision": int(row["speaker_attribution_revision"] or 0),
            "speaker_attribution_source": row["speaker_attribution_source"],
            "speaker_attribution_reason": row["speaker_attribution_reason"],
            "revision": int(row["revision"]),
            "evidence_hash": row["evidence_hash"],
            "correction_status": row["correction_status"] or "pending",
            "correction_before_text": row["correction_before_text"],
            "correction_after_text": row["correction_after_text"],
            "correction_error_class": row["correction_error_class"],
            "correction_updated_at_ms": row["correction_updated_at_ms"],
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
            "error_class": _public_job_error_class(job["error_class"]),
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
