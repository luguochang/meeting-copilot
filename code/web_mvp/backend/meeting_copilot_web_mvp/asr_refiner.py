"""Process-resident local ASR refinement for completed realtime segments."""

from __future__ import annotations

import base64
import json
import math
import os
import queue
import re
import struct
import subprocess
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from meeting_copilot_web_mvp import asr_resource_guard as resource_guard

from meeting_copilot_web_mvp.local_runtime_paths import (
    packaged_funasr_environment,
    read_runtime_manifest,
    resolve_manifest_component,
)


_FILE_CHUNK_MIN_SAMPLES = 20 * 16_000
_FILE_CHUNK_MAX_SAMPLES = 30 * 16_000
_FILE_CHUNK_SEARCH_STEP_SAMPLES = 1_600
_FILE_CHUNK_ENERGY_WINDOW_SAMPLES = 3_200
DEFAULT_REFINER_IDLE_UNLOAD_SECONDS = 120.0
REALTIME_REFINER_POLICY_ENV = "MEETING_COPILOT_REALTIME_REFINER_POLICY"
DEFAULT_REALTIME_REFINER_POLICY = "online_only"
REALTIME_REFINER_POLICY_MODES = frozenset({"online_only", "on_demand", "prewarm"})
ONLINE_ONLY_REFINEMENT_REASON = "offline_refinement_bypassed_by_resource_policy"
RESOURCE_GUARD_ONLINE_FINAL_REASON = "offline_refinement_resource_guard_online_final"
WARMING_REFINEMENT_REASON = "offline_worker_warming_up"
BACKGROUND_REFINEMENT_WARMING_REASON = "offline_refinement_warming"
WARMING_ONLINE_FINAL_REASON = "offline_refinement_warming_online_final"
ONLINE_FINAL_RESOURCE_GUARD_ERRORS = frozenset({
    "asr_capacity_unavailable", "asr_resource_guard_triggered", "asr_cold_start_busy",
})
_STARTUP_EXCEPTION_CODES = {
    "FileNotFoundError": "worker_model_file_missing",
    "PermissionError": "worker_model_file_unreadable",
    "UnpicklingError": "worker_checkpoint_load_failed",
    "ValueError": "worker_model_load_failed",
    "TypeError": "worker_model_load_failed",
    "AssertionError": "worker_model_load_failed",
    "KeyError": "worker_model_load_failed",
    "AttributeError": "worker_model_load_failed",
    "RuntimeError": "worker_model_load_failed",
    "ModuleNotFoundError": "worker_dependency_unavailable",
    "ImportError": "worker_dependency_unavailable",
    "OSError": "worker_model_load_failed",
}
_CHECKPOINT_MODEL_NAMES = frozenset({"SeacoParaformer", "CTTransformer", "FsmnVADStreaming"})


def _startup_exception_name(line: str) -> str | None:
    match = re.match(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]{0,60})\s*:", line.strip())
    name = match.group(1) if match else None
    return name if name in _STARTUP_EXCEPTION_CODES else None


@dataclass(frozen=True)
class RefinementResult:
    text: str
    status: str
    model_id: str | None = None
    reason: str | None = None

    @property
    def authoritative(self) -> bool:
        return self.status == "refined" and bool(self.text.strip())


