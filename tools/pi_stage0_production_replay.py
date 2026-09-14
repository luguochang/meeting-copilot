#!/usr/bin/env python3
"""Replay a controlled WAV through the production ASR and Pi coach path.

The source service must already be running. The tool creates a durable V2
meeting, acknowledges the recording notice, streams the WAV through the same
WebSocket used by the live microphone UI, waits for durable realtime
intelligence, ends the meeting, and writes a redacted evidence bundle.
"""

from __future__ import annotations

from array import array
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
from urllib.parse import quote, urlencode, urlsplit
import wave

import websocket


SAMPLE_RATE_HZ = 16_000
SAMPLE_WIDTH_BYTES = 2
INTELLIGENCE_REALTIME_BUDGET_MS = 10_000
E2E_LATENCY_P50_LIMIT_MS = 2_500.0
E2E_LATENCY_P95_LIMIT_MS = 5_000.0
E2E_LATENCY_MAX_LIMIT_MS = 10_000.0
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE_JOB_STATUSES = frozenset({"pending", "running", "retry_wait"})
SUCCESSFUL_COACH_STATUSES = frozenset({"intervention", "protected_silent", "not_triggered", "silent"})
REFINER_POLICY_ONLINE_ONLY = "online_only"
REFINER_POLICY_PREWARM = "prewarm"
ONLINE_ONLY_REFINEMENT_REASON = "offline_refinement_bypassed_by_resource_policy"
ONLINE_ONLY_FINAL_SOURCE = "local_realtime_online_final"
REQUIRED_ARTIFACTS = (
    "asr-ws-events.jsonl",
    "events.jsonl",
    "decisions.jsonl",
    "jobs.jsonl",
    "meeting-snapshot.json",
    "transcript.json",
    "traces.json",
    "metrics.json",
    "notes.md",
    "acceptance-evidence.json",
)
MEETING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SECRET_KEY_RE = re.compile(
    r"(?i)^(?:api[_-]?key|authorization|bearer|secret|password|cookie|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token)$"
)
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+\S+"),
    re.compile(r"(?i)\b(?:sk|rk)-[A-Za-z0-9._-]{6,}"),
)
RELEASE_INCIDENT_FIXTURE_SHA256 = (
    "b50b0511d8a35a5a29cf721cd4546a670639362c8fb40fd17deb9529a682534b"
)
RELEASE_INCIDENT_QUALITY_CONTRACT_ID = "release_incident_55s.v1"
RELEASE_INCIDENT_MIN_ANCHOR_COVERAGE = 0.75
RELEASE_INCIDENT_REQUIRED_ANCHORS = frozenset({"monitor_threshold_owner"})
RELEASE_INCIDENT_ANCHORS = (
    ("rollout_plan", (("周五", "灰度"),)),
    ("monitoring_metric", (("error",), ("错误率",), ("p99",), ("p九九",))),
    ("rollback_readiness", (("回滚", "脚本"),)),
    ("payment_retry_test", (("支付", "重试"),)),
    ("queue_lag_incident", (("lag", "堆积"), ("消费", "堆积"))),
    ("alert_delay", (("告警", "延迟"),)),
    ("review_schedule", (("复盘", "下周一"),)),
    (
        "monitor_threshold_owner",
        (
            ("监控", "阈值", "谁"),
            ("监控", "阈值", "没定"),
            ("监控", "阈值", "未定"),
        ),
    ),
)


class ReplayFailure(RuntimeError):
    """A classified failure that should appear in the evidence manifest."""

    def __init__(
        self,
        layer: str,
        message: str,
        *,
        transport_stats: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = layer
        # A transport failure is still useful evidence. Keep the snapshot on
        # the classified exception so run_replay can persist it even when the
        # normal stream_wav return path is never reached.
        self.transport_stats = dict(transport_stats or {})


@dataclass(frozen=True)
class WavInfo:
    filename: str
    sha256: str
    size_bytes: int
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float
    compression: str


@dataclass(frozen=True)
class HttpResult:
    status: int
    payload: dict[str, Any]
    duration_ms: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_ms() -> int:
    return time.time_ns() // 1_000_000


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_wav(path: Path) -> WavInfo:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ReplayFailure("fixture_validation", f"WAV does not exist: {path}")
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            compression = handle.getcomptype()
    except (OSError, EOFError, wave.Error) as exc:
        raise ReplayFailure("fixture_validation", "WAV header is invalid") from exc
    if channels != 1:
        raise ReplayFailure("fixture_validation", "WAV must be mono")
    if sample_width != SAMPLE_WIDTH_BYTES:
        raise ReplayFailure("fixture_validation", "WAV must contain signed 16-bit PCM")
    if sample_rate != SAMPLE_RATE_HZ:
        raise ReplayFailure("fixture_validation", f"WAV must be {SAMPLE_RATE_HZ} Hz")
    if compression != "NONE":
        raise ReplayFailure("fixture_validation", "WAV must contain uncompressed PCM")
    if frame_count <= 0:
        raise ReplayFailure("fixture_validation", "WAV contains no audio frames")
    return WavInfo(
        filename=path.name,
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        sample_rate_hz=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_seconds=frame_count / sample_rate,
        compression="PCM_S16LE",
    )


def pcm16le_to_float32le(payload: bytes) -> bytes:
    if len(payload) % SAMPLE_WIDTH_BYTES:
        raise ValueError("PCM16 payload must contain complete samples")
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    normalized = array("f", (sample / 32768.0 for sample in samples))
    if sys.byteorder != "little":
        normalized.byteswap()
    return normalized.tobytes()


def iter_float32le_chunks(
    path: Path,
    *,
    chunk_frames: int,
) -> Iterable[tuple[bytes, int]]:
    if chunk_frames <= 0:
        raise ValueError("chunk_frames must be positive")
    with wave.open(str(path), "rb") as handle:
        while True:
            pcm16 = handle.readframes(chunk_frames)
            if not pcm16:
                return
            actual_frames = len(pcm16) // SAMPLE_WIDTH_BYTES
            yield pcm16le_to_float32le(pcm16), actual_frames


def _sanitize_text(value: str, secret_values: Sequence[str]) -> str:
    sanitized = value
    for secret in secret_values:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    for pattern in SECRET_TEXT_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def sanitize(value: Any, *, secret_values: Sequence[str] = ()) -> Any:
    """Remove credential-shaped fields and known secret values recursively."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            result[key] = (
                "[REDACTED]" if SECRET_KEY_RE.fullmatch(key) else sanitize(raw_value, secret_values=secret_values)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secret_values=secret_values) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, secret_values)
    return value


class JsonHttpClient:
    """Small same-origin JSON client with optional local API authentication."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        parsed = urlsplit(str(base_url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ReplayFailure("argument_validation", "--base-url must be an HTTP(S) URL")
        try:
            loopback_host = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback_host = parsed.hostname.lower() == "localhost"
        if not loopback_host:
            raise ReplayFailure(
                "argument_validation",
                "--base-url must target the local source service on a loopback host",
            )
        if parsed.username is not None or parsed.password is not None:
            raise ReplayFailure("argument_validation", "--base-url must not contain userinfo")
        if parsed.query or parsed.fragment:
            raise ReplayFailure("argument_validation", "--base-url must not contain query or fragment")
        try:
            parsed.port
        except ValueError as exc:
            raise ReplayFailure("argument_validation", "--base-url contains an invalid port") from exc
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.netloc = parsed.netloc
        self.path_prefix = parsed.path.rstrip("/")
        self.base_url = f"{self.scheme}://{self.netloc}{self.path_prefix}"
        self.origin = f"{self.scheme}://{self.netloc}"
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.token = str(token or "").strip()
        self.request_log: list[dict[str, Any]] = []
        self.cookie = (
            "meeting_copilot_session="
            + hmac.new(
                self.token.encode("utf-8"),
                b"meeting-copilot-session-v1",
                hashlib.sha256,
            ).hexdigest()
            if self.token
            else None
        )

    def _target(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("HTTP path must start with /")
        return f"{self.path_prefix}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResult:
        connection_type = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(
            self.host,
            self.port,
            timeout=max(1.0, float(timeout_seconds or self.timeout_seconds)),
        )
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {
            "Origin": self.origin,
            "Accept": "application/json",
            # The replay harness is restricted to a loopback source service;
            # this header gates local-only operational helpers such as the
            # offline refiner prewarm endpoint.
            "X-Meeting-Copilot-Verification": "1",
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Meeting-Copilot-Token"] = self.token
        if self.cookie:
            headers["Cookie"] = self.cookie
        started = time.monotonic()
        status = 0
        try:
            connection.request(method.upper(), self._target(path), body=encoded, headers=headers)
            response = connection.getresponse()
            status = int(response.status)
            raw = response.read(64 * 1024 * 1024)
        except (OSError, http.client.HTTPException) as exc:
            duration_ms = round((time.monotonic() - started) * 1_000, 2)
            self.request_log.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "status": None,
                    "duration_ms": duration_ms,
                    "error_class": type(exc).__name__,
                }
            )
            raise ReplayFailure("http_transport", f"{method.upper()} {path} failed") from exc
        finally:
            connection.close()
        duration_ms = round((time.monotonic() - started) * 1_000, 2)
        self.request_log.append(
            {
                "method": method.upper(),
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
            }
        )
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplayFailure(
                "http_protocol",
                f"{method.upper()} {path} returned non-JSON status {status}",
            ) from exc
        if not isinstance(decoded, dict):
            raise ReplayFailure("http_protocol", f"{method.upper()} {path} did not return an object")
        return HttpResult(status=status, payload=decoded, duration_ms=duration_ms)

    def websocket_url(self, path: str, query: Mapping[str, Any]) -> str:
        scheme = "wss" if self.scheme == "https" else "ws"
        return f"{scheme}://{self.netloc}{self._target(path)}?{urlencode(query)}"


def _expect(result: HttpResult, statuses: set[int], *, layer: str, action: str) -> dict[str, Any]:
    if result.status not in statuses:
        detail = sanitize(result.payload.get("detail") or result.payload)
        encoded = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))[:600]
        raise ReplayFailure(layer, f"{action} returned HTTP {result.status}: {encoded}")
    return result.payload


def prewarm_refiner_if_needed(client: JsonHttpClient) -> dict[str, Any]:
    """Restore a declared ``prewarm`` refiner contract after idle unload.

    This is runtime preparation only. The strict ``validate_runtime`` checks
    still run afterward and remain the source of truth for replay eligibility.
    """

    runtime = _expect(
        client.request("GET", "/providers/asr/runtime"),
        {200},
        layer="runtime_validation",
        action="ASR prewarm status check",
    )
    offline = runtime.get("offline_refinement") if isinstance(runtime.get("offline_refinement"), Mapping) else {}
    capability = offline.get("capability") if isinstance(offline.get("capability"), Mapping) else {}
    policy = capability.get("realtime_policy") if isinstance(capability.get("realtime_policy"), Mapping) else {}
    mode = str(policy.get("mode") or "").strip().casefold()
    worker = offline.get("worker") if isinstance(offline.get("worker"), Mapping) else {}
    healthy = all(bool(worker.get(field)) for field in ("spawned", "process_running", "process_ready"))
    if mode != REFINER_POLICY_PREWARM or healthy:
        return {
            "attempted": False,
            "mode": mode or None,
            "reason": "already_ready" if healthy else "policy_does_not_require_prewarm",
        }
    result = _expect(
        client.request("POST", "/providers/asr/prewarm", timeout_seconds=90.0),
        {200},
        layer="runtime_validation",
        action="ASR refiner prewarm",
    )
    return {
        "attempted": True,
        "mode": mode,
        "started": bool(result.get("started")),
        "worker_ready": bool(
            isinstance(result.get("worker"), Mapping)
            and all(bool(result["worker"].get(field)) for field in ("spawned", "process_running", "process_ready"))
        ),
    }


def meeting_preparation_payload(wav_info: WavInfo) -> dict[str, Any]:
    return {
        "hotwords": ["P99", "SLO", "灰度", "回滚", "压测", "安全测试"],
        "input_source": "microphone",
        "input_device_id": "controlled-wav-replay",
        "input_device_name": wav_info.filename,
        "notice_acknowledged": True,
        "preset_id": "project",
        "meeting_goal": "确认本周五发布是否满足压测、安全测试、负责人和回滚条件",
        "participant_role": "发布负责人",
        "focus_points": ["上线条件", "P99", "风险", "负责人", "下一步"],
        "output_format": "action_plan",
        "proactive_suggestion_policy": "standard",
    }


