from __future__ import annotations

"""The single application SQLite schema bootstrap.

The low-level migration primitive deliberately knows nothing about application
tables.  This module owns the reviewed application registry and is the only
place where repository schema creation and evolution is declared.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from .sqlite_schema import (
    MIGRATION_HISTORY_TABLE,
    MigrationConnection,
    MigrationFailpoint,
    SchemaMigration,
    SchemaMigrationResult,
    migrate_sqlite_schema,
    migration_fingerprint,
    sqlite_schema_migration_lock,
    sql_migration,
)


APPLICATION_SCHEMA_VERSION = 3
APPLICATION_MAX_SUPPORTED_SCHEMA_VERSION = APPLICATION_SCHEMA_VERSION

_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
_FALLBACK_MEETING_TITLE_FORMAT = "%Y年%m月%d日 %H:%M 的会议"


def fallback_meeting_title(now_ms: int) -> str:
    """Return the deterministic user-visible title used for untitled meetings."""

    instant = datetime.fromtimestamp(max(0, int(now_ms)) / 1_000, tz=_SHANGHAI_TIMEZONE)
    return instant.strftime(_FALLBACK_MEETING_TITLE_FORMAT)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('live', 'ending', 'ended', 'interrupted')),
    title TEXT,
    title_source TEXT NOT NULL DEFAULT 'fallback' CHECK (
        title_source IN ('ai', 'fallback', 'import', 'migration', 'user')
    ),
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

CREATE TABLE IF NOT EXISTS meeting_speakers (
    meeting_id TEXT NOT NULL,
    speaker_id TEXT NOT NULL,
    speaker_label TEXT NOT NULL,
    label_source TEXT NOT NULL DEFAULT 'auto' CHECK (label_source IN ('auto', 'user')),
    label_locked INTEGER NOT NULL DEFAULT 0 CHECK (label_locked IN (0, 1)),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, speaker_id),
    UNIQUE (meeting_id, ordinal),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_meeting_speakers_order
    ON meeting_speakers(meeting_id, ordinal);

CREATE TABLE IF NOT EXISTS speaker_runs (
    meeting_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    PRIMARY KEY (meeting_id, run_id),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_speaker_runs_meeting
    ON speaker_runs(meeting_id, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS transcript_segments (
    meeting_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    final_id TEXT NOT NULL,
    transcript_seq INTEGER NOT NULL CHECK (transcript_seq > 0),
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    started_at_ms INTEGER,
    ended_at_ms INTEGER,
    source_track TEXT,
    duplicate_of_segment_id TEXT,
    source_duplicate_similarity REAL CHECK (
        source_duplicate_similarity IS NULL OR (
            source_duplicate_similarity >= 0 AND source_duplicate_similarity <= 1
        )
    ),
    speaker_id TEXT,
    speaker_label TEXT,
    speaker_confidence REAL CHECK (
        speaker_confidence IS NULL OR (speaker_confidence >= 0 AND speaker_confidence <= 1)
    ),
    speaker_attribution_revision INTEGER NOT NULL DEFAULT 0 CHECK (speaker_attribution_revision >= 0),
    speaker_attribution_source TEXT,
    speaker_attribution_reason TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    evidence_hash TEXT NOT NULL,
    correction_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        correction_status IN ('pending', 'processing', 'no_change', 'changed', 'failed_preserved_original')
    ),
    correction_before_text TEXT,
    correction_after_text TEXT,
    correction_error_class TEXT,
    correction_updated_at_ms INTEGER,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, segment_id),
    UNIQUE (meeting_id, final_id),
    UNIQUE (meeting_id, transcript_seq)
);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_meeting_order
    ON transcript_segments(meeting_id, transcript_seq);

CREATE TABLE IF NOT EXISTS speaker_turns (
    meeting_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
    cluster_label TEXT,
    speaker_id TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    is_stable INTEGER NOT NULL DEFAULT 1 CHECK (is_stable IN (0, 1)),
    window_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, run_id, turn_id),
    FOREIGN KEY (meeting_id, run_id) REFERENCES speaker_runs(meeting_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_speaker_turns_run
    ON speaker_turns(meeting_id, run_id, start_ms, turn_id);

CREATE TABLE IF NOT EXISTS speaker_attributions (
    meeting_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    attribution_revision INTEGER NOT NULL CHECK (attribution_revision > 0),
    run_id TEXT NOT NULL,
    speaker_id TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, segment_id, attribution_revision),
    FOREIGN KEY (meeting_id, segment_id)
        REFERENCES transcript_segments(meeting_id, segment_id) ON DELETE CASCADE,
    FOREIGN KEY (meeting_id, run_id)
        REFERENCES speaker_runs(meeting_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_speaker_attributions_segment
    ON speaker_attributions(meeting_id, segment_id, attribution_revision DESC);

CREATE INDEX IF NOT EXISTS idx_speaker_attributions_run
    ON speaker_attributions(meeting_id, run_id, created_at_ms);

CREATE TABLE IF NOT EXISTS asr_checkpoints (
    meeting_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    final_id TEXT NOT NULL,
    transcript_seq INTEGER NOT NULL CHECK (transcript_seq > 0),
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    started_at_ms INTEGER,
    ended_at_ms INTEGER,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, checkpoint_id),
    UNIQUE (meeting_id, final_id),
    UNIQUE (meeting_id, transcript_seq),
    FOREIGN KEY (meeting_id, checkpoint_id)
        REFERENCES transcript_segments(meeting_id, segment_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_paragraphs (
    meeting_id TEXT NOT NULL,
    paragraph_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    text TEXT NOT NULL,
    start_ms INTEGER,
    end_ms INTEGER,
    speaker_id TEXT,
    speaker_label TEXT,
    speaker_confidence REAL CHECK (
        speaker_confidence IS NULL OR (speaker_confidence >= 0 AND speaker_confidence <= 1)
    ),
    status TEXT NOT NULL CHECK (status IN ('active', 'stable')),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (meeting_id, paragraph_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_paragraphs_meeting_order
    ON semantic_paragraphs(meeting_id, start_ms, paragraph_id);

CREATE TABLE IF NOT EXISTS semantic_paragraph_checkpoints (
    meeting_id TEXT NOT NULL,
    paragraph_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (meeting_id, paragraph_id, checkpoint_id),
    UNIQUE (meeting_id, checkpoint_id),
    FOREIGN KEY (meeting_id, paragraph_id)
        REFERENCES semantic_paragraphs(meeting_id, paragraph_id)
        ON DELETE CASCADE,
    FOREIGN KEY (meeting_id, checkpoint_id)
        REFERENCES asr_checkpoints(meeting_id, checkpoint_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_entities (
    meeting_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN ('current_topic', 'open_question', 'decision_candidate', 'action_item', 'risk')
    ),
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    evidence_segment_ids_json TEXT NOT NULL,
    owner TEXT,
    deadline TEXT,
    mitigation TEXT,
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

CREATE TABLE IF NOT EXISTS review_documents (
    document_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    document_kind TEXT NOT NULL,
    source_transcript_revision INTEGER NOT NULL DEFAULT 0 CHECK (source_transcript_revision >= 0),
    revision INTEGER NOT NULL CHECK (revision > 0),
    ai_version INTEGER NOT NULL DEFAULT 0 CHECK (ai_version >= 0),
    user_version INTEGER NOT NULL DEFAULT 0 CHECK (user_version >= 0),
    ai_content_json TEXT,
    user_content_json TEXT,
    user_modified INTEGER NOT NULL DEFAULT 0 CHECK (user_modified IN (0, 1)),
    dirty_state TEXT NOT NULL DEFAULT 'saved' CHECK (dirty_state IN ('saved', 'stale')),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (meeting_id, document_kind),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_document_revisions (
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    version_kind TEXT NOT NULL CHECK (version_kind IN ('ai_generated', 'user_final')),
    version INTEGER NOT NULL CHECK (version > 0),
    author TEXT NOT NULL,
    source_transcript_revision INTEGER NOT NULL DEFAULT 0 CHECK (source_transcript_revision >= 0),
    content_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (document_id, revision),
    FOREIGN KEY (document_id) REFERENCES review_documents(document_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_review_documents_meeting
    ON review_documents(meeting_id, document_kind);
CREATE INDEX IF NOT EXISTS idx_review_document_revisions_history
    ON review_document_revisions(document_id, revision DESC);

CREATE TABLE IF NOT EXISTS recording_import_jobs (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')
    ),
    stage TEXT NOT NULL CHECK (
        stage IN ('reading', 'normalizing', 'transcribing', 'correcting', 'reviewing', 'completed')
    ),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    source_relative_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes > 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    next_attempt_at_ms INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until_ms INTEGER,
    error_class TEXT,
    error_message TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recording_import_jobs_status
    ON recording_import_jobs(status, updated_at_ms);

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
    captured_at_ms INTEGER,
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

CREATE TABLE IF NOT EXISTS recording_derivations (
    asset_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('mixed')),
    derivation TEXT NOT NULL CHECK (derivation IN ('local_pcm16_timeline_mix')),
    status TEXT NOT NULL CHECK (status IN ('ready', 'failed')),
    source_fingerprint TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    output_relative_path TEXT,
    output_sha256 TEXT,
    sample_rate_hz INTEGER,
    duration_ms INTEGER,
    file_size_bytes INTEGER,
    timeline_start_ms INTEGER,
    error_class TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (meeting_id, kind, source_fingerprint),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recording_derivations_meeting
    ON recording_derivations(meeting_id, created_at_ms, asset_id);

CREATE TABLE IF NOT EXISTS meeting_tombstones (
    meeting_id TEXT PRIMARY KEY,
    deletion_job_id TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deletion_jobs (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    deletion_scope TEXT NOT NULL DEFAULT 'all' CHECK (
        deletion_scope IN ('recording', 'derived', 'transcript', 'all')
    ),
    requested_by TEXT NOT NULL DEFAULT 'user' CHECK (
        requested_by IN ('user', 'retention', 'system')
    ),
    retention_policy TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    paths_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_class TEXT,
    result_json TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_deletion_jobs_pending
    ON deletion_jobs(status, updated_at_ms);

CREATE TABLE IF NOT EXISTS data_governance_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    retention_policy TEXT NOT NULL CHECK (
        retention_policy IN (
            'local_until_user_deletes', 'manual_only', '30_days', '90_days', '365_days'
        )
    ),
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS data_governance_audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    meeting_id TEXT,
    deletion_job_id TEXT,
    deletion_scope TEXT,
    requested_by TEXT NOT NULL,
    retention_policy TEXT,
    occurred_at_ms INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_governance_audit_meeting
    ON data_governance_audit_events(meeting_id, occurred_at_ms, id);

CREATE TABLE IF NOT EXISTS retention_runs (
    id TEXT PRIMARY KEY,
    retention_policy TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    cutoff_at_ms INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    deletion_job_count INTEGER NOT NULL DEFAULT 0 CHECK (deletion_job_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_retention_runs_latest
    ON retention_runs(created_at_ms DESC, id DESC);
"""