def realtime_refiner_policy(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve the realtime-only resource policy without changing file ASR."""

    effective_env = os.environ if environ is None else environ
    configured_value = str(effective_env.get(REALTIME_REFINER_POLICY_ENV) or "").strip()
    normalized = configured_value.casefold().replace("-", "_")
    if not normalized:
        mode = DEFAULT_REALTIME_REFINER_POLICY
        source = "default_resource_guard"
        warning = None
    elif normalized in REALTIME_REFINER_POLICY_MODES:
        mode = normalized
        source = "environment"
        warning = (
            "offline_refiner_cold_start_deferred_to_first_final"
            if mode == "on_demand"
            else None
        )
    else:
        mode = DEFAULT_REALTIME_REFINER_POLICY
        source = "invalid_environment_fallback"
        warning = "invalid_realtime_refiner_policy_fell_back_to_online_only"
    return {
        "schema_version": "realtime_refiner_policy.v1",
        "mode": mode,
        "source": source,
        "environment_variable": REALTIME_REFINER_POLICY_ENV,
        "configured_value": configured_value or None,
        "realtime_refinement_enabled": mode != "online_only",
        "prewarm_enabled": mode == "prewarm",
        "cold_start_deferred": mode == "on_demand",
        "degradation_reason": (
            ONLINE_ONLY_REFINEMENT_REASON if mode == "online_only" else None
        ),
        "warning": warning,
    }


def _path_env(
    *names: str,
    environ: Mapping[str, str] | None = None,
    preserve_symlink: bool = False,
) -> Path | None:
    effective_env = os.environ if environ is None else environ
    for name in names:
        value = str(effective_env.get(name) or "").strip()
        if value:
            path = Path(value).expanduser()
            if preserve_symlink:
                return Path(os.path.abspath(path))
            return path.resolve(strict=False)
    return None


def _configured_components(
    environ: Mapping[str, str] | None = None,
) -> tuple[Path | None, Path | None, Path | None, Path | None, Path | None]:
    effective_env = os.environ if environ is None else environ
    manifest = read_runtime_manifest(effective_env)

    def component(
        env_names: tuple[str, ...],
        *,
        component_name: str,
        expected_kind: str,
        mirrored_fields: tuple[tuple[str, ...], ...],
        default: Path | None = None,
        preserve_symlink: bool = False,
    ) -> Path | None:
        configured = _path_env(
            *env_names,
            environ=effective_env,
            preserve_symlink=preserve_symlink,
        )
        if configured is not None:
            return configured
        if not manifest.configured:
            return default
        return resolve_manifest_component(
            manifest,
            component_name=component_name,
            expected_kind=expected_kind,
            mirrored_fields=mirrored_fields,
        ).path

    python = component(
        ("MEETING_COPILOT_REALTIME_REFINER_PYTHON", "MEETING_COPILOT_BATCH_FUNASR_PYTHON"),
        component_name="file_asr.python_launcher",
        expected_kind="file",
        mirrored_fields=(
            ("runtimes", "funasr", "venv_executable"),
            ("file_asr", "runtime", "executable"),
        ),
        preserve_symlink=True,
    )
    worker = component(
        ("MEETING_COPILOT_REALTIME_REFINER_WORKER",),
        component_name="realtime_asr.offline_refiner_worker",
        expected_kind="file",
        mirrored_fields=(("offline_refiner", "worker", "path"),),
        default=Path(__file__).resolve().parents[3]
        / "asr_runtime"
        / "scripts"
        / "funasr_offline_refiner_worker.py",
    )
    model = component(
        ("MEETING_COPILOT_REALTIME_REFINER_MODEL", "MEETING_COPILOT_FILE_ASR_MODEL_DIR"),
        component_name="file_asr.model.offline",
        expected_kind="directory",
        mirrored_fields=(
            ("offline_refiner", "models", "offline"),
            ("file_asr", "models", "offline", "root"),
        ),
    )
    vad = component(
        ("MEETING_COPILOT_REALTIME_REFINER_VAD_MODEL", "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR"),
        component_name="file_asr.model.vad",
        expected_kind="directory",
        mirrored_fields=(
            ("offline_refiner", "models", "vad"),
            ("file_asr", "models", "vad", "root"),
        ),
    )
    punc = component(
        ("MEETING_COPILOT_REALTIME_REFINER_PUNC_MODEL", "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR"),
        component_name="file_asr.model.punc",
        expected_kind="directory",
        mirrored_fields=(
            ("offline_refiner", "models", "punc"),
            ("file_asr", "models", "punc", "root"),
        ),
    )
    return python, worker, model, vad, punc


def _component_missing(name: str, path: Path | None) -> bool:
    if path is None:
        return True
    if name in {"python", "worker"}:
        return not path.is_file()
    return not path.is_dir() or not (path / "model.pt").is_file() or not (path / "config.yaml").is_file()


def refinement_capability() -> dict[str, Any]:
    policy = realtime_refiner_policy()
    python, worker, model, vad, punc = _configured_components()
    missing = [
        name
        for name, path in (
            ("python", python),
            ("worker", worker),
            ("offline_model", model),
            ("vad_model", vad),
            ("punc_model", punc),
        )
        if _component_missing(name, path)
    ]
    return {
        "status": "ready" if not missing else "unavailable",
        "missing_components": missing,
        "model_id": model.name if model is not None else None,
        "process_resident": True,
        "remote_asr_used": False,
        "model_download_performed": False,
        "realtime_policy": policy,
    }


def _offline_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    effective_env = os.environ if environ is None else environ
    env = dict(effective_env)
    for key in list(env):
        upper = key.upper()
        if upper.endswith("_API_KEY") or upper in {
            "AUTHORIZATION",
            "MEETING_COPILOT_LOCAL_API_TOKEN",
        }:
            env.pop(key, None)
    python_home = str(
        effective_env.get("MEETING_COPILOT_REALTIME_REFINER_PYTHON_HOME") or ""
    ).strip()
    python_path = str(
        effective_env.get("MEETING_COPILOT_REALTIME_REFINER_PYTHONPATH") or ""
    ).strip()
    site_packages = str(effective_env.get("MEETING_COPILOT_FUNASR_SITE_PACKAGES") or "").strip()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    manifest = read_runtime_manifest(effective_env)
    worker = _configured_components(effective_env)[1]
    packaged_environment = packaged_funasr_environment(
        manifest,
        worker_path=worker,
        include_realtime_runtime=False,
    )
    if not packaged_environment.errors:
        python_home = python_home or packaged_environment.values.get("PYTHONHOME", "")
        python_path = python_path or packaged_environment.values.get("PYTHONPATH", "")
        site_packages = site_packages or packaged_environment.values.get(
            "MEETING_COPILOT_FUNASR_SITE_PACKAGES", ""
        )
    if python_home:
        env["PYTHONHOME"] = python_home
    if python_path:
        env["PYTHONPATH"] = python_path
    if site_packages:
        env["MEETING_COPILOT_FUNASR_SITE_PACKAGES"] = site_packages
    env.update({
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "MODELSCOPE_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    return env


def _worker_failure_code(line: str, *, startup: bool = False) -> str | None:
    """Classify diagnostics without retaining provider logs, paths or transcript text."""
    lowered = line.casefold()
    if any(value in lowered for value in ("not enough memory", "out of memory", "cannot allocate memory", "winerror 1455")):
        return "worker_out_of_memory"
    if "dll load failed" in lowered or "winerror 126" in lowered or "winerror 127" in lowered:
        return "worker_dependency_load_failed"
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        return "worker_dependency_unavailable"
    if startup:
        return _STARTUP_EXCEPTION_CODES.get(_startup_exception_name(line))
    return None


@dataclass
class _RefinerGeneration:
    process: subprocess.Popen[str]
    ready: threading.Event = field(default_factory=threading.Event)
    responses: queue.Queue[dict[str, Any]] = field(default_factory=queue.Queue)
    stderr_done: threading.Event = field(default_factory=threading.Event)
    metadata: dict[str, Any] = field(default_factory=dict)
    failure_code: str | None = None
    failure_lock: threading.Lock = field(default_factory=threading.Lock)
    monitor: resource_guard.OwnedPopenMonitor | None = None
    cold_lease: resource_guard.ColdStartLease | None = None
    lease_wait_started: bool = False
    startup_started_at: float = field(default_factory=time.monotonic)
    startup_finished_at: float | None = None
    startup_phase: str = "spawned"
    startup_stderr_lines: int = 0
    startup_exception: str | None = None
    startup_completed_model: str | None = None

    def record_startup_line(self, line: str) -> None:
        # Retain only fixed labels. A path, exception message, API key or ASR
        # text in the child stream must never become a diagnostic payload.
        with self.failure_lock:
            if self.startup_finished_at is not None:
                return
            self.startup_stderr_lines = min(10_000, self.startup_stderr_lines + 1)
            name = _startup_exception_name(line)
            if name:
                self.startup_exception = name
            if line.startswith("funasr_cpu_checkpoint_loader "):
                self.startup_phase = "model_loading"
                match = re.search(r"(?:^|\s)model=([A-Za-z_][A-Za-z0-9_]*)(?:\s|$)", line)
                if match and match.group(1) in _CHECKPOINT_MODEL_NAMES and " status=optimized " in line:
                    self.startup_completed_model = match.group(1)

    def finish_startup(self, phase: str) -> None:
        with self.failure_lock:
            if self.startup_finished_at is None:
                self.startup_finished_at = time.monotonic()
                self.startup_phase = phase

    def startup_diagnostics(self) -> dict[str, Any]:
        with self.failure_lock:
            finished = self.startup_finished_at or time.monotonic()
            return {"schema_version": "funasr_refiner_startup_diagnostics.v1", "phase": self.startup_phase,
                    "exception_name": self.startup_exception, "last_completed_model": self.startup_completed_model,
                    "stderr_line_count": self.startup_stderr_lines,
                    "elapsed_ms": min(600_000, max(0, round((finished - self.startup_started_at) * 1_000)))}

    def record_failure(self, code: str) -> str:
        with self.failure_lock:
            if self.failure_code not in resource_guard.RESOURCE_ERROR_CODES:
                if code in resource_guard.RESOURCE_ERROR_CODES or self.failure_code != "worker_out_of_memory":
                    self.failure_code = code
            return self.failure_code or code

    def release_cold_lease(self, *, ready: bool = False) -> None:
        with self.failure_lock:
            lease = self.cold_lease
            if lease is None:
                return
            watcher_closed = self.monitor is None or not self.monitor.is_alive
            if ready or (self.process.poll() is not None and watcher_closed):
                self.cold_lease = None
                lease.release()
            elif not self.lease_wait_started:
                self.lease_wait_started = True
                def wait_for_owned_exit() -> None:
                    self.process.wait()
                    if self.monitor is not None:
                        self.monitor.wait_closed()
                    self.release_cold_lease()
                threading.Thread(target=wait_for_owned_exit, daemon=True,
                                 name="funasr-cold-lease-exit").start()


class _ResidentOfflineRefiner:
    def __init__(self, components: tuple[Path, Path, Path, Path, Path], *,
                 memory_reader: resource_guard.MemoryReader | None = None,
                 monitor_interval_s: float = 0.25) -> None:
        self.components = components
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._generation: _RefinerGeneration | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._ready_metadata: dict[str, Any] = {}
        self._idle_timer: threading.Timer | None = None
        self._last_activity_monotonic: float | None = None
        self._idle_unload_count = 0
        self._last_stop_reason: str | None = None
        self._last_error_code: str | None = None
        self._last_exit_code: int | None = None
        self._recovery_required = False
        self._memory_reader = memory_reader
        self._monitor_interval_s = monitor_interval_s
        self._resource_check: dict[str, Any] | None = None
        self._active_meeting_leases: set[str] = set()
        self._closed = False
        self._meeting_prewarm_pending = False
        self._last_startup_diagnostics: dict[str, Any] | None = None
        self.process_start_count = 0
        self.completed_request_count = 0

    @staticmethod
    def _idle_unload_seconds() -> float | None:
        raw = str(
            os.environ.get("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS")
            or DEFAULT_REFINER_IDLE_UNLOAD_SECONDS
        ).strip()
        if raw.casefold() in {"0", "false", "no", "off", "disabled"}:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            return DEFAULT_REFINER_IDLE_UNLOAD_SECONDS
        if not math.isfinite(seconds) or seconds <= 0:
            return DEFAULT_REFINER_IDLE_UNLOAD_SECONDS
        return max(0.01, seconds)

    def _cancel_idle_timer_locked(self) -> None:
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_idle_unload_locked(self, *, delay_seconds: float | None = None) -> None:
        self._cancel_idle_timer_locked()
        seconds = self._idle_unload_seconds() if delay_seconds is None else delay_seconds
        running = self._process is not None and self._process.poll() is None
        if (
            seconds is None
            or not running
            or not self._ready_metadata
            or self._active_meeting_leases
        ):
            return
        self._last_activity_monotonic = time.monotonic()
        timer = threading.Timer(seconds, self._idle_unload_if_due)
        timer.daemon = True
        timer.name = "funasr-offline-refiner-idle-unload"
        self._idle_timer = timer
        timer.start()

    def _idle_unload_if_due(self) -> None:
        with self._lock:
            self._idle_timer = None
            seconds = self._idle_unload_seconds()
            running = self._process is not None and self._process.poll() is None
            if seconds is None or not running or self._active_meeting_leases:
                return
            last_activity = self._last_activity_monotonic
            remaining = (
                seconds
                if last_activity is None
                else (last_activity + seconds) - time.monotonic()
            )
            if remaining > 0.005:
                self._schedule_idle_unload_locked(delay_seconds=remaining)
                return
            self._idle_unload_count += 1
            self._stop_locked(reason="idle_timeout")

    def retain_for_meeting(self, meeting_id: str, *, prewarm: bool = False) -> bool:
        """Prevent idle unload while one live meeting can still need refinement."""

        normalized = str(meeting_id or "").strip()
        if not normalized:
            raise ValueError("meeting_id is required")
        with self._lock:
            if self._closed:
                return False
            self._active_meeting_leases.add(normalized)
            self._cancel_idle_timer_locked()
            if prewarm:
                self._schedule_meeting_prewarm_locked()
            return True

    def _schedule_meeting_prewarm_locked(self) -> bool:
        """Prepare this exact owner once at stream admission, never per PCM."""

        if self._closed or self._meeting_prewarm_pending or self._recovery_required or not self._active_meeting_leases:
            return False
        if self._process is not None and self._process.poll() is None:
            # Either already ready or loading under an existing explicit start.
            return False
        self._meeting_prewarm_pending = True

        def prepare() -> None:
            try:
                with self._lock:
                    if self._closed or self._recovery_required or not self._active_meeting_leases:
                        return
                try:
                    timeout_s = float(os.environ.get("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS") or 60.0)
                except (TypeError, ValueError):
                    timeout_s = 60.0
                if not math.isfinite(timeout_s):
                    timeout_s = 60.0
                # start() checks closed under the same lock immediately before
                # process creation, so shutdown cannot resurrect an old owner.
                self.start(timeout_s=max(1.0, timeout_s), retry_failed=False)
            except Exception:
                # Optional preparation must not crash online capture or retry
                # implicitly on every retain if the startup boundary fails.
                with self._lock:
                    if not self._closed:
                        self._last_error_code = self._last_error_code or "worker_prewarm_failed"
                        self._last_stop_reason = self._last_stop_reason or "worker_prewarm_failed"
                        self._recovery_required = True
            finally:
                with self._lock:
                    self._meeting_prewarm_pending = False

        try:
            threading.Thread(target=prepare, daemon=True, name="funasr-meeting-refiner-prewarm").start()
        except RuntimeError:
            self._meeting_prewarm_pending = False
            self._last_error_code = "worker_prewarm_failed"
            self._last_stop_reason = "worker_prewarm_failed"
            self._recovery_required = True
            return False
        return True

    def release_for_meeting(self, meeting_id: str) -> None:
        normalized = str(meeting_id or "").strip()
        if not normalized:
            raise ValueError("meeting_id is required")
        with self._lock:
            self._active_meeting_leases.discard(normalized)
            if not self._active_meeting_leases:
                self._schedule_idle_unload_locked()

    def readiness_waiter_for_meeting(
        self, meeting_id: str,
    ) -> Callable[[float, threading.Event], bool] | None:
        """Observe one retained prewarm generation without starting or stopping it."""

        with self._lock:
            if meeting_id not in self._active_meeting_leases:
                return None
            generation = self._generation
            expected_start_count = self.process_start_count + (generation is None)

        def wait_ready(timeout_s: float, cancelled: threading.Event) -> bool:
            nonlocal generation
            budget = float(timeout_s)
            if not math.isfinite(budget) or budget <= 0:
                return False
            deadline = time.monotonic() + budget
            while not cancelled.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                if not self._lock.acquire(timeout=min(0.05, remaining)):
                    continue
                try:
                    if self._closed or self._recovery_required or meeting_id not in self._active_meeting_leases:
                        return False
                    if generation is None:
                        # retain may have scheduled preparation before its child
                        # exists. Bind its first generation once, never a retry.
                        if self._generation is not None:
                            if self.process_start_count != expected_start_count:
                                return False
                            generation = self._generation
                        elif not self._meeting_prewarm_pending:
                            return False
                    if generation is not None:
                        if (self._generation is not generation
                                or self.process_start_count != expected_start_count
                                or generation.failure_code
                                or generation.process.poll() is not None):
                            return False
                        if generation.metadata and self._ready_metadata:
                            return not cancelled.is_set() and time.monotonic() < deadline
                finally:
                    self._lock.release()
                # Canceling asyncio.to_thread does not stop its thread. END and
                # disconnect signal this observer instead of stopping a shared
                # worker or waiting its entire startup budget at session close.
                cancelled.wait(min(0.05, max(0.0, deadline - time.monotonic())))
            return False

        return wait_ready

    def resource_refusal_for_meeting(self, meeting_id: str) -> str | None:
        """Observe a retained owner's resource refusal, never claim model readiness.

        A failed readiness observer alone is insufficient: cancellation,
        missing leases, model/transport failures, and uncertain probes remain
        failures. This observer neither prepares nor retries a generation.
        """
        with self._lock:
            if self._closed or meeting_id not in self._active_meeting_leases:
                return None
            generation = self._generation
            if generation is not None and generation.failure_code in ONLINE_FINAL_RESOURCE_GUARD_ERRORS:
                reason = generation.failure_code
            elif self._recovery_required:
                reason = self._last_error_code
            else:
                return None
            return reason if reason in ONLINE_FINAL_RESOURCE_GUARD_ERRORS else None

    def _command(self) -> list[str]:
        python, worker, model, vad, punc = self.components
        command = [
            str(python), str(worker),
            "--model", str(model),
            "--vad-model", str(vad),
            "--punc-model", str(punc),
            "--device", "cpu",
        ]
        hotwords = _path_env("MEETING_COPILOT_REALTIME_REFINER_HOTWORDS")
        if hotwords is None:
            default_hotwords = Path(__file__).resolve().parents[4] / "configs" / "asr_hotwords.json"
            hotwords = default_hotwords if default_hotwords.is_file() else None
        if hotwords is not None and hotwords.is_file():
            command.extend(["--hotword-manifest", str(hotwords)])
        return command

    def _read_stderr(self, generation: _RefinerGeneration) -> None:
        stream = generation.process.stderr
        try:
            if stream is not None:
                while line := stream.readline(8192):
                    generation.record_startup_line(line)
                    code = _worker_failure_code(line, startup=not bool(generation.metadata))
                    if code:
                        generation.record_failure(code)
        except (OSError, ValueError):
            pass
        finally:
            if stream is not None:
                stream.close()
            generation.stderr_done.set()

    def _failure_locked(self, generation: _RefinerGeneration, *, code: str, exit_code: int | None,
                        reason: str = "worker_exit") -> None:
        if self._generation is not generation:
            return
        if generation.failure_code in resource_guard.RESOURCE_ERROR_CODES:
            code = generation.failure_code
        if code in resource_guard.RESOURCE_ERROR_CODES:
            reason = code
            if generation.monitor is not None and generation.monitor.failure is not None:
                self._resource_check = getattr(generation.monitor.failure, "capacity_check", self._resource_check)
        self._last_error_code = code
        self._last_exit_code = exit_code
        self._last_stop_reason = reason
        self._recovery_required = True
        self._ready_metadata = {}
        if not generation.metadata:
            generation.finish_startup("failed")
        self._last_startup_diagnostics = generation.startup_diagnostics()

    @staticmethod
    def _resource_notification(generation: _RefinerGeneration, error: resource_guard.AsrResourceError) -> None:
        # Never acquire the inference lock: refine() holds it while waiting for
        # a response. Notify first so that a guard can interrupt that wait.
        code = generation.record_failure(error.code)
        generation.responses.put({"event_type": "worker_exit", "error_code": code})
        generation.ready.set()

    def _resource_reason_locked(self, fallback: str) -> str:
        generation = self._generation
        if generation is not None and generation.failure_code in resource_guard.RESOURCE_ERROR_CODES:
            self._failure_locked(generation, code=generation.failure_code, exit_code=generation.process.poll())
        return self._last_error_code if self._last_error_code in resource_guard.RESOURCE_ERROR_CODES else fallback

    def _read_stdout(self, generation: _RefinerGeneration) -> None:
        process = generation.process
        assert process.stdout is not None
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event_type") == "ready":
                    with self._lock:
                        if (self._generation is not generation or self._recovery_required
                                or generation.failure_code in resource_guard.RESOURCE_ERROR_CODES):
                            continue
                        generation.metadata = dict(event)
                        self._ready_metadata = generation.metadata
                        generation.finish_startup("ready")
                        self._last_startup_diagnostics = generation.startup_diagnostics()
                        generation.release_cold_lease(ready=True)
                        generation.ready.set()
                else:
                    generation.responses.put(dict(event))
        except (OSError, ValueError):
            pass
        finally:
            process.stdout.close()
            if generation.monitor is not None:
                generation.monitor.close()
            exit_code = process.poll()
            if exit_code is None:
                try:
                    exit_code = process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    try:
                        if generation.monitor is not None:
                            exit_code = generation.monitor.stop_owned()
                        else:
                            exit_code = resource_guard.OwnedPopenMonitor(process).stop_owned()
                    except resource_guard.AsrResourceError as error:
                        generation.record_failure(error.code)
            generation.stderr_done.wait(0.5)
            code = generation.failure_code or ("worker_exited" if generation.metadata else "worker_exited_before_ready")
            # Release a request waiting under the inference lock before observing failure.
            code = generation.record_failure(code)
            generation.responses.put({"event_type": "worker_exit", "returncode": exit_code, "error_code": code})
            generation.ready.set()
            generation.release_cold_lease()
            with self._lock:
                self._failure_locked(generation, code=code, exit_code=exit_code)

    def start(self, timeout_s: float = 60.0, *, retry_failed: bool = False) -> bool:
        created = False
        with self._lock:
            if self._closed:
                return False
            generation = self._generation
            running = generation is not None and generation.process.poll() is None
            if generation is not None and generation.failure_code in resource_guard.RESOURCE_ERROR_CODES:
                self._failure_locked(generation, code=generation.failure_code, exit_code=generation.process.poll())
            if running and not self._recovery_required:
                if self._ready_metadata:
                    return True
                # Join the existing cold start; concurrent callers never terminate it.
            else:
                if generation is not None and not running and not self._recovery_required:
                    self._failure_locked(generation, code=generation.failure_code or "worker_exited_before_ready",
                                         exit_code=generation.process.poll())
                if self._recovery_required and not retry_failed:
                    return False
                self._stop_locked(reason="restart")
                if not self.cleanup_complete():
                    # Cleanup has not acknowledged exit. Do not spawn a second
                    # heavy process or release its cold-start reservation.
                    return False
                lease = None
                try:
                    # Non-Windows keeps its existing startup behavior; explicit
                    # readers permit deterministic capacity tests on any OS.
                    if os.name == "nt" or self._memory_reader is not None:
                        lease = resource_guard.cold_start_lease()
                    self._resource_check = resource_guard.check_capacity(
                        resource_guard.COLD_LOAD_PROFILE, reader=self._memory_reader,
                    ).as_dict()
                except resource_guard.AsrResourceError as error:
                    if lease is not None:
                        lease.release()
                    self._last_error_code = error.code
                    self._last_stop_reason = error.code
                    self._last_exit_code = None
                    self._resource_check = getattr(error, "capacity_check", self._resource_check)
                    self._recovery_required = True
                    return False
                self._ready_metadata = {}
                self._last_startup_diagnostics = None
                self._last_error_code = None
                self._last_exit_code = None
                self._recovery_required = False
                try:
                    self._process = subprocess.Popen(
                        self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace", bufsize=1, env=_offline_environment(),
                    )
                except OSError:
                    if lease is not None:
                        lease.release()
                    self._process = None
                    self._last_stop_reason = "worker_spawn_failed"
                    self._last_error_code = "worker_spawn_failed"
                    self._recovery_required = True
                    return False
                generation = _RefinerGeneration(self._process, cold_lease=lease)
                generation.monitor = resource_guard.OwnedPopenMonitor(
                    self._process, reader=self._memory_reader,
                    on_failure=lambda error: self._resource_notification(generation, error),
                    interval_s=self._monitor_interval_s,
                )
                self._generation = generation
                self._ready = generation.ready
                self._responses = generation.responses
                self._last_stop_reason = None
                self.process_start_count += 1
                created = True
                self._stderr_reader = threading.Thread(target=self._read_stderr, args=(generation,), daemon=True,
                                                       name="funasr-offline-refiner-stderr")
                self._reader = threading.Thread(target=self._read_stdout, args=(generation,), daemon=True,
                                                name="funasr-offline-refiner-reader")
                self._stderr_reader.start()
                self._reader.start()
                generation.monitor.start()
        assert generation is not None
        if not generation.ready.wait(timeout_s):
            with self._lock:
                if created and self._generation is generation:
                    exit_code = generation.process.poll()
                    code = generation.failure_code or ("worker_exited_before_ready" if exit_code is not None else "worker_start_timeout")
                    reason = "worker_exit" if exit_code is not None else "worker_start_timeout"
                    self._failure_locked(generation, code=code, exit_code=exit_code, reason=reason)
                    self._stop_locked(reason=reason, graceful_timeout_s=min(0.05, max(0.0, timeout_s)))
            return False
        with self._lock:
            if self._generation is generation and generation.failure_code and not generation.metadata:
                self._failure_locked(generation, code=generation.failure_code, exit_code=generation.process.poll())
            ready = bool(
                self._generation is generation
                and generation.process.poll() is None
                and self._ready_metadata
                and not self._recovery_required
                and generation.failure_code not in resource_guard.RESOURCE_ERROR_CODES
            )
            if ready:
                self._schedule_idle_unload_locked()
            return ready

    def refine_on_demand(self, pcm16: bytes, *, timeout_s: float) -> RefinementResult:
        """Prepare one retained generation in the background without joining startup."""

        # Background preparation briefly owns this lock while admitting/spawning
        # its child. Realtime callers must not queue behind that cold operation.
        if not self._lock.acquire(blocking=False):
            reason = WARMING_REFINEMENT_REASON if self._meeting_prewarm_pending else "offline_worker_busy"
            return RefinementResult(text="", status="unavailable", reason=reason)
        try:
            if self._closed:
                return RefinementResult(text="", status="failed", reason="offline_worker_closed")
            reason = self._resource_reason_locked("offline_worker_not_ready")
            if self._recovery_required:
                return RefinementResult(text="", status="failed", reason=self._last_error_code or reason)
            generation = self._generation
            if generation is not None and (generation.failure_code or generation.process.poll() is not None):
                self._failure_locked(generation, code=generation.failure_code or "worker_exited_before_ready",
                    exit_code=generation.process.poll())
                return RefinementResult(text="", status="failed", reason=self._last_error_code or reason)
            ready = bool(generation is not None and generation.process.poll() is None and self._ready_metadata)
            if not ready:
                # Only an actual retained live stream can demand optional loading.
                # Explicit file ASR continues to own its synchronous cold startup.
                if not self._active_meeting_leases:
                    return RefinementResult(text="", status="unavailable", reason="offline_worker_prewarm_required")
                self._schedule_meeting_prewarm_locked()
                if self._recovery_required:
                    return RefinementResult(text="", status="failed", reason=self._last_error_code or reason)
                # pending also covers the interval before a generation exists;
                # the daemon owns start()/ready.wait(), never this PCM request.
                return RefinementResult(text="", status="unavailable", reason=WARMING_REFINEMENT_REASON)
        finally:
            self._lock.release()
        return self.refine(pcm16, timeout_s=timeout_s, allow_cold_start=False)

    def refine(self, pcm16: bytes, *, timeout_s: float, retry_failed: bool = False,
               allow_cold_start: bool = True) -> RefinementResult:
        request_budget = max(0.01, float(timeout_s))
        deadline = time.monotonic() + request_budget
        try:
            configured_startup_timeout = float(os.environ.get("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS") or 60.0)
        except (TypeError, ValueError):
            configured_startup_timeout = 60.0
        startup_timeout = min(max(0.01, configured_startup_timeout), request_budget)
        try:
            resource_guard.check_capacity(resource_guard.WARM_INFERENCE_PROFILE, len(pcm16), reader=self._memory_reader)
        except resource_guard.AsrResourceError as error:
            return RefinementResult(text="", status="failed", reason=error.code)
        # Startup waits must not hold the inference lock needed by the ready reader.
        if allow_cold_start and not self.start(timeout_s=startup_timeout, retry_failed=retry_failed):
            with self._lock:
                reason = self._resource_reason_locked("offline_worker_not_ready")
            return RefinementResult(text="", status="failed", reason=reason)
        # Waiting callers own their deadline, not the shared worker. A caller
        # that expires before sending must never stop an otherwise ready child.
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._lock.acquire(timeout=remaining):
            return RefinementResult(text="", status="failed", reason="offline_request_deadline_expired")
        try:
            generation = self._generation
            if generation is not None and generation.failure_code in resource_guard.RESOURCE_ERROR_CODES:
                self._failure_locked(generation, code=generation.failure_code, exit_code=generation.process.poll())
            if generation is None or generation.process.poll() is not None or self._recovery_required:
                reason = "offline_worker_not_ready" if allow_cold_start or self._recovery_required else "offline_worker_prewarm_required"
                return RefinementResult(text="", status="failed", reason=self._resource_reason_locked(reason))
            if not self._ready_metadata:
                # Live prewarm requests neither create a heavy child nor join
                # its long startup wait. Explicit prewarm/file ASR owns loading.
                reason = "offline_worker_not_ready" if allow_cold_start else "offline_worker_warming_up"
                return RefinementResult(text="", status="unavailable", reason=reason)
            if deadline - time.monotonic() <= 0:
                return RefinementResult(text="", status="failed", reason="offline_request_deadline_expired")
            self._cancel_idle_timer_locked()
            try:
                process = self._process
                assert process is not None and process.stdin is not None
                request_id = uuid.uuid4().hex
                request = {
                    "command": "refine",
                    "request_id": request_id,
                    "sample_rate": 16_000,
                    "pcm16_base64": base64.b64encode(pcm16).decode("ascii"),
                }
                serialized_request = json.dumps(request, ensure_ascii=False) + "\n"
                if deadline - time.monotonic() <= 0:
                    return RefinementResult(text="", status="failed", reason="offline_request_deadline_expired")
                try:
                    process.stdin.write(serialized_request)
                    process.stdin.flush()
                except (OSError, ValueError):
                    self._failure_locked(generation, code="worker_write_failed", exit_code=process.poll(), reason="worker_write_failed")
                    self._stop_locked(reason="worker_write_failed")
                    return RefinementResult(text="", status="failed", reason=self._resource_reason_locked("offline_worker_write_failed"))
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._failure_locked(generation, code="worker_timeout", exit_code=process.poll(), reason="worker_timeout")
                        self._stop_locked(reason="worker_timeout")
                        return RefinementResult(text="", status="failed", reason=self._resource_reason_locked("offline_worker_timeout"))
                    try:
                        response = self._responses.get(timeout=remaining)
                    except queue.Empty:
                        self._failure_locked(generation, code="worker_timeout", exit_code=process.poll(), reason="worker_timeout")
                        self._stop_locked(reason="worker_timeout")
                        return RefinementResult(text="", status="failed", reason=self._resource_reason_locked("offline_worker_timeout"))
                    if response.get("event_type") == "worker_exit":
                        self._failure_locked(generation, code=str(response.get("error_code") or "worker_exited"),
                                             exit_code=generation.process.poll())
                        self._stop_locked(reason="worker_exit")
                        return RefinementResult(text="", status="failed", reason=self._resource_reason_locked("offline_worker_exited"))
                    if response.get("request_id") != request_id:
                        continue
                    if generation.failure_code in resource_guard.RESOURCE_ERROR_CODES:
                        self._failure_locked(generation, code=generation.failure_code, exit_code=process.poll())
                        self._stop_locked(reason=generation.failure_code)
                        return RefinementResult(text="", status="failed", reason=generation.failure_code)
                    self.completed_request_count += 1
                    if response.get("status") != "ok":
                        return RefinementResult(text="", status="failed", reason="offline_worker_failed")
                    text = str(response.get("text") or "").strip()
                    model_id = str(response.get("model_id") or self.components[2].name)
                    if not text:
                        return RefinementResult(text="", status="empty", model_id=model_id, reason="offline_text_empty")
                    return RefinementResult(text=text, status="refined", model_id=model_id)
            finally:
                self._schedule_idle_unload_locked()
        finally:
            self._lock.release()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._resource_reason_locked("offline_worker_not_ready")
            running = self._process is not None and self._process.poll() is None
            idle_seconds = self._idle_unload_seconds()
            idle_age = (
                max(0.0, time.monotonic() - self._last_activity_monotonic)
                if self._last_activity_monotonic is not None
                else None
            )
            return {
                **self._ready_metadata,
                "spawned": self._process is not None,
                "process_running": running,
                "process_ready": running and bool(self._ready_metadata),
                "pid": self._process.pid if running and self._process is not None else None,
                "process_start_count": self.process_start_count,
                "completed_request_count": self.completed_request_count,
                "idle_unload_seconds": idle_seconds,
                "idle_age_seconds": round(idle_age, 3) if idle_age is not None else None,
                "idle_unload_scheduled": self._idle_timer is not None,
                "idle_unload_count": self._idle_unload_count,
                "active_meeting_lease_count": len(self._active_meeting_leases),
                "closed": self._closed,
                "meeting_prewarm_pending": self._meeting_prewarm_pending,
                "idle_unload_blocked_by_active_meeting": bool(
                    self._active_meeting_leases
                ),
                "last_stop_reason": self._last_stop_reason,
                "last_error_code": self._last_error_code,
                "last_exit_code": self._last_exit_code,
                "recovery_required": self._recovery_required,
                "cleanup_incomplete": not self.cleanup_complete() and self._recovery_required,
                "startup_diagnostics": self._generation.startup_diagnostics() if self._generation else self._last_startup_diagnostics,
                "resource": {
                    "last_error_code": self._last_error_code if self._last_error_code in resource_guard.RESOURCE_ERROR_CODES else None,
                    "last_check": self._resource_check,
                },
            }

    def _stop_locked(
        self,
        *,
        reason: str,
        graceful_timeout_s: float = 2.0,
    ) -> None:
        self._cancel_idle_timer_locked()
        process = self._process
        generation = self._generation
        self._ready_metadata = {}
        if self._last_error_code not in resource_guard.RESOURCE_ERROR_CODES:
            self._last_stop_reason = reason
        if generation is not None:
            generation.ready.set()
            if generation.monitor is not None:
                if not generation.monitor.close():
                    generation.record_failure("asr_resource_guard_triggered")
                    self._failure_locked(generation, code=generation.failure_code, exit_code=process.poll() if process else None)
        if process is None:
            return
        if process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=max(0.0, graceful_timeout_s))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    if generation is not None and generation.monitor is not None:
                        generation.monitor.stop_owned()
                    else:
                        resource_guard.OwnedPopenMonitor(process).stop_owned()
                except resource_guard.AsrResourceError as error:
                    self._last_error_code = error.code
                    self._last_stop_reason = error.code
                    self._recovery_required = True
        self._last_exit_code = process.poll()
        if generation is not None:
            if generation.monitor is not None and generation.monitor.failure is not None:
                self._failure_locked(generation, code=generation.monitor.failure.code, exit_code=self._last_exit_code)
            generation.release_cold_lease()
        if self.cleanup_complete():
            self._process = None
            self._generation = None
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_locked(reason="shutdown")

    def cleanup_complete(self) -> bool:
        """A dead exact child and a stopped watcher acknowledge ownership end."""
        with self._lock:
            generation = self._generation
            process_exited = self._process is None or self._process.poll() is not None
            watcher_stopped = generation is None or generation.monitor is None or not generation.monitor.is_alive
            return process_exited and watcher_stopped


_REFINER_LOCK = threading.Lock()
_REFINER: _ResidentOfflineRefiner | None = None


def _get_resident_refiner() -> _ResidentOfflineRefiner:
    global _REFINER
    components = _configured_components()
    if any(path is None for path in components):
        # Capability checks normally guard this path, but startup/prewarm can
        # be raced or deliberately stubbed in tests. Do not turn a missing
        # optional local runtime into an application-wide AssertionError.
        raise RuntimeError("offline_refinement_components_missing")
    typed_components = tuple(components)
    with _REFINER_LOCK:
        if _REFINER is None or _REFINER.components != typed_components:
            if _REFINER is not None:
                _REFINER.shutdown()
                if not _REFINER.cleanup_complete():
                    # Retain the exact previous owner. A changed configuration
                    # must never silently route work to it or spawn beside it.
                    status = _REFINER.status()
                    code = status.get("last_error_code")
                    raise resource_guard.AsrResourceError(code if code in resource_guard.RESOURCE_ERROR_CODES else "asr_resource_guard_triggered")
            _REFINER = _ResidentOfflineRefiner(typed_components)  # type: ignore[arg-type]
        return _REFINER


def prewarm_refiner_worker(*, retry_failed: bool = False) -> bool:
    if not realtime_refiner_policy()["prewarm_enabled"]:
        return False
    if refinement_capability()["status"] != "ready":
        return False
    # Keep prewarm defensive when capability reporting is supplied by a
    # feature flag, a test double, or a stale runtime manifest. The actual
    # refinement call still returns a structured unavailable result.
    components = _configured_components()
    if any(path is None for path in components):
        return False
    timeout_s = max(1.0, float(os.environ.get("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS") or 60.0))
    # Prewarm is a one-shot startup/explicit verification action, never an inference retry loop.
    try:
        return _get_resident_refiner().start(timeout_s=timeout_s, retry_failed=retry_failed)
    except resource_guard.AsrResourceError:
        # Offline ASR is optional at startup. Keep the previous exact owner
        # and its safe failure status without taking the online backend down.
        return False


def retain_refiner_for_meeting(meeting_id: str) -> bool:
    """Hold the resident model for the lifetime of a live meeting stream."""

    policy = realtime_refiner_policy()
    if not policy["realtime_refinement_enabled"]:
        return False
    if refinement_capability()["status"] != "ready":
        return False
    try:
        resident = _get_resident_refiner()
    except RuntimeError:
        return False
    return resident.retain_for_meeting(meeting_id, prewarm=bool(policy["prewarm_enabled"]))


def release_refiner_for_meeting(meeting_id: str) -> None:
    """Release a meeting lease without creating or starting a worker."""

    with _REFINER_LOCK:
        resident = _REFINER
    if resident is not None:
        resident.release_for_meeting(meeting_id)


def get_refiner_readiness_waiter(
    meeting_id: str,
) -> Callable[[float, threading.Event], bool] | None:
    """Return a passive waiter only for configured, retained prewarm capability."""

    if not realtime_refiner_policy()["prewarm_enabled"]:
        return None
    if refinement_capability()["status"] != "ready":
        return None
    with _REFINER_LOCK:
        resident = _REFINER
    return resident.readiness_waiter_for_meeting(meeting_id) if resident is not None else None


def refiner_resource_refusal_for_meeting(meeting_id: str) -> str | None:
    """Read a confirmed resource refusal for this prewarm stream's exact owner."""
    if not realtime_refiner_policy()["prewarm_enabled"]:
        return None
    with _REFINER_LOCK:
        resident = _REFINER
    return resident.resource_refusal_for_meeting(meeting_id) if resident is not None else None


def refiner_worker_status() -> dict[str, Any]:
    policy = realtime_refiner_policy()
    with _REFINER_LOCK:
        worker = _REFINER
    status = worker.status() if worker is not None else {
        "spawned": False,
        "process_running": False,
        "process_ready": False,
        "pid": None,
        "resource": {"last_error_code": None, "last_check": None},
        "process_start_count": 0,
        "completed_request_count": 0,
        "idle_unload_seconds": _ResidentOfflineRefiner._idle_unload_seconds(),
        "idle_age_seconds": None,
        "idle_unload_scheduled": False,
        "idle_unload_count": 0,
        "active_meeting_lease_count": 0,
        "closed": False,
        "meeting_prewarm_pending": False,
        "idle_unload_blocked_by_active_meeting": False,
        "last_stop_reason": None,
        "last_error_code": None,
        "last_exit_code": None,
        "recovery_required": False,
    }
    return {**status, "realtime_policy": policy}


def shutdown_refiner_worker() -> None:
    global _REFINER
    with _REFINER_LOCK:
        worker = _REFINER
        if worker is not None:
            worker.shutdown()
            if worker.cleanup_complete():
                _REFINER = None


def _pcm_f32_to_pcm16(payload: bytes) -> bytes:
    if not payload or len(payload) % 4:
        raise ValueError("pcm_unaligned")
    pcm16 = bytearray(len(payload) // 2)
    for index, (sample,) in enumerate(struct.iter_unpack("<f", payload)):
        if not math.isfinite(sample):
            raise ValueError("pcm_non_finite")
        clamped = max(-1.0, min(1.0, sample))
        value = round(clamped * (32_767 if clamped >= 0 else 32_768))
        struct.pack_into("<h", pcm16, index * 2, value)
    return bytes(pcm16)


def refine_pcm_f32(payload: bytes, *, timeout_s: float = 30.0) -> RefinementResult:
    """Refine one 16 kHz mono float32 segment using local offline FunASR."""
    try:
        pcm16 = _pcm_f32_to_pcm16(payload)
    except ValueError as exc:
        return RefinementResult(text="", status="invalid_audio", reason=str(exc))
    policy = realtime_refiner_policy()
    if not policy["realtime_refinement_enabled"]:
        return RefinementResult(
            text="",
            status="bypassed",
            reason=str(policy["degradation_reason"]),
        )
    capability = refinement_capability()
    if capability["status"] != "ready":
        return RefinementResult(
            text="",
            status="unavailable",
            model_id=capability.get("model_id"),
            reason="offline_refinement_components_missing",
        )
    try:
        resident = _get_resident_refiner()
        if policy["mode"] == "prewarm":
            return resident.refine(pcm16, timeout_s=timeout_s, allow_cold_start=False)
        return resident.refine_on_demand(pcm16, timeout_s=timeout_s)
    except resource_guard.AsrResourceError as error:
        return RefinementResult(text="", status="failed", reason=error.code)


def _split_pcm16_for_file_refinement(pcm16: bytes) -> list[bytes]:
    samples = memoryview(pcm16).cast("h")
    total_samples = len(samples)
    if total_samples <= _FILE_CHUNK_MAX_SAMPLES:
        return [pcm16]

    chunks: list[bytes] = []
    chunk_start = 0
    while total_samples - chunk_start > _FILE_CHUNK_MAX_SAMPLES:
        search_start = chunk_start + _FILE_CHUNK_MIN_SAMPLES
        search_end = chunk_start + _FILE_CHUNK_MAX_SAMPLES
        best_cut = search_end
        best_energy: int | None = None
        half_window = _FILE_CHUNK_ENERGY_WINDOW_SAMPLES // 2
        for candidate in range(
            search_start,
            search_end + 1,
            _FILE_CHUNK_SEARCH_STEP_SAMPLES,
        ):
            window_start = max(chunk_start, candidate - half_window)
            window_end = min(total_samples, candidate + half_window)
            energy = sum(abs(sample) for sample in samples[window_start:window_end])
            if best_energy is None or energy < best_energy:
                best_energy = energy
                best_cut = candidate
        chunks.append(samples[chunk_start:best_cut].tobytes())
        chunk_start = best_cut
    if chunk_start < total_samples:
        chunks.append(samples[chunk_start:].tobytes())
    return chunks


def _join_refined_file_chunks(parts: list[str]) -> str:
    joined = ""
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        if (
            joined
            and joined[-1].isascii()
            and joined[-1].isalnum()
            and part[0].isascii()
            and part[0].isalnum()
        ):
            joined += " "
        joined += part
    return joined


def _pcm16_is_exact_silence(pcm16: bytes) -> bool:
    """Identify a chunk that contains no PCM signal at all.

    File refinement may split a recording at a long trailing silence. An empty
    FunASR result for that final all-zero chunk is a valid no-op, while an
    empty result for non-silent audio must remain fail-closed.
    """

    if not pcm16 or len(pcm16) % 2:
        return False
    return not any(memoryview(pcm16).cast("h"))


def refine_wav_file(audio_path: Path, *, timeout_s: float = 180.0, retry_failed: bool = False) -> RefinementResult:
    """Transcribe one normalized 16 kHz mono PCM WAV with the resident model."""
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            if (
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getframerate(),
                wav_file.getcomptype(),
            ) != (1, 2, 16_000, "NONE"):
                return RefinementResult(
                    text="",
                    status="invalid_audio",
                    reason="wav_must_be_pcm16_mono_16khz",
                )
            # Admission precedes the full-file PCM buffer and chunk copies.
            resource_guard.check_capacity(
                resource_guard.WARM_INFERENCE_PROFILE,
                wav_file.getnframes() * wav_file.getnchannels() * wav_file.getsampwidth(),
            )
            pcm16 = wav_file.readframes(wav_file.getnframes())
    except resource_guard.AsrResourceError as error:
        return RefinementResult(text="", status="failed", reason=error.code)
    except (OSError, EOFError, wave.Error) as exc:
        return RefinementResult(text="", status="invalid_audio", reason=type(exc).__name__)
    capability = refinement_capability()
    if capability["status"] != "ready":
        return RefinementResult(
            text="",
            status="unavailable",
            model_id=capability.get("model_id"),
            reason="offline_refinement_components_missing",
        )
    try:
        resident = _get_resident_refiner()
    except resource_guard.AsrResourceError as error:
        return RefinementResult(text="", status="failed", reason=error.code)
    chunks = _split_pcm16_for_file_refinement(pcm16)
    deadline = time.monotonic() + timeout_s
    texts: list[str] = []
    model_id: str | None = None
    for index, chunk in enumerate(chunks, start=1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return RefinementResult(
                text="",
                status="failed",
                model_id=model_id,
                reason=f"offline_chunk_{index}_timeout",
            )
        kwargs = {"retry_failed": True} if retry_failed and index == 1 else {}
        result = resident.refine(chunk, timeout_s=remaining, **kwargs)
        model_id = result.model_id or model_id
        if not result.authoritative:
            if (
                index == len(chunks)
                and result.reason == "offline_text_empty"
                and _pcm16_is_exact_silence(chunk)
            ):
                continue
            return RefinementResult(
                text="",
                status=result.status,
                model_id=model_id,
                reason=result.reason if result.reason in resource_guard.RESOURCE_ERROR_CODES else f"offline_chunk_{index}:{result.reason or result.status}",
            )
        texts.append(result.text)
    text = _join_refined_file_chunks(texts)
    if not text:
        return RefinementResult(
            text="",
            status="empty",
            model_id=model_id,
            reason="offline_text_empty",
        )
    return RefinementResult(text=text, status="refined", model_id=model_id)
