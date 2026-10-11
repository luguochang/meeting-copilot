"""Source-bound memory candidates. A process restart never grants paid execution."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Mapping

from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from .memory_candidates import digest, explicit_declarations
from .pi_coach_runtime import BRIDGE_RESPONSE_GRACE_SECONDS, PROTOCOL, PiCoachRuntimeError, PiCoachSidecar
from .project_reconcile_admission import log_background_failure
from .project_reconcile_jobs import BackgroundReconcileDeferred
from .source_library_provenance import library_source_is_current, source_snapshot
from .user_memory import UserMemory

LEASE_MS = 90_000


def _now() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MemoryReconcileQueue:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository
        self.memory = UserMemory(repository)
        # Permission is granted by a new confirmation or explicit API request,
        # never reconstructed from pending rows at startup or GET polling.
        self.eligible: set[str] = set()

    def settings(self, project_id: str) -> dict[str, Any]:
        with self.repository._lock:
            self.repository._project(project_id)
            row = self.repository._conn.execute("SELECT * FROM memory_reconcile_settings WHERE project_id=?", (project_id,)).fetchone()
            return dict(row) if row else {"project_id": project_id, "mode": "explicit_only", "revision": 0, "updated_at_ms": 0}

    def set_settings(self, project_id: str, *, mode: str, expected_revision: int) -> tuple[dict[str, Any], list[str]]:
        if mode not in {"off", "explicit_only"} or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid memory reconciliation settings")
        with self.repository._write():
            old = self.settings(project_id)
            if old["revision"] != expected_revision:
                raise KnowledgeConflict("memory settings changed")
            now = _now()
            self.repository._conn.execute("INSERT INTO memory_reconcile_settings VALUES(?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET mode=excluded.mode,revision=excluded.revision,updated_at_ms=excluded.updated_at_ms",
                (project_id, mode, old["revision"] + 1, now))
            cancelled = []
            if mode == "off":
                cancelled = [row[0] for row in self.repository._conn.execute(
                    "SELECT id FROM memory_reconcile_jobs WHERE project_id=? AND status IN ('pending','running')", (project_id,))]
                self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='cancelled',error_code='settings_disabled',"
                    "lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=?,completed_at_ms=? WHERE project_id=? AND status IN ('pending','running')",
                    (now, now, project_id))
                self.eligible.difference_update(cancelled)
            return self.settings(project_id), cancelled

    def _snapshot_memories(self, project_id: str) -> str:
        rows = self.repository._conn.execute("SELECT id,revision,status,fingerprint FROM user_memories WHERE scope='global' OR (scope='project' AND scope_id=?) ORDER BY id", (project_id,)).fetchall()
        exclusions = self.repository._conn.execute("SELECT * FROM memory_source_exclusions ORDER BY semantic_fingerprint,document_id,quote_hash").fetchall()
        return digest({"memories": [dict(row) for row in rows], "exclusions": [dict(row) for row in exclusions]})

    def _source(self, document_id: str, version_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        document = self.repository._get("knowledge_documents", document_id)
        self.repository._project(document["project_id"])
        self.repository._valid_document(document_id, document["project_id"])
        version = self.repository._version(version_id)
        if document["active_version_id"] != version_id or version["confirmed_at_ms"] is None or version["document_id"] != document_id:
            raise KnowledgeScopeError("memory source must be the current confirmed version")
        if not library_source_is_current(self.repository._conn, document_id, version_id):
            raise KnowledgeScopeError("memory source has changed")
        if version["origin"] not in {"user", "meeting_user_final"}:
            raise KnowledgeScopeError("memory source requires a real user write")
        binding = document.get("source_binding")
        if version["origin"] == "meeting_user_final" and (not binding or binding["source_kind"] != "review_document"):
            raise KnowledgeScopeError("meeting memory requires a saved human final")
        if binding:
            snapshot = source_snapshot(self.repository._conn, binding["source_kind"], binding["source_id"])
            if binding["source_kind"] == "note" and snapshot["note_source_kind"] != "manual" and snapshot["source_version"] <= 1:
                raise KnowledgeScopeError("unmodified AI notes are not personal memory")
        return document, version

    def _snapshot(self, document_id: str, version_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        document, version = self._source(document_id, version_id)
        settings = self.settings(document["project_id"])
        if settings["mode"] == "off":
            raise KnowledgeScopeError("memory reconciliation is disabled")
        clues, evidence = [], []
        declarations = explicit_declarations(version["content"])
        if version["origin"] == "meeting_user_final":
            # A meeting participant's unlabelled 'I' is not the user's preference.
            declarations = [item for item in declarations if item["kind"] == "term" or
                item["quote"].startswith(("用户本人偏好：", "用户本人偏好:", "本人表达偏好：", "本人表达偏好:", "我的表达偏好：", "我的表达偏好:"))]
        for span in version["spans"]:
            base = {"source_type": "document", "source_id": span["id"], "document_id": document_id,
                "project_id": document["project_id"], "title": document["title"], "version_id": version_id,
                "version": version["number"], "content_hash": version["content_hash"], "locator": span["locator"], "context_revision": 0}
            eligible = []
            for declaration in declarations:
                if declaration["quote"] not in span["text"]:
                    continue
                ref = {**base, "quote": declaration["quote"]}
                value = {"scope": "project", "scope_id": document["project_id"], "scene": "", **declaration}
                if not self.memory.source_excluded(value, [ref]) and not self.memory.semantic_already_recorded(value):
                    eligible.append(declaration)
            if not eligible:
                continue
            # Keep the evidence budget around the actual declaration, including
            # declarations later in a long paragraph rather than only its prefix.
            start = span["text"].find(eligible[0]["quote"])
            quote = span["text"][start:start + 450].strip()
            added = False
            for declaration in eligible:
                if declaration["quote"] not in quote:
                    continue
                ref = {**base, "quote": declaration["quote"]}
                clue = {key: declaration[key] for key in ("kind", "memory_key", "value", "aliases")}
                clue["source_refs"] = [ref]
                if clue not in clues:
                    clues.append(clue)
                    added = True
                if len(clues) == 6:
                    break
            if added:
                evidence.append({**base, "quote": quote})
            if len(clues) == 6 or len(evidence) == 6:
                break
        if not clues:
            raise KnowledgeScopeError("no explicit new memory clue")
        existing = self.memory.list(scope="project", scope_id=document["project_id"], limit=200)["items"]
        summaries = [{key: item[key] for key in ("id", "kind", "memory_key", "value", "status", "revision")}
                     | {"aliases": item["aliases"][:2]} for item in existing[:12]]
        snapshot = {"document_id": document_id, "version_id": version_id, "content_hash": version["content_hash"],
            "settings_revision": settings["revision"], "memory_fingerprint": self._snapshot_memories(document["project_id"])}
        input_hash = digest(snapshot)
        payload = {"task_mode": "memory_reconcile", "document_evidence": evidence,
            "knowledge_context": {"project_id": document["project_id"], "project_revision": self.repository._project(document["project_id"])["revision"]},
            "memory_reconciliation": {"input_hash": input_hash, "clues": clues, "existing_memories": summaries}}
        return snapshot, payload

    def enqueue(self, document_id: str, *, expected_version_id: str, automatic: bool = False) -> dict[str, Any] | None:
        with self.repository._write():
            try:
                snapshot, payload = self._snapshot(document_id, expected_version_id)
            except KnowledgeScopeError:
                if automatic:
                    return None
                raise
            old = self.repository._conn.execute("SELECT * FROM memory_reconcile_jobs WHERE document_id=? AND source_version_id=?", (document_id, expected_version_id)).fetchone()
            if old:
                if not automatic and old["status"] == "pending":
                    self.eligible.add(old["id"])
                return dict(old)
            document, version = self._source(document_id, expected_version_id)
            job_id, now = "memory_job_" + uuid.uuid4().hex, _now()
            self.repository._conn.execute("INSERT INTO memory_reconcile_jobs(id,project_id,document_id,source_version_id,source_version,source_hash,status,"
                "snapshot_json,source_payload_json,input_hash,created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,'pending',?,?,?,?,?)",
                (job_id, document["project_id"], document_id, expected_version_id, version["number"], version["content_hash"],
                 _json(snapshot), _json(payload), payload["memory_reconciliation"]["input_hash"], now, now))
            self.eligible.add(job_id)
            return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.repository._lock:
            return self.repository._get("memory_reconcile_jobs", job_id)

    def list(self, project_id: str, *, limit=100, cursor=None):
        with self.repository._lock:
            self.repository._project(project_id)
            if type(limit) is not int or not 1 <= limit <= 200:
                raise ValueError("invalid page limit")
            if cursor is not None and (not isinstance(cursor, Mapping) or set(cursor) != {"id", "updated_at_ms"}
                    or not isinstance(cursor["id"], str) or type(cursor["updated_at_ms"]) is not int):
                raise ValueError("invalid cursor")
            after = cursor["id"] if cursor else None
            rows = [dict(row) for row in self.repository._conn.execute("SELECT * FROM memory_reconcile_jobs WHERE project_id=? AND (? IS NULL OR id>?) ORDER BY id LIMIT ?",
                (project_id, after, after, limit + 1))]
            items, more = rows[:limit], len(rows) > limit
            return {"items": items, "has_more": more, "next_cursor": {"id": items[-1]["id"], "updated_at_ms": items[-1]["updated_at_ms"]} if more else None}

    def _validate(self, job: Mapping[str, Any]) -> None:
        snapshot, _ = self._snapshot(job["document_id"], job["source_version_id"])
        if snapshot != json.loads(job["snapshot_json"]):
            raise KnowledgeConflict("memory job input changed")

    def _current(self, job):
        current = self.get(job["id"])
        if current["status"] != "running" or current["lease_owner"] != job["lease_owner"] or current["lease_until_ms"] <= _now():
            raise KnowledgeConflict("memory job cancelled or replaced")
        return current

    def recover_expired(self, *, now_ms=None):
        now = _now() if now_ms is None else now_ms
        with self.repository._write():
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status=CASE WHEN paid_started=1 THEN 'failed' ELSE 'pending' END, "
                "error_code=CASE WHEN paid_started=1 THEN 'execution_interrupted' ELSE NULL END,lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=? "
                "WHERE status='running' AND lease_until_ms<=?", (now, now))

    def claim(self):
        with self.repository._write():
            self.recover_expired()
            if not self.eligible or self.repository._conn.execute("SELECT 1 FROM memory_reconcile_jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            placeholders = ",".join("?" for _ in self.eligible)
            row = self.repository._conn.execute(f"SELECT * FROM memory_reconcile_jobs WHERE id IN ({placeholders}) AND status='pending' "
                "AND next_attempt_at_ms<=? ORDER BY created_at_ms,id LIMIT 1", (*self.eligible, _now())).fetchone()
            if row is None:
                return None
            job = dict(row)
            try:
                self._validate(job)
            except (KnowledgeConflict, KnowledgeScopeError, KeyError):
                self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='failed',error_code='input_superseded',updated_at_ms=? WHERE id=?", (_now(), job["id"]))
                self.eligible.discard(job["id"])
                return None
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='running',lease_owner=?,lease_until_ms=?,updated_at_ms=? WHERE id=?",
                (uuid.uuid4().hex, _now() + LEASE_MS, _now(), job["id"]))
            return self.get(job["id"])

    def mark_started(self, job):
        with self.repository._write():
            current = self._current(job)
            self._validate(current)
            if current["paid_started"]:
                raise KnowledgeConflict("memory execution cannot automatically repeat")
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET paid_started=1,attempts=attempts+1,updated_at_ms=? WHERE id=?", (_now(), job["id"]))

    def fail(self, job, code):
        with self.repository._write():
            try:
                self._current(job)
            except (KnowledgeConflict, KeyError):
                return
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='failed',error_code=?,lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=?,completed_at_ms=? WHERE id=?",
                (code, _now(), _now(), job["id"]))
            self.eligible.discard(job["id"])

    def release(self, job, *, deferred=None):
        with self.repository._write():
            try:
                current = self._current(job)
            except (KnowledgeConflict, KeyError):
                return
            if current["paid_started"] or current["defer_count"] >= 20:
                self.fail(job, "execution_interrupted" if current["paid_started"] else "background_deferred")
                return
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='pending',lease_owner=NULL,lease_until_ms=NULL,defer_count=defer_count+1,"
                "next_attempt_at_ms=?,error_code=?,updated_at_ms=? WHERE id=?",
                (_now() + (deferred.retry_after_ms if deferred else 0), deferred.code if deferred else None, _now(), job["id"]))

    def cancel(self, job_id):
        with self.repository._write():
            self.get(job_id)
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=?,completed_at_ms=? WHERE id=? AND status IN ('pending','running')", (_now(), _now(), job_id))
            self.eligible.discard(job_id)
            return self.get(job_id)

    def retry(self, job_id, *, expected_version_id):
        with self.repository._write():
            job = self.get(job_id)
            if job["status"] not in {"failed", "cancelled"} and not (job["status"] == "pending" and job_id not in self.eligible):
                raise KnowledgeConflict("memory job cannot retry in its current state")
            snapshot, payload = self._snapshot(job["document_id"], expected_version_id)
            if expected_version_id != job["source_version_id"]:
                raise KnowledgeConflict("new source versions use a separate memory job")
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='pending',snapshot_json=?,source_payload_json=?,input_hash=?,paid_started=0,retry_count=retry_count+1,"
                "defer_count=0,next_attempt_at_ms=0,lease_owner=NULL,lease_until_ms=NULL,error_code=NULL,completed_at_ms=NULL,updated_at_ms=? WHERE id=?",
                (_json(snapshot), _json(payload), payload["memory_reconciliation"]["input_hash"], _now(), job_id))
            self.eligible.add(job_id)
            return self.get(job_id)

    def payload(self, job):
        return {"protocol": PROTOCOL, "request_id": f"{job['id']}:{job['lease_owner']}", "session_id": "memory:" + job["project_id"],
            "task_mode": "memory_reconcile", "context": json.loads(job["source_payload_json"])}

    def validate_updates(self, job, terminal):
        if not isinstance(terminal, Mapping) or set(terminal) != {"input_hash", "candidates"} or terminal["input_hash"] != job["input_hash"] or not isinstance(terminal["candidates"], list) or len(terminal["candidates"]) > 6:
            raise ValueError("invalid memory terminal")
        clues = json.loads(job["source_payload_json"])["memory_reconciliation"]["clues"]
        with self.repository._lock:
            self._validate(job)
            for candidate in terminal["candidates"]:
                if not isinstance(candidate, Mapping) or set(candidate) != {"kind", "memory_key", "value", "aliases", "source_refs"}:
                    raise ValueError("invalid memory candidate fields")
                if not any(dict(candidate) == clue for clue in clues):
                    raise ValueError("memory must match a complete registered explicit declaration")
                for ref in candidate["source_refs"]:
                    self.repository.validate_evidence(ref, project_id=job["project_id"])
        return json.loads(_json(terminal))

    def finish(self, job, terminal):
        with self.repository._write():
            self._current(job)
            validated = self.validate_updates(job, terminal)
            results = []
            for candidate in validated["candidates"]:
                item = self.memory.create_ai_candidate(project_id=job["project_id"], candidate=candidate, job_id=job["id"])
                if item and item["id"] not in results:
                    results.append(item["id"])
            self.repository._conn.execute("UPDATE memory_reconcile_jobs SET status='succeeded',result_memory_ids_json=?,lease_owner=NULL,lease_until_ms=NULL,error_code=NULL,updated_at_ms=?,completed_at_ms=? WHERE id=?", (_json(results), _now(), _now(), job["id"]))
            self.eligible.discard(job["id"])
            return results


class MemoryReconcileRegistry:
    def __init__(self, queue, job):
        self.queue, self.job = queue, job

    def register_documents(self, refs):
        self.queue._validate(self.job)
        for ref in refs:
            self.queue.repository.validate_evidence(ref, project_id=self.job["project_id"])
        return refs

    def validate_memory_update(self, terminal):
        return self.queue.validate_updates(self.job, terminal)


class MemoryReconcilePiExecutor:
    def __init__(self, queue):
        self.queue, self.registry = queue, None
        self.runtime = PiCoachSidecar(timeout_seconds=25 + BRIDGE_RESPONSE_GRACE_SECONDS,
                                     evidence_registry_factory=lambda _: self.registry)

    async def __call__(self, payload, job, config):
        if not self.runtime.available:
            raise RuntimeError("pi_unavailable")
        self.registry = MemoryReconcileRegistry(self.queue, job)
        timeout_ms = int(max(1, min(config.timeout_seconds, 25)) * 1000)
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


class MemoryReconcileRunner:
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
        while True:
            job = await asyncio.to_thread(self.queue.claim)
            if job is None:
                await asyncio.sleep(0.2)
                continue
            self._job = job
            try:
                ledger_job = {**job, "meeting_id": "memory:" + job["project_id"]}
                self._execution = asyncio.create_task(self.executor(self.queue.payload(job), ledger_job))
                result = await self._execution
                terminal = result.get("host_memory_update")
                if not isinstance(terminal, Mapping):
                    raise ValueError("host memory terminal missing")
                await asyncio.to_thread(self.queue.finish, job, terminal)
            except BackgroundReconcileDeferred as deferred:
                await asyncio.to_thread(self.queue.release, job, deferred=deferred)
            except asyncio.CancelledError:
                await asyncio.to_thread(self.queue.release, job)
                if asyncio.current_task().cancelling():
                    raise
            except (KnowledgeConflict, KnowledgeScopeError, KeyError):
                await asyncio.to_thread(self.queue.fail, job, "input_superseded")
            except PiCoachRuntimeError as exc:
                code = log_background_failure(exc, job=job, purpose="memory_reconcile")
                await asyncio.to_thread(self.queue.fail, job, code)
            except Exception:
                await asyncio.to_thread(self.queue.fail, job, "memory_reconciliation_failed")
            finally:
                self._execution = self._job = None
