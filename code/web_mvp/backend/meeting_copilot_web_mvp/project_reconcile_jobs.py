"""Explicit, persistent after-meeting reconciliation; candidates require user confirmation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Callable, Mapping
import uuid

from .coach_contract import COACH_ROLE_CONTRACT, COACH_ROLE_CONTRACT_VERSION
from .knowledge_context import retrieve_meeting_knowledge
from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from .pi_coach_runtime import PROTOCOL, PiCoachSidecar
from .project_outcomes import ProjectOutcomes
from .source_library_import import LibrarySourceError
from .user_memory import capture_input_context, input_context_state, memory_context, memory_model_projection

MAX_FINAL_CHARACTERS = 20_000
LEASE_MS = 75_000
MAX_DEFERS = 30


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return time.time_ns() // 1_000_000

class BackgroundReconcileDeferred(RuntimeError):
    def __init__(self, retry_after_ms: int = 2000, code: str = "background_busy") -> None:
        self.retry_after_ms = max(250, min(int(retry_after_ms), 30_000))
        self.code = code
        super().__init__(code)


class ProjectReconcileQueue:
    def __init__(self, repository: KnowledgeRepository, outcomes: ProjectOutcomes) -> None:
        self.repository, self.outcomes = repository, outcomes

    def _get(self, job_id: str) -> dict[str, Any]:
        return self.repository._get("project_reconcile_jobs", job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.repository._lock:
            return self._get(job_id)

    def list(self, project_id: str, *, meeting_id: str | None = None, limit: int = 100,
             cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.repository._lock:
            self.repository._project(project_id, active=False)
            clauses, parameters = ["project_id=?"], [project_id]
            if meeting_id is not None:
                clauses.append("meeting_id=?")
                parameters.append(meeting_id)
            return self.repository._page("project_reconcile_jobs", clauses=clauses, parameters=parameters, limit=limit, cursor=cursor)

    def _snapshot(self, meeting_id: str, document_kind: str, expected_project_revision: int,
                  expected_source_version: int) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        library = self.repository
        context = library.get_meeting_context(meeting_id)
        project_id = context["project_id"]
        if project_id is None:
            raise LibrarySourceError("project_required")
        self.outcomes.reconcile(project_id)
        library._expected(library._project(project_id), expected_project_revision)
        row = library._conn.execute("SELECT document_id FROM review_documents WHERE meeting_id=? AND document_kind=?", (meeting_id, document_kind)).fetchone()
        if row is None:
            raise LibrarySourceError("human_final_required")
        try:
            reference, content = self.outcomes._review_source(row[0], project_id)
        except KnowledgeScopeError:
            raise LibrarySourceError("human_final_required") from None
        if reference["version"] != expected_source_version:
            raise KnowledgeConflict("original source has changed")
        if not content.strip() or len(content.encode("utf-16-le")) // 2 > MAX_FINAL_CHARACTERS:
            raise LibrarySourceError("final_too_large")
        reference["quote"] = content.encode("utf-16-le")[:8000].decode("utf-16-le", errors="ignore")
        documents = retrieve_meeting_knowledge(library, meeting_id, content[:200], include_attachments=False)[:5]
        binding = capture_input_context(library._conn, meeting_id)
        state = self.outcomes.context_for_meeting(meeting_id, max_items=8, max_characters=6000)
        context_payload = {"task_mode": "project_reconcile", "role_contract": COACH_ROLE_CONTRACT,
                           "role_contract_version": COACH_ROLE_CONTRACT_VERSION,
                           "knowledge_context": {"project_id": project_id, "project_revision": binding["project_revision"], "project_state": state},
                           "input_context": binding, "reconciliation_evidence": [reference, *documents],
                           "review_final_content": content, "document_evidence": documents,
                           "user_memory": memory_model_projection(memory_context(library._conn, meeting_id)), "scene": "project"}
        return binding, [reference, *documents], context_payload

    def enqueue(self, meeting_id: str, *, document_kind: str = "minutes", expected_project_revision: int,
                expected_source_version: int, now_ms: int | None = None) -> dict[str, Any]:
        timestamp = _now() if now_ms is None else now_ms
        with self.repository._write():
            binding, refs, payload = self._snapshot(meeting_id, document_kind, expected_project_revision, expected_source_version)
            digest = hashlib.sha256(_json({"binding": binding, "refs": refs}).encode()).hexdigest()
            active = self.repository._conn.execute("SELECT * FROM project_reconcile_jobs WHERE meeting_id=? AND input_hash=? AND status IN ('pending','running')", (meeting_id, digest)).fetchone()
            if active is not None:
                return dict(active)
            job_id = f"reconcile_{uuid.uuid4().hex}"
            self.repository._conn.execute("INSERT INTO project_reconcile_jobs(id,meeting_id,project_id,document_kind,status,base_project_revision,"
                                          "input_context_json,source_refs_json,source_payload_json,input_hash,created_at_ms,updated_at_ms) "
                                          "VALUES(?,?,?,?,'pending',?,?,?,?,?,?,?)", (job_id, meeting_id, binding["project_id"], document_kind,
                                          binding["project_revision"], _json(binding), _json(refs), _json(payload), digest, timestamp, timestamp))
            return self._get(job_id)

    def _validate(self, job: Mapping[str, Any]) -> None:
        self.repository._project(job["project_id"])
        if input_context_state(self.repository._conn, job["meeting_id"], json.loads(job["input_context_json"])) != "valid":
            raise KnowledgeConflict("job input has changed")
        for ref in json.loads(job["source_refs_json"]):
            if ref["source_type"] == "document":
                self.repository.validate_evidence(ref, meeting_id=job["meeting_id"])
            else:
                self.outcomes._refs([ref], job["project_id"])

    def recover_expired(self, *, now_ms: int | None = None) -> int:
        timestamp = _now() if now_ms is None else now_ms
        with self.repository._write():
            rows = self.repository._conn.execute("SELECT * FROM project_reconcile_jobs WHERE status='running' AND lease_until_ms<=?", (timestamp,)).fetchall()
            for row in rows:
                # An interrupted paid request is ambiguous at a relay. Require an explicit retry.
                status = "failed" if row["attempts"] else "pending"
                self.repository._conn.execute("UPDATE project_reconcile_jobs SET status=?,lease_owner=NULL,lease_until_ms=NULL,"
                                              "error_class=?,completed_at_ms=?,updated_at_ms=? WHERE id=?",
                                              (status, "execution_interrupted" if row["attempts"] else None,
                                               timestamp if row["attempts"] else None, timestamp, row["id"]))
            return len(rows)

    def claim(self, *, now_ms: int | None = None) -> dict[str, Any] | None:
        timestamp = _now() if now_ms is None else now_ms
        with self.repository._write():
            self.recover_expired(now_ms=timestamp)
            if self.repository._conn.execute("SELECT 1 FROM project_reconcile_jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = self.repository._conn.execute("SELECT * FROM project_reconcile_jobs WHERE status='pending' AND next_attempt_at_ms<=? ORDER BY created_at_ms,id LIMIT 1", (timestamp,)).fetchone()
            if row is None:
                return None
            job = dict(row)
            try:
                self._validate(job)
            except (KeyError, ValueError, KnowledgeConflict):
                self.repository._conn.execute("UPDATE project_reconcile_jobs SET status='failed',error_class='input_superseded',updated_at_ms=?,completed_at_ms=? WHERE id=?", (timestamp, timestamp, job["id"]))
                return None
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET status='running',lease_owner=?,lease_until_ms=?,updated_at_ms=? WHERE id=?",
                                          (uuid.uuid4().hex, timestamp + LEASE_MS, timestamp, job["id"]))
            return self._get(job["id"])

    def _current(self, job: Mapping[str, Any]) -> dict[str, Any]:
        try:
            current = self._get(job["id"])
        except KeyError:
            raise KnowledgeConflict("job was removed with its source meeting") from None
        if current["status"] != "running" or current["lease_owner"] != job["lease_owner"]:
            raise KnowledgeConflict("job was cancelled or replaced")
        return current

    def mark_started(self, job: Mapping[str, Any]) -> None:
        with self.repository._write():
            current = self._current(job)
            self._validate(current)
            if current["attempts"]:
                raise KnowledgeConflict("paid execution cannot automatically repeat")
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET attempts=attempts+1,updated_at_ms=? WHERE id=?", (_now(), job["id"]))

    def defer(self, job: Mapping[str, Any], deferred: BackgroundReconcileDeferred) -> None:
        with self.repository._write():
            try:
                current = self._current(job)
            except KnowledgeConflict:
                return
            failed = current["attempts"] > 0 or current["defer_count"] >= MAX_DEFERS
            code = "execution_interrupted" if current["attempts"] else deferred.code
            timestamp = _now()
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET status=?,defer_count=defer_count+1,next_attempt_at_ms=?,"
                                          "lease_owner=NULL,lease_until_ms=NULL,error_class=?,completed_at_ms=?,updated_at_ms=? WHERE id=?",
                                          ("failed" if failed else "pending", timestamp + deferred.retry_after_ms, code,
                                           timestamp if failed else None, timestamp, job["id"]))

    def release(self, job: Mapping[str, Any]) -> None:
        with self.repository._write():
            try:
                current = self._current(job)
            except KnowledgeConflict:
                return
            timestamp = _now()
            paid = current["attempts"] > 0
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET status=?,lease_owner=NULL,lease_until_ms=NULL,error_class=?,"
                                          "completed_at_ms=?,updated_at_ms=? WHERE id=?", ("failed" if paid else "pending",
                                          "execution_interrupted" if paid else None, timestamp if paid else None, timestamp, job["id"]))

    def fail(self, job: Mapping[str, Any], code: str) -> None:
        with self.repository._write():
            try:
                self._current(job)
            except KnowledgeConflict:
                return
            timestamp = _now()
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET status='failed',lease_owner=NULL,lease_until_ms=NULL,"
                                          "error_class=?,updated_at_ms=?,completed_at_ms=? WHERE id=?", (code, timestamp, timestamp, job["id"]))

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.repository._write():
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=?,"
                                          "completed_at_ms=? WHERE id=? AND status IN ('pending','running')", (_now(), _now(), job_id))
            return self._get(job_id)

    def retry(self, job_id: str, *, expected_project_revision: int, expected_source_version: int) -> dict[str, Any]:
        with self.repository._write():
            job = self._get(job_id)
            if job["status"] not in {"failed", "cancelled"}:
                raise KnowledgeConflict("only failed or cancelled jobs can be retried")
            binding, refs, payload = self._snapshot(job["meeting_id"], job["document_kind"], expected_project_revision, expected_source_version)
            digest = hashlib.sha256(_json({"binding": binding, "refs": refs}).encode()).hexdigest()
            if self.repository._conn.execute("SELECT 1 FROM project_reconcile_jobs WHERE meeting_id=? AND input_hash=? AND status IN ('pending','running')", (job["meeting_id"], digest)).fetchone():
                raise KnowledgeConflict("matching job already queued")
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET project_id=?,status='pending',base_project_revision=?,input_context_json=?,"
                                          "source_refs_json=?,source_payload_json=?,input_hash=?,attempts=0,defer_count=0,next_attempt_at_ms=0,"
                                          "error_class=NULL,completed_at_ms=NULL,lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE id=?",
                                          (binding["project_id"], binding["project_revision"], _json(binding), _json(refs), _json(payload), digest, _now(), job_id))
            return self._get(job_id)

    def payload(self, job: Mapping[str, Any]) -> dict[str, Any]:
        return {"protocol": PROTOCOL, "request_id": f"{job['id']}:{job['lease_owner']}", "session_id": job["meeting_id"],
                "task_mode": "project_reconcile", "context": json.loads(job["source_payload_json"])}

    def finish(self, job: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any] | None:
        with self.repository._write():
            self._current(job)
            self._validate(job)
            if (not isinstance(changes, Mapping) or set(changes) != {"base_project_revision", "changes"}
                    or type(changes["base_project_revision"]) is not int or changes["base_project_revision"] != job["base_project_revision"]
                    or not isinstance(changes["changes"], list) or len(changes["changes"]) > 12):
                raise ValueError("invalid project reconciliation terminal")
            allowed = {(ref["source_type"], ref["source_id"]) for ref in json.loads(job["source_refs_json"])}
            for change in changes["changes"]:
                if not isinstance(change, Mapping):
                    raise ValueError("invalid project change")
                for ref in change.get("source_refs", []):
                    if not isinstance(ref, Mapping):
                        raise ValueError("invalid project source")
                    if ref.get("source_type") == "document":
                        self.repository.validate_evidence(ref, meeting_id=job["meeting_id"])
                    elif (ref.get("source_type"), ref.get("source_id")) not in allowed:
                        raise KnowledgeScopeError("source was not registered for this job")
            proposal = self.outcomes.propose(job["project_id"], changes["changes"], origin="ai", meeting_id=job["meeting_id"],
                                            expected_project_revision=job["base_project_revision"]) if changes["changes"] else None
            timestamp = _now()
            self.repository._conn.execute("UPDATE project_reconcile_jobs SET status='succeeded',result_proposal_id=?,lease_owner=NULL,lease_until_ms=NULL,"
                                          "error_class=NULL,updated_at_ms=?,completed_at_ms=? WHERE id=?", (proposal["id"] if proposal else None, timestamp, timestamp, job["id"]))
            return proposal


class ProjectReconcileRegistry:
    """Background evidence is allowed after the meeting ends, within its captured scope."""

    def __init__(self, queue: ProjectReconcileQueue, job: Mapping[str, Any]) -> None:
        self.queue, self.job = queue, dict(job)
        self.known = {ref["source_id"]: ref for ref in json.loads(job["source_refs_json"])}
        self.reads = 0

    def register_documents(self, evidence):
        if not isinstance(evidence, list) or len(evidence) > 6 or sum(len(ref.get("quote", "")) for ref in evidence) > 3600:
            raise ValueError("document context exceeds budget")
        for ref in evidence:
            if not isinstance(ref, Mapping) or ref.get("source_type") != "document":
                raise ValueError("project reconciliation requires confirmed project documents")
            self.queue.repository.validate_evidence(ref, meeting_id=self.job["meeting_id"])
            self.known[ref["source_id"]] = dict(ref)
        return evidence

    def _read(self):
        if self.reads >= 5:
            raise ValueError("reconciliation read budget exhausted")
        self.reads += 1
        with self.queue.repository._lock:
            self.queue._validate(self.job)

    def search_documents(self, query, *, limit=6):
        self._read()
        if type(limit) is not int or not 1 <= limit <= 6:
            raise ValueError("invalid source limit")
        scene = json.loads(self.job["input_context_json"]).get("memory_scene", "general")
        return self.register_documents(retrieve_meeting_knowledge(self.queue.repository, self.job["meeting_id"], query,
                                                                 memory_scene=scene, include_attachments=False)[:limit])

    def read_project_state(self):
        self._read()
        state = self.queue.outcomes.context_for_meeting(self.job["meeting_id"], max_items=8, max_characters=6000)
        return [{"source_type": "project_state", **state, "snapshot_hash": hashlib.sha256(_json(state).encode()).hexdigest()}]

    def validate_project_changes(self, terminal):
        if (not isinstance(terminal, Mapping) or set(terminal) != {"base_project_revision", "changes"}
                or type(terminal["base_project_revision"]) is not int
                or terminal["base_project_revision"] != self.job["base_project_revision"]
                or not isinstance(terminal["changes"], list) or len(terminal["changes"]) > 12
                or len(_json(terminal).encode()) > 44_000):
            raise ValueError("invalid project terminal")
        with self.queue.repository._write():
            self.queue._validate(self.job)
            for change in terminal["changes"]:
                if not isinstance(change, Mapping):
                    raise ValueError("invalid project change")
                if not change.get("source_refs"):
                    raise ValueError("AI reconciliation requires registered sources for every change")
                for ref in change["source_refs"]:
                    registered = self.known.get(ref.get("source_id")) if isinstance(ref, Mapping) else None
                    if registered is None or any(ref.get(key) != value for key, value in registered.items() if key != "quote"):
                        raise ValueError("source metadata differs from registered evidence")
                    content = json.loads(self.job["source_payload_json"])["review_final_content"] if ref["source_type"] == "review_document" else registered["quote"]
                    if not isinstance(ref.get("quote"), str) or not ref["quote"].strip() or ref["quote"] not in content:
                        raise ValueError("project source is not registered verbatim text")
            if terminal["changes"]:
                # The existing transition validator also checks references, owners, dates and closed items.
                # Its ephemeral proposal is rolled back; the terminal tool never commits product state.
                self.queue.repository._conn.execute("SAVEPOINT host_project_validation")
                try:
                    self.queue.outcomes.propose(self.job["project_id"], terminal["changes"], origin="ai", meeting_id=self.job["meeting_id"],
                                                expected_project_revision=self.job["base_project_revision"])
                finally:
                    self.queue.repository._conn.execute("ROLLBACK TO host_project_validation")
                    self.queue.repository._conn.execute("RELEASE host_project_validation")
        return json.loads(_json(terminal))


class ProjectReconcilePiExecutor:
    def __init__(self, queue: ProjectReconcileQueue) -> None:
        self.queue = queue
        self.registry = None
        self.runtime = PiCoachSidecar(timeout_seconds=60, evidence_registry_factory=lambda meeting_id: self.registry)

    async def __call__(self, payload, job, config):
        if not self.runtime.available:
            raise RuntimeError("pi_unavailable")
        self.registry = ProjectReconcileRegistry(self.queue, job)
        timeout_ms = int(max(1, min(config.timeout_seconds, 60)) * 1000)
        request = {**payload, "provider": {"base_url": config.base_url, "api_key": config.api_key, "model": config.model,
                                           "api_style": config.api_style, "timeout_ms": timeout_ms, "decision_timeout_ms": timeout_ms}}
        await asyncio.to_thread(self.queue.mark_started, job)
        try:
            return await self.runtime.evaluate(request)
        except asyncio.CancelledError:
            # evaluate's to_thread worker stops on a cancellation flag. Wait for its local Node ACK.
            await asyncio.shield(asyncio.to_thread(self.runtime.close))
            raise

    async def close(self):
        await asyncio.to_thread(self.runtime.close)


class ProjectReconcileRunner:
    def __init__(self, queue: ProjectReconcileQueue, executor: Callable) -> None:
        self.queue, self.executor = queue, executor
        self._task: asyncio.Task | None = None
        self._execution: asyncio.Task | None = None
        self._job: dict[str, Any] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def cancel(self, job_id: str) -> dict[str, Any]:
        result = await asyncio.to_thread(self.queue.cancel, job_id)
        if self._job is not None and self._job["id"] == job_id and self._execution is not None:
            self._execution.cancel()
            try:
                await self._execution
            except asyncio.CancelledError:
                pass
        return result

    async def _run(self) -> None:
        try:
            while True:
                job = await asyncio.to_thread(self.queue.claim)
                if job is None:
                    await asyncio.sleep(0.2)
                    continue
                self._job = job
                try:
                    self._execution = asyncio.create_task(self.executor(self.queue.payload(job), job))
                    result = await self._execution
                    terminal = result.get("host_project_changes")
                    if not isinstance(terminal, Mapping):
                        raise ValueError("host project terminal missing")
                    await asyncio.to_thread(self.queue.finish, job, terminal)
                except BackgroundReconcileDeferred as deferred:
                    await asyncio.to_thread(self.queue.defer, job, deferred)
                except asyncio.CancelledError:
                    try:
                        current = await asyncio.to_thread(self.queue.get, job["id"])
                    except KeyError:
                        current = None
                    if asyncio.current_task().cancelling():
                        await asyncio.to_thread(self.queue.release, job)
                        raise
                    if current is None or current["status"] == "cancelled":
                        continue
                    await asyncio.to_thread(self.queue.release, job)
                    raise
                except (KnowledgeConflict, KnowledgeScopeError):
                    await asyncio.to_thread(self.queue.fail, job, "input_superseded")
                except Exception:
                    await asyncio.to_thread(self.queue.fail, job, "reconciliation_failed")
                finally:
                    self._execution, self._job = None, None
        finally:
            if self._execution is not None:
                self._execution.cancel()
