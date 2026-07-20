from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
from collections.abc import Callable
from typing import Any

from .audio_assets import delete_audio_asset
from .meeting_preparation import MeetingPreparationStore, normalize_meeting_id
from .storage_governance import ensure_private_directory
from .v2_persistence import (
    DATA_DELETION_SCOPES,
    DEFAULT_RETENTION_POLICY,
    RETENTION_POLICIES,
    V2Persistence,
)


_RETENTION_POLICY_ALIASES: dict[object, str] = {
    30: "30_days",
    90: "90_days",
    365: "365_days",
    "30": "30_days",
    "90": "90_days",
    "365": "365_days",
    "30_days": "30_days",
    "90_days": "90_days",
    "365_days": "365_days",
    "local_until_user_deletes": "local_until_user_deletes",
    "manual_only": "manual_only",
}


def normalize_retention_policy(value: object) -> str:
    key: object = value
    if isinstance(value, str):
        key = value.strip().lower()
    normalized = _RETENTION_POLICY_ALIASES.get(key)
    if normalized is None or normalized not in RETENTION_POLICIES:
        raise ValueError(
            "retention_policy must be local_until_user_deletes, manual_only, 30, 90, or 365 days"
        )
    return normalized


def normalize_deletion_scope(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in DATA_DELETION_SCOPES:
        raise ValueError(f"deletion_scope must be one of {sorted(DATA_DELETION_SCOPES)}")
    return normalized


class DataGovernanceService:
    """Coordinate durable deletion jobs with meeting-owned local files.

    The service intentionally has no timer or short polling loop. Production
    code may call ``run_retention_if_due`` from a real scheduler; the durable
    persistence gate rejects another automatic pass for at least 24 hours.
    """

    def __init__(
        self,
        *,
        persistence: V2Persistence,
        data_dir: str | Path,
        meeting_preparation_store: MeetingPreparationStore | None = None,
        pre_purge: Callable[[str, str], None] | None = None,
    ) -> None:
        self.persistence = persistence
        self.data_dir = ensure_private_directory(data_dir)
        self.meeting_preparation_store = meeting_preparation_store or MeetingPreparationStore(
            self.data_dir / "meeting_preparation"
        )
        self.pre_purge = pre_purge

    def get_settings(self) -> dict[str, Any]:
        return self.persistence.get_data_governance_settings()

    def set_retention_policy(
        self,
        retention_policy: object,
        *,
        now_ms: int,
    ) -> dict[str, Any]:
        return self.persistence.set_retention_policy(
            retention_policy=normalize_retention_policy(retention_policy),
            now_ms=now_ms,
            requested_by="user",
        )

    def request_deletion(
        self,
        *,
        meeting_id: str,
        deletion_scope: str,
        now_ms: int,
        requested_by: str = "user",
        retention_policy: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        meeting_id = normalize_meeting_id(meeting_id)
        deletion_scope = normalize_deletion_scope(deletion_scope)
        normalized_retention_policy = (
            normalize_retention_policy(retention_policy)
            if retention_policy is not None
            else None
        )
        managed_paths = (
            [
                f"audio_assets/{meeting_id}",
                f"imports/{meeting_id}",
                f"meeting_preparation/{meeting_id}.json",
            ]
            if deletion_scope in {"recording", "all"}
            else []
        )
        return self.persistence.create_deletion_job(
            meeting_id=meeting_id,
            managed_paths=managed_paths,
            now_ms=now_ms,
            deletion_scope=deletion_scope,
            requested_by=requested_by,
            retention_policy=normalized_retention_policy,
            idempotency_key=idempotency_key,
        )

    def execute_deletion_job(self, *, job_id: str, now_ms: int) -> dict[str, Any]:
        job = self.persistence.get_deletion_job(job_id)
        if job["status"] == "completed":
            return job
        running = self.persistence.mark_deletion_running(job_id=job_id, now_ms=now_ms)
        if running["status"] == "completed":
            return running
        managed_path_results: dict[str, str] = {}
        try:
            if self.pre_purge is not None:
                self.pre_purge(
                    str(running["meeting_id"]),
                    str(running["deletion_scope"]),
                )
            for managed_path in running["managed_paths"]:
                managed_path_results[managed_path] = self._delete_managed_path(
                    meeting_id=str(running["meeting_id"]),
                    managed_path=str(managed_path),
                )
            return self.persistence.complete_deletion_and_purge(
                job_id=job_id,
                now_ms=now_ms,
                managed_path_results=managed_path_results,
            )
        except BaseException as exc:
            self.persistence.fail_deletion_job(
                job_id=job_id,
                error_class=type(exc).__name__,
                now_ms=now_ms,
            )
            raise

    def process_pending_deletions(
        self,
        *,
        now_ms: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = int(limit)
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        jobs = self.persistence.list_deletion_jobs(statuses=("pending", "failed"))[:limit]
        return [
            self.execute_deletion_job(job_id=str(job["id"]), now_ms=now_ms)
            for job in jobs
        ]

    def run_retention_if_due(self, *, now_ms: int) -> dict[str, Any]:
        claim = self.persistence.claim_retention_run(now_ms=now_ms)
        if not claim["claimed"]:
            return claim

        run = claim["run"]
        retention_policy = str(run["retention_policy"])
        retry_jobs = [
            job
            for job in self.persistence.list_deletion_jobs(statuses=("pending", "failed"))
            if job["requested_by"] == "retention"
            and job["retention_policy"] == retention_policy
        ]
        candidates = self.persistence.list_meetings_due_for_retention(
            retention_policy=retention_policy,
            now_ms=now_ms,
        )
        completed_jobs: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for job in retry_jobs:
            try:
                completed_jobs.append(
                    self.execute_deletion_job(job_id=str(job["id"]), now_ms=now_ms)
                )
            except Exception as exc:
                errors.append(
                    {
                        "meeting_id": str(job["meeting_id"]),
                        "error_class": type(exc).__name__,
                    }
                )
        for meeting in candidates:
            meeting_id = str(meeting["id"])
            try:
                job = self.request_deletion(
                    meeting_id=meeting_id,
                    deletion_scope="all",
                    now_ms=now_ms,
                    requested_by="retention",
                    retention_policy=retention_policy,
                    idempotency_key=f"retention.delete:{retention_policy}:{meeting_id}",
                )
                completed_jobs.append(
                    self.execute_deletion_job(job_id=str(job["id"]), now_ms=now_ms)
                )
            except Exception as exc:
                errors.append(
                    {
                        "meeting_id": meeting_id,
                        "error_class": type(exc).__name__,
                    }
                )
        completed_run = self.persistence.complete_retention_run(
            run_id=str(run["id"]),
            candidate_count=len(retry_jobs) + len(candidates),
            deletion_job_count=len(completed_jobs),
            error_count=len(errors),
            now_ms=now_ms,
        )
        return {
            "claimed": True,
            "run": completed_run,
            "deletion_jobs": completed_jobs,
            "errors": errors,
        }

    def _delete_managed_path(self, *, meeting_id: str, managed_path: str) -> str:
        meeting_id = normalize_meeting_id(meeting_id)
        if "\\" in managed_path:
            raise ValueError("managed deletion path must use POSIX separators")
        path = PurePosixPath(managed_path)
        if path.parts == ("audio_assets", meeting_id):
            return delete_audio_asset(
                self.data_dir,
                {"relative_path": f"audio_assets/{meeting_id}/audio.wav"},
            )
        if path.parts == ("imports", meeting_id):
            imports_root = self.data_dir / "imports"
            target = imports_root / meeting_id
            if imports_root.is_symlink() or target.is_symlink():
                raise ValueError("controlled import directory must not be a symlink")
            if not target.exists():
                return "already_missing"
            if not target.is_dir():
                raise ValueError("controlled import path is not a directory")
            resolved_target = target.resolve()
            if self.data_dir not in resolved_target.parents:
                raise ValueError("controlled import path escapes data_dir")
            shutil.rmtree(target)
            return "deleted"
        if path.parts == ("meeting_preparation", f"{meeting_id}.json"):
            return "deleted" if self.meeting_preparation_store.delete(meeting_id) else "already_missing"
        raise ValueError("deletion job contains a path outside the meeting-owned data roots")


__all__ = [
    "DEFAULT_RETENTION_POLICY",
    "DataGovernanceService",
    "normalize_deletion_scope",
    "normalize_retention_policy",
]
