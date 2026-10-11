"""Single-flight durable imports, isolated from the realtime process and model lanes."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
import uuid

from .document_import import DocumentExtraction, DocumentImportService, MAX_UPLOAD_BYTES, extract_document
from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from .storage_governance import ensure_private_directory, harden_private_file


IMPORT_TIMEOUT_SECONDS = 60
IMPORT_MEMORY_BYTES = 512 * 1024 * 1024
JOB_LEASE_MS = 75_000
MAX_RESULT_BYTES = 20 * 1024 * 1024
# Project imports and meeting attachments share one bounded parser subprocess.
# Acquire before claiming a durable lease, so waiting never consumes that lease.
CONVERSION_WORKER_LOCK = threading.Lock()


class KnowledgeJobQueue:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def _get(self, job_id: str) -> dict[str, Any]:
        return self.repository._get("knowledge_jobs", job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.repository._lock:
            return self._get(job_id)

    def list(self, project_id: str, *, status: str | None = None, document_id: str | None = None, limit: int = 100,
             cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.repository._lock:
            self.repository._project(project_id, active=False)
            clauses, parameters = ["project_id=?"], [project_id]
            if document_id is not None:
                if self.repository._get("knowledge_documents", document_id)["project_id"] != project_id:
                    raise KnowledgeScopeError("document belongs to another project")
                clauses.append("document_id=?")
                parameters.append(document_id)
            if status is not None:
                if status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
                    raise ValueError("invalid job status")
                clauses.append("status=?")
                parameters.append(status)
            return self.repository._page("knowledge_jobs", clauses=clauses, parameters=parameters, limit=limit, cursor=cursor)

    def enqueue(self, document_id: str, *, job_type: str = "import", now_ms: int | None = None) -> dict[str, Any]:
        if job_type not in {"import", "cleanup"}:
            raise ValueError("invalid knowledge job type")
        timestamp = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        with self.repository._write():
            document = self.repository._get("knowledge_documents", document_id)
            if job_type == "import":
                self.repository._project(document["project_id"])
                if document["status"] in {"disabled", "deleted"}:
                    raise KnowledgeScopeError("document is disabled or deleted")
            elif document["status"] != "deleted":
                raise KnowledgeScopeError("cleanup requires a deleted document")
            row = self.repository._conn.execute("SELECT * FROM knowledge_jobs WHERE document_id=? AND type=? "
                                                "AND status IN ('queued','running')", (document_id, job_type)).fetchone()
            if row is not None:
                return dict(row)
            job_id = f"knowledge_job_{uuid.uuid4().hex}"
            self.repository._conn.execute("INSERT INTO knowledge_jobs(id,document_id,project_id,type,input_revision,status,progress,"
                                          "created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,'queued','waiting',?,?)",
                                          (job_id, document_id, document["project_id"], job_type, document["revision"], timestamp, timestamp))
            return self._get(job_id)

    def recover_expired(self, *, now_ms: int | None = None) -> int:
        timestamp = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        with self.repository._write():
            return self.repository._conn.execute("UPDATE knowledge_jobs SET status='queued',progress='waiting_recovery',"
                                                  "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE status='running' "
                                                  "AND lease_until_ms<=?", (timestamp, timestamp)).rowcount

    def recover_orphans(self) -> int:
        """Recover request crashes between persisting an original/tombstone and queuing its work."""
        with self.repository._write():
            rows = self.repository._conn.execute("SELECT d.id,d.status FROM knowledge_documents d JOIN knowledge_projects p ON p.id=d.project_id "
                                                 "WHERE d.original_relative_path IS NOT NULL AND ((d.status='draft' AND d.import_status='pending' "
                                                 "AND p.archived_at_ms IS NULL) OR d.status='deleted') AND NOT EXISTS "
                                                 "(SELECT 1 FROM knowledge_jobs j WHERE j.document_id=d.id)").fetchall()
            for row in rows:
                self.enqueue(row["id"], job_type="cleanup" if row["status"] == "deleted" else "import")
            return len(rows)

    def claim(self, *, now_ms: int | None = None) -> dict[str, Any] | None:
        timestamp = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        with self.repository._write():
            self.recover_expired(now_ms=timestamp)
            row = self.repository._conn.execute("SELECT id FROM knowledge_jobs WHERE status='queued' AND next_retry_at_ms<=? "
                                                "ORDER BY CASE type WHEN 'cleanup' THEN 0 ELSE 1 END,created_at_ms,id LIMIT 1",
                                                (timestamp,)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self.repository._conn.execute("UPDATE knowledge_jobs SET status='running',progress=CASE type WHEN 'cleanup' THEN 'cleaning' ELSE 'extracting' END,attempt=attempt+1,"
                                          "lease_token=?,lease_until_ms=?,updated_at_ms=? WHERE id=?",
                                          (token, timestamp + JOB_LEASE_MS, timestamp, row[0]))
            return self._get(row[0])

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.repository._write():
            job = self._get(job_id)
            if job["status"] in {"queued", "running"}:
                self.repository._conn.execute("UPDATE knowledge_jobs SET status='cancelled',progress='cancelled',lease_token=NULL,"
                                              "lease_until_ms=NULL,error_code='user_cancelled',updated_at_ms=? WHERE id=?",
                                              (time.time_ns() // 1_000_000, job_id))
                if job["type"] == "import":
                    self.repository.set_import_status(job["document_id"], "failed", error_code="user_cancelled")
            return self._get(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        with self.repository._write():
            job = self._get(job_id)
            if job["status"] not in {"failed", "cancelled"}:
                raise KnowledgeConflict("only failed or cancelled jobs can be retried")
            document = self.repository._get("knowledge_documents", job["document_id"])
            if job["type"] == "import":
                self.repository._project(document["project_id"])
                if document["status"] in {"disabled", "deleted"}:
                    raise KnowledgeScopeError("document is disabled or deleted")
                self.repository.set_import_status(document["id"], "pending")
            if self.repository._conn.execute("SELECT 1 FROM knowledge_jobs WHERE document_id=? AND type=? "
                                              "AND status IN ('queued','running')", (job["document_id"], job["type"])).fetchone():
                raise KnowledgeConflict("another job is already queued")
            self.repository._conn.execute("UPDATE knowledge_jobs SET status='queued',progress='waiting',error_code=NULL,"
                                          "input_revision=?,lease_token=NULL,lease_until_ms=NULL,next_retry_at_ms=0,updated_at_ms=? WHERE id=?",
                                          (document["revision"], time.time_ns() // 1_000_000, job_id))
            return self._get(job_id)

    def release(self, job: Mapping[str, Any]) -> None:
        with self.repository._write():
            self.repository._conn.execute("UPDATE knowledge_jobs SET status='queued',progress='waiting_recovery',"
                                          "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE id=? AND status='running' AND lease_token=?",
                                          (time.time_ns() // 1_000_000, job["id"], job["lease_token"]))

    def finish(self, job: Mapping[str, Any], result: DocumentExtraction) -> bool:
        with self.repository._write():
            current = self._get(job["id"])
            if current["status"] != "running" or current["lease_token"] != job["lease_token"]:
                return False
            document = self.repository._get("knowledge_documents", job["document_id"])
            project = self.repository._project(document["project_id"], active=False)
            if job["type"] == "import" and (document["revision"] != job["input_revision"]
                    or document["status"] in {"disabled", "deleted"} or project["archived_at_ms"] is not None):
                result = DocumentExtraction("failed", error_code="input_superseded")
            version = None
            if result.content:
                version = self.repository.add_version(document["id"], result.content, spans=result.spans, origin="extracted",
                                                      expected_revision=job["input_revision"])
            succeeded = result.status in {"extracted", "needs_ocr"}
            if job["type"] == "import" and document["status"] != "deleted" and result.error_code != "input_superseded":
                self.repository.set_import_status(document["id"], result.status, error_code=result.error_code)
            self.repository._conn.execute("UPDATE knowledge_jobs SET status=?,progress=?,error_code=?,result_version_id=?,"
                                          "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE id=?",
                                          ("succeeded" if succeeded else "failed", ("cleaned" if job["type"] == "cleanup" else "preview_ready") if succeeded else "failed",
                                           result.error_code, version["id"] if version else None, time.time_ns() // 1_000_000, job["id"]))
            return True

    def delete_document(self, document_id: str, *, expected_revision: int) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.repository._write():
            current = self.repository._get("knowledge_documents", document_id)
            if current["status"] == "deleted":
                previous = self.repository._conn.execute("SELECT * FROM knowledge_jobs WHERE document_id=? AND type='cleanup' "
                                                         "ORDER BY updated_at_ms DESC,id DESC LIMIT 1", (document_id,)).fetchone()
                return current, dict(previous) if previous is not None else self.enqueue(document_id, job_type="cleanup")
            document = self.repository.set_document_status(document_id, "deleted", expected_revision=expected_revision)
            self.repository._conn.execute("UPDATE knowledge_jobs SET status='cancelled',progress='cancelled',error_code='document_deleted',"
                                          "lease_token=NULL,lease_until_ms=NULL WHERE document_id=? AND type='import' AND status IN ('queued','running')",
                                          (document_id,))
            return document, self.enqueue(document_id, job_type="cleanup")


def _attach_windows_memory_limit(process: subprocess.Popen) -> Any:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(field, ctypes.c_uint64) for field in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = (ctypes.c_void_p, wintypes.LPCWSTR), wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x100 | 0x2000  # PROCESS_MEMORY | KILL_ON_JOB_CLOSE
    limits.ProcessMemoryLimit = IMPORT_MEMORY_BYTES
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        if handle:
            kernel.CloseHandle(handle)
        raise OSError("knowledge worker resource isolation unavailable")
    if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle)):
        kernel.CloseHandle(handle)
        raise OSError("knowledge worker resource isolation unavailable")
    return handle, kernel


class KnowledgeJobRunner:
    def __init__(self, queue: KnowledgeJobQueue, importer: DocumentImportService, *, timeout_seconds: float = IMPORT_TIMEOUT_SECONDS) -> None:
        self.queue, self.importer = queue, importer
        self.timeout_seconds = max(0.05, min(float(timeout_seconds), IMPORT_TIMEOUT_SECONDS))
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None
        self.results_dir = ensure_private_directory(importer.data_dir / "knowledge_job_results")

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            await asyncio.to_thread(self.queue.recover_expired)
            await asyncio.to_thread(self.queue.recover_orphans)
            self._task = asyncio.create_task(self._loop(), name="knowledge-import-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if not CONVERSION_WORKER_LOCK.acquire(blocking=False):
                await asyncio.sleep(0.1)
                continue
            job = None
            try:
                job = await asyncio.to_thread(self.queue.claim)
                if job is not None:
                    if job["type"] == "cleanup":
                        result = await asyncio.to_thread(self._cleanup, job)
                    else:
                        result = await asyncio.to_thread(self._convert, job)
                    if self._stop.is_set():
                        await asyncio.to_thread(self.queue.release, job)
                    else:
                        await asyncio.to_thread(self.queue.finish, job, result)
            except Exception:
                # Keep the local queue alive without exposing parser data or paths.
                if job is not None:
                    await asyncio.to_thread(self.queue.finish, job, DocumentExtraction("failed", error_code="knowledge_worker_failed"))
            finally:
                CONVERSION_WORKER_LOCK.release()
            await asyncio.sleep(0.05 if job is not None else 0.2)

    def _convert(self, job: Mapping[str, Any]) -> DocumentExtraction:
        output = self.results_dir / f"{uuid.uuid4().hex}.json"
        process, limit_handle = None, None
        try:
            document = self.importer.repository.get_document(job["document_id"])
            original = self.importer.original_path(job["document_id"])
            environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH", "LANG") if key in os.environ}
            environment.update({"PYTHON_DOTENV_DISABLED": "1", "PYTHONIOENCODING": "utf-8",
                                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
            process = subprocess.Popen([sys.executable, "-B", "-m", "meeting_copilot_web_mvp.knowledge_jobs", "--extract",
                                        str(original), str(output), document["filename"], document["content_type"] or ""],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env=environment, cwd=Path(__file__).resolve().parent.parent,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            limit_handle = _attach_windows_memory_limit(process)
            deadline = time.monotonic() + self.timeout_seconds
            while process.poll() is None:
                if self._stop.is_set() or self.queue.get(job["id"])["status"] != "running":
                    return DocumentExtraction("failed", error_code="worker_interrupted")
                if time.monotonic() >= deadline:
                    return DocumentExtraction("failed", error_code="conversion_timeout")
                time.sleep(0.05)
            if process.returncode != 0 or not output.is_file() or output.stat().st_size > MAX_RESULT_BYTES:
                return DocumentExtraction("failed", error_code="conversion_process_failed")
            data = json.loads(output.read_text(encoding="utf-8"))
            return DocumentExtraction(data["status"], data.get("content", ""), tuple(data.get("spans", [])), data.get("error_code"),
                                      data.get("format", ""), data.get("structure", {}), tuple(data.get("warnings", [])))
        except (OSError, ValueError, KeyError):
            return DocumentExtraction("failed", error_code="conversion_process_failed")
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if limit_handle is not None:
                handle, kernel = limit_handle
                kernel.CloseHandle(handle)
            output.unlink(missing_ok=True)

    def _cleanup(self, job: Mapping[str, Any]) -> DocumentExtraction:
        document = self.importer.repository.get_document(job["document_id"])
        if document["status"] != "deleted":
            return DocumentExtraction("failed", error_code="cleanup_requires_deleted_document")
        relative = document["original_relative_path"]
        if relative is not None:
            if not re.fullmatch(r"knowledge_assets/[0-9a-f]{32}/original\.(txt|md|docx|pdf)", relative):
                return DocumentExtraction("failed", error_code="unsafe_original_path")
            original = self.importer.data_dir / relative
            if any(path.is_symlink() for path in (self.importer.assets_dir, original.parent, original)):
                return DocumentExtraction("failed", error_code="unsafe_original_path")
            if not original.resolve().is_relative_to(self.importer.assets_dir):
                return DocumentExtraction("failed", error_code="unsafe_original_path")
            original.unlink(missing_ok=True)
            temporary = original.parent / "original.tmp"
            if temporary.is_symlink():
                return DocumentExtraction("failed", error_code="unsafe_original_path")
            temporary.unlink(missing_ok=True)
            if original.parent.exists() and not any(original.parent.iterdir()):
                original.parent.rmdir()
        with self.queue.repository._write():
            connection = self.queue.repository._conn
            connection.execute("DELETE FROM knowledge_search WHERE document_id=?", (document["id"],))
            connection.execute("DELETE FROM knowledge_spans WHERE version_id IN (SELECT id FROM knowledge_versions WHERE document_id=?)", (document["id"],))
            connection.execute("DELETE FROM knowledge_versions WHERE document_id=?", (document["id"],))
            connection.execute("UPDATE knowledge_documents SET active_version_id=NULL,original_relative_path=NULL,original_sha256=NULL,"
                               "file_size_bytes=0 WHERE id=?", (document["id"],))
        return DocumentExtraction("extracted")


def _extract_cli() -> int:
    if len(sys.argv) != 6 or sys.argv[1] != "--extract":
        return 2
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (IMPORT_MEMORY_BYTES, IMPORT_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (IMPORT_TIMEOUT_SECONDS, IMPORT_TIMEOUT_SECONDS + 1))
    original, output = Path(sys.argv[2]), Path(sys.argv[3])
    if original.is_symlink() or not original.is_file() or original.stat().st_size > MAX_UPLOAD_BYTES:
        return 2
    result = extract_document(sys.argv[4], original.read_bytes(), sys.argv[5] or None)
    output.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")
    harden_private_file(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(_extract_cli())
