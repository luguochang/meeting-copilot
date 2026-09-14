"""Small circuit breaker for the shared realtime Provider dependency.

Pi coaching and direct semantic extraction target the same configured gateway.
Once that gateway has failed repeatedly, either path can otherwise spend its
entire budget and produce a late silent result. This module provides their
shared availability state. It is not a provider health oracle and it does not
claim account, quota, worker, or billing isolation upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time
import uuid
from typing import Any, Mapping


RETRYABLE_FAILURE_CLASSES = frozenset(
    {"timeout", "transport", "provider_server", "rate_limit"}
)
_FAILURE_CLASS_ALIASES = {
    "rate_limited": "rate_limit",
    "server_error": "provider_server",
    "provider_server_error": "provider_server",
}
MAX_RETRY_AFTER_MS = 300_000
DEFAULT_HALF_OPEN_LEASE_MS = 30_000


@dataclass(frozen=True)
class CircuitSnapshot:
    """Safe state for diagnostics and durable coach metrics."""

    state: str
    reason: str
    failure_count: int
    retry_after_ms: int
    half_open: bool
    identity_generation: int
    last_failure_class: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "failure_count": self.failure_count,
            "retry_after_ms": self.retry_after_ms,
            "half_open": self.half_open,
            "identity_generation": self.identity_generation,
            "last_failure_class": self.last_failure_class,
        }


@dataclass(frozen=True)
class CircuitAdmission:
    """The result of trying to reserve one shared realtime Provider attempt."""

    admitted: bool
    snapshot: CircuitSnapshot
    permit: "CircuitPermit | None" = None


class CircuitPermit:
    """Single-use lease returned for an admitted realtime attempt."""

    def __init__(
        self,
        owner: "RealtimeProviderCircuit",
        *,
        identity_fingerprint: str,
        identity_generation: int,
        circuit_epoch: int,
        half_open: bool,
        permit_token: str | None = None,
    ) -> None:
        self._owner = owner
        self._identity_fingerprint = identity_fingerprint
        self._identity_generation = identity_generation
        self._circuit_epoch = circuit_epoch
        self._half_open = half_open
        self._permit_token = permit_token
        self._completed = False
        self._completion_lock = threading.Lock()

    @property
    def half_open(self) -> bool:
        return self._half_open

    @property
    def identity_generation(self) -> int:
        return self._identity_generation

    def record_success(self) -> None:
        self._complete(success=True, failure_class=None)

    def record_failure(
        self,
        failure_class: str | None,
        *,
        retry_after_ms: int | None = None,
    ) -> None:
        self._complete(
            success=False,
            failure_class=normalize_failure_class(failure_class),
            retry_after_ms=normalize_retry_after_ms(retry_after_ms),
        )

    def release(self) -> None:
        """Release a permit when no reliable outcome was observed.

        Unknown failures are intentionally non-retryable for this circuit.  A
        protocol/schema failure should not open a transport circuit, but the
        half-open reservation must still be released.
        """

        self._complete(success=False, failure_class=None, release_only=True)

    def _complete(
        self,
        *,
        success: bool,
        failure_class: str | None,
        retry_after_ms: int | None = None,
        release_only: bool = False,
    ) -> None:
        with self._completion_lock:
            if self._completed:
                return
            self._completed = True
        self._owner._complete(
            self,
            success=success,
            failure_class=failure_class,
            retry_after_ms=retry_after_ms,
            release_only=release_only,
        )


class RealtimeProviderCircuit:
    """A bounded availability circuit for one configured realtime Provider.

    When ``persistence`` is supplied, circuit state is durable and admission
    is coordinated across worker processes through the SQLite transaction
    helper. Without it the class retains the small in-process mode used by
    memory-only deployments and unit tests. ``identity`` should contain only
    non-secret provider/model configuration. A changed identity uses a
    separate digest, so a newly configured gateway is never blocked by an old
    gateway's failures. Timeout, transport, and server failures use a
    two-failure/15-second default; one rate-limit response uses a longer
    60-second backoff because an immediate retry only extends quota pressure.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 15.0,
        rate_limit_cooldown_seconds: float = 60.0,
        clock: Any | None = None,
        persistence: Any | None = None,
        half_open_lease_ms: int = DEFAULT_HALF_OPEN_LEASE_MS,
    ) -> None:
        if int(failure_threshold) < 1:
            raise ValueError("failure_threshold must be positive")
        if float(cooldown_seconds) <= 0:
            raise ValueError("cooldown_seconds must be positive")
        if float(rate_limit_cooldown_seconds) <= 0:
            raise ValueError("rate_limit_cooldown_seconds must be positive")
        if int(half_open_lease_ms) <= 0:
            raise ValueError("half_open_lease_ms must be positive")
        self.failure_threshold = int(failure_threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self.rate_limit_cooldown_seconds = float(rate_limit_cooldown_seconds)
        self._persistence = persistence
        self._clock = clock or (time.time if persistence is not None else time.monotonic)
        self.half_open_lease_ms = int(half_open_lease_ms)
        self._lock = threading.RLock()
        self._identity_fingerprint: str | None = None
        self._identity_generation = 0
        self._circuit_epoch = 0
        self._state = "closed"
        self._failure_count = 0
        self._last_failure_class: str | None = None
        self._opened_at: float | None = None
        self._active_cooldown_seconds = self.cooldown_seconds
        self._half_open_inflight = False

    @staticmethod
    def fingerprint(identity: Any) -> str:
        """Return a non-secret identity fingerprint for metrics/tests."""

        return hashlib.sha256(str(identity or "").encode("utf-8")).hexdigest()

    def acquire(self, identity: Any) -> CircuitAdmission:
        if self._persistence is not None:
            return self._durable_acquire(identity)
        fingerprint = self.fingerprint(identity)
        with self._lock:
            self._sync_identity_locked(fingerprint)
            now = float(self._clock())
            if self._state == "open":
                remaining = self._remaining_ms(now)
                if remaining > 0:
                    snapshot = self._snapshot_locked(
                        reason=self._open_reason_locked(),
                        now=now,
                    )
                    return CircuitAdmission(False, snapshot)
                if self._half_open_inflight:
                    snapshot = self._snapshot_locked(
                        reason="realtime_provider_circuit_half_open",
                        now=now,
                    )
                    return CircuitAdmission(False, snapshot)
                self._state = "half_open"
                self._half_open_inflight = True
                snapshot = self._snapshot_locked(
                    reason="realtime_provider_circuit_half_open_trial",
                    now=now,
                )
                permit = CircuitPermit(
                    self,
                    identity_fingerprint=fingerprint,
                    identity_generation=self._identity_generation,
                    circuit_epoch=self._circuit_epoch,
                    half_open=True,
                    permit_token=None,
                )
                return CircuitAdmission(True, snapshot, permit)

            if self._state == "half_open":
                # The first post-cooldown caller owns the only trial.  This
                # branch is normally reached by a concurrent caller while the
                # trial is still running.
                snapshot = self._snapshot_locked(
                    reason="realtime_provider_circuit_half_open",
                    now=now,
                )
                return CircuitAdmission(False, snapshot)

            # A normal closed circuit admits concurrent realtime attempts.  The
            # failure threshold still counts their outcomes under the lock.
            snapshot = self._snapshot_locked(
                reason="realtime_provider_circuit_closed",
                now=now,
            )
            permit = CircuitPermit(
                self,
                identity_fingerprint=fingerprint,
                identity_generation=self._identity_generation,
                circuit_epoch=self._circuit_epoch,
                half_open=False,
                permit_token=None,
            )
            return CircuitAdmission(True, snapshot, permit)

    def snapshot(self, identity: Any) -> CircuitSnapshot:
        if self._persistence is not None:
            return self._durable_snapshot(identity)
        fingerprint = self.fingerprint(identity)
        with self._lock:
            self._sync_identity_locked(fingerprint)
            return self._snapshot_locked(now=float(self._clock()))

    def record_probe_success(self, identity: Any) -> CircuitSnapshot:
        """Reset the realtime circuit after an explicit successful probe."""

        if self._persistence is not None:
            return self._durable_probe_success(identity)
        fingerprint = self.fingerprint(identity)
        with self._lock:
            self._sync_identity_locked(fingerprint)
            self._state = "closed"
            self._failure_count = 0
            self._last_failure_class = None
            self._opened_at = None
            self._active_cooldown_seconds = self.cooldown_seconds
            self._half_open_inflight = False
            # Invalidate an in-flight pre-probe trial; its late result must not
            # reopen the freshly verified circuit.
            self._identity_generation += 1
            return self._snapshot_locked(reason="manual_probe_succeeded", now=float(self._clock()))

    def record_probe_failure(
        self,
        identity: Any,
        failure_class: str | None,
        *,
        retry_after_ms: int | None = None,
    ) -> CircuitSnapshot:
        """Open immediately after an explicit retryable health probe fails.

        A probe is already the bounded recovery attempt for this dependency.
        Requiring two more user-facing requests to fail before opening the
        circuit would waste both the realtime window and Provider quota. A
        non-availability failure remains outside this transport circuit.
        """

        normalized = normalize_failure_class(failure_class)
        if normalized is None:
            return self.snapshot(identity)
        if self._persistence is not None:
            return self._durable_probe_failure(
                identity,
                normalized,
                retry_after_ms=retry_after_ms,
            )
        fingerprint = self.fingerprint(identity)
        with self._lock:
            self._sync_identity_locked(fingerprint)
            bounded_retry_after_ms = normalize_retry_after_ms(retry_after_ms)
            default_cooldown_seconds = (
                self.rate_limit_cooldown_seconds
                if normalized == "rate_limit"
                else self.cooldown_seconds
            )
            self._state = "open"
            self._failure_count = max(
                self._failure_count + 1,
                1 if normalized == "rate_limit" else self.failure_threshold,
            )
            self._last_failure_class = normalized
            self._opened_at = float(self._clock())
            self._active_cooldown_seconds = max(
                default_cooldown_seconds,
                (bounded_retry_after_ms or 0) / 1_000,
            )
            self._half_open_inflight = False
            self._circuit_epoch += 1
            return self._snapshot_locked(
                reason="manual_probe_failed",
                now=float(self._clock()),
            )

    def _sync_identity_locked(self, fingerprint: str) -> None:
        if fingerprint == self._identity_fingerprint:
            return
        self._identity_fingerprint = fingerprint
        self._identity_generation += 1
        self._state = "closed"
        self._failure_count = 0
        self._last_failure_class = None
        self._opened_at = None
        self._active_cooldown_seconds = self.cooldown_seconds
        self._half_open_inflight = False

    def _remaining_ms(self, now: float) -> int:
        if self._opened_at is None:
            return 0
        return max(
            0,
            int(
                round(
                    (self._active_cooldown_seconds - (now - self._opened_at))
                    * 1_000
                )
            ),
        )

    def _open_reason_locked(self) -> str:
        return (
            "realtime_provider_rate_limit_backoff"
            if self._last_failure_class == "rate_limit"
            else "realtime_provider_circuit_open"
        )

    def _snapshot_locked(
        self,
        *,
        now: float,
        reason: str | None = None,
    ) -> CircuitSnapshot:
        state = self._state
        resolved_reason = reason or (
            self._open_reason_locked()
            if state == "open"
            else "realtime_provider_circuit_half_open"
            if state == "half_open"
            else "realtime_provider_circuit_closed"
        )
        return CircuitSnapshot(
            state=state,
            reason=resolved_reason,
            failure_count=self._failure_count,
            retry_after_ms=self._remaining_ms(now) if state == "open" else 0,
            half_open=state == "half_open",
            identity_generation=self._identity_generation,
            last_failure_class=self._last_failure_class,
        )

    def _complete(
        self,
        permit: CircuitPermit,
        *,
        success: bool,
        failure_class: str | None,
        retry_after_ms: int | None = None,
        release_only: bool = False,
    ) -> None:
        if self._persistence is not None:
            self._durable_complete(
                permit,
                success=success,
                failure_class=failure_class,
                retry_after_ms=retry_after_ms,
                release_only=release_only,
            )
            return
        with self._lock:
            if (
                permit._identity_fingerprint != self._identity_fingerprint
                or permit._identity_generation != self._identity_generation
                or permit._circuit_epoch != self._circuit_epoch
            ):
                # Configuration changed while the old request was running.
                # Its result must not mutate the newly configured circuit.
                return
            if permit.half_open:
                self._half_open_inflight = False
            if release_only:
                if permit.half_open and self._state == "half_open":
                    self._state = "open"
                    # No Provider attempt happened. Release only the exclusive
                    # trial slot and preserve the already-expired open time so
                    # the next caller with a viable budget can probe at once.
                    self._circuit_epoch += 1
                return
            if success:
                self._state = "closed"
                self._failure_count = 0
                self._last_failure_class = None
                self._opened_at = None
                self._active_cooldown_seconds = self.cooldown_seconds
                self._half_open_inflight = False
                return
            if failure_class not in RETRYABLE_FAILURE_CLASSES:
                # Non-retryable/provider-schema failures do not poison this
                # transport circuit. They also close a half-open trial.
                if self._state == "half_open" or not permit.half_open:
                    self._state = "closed"
                    self._failure_count = 0
                    self._last_failure_class = None
                    self._opened_at = None
                    self._active_cooldown_seconds = self.cooldown_seconds
                return
            self._failure_count += 1
            self._last_failure_class = failure_class
            bounded_retry_after_ms = normalize_retry_after_ms(retry_after_ms)
            default_cooldown_seconds = (
                self.rate_limit_cooldown_seconds
                if failure_class == "rate_limit"
                else self.cooldown_seconds
            )
            effective_cooldown_seconds = max(
                default_cooldown_seconds,
                (bounded_retry_after_ms or 0) / 1_000,
            )
            if failure_class == "rate_limit":
                self._state = "open"
                self._opened_at = float(self._clock())
                self._active_cooldown_seconds = effective_cooldown_seconds
                self._half_open_inflight = False
                self._circuit_epoch += 1
                return
            if permit.half_open or self._failure_count >= self.failure_threshold:
                self._state = "open"
                self._opened_at = float(self._clock())
                self._active_cooldown_seconds = effective_cooldown_seconds
                self._half_open_inflight = False
                # Revoke every closed-state permit issued before this opening.
                # Their result remains stale even after a later half-open
                # trial closes the circuit again.
                self._circuit_epoch += 1

    # Durable mode ---------------------------------------------------------
    # The persistence callback owns the SQLite BEGIN IMMEDIATE boundary. The
    # in-memory fields above are deliberately bypassed in this mode so a
    # second backend process cannot observe a stale half-open permit.

    def _durable_now_ms(self) -> int:
        return max(0, int(float(self._clock()) * 1_000))

    def _durable_default_row(self, now_ms: int) -> dict[str, Any]:
        return {
            "state": "closed",
            "failure_count": 0,
            "last_failure_class": None,
            "opened_at_ms": None,
            "open_until_ms": None,
            "backoff_source": None,
            "half_open_permit_token": None,
            "half_open_lease_until_ms": None,
            "epoch": 0,
            "revision": 1,
            "updated_at_ms": now_ms,
        }

    def _durable_row(self, raw_row: Mapping[str, Any] | None, now_ms: int) -> dict[str, Any]:
        row = self._durable_default_row(now_ms)
        if raw_row is not None:
            for key in row:
                if key in raw_row:
                    row[key] = raw_row[key]
        row["state"] = str(row.get("state") or "closed")
        row["failure_count"] = max(0, int(row.get("failure_count") or 0))
        row["epoch"] = max(0, int(row.get("epoch") or 0))
        row["revision"] = max(1, int(row.get("revision") or 1))
        row["updated_at_ms"] = max(0, int(row.get("updated_at_ms") or now_ms))
        return row

    def _durable_snapshot_from_row(
        self,
        row: Mapping[str, Any],
        *,
        now_ms: int,
        reason: str | None = None,
    ) -> CircuitSnapshot:
        state = str(row.get("state") or "closed")
        resolved_reason = reason or (
            "realtime_provider_rate_limit_backoff"
            if state == "open" and row.get("last_failure_class") == "rate_limit"
            else "realtime_provider_circuit_open"
            if state == "open"
            else "realtime_provider_circuit_half_open"
            if state == "half_open"
            else "realtime_provider_circuit_closed"
        )
        return CircuitSnapshot(
            state=state,
            reason=resolved_reason,
            failure_count=max(0, int(row.get("failure_count") or 0)),
            retry_after_ms=(
                max(0, int(row.get("open_until_ms") or 0) - now_ms)
                if state == "open"
                else 0
            ),
            half_open=state == "half_open",
            # Each provider identity owns a separate durable row. Revision is
            # a monotonic, non-secret generation marker for diagnostics.
            identity_generation=max(1, int(row.get("revision") or 1)),
            last_failure_class=(
                str(row["last_failure_class"])
                if row.get("last_failure_class") is not None
                else None
            ),
        )

    def _durable_acquire(self, identity: Any) -> CircuitAdmission:
        fingerprint = self.fingerprint(identity)
        now_ms = self._durable_now_ms()
        permit_token = uuid.uuid4().hex

        def mutate(raw_row: dict[str, Any] | None) -> tuple[Mapping[str, Any], Any]:
            row = self._durable_row(raw_row, now_ms)
            state = str(row["state"])
            if state == "open" and int(row.get("open_until_ms") or 0) <= now_ms:
                state = "half_open"
            if state == "open":
                return row, CircuitAdmission(
                    False,
                    self._durable_snapshot_from_row(row, now_ms=now_ms),
                )
            if state == "half_open":
                lease_until = int(row.get("half_open_lease_until_ms") or 0)
                if row.get("half_open_permit_token") and lease_until > now_ms:
                    return row, CircuitAdmission(
                        False,
                        self._durable_snapshot_from_row(
                            row,
                            now_ms=now_ms,
                            reason="realtime_provider_circuit_half_open",
                        ),
                    )
                row.update(
                    {
                        "state": "half_open",
                        "half_open_permit_token": permit_token,
                        "half_open_lease_until_ms": now_ms + self.half_open_lease_ms,
                        "revision": int(row["revision"]) + 1,
                        "updated_at_ms": now_ms,
                    }
                )
                snapshot = self._durable_snapshot_from_row(
                    row,
                    now_ms=now_ms,
                    reason="realtime_provider_circuit_half_open_trial",
                )
                return row, CircuitAdmission(
                    True,
                    snapshot,
                    CircuitPermit(
                        self,
                        identity_fingerprint=fingerprint,
                        identity_generation=snapshot.identity_generation,
                        circuit_epoch=int(row["epoch"]),
                        half_open=True,
                        permit_token=permit_token,
                    ),
                )
            row.update({"state": "closed", "updated_at_ms": now_ms})
            snapshot = self._durable_snapshot_from_row(
                row,
                now_ms=now_ms,
                reason="realtime_provider_circuit_closed",
            )
            return row, CircuitAdmission(
                True,
                snapshot,
                CircuitPermit(
                    self,
                    identity_fingerprint=fingerprint,
                    identity_generation=snapshot.identity_generation,
                    circuit_epoch=int(row["epoch"]),
                    half_open=False,
                ),
            )

        return self._persistence.transact_realtime_provider_circuit(fingerprint, mutate)

    def _durable_snapshot(self, identity: Any) -> CircuitSnapshot:
        fingerprint = self.fingerprint(identity)
        now_ms = self._durable_now_ms()

        def mutate(raw_row: dict[str, Any] | None) -> tuple[Mapping[str, Any], Any]:
            row = self._durable_row(raw_row, now_ms)
            if row["state"] == "half_open" and int(row.get("half_open_lease_until_ms") or 0) <= now_ms:
                row.update(
                    {
                        "state": "open",
                        "half_open_permit_token": None,
                        "half_open_lease_until_ms": None,
                        "revision": int(row["revision"]) + 1,
                        "updated_at_ms": now_ms,
                    }
                )
            return row, self._durable_snapshot_from_row(row, now_ms=now_ms)

        return self._persistence.transact_realtime_provider_circuit(fingerprint, mutate)

    def _durable_probe_success(self, identity: Any) -> CircuitSnapshot:
        fingerprint = self.fingerprint(identity)
        now_ms = self._durable_now_ms()

        def mutate(raw_row: dict[str, Any] | None) -> tuple[Mapping[str, Any], Any]:
            row = self._durable_row(raw_row, now_ms)
            row.update(
                {
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_class": None,
                    "opened_at_ms": None,
                    "open_until_ms": None,
                    "backoff_source": None,
                    "half_open_permit_token": None,
                    "half_open_lease_until_ms": None,
                    "epoch": int(row["epoch"]) + 1,
                    "revision": int(row["revision"]) + 1,
                    "updated_at_ms": now_ms,
                }
            )
            return row, self._durable_snapshot_from_row(
                row,
                now_ms=now_ms,
                reason="manual_probe_succeeded",
            )

        return self._persistence.transact_realtime_provider_circuit(fingerprint, mutate)

    def _durable_probe_failure(
        self,
        identity: Any,
        failure_class: str,
        *,
        retry_after_ms: int | None,
    ) -> CircuitSnapshot:
        fingerprint = self.fingerprint(identity)
        now_ms = self._durable_now_ms()
        default_ms = int(
            (
                self.rate_limit_cooldown_seconds
                if failure_class == "rate_limit"
                else self.cooldown_seconds
            )
            * 1_000
        )
        bounded_retry_after = normalize_retry_after_ms(retry_after_ms)
        cooldown_ms = max(default_ms, bounded_retry_after or 0)

        def mutate(raw_row: dict[str, Any] | None) -> tuple[Mapping[str, Any], Any]:
            row = self._durable_row(raw_row, now_ms)
            row.update(
                {
                    "state": "open",
                    "failure_count": max(
                        int(row["failure_count"]) + 1,
                        1 if failure_class == "rate_limit" else self.failure_threshold,
                    ),
                    "last_failure_class": failure_class,
                    "opened_at_ms": now_ms,
                    "open_until_ms": now_ms + cooldown_ms,
                    "backoff_source": (
                        "provider_retry_after" if bounded_retry_after else "default"
                    ),
                    "half_open_permit_token": None,
                    "half_open_lease_until_ms": None,
                    "epoch": int(row["epoch"]) + 1,
                    "revision": int(row["revision"]) + 1,
                    "updated_at_ms": now_ms,
                }
            )
            return row, self._durable_snapshot_from_row(
                row,
                now_ms=now_ms,
                reason="manual_probe_failed",
            )

        return self._persistence.transact_realtime_provider_circuit(fingerprint, mutate)

    def _durable_complete(
        self,
        permit: CircuitPermit,
        *,
        success: bool,
        failure_class: str | None,
        retry_after_ms: int | None,
        release_only: bool,
    ) -> None:
        now_ms = self._durable_now_ms()
        fingerprint = permit._identity_fingerprint

        def mutate(raw_row: dict[str, Any] | None) -> tuple[Mapping[str, Any], Any]:
            if raw_row is None:
                return self._durable_default_row(now_ms), None
            row = self._durable_row(raw_row, now_ms)
            if int(row["epoch"]) != permit._circuit_epoch:
                return row, None
            if permit.half_open and row.get("half_open_permit_token") != permit._permit_token:
                return row, None
            if release_only:
                if permit.half_open and row["state"] == "half_open":
                    row.update(
                        {
                            "state": "open",
                            "half_open_permit_token": None,
                            "half_open_lease_until_ms": None,
                            "revision": int(row["revision"]) + 1,
                            "updated_at_ms": now_ms,
                        }
                    )
                return row, None
            if success:
                row.update(
                    {
                        "state": "closed",
                        "failure_count": 0,
                        "last_failure_class": None,
                        "opened_at_ms": None,
                        "open_until_ms": None,
                        "backoff_source": None,
                        "half_open_permit_token": None,
                        "half_open_lease_until_ms": None,
                        "revision": int(row["revision"]) + 1,
                        "updated_at_ms": now_ms,
                    }
                )
                return row, None
            if failure_class not in RETRYABLE_FAILURE_CLASSES:
                if row["state"] == "half_open" or not permit.half_open:
                    row.update(
                        {
                            "state": "closed",
                            "failure_count": 0,
                            "last_failure_class": None,
                            "opened_at_ms": None,
                            "open_until_ms": None,
                            "backoff_source": None,
                            "half_open_permit_token": None,
                            "half_open_lease_until_ms": None,
                            "revision": int(row["revision"]) + 1,
                            "updated_at_ms": now_ms,
                        }
                    )
                return row, None
            failure_count = int(row["failure_count"]) + 1
            default_ms = int(
                (self.rate_limit_cooldown_seconds if failure_class == "rate_limit" else self.cooldown_seconds)
                * 1_000
            )
            bounded_retry_after = normalize_retry_after_ms(retry_after_ms)
            cooldown_ms = max(default_ms, bounded_retry_after or 0)
            should_open = (
                failure_class == "rate_limit"
                or permit.half_open
                or failure_count >= self.failure_threshold
            )
            row.update(
                {
                    "failure_count": failure_count,
                    "last_failure_class": failure_class,
                    "revision": int(row["revision"]) + 1,
                    "updated_at_ms": now_ms,
                }
            )
            if should_open:
                row.update(
                    {
                        "state": "open",
                        "opened_at_ms": now_ms,
                        "open_until_ms": now_ms + cooldown_ms,
                        "backoff_source": "provider_retry_after" if bounded_retry_after else "default",
                        "half_open_permit_token": None,
                        "half_open_lease_until_ms": None,
                        "epoch": int(row["epoch"]) + 1,
                    }
                )
            return row, None

        self._persistence.transact_realtime_provider_circuit(fingerprint, mutate)


def normalize_failure_class(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().lower()
    if normalized.startswith("providererrorcategory."):
        normalized = normalized.rsplit(".", 1)[-1]
    normalized = _FAILURE_CLASS_ALIASES.get(normalized, normalized)
    return normalized if normalized in RETRYABLE_FAILURE_CLASSES else None


def normalize_retry_after_ms(value: Any) -> int | None:
    """Normalize an upstream Retry-After hint without trusting unbounded input."""

    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if normalized < 0:
        return None
    return min(normalized, MAX_RETRY_AFTER_MS)


def classify_realtime_provider_failure(value: Any) -> str | None:
    """Classify only availability failures that justify a short circuit."""

    if isinstance(value, Mapping):
        # A Pi bridge/runtime failure can fall back to the direct coach before
        # Pi model work begins.  A successful direct fallback is not evidence
        # that the upstream Provider itself had an availability failure.
        if str(value.get("runtime_used") or "").strip().lower() == "direct":
            return None
        status_code = value.get("status_code")
        for metrics_key in ("agent_metrics", "metrics"):
            metrics = value.get(metrics_key)
            if isinstance(metrics, Mapping) and type(metrics.get("provider_status_code")) is int:
                status_code = metrics["provider_status_code"]
                break
        if status_code == 429:
            return "rate_limit"
        if type(status_code) is int and 500 <= status_code <= 599:
            return "provider_server"
        for key in ("failure_class", "provider_failure_class", "category"):
            classified = normalize_failure_class(value.get(key))
            if classified is not None:
                return classified
        fallback_reason = str(value.get("fallback_reason") or "").strip().lower()
        if fallback_reason in {
            "provider_timeout",
            "provider_transport",
            "provider_temporarily_unavailable",
        }:
            return {
                "provider_timeout": "timeout",
                "provider_transport": "transport",
                "provider_temporarily_unavailable": "provider_server",
            }[fallback_reason]
        code = str(value.get("fallback_error_code") or value.get("code") or "").strip().lower()
    else:
        status_code = getattr(value, "status_code", None)
        if status_code == 429:
            return "rate_limit"
        if type(status_code) is int and 500 <= status_code <= 599:
            return "provider_server"
        classified = normalize_failure_class(getattr(value, "category", None))
        if classified is not None:
            return classified
        fallback_reason = str(getattr(value, "fallback_reason", "") or "").strip().lower()
        if fallback_reason in {
            "provider_timeout",
            "provider_transport",
            "provider_temporarily_unavailable",
        }:
            return {
                "provider_timeout": "timeout",
                "provider_transport": "transport",
                "provider_temporarily_unavailable": "provider_server",
            }[fallback_reason]
        code = str(getattr(value, "code", "") or "").strip().lower()
        if code in {"agent_deadline_exceeded", "pi_timeout"}:
            return "timeout"
        if code == "pi_transport_error":
            return "transport"
        return None
    if code in {"agent_deadline_exceeded", "pi_timeout"}:
        return "timeout"
    if code == "pi_transport_error":
        return "transport"
    return None
