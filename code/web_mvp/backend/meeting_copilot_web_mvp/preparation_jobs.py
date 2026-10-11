"""Explicit, durable project preparation. Polling/restart never grants paid execution."""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from .knowledge_repository import KnowledgeConflict, KnowledgeScopeError
from .pi_coach_runtime import BRIDGE_RESPONSE_GRACE_SECONDS, PROTOCOL, PiCoachRuntimeError, PiCoachSidecar
from .prepared_brief import PreparedBriefSources, canonical, digest
from .project_reconcile_jobs import BackgroundReconcileDeferred
from .project_reconcile_admission import log_background_failure


def now():
    return time.time_ns() // 1_000_000


class PreparationQueue:
    def __init__(self, repository):
        self.repository = repository
        self.sources = PreparedBriefSources(repository)
        self.eligible = set()

    def get(self, job_id):
        with self.repository._lock:
            return self.repository._get("project_preparation_jobs", job_id)

    def list(self, project_id, *, limit=100, cursor=None):
        with self.repository._lock:
            self.repository._project(project_id, active=False)
            return self.repository._page("project_preparation_jobs", clauses=["project_id=?"], parameters=[project_id], limit=limit, cursor=cursor)

    def enqueue(self, project_id, **parameters):
        with self.repository._write():
            snapshot, payload = self.sources.capture(project_id, **parameters)
            input_hash = digest(snapshot)
            old = self.repository._conn.execute("SELECT * FROM project_preparation_jobs WHERE project_id=? AND input_hash=? "
                "AND status IN ('pending','running','succeeded') ORDER BY created_at_ms DESC LIMIT 1", (project_id, input_hash)).fetchone()
            if old is not None:
                if old["status"] == "pending":
                    self.eligible.add(old["id"])
                return dict(old)
            job_id, timestamp = "prepare_" + uuid.uuid4().hex, now()
            self.repository._conn.execute("INSERT INTO project_preparation_jobs(id,project_id,status,snapshot_json,source_payload_json,"
                "input_hash,base_project_revision,created_at_ms,updated_at_ms) VALUES(?,?,'pending',?,?,?,?,?,?)",
                (job_id, project_id, canonical(snapshot), canonical(payload), input_hash, snapshot["project_revision"], timestamp, timestamp))
            self._register_sources(job_id, [*payload["preparation"]["sources"], *snapshot["memory_sources"]])
            self.eligible.add(job_id)
            return self.get(job_id)

    def _register_sources(self, job_id, sources):
        for ref in sources:
            documents = [ref] if ref["source_type"] == "document" else ref.get("source_refs", [])
            for document in documents:
                if document.get("source_type") == "document":
                    self.repository._conn.execute("INSERT OR IGNORE INTO preparation_source_versions(job_id,version_id) VALUES(?,?)", (job_id, document["version_id"]))

    def validate(self, job):
        self.sources.validate_snapshot(json.loads(job["snapshot_json"]))
        payload = json.loads(job["source_payload_json"])
        selection = json.loads(job["snapshot_json"])["selection"]
        for ref in payload["preparation"]["sources"]:
            self.sources.validate_ref(ref, selection)

    def _current(self, job):
        current = self.get(job["id"])
        if current["status"] != "running" or current["lease_owner"] != job["lease_owner"]:
            raise KnowledgeConflict("preparation was cancelled or replaced")
        return current

    def recover_expired(self):
        with self.repository._write():
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status=CASE paid_started WHEN 1 THEN 'failed' ELSE 'pending' END,"
                "error_code=CASE paid_started WHEN 1 THEN 'execution_interrupted' ELSE NULL END,lease_owner=NULL,lease_until_ms=NULL,"
                "revision=revision+1,updated_at_ms=? WHERE status='running' AND lease_until_ms<=?", (now(), now()))

    def claim(self):
        with self.repository._write():
            self.recover_expired()
            if not self.eligible or self.repository._conn.execute("SELECT 1 FROM project_preparation_jobs WHERE status='running'").fetchone():
                return None
            row = self.repository._conn.execute("SELECT * FROM project_preparation_jobs WHERE id IN (" + ",".join("?" for _ in self.eligible)
                + ") AND status='pending' AND next_attempt_at_ms<=? ORDER BY created_at_ms,id LIMIT 1", (*self.eligible, now())).fetchone()
            if row is None:
                return None
            job = dict(row)
            try:
                self.validate(job)
            except (KnowledgeConflict, KnowledgeScopeError, KeyError):
                self.repository._conn.execute("UPDATE project_preparation_jobs SET status='failed',error_code='input_superseded',revision=revision+1,updated_at_ms=?,completed_at_ms=? WHERE id=?", (now(), now(), job["id"]))
                self.eligible.discard(job["id"])
                return None
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status='running',lease_owner=?,lease_until_ms=?,revision=revision+1,updated_at_ms=? WHERE id=?",
                (uuid.uuid4().hex, now()+90_000, now(), job["id"]))
            return self.get(job["id"])

    def mark_started(self, job):
        with self.repository._write():
            current = self._current(job)
            self.validate(current)
            if current["paid_started"]:
                raise KnowledgeConflict("preparation cannot automatically repeat paid execution")
            self.repository._conn.execute("UPDATE project_preparation_jobs SET paid_started=1,attempts=attempts+1,revision=revision+1,updated_at_ms=? WHERE id=?", (now(), job["id"]))

    def fail(self, job, code):
        with self.repository._write():
            try:
                self._current(job)
            except (KnowledgeConflict, KeyError):
                return
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status='failed',error_code=?,lease_owner=NULL,lease_until_ms=NULL,revision=revision+1,updated_at_ms=?,completed_at_ms=? WHERE id=?", (code, now(), now(), job["id"]))
            self.eligible.discard(job["id"])

    def release(self, job, deferred=None):
        with self.repository._write():
            try:
                current = self._current(job)
            except (KnowledgeConflict, KeyError):
                return
            if current["paid_started"] or current["defer_count"] >= 20:
                self.fail(job, "execution_interrupted" if current["paid_started"] else "background_deferred")
                return
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status='pending',defer_count=defer_count+1,"
                "next_attempt_at_ms=?,error_code=?,lease_owner=NULL,lease_until_ms=NULL,revision=revision+1,updated_at_ms=? WHERE id=?",
                (now() + (deferred.retry_after_ms if deferred else 0), deferred.code if deferred else None, now(), job["id"]))

    def cancel(self, job_id):
        with self.repository._write():
            self.get(job_id)
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,revision=revision+1,updated_at_ms=?,completed_at_ms=? WHERE id=? AND status IN ('pending','running')", (now(), now(), job_id))
            self.eligible.discard(job_id)
            return self.get(job_id)

    def retry(self, job_id, *, expected_revision, expected_input_hash, expected_project_revision):
        with self.repository._write():
            job = self.get(job_id)
            if job["revision"] != expected_revision or job["input_hash"] != expected_input_hash:
                raise KnowledgeConflict("preparation job changed")
            if job["status"] not in {"failed", "cancelled"} and not (job["status"] == "pending" and job_id not in self.eligible):
                raise KnowledgeConflict("preparation cannot retry in current state")
            old = json.loads(job["snapshot_json"])
            snapshot, payload = self.sources.capture(**old["selection"], **{key: old[key] for key in ("goal", "role", "focus", "scene")}, expected_project_revision=expected_project_revision)
            self.repository._conn.execute("DELETE FROM preparation_source_versions WHERE job_id=?", (job_id,))
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status='pending',snapshot_json=?,source_payload_json=?,input_hash=?,"
                "base_project_revision=?,paid_started=0,attempts=0,defer_count=0,next_attempt_at_ms=0,error_code=NULL,result_json=NULL,"
                "lease_owner=NULL,lease_until_ms=NULL,completed_at_ms=NULL,revision=revision+1,updated_at_ms=? WHERE id=?",
                (canonical(snapshot), canonical(payload), digest(snapshot), snapshot["project_revision"], now(), job_id))
            self._register_sources(job_id, [*payload["preparation"]["sources"], *snapshot["memory_sources"]])
            self.eligible.add(job_id)
            return self.get(job_id)

    def payload(self, job):
        return {"protocol": PROTOCOL, "request_id": job["id"]+":"+job["lease_owner"], "session_id": "preparation:"+job["project_id"],
            "context": json.loads(job["source_payload_json"])}

    def finish(self, job, terminal):
        with self.repository._write():
            self._current(job)
            payload = json.loads(self.get(job["id"])["source_payload_json"])
            result = self.sources.validate_result(terminal, json.loads(job["snapshot_json"]), {canonical(ref) for ref in payload["preparation"]["sources"]})
            self.repository._conn.execute("UPDATE project_preparation_jobs SET status='succeeded',result_json=?,error_code=NULL,lease_owner=NULL,lease_until_ms=NULL,revision=revision+1,updated_at_ms=?,completed_at_ms=? WHERE id=?", (canonical(result), now(), now(), job["id"]))
            self.eligible.discard(job["id"])
            return result


