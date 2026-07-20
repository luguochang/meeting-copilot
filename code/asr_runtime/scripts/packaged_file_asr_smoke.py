#!/usr/bin/env python3
"""Run a real file-ASR smoke through an app resource bundle and direct backend API."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import time
from typing import Any, Mapping
import uuid


RUNTIME_MANIFEST_SCHEMA = "meeting_copilot.runtime_bundle.v1"
FIXTURE_MANIFEST_SCHEMA = "meeting_copilot.file_asr_smoke_fixtures.v1"
EVIDENCE_SCHEMA = "meeting_copilot.packaged_file_asr_smoke.v1"
PACKAGE_EVIDENCE_SCHEMA = "meeting_copilot.tauri_runtime_package.v1"
FORMATS = ("wav", "m4a", "mp3")
SMOKE_NAME = "app resource bundle + direct backend API file ASR smoke"
SMOKE_CLAIM_SCOPE = {
    "app_resource_bundle": True,
    "direct_backend_api": True,
    "tauri_supervisor": False,
    "rust_supervisor": False,
}
_PACKAGED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_CHILD_ENV_BLOCKED_PREFIXES = (
    "MEETING_COPILOT_BATCH_",
    "MEETING_COPILOT_FILE_ASR_",
    "MEETING_COPILOT_FUNASR_",
    "MEETING_COPILOT_ASR_",
)
_CHILD_ENV_BLOCKED_NAMES = frozenset({
    "MEETING_COPILOT_FFMPEG",
    "IMAGEIO_FFMPEG_EXE",
    "PYTHONHOME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "PYTHONUSERBASE",
    "HF_HOME",
    "HF_DATASETS_CACHE",
    "HF_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "MODELSCOPE_CACHE",
    "MODELSCOPE_HOME",
    "TORCH_HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
})
_CHILD_ENV_NETWORK_NAMES = frozenset({
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
})
_REMOTE_ASR_LOG_MARKERS = (
    '"remote_asr_used": true',
    '"remote_asr_used":true',
    '"event": "remote_asr.',
    '"event":"remote_asr.',
    "remote_asr.request",
    "remote-asr request",
    "remote asr request",
)
_FAKE_ASR_LOG_MARKERS = (
    '"fake_asr_used": true',
    '"fake_asr_used":true',
    '"event": "fake_asr.',
    '"event":"fake_asr.',
    "fake_asr.request",
    "fake-asr request",
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ABSOLUTE_PATH_FRAGMENT = re.compile(
    r"(?<![A-Za-z0-9:/])/(?:[^/\s]+/)+[^/\s]+|(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s]+"
)
_APP_BINARY_RELATIVE = Path("Contents/MacOS/meeting-copilot-desktop")
_RUNTIME_MANIFEST_RELATIVE = Path(
    "Contents/Resources/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _blocked_child_environment_name(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(_CHILD_ENV_BLOCKED_PREFIXES) or upper in _CHILD_ENV_BLOCKED_NAMES


def build_packaged_child_environment(
    *,
    base_env: Mapping[str, str],
    runtime_bundle: Path,
    data_dir: Path,
    port: int,
    token: str,
    parent_pid: int | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build a minimal child environment whose ASR paths cannot come from the host."""
    runtime = runtime_bundle.expanduser().resolve()
    data = data_dir.expanduser().resolve()
    for name in ("home", "tmp"):
        (data / name).mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": _PACKAGED_PATH,
        "HOME": str(data / "home"),
        "TMPDIR": str(data / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "MEETING_COPILOT_PORT": str(port),
        "MEETING_COPILOT_DATA_DIR": str(data),
        "MEETING_COPILOT_DESKTOP_RUNTIME": "1",
        "MEETING_COPILOT_RUNTIME_MANIFEST": str(runtime / "runtime-bundle-manifest.json"),
        "MEETING_COPILOT_LOCAL_API_TOKEN": token,
    }
    if parent_pid is not None:
        environment["MEETING_COPILOT_PARENT_PID"] = str(parent_pid)
    removed_parent_names = sorted(name for name in base_env if name not in environment)
    overridden_parent_names = sorted(
        name
        for name, value in base_env.items()
        if name in environment and environment[name] != value
    )
    return environment, {
        "allowlist_only": True,
        "blocked_prefixes": list(_CHILD_ENV_BLOCKED_PREFIXES),
        "blocked_names": sorted(_CHILD_ENV_BLOCKED_NAMES),
        "removed_parent_names": removed_parent_names,
        "overridden_parent_names": overridden_parent_names,
        "effective_names": sorted(environment),
        "network_parent_names_removed": sorted(
            name for name in removed_parent_names if name.upper() in _CHILD_ENV_NETWORK_NAMES
        ),
        "asr_path_parent_names_removed": sorted(
            name for name in removed_parent_names if _blocked_child_environment_name(name)
        ),
    }