def validate_runtime(
    client: JsonHttpClient,
    *,
    allow_mock_llm: bool,
) -> dict[str, Any]:
    health = _expect(
        client.request("GET", "/health"),
        {200},
        layer="runtime_validation",
        action="service health check",
    )
    provider_health = _expect(
        client.request("GET", "/providers/health"),
        {200},
        layer="runtime_validation",
        action="provider health check",
    )
    asr_runtime = _expect(
        client.request("GET", "/providers/asr/runtime"),
        {200},
        layer="runtime_validation",
        action="ASR runtime check",
    )
    settings = _expect(
        client.request("GET", "/settings"),
        {200},
        layer="runtime_validation",
        action="runtime settings check",
    )
    if health.get("status") != "ok":
        raise ReplayFailure("runtime_validation", "source service is not healthy")
    llm = provider_health.get("llm") if isinstance(provider_health.get("llm"), Mapping) else {}
    asr = provider_health.get("asr") if isinstance(provider_health.get("asr"), Mapping) else {}
    if not bool(llm.get("configured") or llm.get("credential_configured")):
        raise ReplayFailure("runtime_validation", "LLM provider is not configured")
    if bool(llm.get("is_mock")) and not allow_mock_llm:
        raise ReplayFailure("runtime_validation", "mock LLM is not accepted by production replay")
    realtime_model = str(llm.get("realtime_model") or "").strip()
    realtime_model_source = str(llm.get("realtime_model_source") or "").strip()
    if not bool(llm.get("realtime_model_explicit")):
        raise ReplayFailure(
            "runtime_validation",
            "realtime LLM model must be configured explicitly for Stage 0 replay",
        )
    if not realtime_model or realtime_model in {"not_called", "not_configured"}:
        raise ReplayFailure(
            "runtime_validation",
            "explicit realtime LLM model is missing from provider provenance",
        )
    if not realtime_model_source or realtime_model_source == "general_model_fallback":
        raise ReplayFailure(
            "runtime_validation",
            "realtime LLM model provenance still reports general-model fallback",
        )
    if not bool(asr.get("realtime_asr_available")):
        raise ReplayFailure("runtime_validation", "realtime ASR provider is unavailable")
    if not bool(asr_runtime.get("realtime_available")):
        raise ReplayFailure("runtime_validation", "ASR runtime reports realtime_available=false")
    asr_settings = settings.get("asr") if isinstance(settings.get("asr"), Mapping) else {}
    if asr_settings.get("l2_correction_enabled") is not True:
        raise ReplayFailure(
            "runtime_validation",
            "L2 transcript correction must be enabled for Stage 0 replay",
        )

    # The realtime resident worker is required in every replay mode. The
    # capability flags only prove that files/configuration were discovered and
    # must not be used as a substitute for a live process.
    if not bool(asr_runtime.get("resident_enabled")):
        raise ReplayFailure("runtime_validation", "realtime ASR resident worker is disabled")
    resident = asr_runtime.get("resident") if isinstance(asr_runtime.get("resident"), Mapping) else {}
    resident_failures = [
        field
        for field in ("spawned", "process_running", "process_ready")
        if not bool(resident.get(field))
    ]
    if not isinstance(resident.get("pid"), int) or int(resident.get("pid") or 0) <= 0:
        resident_failures.append("pid")
    if not isinstance(resident.get("process_start_count"), int) or int(
        resident.get("process_start_count") or 0
    ) <= 0:
        resident_failures.append("process_start_count")
    if resident.get("last_exit_code") is not None:
        resident_failures.append("last_exit_code")
    if resident.get("last_error"):
        resident_failures.append("last_error")
    if resident_failures:
        raise ReplayFailure(
            "runtime_validation",
            "realtime ASR resident worker is not healthy: " + ", ".join(resident_failures),
        )

    offline_refinement = (
        asr_runtime.get("offline_refinement")
        if isinstance(asr_runtime.get("offline_refinement"), Mapping)
        else {}
    )
    refinement_capability = (
        offline_refinement.get("capability")
        if isinstance(offline_refinement.get("capability"), Mapping)
        else {}
    )
    refiner_policy = (
        refinement_capability.get("realtime_policy")
        if isinstance(refinement_capability.get("realtime_policy"), Mapping)
        else {}
    )
    policy_mode = str(refiner_policy.get("mode") or "").strip().casefold()
    if policy_mode not in {REFINER_POLICY_ONLINE_ONLY, REFINER_POLICY_PREWARM}:
        raise ReplayFailure(
            "runtime_validation",
            "offline ASR refiner policy must be explicit online_only or prewarm",
        )
    policy_contract_failures = [
        field
        for field in ("schema_version", "source", "mode")
        if not str(refiner_policy.get(field) or "").strip()
    ]
    if policy_mode == REFINER_POLICY_ONLINE_ONLY:
        if refiner_policy.get("schema_version") != "realtime_refiner_policy.v1":
            policy_contract_failures.append("schema_version")
        if refiner_policy.get("realtime_refinement_enabled") is not False:
            policy_contract_failures.append("realtime_refinement_enabled")
        if refiner_policy.get("prewarm_enabled") is not False:
            policy_contract_failures.append("prewarm_enabled")
        if refiner_policy.get("degradation_reason") != ONLINE_ONLY_REFINEMENT_REASON:
            policy_contract_failures.append("degradation_reason")
    if policy_contract_failures:
        raise ReplayFailure(
            "runtime_validation",
            "offline ASR refiner policy contract is incomplete: "
            + ", ".join(dict.fromkeys(policy_contract_failures)),
        )
    refiner = (
        offline_refinement.get("worker")
        if isinstance(offline_refinement.get("worker"), Mapping)
        else {}
    )
    if policy_mode == REFINER_POLICY_ONLINE_ONLY:
        # online_only is the default resource guard: no large offline worker
        # may be spawned merely because its files are installed. Treat any
        # resident process or non-zero start count as a contract violation.
        refiner_failures = [
            field
            for field in ("spawned", "process_running", "process_ready")
            if bool(refiner.get(field))
        ]
        if refiner.get("pid") is not None:
            refiner_failures.append("pid")
        if refiner.get("process_start_count") != 0:
            refiner_failures.append("process_start_count")
    else:
        # Explicit prewarm keeps the original strict process contract.
        if refinement_capability.get("status") != "ready" or not bool(
            refinement_capability.get("process_resident")
        ):
            raise ReplayFailure(
                "runtime_validation",
                "offline ASR refiner capability is not resident and ready",
            )
        refiner_failures = [
            field
            for field in ("spawned", "process_running", "process_ready")
            if not bool(refiner.get(field))
        ]
        if not isinstance(refiner.get("pid"), int) or int(refiner.get("pid") or 0) <= 0:
            refiner_failures.append("pid")
        if not isinstance(refiner.get("process_start_count"), int) or int(
            refiner.get("process_start_count") or 0
        ) <= 0:
            refiner_failures.append("process_start_count")
    if refiner_failures:
        raise ReplayFailure(
            "runtime_validation",
            (
                "offline ASR refiner worker violates online_only policy: "
                if policy_mode == REFINER_POLICY_ONLINE_ONLY
                else "offline ASR refiner worker is not healthy: "
            )
            + ", ".join(dict.fromkeys(refiner_failures)),
        )
    return {
        "validated": True,
        "health": health,
        "provider_health": provider_health,
        "realtime_model": {
            "general_model": str(llm.get("model") or "") or None,
            "selected_model": realtime_model,
            "source": realtime_model_source,
            "explicit": True,
            "warning": llm.get("realtime_model_warning"),
        },
        "settings": {
            "l2_correction_enabled": True,
            "l3_normalize_enabled": asr_settings.get("l3_normalize_enabled"),
        },
        "asr_runtime": asr_runtime,
        "refiner_policy": {
            "mode": policy_mode,
            "schema_version": refiner_policy.get("schema_version"),
            "source": refiner_policy.get("source"),
            "degradation_reason": refiner_policy.get("degradation_reason"),
        },
    }


