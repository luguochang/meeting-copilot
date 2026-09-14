"""Process-resident local ASR refinement for completed realtime segments."""

from __future__ import annotations

import base64
import json
import math
import os
import queue
import struct
import subprocess
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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


class _ResidentOfflineRefiner:
    def __init__(self, components: tuple[Path, Path, Path, Path, Path]) -> None:
        self.components = components
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._ready_metadata: dict[str, Any] = {}
        self._idle_timer: threading.Timer | None = None
        self._last_activity_monotonic: float | None = None
        self._idle_unload_count = 0
        self._last_stop_reason: str | None = None
        self._active_meeting_leases: set[str] = set()
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

    def retain_for_meeting(self, meeting_id: str) -> None:
        """Prevent idle unload while one live meeting can still need refinement."""

        normalized = str(meeting_id or "").strip()
        if not normalized:
            raise ValueError("meeting_id is required")
        with self._lock:
            self._active_meeting_leases.add(normalized)
            self._cancel_idle_timer_locked()

    def release_for_meeting(self, meeting_id: str) -> None:
        normalized = str(meeting_id or "").strip()
        if not normalized:
            raise ValueError("meeting_id is required")
        with self._lock:
            self._active_meeting_leases.discard(normalized)
            if not self._active_meeting_leases:
                self._schedule_idle_unload_locked()

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

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if event.get("event_type") == "ready":
                self._ready_metadata = dict(event)
                self._ready.set()
            else:
                self._responses.put(dict(event))
        self._responses.put({"event_type": "worker_exit", "returncode": process.poll()})
        self._ready.set()

    def start(self, timeout_s: float = 60.0) -> bool:
        with self._lock:
            if self._process is not None and self._process.poll() is None and self._ready_metadata:
                return True
            self._stop_locked(reason="restart")
            self._ready.clear()
            self._ready_metadata = {}
            self._responses = queue.Queue()
            try:
                self._process = subprocess.Popen(
                    self._command(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=_offline_environment(),
                )
            except OSError:
                self._process = None
                return False
            self._last_stop_reason = None
            self.process_start_count += 1
            self._reader = threading.Thread(
                target=self._read_stdout,
                args=(self._process,),
                daemon=True,
                name="funasr-offline-refiner-reader",
            )
            self._reader.start()
        if not self._ready.wait(timeout_s):
            with self._lock:
                self._stop_locked(
                    reason="worker_start_timeout",
                    graceful_timeout_s=min(0.05, max(0.0, timeout_s)),
                )
            return False
        with self._lock:
            ready = bool(
                self._process is not None
                and self._process.poll() is None
                and self._ready_metadata
            )
            if ready:
                self._schedule_idle_unload_locked()
            return ready

    def refine(self, pcm16: bytes, *, timeout_s: float) -> RefinementResult:
        with self._lock:
            request_budget = max(0.01, float(timeout_s))
            deadline = time.monotonic() + request_budget
            try:
                configured_startup_timeout = float(
                    os.environ.get("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS")
                    or 60.0
                )
            except (TypeError, ValueError):
                configured_startup_timeout = 60.0
            startup_timeout = min(
                max(0.01, configured_startup_timeout),
                request_budget,
            )
            if not self.start(timeout_s=startup_timeout):
                return RefinementResult(text="", status="failed", reason="offline_worker_not_ready")
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
                try:
                    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                except (OSError, ValueError):
                    self._stop_locked(reason="worker_write_failed")
                    return RefinementResult(text="", status="failed", reason="offline_worker_write_failed")
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stop_locked(reason="worker_timeout")
                        return RefinementResult(text="", status="failed", reason="offline_worker_timeout")
                    try:
                        response = self._responses.get(timeout=remaining)
                    except queue.Empty:
                        self._stop_locked(reason="worker_timeout")
                        return RefinementResult(text="", status="failed", reason="offline_worker_timeout")
                    if response.get("event_type") == "worker_exit":
                        self._stop_locked(reason="worker_exit")
                        return RefinementResult(text="", status="failed", reason="offline_worker_exited")
                    if response.get("request_id") != request_id:
                        continue
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

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            idle_seconds = self._idle_unload_seconds()
            idle_age = (
                max(0.0, time.monotonic() - self._last_activity_monotonic)
                if self._last_activity_monotonic is not None
                else None
            )
            return {
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
                "idle_unload_blocked_by_active_meeting": bool(
                    self._active_meeting_leases
                ),
                "last_stop_reason": self._last_stop_reason,
                **self._ready_metadata,
            }

    def _stop_locked(
        self,
        *,
        reason: str,
        graceful_timeout_s: float = 2.0,
    ) -> None:
        self._cancel_idle_timer_locked()
        process = self._process
        self._process = None
        self._ready_metadata = {}
        self._last_stop_reason = reason
        if process is None:
            return
        if process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=max(0.0, graceful_timeout_s))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=min(0.5, max(0.0, graceful_timeout_s)))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)

    def shutdown(self) -> None:
        with self._lock:
            self._stop_locked(reason="shutdown")


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
            _REFINER = _ResidentOfflineRefiner(typed_components)  # type: ignore[arg-type]
        return _REFINER


def prewarm_refiner_worker() -> bool:
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
    return _get_resident_refiner().start(timeout_s=timeout_s)


def retain_refiner_for_meeting(meeting_id: str) -> bool:
    """Hold the resident model for the lifetime of a live meeting stream."""

    if not realtime_refiner_policy()["realtime_refinement_enabled"]:
        return False
    if refinement_capability()["status"] != "ready":
        return False
    try:
        resident = _get_resident_refiner()
    except RuntimeError:
        return False
    resident.retain_for_meeting(meeting_id)
    return True


def release_refiner_for_meeting(meeting_id: str) -> None:
    """Release a meeting lease without creating or starting a worker."""

    with _REFINER_LOCK:
        resident = _REFINER
    if resident is not None:
        resident.release_for_meeting(meeting_id)


def refiner_worker_status() -> dict[str, Any]:
    policy = realtime_refiner_policy()
    with _REFINER_LOCK:
        worker = _REFINER
    status = worker.status() if worker is not None else {
        "spawned": False,
        "process_running": False,
        "process_ready": False,
        "pid": None,
        "process_start_count": 0,
        "completed_request_count": 0,
        "idle_unload_seconds": _ResidentOfflineRefiner._idle_unload_seconds(),
        "idle_age_seconds": None,
        "idle_unload_scheduled": False,
        "idle_unload_count": 0,
        "active_meeting_lease_count": 0,
        "idle_unload_blocked_by_active_meeting": False,
        "last_stop_reason": None,
    }
    return {**status, "realtime_policy": policy}


def shutdown_refiner_worker() -> None:
    global _REFINER
    with _REFINER_LOCK:
        worker = _REFINER
        _REFINER = None
    if worker is not None:
        worker.shutdown()


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
    return _get_resident_refiner().refine(pcm16, timeout_s=timeout_s)


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


def refine_wav_file(audio_path: Path, *, timeout_s: float = 180.0) -> RefinementResult:
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
            pcm16 = wav_file.readframes(wav_file.getnframes())
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
    resident = _get_resident_refiner()
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
        result = resident.refine(chunk, timeout_s=remaining)
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
                reason=f"offline_chunk_{index}:{result.reason or result.status}",
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
