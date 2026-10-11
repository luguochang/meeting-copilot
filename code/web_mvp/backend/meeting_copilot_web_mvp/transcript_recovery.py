"""One local-ASR worker with durable leases and explicit candidate confirmation."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping
import uuid
import structlog

from . import asr_resource_guard, batch_transcribe
from .storage_governance import ensure_private_directory
from .transcript_recovery_audio import RecoveryAudioError, crop_verified_recovery_audio, managed_recovery_path, public_recovery_source
from .transcript_recovery_persistence import RecoveryError

TIMEOUT_SECONDS = 180
_log = structlog.get_logger(__name__)


class TranscriptRecoveryService:
    def __init__(self, persistence: Any, data_dir: Path, *,
                 recognize: Callable[..., Mapping[str, Any]] | None = None,
                 available: Callable[[], bool] | None = None,
                 capacity: Callable[..., Any] | None = None,
                 clock_ms: Callable[[], int] | None = None,
                 poll_seconds: float = 10.0, heartbeat_seconds: float = 5.0) -> None:
        self.persistence = persistence
        self.data_dir = data_dir.resolve()
        self.recognize = recognize or batch_transcribe.transcribe_file_report
        self.available = available or batch_transcribe.is_available
        self.capacity = capacity or asr_resource_guard.check_capacity
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.poll_seconds, self.heartbeat_seconds = poll_seconds, heartbeat_seconds
        self.owner = "transcript-recovery-" + uuid.uuid4().hex
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    @contextmanager
    def _crop(self, source: Mapping[str, Any], request: Mapping[str, Any]) -> Iterator[Path]:
        # Managed private temp, no user filename; original audio is never changed.
        root = ensure_private_directory(managed_recovery_path(
            self.data_dir, f"audio_assets/{source['meeting_id']}/transcript-recovery-temp"))
        if root.is_symlink() or root.is_junction() or not root.resolve().is_relative_to(self.data_dir):
            raise RecoveryAudioError("recovery_audio_path_invalid")
        with tempfile.TemporaryDirectory(prefix="range-", dir=root) as directory:
            path = Path(directory).resolve()
            if not path.is_relative_to(root.resolve()):
                raise RecoveryAudioError("recovery_audio_path_invalid")
            destination = path / "range.wav"
            crop_verified_recovery_audio(data_dir=self.data_dir, source=source,
                                         start_sample=request["start_sample"], end_sample=request["end_sample"],
                                         destination=destination)
            yield destination

    def _verify(self, request: Mapping[str, Any]) -> Callable[[Mapping[str, Any]], None]:
        def verify(source: Mapping[str, Any]) -> None:
            with self._crop(source, request):
                pass
        return verify

    def describe(self, meeting_id: str) -> dict[str, Any]:
        sources, unavailable = self.persistence.recovery_sources(meeting_id)
        jobs = self.persistence.list_recovery_jobs(meeting_id)
        active = bool(self.persistence.list_active_recording_sessions())
        capable = self.available()
        reason = "recovery_components_missing" if not capable else "recovery_capture_active" if active else None
        if reason is None and self.persistence.recovery_execution_active():
            reason = "recovery_worker_busy"
        if reason is None:
            try:
                self.capacity(asr_resource_guard.COLD_LOAD_PROFILE)
            except asr_resource_guard.AsrResourceError as exc:
                reason = exc.code
        return {"schema_version": "transcript_recovery.v1", "meeting_id": meeting_id,
                "merge_allowed": self.persistence.get_meeting(meeting_id)["state"] == "ended" and not active,
                "capability": {"status": "unavailable" if not capable else "waiting_resources" if reason else "ready",
                               "reason": reason, "max_range_seconds": 300, "max_attempts": 3, "timeout_seconds": TIMEOUT_SECONDS},
                "sources": [public_recovery_source(source) for source in sources], "unavailable_sources": unavailable, "jobs": jobs}

    def create(self, meeting_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        result = self.persistence.create_recovery_job(meeting_id, request, now_ms=self.clock_ms(), verify=self._verify(request))
        self._notify()
        return result

    def cancel(self, meeting_id: str, job_id: str, expected_version: int | None) -> dict[str, Any]:
        return self.persistence.cancel_recovery_job(meeting_id, job_id, expected_version=expected_version, now_ms=self.clock_ms())

    def retry(self, meeting_id: str, job_id: str, expected_version: int | None) -> dict[str, Any]:
        job = self.persistence.get_recovery_job(meeting_id, job_id)
        try:
            result = self.persistence.retry_recovery_job(meeting_id, job_id, expected_version=expected_version,
                                                         now_ms=self.clock_ms(), verify=self._verify(job))
        except (RecoveryAudioError, RecoveryError) as exc:
            self._stale_on_change(meeting_id, job_id, exc.code)
            raise
        self._notify()
        return result

    def merge(self, meeting_id: str, job_id: str, candidate_version: int, text: str | None) -> dict[str, Any]:
        job = self.persistence.get_recovery_job(meeting_id, job_id)
        try:
            return self.persistence.merge_recovery_job(meeting_id, job_id, candidate_version=candidate_version, text=text,
                                                       now_ms=self.clock_ms(), verify=self._verify(job))
        except (RecoveryAudioError, RecoveryError) as exc:
            self._stale_on_change(meeting_id, job_id, exc.code)
            raise

    def _stale_on_change(self, meeting_id: str, job_id: str, code: str) -> None:
        if code in {"recovery_source_changed", "recovery_range_covered", "recovery_audio_changed",
                    "recovery_audio_not_ready", "recovery_audio_path_invalid", "recovery_audio_unavailable",
                    "recovery_timeline_unknown", "recovery_coverage_unknown"}:
            self.persistence.mark_recovery_stale(meeting_id, job_id, now_ms=self.clock_ms(), error_code=code)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await asyncio.to_thread(self.persistence.recover_recovery_leases, now_ms=self.clock_ms())
        # A second concurrent startup may have completed during lease recovery.
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._wake.set()
        self._task = asyncio.create_task(self._run(), name="historical-transcript-recovery")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        task = self._task
        if task is not None:
            # Do not cancel to_thread: batch owns timeout/kill/monitor and cold-start lease.
            try:
                await task
            finally:
                if self._task is task:
                    self._task = None

    def _notify(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except (sqlite3.Error, OSError, RecoveryError, RuntimeError) as exc:
                # Fail closed; durable lease is retained/recovered, no auto recognition retry.
                _log.warning("transcript.recovery.worker_failed", error_class=type(exc).__name__)
            if self._stop.is_set():
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _heartbeat(self, job_id: str, version: int, done: asyncio.Event) -> None:
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=self.heartbeat_seconds)
            except TimeoutError:
                await asyncio.to_thread(self.persistence.heartbeat_recovery_job, job_id,
                                        owner=self.owner, version=version, now_ms=self.clock_ms())

    def _recognize_job(self, job: Mapping[str, Any]) -> str:
        # All bounded hashing/cropping/model work is off the event loop; heartbeat remains live.
        with ExitStack() as stack:
            paths: list[Path] = []
            def verify(source: Mapping[str, Any]) -> None:
                paths.append(stack.enter_context(self._crop(source, job)))
            self.persistence.begin_recovery_attempt(job["id"], owner=self.owner, version=job["version"],
                                                     now_ms=self.clock_ms(), verify=verify)
            try:
                result = self.recognize(paths[0], timeout=TIMEOUT_SECONDS)
            except asr_resource_guard.AsrResourceError as error:
                owned = error.owned_cleanup_lease
                if owned is not None and not owned.is_released:
                    # Keep the crop and this job's exact claim until the batch's
                    # owned monitor proves exit; another global cold lease is irrelevant.
                    try:
                        self.persistence.finish_recovery_job(job["id"], owner=self.owner, version=job["version"],
                            now_ms=self.clock_ms(), status="failed", error_code=error.code, keep_execution=True)
                    except (sqlite3.Error, RuntimeError) as failure:
                        _log.warning("transcript.recovery.cleanup_state_failed", error_class=type(failure).__name__)
                    while not owned.is_released:
                        time.sleep(0.1)
                raise
            if not isinstance(result, Mapping) or not isinstance(result.get("text"), str):
                raise RecoveryError("recovery_candidate_invalid")
            text = result["text"].strip()
            if not text:
                raise RecoveryError("recovery_no_speech")
            if len(text) > 20_000 or len(text.encode("utf-8")) > 65_536 or "\x00" in text:
                raise RecoveryError("recovery_candidate_invalid")
            return text

    async def run_once(self) -> bool:
        await asyncio.to_thread(self.persistence.recover_recovery_leases, now_ms=self.clock_ms())
        job = await asyncio.to_thread(self.persistence.claim_recovery_job, owner=self.owner, now_ms=self.clock_ms())
        if job is None:
            return False
        done = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job["id"], job["version"], done))
        status, error, text = "failed", "recovery_asr_failed", None
        refund_preflight = False
        try:
            if not self.available():
                raise RecoveryError("recovery_components_missing")
            await asyncio.to_thread(self.capacity, asr_resource_guard.COLD_LOAD_PROFILE,
                                    input_bytes=(job["end_sample"] - job["start_sample"]) * 2)
            operation = asyncio.create_task(asyncio.to_thread(self._recognize_job, job))
            try:
                text = await asyncio.shield(operation)
            except asyncio.CancelledError:
                # Cancellation cannot release this lease while owned ASR is still executing.
                try:
                    await asyncio.shield(operation)
                finally:
                    raise
            status, error = "candidate_ready", None
        except asr_resource_guard.AsrResourceError as exc:
            # Preflight waiting is free. A guarded ASR invocation failure needs explicit retry.
            current = self.persistence.get_recovery_job(job["meeting_id"], job["id"])
            preflight = current["attempts"] == job["attempts"] or (
                exc.execution_phase == "before_spawn" and exc.code in {
                    "asr_capacity_unavailable", "asr_resource_probe_failed", "asr_cold_start_busy"
                })
            status = "waiting_resources" if preflight else "failed"
            refund_preflight = preflight and current["attempts"] > job["attempts"]
            error = exc.code
        except (RecoveryAudioError, RecoveryError) as exc:
            error = exc.code
            if error == "recovery_capture_active":
                status = "waiting_resources"
            elif error in {"recovery_source_changed", "recovery_range_covered", "recovery_audio_changed",
                            "recovery_audio_not_ready", "recovery_audio_path_invalid", "recovery_audio_unavailable",
                            "recovery_timeline_unknown", "recovery_coverage_unknown"}:
                status = "stale"
        except subprocess.TimeoutExpired:
            error = "recovery_asr_timeout"
        except (OSError, RuntimeError, ValueError, TypeError):
            error = "recovery_asr_failed"
        finally:
            try:
                await asyncio.to_thread(self.persistence.finish_recovery_job, job["id"], owner=self.owner,
                                        version=job["version"], now_ms=self.clock_ms(), status=status,
                                        error_code=error, text=text, verify=self._verify(job) if status == "candidate_ready" else None,
                                        refund_preflight=refund_preflight)
            finally:
                done.set()
                await heartbeat
        return True