class PreparationRegistry:
    def __init__(self, queue, job):
        self.queue, self.job = queue, job

    def register_documents(self, documents):
        for ref in documents:
            self.queue.sources.validate_ref(ref, json.loads(self.job["snapshot_json"])["selection"])

    def search_documents(self, query, *, limit=6):
        with self.queue.repository._write():
            self.queue._current(self.job)
            self.queue.validate(self.job)
            snapshot = json.loads(self.job["snapshot_json"])
            refs = self.queue.sources.documents(snapshot["selection"], query, limit=limit, scene=snapshot["scene"])
            current = self.queue.get(self.job["id"])
            payload = json.loads(current["source_payload_json"])
            known = payload["preparation"]["sources"]
            for ref in refs:
                if canonical(ref) not in {canonical(item) for item in known}:
                    if len(known) >= 20:
                        break
                    known.append(ref)
            self.queue.repository._conn.execute("UPDATE project_preparation_jobs SET source_payload_json=? WHERE id=?", (canonical(payload), self.job["id"]))
            self.queue._register_sources(self.job["id"], known)
            return [ref for ref in refs if canonical(ref) in {canonical(item) for item in known}]

    def read_project_state(self):
        self.queue.validate(self.job)
        return [ref for ref in json.loads(self.job["source_payload_json"])["preparation"]["sources"] if ref["source_type"] == "project_state"]

    def validate_preparation_brief(self, terminal):
        with self.queue.repository._lock:
            self.queue._current(self.job)
            current = self.queue.get(self.job["id"])
            known = {canonical(ref) for ref in json.loads(current["source_payload_json"])["preparation"]["sources"]}
            return self.queue.sources.validate_result(terminal, json.loads(self.job["snapshot_json"]), known)