def packaged_backend_command(runtime_bundle: Path) -> list[str]:
    launcher = runtime_bundle / "bin/meeting-copilot-backend"
    if not launcher.is_file():
        raise ValueError("packaged backend launcher is missing")
    return ["/bin/sh", str(launcher)]


def report_backend_command(*, app_path: Path, backend_command: list[str]) -> list[str]:
    if len(backend_command) != 2 or backend_command[0] != "/bin/sh":
        raise ValueError("packaged backend command is invalid")
    app = app_path.expanduser().resolve()
    launcher = Path(backend_command[1]).expanduser().resolve(strict=False)
    try:
        relative = launcher.relative_to(app)
    except ValueError as exc:
        raise ValueError("packaged backend command escapes app bundle") from exc
    if not launcher.is_file():
        raise ValueError("packaged backend launcher is missing")
    return [relative.as_posix()]


def packaged_component_paths(runtime_bundle: Path, manifest: Mapping[str, Any]) -> dict[str, Path]:
    file_asr = manifest.get("file_asr") or {}
    paths = {
        "file_asr_runtime": runtime_bundle / _safe_relative(
            file_asr.get("runtime", {}).get("executable"), field="file ASR runtime"
        ),
        "file_asr_worker": runtime_bundle / _safe_relative(
            file_asr.get("worker", {}).get("path"), field="file ASR worker"
        ),
        "converter": runtime_bundle / _safe_relative(
            file_asr.get("converter", {}).get("path"), field="file ASR converter"
        ),
        "converter_license": runtime_bundle / _safe_relative(
            file_asr.get("converter", {}).get("license_path"), field="file ASR converter license"
        ),
    }
    for name, spec in (file_asr.get("models") or {}).items():
        paths[f"model_{name}"] = runtime_bundle / _safe_relative(
            spec.get("root"), field=f"file ASR {name} model"
        )
    bundle_root = runtime_bundle.resolve()
    for name, path in paths.items():
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(bundle_root)
        except ValueError as exc:
            raise ValueError(f"packaged {name} escapes runtime bundle") from exc
        if not path.exists():
            raise ValueError(f"packaged {name} is missing")
    return paths


