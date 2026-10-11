"""Attachment jobs sharing the existing bounded local document worker."""
from __future__ import annotations

import asyncio
from typing import Any, Mapping
import uuid

from .document_import import DocumentExtraction
from .knowledge_jobs import JOB_LEASE_MS, KnowledgeJobRunner
from .knowledge_repository import KnowledgeConflict, KnowledgeScopeError
from .meeting_materials import MeetingMaterialImportService, MeetingMaterialsRepository, _enqueue, _now, _set


class MeetingMaterialJobQueue:
    def __init__(self, repository: MeetingMaterialsRepository) -> None:
        self.repository = repository

    def _get(self, job_id: str) -> dict[str, Any]:
        row = self.repository._conn.execute("SELECT * FROM meeting_attachment_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("material job not found")
        return dict(row)

    def get(self, job_id: str, *, set_id: str | None = None) -> dict[str, Any]:
        with self.repository._lock:
            job = self._get(job_id)
            if set_id is not None:
                if job["set_id"] != set_id:
                    raise KeyError("job not found in material set")
                _set(self.repository._conn, set_id, allow_deleted=job["type"] == "cleanup")
            return job

    def list(self, set_id: str, *, limit: int = 100, before_id: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("invalid job page")
        with self.repository._lock:
            _set(self.repository._conn, set_id, allow_deleted=True)
            clauses, values = ["set_id=?"], [set_id]
            if before_id:
                old = self.get(before_id, set_id=set_id)
                clauses.append("(created_at_ms<? OR (created_at_ms=? AND id<?))")
                values.extend((old["created_at_ms"], old["created_at_ms"], old["id"]))
            rows = self.repository._conn.execute("SELECT * FROM meeting_attachment_jobs WHERE " + " AND ".join(clauses) +
                                                 " ORDER BY created_at_ms DESC,id DESC LIMIT ?", (*values, limit + 1)).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {"items": items, "has_more": len(rows) > limit, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def recover_expired(self, *, now_ms: int | None = None) -> int:
        timestamp = _now() if now_ms is None else now_ms
        with self.repository._write():
            return self.repository._conn.execute("UPDATE meeting_attachment_jobs SET status='queued',progress='waiting_recovery',"
                                                 "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE status='running' "
                                                 "AND lease_until_ms<=?", (timestamp, timestamp)).rowcount

    def recover_orphans(self) -> int:
        with self.repository._write():
            connection = self.repository._conn
            rows = connection.execute("SELECT a.id,a.status FROM meeting_attachments a JOIN material_sets m ON m.id=a.set_id "
                                      "WHERE a.original_relative_path IS NOT NULL AND "
                                      "((a.status='active' AND a.import_status='pending' AND m.state!='deleted') OR a.status='deleted') "
                                      "AND NOT EXISTS(SELECT 1 FROM meeting_attachment_jobs j WHERE j.attachment_id=a.id AND "
                                      "j.type=CASE WHEN a.status='deleted' THEN 'cleanup' ELSE 'import' END)").fetchall()
            for row in rows:
                _enqueue(connection, row["id"], "cleanup" if row["status"] == "deleted" else "import", _now())
            return len(rows)

    def claim(self, *, now_ms: int | None = None) -> dict[str, Any] | None:
        timestamp = _now() if now_ms is None else now_ms
        with self.repository._write():
            self.recover_expired(now_ms=timestamp)
            connection = self.repository._conn
            row = connection.execute("SELECT id FROM meeting_attachment_jobs WHERE status='queued' AND next_retry_at_ms<=? "
                                     "ORDER BY CASE type WHEN 'cleanup' THEN 0 ELSE 1 END,created_at_ms,id LIMIT 1", (timestamp,)).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE meeting_attachment_jobs SET status='running',progress=CASE type WHEN 'cleanup' THEN 'cleaning' ELSE 'extracting' END,"
                               "attempt=attempt+1,lease_token=?,lease_until_ms=?,updated_at_ms=? WHERE id=?",
                               (uuid.uuid4().hex, timestamp + JOB_LEASE_MS, timestamp, row[0]))
            return self._get(row[0])

    def release(self, job: Mapping[str, Any]) -> None:
        with self.repository._write():
            self.repository._conn.execute("UPDATE meeting_attachment_jobs SET status='queued',progress='waiting_recovery',"
                                          "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE id=? AND status='running' AND lease_token=?",
                                          (_now(), job["id"], job["lease_token"]))

    def finish(self, job: Mapping[str, Any], result: DocumentExtraction) -> bool:
        with self.repository._write():
            current = self._get(job["id"])
            if current["status"] != "running" or current["lease_token"] != job["lease_token"]:
                return False
            attachment = self.repository.get_document(job["attachment_id"])
            version = None
            if job["type"] == "import":
                if (attachment["content_revision"] != job["input_revision"] or attachment["original_sha256"] != job["input_sha256"]
                        or attachment["status"] == "deleted" or _set(self.repository._conn, job["set_id"], allow_deleted=True)["state"] == "deleted"):
                    result = DocumentExtraction("failed", error_code="input_superseded")
                elif result.content:
                    version = self.repository.add_version(job["set_id"], attachment["id"], result.content, spans=result.spans,
                                                          origin="extracted", structure=result.structure, warnings=result.warnings)
                if result.error_code != "input_superseded" and attachment["status"] != "deleted":
                    self.repository._conn.execute("UPDATE meeting_attachments SET import_status=?,error_code=?,updated_at_ms=? WHERE id=?",
                                                  (result.status, result.error_code, _now(), attachment["id"]))
            succeeded = result.status in {"extracted", "needs_ocr"}
            self.repository._conn.execute("UPDATE meeting_attachment_jobs SET status=?,progress=?,error_code=?,result_version_id=?,"
                                          "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE id=?",
                                          ("succeeded" if succeeded else "failed", ("cleaned" if job["type"] == "cleanup" else "preview_ready") if succeeded else "failed",
                                           result.error_code, version["id"] if version else None, _now(), job["id"]))
            return True

    def cancel(self, set_id: str, job_id: str) -> dict[str, Any]:
        with self.repository._write():
            job = self.get(job_id, set_id=set_id)
            if job["type"] == "cleanup":
                raise KnowledgeScopeError("requested deletion cleanup cannot be cancelled")
            if job["status"] in {"queued", "running"}:
                self.repository._conn.execute("UPDATE meeting_attachment_jobs SET status='cancelled',progress='cancelled',error_code='user_cancelled',"
                                              "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE id=?", (_now(), job_id))
                self.repository._conn.execute("UPDATE meeting_attachments SET import_status='failed',error_code='user_cancelled',updated_at_ms=? WHERE id=?",
                                              (_now(), job["attachment_id"]))
            return self._get(job_id)

    def retry(self, set_id: str, job_id: str) -> dict[str, Any]:
        with self.repository._write():
            job = self.get(job_id, set_id=set_id)
            if job["status"] not in {"failed", "cancelled"}:
                raise KnowledgeConflict("only failed or cancelled jobs can be retried")
            attachment = self.repository.get_document(job["attachment_id"])
            if job["type"] == "import" and attachment["status"] == "deleted":
                raise KnowledgeScopeError("deleted attachments cannot be converted")
            if self.repository._conn.execute("SELECT 1 FROM meeting_attachment_jobs WHERE attachment_id=? AND type=? AND status IN ('queued','running')",
                                             (attachment["id"], job["type"])).fetchone():
                raise KnowledgeConflict("another job is already queued")
            # A stale conversion must not be retried over a user's newer version.
            if job["type"] == "import" and job["error_code"] == "input_superseded":
                raise KnowledgeConflict("conversion was superseded by a saved version")
            self.repository._conn.execute("UPDATE meeting_attachment_jobs SET status='queued',progress='waiting',input_revision=?,input_sha256=?,"
                                          "error_code=NULL,lease_token=NULL,lease_until_ms=NULL,next_retry_at_ms=0,updated_at_ms=? WHERE id=?",
                                          (attachment["content_revision"], attachment["original_sha256"], _now(), job_id))
            if job["type"] == "import":
                self.repository._conn.execute("UPDATE meeting_attachments SET import_status='pending',error_code=NULL,updated_at_ms=? WHERE id=?", (_now(), attachment["id"]))
            return self._get(job_id)


class MeetingMaterialJobRunner(KnowledgeJobRunner):
    def __init__(self, queue: MeetingMaterialJobQueue, importer: MeetingMaterialImportService, **kwargs) -> None:
        super().__init__(queue, importer, **kwargs)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            await asyncio.to_thread(self.importer.recover_unpublished_assets)
        await super().start()

    def _convert(self, job: Mapping[str, Any]) -> DocumentExtraction:
        return super()._convert({**job, "document_id": job["attachment_id"]})

    def _cleanup(self, job: Mapping[str, Any]) -> DocumentExtraction:
        attachment = self.importer.repository.get_document(job["attachment_id"])
        if attachment["status"] != "deleted":
            return DocumentExtraction("failed", error_code="cleanup_requires_deleted_document")
        if attachment["original_relative_path"] is not None:
            try:
                original = self.importer.original_path(attachment["id"], require_exists=False)
            except (ValueError, OSError):
                return DocumentExtraction("failed", error_code="unsafe_original_path")
            temporary = original.parent / "original.tmp"
            if temporary.is_symlink() or temporary.is_junction() or (temporary.exists() and temporary.stat().st_nlink != 1):
                return DocumentExtraction("failed", error_code="unsafe_original_path")
            original.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            if original.parent.exists() and not any(original.parent.iterdir()):
                original.parent.rmdir()
        with self.queue.repository._write():
            current = self.queue.get(job["id"])
            if current["status"] != "running" or current["lease_token"] != job["lease_token"]:
                return DocumentExtraction("failed", error_code="worker_interrupted")
            connection = self.queue.repository._conn
            connection.execute("DELETE FROM meeting_attachment_search WHERE attachment_id=?", (attachment["id"],))
            # Keep only source/version identity and locator for historical tombstones.
            connection.execute("UPDATE meeting_attachment_spans SET text='' WHERE version_id IN "
                               "(SELECT id FROM meeting_attachment_versions WHERE attachment_id=?)", (attachment["id"],))
            connection.execute("UPDATE meeting_attachment_versions SET content='',content_characters=0,structure_json='{}',warnings_json='[]' WHERE attachment_id=?", (attachment["id"],))
            connection.execute("UPDATE meeting_attachments SET title='已删除的本场资料',filename='',content_type=NULL,purpose='',active_version_id=NULL,"
                               "selected_span_ids_json=NULL,original_relative_path=NULL,original_sha256=NULL,file_size_bytes=0 WHERE id=?", (attachment["id"],))
        return DocumentExtraction("extracted")