class PreparationPiExecutor:
    def __init__(self, queue):
        self.queue, self.registry = queue, None
        self.runtime = PiCoachSidecar(timeout_seconds=45 + BRIDGE_RESPONSE_GRACE_SECONDS,
                                     evidence_registry_factory=lambda _: self.registry)

    async def __call__(self, payload, job, config):
        if not self.runtime.available:
            raise RuntimeError("pi_unavailable")
        self.registry = PreparationRegistry(self.queue, job)
        timeout_ms = int(max(1, min(config.timeout_seconds, 45)) * 1000)
        request = {**payload, "provider": {"base_url": config.base_url, "api_key": config.api_key, "model": config.model,
            "api_style": config.api_style, "timeout_ms": timeout_ms, "decision_timeout_ms": timeout_ms}}
        await asyncio.to_thread(self.queue.mark_started, job)
        try:
            return await self.runtime.evaluate(request)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self.runtime.close))
            raise

    async def close(self):
        await asyncio.to_thread(self.runtime.close)


class PreparationRunner:
    def __init__(self, queue, executor):
        self.queue, self.executor = queue, executor
        self._task = self._execution = self._job = None

    async def start(self):
        await asyncio.to_thread(self.queue.recover_expired)
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def cancel(self, job_id):
        result = await asyncio.to_thread(self.queue.cancel, job_id)
        if self._job and self._job["id"] == job_id and self._execution:
            self._execution.cancel()
            try:
                await self._execution
            except asyncio.CancelledError:
                pass
        return result

    async def _run(self):
        try:
            while True:
                job = await asyncio.to_thread(self.queue.claim)
                if job is None:
                    await asyncio.sleep(0.2)
                    continue
                self._job = job
                try:
                    self._execution = asyncio.create_task(self.executor(self.queue.payload(job), {**job, "meeting_id": "preparation:"+job["project_id"]}))
                    result = await self._execution
                    await asyncio.to_thread(self.queue.finish, job, result["host_preparation_brief"])
                except BackgroundReconcileDeferred as deferred:
                    await asyncio.to_thread(self.queue.release, job, deferred)
                except asyncio.CancelledError:
                    await asyncio.to_thread(self.queue.release, job)
                    if asyncio.current_task().cancelling():
                        raise
                except (KnowledgeConflict, KnowledgeScopeError, KeyError):
                    await asyncio.to_thread(self.queue.fail, job, "input_superseded")
                except PiCoachRuntimeError as exc:
                    code = log_background_failure(exc, job=job, purpose="meeting_preparation")
                    await asyncio.to_thread(self.queue.fail, job, code)
                except Exception:
                    await asyncio.to_thread(self.queue.fail, job, "preparation_failed")
                finally:
                    self._execution = self._job = None
        finally:
            if self._execution is not None:
                self._execution.cancel()
                try:
                    await self._execution
                except asyncio.CancelledError:
                    pass