def _controlled_bundle_path(runtime_bundle: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(runtime_bundle.resolve())
    except ValueError:
        return False
    return path.exists()


def build_network_boundary(
    *,
    port: int,
    child_environment: Mapping[str, str],
    provider_health: Mapping[str, Any],
    runtime_logs: str,
) -> dict[str, Any]:
    remote_observations = [
        line[-400:]
        for line in runtime_logs.splitlines()
        if any(marker in line.lower() for marker in _REMOTE_ASR_LOG_MARKERS)
    ]
    proxy_environment_absent = not any(
        name.upper() in _CHILD_ENV_NETWORK_NAMES for name in child_environment
    )
    offline_environment = all(
        child_environment.get(name) == "1"
        for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    )
    remote_provider = provider_health.get("remote_asr") or {}
    remote_asr_provider_disabled = (
        remote_provider.get("enabled") is False
        and remote_provider.get("default_enabled") is False
        and list(remote_provider.get("providers") or []) == []
    )
    return {
        "mode": "loopback_only_direct_backend_api",
        "client": "http.client.HTTPConnection",
        "allowed_destinations": [{"host": "127.0.0.1", "port": port}],
        "proxy_environment_absent": proxy_environment_absent,
        "offline_environment": offline_environment,
        "remote_asr_provider_disabled": remote_asr_provider_disabled,
        "remote_asr_observations": remote_observations,
        "process_wide_socket_monitoring": "not_performed",
        "verification_scope": "smoke client destination, child environment, and provider status",
        "verification_status": "verified"
        if (
            proxy_environment_absent
            and offline_environment
            and remote_asr_provider_disabled
            and not remote_observations
        )
        else "unverified",
    }


def derive_usage_flags(
    *,
    runtime_bundle: Path,
    component_paths: Mapping[str, Path],
    backend_command: list[str],
    child_environment: Mapping[str, str],
    fixture_policy: Mapping[str, Any],
    provider_health: Mapping[str, Any],
    network_boundary: Mapping[str, Any],
    runtime_logs: str,
    conversion_events: Mapping[str, bool],
) -> dict[str, bool]:
    component_paths_controlled = bool(component_paths) and all(
        _controlled_bundle_path(runtime_bundle, path) for path in component_paths.values()
    )
    command_path = Path(backend_command[-1]) if backend_command else Path()
    command_controlled = (
        backend_command[:1] == ["/bin/sh"]
        and len(backend_command) == 2
        and _controlled_bundle_path(runtime_bundle, command_path)
    )
    child_environment_controlled = (
        child_environment.get("PATH") == _PACKAGED_PATH
        and child_environment.get("HF_HUB_OFFLINE") == "1"
        and child_environment.get("TRANSFORMERS_OFFLINE") == "1"
        and child_environment.get("MEETING_COPILOT_RUNTIME_MANIFEST")
        == str(runtime_bundle / "runtime-bundle-manifest.json")
        and not any(_blocked_child_environment_name(name) for name in child_environment)
    )
    fake_log_observed = any(marker in runtime_logs.lower() for marker in _FAKE_ASR_LOG_MARKERS)
    fake_asr_used = not (
        fixture_policy.get("quality_scope", {}).get("fake_asr_allowed") is False
        and component_paths_controlled
        and command_controlled
        and child_environment_controlled
        and provider_health.get("asr", {}).get("file_provider") == "local_funasr_batch"
        and provider_health.get("asr", {}).get("file_asr_available") is True
        and not fake_log_observed
    )
    remote_boundary_verified = (
        network_boundary.get("verification_status") == "verified"
        and network_boundary.get("proxy_environment_absent") is True
        and network_boundary.get("offline_environment") is True
        and network_boundary.get("remote_asr_provider_disabled") is True
        and all(
            destination.get("host") == "127.0.0.1"
            for destination in network_boundary.get("allowed_destinations") or []
        )
    )
    remote_asr_used = not (
        component_paths_controlled
        and command_controlled
        and child_environment_controlled
        and remote_boundary_verified
        and not network_boundary.get("remote_asr_observations")
        and not any(marker in runtime_logs.lower() for marker in _REMOTE_ASR_LOG_MARKERS)
    )
    converter_controlled = _controlled_bundle_path(
        runtime_bundle, component_paths.get("converter", Path())
    )
    explicit_global_ffmpeg = any(
        name in child_environment for name in ("MEETING_COPILOT_FFMPEG", "IMAGEIO_FFMPEG_EXE")
    )
    bundled_conversion_observed = all(
        bool(conversion_events.get(extension)) for extension in ("m4a", "mp3")
    )
    global_ffmpeg_used = not (
        converter_controlled
        and bundled_conversion_observed
        and not explicit_global_ffmpeg
        and child_environment.get("PATH") == _PACKAGED_PATH
    )
    return {
        "fake_asr_used": bool(fake_asr_used),
        "remote_asr_used": bool(remote_asr_used),
        "global_ffmpeg_used": bool(global_ffmpeg_used),
    }


def app_binary_evidence(app_path: Path) -> dict[str, Any]:
    binary = app_path.expanduser().resolve() / _APP_BINARY_RELATIVE
    result: dict[str, Any] = {
        "path": _APP_BINARY_RELATIVE.as_posix(),
        "sha256": None,
        "verification_status": "unverified",
    }
    if binary.is_file() and not binary.is_symlink():
        result["sha256"] = sha256_file(binary)
        result["verification_status"] = "verified"
    else:
        result["reason"] = "app binary missing or is a symlink"
    return result


def _read_json_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{description} must be a JSON object")
    return payload


def verify_package_evidence(
    *,
    package_evidence_path: Path | None,
    app_path: Path,
    runtime_manifest_path: Path,
) -> dict[str, Any]:
    app = app_path.expanduser().resolve()
    manifest_path = runtime_manifest_path.expanduser().resolve()
    if manifest_path != app / _RUNTIME_MANIFEST_RELATIVE:
        raise ValueError("packaged runtime manifest is not inside the current app")
    manifest_sha256 = sha256_file(manifest_path) if manifest_path.is_file() else None
    binary = app_binary_evidence(app)
    if package_evidence_path is None:
        return {
            "verification_status": "unverified",
            "reason": "package_evidence_not_supplied",
            "run_id": None,
            "app_bundle_name": app.name,
            "app_binary": {
                "path": _APP_BINARY_RELATIVE.as_posix(),
                "sha256": binary.get("sha256"),
            },
            "packaged_runtime_manifest": {
                "path": _RUNTIME_MANIFEST_RELATIVE.as_posix(),
                "sha256": manifest_sha256,
            },
        }

    evidence_path = package_evidence_path.expanduser().resolve()
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("package evidence is missing or is a symlink")
    payload = _read_json_object(evidence_path, description="package evidence")
    if payload.get("schema_version") != PACKAGE_EVIDENCE_SCHEMA:
        raise ValueError("package evidence schema is invalid")

    run_id = str(payload.get("run_id") or "").strip()
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("package evidence run_id is invalid")
    if evidence_path.parent.name != run_id or app.parent != evidence_path.parent:
        raise ValueError("package evidence run_id does not match the current app")

    declared_app_relative = _safe_relative(
        payload.get("app_path"), field="package evidence app path"
    )
    declared_app_path = Path(declared_app_relative)
    fixed_bundle_name = str(
        payload.get("fixed_app_identity", {}).get("app_bundle_name") or ""
    ).strip()
    if declared_app_path.name != app.name or fixed_bundle_name != app.name:
        raise ValueError("package evidence app bundle name does not match the current app")
    if declared_app_path.parent.name != run_id:
        raise ValueError("package evidence run_id does not match its declared app path")

    declared_binary = payload.get("app_binary") or {}
    binary_relative = _safe_relative(
        declared_binary.get("path"), field="package evidence app binary path"
    )
    if binary_relative != _APP_BINARY_RELATIVE.as_posix():
        raise ValueError("package evidence app binary path is invalid")
    declared_binary_sha256 = str(declared_binary.get("sha256") or "").lower()
    if not _SHA256_PATTERN.fullmatch(declared_binary_sha256):
        raise ValueError("package evidence app binary sha256 is invalid")
    if binary.get("verification_status") != "verified" or binary.get("sha256") != declared_binary_sha256:
        raise ValueError("package evidence app binary sha256 mismatch")

    embedded_manifest = payload.get("packaged_runtime_manifest")
    if not isinstance(embedded_manifest, dict):
        raise ValueError("package evidence packaged runtime manifest is missing")
    if embedded_manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise ValueError("package evidence packaged runtime manifest schema is invalid")
    actual_manifest = _read_json_object(
        manifest_path,
        description="packaged runtime manifest",
    )
    expected_manifest_sha256 = canonical_json_sha256(embedded_manifest)
    actual_manifest_sha256 = sha256_file(manifest_path)
    declared_manifest_sha256 = payload.get("packaged_runtime_manifest_sha256")
    if declared_manifest_sha256 is not None:
        declared_manifest_sha256 = str(declared_manifest_sha256).lower()
        if not _SHA256_PATTERN.fullmatch(declared_manifest_sha256):
            raise ValueError("package evidence runtime manifest sha256 is invalid")
    if (
        actual_manifest != embedded_manifest
        or actual_manifest_sha256 != expected_manifest_sha256
        or (
            declared_manifest_sha256 is not None
            and declared_manifest_sha256 != actual_manifest_sha256
        )
    ):
        raise ValueError("package evidence runtime manifest sha256 mismatch")

    package = dict(embedded_manifest.get("file_asr", {}).get("package") or {})
    return {
        "verification_status": "verified",
        "schema_version": PACKAGE_EVIDENCE_SCHEMA,
        "run_id": run_id,
        "app_bundle_name": app.name,
        "package_evidence_sha256": sha256_file(evidence_path),
        "app_binary": {
            "path": _APP_BINARY_RELATIVE.as_posix(),
            "sha256": declared_binary_sha256,
        },
        "packaged_runtime_manifest": {
            "path": _RUNTIME_MANIFEST_RELATIVE.as_posix(),
            "sha256": actual_manifest_sha256,
        },
        "file_asr_package_sha256": package.get("sha256"),
        "file_asr_control_manifest_sha256": package.get("control_manifest_sha256"),
    }


def _report_string_values(value: Any):
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _report_string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _report_string_values(item)
    elif isinstance(value, str):
        yield value


def validate_report_safety(
    report: Mapping[str, Any],
    *,
    forbidden_values: tuple[str, ...] = (),
) -> None:
    for value in _report_string_values(report):
        if (
            value.startswith(("/", "~/"))
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or _ABSOLUTE_PATH_FRAGMENT.search(value)
        ):
            raise ValueError("evidence report contains an absolute path")
        if "bearer " in value.lower():
            raise ValueError("evidence report contains a Bearer secret")
        if any(secret and secret in value for secret in forbidden_values):
            raise ValueError("evidence report contains a runtime secret")


def _write_evidence(
    output: Path,
    evidence: dict[str, Any],
    *,
    forbidden_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    validate_report_safety(evidence, forbidden_values=forbidden_values)
    (output / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


def _safe_relative(value: Any, *, field: str) -> str:
    relative = str(value or "").strip()
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"unsafe packaged {field}")
    return path.as_posix()


def resolve_runtime_bundle(app_path: Path) -> Path:
    app = app_path.expanduser().resolve()
    runtime = app / "Contents/Resources/MeetingCopilotRuntime.bundle"
    if not runtime.is_dir():
        raise ValueError("packaged app runtime bundle is missing")
    return runtime


def load_packaged_manifest(runtime_bundle: Path) -> dict[str, Any]:
    path = runtime_bundle / "runtime-bundle-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("packaged runtime manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise ValueError("packaged runtime manifest schema is invalid")
    if payload.get("component_inventory", {}).get("status") != "sealed":
        raise ValueError("packaged runtime component inventory is not sealed")
    file_asr = payload.get("file_asr") or {}
    package = file_asr.get("package") or {}
    redistribution = file_asr.get("redistribution") or {}
    if package.get("install_status") != "bundled":
        raise ValueError("文件导入组件未安装")
    if package.get("internal_controlled_smoke_status") != "internal_controlled_smoke":
        raise ValueError("packaged file ASR internal controlled smoke is not ready")
    if (
        package.get("redistribution_status") != "public_redistribution_unresolved"
        or package.get("counts_as_public_release") is not False
        or redistribution.get("status") != "public_redistribution_unresolved"
        or redistribution.get("public_redistribution_approved") is not False
    ):
        raise ValueError("packaged file ASR public redistribution boundary is invalid")
    required_paths = [
        file_asr.get("runtime", {}).get("executable"),
        file_asr.get("worker", {}).get("path"),
        file_asr.get("converter", {}).get("path"),
        file_asr.get("converter", {}).get("license_path"),
        *(spec.get("root") for spec in file_asr.get("models", {}).values()),
    ]
    for raw in required_paths:
        relative = _safe_relative(raw, field="file ASR component path")
        if not (runtime_bundle / relative).exists():
            raise ValueError(f"packaged file ASR component is missing: {relative}")
    return payload


def load_controlled_fixtures(
    fixture_manifest: Path,
    supplied: dict[str, Path],
) -> tuple[dict[str, Any], dict[str, Path]]:
    try:
        payload = json.loads(fixture_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("controlled fixture manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != FIXTURE_MANIFEST_SCHEMA:
        raise ValueError("controlled fixture manifest schema is invalid")
    if payload.get("quality_scope", {}).get("fake_asr_allowed") is not False:
        raise ValueError("controlled fixture manifest must forbid fake ASR")
    verified: dict[str, Path] = {}
    for extension in FORMATS:
        path = supplied[extension].expanduser().resolve()
        if path.suffix.lower() != f".{extension}" or not path.is_file() or path.is_symlink():
            raise ValueError(f"controlled {extension} fixture is missing or has the wrong format")
        expected = str(payload.get("fixtures", {}).get(extension, {}).get("sha256") or "")
        if sha256_file(path) != expected:
            raise ValueError(f"controlled {extension} fixture hash mismatch")
        verified[extension] = path
    return payload, verified


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(
    port: int,
    method: str,
    path: str,
    *,
    cookie: str | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Origin": f"http://127.0.0.1:{port}"}
    if cookie:
        headers["Cookie"] = cookie
    if content_type:
        headers["Content-Type"] = content_type
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(8 * 1024 * 1024)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        return {
            "status": response.status,
            "headers": {key.lower(): value for key, value in response.getheaders()},
            "body": parsed,
            "raw": raw,
        }
    finally:
        connection.close()


def _wait_for_health(port: int, process: subprocess.Popen[bytes], timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"packaged backend exited before health: {process.returncode}")
        try:
            response = _request(port, "GET", "/health", timeout=2)
        except OSError:
            time.sleep(0.1)
            continue
        if response["status"] == 200:
            return
        time.sleep(0.1)
    raise TimeoutError("packaged backend health timed out")


def _bootstrap_cookie(port: int, token: str) -> str:
    response = _request(port, "GET", f"/desktop/bootstrap?token={token}")
    raw_cookie = str(response["headers"].get("set-cookie") or "")
    cookie = raw_cookie.split(";", 1)[0]
    if response["status"] != 303 or not cookie:
        raise RuntimeError("packaged backend bootstrap authentication failed")
    return cookie


def _multipart_audio(path: Path, *, title: str) -> tuple[bytes, str]:
    boundary = f"meeting-copilot-{uuid.uuid4().hex}"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\n{title}\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"fixture.{path.suffix.lstrip('.')}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _import_and_wait(
    *,
    port: int,
    cookie: str,
    extension: str,
    fixture: Path,
    timeout: float,
) -> dict[str, Any]:
    body, content_type = _multipart_audio(fixture, title=f"NEXT-022 {extension.upper()} smoke")
    submitted = _request(
        port,
        "POST",
        "/v2/meetings/import-audio",
        cookie=cookie,
        body=body,
        content_type=content_type,
        timeout=30,
    )
    if submitted["status"] != 202 or not isinstance(submitted["body"], dict):
        raise RuntimeError(f"{extension} import submission failed: {submitted['status']}")
    meeting_id = str(submitted["body"].get("meeting_id") or "")
    if not meeting_id:
        raise RuntimeError(f"{extension} import response omitted meeting_id")
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        job_response = _request(
            port,
            "GET",
            f"/v2/meetings/{meeting_id}/import-job",
            cookie=cookie,
            timeout=10,
        )
        if job_response["status"] == 200 and isinstance(job_response["body"], dict):
            latest = job_response["body"].get("import_job")
            if isinstance(latest, dict) and latest.get("status") in {"succeeded", "failed"}:
                break
        time.sleep(0.25)
    if not isinstance(latest, dict) or latest.get("status") != "succeeded":
        raise RuntimeError(f"{extension} packaged file ASR failed: {latest}")
    snapshot_response = _request(
        port,
        "GET",
        f"/v2/meetings/{meeting_id}/snapshot",
        cookie=cookie,
        timeout=30,
    )
    snapshot = snapshot_response["body"] if isinstance(snapshot_response["body"], dict) else {}
    segments = list(snapshot.get("segments") or [])
    texts = [str(segment.get("text") or "").strip() for segment in segments]
    texts = [text for text in texts if text]
    if snapshot_response["status"] != 200 or not texts:
        raise RuntimeError(f"{extension} packaged file ASR produced no persisted transcript")
    return {
        "meeting_id": meeting_id,
        "job_status": latest["status"],
        "job_stage": latest.get("stage"),
        "segment_count": len(texts),
        "texts": texts,
    }


def _sqlite_persistence_evidence(database_path: Path, meeting_ids: list[str]) -> dict[str, Any]:
    if not database_path.is_file():
        raise RuntimeError("packaged backend SQLite database is missing")
    placeholders = ",".join("?" for _ in meeting_ids)
    with sqlite3.connect(database_path) as connection:
        meetings = connection.execute(
            f"SELECT COUNT(*) FROM meetings WHERE id IN ({placeholders})", meeting_ids
        ).fetchone()[0]
        segments = connection.execute(
            f"SELECT COUNT(*) FROM transcript_segments WHERE meeting_id IN ({placeholders})",
            meeting_ids,
        ).fetchone()[0]
        completed_jobs = connection.execute(
            f"SELECT COUNT(*) FROM recording_import_jobs WHERE meeting_id IN ({placeholders}) "
            "AND status = 'succeeded'",
            meeting_ids,
        ).fetchone()[0]
    expected = len(meeting_ids)
    if meetings != expected or completed_jobs != expected or segments < expected:
        raise RuntimeError("packaged file ASR results were not durably persisted")
    return {
        "database_present": True,
        "meeting_rows": meetings,
        "transcript_segment_rows": segments,
        "succeeded_import_job_rows": completed_jobs,
    }


def run_smoke(
    *,
    app_path: Path,
    package_evidence_path: Path | None = None,
    fixture_manifest: Path,
    fixtures: dict[str, Path],
    output_dir: Path,
    per_format_timeout: float = 360,
) -> dict[str, Any]:
    started = time.monotonic()
    app = app_path.expanduser().resolve()
    runtime = resolve_runtime_bundle(app)
    runtime_manifest = load_packaged_manifest(runtime)
    runtime_manifest_path = runtime / "runtime-bundle-manifest.json"
    runtime_manifest_sha256 = sha256_file(runtime_manifest_path)
    output = output_dir.expanduser().resolve()
    if output.exists():
        import shutil

        shutil.rmtree(output)
    output.mkdir(parents=True)
    data_dir = output / "data"
    data_dir.mkdir()
    app_evidence = app_binary_evidence(app)
    package_association = verify_package_evidence(
        package_evidence_path=package_evidence_path,
        app_path=app,
        runtime_manifest_path=runtime_manifest_path,
    )
    if package_association["verification_status"] != "verified":
        return _write_evidence(
            output,
            {
                "schema_version": EVIDENCE_SCHEMA,
                "status": "no_go_package_evidence_unverified",
                "smoke_name": SMOKE_NAME,
                "claim_scope": SMOKE_CLAIM_SCOPE,
                "app_bundle_name": app.name,
                "app_binary": {
                    "path": _APP_BINARY_RELATIVE.as_posix(),
                    "sha256": app_evidence.get("sha256"),
                },
                "runtime_manifest_sha256": runtime_manifest_sha256,
                "package_evidence": package_association,
                "execution": {
                    "controls_verified": False,
                    "usage_flag_policy": "fail_closed_true_when_absence_is_not_verified",
                },
                "counts_as_packaged_file_asr_plumbing_evidence": False,
                "counts_as_real_model_execution_smoke": False,
                "counts_as_model_quality_benchmark": False,
                "duration_seconds": round(time.monotonic() - started, 3),
            },
            forbidden_values=(str(app), str(runtime), str(output)),
        )
    fixture_policy, verified_fixtures = load_controlled_fixtures(fixture_manifest, fixtures)
    stdout_path = output / "backend.stdout.log"
    stderr_path = output / "backend.stderr.log"
    port = reserve_loopback_port()
    token = secrets.token_hex(32)
    backend_command = packaged_backend_command(runtime)
    component_paths = packaged_component_paths(runtime, runtime_manifest)
    environment, child_env_controls = build_packaged_child_environment(
        base_env=os.environ,
        runtime_bundle=runtime,
        data_dir=data_dir,
        port=port,
        token=token,
        parent_pid=os.getpid(),
    )
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            backend_command,
            cwd=runtime,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    try:
        _wait_for_health(port, process)
        cookie = _bootstrap_cookie(port, token)
        provider_health = _request(port, "GET", "/providers/health", cookie=cookie)["body"]
        if not isinstance(provider_health, dict):
            raise RuntimeError("packaged backend provider health is invalid")
        if not bool((provider_health or {}).get("asr", {}).get("file_asr_available")):
            raise RuntimeError("文件导入组件未安装")
        results = {
            extension: _import_and_wait(
                port=port,
                cookie=cookie,
                extension=extension,
                fixture=verified_fixtures[extension],
                timeout=per_format_timeout,
            )
            for extension in FORMATS
        }
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    meeting_ids = [results[extension]["meeting_id"] for extension in FORMATS]
    persistence = _sqlite_persistence_evidence(data_dir / "meeting_copilot.db", meeting_ids)
    runtime_logs = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (stdout_path, stderr_path)
    )
    conversion_count = runtime_logs.count('"event": "ffmpeg.converted"')
    conversion_events = {"m4a": conversion_count >= 1, "mp3": conversion_count >= 2}
    if not all(conversion_events.values()):
        raise RuntimeError("packaged M4A/MP3 conversion was not observed in backend logs")
    network_boundary = build_network_boundary(
        port=port,
        child_environment=environment,
        provider_health=provider_health,
        runtime_logs=runtime_logs,
    )
    usage_flags = derive_usage_flags(
        runtime_bundle=runtime,
        component_paths=component_paths,
        backend_command=backend_command,
        child_environment=environment,
        fixture_policy=fixture_policy,
        provider_health=provider_health,
        network_boundary=network_boundary,
        runtime_logs=runtime_logs,
        conversion_events=conversion_events,
    )
    if any(usage_flags.values()):
        failed_controls = ", ".join(name for name, used in usage_flags.items() if used)
        raise RuntimeError(f"packaged file ASR execution controls were not verified: {failed_controls}")
    component_evidence = {
        name: {
            "path": path.relative_to(runtime).as_posix(),
            "kind": "directory" if path.is_dir() else "file",
            "exists": path.exists(),
            "controlled_by_resource_bundle": _controlled_bundle_path(runtime, path),
            **({
                "sha256": sha256_file(path),
            } if path.is_file() else {}),
        }
        for name, path in component_paths.items()
    }
    reported_backend_command = report_backend_command(
        app_path=app,
        backend_command=backend_command,
    )
    controls_verified = (
        all(item["controlled_by_resource_bundle"] for item in component_evidence.values())
        and child_env_controls["allowlist_only"]
        and network_boundary["verification_status"] == "verified"
        and app_evidence["verification_status"] == "verified"
        and package_association["verification_status"] == "verified"
    )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "status": "passed",
        "smoke_name": SMOKE_NAME,
        "claim_scope": SMOKE_CLAIM_SCOPE,
        "app_bundle_name": app.name,
        "app_binary": {
            "path": _APP_BINARY_RELATIVE.as_posix(),
            "sha256": app_evidence.get("sha256"),
        },
        "runtime_manifest_sha256": runtime_manifest_sha256,
        "file_asr_package": runtime_manifest["file_asr"]["package"],
        "package_evidence": package_association,
        "fixture_set_id": fixture_policy["fixture_set_id"],
        "fixtures": {
            extension: {
                "sha256": fixture_policy["fixtures"][extension]["sha256"],
                "format": extension,
            }
            for extension in FORMATS
        },
        "results": results,
        "conversion_events": conversion_events,
        "persistence": persistence,
        "execution": {
            "component_paths": component_evidence,
            "backend_command": reported_backend_command,
            "child_environment": child_env_controls,
            "provider_health": provider_health,
            "network_boundary": network_boundary,
            "usage_flag_policy": "fail_closed_true_when_absence_is_not_verified",
            "controls_verified": controls_verified,
        },
        **usage_flags,
        "counts_as_packaged_file_asr_plumbing_evidence": bool(
            controls_verified and not any(usage_flags.values())
        ),
        "counts_as_real_model_execution_smoke": bool(
            controls_verified and not any(usage_flags.values())
        ),
        "counts_as_model_quality_benchmark": bool(
            fixture_policy["quality_scope"].get("counts_as_model_quality_benchmark")
        ),
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    return _write_evidence(
        output,
        evidence,
        forbidden_values=(token, cookie, str(app), str(runtime), str(output)),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--package-evidence", type=Path)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--m4a", type=Path, required=True)
    parser.add_argument("--mp3", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-format-timeout", type=float, default=360)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence = run_smoke(
        app_path=args.app,
        package_evidence_path=args.package_evidence,
        fixture_manifest=args.fixture_manifest,
        fixtures={"wav": args.wav, "m4a": args.m4a, "mp3": args.mp3},
        output_dir=args.output_dir,
        per_format_timeout=args.per_format_timeout,
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0 if evidence.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
