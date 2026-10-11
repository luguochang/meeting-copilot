"""Transactional, source-bound historical recovery; independent of realtime jobs."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

from .transcript_recovery_audio import RecoveryAudioError, build_recovery_source, range_plan


class RecoveryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _identity(prefix: str, *parts: Any) -> str:
    return prefix + "_" + hashlib.sha256(_json(parts).encode()).hexdigest()[:32]


def order_transcript_for_reading(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep seq cursors intact, but insert confirmed history at its audio time."""
    rows = [dict(segment) for segment in segments]
    if any(row.get("recovery_job_id") for row in rows):
        rows.sort(key=lambda row: (row.get("started_at_ms") is None,
                                  int(row.get("started_at_ms") or 0), int(row["transcript_seq"])))
    return rows


class TranscriptRecoveryPersistence:
    """Mixin using V2Persistence's one SQLite transaction and tombstone fence."""

    def _recovery_meeting_locked(self, meeting_id: str) -> Any:
        self._raise_if_tombstoned_locked(meeting_id)
        row = self._conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if row is None:
            raise RecoveryError("recovery_meeting_not_found")
        return row

    def _recovery_segments_locked(self, meeting_id: str) -> list[dict[str, Any]]:
        return [self._segment_dict(row) for row in self._conn.execute(
            "SELECT * FROM transcript_segments WHERE meeting_id=? ORDER BY transcript_seq",
            (meeting_id,)).fetchall()]

    def _recovery_source_locked(self, meeting_id: str, track: str, epoch: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self._recovery_meeting_locked(meeting_id)
        recordings = [self._recording_session_dict(row) for row in self._conn.execute(
            "SELECT * FROM recording_sessions WHERE meeting_id=? ORDER BY track,epoch", (meeting_id,)).fetchall()]
        recording = next((row for row in recordings if row["track"] == track and row["epoch"] == epoch), None)
        if recording is None or recording["sealed_at_ms"] is None:
            raise RecoveryError("recovery_audio_not_ready")
        # Only server-owned layouts for this meeting and epoch. No caller paths.
        expected = f"audio_assets/{meeting_id}/tracks/{track}/epoch-{epoch}/audio.wav"
        legacy = f"audio_assets/{meeting_id}/audio.wav"
        if recording["output_relative_path"] != expected and not (
            track == "microphone" and epoch == 0 and recording["output_relative_path"] == legacy
        ):
            raise RecoveryError("recovery_audio_path_invalid")
        segments = self._recovery_segments_locked(meeting_id)
        try:
            return build_recovery_source(recording, recordings=recordings, segments=segments), segments
        except RecoveryAudioError as exc:
            raise RecoveryError(exc.code) from None

    def recovery_source(self, meeting_id: str, track: str, epoch: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._lock:
            return self._recovery_source_locked(meeting_id, track, epoch)

    def recovery_sources(self, meeting_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._lock:
            self._recovery_meeting_locked(meeting_id)
            sources, unavailable = [], []
            for row in self._conn.execute(
                "SELECT track,epoch,status FROM recording_sessions WHERE meeting_id=? ORDER BY track,epoch", (meeting_id,)
            ).fetchall():
                if row["status"] != "ready":
                    continue
                try:
                    sources.append(self._recovery_source_locked(meeting_id, row["track"], row["epoch"])[0])
                except RecoveryError as exc:
                    unavailable.append({"track": row["track"], "epoch": row["epoch"], "error_code": exc.code})
            return sources, unavailable

    def _recovery_plan_locked(self, meeting_id: str, request: Mapping[str, Any], *, uncovered: bool = True) -> dict[str, Any]:
        source, segments = self._recovery_source_locked(meeting_id, request["track"], request["epoch"])
        try:
            plan = range_plan(source, segments, start_sample=request["start_sample"], end_sample=request["end_sample"])
        except RecoveryAudioError as exc:
            raise RecoveryError(exc.code) from None
        if any(plan[key] != request[key] for key in ("audio_digest", "coverage_digest")):
            raise RecoveryError("recovery_source_changed")
        if uncovered and any(
            segment.get("source_track") in {None, plan["track"]}
            and (segment.get("started_at_ms") is None or segment.get("ended_at_ms") is None
                 or (segment["ended_at_ms"] > plan["start_ms"] and segment["started_at_ms"] < plan["end_ms"]))
            for segment in segments
        ):
            raise RecoveryError("recovery_range_covered")
        return source

    @staticmethod
    def _recovery_job_dict(row: Any) -> dict[str, Any]:
        job = dict(row)
        job.pop("request_key", None)
        job["merged_segment_ids"] = json.loads(job.pop("merged_segment_ids_json"))
        job["max_attempts"] = 3
        job["execution_active"] = bool(job["lease_owner"])
        job.pop("lease_owner", None)
        job.pop("execution_version", None)
        return job

    def recovery_execution_active(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM transcript_recovery_jobs WHERE lease_owner IS NOT NULL").fetchone() is not None

    def get_recovery_job(self, meeting_id: str, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._recovery_meeting_locked(meeting_id)
            row = self._conn.execute("SELECT * FROM transcript_recovery_jobs WHERE meeting_id=? AND id=?",
                                     (meeting_id, job_id)).fetchone()
            if row is None:
                raise RecoveryError("recovery_job_not_found")
            return self._recovery_job_dict(row)

    def list_recovery_jobs(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._recovery_meeting_locked(meeting_id)
            return [self._recovery_job_dict(row) for row in self._conn.execute(
                "SELECT * FROM transcript_recovery_jobs WHERE meeting_id=? ORDER BY created_at_ms,id", (meeting_id,)
            ).fetchall()]

    def _recovery_overlap_locked(self, meeting_id: str, request: Mapping[str, Any], *, excluding: str = "") -> None:
        if self._conn.execute(
            "SELECT 1 FROM transcript_recovery_jobs WHERE meeting_id=? AND track=? AND epoch=? AND id!=? "
            "AND start_sample<? AND end_sample>? AND (status NOT IN ('cancelled','stale') OR lease_owner IS NOT NULL)",
            (meeting_id, request["track"], request["epoch"], excluding, request["end_sample"], request["start_sample"]),
        ).fetchone():
            raise RecoveryError("recovery_range_overlap")

    def create_recovery_job(self, meeting_id: str, request: Mapping[str, Any], *, now_ms: int,
                            verify: Callable[[Mapping[str, Any]], None]) -> dict[str, Any]:
        key = hashlib.sha256(_json([meeting_id, dict(request)]).encode()).hexdigest()
        with self._lock:
            self._recovery_meeting_locked(meeting_id)
            prior = self._conn.execute("SELECT * FROM transcript_recovery_jobs WHERE request_key=?", (key,)).fetchone()
            if prior is not None:
                return self._recovery_job_dict(prior)
            source = self._recovery_plan_locked(meeting_id, request)
        # Hash/crop without a SQLite reservation: a request for an old sealed
        # epoch must not block another meeting's realtime final commits.
        verify(source)
        with self._write_transaction():
            self._recovery_meeting_locked(meeting_id)
            prior = self._conn.execute("SELECT * FROM transcript_recovery_jobs WHERE request_key=?", (key,)).fetchone()
            # A merged identical action remains idempotent even though it added coverage.
            if prior is not None:
                return self._recovery_job_dict(prior)
            source = self._recovery_plan_locked(meeting_id, request)
            self._recovery_overlap_locked(meeting_id, request)
            _, segments = self._recovery_source_locked(meeting_id, request["track"], request["epoch"])
            plan = range_plan(source, segments, start_sample=request["start_sample"], end_sample=request["end_sample"])
            job_id = _identity("recovery", key)
            self._conn.execute(
                "INSERT INTO transcript_recovery_jobs(id,meeting_id,request_key,track,epoch,start_sample,end_sample,start_ms,end_ms,"
                "audio_digest,coverage_digest,status,created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)",
                (job_id, meeting_id, key, request["track"], request["epoch"], request["start_sample"], request["end_sample"],
                 plan["start_ms"], plan["end_ms"], request["audio_digest"], request["coverage_digest"], now_ms, now_ms))
            return self.get_recovery_job(meeting_id, job_id)

    def _recovery_owned_locked(self, job_id: str, owner: str, version: int) -> Any:
        row = self._conn.execute("SELECT * FROM transcript_recovery_jobs WHERE id=?", (job_id,)).fetchone()
        if (row is None or row["status"] != "running" or row["lease_owner"] != owner
                or row["version"] != version or row["execution_version"] != version):
            raise RecoveryError("recovery_execution_superseded")
        self._recovery_meeting_locked(row["meeting_id"])
        return row

    def recover_recovery_leases(self, *, now_ms: int) -> int:
        with self._write_transaction():
            result = self._conn.execute(
                "UPDATE transcript_recovery_jobs SET status=CASE WHEN status='running' THEN 'failed' ELSE status END, "
                "error_code=CASE WHEN status='running' THEN 'recovery_worker_interrupted' ELSE error_code END, "
                "lease_owner=NULL,lease_until_ms=NULL,execution_version=NULL,version=version+1,updated_at_ms=? "
                "WHERE lease_owner IS NOT NULL AND COALESCE(lease_until_ms,0)<=?", (now_ms, now_ms))
            return result.rowcount

    def claim_recovery_job(self, *, owner: str, now_ms: int, lease_ms: int = 240_000) -> dict[str, Any] | None:
        with self._write_transaction():
            if self._conn.execute("SELECT 1 FROM recording_sessions WHERE status='active'").fetchone():
                self._conn.execute("UPDATE transcript_recovery_jobs SET status='waiting_resources',error_code='recovery_capture_active',"
                                   "version=version+1,updated_at_ms=? WHERE status='queued'", (now_ms,))
                return None
            if self._conn.execute("SELECT 1 FROM transcript_recovery_jobs WHERE lease_owner IS NOT NULL").fetchone():
                return None
            row = self._conn.execute(
                "SELECT * FROM transcript_recovery_jobs WHERE status IN ('queued','waiting_resources') ORDER BY created_at_ms,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._recovery_meeting_locked(row["meeting_id"])
            self._conn.execute("UPDATE transcript_recovery_jobs SET status='running',lease_owner=?,lease_until_ms=?,"
                               "execution_version=version+1,version=version+1,error_code=NULL,updated_at_ms=? WHERE id=?",
                               (owner, now_ms + lease_ms, now_ms, row["id"]))
            return self.get_recovery_job(row["meeting_id"], row["id"])

    def heartbeat_recovery_job(self, job_id: str, *, owner: str, now_ms: int, lease_ms: int = 240_000,
                               version: int | None = None) -> bool:
        with self._write_transaction():
            return bool(self._conn.execute("UPDATE transcript_recovery_jobs SET lease_until_ms=? WHERE id=? AND lease_owner=? "
                                           "AND (? IS NULL OR execution_version=?)",
                                           (now_ms + lease_ms, job_id, owner, version, version)).rowcount)

    def begin_recovery_attempt(self, job_id: str, *, owner: str, version: int, now_ms: int,
                               verify: Callable[[Mapping[str, Any]], None]) -> dict[str, Any]:
        with self._lock:
            row = self._recovery_owned_locked(job_id, owner, version)
            source = self._recovery_plan_locked(row["meeting_id"], row)
        verify(source)
        with self._write_transaction():
            row = self._recovery_owned_locked(job_id, owner, version)
            if row["attempts"] >= 3:
                raise RecoveryError("recovery_attempts_exhausted")
            if self._conn.execute("SELECT 1 FROM recording_sessions WHERE status='active'").fetchone():
                raise RecoveryError("recovery_capture_active")
            self._recovery_plan_locked(row["meeting_id"], row)
            self._conn.execute("UPDATE transcript_recovery_jobs SET attempts=attempts+1,updated_at_ms=? WHERE id=?", (now_ms, job_id))
            return self.get_recovery_job(row["meeting_id"], job_id)

    def finish_recovery_job(self, job_id: str, *, owner: str, version: int, now_ms: int, status: str,
                            error_code: str | None = None, text: str | None = None,
                            verify: Callable[[Mapping[str, Any]], None] | None = None,
                            refund_preflight: bool = False, keep_execution: bool = False) -> bool:
        if status not in {"candidate_ready", "failed", "stale", "waiting_resources"}:
            raise ValueError("invalid recovery terminal state")
        if refund_preflight and (status != "waiting_resources" or error_code not in {
            "asr_capacity_unavailable", "asr_resource_probe_failed", "asr_cold_start_busy"
        }):
            raise ValueError("only a known pre-spawn resource refusal can refund an attempt")
        if keep_execution and status != "failed":
            raise ValueError("an unfinished owned cleanup must report failure")
        if status == "candidate_ready":
            try:
                with self._lock:
                    row = self._recovery_owned_locked(job_id, owner, version)
                    source = self._recovery_plan_locked(row["meeting_id"], row)
                if verify:
                    verify(source)
            except (RecoveryError, RecoveryAudioError) as exc:
                status, error_code, text = "stale", exc.code, None
        with self._write_transaction():
            row = self._conn.execute("SELECT * FROM transcript_recovery_jobs WHERE id=? AND lease_owner=?", (job_id, owner)).fetchone()
            if row is None:
                return False
            if row["execution_version"] != version:
                return False
            if row["status"] != "running" or row["version"] != version:
                if keep_execution:
                    return False
                self._conn.execute("UPDATE transcript_recovery_jobs SET lease_owner=NULL,lease_until_ms=NULL,execution_version=NULL,"
                                   "attempts=MAX(0,attempts-?) WHERE id=?", (int(refund_preflight), job_id))
                return False
            self._recovery_meeting_locked(row["meeting_id"])
            if status == "candidate_ready":
                try:
                    source = self._recovery_plan_locked(row["meeting_id"], row)
                except (RecoveryError, RecoveryAudioError) as exc:
                    status, error_code, text = "stale", exc.code, None
            self._conn.execute(
                "UPDATE transcript_recovery_jobs SET status=?,error_code=?,original_candidate_text=COALESCE(?,original_candidate_text),"
                "candidate_text=COALESCE(?,candidate_text),candidate_version=candidate_version+?,version=version+1,"
                "lease_owner=CASE WHEN ? THEN lease_owner ELSE NULL END,lease_until_ms=CASE WHEN ? THEN lease_until_ms ELSE NULL END,"
                "execution_version=CASE WHEN ? THEN execution_version ELSE NULL END,attempts=MAX(0,attempts-?),updated_at_ms=? WHERE id=?",
                (status, error_code, text, text, int(status == "candidate_ready"), keep_execution, keep_execution, keep_execution,
                 int(refund_preflight), now_ms, job_id))
            return True

    def cancel_recovery_job(self, meeting_id: str, job_id: str, *, expected_version: int | None, now_ms: int) -> dict[str, Any]:
        with self._write_transaction():
            job = self.get_recovery_job(meeting_id, job_id)
            if expected_version is not None and job["version"] != expected_version:
                raise RecoveryError("recovery_version_conflict")
            if job["status"] == "merged":
                raise RecoveryError("recovery_already_merged")
            if job["status"] != "cancelled":
                self._conn.execute("UPDATE transcript_recovery_jobs SET status='cancelled',version=version+1,updated_at_ms=? WHERE id=?",
                                   (now_ms, job_id))
            return self.get_recovery_job(meeting_id, job_id)

    def retry_recovery_job(self, meeting_id: str, job_id: str, *, expected_version: int | None, now_ms: int,
                           verify: Callable[[Mapping[str, Any]], None]) -> dict[str, Any]:
        with self._lock:
            snapshot = self.get_recovery_job(meeting_id, job_id)
            if expected_version is not None and snapshot["version"] != expected_version:
                raise RecoveryError("recovery_version_conflict")
            if snapshot["status"] not in {"failed", "cancelled"} or snapshot["execution_active"]:
                raise RecoveryError("recovery_retry_not_allowed")
            if snapshot["attempts"] >= 3 and not snapshot["original_candidate_text"]:
                raise RecoveryError("recovery_attempts_exhausted")
            source = self._recovery_plan_locked(meeting_id, snapshot)
        verify(source)
        with self._write_transaction():
            job = self.get_recovery_job(meeting_id, job_id)
            if expected_version is not None and job["version"] != expected_version:
                raise RecoveryError("recovery_version_conflict")
            if job["status"] not in {"failed", "cancelled"} or job["execution_active"]:
                raise RecoveryError("recovery_retry_not_allowed")
            if job["attempts"] >= 3 and not job["original_candidate_text"]:
                raise RecoveryError("recovery_attempts_exhausted")
            self._recovery_plan_locked(meeting_id, job)
            self._recovery_overlap_locked(meeting_id, job, excluding=job_id)
            self._conn.execute("UPDATE transcript_recovery_jobs SET status=?,error_code=NULL,version=version+1,updated_at_ms=? WHERE id=?",
                               ("candidate_ready" if job["original_candidate_text"] else "queued", now_ms, job_id))
            return self.get_recovery_job(meeting_id, job_id)

    def mark_recovery_stale(self, meeting_id: str, job_id: str, *, now_ms: int, error_code: str) -> None:
        with self._write_transaction():
            self._conn.execute("UPDATE transcript_recovery_jobs SET status='stale',error_code=?,version=version+1,updated_at_ms=? "
                               "WHERE meeting_id=? AND id=? AND lease_owner IS NULL "
                               "AND status IN ('candidate_ready','failed','queued','waiting_resources','cancelled')",
                               (error_code, now_ms, meeting_id, job_id))

    def merge_recovery_job(self, meeting_id: str, job_id: str, *, candidate_version: int, text: str | None,
                           now_ms: int, verify: Callable[[Mapping[str, Any]], None]) -> dict[str, Any]:
        with self._write_transaction():
            meeting = self._recovery_meeting_locked(meeting_id)
            job = self.get_recovery_job(meeting_id, job_id)
            if job["candidate_version"] != candidate_version:
                raise RecoveryError("recovery_version_conflict")
            if job["status"] == "merged":
                if text is not None and text.strip() != job["candidate_text"]:
                    raise RecoveryError("recovery_already_merged")
                return job
            if job["status"] != "candidate_ready" or job["execution_active"]:
                raise RecoveryError("recovery_candidate_not_ready")
            if meeting["state"] != "ended" or self._conn.execute("SELECT 1 FROM recording_sessions WHERE status='active'").fetchone():
                raise RecoveryError("recovery_merge_capture_active")
            source = self._recovery_plan_locked(meeting_id, job)
            verify(source)
            candidate = str(job["original_candidate_text"] if text is None else text).strip()
            if not candidate or len(candidate) > 20_000 or len(candidate.encode("utf-8")) > 65_536 or "\x00" in candidate:
                raise RecoveryError("recovery_candidate_invalid")
            segment_id, final_id = _identity("recovered_segment", job_id), _identity("recovered_final", job_id)
            paragraph_id = _identity("recovered_paragraph", job_id)
            seq = int(self._conn.execute("SELECT COALESCE(MAX(transcript_seq),0)+1 FROM transcript_segments WHERE meeting_id=?",
                                         (meeting_id,)).fetchone()[0])
            evidence_hash = hashlib.sha256((segment_id + "\x1f" + candidate).encode()).hexdigest()
            provenance = {"job_id": job_id, "track": job["track"], "epoch": job["epoch"],
                          "start_sample": job["start_sample"], "end_sample": job["end_sample"],
                          "audio_digest": job["audio_digest"], "coverage_digest": job["coverage_digest"],
                          "candidate_version": candidate_version, "timing": "whole_audio_range",
                          "recognition": "local_batch_asr", "correction": "not_requested",
                          "user_edited": candidate != job["original_candidate_text"]}
            self._conn.execute(
                "INSERT INTO transcript_segments(meeting_id,segment_id,final_id,transcript_seq,text,normalized_text,"
                "started_at_ms,ended_at_ms,source_track,evidence_hash,created_at_ms,updated_at_ms,recovery_job_id,recovery_provenance_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (meeting_id, segment_id, final_id, seq, candidate, candidate, job["start_ms"], job["end_ms"], job["track"],
                 evidence_hash, now_ms, now_ms, job_id, _json(provenance)))
            self._conn.execute("INSERT INTO asr_checkpoints(meeting_id,checkpoint_id,final_id,transcript_seq,text,normalized_text,"
                               "started_at_ms,ended_at_ms,created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (meeting_id, segment_id, final_id, seq, candidate, candidate, job["start_ms"], job["end_ms"], now_ms, now_ms))
            # Independent stable source: never feed old time into the rolling projector.
            self._conn.execute("INSERT INTO semantic_paragraphs(meeting_id,paragraph_id,revision,text,start_ms,end_ms,status,"
                               "created_at_ms,updated_at_ms,projection_version) VALUES(?,?,1,?,?,?,'stable',?,?,2)",
                               (meeting_id, paragraph_id, candidate, job["start_ms"], job["end_ms"], now_ms, now_ms))
            self._conn.execute("INSERT INTO semantic_paragraph_checkpoints(meeting_id,paragraph_id,checkpoint_id,ordinal) VALUES(?,?,?,0)",
                               (meeting_id, paragraph_id, segment_id))
            payload = self._segment_dict(self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=?",
                                                            (meeting_id, segment_id)).fetchone())
            event_seq = self._append_event_locked(meeting_id=meeting_id, event_type="transcript.segment.finalized",
                                                 aggregate_type="transcript_segment", aggregate_id=segment_id, occurred_at_ms=now_ms,
                                                 idempotency_key=f"transcript.recovery:{job_id}", payload=payload)
            self._conn.execute("INSERT INTO intelligence_input_coverage(meeting_id,event_seq,segment_id,revision,status,failure_reason,"
                               "input_transcript_seq,input_text_hash,created_at_ms,updated_at_ms) VALUES(?,?,?,1,'excluded','historical_recovery_manual',?,?,?,?)",
                               (meeting_id, event_seq, segment_id, seq, evidence_hash, now_ms, now_ms))
            self._invalidate_review_sources_locked(meeting_id=meeting_id, now_ms=now_ms, source_id=job_id)
            self._conn.execute("UPDATE transcript_recovery_jobs SET status='merged',candidate_text=?,merged_segment_ids_json=?,"
                               "merged_at_ms=?,updated_at_ms=?,version=version+1 WHERE id=?",
                               (candidate, _json([segment_id]), now_ms, now_ms, job_id))
            self._append_event_locked(meeting_id=meeting_id, event_type="transcript.recovery.merged", aggregate_type="transcript_recovery_job",
                                     aggregate_id=job_id, occurred_at_ms=now_ms, idempotency_key=f"transcript.recovery.merged:{job_id}",
                                     payload={"job_id": job_id, "segment_ids": [segment_id], "candidate_version": candidate_version})
            return self.get_recovery_job(meeting_id, job_id)