# Version 1 was written by the legacy repositories.  Keep these statements
# explicit so an empty database and a legacy database share one registry.
_LEGACY_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS asr_live_sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, created_at_ms INTEGER, last_activity_ms INTEGER, source TEXT, has_minutes INTEGER DEFAULT 0, has_audio INTEGER DEFAULT 0, suggestion_count INTEGER DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS pending_audio_cleanup (session_id TEXT PRIMARY KEY, audio_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS deleted_sessions (session_id TEXT PRIMARY KEY, deleted_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS app_settings (singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1), settings_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS llm_usage_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage_ledger(timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_session_timestamp ON llm_usage_ledger(session_id, timestamp_ms)",
)


def _complete_sql_statements(script: str) -> tuple[str, ...]:
    """Parse a reviewed SQL script using SQLite's statement completeness API."""

    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("application schema SQL contains an incomplete statement")
    if not statements:
        raise ValueError("application schema SQL contains no statements")
    return tuple(statements)


_V2_BASE_SCHEMA_STATEMENTS = _complete_sql_statements(_SCHEMA)

_V2_ADDITIVE_COLUMNS = (
    ("meetings", (("title_source", "TEXT NOT NULL DEFAULT 'fallback'"),)),
    (
        "meeting_speakers",
        (
            ("label_source", "TEXT NOT NULL DEFAULT 'auto'"),
            ("label_locked", "INTEGER NOT NULL DEFAULT 0"),
        ),
    ),
    ("suggestions", (("feedback", "TEXT"), ("feedback_at_ms", "INTEGER"))),
    ("jobs", (("deadline_at_ms", "INTEGER"),)),
    (
        "transcript_segments",
        (
            ("correction_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("correction_before_text", "TEXT"),
            ("correction_after_text", "TEXT"),
            ("correction_error_class", "TEXT"),
            ("correction_updated_at_ms", "INTEGER"),
            ("speaker_id", "TEXT"),
            ("speaker_label", "TEXT"),
            (
                "speaker_confidence",
                "REAL CHECK (speaker_confidence IS NULL OR "
                "(speaker_confidence >= 0 AND speaker_confidence <= 1))",
            ),
            ("speaker_attribution_revision", "INTEGER NOT NULL DEFAULT 0"),
            ("speaker_attribution_source", "TEXT"),
            ("speaker_attribution_reason", "TEXT"),
            ("source_track", "TEXT"),
            ("duplicate_of_segment_id", "TEXT"),
            (
                "source_duplicate_similarity",
                "REAL CHECK (source_duplicate_similarity IS NULL OR "
                "(source_duplicate_similarity >= 0 AND source_duplicate_similarity <= 1))",
            ),
        ),
    ),
    (
        "semantic_paragraphs",
        (
            ("speaker_id", "TEXT"),
            ("speaker_label", "TEXT"),
            (
                "speaker_confidence",
                "REAL CHECK (speaker_confidence IS NULL OR "
                "(speaker_confidence >= 0 AND speaker_confidence <= 1))",
            ),
        ),
    ),
    (
        "meeting_entities",
        (
            ("confidence", "REAL NOT NULL DEFAULT 0"),
            ("evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("owner", "TEXT"),
            ("deadline", "TEXT"),
            ("mitigation", "TEXT"),
            ("version", "INTEGER NOT NULL DEFAULT 1"),
            ("first_seen_seq", "INTEGER NOT NULL DEFAULT 1"),
            ("last_updated_seq", "INTEGER NOT NULL DEFAULT 1"),
        ),
    ),
    (
        "recording_sessions",
        (
            ("journal_sha256", "TEXT"),
            ("capture_generation", "INTEGER NOT NULL DEFAULT 1"),
        ),
    ),
    ("audio_chunks", (("captured_at_ms", "INTEGER"),)),
    ("recording_exports", (("input_journal_sha256", "TEXT"),)),
    (
        "recording_import_jobs",
        (
            ("next_attempt_at_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("lease_owner", "TEXT"),
            ("lease_until_ms", "INTEGER"),
        ),
    ),
    (
        "deletion_jobs",
        (
            ("deletion_scope", "TEXT NOT NULL DEFAULT 'all'"),
            ("requested_by", "TEXT NOT NULL DEFAULT 'user'"),
            ("retention_policy", "TEXT"),
            ("idempotency_key", "TEXT"),
            ("result_json", "TEXT"),
        ),
    ),
)

_MEETING_ENTITIES_REBUILD_REQUIRED_KINDS = (
    "decision_candidate",
    "action_item",
    "risk",
)
_MEETING_ENTITIES_REBUILD_REQUIRED_COLUMNS = (
    "confidence",
    "evidence_json",
    "owner",
    "deadline",
    "mitigation",
)
_MEETING_ENTITIES_REBUILD_DROP_INDEX_SQL = (
    "DROP INDEX IF EXISTS idx_meeting_entities_projection"
)
_MEETING_ENTITIES_REBUILD_RENAME_SQL = (
    "ALTER TABLE meeting_entities RENAME TO meeting_entities_legacy"
)
_MEETING_ENTITIES_REBUILD_CREATE_SQL = (
    "CREATE TABLE meeting_entities ("
    "meeting_id TEXT NOT NULL, entity_id TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('current_topic', 'open_question', "
    "'decision_candidate', 'action_item', 'risk')), "
    "status TEXT NOT NULL, text TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0, "
    "evidence_json TEXT NOT NULL DEFAULT '{}', evidence_segment_ids_json TEXT NOT NULL, "
    "owner TEXT, deadline TEXT, mitigation TEXT, updated_at_ms INTEGER, "
    "version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0), "
    "first_seen_seq INTEGER NOT NULL DEFAULT 1 CHECK (first_seen_seq > 0), "
    "last_updated_seq INTEGER NOT NULL DEFAULT 1 CHECK (last_updated_seq > 0), "
    "PRIMARY KEY (meeting_id, entity_id))"
)
# Each tuple is (target column, SQL fallback when absent, coalesce existing NULL).
_MEETING_ENTITIES_REBUILD_COPY_COLUMNS = (
    ("meeting_id", None, False),
    ("entity_id", None, False),
    ("kind", None, False),
    ("status", None, False),
    ("text", None, False),
    ("confidence", "0", True),
    ("evidence_json", "'{}'", True),
    ("evidence_segment_ids_json", "'[]'", True),
    ("owner", "NULL", False),
    ("deadline", "NULL", False),
    ("mitigation", "NULL", False),
    ("updated_at_ms", "NULL", False),
    ("version", "1", True),
    ("first_seen_seq", "1", True),
    ("last_updated_seq", "1", True),
)
_MEETING_ENTITIES_REBUILD_DROP_LEGACY_SQL = "DROP TABLE meeting_entities_legacy"
_MEETING_ENTITIES_REBUILD_CREATE_INDEX_SQL = (
    "CREATE INDEX idx_meeting_entities_projection "
    "ON meeting_entities(meeting_id, kind, updated_at_ms)"
)

_MEETING_TITLE_SELECT_SQL = (
    "SELECT id, title, started_at_ms, created_at_ms, title_source FROM meetings"
)
_MEETING_TITLE_UPDATE_SQL = "UPDATE meetings SET title = ?, title_source = ? WHERE id = ?"
_MEETING_TITLE_EXISTING_SOURCE = "migration"
_MEETING_TITLE_EMPTY_SOURCE = "fallback"

_V2_BACKFILL_STATEMENTS = (
    "UPDATE meeting_speakers "
    "SET label_source = COALESCE(NULLIF(label_source, ''), 'auto'), "
    "label_locked = COALESCE(label_locked, 0)",
    "UPDATE audio_chunks SET captured_at_ms = created_at_ms WHERE captured_at_ms IS NULL",
    "UPDATE deletion_jobs SET idempotency_key = 'legacy:' || id "
    "WHERE idempotency_key IS NULL OR TRIM(idempotency_key) = ''",
)

_V2_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_recording_import_jobs_claim "
    "ON recording_import_jobs(status, next_attempt_at_ms, created_at_ms, id)",
    "CREATE INDEX IF NOT EXISTS idx_recording_import_jobs_expired_lease "
    "ON recording_import_jobs(status, lease_until_ms) WHERE status = 'running'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_deletion_jobs_idempotency "
    "ON deletion_jobs(idempotency_key)",
)

_V2_SEED_STATEMENTS = (
    (
        "INSERT OR IGNORE INTO data_governance_settings "
        "(id, retention_policy, updated_at_ms) VALUES (1, ?, 0)",
        ("local_until_user_deletes",),
    ),
)

_V2_FINGERPRINT_MATERIAL = {
    "identity": "meeting-copilot-application-schema-v2",
    "base_schema_statements": _V2_BASE_SCHEMA_STATEMENTS,
    "additive_columns": _V2_ADDITIVE_COLUMNS,
    "meeting_entities_rebuild": {
        "required_kinds": _MEETING_ENTITIES_REBUILD_REQUIRED_KINDS,
        "required_columns": _MEETING_ENTITIES_REBUILD_REQUIRED_COLUMNS,
        "drop_index_sql": _MEETING_ENTITIES_REBUILD_DROP_INDEX_SQL,
        "rename_sql": _MEETING_ENTITIES_REBUILD_RENAME_SQL,
        "create_sql": _MEETING_ENTITIES_REBUILD_CREATE_SQL,
        "copy_columns": _MEETING_ENTITIES_REBUILD_COPY_COLUMNS,
        "drop_legacy_sql": _MEETING_ENTITIES_REBUILD_DROP_LEGACY_SQL,
        "create_index_sql": _MEETING_ENTITIES_REBUILD_CREATE_INDEX_SQL,
    },
    "meeting_title_backfill": {
        "select_sql": _MEETING_TITLE_SELECT_SQL,
        "update_sql": _MEETING_TITLE_UPDATE_SQL,
        "existing_title_source": _MEETING_TITLE_EXISTING_SOURCE,
        "empty_title_source": _MEETING_TITLE_EMPTY_SOURCE,
        "timezone_offset_minutes": 480,
        "format": _FALLBACK_MEETING_TITLE_FORMAT,
    },
    "backfill_statements": _V2_BACKFILL_STATEMENTS,
    "index_statements": _V2_INDEX_STATEMENTS,
    "seed_statements": _V2_SEED_STATEMENTS,
}


def _fingerprint_v2_material(material: object) -> str:
    payload = json.dumps(
        material,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return migration_fingerprint(payload)


def _columns(connection: MigrationConnection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(
    connection: MigrationConnection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column not in _columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_meeting_entities(connection: MigrationConnection) -> None:
    """Rebuild the historical entity table without ending the caller's tx."""

    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'meeting_entities'"
    ).fetchone()
    if row is None:
        return
    schema = str(row[0] or "").lower()
    if all(kind in schema for kind in _MEETING_ENTITIES_REBUILD_REQUIRED_KINDS) and all(
        f"{column} " in schema or f"{column}\n" in schema
        for column in _MEETING_ENTITIES_REBUILD_REQUIRED_COLUMNS
    ):
        return

    connection.execute(_MEETING_ENTITIES_REBUILD_DROP_INDEX_SQL)
    legacy_columns = _columns(connection, "meeting_entities")
    copy_expressions: list[str] = []
    for column, fallback, coalesce_existing in _MEETING_ENTITIES_REBUILD_COPY_COLUMNS:
        if column in legacy_columns:
            expression = (
                f"COALESCE({column}, {fallback})"
                if coalesce_existing and fallback is not None
                else column
            )
        elif fallback is not None:
            expression = fallback
        else:
            raise RuntimeError(f"meeting_entities migration requires legacy column: {column}")
        copy_expressions.append(expression)

    target_columns = ", ".join(
        column for column, _fallback, _coalesce in _MEETING_ENTITIES_REBUILD_COPY_COLUMNS
    )
    connection.execute(_MEETING_ENTITIES_REBUILD_RENAME_SQL)
    connection.execute(_MEETING_ENTITIES_REBUILD_CREATE_SQL)
    connection.execute(
        f"INSERT INTO meeting_entities ({target_columns}) "
        f"SELECT {', '.join(copy_expressions)} FROM meeting_entities_legacy"
    )
    connection.execute(_MEETING_ENTITIES_REBUILD_DROP_LEGACY_SQL)
    connection.execute(_MEETING_ENTITIES_REBUILD_CREATE_INDEX_SQL)


def _apply_v2(connection: MigrationConnection) -> None:
    for statement in _V2_BASE_SCHEMA_STATEMENTS:
        connection.execute(statement)

    _migrate_meeting_entities(connection)

    for table, columns in _V2_ADDITIVE_COLUMNS:
        for column, definition in columns:
            _ensure_column(connection, table, column, definition)

    meeting_rows = connection.execute(_MEETING_TITLE_SELECT_SQL).fetchall()
    for row in meeting_rows:
        existing_title = str(row[1] or "").strip()
        timestamp_ms = int(row[2] or row[3] or 0)
        title = existing_title or fallback_meeting_title(timestamp_ms)
        source = (
            _MEETING_TITLE_EXISTING_SOURCE
            if existing_title
            else _MEETING_TITLE_EMPTY_SOURCE
        )
        connection.execute(
            _MEETING_TITLE_UPDATE_SQL,
            (title, source, row[0]),
        )

    for statement in _V2_BACKFILL_STATEMENTS:
        connection.execute(statement)
    for statement in _V2_INDEX_STATEMENTS:
        connection.execute(statement)
    for statement, parameters in _V2_SEED_STATEMENTS:
        connection.execute(statement, parameters)


_LEGACY_MIGRATION = sql_migration(1, "create_legacy_repository_schema", _LEGACY_SCHEMA_STATEMENTS)

_LEGACY_V2_INCOMPLETE_FINGERPRINT = migration_fingerprint(
    "meeting-copilot-application-schema-v2|"
    + "|".join(_V2_BASE_SCHEMA_STATEMENTS)
    + "|additive-columns-and-entity-rebuild-v1"
)
_V2_FINGERPRINT = _fingerprint_v2_material(_V2_FINGERPRINT_MATERIAL)

APPLICATION_SCHEMA_MIGRATIONS = (
    _LEGACY_MIGRATION,
    SchemaMigration(
        version=2,
        name="create_v2_application_schema",
        fingerprint=_V2_FINGERPRINT,
        apply=_apply_v2,
    ),
    sql_migration(
        3,
        "add_native_pcm_source_ranges",
        (
            "ALTER TABLE audio_chunks ADD COLUMN source_sequence_start INTEGER",
            "ALTER TABLE audio_chunks ADD COLUMN source_sequence_end INTEGER",
            "ALTER TABLE audio_chunks ADD COLUMN source_timestamp_start_ms INTEGER",
            "ALTER TABLE audio_chunks ADD COLUMN source_timestamp_end_ms INTEGER",
        ),
    ),
)


def _normalized_schema_sql(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _application_schema_shape(
    connection: sqlite3.Connection,
) -> tuple[dict[str, frozenset[str]], dict[str, tuple[str, str]]]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    columns = {
        table: frozenset(_columns(connection, table))
        for table in tables
    }
    indexes = {
        str(row[0]): (str(row[1]), _normalized_schema_sql(row[2]))
        for row in connection.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    return columns, indexes


def _expected_application_schema_shape() -> tuple[
    dict[str, frozenset[str]],
    dict[str, tuple[str, str]],
]:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in _LEGACY_SCHEMA_STATEMENTS:
            connection.execute(statement)
        _apply_v2(connection)
        return _application_schema_shape(connection)
    finally:
        connection.close()


_V2_PROMOTION_BACKFILL_VIOLATIONS = (
    "SELECT 1 FROM meetings WHERE title IS NULL OR TRIM(title) = '' "
    "OR title_source IS NULL OR title_source NOT IN "
    "('ai', 'fallback', 'import', 'migration', 'user') LIMIT 1",
    "SELECT 1 FROM meeting_speakers WHERE label_source IS NULL "
    "OR label_source NOT IN ('auto', 'user') "
    "OR label_locked IS NULL OR label_locked NOT IN (0, 1) LIMIT 1",
    "SELECT 1 FROM audio_chunks WHERE captured_at_ms IS NULL LIMIT 1",
    "SELECT 1 FROM deletion_jobs WHERE idempotency_key IS NULL "
    "OR TRIM(idempotency_key) = '' LIMIT 1",
    "SELECT 1 FROM deletion_jobs GROUP BY idempotency_key HAVING COUNT(*) > 1 LIMIT 1",
    "SELECT 1 FROM meeting_entities WHERE confidence IS NULL "
    "OR evidence_json IS NULL OR TRIM(evidence_json) = '' "
    "OR version IS NULL OR version <= 0 "
    "OR first_seen_seq IS NULL OR first_seen_seq <= 0 "
    "OR last_updated_seq IS NULL OR last_updated_seq <= 0 LIMIT 1",
)


def _known_prerelease_v2_schema_is_complete(connection: sqlite3.Connection) -> bool:
    """Audit the exact old-fingerprint candidate without repairing it."""

    try:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            return False
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return False

        expected_columns, expected_indexes = _expected_application_schema_shape()
        actual_columns, actual_indexes = _application_schema_shape(connection)
        for table, required_columns in expected_columns.items():
            if not required_columns.issubset(actual_columns.get(table, frozenset())):
                return False
        for index, expected_signature in expected_indexes.items():
            if actual_indexes.get(index) != expected_signature:
                return False

        entity_schema_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'meeting_entities'"
        ).fetchone()
        entity_schema = _normalized_schema_sql(entity_schema_row[0] if entity_schema_row else "")
        if any(kind not in entity_schema for kind in _MEETING_ENTITIES_REBUILD_REQUIRED_KINDS):
            return False
        if any(
            connection.execute(statement).fetchone() is not None
            for statement in _V2_PROMOTION_BACKFILL_VIOLATIONS
        ):
            return False
        governance_row = connection.execute(
            "SELECT retention_policy FROM data_governance_settings WHERE id = 1"
        ).fetchone()
        if governance_row is None or str(governance_row[0] or "") not in {
            "local_until_user_deletes",
            "manual_only",
            "30_days",
            "90_days",
            "365_days",
        }:
            return False
    except (RuntimeError, sqlite3.DatabaseError):
        return False
    return True


def _promote_known_prerelease_v2_fingerprint(
    database_path: str | Path,
    *,
    lock_path: str | Path | None,
    timeout_seconds: float,
) -> None:
    """Replace one unreleased incomplete hash without accepting history drift.

    The old callback and the reviewed declaration registry produce the same
    completed V2 schema. Only its fingerprint material was incomplete. The
    history row is transactionally promoted under the normal migration lock;
    unknown fingerprints remain untouched and fail in the formal migrator.
    """

    database = Path(database_path)
    if not database.is_file():
        return
    with sqlite_schema_migration_lock(
        database,
        lock_path=lock_path,
        timeout_seconds=timeout_seconds,
    ):
        connection = sqlite3.connect(
            database,
            isolation_level=None,
            timeout=timeout_seconds,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={max(1, int(timeout_seconds * 1_000))}")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {2, APPLICATION_SCHEMA_VERSION}:
                return
            try:
                row = connection.execute(
                    f"SELECT name, fingerprint FROM {MIGRATION_HISTORY_TABLE} WHERE version = 2"
                ).fetchone()
            except sqlite3.OperationalError:
                return
            if row != (
                "create_v2_application_schema",
                _LEGACY_V2_INCOMPLETE_FINGERPRINT,
            ):
                return
            if not _known_prerelease_v2_schema_is_complete(connection):
                return

            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    f"UPDATE {MIGRATION_HISTORY_TABLE} SET fingerprint = ? "
                    "WHERE version = 2 AND name = ? AND fingerprint = ?",
                    (
                        _V2_FINGERPRINT,
                        "create_v2_application_schema",
                        _LEGACY_V2_INCOMPLETE_FINGERPRINT,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("V2 migration fingerprint changed during promotion")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()


def bootstrap_application_schema(
    database_path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    lock_path: str | Path | None = None,
    timeout_seconds: float = 30.0,
    failpoint: MigrationFailpoint | None = None,
) -> SchemaMigrationResult:
    """Create or upgrade the complete local application database.

    This is the one public schema entry point. Repository constructors may
    call it for test/backward compatibility, while production application
    startup should call it once before opening any repository connection.
    """

    _promote_known_prerelease_v2_fingerprint(
        database_path,
        lock_path=lock_path,
        timeout_seconds=timeout_seconds,
    )
    return migrate_sqlite_schema(
        database_path,
        APPLICATION_SCHEMA_MIGRATIONS,
        current_version=APPLICATION_SCHEMA_VERSION,
        max_supported_version=APPLICATION_MAX_SUPPORTED_SCHEMA_VERSION,
        backup_dir=backup_dir,
        lock_path=lock_path,
        timeout_seconds=timeout_seconds,
        failpoint=failpoint,
    )


def safe_schema_migration_report(
    result: SchemaMigrationResult | None,
) -> dict[str, object]:
    """Project migration state without filesystem paths or database content."""

    if result is None:
        return {
            "schema_version": "application-schema-migration-report.v1",
            "status": "not_applicable",
            "storage": "memory",
        }
    return {
        "schema_version": "application-schema-migration-report.v1",
        "status": "ready",
        "storage": "sqlite",
        "source_version": result.source_version,
        "final_version": result.final_version,
        "applied_versions": list(result.applied_versions),
        "migrated": result.migrated,
        "backup_created": result.backup_path is not None,
    }


__all__ = [
    "APPLICATION_MAX_SUPPORTED_SCHEMA_VERSION",
    "APPLICATION_SCHEMA_MIGRATIONS",
    "APPLICATION_SCHEMA_VERSION",
    "bootstrap_application_schema",
    "safe_schema_migration_report",
]
