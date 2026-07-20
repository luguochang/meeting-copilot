"""Build deterministic, allowlist-only diagnostic bundles.

The module deliberately does not inspect application storage or environment
variables. Callers pass an in-memory snapshot, and only documented aggregate
diagnostic fields can cross the bundle boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from zipfile import ZIP_STORED, ZipFile, ZipInfo


SCHEMA_VERSION = "meeting_copilot.diagnostic_bundle.v1"
MANIFEST_SCHEMA_VERSION = "meeting_copilot.diagnostic_bundle_manifest.v1"
HASH_ALGORITHM = "sha256"
DIAGNOSTICS_NAME = "diagnostics.json"
MANIFEST_NAME = "manifest.json"
MAX_STRING_LENGTH = 256

_VERSION_KEYS = {
    "app_version",
    "architecture",
    "build",
    "build_number",
    "commit",
    "os",
    "os_version",
    "package_version",
    "release_channel",
    "runtime_version",
    "version",
}
_CONFIG_KEYS = {
    "app_mode",
    "asr_provider",
    "environment",
    "feature_flags",
    "language",
    "llm_provider",
    "locale",
    "log_level",
    "microphone_mode",
    "network_mode",
    "provider_mode",
    "recording_enabled",
    "release_channel",
    "retention_days",
    "system_audio_enabled",
}
_CAPABILITY_KEYS = {
    "available",
    "capabilities",
    "configured",
    "connected",
    "endpoint_kind",
    "features",
    "kind",
    "mode",
    "model",
    "protocol",
    "provider",
    "provider_id",
    "reason_code",
    "status",
    "supports_batch",
    "supports_file_asr",
    "supports_json_schema",
    "supports_realtime",
    "supports_streaming",
    "supports_tools",
}
_METRIC_KEYS = {
    "asr_rtf",
    "avg_latency_ms",
    "correction_latency_ms",
    "cancelled_count",
    "current_queue_depth",
    "dropped_frame_count",
    "dropped_frames",
    "error_count",
    "event_to_ui_p95_ms",
    "final_to_event_p95_ms",
    "final_to_first_token_p95_ms",
    "final_latency_ms",
    "first_partial_latency_ms",
    "latency_ms",
    "llm_ttft_ms",
    "max_latency_ms",
    "max_queue_depth",
    "missing_trace_count",
    "observation_count",
    "p50_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
    "queue_depth",
    "queue_depth_max",
    "queue_depth_p95",
    "queue_wait_p95_ms",
    "realtime_factor",
    "recording_gap_count",
    "recording_gap_ms",
    "restart_count",
    "retry_count",
    "rtf",
    "sample_count",
    "provider_total_p95_ms",
    "provider_ttft_p95_ms",
    "timeout_count",
    "ttft_ms",
}
_ERROR_KEYS = {
    "category",
    "code",
    "count",
    "error_type",
    "retryable",
    "stage",
}
_ERROR_IDENTIFIER_KEYS = {"category", "code", "error_type", "stage"}
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "audio",
    "body",
    "content",
    "credential",
    "database",
    "detail",
    "directory",
    "error_message",
    "exception_message",
    "home",
    "message",
    "password",
    "path",
    "pcm",
    "prompt",
    "response",
    "secret",
    "sqlite",
    "stderr",
    "stdout",
    "text",
    "token",
    "traceback",
    "transcript",
    "wav",
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|credential|password|secret|token)\b\s*[:=]\s*\S+"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+\S+")
_API_KEY_SECRET = re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9][A-Za-z0-9._-]*")
_JWT_SECRET = re.compile(r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b")
_URL_QUERY = re.compile(r"(?P<base>https?://[^\s?#]+)(?:\?[^\s#]*)?(?:#[^\s]*)?", re.IGNORECASE)
_PRIVATE_PATH = re.compile(
    r"(?i)(?:file://)?/(?:Users|home|private|tmp|var|Volumes|root|etc|opt|mnt|workspace)/[^\s,;]*"
    r"|[A-Za-z]:[\\/](?:Users[\\/])?[^\s,;]*"
    r"|\\\\[^\\/\s]+[\\/][^\s,;]*"
    r"|(?<!\w)~[\\/][^\s,;]*"
)


def build_diagnostic_report(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic diagnostic report from an untrusted snapshot."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sanitization": {
            "binary_values_included": False,
            "freeform_error_text_included": False,
            "private_paths_included": False,
            "strategy": "strict_allowlist",
        },
        "version": _sanitize_flat_mapping(_first(snapshot, "version", "versions"), _VERSION_KEYS),
        "config_summary": _sanitize_config(_first(snapshot, "config_summary", "configuration", "config")),
        "provider_capabilities": _sanitize_capabilities(
            _first(snapshot, "provider_capabilities", "provider_capability")
        ),
        "stage_metrics": _sanitize_stage_metrics(_first(snapshot, "stage_metrics", "metrics")),
        "errors": _sanitize_errors(snapshot.get("errors")),
    }
    if not report["version"] and isinstance(snapshot.get("version"), str):
        version = _safe_string(snapshot["version"])
        if version is not None:
            report["version"] = {"app_version": version}
    return _drop_tainted_values(report, _collect_tainted_strings(snapshot))


def build_manifest(report_bytes: bytes) -> dict[str, Any]:
    """Describe the deterministic payload without referring to local paths."""

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "entries": [
            {
                "name": DIAGNOSTICS_NAME,
                "sha256": hashlib.sha256(report_bytes).hexdigest(),
                "size_bytes": len(report_bytes),
            }
        ],
        "privacy": {
            "binary_payloads_included": False,
            "database_contents_included": False,
            "freeform_meeting_content_included": False,
            "private_paths_included": False,
            "secret_values_included": False,
        },
    }


def create_diagnostic_bundle(
    snapshot: Mapping[str, Any],
    output_path: str | Path,
) -> dict[str, Any]:
    """Atomically write a deterministic ZIP and its package-level SHA-256."""

    output = Path(output_path)
    if output.suffix.lower() != ".zip":
        raise ValueError("output_path must end in .zip")
    output.parent.mkdir(parents=True, exist_ok=True)

    bundle_bytes = build_diagnostic_bundle_bytes(snapshot)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.write_bytes(bundle_bytes)
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    bundle_sha256 = _sha256_file(output)
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    _write_atomic(checksum_path, f"{bundle_sha256}  {output.name}\n".encode("ascii"))
    return {
        "bundle": output.name,
        "bundle_sha256": bundle_sha256,
        "checksum": checksum_path.name,
        "manifest": MANIFEST_NAME,
        "schema_version": SCHEMA_VERSION,
    }


def build_diagnostic_bundle_bytes(snapshot: Mapping[str, Any]) -> bytes:
    """Return the deterministic archive without persisting user data."""

    report_bytes = canonical_json_bytes(build_diagnostic_report(snapshot))
    manifest_bytes = canonical_json_bytes(build_manifest(report_bytes))
    entries = {
        DIAGNOSTICS_NAME: report_bytes,
        MANIFEST_NAME: manifest_bytes,
    }
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_STORED) as archive:
        for name in sorted(entries):
            archive.writestr(_deterministic_zip_info(name), entries[name])
    return buffer.getvalue()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sanitize_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    sanitized: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = _normalized_key(raw_key)
        if key not in _CONFIG_KEYS:
            continue
        raw_value = value[raw_key]
        if key == "feature_flags":
            feature_flags = _sanitize_feature_flags(raw_value)
            if feature_flags:
                sanitized[key] = feature_flags
            continue
        scalar = _safe_scalar(raw_value)
        if scalar is not None:
            sanitized[key] = scalar
    return sanitized


def _sanitize_feature_flags(value: Any) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        return {}
    flags: dict[str, bool] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = _safe_identifier(raw_key)
        flag = value[raw_key]
        if key is not None and isinstance(flag, bool):
            flags[key] = flag
    return flags


def _sanitize_capabilities(value: Any) -> list[dict[str, Any]]:
    items: list[tuple[str | None, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        if any(_normalized_key(key) in _CAPABILITY_KEYS for key in value):
            items.append((None, value))
        else:
            for raw_kind, raw_item in value.items():
                if isinstance(raw_item, Mapping):
                    items.append((str(raw_kind), raw_item))
    elif _is_sequence(value):
        items.extend((None, item) for item in value if isinstance(item, Mapping))

    sanitized: list[dict[str, Any]] = []
    for fallback_kind, item in items:
        clean: dict[str, Any] = {}
        for raw_key in sorted(item, key=lambda candidate: str(candidate)):
            key = _normalized_key(raw_key)
            if key not in _CAPABILITY_KEYS:
                continue
            raw_value = item[raw_key]
            if key in {"capabilities", "features"}:
                labels = _sanitize_identifier_list(raw_value)
                if labels:
                    clean[key] = labels
            elif isinstance(raw_value, (bool, int, float)) and not isinstance(raw_value, bytes):
                scalar = _safe_scalar(raw_value)
                if scalar is not None:
                    clean[key] = scalar
            else:
                label = _safe_identifier(raw_value)
                if label is not None:
                    clean[key] = label
        if "kind" not in clean and fallback_kind is not None:
            kind = _safe_identifier(fallback_kind)
            if kind is not None:
                clean["kind"] = kind
        if clean:
            sanitized.append(clean)
    return sorted(sanitized, key=_canonical_sort_key)


def _sanitize_stage_metrics(value: Any) -> list[dict[str, Any]]:
    items: list[tuple[str | None, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        if any(_normalized_key(key) in _METRIC_KEYS for key in value):
            items.append(("runtime", value))
        else:
            for raw_stage, raw_item in value.items():
                if isinstance(raw_item, Mapping):
                    items.append((str(raw_stage), raw_item))
    elif _is_sequence(value):
        for item in value:
            if isinstance(item, Mapping):
                stage = item.get("stage", item.get("name"))
                items.append((str(stage) if stage is not None else None, item))

    sanitized: list[dict[str, Any]] = []
    for fallback_stage, item in items:
        stage = _safe_identifier(item.get("stage", item.get("name", fallback_stage)))
        if stage is None:
            continue
        metrics: dict[str, int | float] = {}
        raw_metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else item
        for raw_key in sorted(raw_metrics, key=lambda candidate: str(candidate)):
            key = _normalized_key(raw_key)
            if key not in _METRIC_KEYS:
                continue
            metric = _safe_nonnegative_number(raw_metrics[raw_key])
            if metric is not None:
                metrics[key] = metric
        if metrics:
            sanitized.append({"stage": stage, "metrics": metrics})
    return sorted(sanitized, key=_canonical_sort_key)


def _sanitize_errors(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        items: Sequence[Any] = [value]
    elif _is_sequence(value):
        items = value
    else:
        return []

    sanitized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        clean: dict[str, Any] = {}
        for raw_key in sorted(item, key=lambda candidate: str(candidate)):
            key = _normalized_key(raw_key)
            if key not in _ERROR_KEYS:
                continue
            raw_value = item[raw_key]
            if key in _ERROR_IDENTIFIER_KEYS:
                label = _safe_identifier(raw_value)
                if label is not None:
                    clean[key] = label
            elif key == "retryable" and isinstance(raw_value, bool):
                clean[key] = raw_value
            elif key == "count":
                count = _safe_nonnegative_number(raw_value, integer_only=True)
                if count is not None:
                    clean[key] = count
        if clean:
            sanitized.append(clean)
    return sorted(sanitized, key=_canonical_sort_key)


def _sanitize_flat_mapping(value: Any, allowed_keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    sanitized: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = _normalized_key(raw_key)
        if key not in allowed_keys:
            continue
        scalar = _safe_scalar(value[raw_key])
        if scalar is not None:
            sanitized[key] = scalar
    return sanitized


def _sanitize_identifier_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates: Sequence[Any] = [value]
    elif _is_sequence(value):
        candidates = value
    else:
        return []
    labels = {_safe_identifier(item) for item in candidates}
    return sorted(label for label in labels if label is not None)


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_string(value)
    return None


def _safe_nonnegative_number(value: Any, *, integer_only: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0 or (integer_only and not isinstance(value, int)):
        return None
    return value


def _safe_string(value: str) -> str | None:
    if not value or len(value) > MAX_STRING_LENGTH or any(ord(char) < 32 for char in value):
        return None
    sanitized = _PRIVATE_PATH.sub("<redacted_path>", value)
    sanitized = _URL_QUERY.sub(lambda match: match.group("base"), sanitized)
    sanitized = _SENSITIVE_ASSIGNMENT.sub("<redacted_secret>", sanitized)
    sanitized = _BEARER_SECRET.sub("Bearer <redacted>", sanitized)
    sanitized = _API_KEY_SECRET.sub("<redacted_secret>", sanitized)
    sanitized = _JWT_SECRET.sub("<redacted_secret>", sanitized)
    return sanitized


def _safe_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    sanitized = _safe_string(value)
    if sanitized is None or not _IDENTIFIER.fullmatch(sanitized):
        return None
    lowered = sanitized.casefold()
    if "<redacted" in lowered or lowered.startswith(("bearer", "sk-")):
        return None
    return sanitized


def _collect_tainted_strings(value: Any, *, tainted: bool = False) -> set[str]:
    collected: set[str] = set()
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = _normalized_key(raw_key)
            child_tainted = tainted or any(marker in key for marker in _SENSITIVE_KEY_MARKERS)
            collected.update(_collect_tainted_strings(raw_value, tainted=child_tainted))
        return collected
    if _is_sequence(value):
        for item in value:
            collected.update(_collect_tainted_strings(item, tainted=tainted))
        return collected
    if tainted and isinstance(value, str) and len(value) >= 4:
        collected.add(value)
        bearer = re.fullmatch(r"(?i)Bearer\s+(.+)", value)
        if bearer and len(bearer.group(1)) >= 4:
            collected.add(bearer.group(1))
    elif tainted and isinstance(value, (bytes, bytearray)):
        decoded = bytes(value).decode("utf-8", errors="ignore")
        if len(decoded) >= 4:
            collected.add(decoded)
    return collected


def _drop_tainted_values(value: Any, tainted_strings: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_tainted_values(item, tainted_strings)) is not None
        }
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := _drop_tainted_values(item, tainted_strings)) is not None
        ]
    if isinstance(value, str) and any(tainted in value for tainted in tainted_strings):
        return None
    return value


def _normalized_key(value: Any) -> str:
    key = str(value)
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return key.casefold().replace("-", "_")


def _first(snapshot: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in snapshot:
            return snapshot[key]
    return None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _canonical_sort_key(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(value)


def _deterministic_zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    return info


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