def stream_wav(
    client: JsonHttpClient,
    *,
    meeting_id: str,
    wav_path: Path,
    wav_info: WavInfo,
    pace: float,
    chunk_seconds: float,
    tail_silence_seconds: float,
    ready_timeout_seconds: float,
    finalize_timeout_seconds: float,
    event_sink: list[dict[str, Any]],
    audio_source: str = "browser_live_mic",
) -> dict[str, Any]:
    """Stream F32LE frames through the production ASR socket.

    ``browser_live_mic`` is retained for the hardware-style transport test,
    while the CLI's default is ``simulated_realtime_wav`` so a fixture cannot
    be mistaken for proof of a physical microphone path.
    """

    if audio_source not in {"browser_live_mic", "simulated_realtime_wav", "speaker_loopback"}:
        raise ReplayFailure("argument_validation", "unsupported --audio-source")

    chunk_frames = max(1, round(SAMPLE_RATE_HZ * chunk_seconds))
    expected_duration_seconds = max(
        90,
        math.ceil(wav_info.duration_seconds + tail_silence_seconds + 10),
    )
    ws_url = client.websocket_url(
        f"/live/asr/stream/ws/{quote(meeting_id, safe='')}",
        {
            "audio_source": audio_source,
            "expected_duration_seconds": expected_duration_seconds,
        },
    )
    try:
        ws = websocket.create_connection(
            ws_url,
            timeout=max(1.0, ready_timeout_seconds),
            origin=client.origin,
            cookie=client.cookie,
        )
    except Exception as exc:
        raise ReplayFailure("asr_connect", "failed to open production ASR WebSocket") from exc

    ready = False
    closed = False
    eos_observed = False
    rejected_event: dict[str, Any] | None = None
    final_events: list[dict[str, Any]] = []
    event_counts: Counter[str] = Counter()
    stream_started = time.monotonic()
    stream_started_at_ms = _epoch_ms()
    ready_at: float | None = None
    last_event_at = stream_started
    end_sent_at_ms: int | None = None
    sent_audio_frames = 0
    sent_silence_frames = 0
    sent_audio_frames_before_ready = 0
    sent_silence_frames_before_ready = 0
    sent_audio_chunk_count = 0
    sent_silence_chunk_count = 0
    end_send_attempted = False
    end_send_succeeded = False
    end_send_error_class: str | None = None
    end_send_error_message: str | None = None
    receive_error_class: str | None = None
    receive_error_message: str | None = None
    asr_shutdown_diagnostics: dict[str, Any] | None = None
    close_code: int | None = None
    close_reason: str | None = None
    close_received_at_ms: int | None = None
    termination = "unknown"

    def transport_stats() -> dict[str, Any]:
        """Return a redacted, failure-safe snapshot of the socket exchange."""

        return {
            "audio_source": audio_source,
            "audio_provenance": (
                "physical_browser_microphone"
                if audio_source == "browser_live_mic"
                else "controlled_wav_fixture"
                if audio_source == "simulated_realtime_wav"
                else "speaker_loopback_fixture"
            ),
            "ready": ready,
            "ready_wait_ms": (
                round((ready_at - stream_started) * 1_000, 2)
                if ready_at is not None
                else None
            ),
            "stream_started_at_ms": stream_started_at_ms,
            "end_send_attempted": end_send_attempted,
            "end_send_succeeded": end_send_succeeded,
            "end_sent_at_ms": end_sent_at_ms,
            "end_send_error_class": end_send_error_class,
            "end_send_error_message": end_send_error_message,
            "stream_wall_ms": round((time.monotonic() - stream_started) * 1_000, 2),
            "sent_audio_frames": sent_audio_frames,
            "sent_audio_seconds": round(sent_audio_frames / SAMPLE_RATE_HZ, 6),
            "sent_audio_chunk_count": sent_audio_chunk_count,
            "sent_audio_frames_before_ready": sent_audio_frames_before_ready,
            "sent_audio_seconds_before_ready": round(
                sent_audio_frames_before_ready / SAMPLE_RATE_HZ,
                6,
            ),
            "sent_silence_frames": sent_silence_frames,
            "sent_silence_seconds": round(sent_silence_frames / SAMPLE_RATE_HZ, 6),
            "sent_silence_chunk_count": sent_silence_chunk_count,
            "sent_tail_silence_frames": sent_silence_frames,
            "sent_tail_silence_seconds": round(sent_silence_frames / SAMPLE_RATE_HZ, 6),
            "sent_tail_silence_frames_before_ready": sent_silence_frames_before_ready,
            "sent_tail_silence_seconds_before_ready": round(
                sent_silence_frames_before_ready / SAMPLE_RATE_HZ,
                6,
            ),
            "sent_binary_frame_count": sent_audio_chunk_count + sent_silence_chunk_count,
            "sent_text_frame_count": int(end_send_succeeded),
            "chunk_frames": chunk_frames,
            "chunk_seconds": chunk_seconds,
            "pace": pace,
            "termination": termination,
            "eos_observed": eos_observed,
            "socket_closed_by_server": closed,
            "socket_close_code": close_code,
            "socket_close_reason": close_reason,
            "socket_close_received_at_ms": close_received_at_ms,
            "receive_error_class": receive_error_class,
            "receive_error_message": receive_error_message,
            "event_counts": dict(sorted(event_counts.items())),
            "partial_count": int(event_counts.get("partial", 0)),
            "final_count": int(event_counts.get("final", 0)),
            "non_empty_final_count": len(final_events),
            "first_final_received_at_ms": (
                final_events[0].get("replay_received_at_ms") if final_events else None
            ),
            "last_final_received_at_ms": (
                final_events[-1].get("replay_received_at_ms") if final_events else None
            ),
            "non_empty_final_texts": [
                str(event.get("normalized_text") or event.get("text") or "")
                for event in final_events
            ],
            "final_sources": sorted(
                {
                    str(event.get("final_source") or "").strip()
                    for event in final_events
                    if str(event.get("final_source") or "").strip()
                }
            ),
            "asr_shutdown_diagnostics": asr_shutdown_diagnostics,
        }

    def receive_available(timeout_seconds: float) -> None:
        nonlocal ready, closed, eos_observed, rejected_event, last_event_at, ready_at
        nonlocal close_code, close_reason, close_received_at_ms
        nonlocal receive_error_class, receive_error_message
        nonlocal asr_shutdown_diagnostics
        ws.settimeout(max(0.01, timeout_seconds))
        while True:
            frame_opcode: int | None = None
            try:
                # websocket-client's recv() discards close-frame metadata.
                # Prefer recv_data() when available so a failed replay records
                # the server's close code/reason; the fallback keeps the unit
                # test seam and alternate WebSocket clients compatible.
                recv_data = getattr(ws, "recv_data", None)
                if callable(recv_data):
                    opcode, raw = recv_data()
                    frame_opcode = int(opcode)
                    if frame_opcode == 8:  # websocket ABNF close opcode
                        closed = True
                        close_received_at_ms = _epoch_ms()
                        close_payload = bytes(raw or b"")
                        if len(close_payload) >= 2:
                            close_code = int.from_bytes(close_payload[:2], "big")
                            close_reason = close_payload[2:].decode("utf-8", errors="replace")
                        return
                else:
                    raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                return
            except (websocket.WebSocketConnectionClosedException, OSError) as exc:
                closed = True
                receive_error_class = type(exc).__name__
                receive_error_message = str(exc)[:300]
                return
            if raw in {None, ""}:
                closed = True
                close_received_at_ms = close_received_at_ms or _epoch_ms()
                return
            received_at_ms = _epoch_ms()
            # ``websocket-client.recv_data`` returns UTF-8 text-frame payloads
            # as bytes. The opcode, rather than the Python value type, is the
            # authoritative discriminator between JSON text and audio/binary.
            if frame_opcode == 1 and isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw).decode("utf-8", errors="replace")
            if frame_opcode == 2 or (frame_opcode is None and isinstance(raw, bytes)):
                event: dict[str, Any] = {
                    "event_type": "unexpected_binary",
                    "size_bytes": len(raw),
                }
            else:
                try:
                    decoded = json.loads(raw)
                    event = (
                        decoded
                        if isinstance(decoded, dict)
                        else {
                            "event_type": "unparsed",
                            "raw": raw,
                        }
                    )
                except json.JSONDecodeError:
                    event = {"event_type": "unparsed", "raw": raw}
            event = {
                **event,
                "replay_received_at_ms": received_at_ms,
                "replay_received_offset_ms": round(
                    (time.monotonic() - stream_started) * 1_000,
                    2,
                ),
            }
            event_sink.append(event)
            event_type = str(event.get("event_type") or "unknown")
            event_counts[event_type] += 1
            last_event_at = time.monotonic()
            if event_type == "asr_ready" and event.get("ready") is True:
                ready = True
                ready_at = ready_at or time.monotonic()
            if event_type == "final" and str(event.get("normalized_text") or event.get("text") or "").strip():
                final_events.append(event)
            if event_type == "end_of_stream":
                eos_observed = True
                diagnostics = event.get("asr_shutdown_diagnostics")
                if isinstance(diagnostics, dict):
                    # The server applies a content-free whitelist; keep the
                    # replay report defensive and bounded as well.
                    asr_shutdown_diagnostics = sanitize(diagnostics)
            if event_type in {"provider_error", "recording_rejected", "error"}:
                rejected_event = event

    ready_deadline = stream_started + ready_timeout_seconds

    def require_ready(*, layer: str) -> None:
        while not ready and not closed and rejected_event is None:
            remaining = ready_deadline - time.monotonic()
            if remaining <= 0:
                break
            receive_available(min(0.5, max(0.01, remaining)))
        if rejected_event is not None:
            code = str(rejected_event.get("error_code") or rejected_event.get("message") or "unknown")
            raise ReplayFailure(layer, f"ASR rejected the stream: {code}")
        if not ready:
            raise ReplayFailure(layer, "ASR did not become ready before the timeout")

    try:
        # The production server persists and buffers PCM while the resident ASR
        # model warms. Send immediately so cold-start readiness cannot deadlock
        # with a client that waits for ready before providing the first frame.
        pacing_started = time.monotonic()

        for float32_chunk, actual_frames in iter_float32le_chunks(
            wav_path,
            chunk_frames=chunk_frames,
        ):
            try:
                ws.send_binary(float32_chunk)
            except (websocket.WebSocketConnectionClosedException, OSError) as exc:
                receive_error_class = type(exc).__name__
                receive_error_message = str(exc)[:300]
                raise ReplayFailure(
                    "asr_stream",
                    "ASR WebSocket closed while sending audio",
                    transport_stats=transport_stats(),
                ) from exc
            sent_audio_frames += actual_frames
            sent_audio_chunk_count += 1
            if not ready:
                sent_audio_frames_before_ready += actual_frames
            receive_available(0.02)
            if rejected_event is not None:
                code = str(rejected_event.get("error_code") or rejected_event.get("message") or "unknown")
                raise ReplayFailure("asr_stream", f"ASR failed while streaming: {code}")
            if not ready and time.monotonic() >= ready_deadline:
                require_ready(layer="asr_ready")
            target = pacing_started + (sent_audio_frames / SAMPLE_RATE_HZ) / pace
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        total_silence_frames = max(0, round(SAMPLE_RATE_HZ * tail_silence_seconds))
        zero_chunk = b"\x00" * (chunk_frames * 4)
        while sent_silence_frames < total_silence_frames:
            frame_count = min(chunk_frames, total_silence_frames - sent_silence_frames)
            try:
                ws.send_binary(zero_chunk[: frame_count * 4])
            except (websocket.WebSocketConnectionClosedException, OSError) as exc:
                receive_error_class = type(exc).__name__
                receive_error_message = str(exc)[:300]
                raise ReplayFailure(
                    "asr_stream",
                    "ASR WebSocket closed while sending tail silence",
                    transport_stats=transport_stats(),
                ) from exc
            sent_silence_frames += frame_count
            sent_silence_chunk_count += 1
            if not ready:
                sent_silence_frames_before_ready += frame_count
            receive_available(0.02)
            if rejected_event is not None:
                code = str(rejected_event.get("error_code") or rejected_event.get("message") or "unknown")
                raise ReplayFailure("asr_stream", f"ASR failed while streaming: {code}")
            if not ready and time.monotonic() >= ready_deadline:
                require_ready(layer="asr_ready")
            target = pacing_started + ((sent_audio_frames + sent_silence_frames) / SAMPLE_RATE_HZ) / pace
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        if not ready:
            require_ready(layer="asr_ready")

        end_send_attempted = True
        try:
            ws.send("END")
        except (websocket.WebSocketConnectionClosedException, OSError) as exc:
            end_send_error_class = type(exc).__name__
            end_send_error_message = str(exc)[:300]
            raise ReplayFailure(
                "asr_finalize",
                "failed to send the ASR END frame",
                transport_stats=transport_stats(),
            ) from exc
        end_send_succeeded = True
        end_sent_at_ms = _epoch_ms()
        finalize_deadline = time.monotonic() + finalize_timeout_seconds
        while time.monotonic() < finalize_deadline:
            receive_available(min(0.5, max(0.01, finalize_deadline - time.monotonic())))
            if rejected_event is not None:
                code = str(rejected_event.get("error_code") or rejected_event.get("message") or "unknown")
                raise ReplayFailure("asr_finalize", f"ASR finalization failed: {code}")
            if eos_observed:
                termination = "end_of_stream_event"
                break
            if closed:
                termination = "server_close"
                break
        else:
            termination = "finalize_timeout"
        if not final_events:
            raise ReplayFailure(
                "asr_finalize",
                "ASR produced no non-empty final event",
                transport_stats=transport_stats(),
            )
        if termination == "finalize_timeout":
            raise ReplayFailure(
                "asr_finalize",
                "ASR did not reach a terminal condition",
                transport_stats=transport_stats(),
            )
    except ReplayFailure as exc:
        if not exc.transport_stats:
            exc.transport_stats.update(transport_stats())
        raise
    except Exception as exc:
        raise ReplayFailure(
            "asr_stream",
            f"unexpected ASR WebSocket transport failure: {type(exc).__name__}",
            transport_stats=transport_stats(),
        ) from exc
    finally:
        try:
            ws.close()
        except Exception:
            pass

    return transport_stats()


def fetch_all_events(client: JsonHttpClient, meeting_id: str) -> list[dict[str, Any]]:
    cursor = 0
    events: list[dict[str, Any]] = []
    encoded_id = quote(meeting_id, safe="")
    while True:
        result = client.request(
            "GET",
            f"/v2/meetings/{encoded_id}/events?after_seq={cursor}&limit=1000",
        )
        page = _expect(
            result,
            {200},
            layer="evidence_collection",
            action="formal event pagination",
        )
        raw_events = page.get("events")
        if not isinstance(raw_events, list):
            raise ReplayFailure("evidence_collection", "formal event page has no events list")
        events.extend(item for item in raw_events if isinstance(item, dict))
        if not page.get("has_more"):
            return events
        next_cursor = int(page.get("next_after_seq") or 0)
        if next_cursor <= cursor:
            raise ReplayFailure("evidence_collection", "formal event pagination did not advance")
        cursor = next_cursor


def fetch_full_transcript(client: JsonHttpClient, meeting_id: str) -> dict[str, Any]:
    cursor = 0
    segments: list[dict[str, Any]] = []
    page_count = 0
    encoded_id = quote(meeting_id, safe="")
    while True:
        result = client.request(
            "GET",
            f"/v2/meetings/{encoded_id}/transcript?after_transcript_seq={cursor}&limit=1000",
        )
        page = _expect(
            result,
            {200},
            layer="evidence_collection",
            action="transcript pagination",
        )
        page_count += 1
        raw_segments = page.get("segments")
        if not isinstance(raw_segments, list):
            raise ReplayFailure("evidence_collection", "transcript page has no segments list")
        segments.extend(item for item in raw_segments if isinstance(item, dict))
        if not page.get("has_more"):
            return {
                "meeting_id": meeting_id,
                "after_transcript_seq": 0,
                "segments": segments,
                "has_more": False,
                "next_after_transcript_seq": (int(segments[-1].get("transcript_seq") or 0) if segments else 0),
                "page_count": page_count,
            }
        next_cursor = int(page.get("next_after_transcript_seq") or 0)
        if next_cursor <= cursor:
            raise ReplayFailure("evidence_collection", "transcript pagination did not advance")
        cursor = next_cursor


def intelligence_jobs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs = snapshot.get("jobs")
    if not isinstance(jobs, list):
        return []
    return [dict(job) for job in jobs if isinstance(job, Mapping) and str(job.get("kind") or "") == "intelligence"]


def latest_intelligence_job(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    jobs = intelligence_jobs(snapshot)
    return max(
        jobs,
        key=lambda job: (
            int(job.get("created_at_ms") or 0),
            int(job.get("updated_at_ms") or 0),
            str(job.get("id") or ""),
        ),
        default=None,
    )


def _applied_event_for_job(
    events: Sequence[Mapping[str, Any]],
    job_id: str,
) -> dict[str, Any] | None:
    matching = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if (
            event.get("type") == "meeting.intelligence.applied"
            and str(payload.get("job_id") or event.get("causation_id") or "") == job_id
        ):
            matching.append(dict(event))
    return matching[-1] if matching else None


def wait_for_intelligence(
    client: JsonHttpClient,
    *,
    meeting_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    encoded_id = quote(meeting_id, safe="")
    deadline = time.monotonic() + timeout_seconds
    latest_snapshot: dict[str, Any] = {}
    latest_events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        snapshot_result = client.request(
            "GET",
            f"/v2/meetings/{encoded_id}/snapshot?segment_limit=500",
        )
        latest_snapshot = _expect(
            snapshot_result,
            {200},
            layer="intelligence_poll",
            action="meeting snapshot poll",
        )
        jobs = intelligence_jobs(latest_snapshot)
        active = [job for job in jobs if str(job.get("status") or "") in ACTIVE_JOB_STATUSES]
        latest_job = latest_intelligence_job(latest_snapshot)
        if jobs and not active and latest_job is not None:
            latest_events = fetch_all_events(client, meeting_id)
            status = str(latest_job.get("status") or "")
            applied = _applied_event_for_job(latest_events, str(latest_job.get("id") or ""))
            if status != "succeeded":
                raise ReplayFailure(
                    "intelligence_terminal",
                    f"latest intelligence job ended with status={status}",
                )
            if applied is None:
                raise ReplayFailure(
                    "intelligence_evidence",
                    "succeeded intelligence job has no meeting.intelligence.applied event",
                )
            return latest_snapshot, latest_events, latest_job, applied
        time.sleep(max(0.05, poll_interval_seconds))
    latest_job = latest_intelligence_job(latest_snapshot)
    detail = f"latest status={latest_job.get('status')}" if latest_job is not None else "no intelligence job appeared"
    raise ReplayFailure("intelligence_poll", f"intelligence did not settle: {detail}")


def extract_decisions(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "meeting.intelligence.applied":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        decision = payload.get("coach_decision") if isinstance(payload.get("coach_decision"), Mapping) else None
        intervention = (
            payload.get("coach_intervention") if isinstance(payload.get("coach_intervention"), Mapping) else None
        )
        status = str((decision or {}).get("status") or "")
        if intervention is not None:
            outcome = "intervention"
        elif status in {"protected_silent", "not_triggered", "silent"}:
            outcome = "explicit_silence"
        elif status:
            outcome = "coach_failure_or_stale"
        else:
            outcome = "missing_decision"
        decisions.append(
            {
                "seq": event.get("seq"),
                "occurred_at_ms": event.get("occurred_at_ms"),
                "job_id": payload.get("job_id"),
                "batch_id": payload.get("batch_id"),
                "source": payload.get("source"),
                "llm_called": payload.get("llm_called"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "evidence": payload.get("evidence"),
                "outcome": outcome,
                "coach_decision": dict(decision) if decision is not None else None,
                "coach_intervention": dict(intervention) if intervention is not None else None,
                "semantic_follow_up": (
                    dict(payload["semantic_follow_up"])
                    if isinstance(payload.get("semantic_follow_up"), Mapping)
                    else None
                ),
            }
        )
    return decisions


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        normalized = _optional_int(value)
        if normalized is not None:
            return normalized
    return None


def _job_deadline(
    job: Mapping[str, Any],
    applied_decision: Mapping[str, Any] | None,
) -> tuple[int | None, str | None]:
    decision_deadline = _optional_int((applied_decision or {}).get("deadline_at_ms"))
    if decision_deadline is not None:
        return decision_deadline, "coach_decision"
    job_deadline = _optional_int(job.get("deadline_at_ms"))
    if job_deadline is not None:
        return job_deadline, "job"
    created_at_ms = _optional_int(job.get("created_at_ms"))
    if created_at_ms is not None:
        return created_at_ms + INTELLIGENCE_REALTIME_BUDGET_MS, "derived_from_job_created_at"
    return None, None


def _unapplied_terminal_classification(
    job: Mapping[str, Any],
    *,
    deadline_at_ms: int | None = None,
) -> tuple[str, str | None]:
    status = str(job.get("status") or "").strip().lower()
    error_class = str(job.get("error_class") or "").strip()
    normalized_error = error_class.lower()
    if normalized_error in {"deadline_exceeded", "timeouterror", "timed_out", "provider_timeout"}:
        return "timed_out", error_class or "deadline_exceeded"
    if normalized_error in {
        "evidence_superseded",
        "intelligence_validation_stale",
        "reservationchanged",
        "stale",
    }:
        return "stale", error_class or "evidence_superseded"
    if error_class and status == "failed":
        return "failed", error_class
    if error_class and status == "cancelled":
        return "cancelled", error_class
    terminal_at_ms = _first_int(job.get("completed_at_ms"), job.get("updated_at_ms"))
    if (
        deadline_at_ms is not None
        and terminal_at_ms is not None
        and terminal_at_ms >= deadline_at_ms
        and status in TERMINAL_JOB_STATUSES
    ):
        return "timed_out", "deadline_exceeded"
    if status == "failed":
        return "failed", error_class or "job_failed"
    if status == "cancelled":
        return "cancelled", error_class or "cancelled"
    if status == "succeeded":
        return "missing_applied", "missing_applied_event"
    return "active", None


def build_intelligence_job_audit(
    snapshot: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project every intelligence job, including terminal jobs with no applied event."""

    applied_events = {
        str((event.get("payload") or {}).get("job_id") or event.get("causation_id") or ""): event
        for event in events
        if event.get("type") == "meeting.intelligence.applied" and isinstance(event.get("payload"), Mapping)
    }
    records: list[dict[str, Any]] = []
    for job in intelligence_jobs(snapshot):
        job_id = str(job.get("id") or "")
        applied_event = applied_events.get(job_id)
        payload = (
            applied_event.get("payload")
            if isinstance(applied_event, Mapping) and isinstance(applied_event.get("payload"), Mapping)
            else {}
        )
        coach_decision = payload.get("coach_decision") if isinstance(payload.get("coach_decision"), Mapping) else None
        deadline_at_ms, deadline_source = _job_deadline(job, coach_decision)
        completed_at_ms = _optional_int(job.get("completed_at_ms"))
        if completed_at_ms is None and applied_event is not None:
            completed_at_ms = _first_int(
                (coach_decision or {}).get("completed_at_ms"),
                applied_event.get("occurred_at_ms"),
            )
        applied = applied_event is not None
        if applied:
            projection_status = "applied"
            drop_reason = None
        else:
            projection_status, drop_reason = _unapplied_terminal_classification(
                job,
                deadline_at_ms=deadline_at_ms,
            )
        dropped_at_ms = (
            _first_int(completed_at_ms, job.get("updated_at_ms"))
            if not applied and str(job.get("status") or "") in TERMINAL_JOB_STATUSES
            else None
        )
        records.append(
            {
                "schema_version": "meeting_copilot.pi_stage0_intelligence_job_audit.v1",
                "record_type": "intelligence_job",
                "job_id": job_id,
                "kind": "intelligence",
                "job_status": job.get("status"),
                "projection_status": projection_status,
                "applied": applied,
                "applied_event_seq": applied_event.get("seq") if applied_event is not None else None,
                "coach_status": (coach_decision or {}).get("status"),
                "created_at_ms": _optional_int(job.get("created_at_ms")),
                "completed_at_ms": completed_at_ms,
                "dropped_at_ms": dropped_at_ms,
                "drop_reason": drop_reason,
                "error_class": job.get("error_class"),
                "deadline_at_ms": deadline_at_ms,
                "deadline_source": deadline_source,
                "attempts": job.get("attempts"),
                "max_attempts": job.get("max_attempts"),
            }
        )
    return records


def build_decision_audit(
    snapshot: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    job_audit: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keep applied decision fields intact and add lifecycle-only terminal rows."""

    applied_decisions = extract_decisions(events)
    jobs = list(job_audit) if job_audit is not None else build_intelligence_job_audit(snapshot, events)
    jobs_by_id = {str(job.get("job_id") or ""): job for job in jobs}
    audited: list[dict[str, Any]] = []
    applied_job_ids: set[str] = set()
    for decision in applied_decisions:
        job_id = str(decision.get("job_id") or "")
        applied_job_ids.add(job_id)
        job = jobs_by_id.get(job_id, {})
        coach_decision = decision.get("coach_decision") if isinstance(decision.get("coach_decision"), Mapping) else {}
        audited.append(
            {
                **decision,
                "record_type": "applied_decision",
                "job_status": job.get("job_status"),
                "created_at_ms": _first_int(
                    coach_decision.get("created_at_ms"),
                    job.get("created_at_ms"),
                ),
                "completed_at_ms": _first_int(
                    coach_decision.get("completed_at_ms"),
                    job.get("completed_at_ms"),
                    decision.get("occurred_at_ms"),
                ),
                "dropped_at_ms": None,
                "drop_reason": None,
                "error_class": job.get("error_class"),
                "deadline_at_ms": _first_int(
                    coach_decision.get("deadline_at_ms"),
                    job.get("deadline_at_ms"),
                ),
                "deadline_source": job.get("deadline_source"),
            }
        )
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        if job_id in applied_job_ids or job.get("projection_status") == "active":
            continue
        audited.append(
            {
                "seq": None,
                "occurred_at_ms": None,
                "job_id": job_id,
                "batch_id": None,
                "source": "intelligence_job_terminal",
                "llm_called": None,
                "provider": None,
                "model": None,
                "evidence": None,
                "outcome": job.get("projection_status"),
                "coach_decision": None,
                "coach_intervention": None,
                "semantic_follow_up": None,
                "record_type": "terminal_without_applied_decision",
                "job_status": job.get("job_status"),
                "created_at_ms": job.get("created_at_ms"),
                "completed_at_ms": job.get("completed_at_ms"),
                "dropped_at_ms": job.get("dropped_at_ms"),
                "drop_reason": job.get("drop_reason"),
                "error_class": job.get("error_class"),
                "deadline_at_ms": job.get("deadline_at_ms"),
                "deadline_source": job.get("deadline_source"),
            }
        )
    return sorted(
        audited,
        key=lambda item: (
            _first_int(
                item.get("occurred_at_ms"),
                item.get("completed_at_ms"),
                item.get("dropped_at_ms"),
                item.get("created_at_ms"),
            )
            or -1,
            _optional_int(item.get("seq")) or -1,
            str(item.get("job_id") or ""),
        ),
    )


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    """Return a deterministic nearest-rank percentile for a non-empty series."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(float(percentile) * len(ordered))) - 1))
    return round(ordered[index], 2)


def build_e2e_latency_audit(
    snapshot: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Measure durable-final-to-coach-projection latency.

    The decision-layer replay times only the Agent call.  A production user
    waits for the ASR final to be committed, the durable job to become
    runnable, the coach decision to finish, and the applied event to project.
    This audit joins those timestamps without guessing: a missing or
    contradictory timestamp is reported as invalid and never enters the
    latency distribution.
    """

    final_commits: dict[str, int] = {}
    for event in events:
        if str(event.get("type") or "") != "transcript.segment.finalized":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        segment_id = str(payload.get("segment_id") or "").strip()
        occurred_at_ms = _optional_int(event.get("occurred_at_ms"))
        if segment_id and occurred_at_ms is not None:
            # Keep the first durable commit for an idempotent event stream;
            # conflicting duplicates are surfaced by the consistency check.
            final_commits.setdefault(segment_id, occurred_at_ms)

    applied_by_job: dict[str, dict[str, Any]] = {}
    for event in events:
        if str(event.get("type") or "") != "meeting.intelligence.applied":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        job_id = str(payload.get("job_id") or event.get("causation_id") or "").strip()
        if job_id:
            applied_by_job[job_id] = dict(event)

    records: list[dict[str, Any]] = []
    e2e_latencies: list[float] = []
    intervention_latencies: list[float] = []
    enqueue_latencies: list[float] = []
    queue_latencies: list[float] = []
    execution_latencies: list[float] = []
    projection_latencies: list[float] = []
    invalid_count = 0
    excluded_count = 0

    for job in intelligence_jobs(snapshot):
        job_id = str(job.get("id") or "").strip()
        target_segment_id = str(job.get("evidence_segment_id") or "").strip()
        final_at = final_commits.get(target_segment_id)
        job_created_at = _optional_int(job.get("created_at_ms"))
        applied_event = applied_by_job.get(job_id)
        payload = (
            applied_event.get("payload")
            if isinstance(applied_event, Mapping)
            and isinstance(applied_event.get("payload"), Mapping)
            else {}
        )
        decision = payload.get("coach_decision") if isinstance(payload.get("coach_decision"), Mapping) else {}
        if not target_segment_id:
            evidence_payload = payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {}
            evidence_ids = evidence_payload.get("segment_ids")
            if isinstance(evidence_ids, (list, tuple)):
                for candidate_id in evidence_ids:
                    candidate_text = str(candidate_id or "").strip()
                    if candidate_text and candidate_text in final_commits:
                        target_segment_id = candidate_text
                        final_at = final_commits[candidate_text]
                        break
        # Superseded coalesced jobs are intentionally not user-visible and do
        # not have a projection timestamp to measure. Keep them in the audit
        # record, but do not turn an expected cancellation into an invalid E2E
        # timing sample.
        superseded = (
            str(job.get("status") or "") == "cancelled"
            and str(job.get("error_class") or "").strip().casefold() == "evidence_superseded"
        )
        coach_started_at = _optional_int(decision.get("created_at_ms"))
        decision_completed_at = _first_int(
            decision.get("completed_at_ms"),
            job.get("completed_at_ms"),
        )
        projected_at = _first_int(
            decision.get("projected_at_ms"),
            applied_event.get("occurred_at_ms") if applied_event is not None else None,
        )
        status = str(decision.get("status") or "")
        intervention = isinstance(payload.get("coach_intervention"), Mapping) or status == "intervention"

        reasons: list[str] = []
        if final_at is None and not superseded:
            reasons.append("missing_final_commit")
        if job_created_at is None and not superseded:
            reasons.append("missing_job_created")
        if applied_event is None and not superseded:
            reasons.append("missing_applied_projection")
        if coach_started_at is None and not superseded:
            reasons.append("missing_coach_started")
        if decision_completed_at is None and not superseded:
            reasons.append("missing_decision_completed")
        if projected_at is None and not superseded:
            reasons.append("missing_projected_at")

        enqueue_ms: float | None = None
        queue_ms: float | None = None
        execution_ms: float | None = None
        projection_ms: float | None = None
        e2e_ms: float | None = None
        if final_at is not None and job_created_at is not None:
            enqueue_ms = float(job_created_at - final_at)
            if enqueue_ms < 0:
                reasons.append("job_created_before_final_commit")
        if job_created_at is not None and coach_started_at is not None:
            queue_ms = float(coach_started_at - job_created_at)
            if queue_ms < 0:
                reasons.append("coach_started_before_job_created")
        if coach_started_at is not None and decision_completed_at is not None:
            execution_ms = float(decision_completed_at - coach_started_at)
            if execution_ms < 0:
                reasons.append("decision_completed_before_coach_started")
        if decision_completed_at is not None and projected_at is not None:
            projection_ms = float(projected_at - decision_completed_at)
            if projection_ms < 0:
                reasons.append("projected_before_decision_completed")
        if final_at is not None and projected_at is not None:
            e2e_ms = float(projected_at - final_at)
            if e2e_ms < 0:
                reasons.append("projected_before_final_commit")

        valid = not reasons and e2e_ms is not None
        if superseded:
            valid = False
            excluded_count += 1
        if valid:
            assert e2e_ms is not None
            e2e_latencies.append(e2e_ms)
            if intervention:
                intervention_latencies.append(e2e_ms)
            if enqueue_ms is not None:
                enqueue_latencies.append(enqueue_ms)
            if queue_ms is not None:
                queue_latencies.append(queue_ms)
            if execution_ms is not None:
                execution_latencies.append(execution_ms)
            if projection_ms is not None:
                projection_latencies.append(projection_ms)
        elif not superseded:
            invalid_count += 1
        records.append(
            {
                "job_id": job_id or None,
                "evidence_segment_id": target_segment_id or None,
                "job_status": job.get("status"),
                "coach_status": status or None,
                "intervention": intervention,
                "excluded": superseded,
                "valid": valid,
                "invalid_reasons": list(dict.fromkeys(reasons)),
                "final_committed_at_ms": final_at,
                "job_created_at_ms": job_created_at,
                "coach_started_at_ms": coach_started_at,
                "decision_completed_at_ms": decision_completed_at,
                "projected_at_ms": projected_at,
                "enqueue_latency_ms": round(enqueue_ms, 2) if enqueue_ms is not None and enqueue_ms >= 0 else None,
                "queue_latency_ms": round(queue_ms, 2) if queue_ms is not None and queue_ms >= 0 else None,
                "execution_latency_ms": round(execution_ms, 2) if execution_ms is not None and execution_ms >= 0 else None,
                "projection_latency_ms": round(projection_ms, 2) if projection_ms is not None and projection_ms >= 0 else None,
                "e2e_latency_ms": round(e2e_ms, 2) if e2e_ms is not None and e2e_ms >= 0 else None,
            }
        )

    return {
        "schema_version": "meeting_copilot.pi_stage0_e2e_latency.v1",
        "job_count": len(records),
        "valid_count": len(e2e_latencies),
        "invalid_count": invalid_count,
        "excluded_count": excluded_count,
        "attempted_count": len(e2e_latencies),
        "intervention_count": len(intervention_latencies),
        "e2e_latency_ms": {
            "p50": _percentile(e2e_latencies, 0.50),
            "p95": _percentile(e2e_latencies, 0.95),
            "max": round(max(e2e_latencies), 2) if e2e_latencies else None,
        },
        "intervention_e2e_latency_ms": {
            "p50": _percentile(intervention_latencies, 0.50),
            "p95": _percentile(intervention_latencies, 0.95),
            "max": round(max(intervention_latencies), 2) if intervention_latencies else None,
        },
        "enqueue_latency_ms": {
            "p50": _percentile(enqueue_latencies, 0.50),
            "p95": _percentile(enqueue_latencies, 0.95),
            "max": round(max(enqueue_latencies), 2) if enqueue_latencies else None,
        },
        "queue_latency_ms": {
            "p50": _percentile(queue_latencies, 0.50),
            "p95": _percentile(queue_latencies, 0.95),
            "max": round(max(queue_latencies), 2) if queue_latencies else None,
        },
        "execution_latency_ms": {
            "p50": _percentile(execution_latencies, 0.50),
            "p95": _percentile(execution_latencies, 0.95),
            "max": round(max(execution_latencies), 2) if execution_latencies else None,
        },
        "projection_latency_ms": {
            "p50": _percentile(projection_latencies, 0.50),
            "p95": _percentile(projection_latencies, 0.95),
            "max": round(max(projection_latencies), 2) if projection_latencies else None,
        },
        "records": records,
    }


def latest_applied_decision(
    decisions: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the newest applied projection for history, regardless of the latest job."""

    applied = [item for item in decisions if item.get("record_type") == "applied_decision"]
    if not applied:
        return None
    return max(
        applied,
        key=lambda item: (
            _optional_int(item.get("seq")) or -1,
            _optional_int(item.get("occurred_at_ms")) or -1,
            _optional_int(item.get("completed_at_ms")) or -1,
        ),
    )


def latest_intelligence_result(
    snapshot: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind the reported current-turn result to the latest intelligence job."""

    latest_job = latest_intelligence_job(snapshot)
    if latest_job is None:
        return {
            "schema_version": "meeting_copilot.pi_stage0_latest_intelligence_result.v1",
            "job_id": None,
            "job_status": None,
            "projection_status": "unavailable",
            "applied": False,
            "error_class": None,
            "drop_reason": "no_intelligence_job",
            "decision": None,
        }

    job_id = str(latest_job.get("id") or "")
    matching_rows = [item for item in decisions if str(item.get("job_id") or "") == job_id]
    applied_decision = latest_applied_decision(matching_rows)
    terminal_rows = [
        item
        for item in matching_rows
        if item.get("record_type") == "terminal_without_applied_decision"
    ]
    terminal_row = terminal_rows[-1] if terminal_rows else None
    if applied_decision is not None:
        projection_status = "applied"
        drop_reason = None
    elif terminal_row is not None:
        projection_status = str(terminal_row.get("outcome") or "unavailable")
        drop_reason = terminal_row.get("drop_reason")
    else:
        deadline_at_ms, _deadline_source = _job_deadline(latest_job, None)
        projection_status, drop_reason = _unapplied_terminal_classification(
            latest_job,
            deadline_at_ms=deadline_at_ms,
        )
    return {
        "schema_version": "meeting_copilot.pi_stage0_latest_intelligence_result.v1",
        "job_id": job_id or None,
        "job_status": latest_job.get("status"),
        "projection_status": projection_status,
        "applied": applied_decision is not None,
        "error_class": latest_job.get("error_class"),
        "drop_reason": drop_reason,
        "decision": dict(applied_decision) if applied_decision is not None else None,
    }


def _get_json(
    client: JsonHttpClient,
    path: str,
    *,
    action: str,
) -> dict[str, Any]:
    return _expect(
        client.request("GET", path),
        {200},
        layer="evidence_collection",
        action=action,
    )


def collect_evidence(client: JsonHttpClient, meeting_id: str) -> dict[str, Any]:
    encoded_id = quote(meeting_id, safe="")
    atomic_capture = _get_json(
        client,
        f"/v2/meetings/{encoded_id}/acceptance-evidence?max_segments=10000&max_events=10000",
        action="atomic acceptance evidence collection",
    )
    if not isinstance(atomic_capture.get("snapshot"), Mapping):
        raise ReplayFailure("evidence_collection", "atomic acceptance evidence has no snapshot")
    if not isinstance(atomic_capture.get("transcript"), Mapping):
        raise ReplayFailure("evidence_collection", "atomic acceptance evidence has no transcript")
    if not isinstance(atomic_capture.get("events"), list):
        raise ReplayFailure("evidence_collection", "atomic acceptance evidence has no events list")
    # The atomic capture intentionally preserves durable coach evidence. The
    # regular snapshot endpoint applies the live UI projection policy, which
    # clears current coach cards after meeting end. Keep both views so the
    # acceptance report can distinguish audit retention from user-visible UI.
    ui_snapshot = _get_json(
        client,
        f"/v2/meetings/{encoded_id}/snapshot?segment_limit=500",
        action="UI projection snapshot collection",
    )
    captured_transcript = dict(atomic_capture["transcript"])
    captured_segments = captured_transcript.get("segments")
    if not isinstance(captured_segments, list):
        raise ReplayFailure("evidence_collection", "atomic acceptance evidence transcript has no segments list")
    captured_transcript.setdefault("meeting_id", meeting_id)
    captured_transcript.setdefault("page_count", 1 if captured_segments else 0)
    captured_transcript.setdefault("after_transcript_seq", 0)
    captured_transcript.setdefault(
        "next_after_transcript_seq",
        max((int(item.get("transcript_seq") or 0) for item in captured_segments if isinstance(item, Mapping)), default=0),
    )
    live_session = atomic_capture.get("live_session")
    if isinstance(live_session, Mapping):
        asr_live_events = dict(live_session)
        asr_live_events.setdefault("session_id", meeting_id)
        asr_live_events.setdefault("events", [])
    else:
        asr_live_events = {
            "session_id": meeting_id,
            "source": "atomic_acceptance_capture",
            "events": [],
        }
    return {
        "acceptance_evidence": atomic_capture,
        "snapshot": atomic_capture["snapshot"],
        "ui_snapshot": ui_snapshot,
        "events": list(atomic_capture["events"]),
        "transcript": captured_transcript,
        "semantic_paragraphs": _get_json(
            client,
            f"/v2/meetings/{encoded_id}/semantic-paragraphs",
            action="semantic paragraph collection",
        ),
        "traces": _get_json(
            client,
            f"/v2/meetings/{encoded_id}/traces",
            action="trace collection",
        ),
        "slo": _get_json(
            client,
            f"/v2/meetings/{encoded_id}/realtime-ai-slo",
            action="realtime AI SLO collection",
        ),
        "asr_live_events": asr_live_events,
    }


def _snapshot_job_values(snapshot: Mapping[str, Any], key: str) -> list[Mapping[str, Any]] | None:
    """Return redacted job summaries while preserving a missing-field signal."""

    raw_jobs = snapshot.get(key)
    if isinstance(raw_jobs, Mapping):
        return [job for job in raw_jobs.values() if isinstance(job, Mapping)]
    if isinstance(raw_jobs, list):
        return [job for job in raw_jobs if isinstance(job, Mapping)]
    return None


def snapshot_jobs_settled(snapshot: Mapping[str, Any]) -> bool:
    """Require every durable job to be terminal before replay evidence is final."""

    durable_jobs = _snapshot_job_values(snapshot, "jobs")
    if durable_jobs is None:
        # A missing jobs field means the checker cannot prove that all durable
        # work has settled. Keep the acceptance gate fail-closed.
        return False
    if any(str(job.get("status") or "") not in TERMINAL_JOB_STATUSES for job in durable_jobs):
        return False

    # Review jobs are an older, separate projection. They may be absent on
    # older snapshots, but if present they are part of the post-end contract.
    review_jobs = _snapshot_job_values(snapshot, "review_jobs") or []
    return all(str(job.get("status") or "") in TERMINAL_JOB_STATUSES for job in review_jobs)


def wait_after_end(
    client: JsonHttpClient,
    *,
    meeting_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], bool]:
    encoded_id = quote(meeting_id, safe="")
    deadline = time.monotonic() + timeout_seconds
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = _get_json(
            client,
            f"/v2/meetings/{encoded_id}/snapshot?segment_limit=500",
            action="post-end snapshot poll",
        )
        audio_status = str((snapshot.get("audio") or {}).get("status") or "")
        phase = str((snapshot.get("runtime") or {}).get("phase") or "")
        if phase == "ended" and snapshot_jobs_settled(snapshot) and audio_status not in {"recording", "assembling"}:
            return snapshot, True
        time.sleep(max(0.05, poll_interval_seconds))
    return snapshot, False


def end_meeting_with_retry(
    client: JsonHttpClient,
    *,
    meeting_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> HttpResult:
    """Retry only the documented transient ASR-finalization conflict."""

    encoded_id = quote(meeting_id, safe="")
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    last_result: HttpResult | None = None
    while time.monotonic() < deadline:
        last_result = client.request(
            "POST",
            f"/v2/meetings/{encoded_id}/end",
            payload={"action": "end_and_review"},
            timeout_seconds=max(45.0, min(timeout_seconds, 90.0)),
        )
        if last_result.status in {200, 202}:
            return last_result
        detail = json.dumps(last_result.payload.get("detail") or {}, ensure_ascii=False)
        if last_result.status != 409 or "asr_finalization_pending" not in detail:
            return last_result
        time.sleep(max(0.05, poll_interval_seconds))
    if last_result is not None:
        return last_result
    raise ReplayFailure("meeting_end", "meeting end retry window elapsed without an HTTP response")


def _recording_source_types(events: Sequence[Mapping[str, Any]]) -> list[str]:
    sources: set[str] = set()
    for event in events:
        if not str(event.get("type") or "").startswith("recording."):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        nested_records = [payload]
        for key in ("output", "recording"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                nested_records.append(nested)
        for record in nested_records:
            source_type = str(record.get("source_type") or "").strip()
            if source_type:
                sources.add(source_type)
    return sorted(sources)


def _normalize_fixture_quality_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _release_incident_fixture_quality(
    *,
    fixture_sha256: str | None,
    snapshot: Mapping[str, Any],
    transcript: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if str(fixture_sha256 or "").lower() != RELEASE_INCIDENT_FIXTURE_SHA256:
        return {
            "applicable": False,
            "contract_id": None,
            "fixture_sha256": str(fixture_sha256 or "") or None,
            "reason": "no_quality_contract_for_fixture",
            "checks": {},
        }

    segments = [item for item in transcript.get("segments") or [] if isinstance(item, Mapping)]
    effective_texts = [
        str(segment.get("correction_after_text") or segment.get("text") or "")
        for segment in segments
    ]
    normalized_transcript = _normalize_fixture_quality_text("\n".join(effective_texts))
    matched_anchor_ids: list[str] = []
    for anchor_id, alternatives in RELEASE_INCIDENT_ANCHORS:
        if any(
            all(
                _normalize_fixture_quality_text(term) in normalized_transcript
                for term in required_terms
            )
            for required_terms in alternatives
        ):
            matched_anchor_ids.append(anchor_id)
    all_anchor_ids = [anchor_id for anchor_id, _alternatives in RELEASE_INCIDENT_ANCHORS]
    missing_anchor_ids = [
        anchor_id for anchor_id in all_anchor_ids if anchor_id not in matched_anchor_ids
    ]
    anchor_coverage = len(matched_anchor_ids) / len(all_anchor_ids)
    missing_required_anchor_ids = sorted(
        RELEASE_INCIDENT_REQUIRED_ANCHORS.difference(matched_anchor_ids)
    )

    correction_jobs = [
        job
        for job in (_snapshot_job_values(snapshot, "jobs") or [])
        if str(job.get("kind") or "") == "correction"
    ]
    failed_correction_job_ids = [
        str(job.get("id") or "")
        for job in correction_jobs
        if str(job.get("status") or "") == "failed"
    ]
    failed_correction_segment_ids = [
        str(segment.get("segment_id") or "")
        for segment in segments
        if str(segment.get("correction_status") or "").startswith("failed")
        or bool(str(segment.get("correction_error_class") or "").strip())
    ]
    unsettled_correction_job_ids = [
        str(job.get("id") or "")
        for job in correction_jobs
        if str(job.get("status") or "") not in TERMINAL_JOB_STATUSES
    ]
    unprocessed_correction_segment_ids = [
        str(segment.get("segment_id") or "")
        for segment in segments
        if str(segment.get("correction_status") or "") not in {"changed", "no_change"}
    ]

    intelligence_jobs = [
        job
        for job in (_snapshot_job_values(snapshot, "jobs") or [])
        if str(job.get("kind") or "") == "intelligence"
    ]
    intelligence_reliability_error_job_ids = [
        str(job.get("id") or "")
        for job in intelligence_jobs
        if str(job.get("status") or "") == "failed"
        or (
            str(job.get("status") or "") == "cancelled"
            and str(job.get("error_class") or "") != "evidence_superseded"
        )
    ]

    pi_runtime_event_seqs: list[int | None] = []
    pi_intervention_event_seqs: list[int | None] = []
    pi_reliability_error_event_seqs: list[int | None] = []
    for event in events:
        if str(event.get("type") or "") != "meeting.intelligence.applied":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        decision = (
            payload.get("coach_decision")
            if isinstance(payload.get("coach_decision"), Mapping)
            else {}
        )
        runtime_used = str(decision.get("runtime_used") or payload.get("runtime_used") or "")
        if str(decision.get("origin") or "") != "pi" or runtime_used != "pi":
            continue
        event_seq = event.get("seq") if isinstance(event.get("seq"), int) else None
        pi_runtime_event_seqs.append(event_seq)
        decision_status = str(decision.get("status") or "")
        if decision_status in {"timed_out", "failed", "error"}:
            pi_reliability_error_event_seqs.append(event_seq)
        if decision_status == "intervention" and isinstance(
            payload.get("coach_intervention"), Mapping
        ):
            pi_intervention_event_seqs.append(event_seq)

    checks = {
        "known_fixture_transcript_anchor_coverage": (
            anchor_coverage >= RELEASE_INCIDENT_MIN_ANCHOR_COVERAGE
        ),
        "known_fixture_required_anchors": not missing_required_anchor_ids,
        "known_fixture_corrections_healthy": (
            bool(correction_jobs)
            and bool(segments)
            and not failed_correction_job_ids
            and not failed_correction_segment_ids
            and not unsettled_correction_job_ids
            and not unprocessed_correction_segment_ids
        ),
        "known_fixture_pi_runtime_activity": bool(pi_runtime_event_seqs),
        "known_fixture_pi_intervention": bool(pi_intervention_event_seqs),
        "known_fixture_pi_reliability_healthy": (
            not intelligence_reliability_error_job_ids
            and not pi_reliability_error_event_seqs
        ),
    }
    return {
        "applicable": True,
        "contract_id": RELEASE_INCIDENT_QUALITY_CONTRACT_ID,
        "fixture_sha256": RELEASE_INCIDENT_FIXTURE_SHA256,
        "passed": all(checks.values()),
        "checks": checks,
        "transcript": {
            "effective_segment_count": len(effective_texts),
            "normalized_character_count": len(normalized_transcript),
            "matched_anchor_count": len(matched_anchor_ids),
            "total_anchor_count": len(all_anchor_ids),
            "anchor_coverage": round(anchor_coverage, 4),
            "minimum_anchor_coverage": RELEASE_INCIDENT_MIN_ANCHOR_COVERAGE,
            "matched_anchor_ids": matched_anchor_ids,
            "missing_anchor_ids": missing_anchor_ids,
            "required_anchor_ids": sorted(RELEASE_INCIDENT_REQUIRED_ANCHORS),
            "missing_required_anchor_ids": missing_required_anchor_ids,
        },
        "corrections": {
            "job_count": len(correction_jobs),
            "failed_job_count": len(failed_correction_job_ids),
            "failed_job_ids": failed_correction_job_ids,
            "unsettled_job_count": len(unsettled_correction_job_ids),
            "unsettled_job_ids": unsettled_correction_job_ids,
            "failed_segment_count": len(failed_correction_segment_ids),
            "failed_segment_ids": failed_correction_segment_ids,
            "unprocessed_segment_count": len(unprocessed_correction_segment_ids),
            "unprocessed_segment_ids": unprocessed_correction_segment_ids,
        },
        "pi": {
            "runtime_activity_count": len(pi_runtime_event_seqs),
            "runtime_event_seqs": pi_runtime_event_seqs,
            "intervention_count": len(pi_intervention_event_seqs),
            "intervention_event_seqs": pi_intervention_event_seqs,
            "reliability_error_job_count": len(
                intelligence_reliability_error_job_ids
            ),
            "reliability_error_job_ids": intelligence_reliability_error_job_ids,
            "reliability_error_event_count": len(
                pi_reliability_error_event_seqs
            ),
            "reliability_error_event_seqs": pi_reliability_error_event_seqs,
        },
    }


def evaluate_acceptance(
    *,
    procedural_failure: Mapping[str, Any] | None,
    runtime_validation: Mapping[str, Any],
    response_statuses: Mapping[str, int | None],
    ws_stats: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    transcript: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    evidence_complete: bool,
    post_end_settled: bool,
    fixture_sha256: str | None = None,
    e2e_latency: Mapping[str, Any] | None = None,
    atomic_evidence: Mapping[str, Any] | None = None,
    ui_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    latest_job = latest_intelligence_job(snapshot)
    latest_job_id = str((latest_job or {}).get("id") or "")
    applied = _applied_event_for_job(events, latest_job_id) if latest_job_id else None
    payload = (
        applied.get("payload") if isinstance(applied, Mapping) and isinstance(applied.get("payload"), Mapping) else {}
    )
    decision = payload.get("coach_decision") if isinstance(payload.get("coach_decision"), Mapping) else {}
    intervention = payload.get("coach_intervention") if isinstance(payload.get("coach_intervention"), Mapping) else None
    decision_status = str(decision.get("status") or "")
    decision_llm_called = (
        decision.get("llm_called") is True
        or decision.get("llm_call_status") == "called"
        or (
            isinstance(decision.get("agent_metrics"), Mapping)
            and decision["agent_metrics"].get("provider_attempted") is True
        )
    )
    pi_path_evaluated = bool(
        decision.get("runtime_used") == "pi"
        or decision.get("origin") == "pi"
        or decision.get("pi_provider_attempted") is True
        or (
            isinstance(decision.get("agent_metrics"), Mapping)
            and decision["agent_metrics"].get("provider_attempted") is True
        )
    )
    pi_terminal_action_completed = bool(
        pi_path_evaluated
        and decision.get("origin") == "pi"
        and decision.get("runtime_used") in {None, "pi"}
        and decision_status in SUCCESSFUL_COACH_STATUSES
    )
    # ``snapshot`` is the atomic/durable evidence view. For the end-state UI
    # gate prefer the decorated snapshot endpoint, falling back to the atomic
    # view for older callers and unit fixtures that do not provide it.
    projected_snapshot = ui_snapshot if isinstance(ui_snapshot, Mapping) else snapshot
    segments = [item for item in transcript.get("segments") or [] if isinstance(item, Mapping)]
    ws_event_counts = ws_stats.get("event_counts") if isinstance(ws_stats.get("event_counts"), Mapping) else {}
    audio_source = str(ws_stats.get("audio_source") or "unknown")
    refiner_policy = (
        runtime_validation.get("refiner_policy")
        if isinstance(runtime_validation.get("refiner_policy"), Mapping)
        else {}
    )
    final_sources = {
        str(source).strip()
        for source in ws_stats.get("final_sources") or []
        if str(source).strip()
    }
    track_provenance = bool(segments) and all(
        str(segment.get("source_track") or "") == "microphone" for segment in segments
    )
    recording_source_types = _recording_source_types(events)
    recording_source_matches = audio_source in recording_source_types
    fixture_quality = _release_incident_fixture_quality(
        fixture_sha256=fixture_sha256,
        snapshot=snapshot,
        transcript=transcript,
        events=events,
    )
    # The caller's polling result is necessary but not sufficient: verify the
    # final snapshot itself so a stale/partial polling response cannot produce
    # a false PASS.
    post_end_jobs_settled = bool(post_end_settled and snapshot_jobs_settled(snapshot))
    checks = {
        "no_procedural_failure": procedural_failure is None,
        "runtime_validated": runtime_validation.get("validated") is True,
        "meeting_created": response_statuses.get("create_meeting") == 201,
        "preparation_saved": response_statuses.get("save_preparation") == 200,
        "asr_ready": ws_stats.get("ready") is True,
        "asr_non_empty_final": int(ws_stats.get("non_empty_final_count") or 0) > 0,
        "asr_final_source_contract": (
            refiner_policy.get("mode") != REFINER_POLICY_ONLINE_ONLY
            or ONLINE_ONLY_FINAL_SOURCE in final_sources
        ),
        "asr_no_error_event": not any(
            int(ws_event_counts.get(name) or 0) > 0 for name in ("provider_error", "recording_rejected", "error")
        ),
        "durable_transcript_present": bool(segments),
        # Hardware proof is deliberately separate from controlled WAV proof.
        # A replay may use the same microphone track contract without having
        # touched a physical input device.
        "microphone_provenance_present": (
            audio_source == "browser_live_mic" and recording_source_matches and track_provenance
        ),
        "audio_provenance_present": audio_source in {
            "browser_live_mic",
            "simulated_realtime_wav",
            "speaker_loopback",
        }
        and recording_source_matches
        and track_provenance,
        "intelligence_job_present": latest_job is not None,
        "intelligence_job_succeeded": str((latest_job or {}).get("status") or "") == "succeeded",
        "intelligence_applied": applied is not None,
        "llm_first_source": payload.get("source") == "llm_first",
        "llm_called": decision_llm_called or payload.get("llm_called") is True,
        "coach_decision_completed": decision_status in SUCCESSFUL_COACH_STATUSES,
        "pi_terminal_action_completed": pi_terminal_action_completed,
        "coach_intervention_consistent": (
            intervention is not None if decision_status == "intervention" else intervention is None
        ),
        "meeting_ended": response_statuses.get("end_meeting") in {200, 202}
        and str((snapshot.get("runtime") or {}).get("phase") or "") == "ended",
        "ended_coach_projection_cleared": (
            str((projected_snapshot.get("runtime") or {}).get("phase") or "") != "ended"
            or all(projected_snapshot.get(key) is None for key in ("follow_up", "semantic_follow_up", "coach_decision"))
        ),
        "evidence_complete": evidence_complete,
        "post_end_jobs_settled": post_end_jobs_settled,
    }
    if atomic_evidence is not None:
        consistency = (
            atomic_evidence.get("consistency")
            if isinstance(atomic_evidence.get("consistency"), Mapping)
            else {}
        )
        checks["atomic_evidence_eligible"] = consistency.get("acceptance_eligible") is True
    e2e_checks: dict[str, bool] = {}
    if e2e_latency is not None:
        latency_summary = (
            e2e_latency.get("e2e_latency_ms")
            if isinstance(e2e_latency.get("e2e_latency_ms"), Mapping)
            else {}
        )
        latency_p50 = latency_summary.get("p50")
        latency_p95 = latency_summary.get("p95")
        latency_max = latency_summary.get("max")
        e2e_checks = {
            "e2e_latency_complete": int(e2e_latency.get("invalid_count") or 0) == 0,
            "e2e_latency_p50_ms": (
                isinstance(latency_p50, (int, float))
                and float(latency_p50) <= E2E_LATENCY_P50_LIMIT_MS
            ),
            "e2e_latency_p95_ms": (
                isinstance(latency_p95, (int, float))
                and float(latency_p95) <= E2E_LATENCY_P95_LIMIT_MS
            ),
            "e2e_latency_max_ms": (
                isinstance(latency_max, (int, float))
                and float(latency_max) <= E2E_LATENCY_MAX_LIMIT_MS
            ),
        }
        checks.update(e2e_checks)
    checks.update(fixture_quality.get("checks") or {})
    skipped_checks = (
        {}
        if audio_source == "browser_live_mic"
        else {
            "microphone_provenance_present": (
                f"not_applicable_for_audio_source:{audio_source}"
            )
        }
    )
    if not pi_path_evaluated:
        skipped_checks["pi_terminal_action_completed"] = "not_applicable_for_non_pi_decision"
    required_checks = {name: passed for name, passed in checks.items() if name not in skipped_checks}
    failed_checks = [name for name, passed in required_checks.items() if not passed]
    return {
        "passed": all(required_checks.values()),
        "checks": checks,
        "required_checks": required_checks,
        "skipped_checks": skipped_checks,
        "failed_checks": failed_checks,
        "audio_provenance": {
            "declared_audio_source": audio_source,
            "recording_source_types": recording_source_types,
            "recording_source_matches": recording_source_matches,
            "transcript_track_provenance": track_provenance,
        },
        "latest_intelligence_job": latest_job,
        "latest_applied_event_seq": applied.get("seq") if isinstance(applied, Mapping) else None,
        "latest_coach_status": decision_status or None,
        "latest_coach_origin": decision.get("origin"),
        "projection_snapshot_source": "ui_snapshot" if isinstance(ui_snapshot, Mapping) else "atomic_snapshot",
        "latest_coach_outcome": (
            "intervention"
            if intervention is not None
            else "explicit_silence"
            if decision_status in {"protected_silent", "not_triggered", "silent"}
            else "failure_or_missing"
        ),
        "fixture_quality": fixture_quality,
        "e2e_latency": dict(e2e_latency) if e2e_latency is not None else None,
        "post_end": {
            "settled": post_end_jobs_settled,
            "unsettled_job_ids": [
                str(job.get("id") or "")
                for job in (_snapshot_job_values(snapshot, "jobs") or [])
                if str(job.get("status") or "") not in TERMINAL_JOB_STATUSES
            ],
        },
    }


def _write_json(path: Path, payload: Any, *, secret_values: Sequence[str]) -> None:
    safe_payload = sanitize(payload, secret_values=secret_values)
    path.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Any], *, secret_values: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    sanitize(row, secret_values=secret_values),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


def _one_line(value: Any, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def build_notes(
    *,
    meeting_id: str,
    acceptance: Mapping[str, Any],
    wav_info: WavInfo | None,
    ws_stats: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
    post_end_settled: bool,
    failure: Mapping[str, Any] | None,
    e2e_latency: Mapping[str, Any] | None = None,
) -> str:
    latest_result = latest_intelligence_result(snapshot, decisions)
    latest = (
        latest_result.get("decision")
        if isinstance(latest_result.get("decision"), Mapping)
        else {}
    )
    decision = latest.get("coach_decision") if isinstance(latest.get("coach_decision"), Mapping) else {}
    intervention = latest.get("coach_intervention") if isinstance(latest.get("coach_intervention"), Mapping) else {}
    latest_job_id = str(latest_result.get("job_id") or "")
    historical = (
        latest_applied_decision(
            [
                item
                for item in decisions
                if str(item.get("job_id") or "") != latest_job_id
            ]
        )
        if not latest_result.get("applied")
        else None
    )
    historical_decision = (
        historical.get("coach_decision")
        if isinstance(historical, Mapping)
        and isinstance(historical.get("coach_decision"), Mapping)
        else {}
    )
    failed_checks = list(acceptance.get("failed_checks") or [])
    if not failed_checks and not acceptance.get("required_checks"):
        failed_checks = [name for name, passed in (acceptance.get("checks") or {}).items() if not passed]
    skipped_checks = acceptance.get("skipped_checks") or {}
    fixture_quality = (
        acceptance.get("fixture_quality")
        if isinstance(acceptance.get("fixture_quality"), Mapping)
        else {}
    )
    lines = [
        "# Pi Stage 0 production replay",
        "",
        f"- Outcome: {'PASS' if acceptance.get('passed') else 'FAIL'}",
        f"- Meeting: `{meeting_id}`",
        f"- Fixture: `{wav_info.filename if wav_info else 'unavailable'}`",
        f"- Audio source: `{ws_stats.get('audio_source') or 'unknown'}` ({ws_stats.get('audio_provenance') or 'unknown'})",
        f"- Audio sent: {float(ws_stats.get('sent_audio_seconds') or 0):.3f}s at {float(ws_stats.get('pace') or 0):.2f}x pacing",
        f"- ASR finals: {int(ws_stats.get('non_empty_final_count') or 0)}",
        f"- Latest intelligence job: `{latest_result.get('job_id') or 'unavailable'}`",
        f"- Latest intelligence job status: `{latest_result.get('job_status') or 'unavailable'}`",
        f"- Latest intelligence projection: `{latest_result.get('projection_status') or 'unavailable'}`",
        f"- Latest intelligence error_class: `{latest_result.get('error_class') or 'unavailable'}`",
        f"- Current-turn coach outcome: `{latest.get('outcome') or 'unavailable'}`",
        f"- Current-turn coach status/origin: `{decision.get('status') or 'unavailable'}` / `{decision.get('origin') or 'unavailable'}`",
        f"- Post-end jobs and recording settled: {'yes' if post_end_settled else 'no'}",
    ]
    if isinstance(e2e_latency, Mapping):
        summary = e2e_latency.get("e2e_latency_ms") if isinstance(e2e_latency.get("e2e_latency_ms"), Mapping) else {}
        queue_summary = e2e_latency.get("queue_latency_ms") if isinstance(e2e_latency.get("queue_latency_ms"), Mapping) else {}
        lines.extend(
            [
                (
                    "- Durable final -> coach projection latency (P50/P95/max): "
                    f"{summary.get('p50') if summary.get('p50') is not None else 'n/a'}/"
                    f"{summary.get('p95') if summary.get('p95') is not None else 'n/a'}/"
                    f"{summary.get('max') if summary.get('max') is not None else 'n/a'} ms"
                ),
                (
                    "- Coach start queue latency (P50/P95/max): "
                    f"{queue_summary.get('p50') if queue_summary.get('p50') is not None else 'n/a'}/"
                    f"{queue_summary.get('p95') if queue_summary.get('p95') is not None else 'n/a'}/"
                    f"{queue_summary.get('max') if queue_summary.get('max') is not None else 'n/a'} ms"
                ),
                f"- E2E timing records valid/invalid: {int(e2e_latency.get('valid_count') or 0)}/{int(e2e_latency.get('invalid_count') or 0)}",
            ]
        )
    if fixture_quality.get("applicable"):
        quality_transcript = (
            fixture_quality.get("transcript")
            if isinstance(fixture_quality.get("transcript"), Mapping)
            else {}
        )
        quality_corrections = (
            fixture_quality.get("corrections")
            if isinstance(fixture_quality.get("corrections"), Mapping)
            else {}
        )
        quality_pi = (
            fixture_quality.get("pi")
            if isinstance(fixture_quality.get("pi"), Mapping)
            else {}
        )
        lines.extend(
            [
                f"- Fixture quality contract: `{fixture_quality.get('contract_id')}`",
                (
                    "- Transcript anchor coverage: "
                    f"{quality_transcript.get('matched_anchor_count', 0)}/"
                    f"{quality_transcript.get('total_anchor_count', 0)}"
                ),
                (
                    "- Failed correction jobs/segments: "
                    f"{quality_corrections.get('failed_job_count', 0)}/"
                    f"{quality_corrections.get('failed_segment_count', 0)}"
                ),
                (
                    "- Pi runtime activity/interventions: "
                    f"{quality_pi.get('runtime_activity_count', 0)}/"
                    f"{quality_pi.get('intervention_count', 0)}"
                ),
            ]
        )
    if intervention:
        lines.extend(
            [
                "",
                "## Coach intervention",
                "",
                f"- Recommendation: {_one_line(intervention.get('recommendation') or intervention.get('next_move'))}",
                f"- Reason: {_one_line(intervention.get('reason'))}",
                f"- Evidence: {_one_line(intervention.get('evidence_quote'))}",
            ]
        )
    elif decision:
        lines.extend(
            [
                "",
                "## Explicit silence",
                "",
                f"- Decision reason: {_one_line(decision.get('decision_reason') or decision.get('status_reason'))}",
                "- This is a completed coach decision, not a missing right-rail card.",
            ]
        )
    elif latest_result.get("job_id"):
        lines.extend(
            [
                "",
                "## Latest intelligence terminal result",
                "",
                f"- Job status: `{latest_result.get('job_status') or 'unavailable'}`",
                f"- Projection: `{latest_result.get('projection_status') or 'unavailable'}`",
                f"- Error class: `{latest_result.get('error_class') or 'unavailable'}`",
                f"- Drop reason: `{latest_result.get('drop_reason') or 'unavailable'}`",
                "- No coach decision was applied for this latest intelligence job.",
            ]
        )
    if isinstance(historical, Mapping):
        lines.extend(
            [
                "",
                "## Historical coach state",
                "",
                f"- Historical applied job: `{historical.get('job_id') or 'unavailable'}`",
                f"- Historical outcome: `{historical.get('outcome') or 'unavailable'}`",
                f"- Historical status/origin: `{historical_decision.get('status') or 'unavailable'}` / `{historical_decision.get('origin') or 'unavailable'}`",
                "- This decision belongs to an earlier job and is not the current-turn Pi result.",
            ]
        )
    if failure or failed_checks:
        lines.extend(["", "## Failures", ""])
        if failure:
            lines.append(
                f"- `{failure.get('layer')}` / `{failure.get('error_class')}`: {_one_line(failure.get('message'))}"
            )
        for check in failed_checks:
            lines.append(f"- Acceptance check failed: `{check}`")
    if skipped_checks:
        lines.extend(["", "## Skipped checks", ""])
        for check, reason in skipped_checks.items():
            lines.append(f"- `{check}`: {_one_line(reason)}")
    capabilities = (
        (((snapshot.get("runtime") or {}).get("ai") or {}).get("capabilities") or {}).get("proactive_suggestions")
        if isinstance(snapshot, Mapping)
        else None
    )
    if isinstance(capabilities, Mapping):
        lines.extend(
            [
                "",
                "## Runtime projection",
                "",
                f"- State: `{capabilities.get('state') or 'unknown'}`",
                f"- Label: {_one_line(capabilities.get('label'))}",
                f"- Detail: {_one_line(capabilities.get('detail'))}",
            ]
        )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "Raw WebSocket events and durable formal events are intentionally stored in separate JSONL files.",
            "The manifest contains fixture provenance and checksums; no audio bytes or credentials are copied into this bundle.",
            "",
        ]
    )
    return "\n".join(lines)


def _artifact_metadata(output_dir: Path, filenames: Iterable[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for filename in sorted(set(filenames)):
        path = output_dir / filename
        if not path.is_file():
            continue
        metadata[filename] = {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    return metadata


def _prepare_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.exists():
        if not resolved.is_dir():
            raise ReplayFailure("argument_validation", "--output-dir is not a directory")
        if any(resolved.iterdir()):
            raise ReplayFailure("argument_validation", "--output-dir must be empty or absent")
    else:
        resolved.mkdir(parents=True)
    return resolved


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    if not MEETING_ID_RE.fullmatch(str(args.meeting_id)):
        raise ReplayFailure("argument_validation", "--meeting-id contains unsafe characters")
    for name in (
        "pace",
        "chunk_seconds",
        "ready_timeout",
        "finalize_timeout",
        "intelligence_timeout",
        "post_end_timeout",
        "poll_interval",
        "http_timeout",
    ):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            raise ReplayFailure("argument_validation", f"--{name.replace('_', '-')} must be positive")
    if not math.isfinite(float(args.tail_silence_seconds)) or float(args.tail_silence_seconds) < 0:
        raise ReplayFailure("argument_validation", "--tail-silence-seconds must be non-negative")

    output_dir = _prepare_output_dir(args.output_dir)
    token = str(os.environ.get(args.token_env) or "").strip()
    client = JsonHttpClient(
        args.base_url,
        token=token,
        timeout_seconds=args.http_timeout,
    )
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    response_statuses: dict[str, int | None] = {}
    runtime_validation: dict[str, Any] = {}
    refiner_prewarm: dict[str, Any] = {}
    wav_info: WavInfo | None = None
    ws_events: list[dict[str, Any]] = []
    ws_stats: dict[str, Any] = {}
    pre_end_snapshot: dict[str, Any] = {}
    evidence: dict[str, Any] = {
        "acceptance_evidence": {},
        "snapshot": {},
        "events": [],
        "transcript": {},
        "semantic_paragraphs": {},
        "traces": {},
        "slo": {},
        "asr_live_events": {},
    }
    end_response: dict[str, Any] = {}
    meeting_created = False
    meeting_ended = False
    post_end_settled = False
    post_end_wait_attempted = False
    intelligence_poll: dict[str, Any] = {}
    failure: dict[str, Any] | None = None
    warnings: list[str] = []

    try:
        wav_info = inspect_wav(args.wav)
        refiner_prewarm = prewarm_refiner_if_needed(client)
        runtime_validation = validate_runtime(client, allow_mock_llm=args.allow_mock_llm)
        runtime_validation["refiner_prewarm"] = refiner_prewarm
        expected_duration = max(
            90,
            math.ceil(wav_info.duration_seconds + args.tail_silence_seconds + 10),
        )
        create_result = client.request(
            "POST",
            "/v2/meetings",
            payload={
                "meeting_id": args.meeting_id,
                "title": "Pi Stage 0 release incident replay",
                "expected_duration_seconds": expected_duration,
                "track_count": 1,
            },
        )
        response_statuses["create_meeting"] = create_result.status
        _expect(
            create_result,
            {201},
            layer="meeting_create",
            action="meeting creation",
        )
        meeting_created = True
        encoded_id = quote(args.meeting_id, safe="")
        preparation_result = client.request(
            "PUT",
            f"/v2/meetings/{encoded_id}/preparation",
            payload=meeting_preparation_payload(wav_info),
        )
        response_statuses["save_preparation"] = preparation_result.status
        _expect(
            preparation_result,
            {200},
            layer="meeting_preparation",
            action="meeting preparation",
        )
        ws_stats = stream_wav(
            client,
            meeting_id=args.meeting_id,
            wav_path=args.wav.expanduser().resolve(),
            wav_info=wav_info,
            pace=float(args.pace),
            chunk_seconds=float(args.chunk_seconds),
            tail_silence_seconds=float(args.tail_silence_seconds),
            ready_timeout_seconds=float(args.ready_timeout),
            finalize_timeout_seconds=float(args.finalize_timeout),
            event_sink=ws_events,
            audio_source=str(args.audio_source),
        )
        (
            pre_end_snapshot,
            polled_events,
            terminal_job,
            applied_event,
        ) = wait_for_intelligence(
            client,
            meeting_id=args.meeting_id,
            timeout_seconds=float(args.intelligence_timeout),
            poll_interval_seconds=float(args.poll_interval),
        )
        intelligence_poll = {
            "terminal_job": terminal_job,
            "applied_event_seq": applied_event.get("seq"),
            "formal_event_count_at_terminal": len(polled_events),
        }

        end_result = end_meeting_with_retry(
            client,
            meeting_id=args.meeting_id,
            timeout_seconds=max(45.0, float(args.http_timeout)),
            poll_interval_seconds=float(args.poll_interval),
        )
        response_statuses["end_meeting"] = end_result.status
        end_response = _expect(
            end_result,
            {200, 202},
            layer="meeting_end",
            action="meeting end_and_review",
        )
        meeting_ended = True
        post_end_wait_attempted = True
        _, post_end_settled = wait_after_end(
            client,
            meeting_id=args.meeting_id,
            timeout_seconds=float(args.post_end_timeout),
            poll_interval_seconds=float(args.poll_interval),
        )
        if not post_end_settled:
            warnings.append("post-end review jobs or recording export did not settle before timeout")
    except Exception as exc:
        classified = exc if isinstance(exc, ReplayFailure) else ReplayFailure("unhandled", str(exc))
        if isinstance(classified, ReplayFailure) and classified.transport_stats:
            ws_stats = dict(classified.transport_stats)
        failure = {
            "layer": classified.layer,
            "error_class": type(exc).__name__,
            "message": _sanitize_text(str(classified), (token,))[:1000],
        }
    finally:
        if meeting_created and not meeting_ended:
            try:
                end_result = end_meeting_with_retry(
                    client,
                    meeting_id=args.meeting_id,
                    timeout_seconds=max(45.0, float(args.http_timeout)),
                    poll_interval_seconds=float(args.poll_interval),
                )
                response_statuses["end_meeting"] = end_result.status
                if end_result.status in {200, 202}:
                    meeting_ended = True
                    end_response = end_result.payload
                else:
                    warnings.append(f"best-effort meeting end returned HTTP {end_result.status}")
            except Exception as end_exc:
                warnings.append(f"best-effort meeting end failed: {type(end_exc).__name__}")
        if meeting_created and meeting_ended and not post_end_wait_attempted:
            post_end_wait_attempted = True
            try:
                _, post_end_settled = wait_after_end(
                    client,
                    meeting_id=args.meeting_id,
                    timeout_seconds=float(args.post_end_timeout),
                    poll_interval_seconds=float(args.poll_interval),
                )
                if not post_end_settled:
                    warnings.append("post-end jobs or recording export did not settle before timeout")
            except Exception as post_end_exc:
                warnings.append(f"post-end settlement wait failed: {type(post_end_exc).__name__}")
        if meeting_created:
            try:
                evidence = collect_evidence(client, args.meeting_id)
            except Exception as evidence_exc:
                warnings.append(f"final evidence collection failed: {type(evidence_exc).__name__}")
                if failure is None:
                    classified = (
                        evidence_exc
                        if isinstance(evidence_exc, ReplayFailure)
                        else ReplayFailure("evidence_collection", str(evidence_exc))
                    )
                    failure = {
                        "layer": classified.layer,
                        "error_class": type(evidence_exc).__name__,
                        "message": _sanitize_text(str(classified), (token,))[:1000],
                    }
    if post_end_settled and not snapshot_jobs_settled(evidence.get("snapshot") or {}):
        # Evidence collection is authoritative for the final acceptance
        # decision; a job that reappears or remains active after the poll must
        # not be hidden by an earlier successful wait response.
        post_end_settled = False

    job_audit = build_intelligence_job_audit(evidence["snapshot"], evidence["events"])
    decisions = build_decision_audit(
        evidence["snapshot"],
        evidence["events"],
        job_audit=job_audit,
    )
    e2e_latency = build_e2e_latency_audit(
        evidence["snapshot"],
        evidence["events"],
    )
    evidence_complete = all(
        bool(evidence.get(key))
        for key in ("acceptance_evidence", "snapshot", "transcript", "traces", "slo", "asr_live_events")
    ) and isinstance(evidence.get("events"), list)
    acceptance = evaluate_acceptance(
        procedural_failure=failure,
        runtime_validation=runtime_validation,
        response_statuses=response_statuses,
        ws_stats=ws_stats,
        snapshot=evidence["snapshot"],
        transcript=evidence["transcript"],
        events=evidence["events"],
        evidence_complete=evidence_complete,
        post_end_settled=post_end_settled,
        fixture_sha256=wav_info.sha256 if wav_info is not None else None,
        e2e_latency=e2e_latency,
        atomic_evidence=evidence.get("acceptance_evidence")
        if isinstance(evidence.get("acceptance_evidence"), Mapping)
        else None,
        ui_snapshot=evidence.get("ui_snapshot")
        if isinstance(evidence.get("ui_snapshot"), Mapping)
        else None,
    )
    event_type_counts = Counter(str(event.get("type") or "unknown") for event in evidence["events"])
    decision_outcome_counts = Counter(str(item.get("outcome") or "unknown") for item in decisions)
    job_projection_counts = Counter(str(item.get("projection_status") or "unknown") for item in job_audit)
    latest_job = latest_intelligence_job(evidence["snapshot"])
    latest_e2e_record = next(
        (
            record
            for record in e2e_latency.get("records", [])
            if str(record.get("job_id") or "") == str((latest_job or {}).get("id") or "")
        ),
        None,
    )
    # Retain the historical field name for consumers while sourcing it from
    # durable commit/projection timestamps rather than a socket receive time.
    final_to_applied_ms = (
        latest_e2e_record.get("e2e_latency_ms")
        if isinstance(latest_e2e_record, Mapping)
        else None
    )
    latest_result = latest_intelligence_result(evidence["snapshot"], decisions)
    latest_decision = (
        latest_result.get("decision")
        if isinstance(latest_result.get("decision"), Mapping)
        else None
    )
    latest_historical_applied_decision = (
        latest_applied_decision(
            [
                item
                for item in decisions
                if str(item.get("job_id") or "")
                != str(latest_result.get("job_id") or "")
            ]
        )
        if not latest_result.get("applied")
        else None
    )
    terminal_decision_count = sum(
        1 for item in decisions if item.get("record_type") == "terminal_without_applied_decision"
    )
    metrics = {
        "schema_version": "meeting_copilot.pi_stage0_production_replay.metrics.v1",
        "meeting_id": args.meeting_id,
        "acceptance": acceptance,
        "fixture": asdict(wav_info) if wav_info is not None else None,
        "transport": ws_stats,
        "http": {
            "response_statuses": response_statuses,
            "requests": client.request_log,
        },
        "runtime_validation": runtime_validation,
        "intelligence_poll": intelligence_poll,
        "intelligence": {
            "job_count": len(intelligence_jobs(evidence["snapshot"])),
            "latest_job": latest_job,
            "latest_result": {
                key: value
                for key, value in latest_result.items()
                if key != "decision"
            },
            "final_to_applied_ms": final_to_applied_ms,
            "e2e_latency": e2e_latency,
            "job_audit_count": len(job_audit),
            "job_projection_counts": dict(sorted(job_projection_counts.items())),
        },
        "formal_events": {
            "count": len(evidence["events"]),
            "type_counts": dict(sorted(event_type_counts.items())),
        },
        "transcript": {
            "segment_count": len(evidence["transcript"].get("segments") or []),
            "page_count": evidence["transcript"].get("page_count"),
        },
        "decisions": {
            "count": len(decisions),
            "applied_count": len(decisions) - terminal_decision_count,
            "terminal_without_applied_count": terminal_decision_count,
            "outcome_counts": dict(sorted(decision_outcome_counts.items())),
            "latest": latest_decision,
            "latest_historical_applied": latest_historical_applied_decision,
        },
        "realtime_ai_slo": evidence["slo"],
        "post_end": {
            "settled": post_end_settled,
            "wait_attempted": post_end_wait_attempted,
            "jobs": evidence["snapshot"].get("jobs"),
            "review_jobs": evidence["snapshot"].get("review_jobs"),
            "audio": evidence["snapshot"].get("audio"),
        },
        "warnings": warnings,
        "failure": failure,
        "wall_time_ms": round((time.monotonic() - started_monotonic) * 1_000, 2),
    }

    secret_values = (token,)
    _write_jsonl(output_dir / "asr-ws-events.jsonl", ws_events, secret_values=secret_values)
    _write_jsonl(output_dir / "events.jsonl", evidence["events"], secret_values=secret_values)
    _write_jsonl(output_dir / "decisions.jsonl", decisions, secret_values=secret_values)
    _write_jsonl(output_dir / "jobs.jsonl", job_audit, secret_values=secret_values)
    _write_json(output_dir / "meeting-snapshot.json", evidence["snapshot"], secret_values=secret_values)
    _write_json(output_dir / "acceptance-evidence.json", evidence["acceptance_evidence"], secret_values=secret_values)
    _write_json(output_dir / "meeting-snapshot.pre-end.json", pre_end_snapshot, secret_values=secret_values)
    _write_json(output_dir / "transcript.json", evidence["transcript"], secret_values=secret_values)
    _write_json(output_dir / "semantic-paragraphs.json", evidence["semantic_paragraphs"], secret_values=secret_values)
    _write_json(output_dir / "traces.json", evidence["traces"], secret_values=secret_values)
    _write_json(output_dir / "realtime-ai-slo.json", evidence["slo"], secret_values=secret_values)
    _write_json(output_dir / "asr-live-events.json", evidence["asr_live_events"], secret_values=secret_values)
    _write_json(output_dir / "end-response.json", end_response, secret_values=secret_values)
    _write_json(output_dir / "metrics.json", metrics, secret_values=secret_values)
    notes = build_notes(
        meeting_id=args.meeting_id,
        acceptance=acceptance,
        wav_info=wav_info,
        ws_stats=ws_stats,
        decisions=decisions,
        snapshot=evidence["snapshot"],
        post_end_settled=post_end_settled,
        failure=failure,
        e2e_latency=e2e_latency,
    )
    (output_dir / "notes.md").write_text(
        _sanitize_text(notes, secret_values),
        encoding="utf-8",
    )
    artifact_names = [
        *REQUIRED_ARTIFACTS,
        "meeting-snapshot.pre-end.json",
        "semantic-paragraphs.json",
        "realtime-ai-slo.json",
        "asr-live-events.json",
        "end-response.json",
    ]
    artifact_metadata = _artifact_metadata(output_dir, artifact_names)
    manifest = {
        "schema_version": "meeting_copilot.pi_stage0_production_replay.v1",
        "status": "passed" if acceptance["passed"] else "failed",
        "started_at": started_at,
        "completed_at": _utc_now(),
        "meeting_id": args.meeting_id,
        "source_service": {
            "base_url": client.base_url,
            "auth_enabled": bool(token),
            "auth_token_env": args.token_env if token else None,
        },
        "runtime_validation": runtime_validation,
        "fixture": asdict(wav_info) if wav_info is not None else None,
        "replay": {
            "audio_source": str(args.audio_source),
            "wire_format": "F32LE mono 16000Hz",
            "pace": float(args.pace),
            "chunk_seconds": float(args.chunk_seconds),
            "tail_silence_seconds": float(args.tail_silence_seconds),
        },
        "acceptance": acceptance,
        "failure": failure,
        "warnings": warnings,
        "artifacts": artifact_metadata,
    }
    _write_json(output_dir / "manifest.json", manifest, secret_values=secret_values)

    if token and len(token) >= 8:
        leaked = []
        for path in output_dir.iterdir():
            if path.is_file() and token.encode("utf-8") in path.read_bytes():
                leaked.append(path.name)
        if leaked:
            raise ReplayFailure("artifact_redaction", f"credential leaked into artifacts: {leaked}")
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Running source service, e.g. http://127.0.0.1:8878")
    parser.add_argument("--wav", required=True, type=Path, help="S16LE mono 16 kHz WAV fixture")
    parser.add_argument("--output-dir", required=True, type=Path, help="Absent or empty evidence directory")
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument(
        "--audio-source",
        choices=["simulated_realtime_wav", "browser_live_mic", "speaker_loopback"],
        default="simulated_realtime_wav",
        help="Transport provenance; WAV replay defaults to controlled fixture, not physical microphone",
    )
    parser.add_argument("--pace", type=float, default=1.0, help="1.0 is realtime; 2.0 is twice realtime")
    parser.add_argument("--chunk-seconds", type=float, default=0.3)
    parser.add_argument("--tail-silence-seconds", type=float, default=9.0)
    parser.add_argument("--ready-timeout", type=float, default=90.0)
    parser.add_argument("--finalize-timeout", type=float, default=180.0)
    parser.add_argument("--intelligence-timeout", type=float, default=90.0)
    parser.add_argument("--post-end-timeout", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--http-timeout", type=float, default=30.0)
    parser.add_argument(
        "--token-env",
        default="MEETING_COPILOT_LOCAL_API_TOKEN",
        help="Environment variable containing local API auth; its value is never archived",
    )
    parser.add_argument(
        "--allow-mock-llm",
        action="store_true",
        help="Permit a mock LLM for harness validation; production evidence should omit this flag",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = run_replay(args)
    except ReplayFailure as exc:
        print(
            json.dumps(
                {
                    "status": "failed_before_bundle",
                    "layer": exc.layer,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    summary = {
        "status": manifest["status"],
        "meeting_id": manifest["meeting_id"],
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "coach_outcome": manifest["acceptance"].get("latest_coach_outcome"),
        "failed_checks": list(manifest["acceptance"].get("failed_checks") or []),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
