#!/usr/bin/env python3
"""Build and probe a relocatable, offline macOS backend/FunASR runtime bundle.

This is a local architecture spike. It deliberately does not claim clean-Mac,
licensing, signing, notarization, or public-release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import package_tauri_runtime_app as runtime_packager  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 8
BACKEND_STARTUP_TIMEOUT_SECONDS = 60.0
BACKEND_PROBE_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "macos_bundled_runtime"
DEFAULT_MODEL_DIR = (
    Path.home()
    / ".cache/modelscope/hub/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
)
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNTIME_MANIFEST_RELATIVE = Path("code/desktop_tauri/runtime-bundle-manifest.json")
RUNTIME_MANIFEST_SCHEMA = "meeting_copilot.runtime_bundle.v1"
SOURCE_VENVS = {
    "backend": Path("code/web_mvp/backend/.venv"),
    "funasr": Path("code/asr_runtime/.venv-funasr"),
}
FILE_ASR_MODEL_NAMES = ("offline", "vad", "punc")
NATIVE_MIC_SOURCE_RELATIVE = Path(
    "code/desktop_tauri/native_mic/Sources/MeetingCopilotNativeMic/main.swift"
)
NATIVE_MIC_INFO_PLIST_RELATIVE = Path("code/desktop_tauri/native_mic/Info.plist")
NATIVE_SYSTEM_AUDIO_SOURCE_RELATIVE = Path(
    "code/desktop_tauri/native_system_audio/Sources/MeetingCopilotNativeSystemAudio/main.swift"
)
NATIVE_SYSTEM_AUDIO_INFO_PLIST_RELATIVE = Path(
    "code/desktop_tauri/native_system_audio/Info.plist"
)


def _safe_bundle_path(value: Any, *, field: str) -> str:
    relative = str(value or "").strip()
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"{field} must be a safe bundle-relative path")
    return path.as_posix()


def validate_runtime_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise ValueError("runtime bundle manifest schema is invalid")
    runtimes = payload.get("runtimes")
    if not isinstance(runtimes, dict):
        raise ValueError("runtime bundle manifest runtimes are missing")
    for name in ("backend", "funasr"):
        runtime = runtimes.get(name)
        if not isinstance(runtime, dict) or not re.fullmatch(r"\d+\.\d+", str(runtime.get("python_version") or "")):
            raise ValueError(f"runtime bundle manifest {name} runtime is invalid")
        if "source_venv" in runtime:
            raise ValueError(f"runtime bundle manifest {name} exposes a development venv path")
        for field in ("executable", "venv_executable", "site_packages"):
            runtime[field] = _safe_bundle_path(runtime.get(field), field=f"runtimes.{name}.{field}")
    identity = payload.get("app_identity")
    if not isinstance(identity, dict):
        raise ValueError("runtime bundle manifest app_identity is missing")
    expected_identity = {
        "product_name": "Meeting Copilot",
        "bundle_identifier": "com.meetingcopilot.desktop",
        "app_bundle_name": "Meeting Copilot.app",
        "executable_name": "meeting-copilot-desktop",
    }
    if identity != expected_identity:
        raise ValueError("runtime bundle manifest app_identity is not the fixed product identity")
    workers = payload.get("workers")
    if not isinstance(workers, dict):
        raise ValueError("runtime bundle manifest workers are missing")
    for name in ("realtime", "file_asr", "diarization"):
        workers[name] = _safe_bundle_path(workers.get(name), field=f"workers.{name}")
    diarization = payload.get("diarization")
    if not isinstance(diarization, dict) or diarization.get("optional") is not True:
        raise ValueError("runtime bundle manifest optional diarization component is invalid")
    model_pack = diarization.get("model_pack")
    if not isinstance(model_pack, dict):
        raise ValueError("runtime bundle manifest diarization model pack is missing")
    model_pack["root"] = _safe_bundle_path(
        model_pack.get("root"), field="diarization.model_pack.root"
    )
    model_pack["required_files"] = [
        _safe_bundle_path(value, field="diarization.model_pack.required_files")
        for value in list(model_pack.get("required_files") or [])
    ]
    vad_model = diarization.get("vad_model")
    if not isinstance(vad_model, dict):
        raise ValueError("runtime bundle manifest diarization VAD model is missing")
    vad_model["root"] = _safe_bundle_path(vad_model.get("root"), field="diarization.vad_model.root")
    vad_model["required_files"] = [
        _safe_bundle_path(value, field="diarization.vad_model.required_files")
        for value in list(vad_model.get("required_files") or [])
    ]
    file_asr = payload.get("file_asr")
    if not isinstance(file_asr, dict) or file_asr.get("optional") is not True:
        raise ValueError("runtime bundle manifest optional file_asr component is invalid")
    models = file_asr.get("models")
    if not isinstance(models, dict):
        raise ValueError("runtime bundle manifest file_asr models are missing")
    for name in FILE_ASR_MODEL_NAMES:
        model = models.get(name)
        if not isinstance(model, dict):
            raise ValueError(f"runtime bundle manifest file_asr {name} model is missing")
        model["root"] = _safe_bundle_path(model.get("root"), field=f"file_asr.models.{name}.root")
        model_files = model.get("required_files")
        if not isinstance(model_files, list) or not model_files:
            raise ValueError(f"runtime bundle manifest file_asr {name} required_files are missing")
        model["required_files"] = [
            _safe_bundle_path(value, field=f"file_asr.models.{name}.required_files")
            for value in model_files
        ]
    package = file_asr.get("package")
    if not isinstance(package, dict) or package.get("install_status") not in {
        "not_bundled",
        "bundled",
    }:
        raise ValueError("runtime bundle manifest file_asr package metadata is invalid")
    if package["install_status"] == "bundled":
        if not isinstance(package.get("version"), str) or not package["version"].strip():
            raise ValueError("bundled file_asr package version is missing")
        if not isinstance(package.get("size_bytes"), int) or package["size_bytes"] <= 0:
            raise ValueError("bundled file_asr package size_bytes is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(package.get("sha256") or "")):
            raise ValueError("bundled file_asr package sha256 is invalid")
    required = payload.get("required_files")
    if not isinstance(required, list) or not required:
        raise ValueError("runtime bundle manifest required_files are missing")
    payload["required_files"] = [
        _safe_bundle_path(relative, field="required_files") for relative in required
    ]
    for name in ("backend", "funasr"):
        runtime = runtimes[name]
        if runtime["executable"] not in payload["required_files"]:
            raise ValueError(f"runtime bundle manifest omits {name} executable from required_files")
    for name in ("realtime", "file_asr", "diarization"):
        if workers[name] not in payload["required_files"]:
            raise ValueError(f"runtime bundle manifest omits {name} worker from required_files")
    return runtime_packager.validate_runtime_manifest_contract(payload)


def load_runtime_manifest(repo_root: Path) -> dict[str, Any]:
    path = repo_root / RUNTIME_MANIFEST_RELATIVE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load runtime bundle manifest: {exc}") from exc
    return validate_runtime_manifest(payload)


def load_embedded_runtime_manifest(bundle: Path) -> dict[str, Any]:
    try:
        payload = json.loads((bundle / "runtime-bundle-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load embedded runtime bundle manifest: {exc}") from exc
    return validate_runtime_manifest(payload)


def inspect_file_asr_capability(bundle: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Inspect optional file-ASR assets without turning them into core runtime requirements."""
    components: dict[str, dict[str, Any]] = {}
    runtime_path = bundle / manifest["runtimes"]["funasr"]["executable"]
    worker_path = bundle / manifest["workers"]["file_asr"]
    components["funasr_python"] = {
        "status": "ready" if runtime_path.is_file() else "missing",
        "missing_required_files": [] if runtime_path.is_file() else [
            manifest["runtimes"]["funasr"]["executable"]
        ],
    }
    components["worker"] = {
        "status": "ready" if worker_path.is_file() else "missing",
        "missing_required_files": [] if worker_path.is_file() else [manifest["workers"]["file_asr"]],
    }
    for name in FILE_ASR_MODEL_NAMES:
        spec = manifest["file_asr"]["models"][name]
        root = bundle / spec["root"]
        missing = [relative for relative in spec["required_files"] if not (root / relative).is_file()]
        component_name = f"{name}_model"
        components[component_name] = {
            "status": "ready" if root.is_dir() and not missing else "missing",
            "missing_required_files": missing or ([] if root.is_dir() else [spec["root"]]),
        }
    missing_components = [name for name, item in components.items() if item["status"] != "ready"]
    model_missing = [name for name in missing_components if name.endswith("_model")]
    status = (
        "ready"
        if not missing_components
        else "file_asr_models_not_installed"
        if model_missing and len(model_missing) == len(missing_components)
        else "file_asr_runtime_not_installed"
    )
    return {
        "schema_version": "meeting_copilot.file_asr_bundle_capability.v1",
        "status": status,
        "available": not missing_components,
        "components": components,
        "missing_components": missing_components,
        "optional_component": True,
        "remote_asr_used": False,
        "model_download_performed": False,
    }


def build_and_probe(
    *,
    repo_root: Path,
    output_root: Path,
    run_id: str,
    model_dir: Path,
    file_asr_model_dirs: dict[str, Path] | None = None,
    file_asr_model_pack_version: str | None = None,
    diarization_vad_model_dir: Path | None = None,
    diarization_camplus_model_dir: Path | None = None,
    diarization_model_pack_manifest: Path | None = None,
    asr_timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    manifest = load_runtime_manifest(repo_root)
    backend_runtime = manifest["runtimes"]["backend"]
    funasr_runtime = manifest["runtimes"]["funasr"]
    backend_venv = repo_root / SOURCE_VENVS["backend"]
    funasr_venv = repo_root / SOURCE_VENVS["funasr"]
    file_asr_model_dirs = dict(file_asr_model_dirs or {})
    unknown_file_models = sorted(set(file_asr_model_dirs) - set(FILE_ASR_MODEL_NAMES))
    if unknown_file_models:
        raise ValueError(f"unknown file ASR model components: {', '.join(unknown_file_models)}")
    if file_asr_model_dirs and set(file_asr_model_dirs) != set(FILE_ASR_MODEL_NAMES):
        missing_sources = sorted(set(FILE_ASR_MODEL_NAMES) - set(file_asr_model_dirs))
        raise ValueError(
            "file ASR model pack requires offline, vad, and punc sources together; missing: "
            + ", ".join(missing_sources)
        )
    if file_asr_model_dirs:
        if not file_asr_model_pack_version or not RUN_ID_PATTERN.fullmatch(file_asr_model_pack_version):
            raise ValueError("file ASR model pack requires a safe explicit version")
    elif file_asr_model_pack_version:
        raise ValueError("file ASR model pack version was provided without model sources")
    complete_diarization_inputs = (
        runtime_packager.require_complete_diarization_model_inputs(
            vad_model_dir=diarization_vad_model_dir,
            camplus_model_dir=diarization_camplus_model_dir,
            model_pack_manifest=diarization_model_pack_manifest,
        )
    )
    verified_diarization_pack: dict[str, Any] | None = None
    if complete_diarization_inputs:
        assert diarization_vad_model_dir is not None
        assert diarization_camplus_model_dir is not None
        assert diarization_model_pack_manifest is not None
        verified_diarization_pack = (
            runtime_packager.validate_controlled_diarization_model_pack(
                vad_model_dir=diarization_vad_model_dir,
                camplus_model_dir=diarization_camplus_model_dir,
                model_pack_manifest=diarization_model_pack_manifest,
            )
        )
        runtime_packager.validate_diarization_model_pack_compatibility(
            manifest, verified_diarization_pack
        )
    runtime_packager.reset_diarization_package_metadata(manifest)

    run_root = output_root / run_id
    bundle = run_root / "MeetingCopilotRuntime.bundle"
    if verified_diarization_pack is not None:
        for model in verified_diarization_pack["models"].values():
            source_path = model["source"]
            if source_path == bundle or bundle in source_path.parents:
                raise ValueError(
                    "diarization model source cannot be inside the runtime bundle destination"
                )
    if run_root.exists():
        shutil.rmtree(run_root)
    bundle.mkdir(parents=True)
    preconditions = {
        "backend_venv": backend_venv.is_dir(),
        "backend_python": (backend_venv / "bin/python").is_file(),
        "funasr_venv": funasr_venv.is_dir(),
        "funasr_python": (funasr_venv / "bin/python").is_file(),
        "model_dir": model_dir.is_dir(),
        "model_pt": (model_dir / "model.pt").is_file(),
        "model_config": (model_dir / "config.yaml").is_file(),
        "frontend_dist": (repo_root / "code/web_mvp/frontend_v2/dist/index.html").is_file(),
        "native_mic_source": (repo_root / NATIVE_MIC_SOURCE_RELATIVE).is_file(),
        "native_mic_info_plist": (repo_root / NATIVE_MIC_INFO_PLIST_RELATIVE).is_file(),
        "native_system_audio_source": (
            repo_root / NATIVE_SYSTEM_AUDIO_SOURCE_RELATIVE
        ).is_file(),
        "native_system_audio_info_plist": (
            repo_root / NATIVE_SYSTEM_AUDIO_INFO_PLIST_RELATIVE
        ).is_file(),
        "xcrun": shutil.which("xcrun") is not None,
    }
    for name, source in file_asr_model_dirs.items():
        preconditions[f"file_asr_{name}_model_dir"] = source.is_dir()
        preconditions[f"file_asr_{name}_model_pt"] = (source / "model.pt").is_file()
        preconditions[f"file_asr_{name}_model_config"] = (source / "config.yaml").is_file()
    if not all(preconditions.values()):
        raise RuntimeError(f"bundle preconditions failed: {preconditions}")
    backend_info = python_runtime_info(backend_venv / "bin/python")
    funasr_info = python_runtime_info(funasr_venv / "bin/python")
    actual_versions = {
        "backend": backend_info["version"],
        "funasr": funasr_info["version"],
    }
    expected_versions = {
        "backend": backend_runtime["python_version"],
        "funasr": funasr_runtime["python_version"],
    }
    if actual_versions != expected_versions:
        raise RuntimeError(
            f"runtime Python version mismatch: expected={expected_versions}, actual={actual_versions}"
        )

    clone_tree(Path(backend_info["base_prefix"]), bundle / "runtime/backend-python")
    clone_tree(backend_venv, bundle / "runtime/backend-venv")
    rewrite_venv(
        bundle / "runtime/backend-venv",
        python_directory_name="backend-python",
        version=backend_info["version"],
    )
    clone_tree(Path(funasr_info["base_prefix"]), bundle / "runtime/funasr-python")
    clone_tree(funasr_venv, bundle / "runtime/funasr-venv")
    rewrite_venv(
        bundle / "runtime/funasr-venv",
        python_directory_name="funasr-python",
        version=funasr_info["version"],
    )
    clone_tree(model_dir, bundle / "models/funasr-online")
    for name, source in file_asr_model_dirs.items():
        destination = bundle / manifest["file_asr"]["models"][name]["root"]
        clone_tree(source, destination)
    if file_asr_model_dirs:
        model_pack_root = bundle / "models/funasr-file"
        model_pack_size, model_pack_sha256 = directory_size_and_sha256(model_pack_root)
        manifest["file_asr"]["package"] = {
            "install_status": "bundled",
            "version": file_asr_model_pack_version,
            "size_bytes": model_pack_size,
            "sha256": model_pack_sha256,
        }
    copy_application_sources(repo_root, bundle)
    if verified_diarization_pack is not None:
        runtime_packager.stage_controlled_diarization_model_pack(
            bundle=bundle,
            manifest=manifest,
            verified_pack=verified_diarization_pack,
        )
    write_json(bundle / "runtime-bundle-manifest.json", manifest)
    write_launchers(bundle, manifest=manifest)
    build_native_mic_helper(repo_root, bundle)
    build_native_system_audio_helper(repo_root, bundle)

    links = external_symlinks(bundle)
    file_asr_capability = inspect_file_asr_capability(bundle, manifest)
    diarization_capability = runtime_packager.inspect_diarization_capability(
        bundle, manifest
    )
    probe_parent = Path(tempfile.mkdtemp(prefix=f"meeting-copilot-{run_id}-", dir="/tmp"))
    relocated_bundle = probe_parent / bundle.name
    try:
        clone_tree(bundle, relocated_bundle)
        relocated_links = external_symlinks(relocated_bundle)
        backend_probe = probe_backend(relocated_bundle, probe_parent, repo_root=repo_root)
        asr_probe = probe_funasr(relocated_bundle, probe_parent, timeout_seconds=asr_timeout_seconds)
        native_mic_probe = probe_native_mic(relocated_bundle)
        native_system_audio_probe = probe_native_system_audio(relocated_bundle)
    finally:
        shutil.rmtree(probe_parent, ignore_errors=True)

    decision = spike_decision(
        backend_probe=backend_probe,
        asr_probe=asr_probe,
        native_mic_probe=native_mic_probe,
        native_system_audio_probe=native_system_audio_probe,
        external_link_count=len(links) + len(relocated_links),
    )
    evidence = {
        "schema_version": "meeting_copilot.macos_bundled_runtime_spike.v1",
        "run_id": run_id,
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "bundle_path": str(bundle.relative_to(repo_root)),
        "bundle_logical_size_bytes": directory_size(bundle),
        "preconditions": preconditions,
        "runtime_versions": {
            "backend_python": backend_info["version"],
            "funasr_python": funasr_info["version"],
        },
        "runtime_manifest": manifest,
        "file_asr_capability": file_asr_capability,
        "diarization_capability": diarization_capability,
        "controlled_diarization_model_pack": None
        if verified_diarization_pack is None
        else {
            "pack_id": verified_diarization_pack["pack_id"],
            "version": verified_diarization_pack["version"],
            "manifest_sha256": verified_diarization_pack["manifest_sha256"],
            "verification_status": runtime_packager.DIARIZATION_BUNDLED_STATUS,
            "offline_boundary": verified_diarization_pack["offline_boundary"],
            "redistribution_status": verified_diarization_pack["redistribution"][
                "status"
            ],
            "counts_as_public_release": False,
        },
        "key_artifacts": key_artifacts(bundle),
        "external_symlinks": links,
        "relocated_external_symlinks": relocated_links,
        "backend_probe": backend_probe,
        "asr_probe": asr_probe,
        "native_mic_probe": native_mic_probe,
        "native_system_audio_probe": native_system_audio_probe,
        "decision": decision,
        "privacy_cost_flags": {
            "parent_environment_secrets_inherited": False,
            "configs_local_read": False,
            "remote_service_called": False,
            "remote_asr_called": False,
            "llm_called": False,
            "user_audio_read": False,
        },
        "remaining_blockers": [
            *([] if file_asr_capability["available"] else ["file_asr_models_not_installed"]),
            *(
                ["diarization_model_redistribution_unresolved"]
                if diarization_capability["available"]
                else ["optional_diarization_models_not_bundled"]
            ),
            "not_verified_on_a_separate_clean_mac",
            "model_and_binary_redistribution_not_approved",
            "native_microphone_tauri_ipc_and_ui_not_yet_proven",
            "native_system_audio_real_packaged_permission_gate_not_yet_run",
            "not_signed_or_notarized",
        ],
    }
    evidence_path = run_root / "evidence.json"
    write_json(evidence_path, evidence)
    return evidence | {"evidence_path": str(evidence_path.relative_to(repo_root))}


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    resolved = output_root.resolve() if output_root.is_absolute() else (repo_root / output_root).resolve()
    approved = (repo_root / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must contain only safe ASCII letters, digits, dot, underscore, or hyphen")


def python_runtime_info(python: Path) -> dict[str, str]:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import json,sys; print(json.dumps({'base_prefix':sys.base_prefix,'version':f'{sys.version_info.major}.{sys.version_info.minor}'}))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    return {"base_prefix": str(payload["base_prefix"]), "version": str(payload["version"])}


def clone_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["cp", "-cR", str(source), str(destination)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        shutil.copytree(source, destination, symlinks=True)


def rewrite_venv(venv: Path, *, python_directory_name: str, version: str) -> None:
    bin_dir = venv / "bin"
    for name in ("python", "python3", f"python{version}"):
        path = bin_dir / name
        if path.exists() or path.is_symlink():
            path.unlink()
    (bin_dir / "python").symlink_to(f"../../{python_directory_name}/bin/python{version}")
    (bin_dir / "python3").symlink_to("python")
    (bin_dir / f"python{version}").symlink_to("python")
    (venv / "pyvenv.cfg").write_text(
        "\n".join(
            [
                f"home = ../../{python_directory_name}/bin",
                "implementation = CPython",
                f"version_info = {version}",
                "include-system-site-packages = false",
                "relocatable = true",
                "",
            ]
        ),
        encoding="utf-8",
    )


def copy_application_sources(repo_root: Path, bundle: Path) -> None:
    mappings = (
        (
            repo_root / "code/web_mvp/backend/meeting_copilot_web_mvp",
            bundle / "app/code/web_mvp/backend/meeting_copilot_web_mvp",
        ),
        (
            repo_root / "code/web_mvp/frontend_v2/dist",
            bundle / "app/code/web_mvp/frontend_v2/dist",
        ),
        (
            repo_root / "code/core/meeting_copilot_core",
            bundle / "app/code/core/meeting_copilot_core",
        ),
        (
            repo_root / "code/asr_runtime/scripts",
            bundle / "app/code/asr_runtime/scripts",
        ),
    )
    for source, destination in mappings:
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "artifacts", "outputs", "models"),
        )
    config_root = bundle / "app/configs"
    config_root.mkdir(parents=True)
    for name in ("asr_hotwords.json", "asr_terms.json"):
        shutil.copy2(repo_root / "configs" / name, config_root / name)


def write_launchers(
    bundle: Path,
    *,
    manifest: dict[str, Any],
) -> None:
    manifest = validate_runtime_manifest(manifest)
    backend_version = manifest["runtimes"]["backend"]["python_version"]
    funasr_version = manifest["runtimes"]["funasr"]["python_version"]
    bin_dir = bundle / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    backend = bin_dir / "meeting-copilot-backend"
    backend.write_text(
        """#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHOME="$ROOT/runtime/backend-python"
export PYTHONPATH="$ROOT/runtime/backend-venv/lib/python{backend_version}/site-packages:$ROOT/app/code/web_mvp/backend:$ROOT/app/code/core"
export MEETING_COPILOT_FUNASR_PYTHON="$ROOT/runtime/funasr-python/bin/python{funasr_version}"
export MEETING_COPILOT_FUNASR_PYTHON_HOME="$ROOT/runtime/funasr-python"
export MEETING_COPILOT_FUNASR_PYTHONPATH="$ROOT/runtime/funasr-venv/lib/python{funasr_version}/site-packages:$ROOT/app/code/asr_runtime/scripts"
export MEETING_COPILOT_FUNASR_WORKER="$ROOT/app/code/asr_runtime/scripts/funasr_stream_worker.py"
export MEETING_COPILOT_FUNASR_MODEL_DIR="$ROOT/models/funasr-online"
export MEETING_COPILOT_DIARIZATION_WORKER="$ROOT/app/code/asr_runtime/scripts/funasr_diarization_worker.py"
export MEETING_COPILOT_DIARIZATION_VAD_DIR="$ROOT/models/diarization-vad"
export MEETING_COPILOT_DIARIZATION_CAMPLUS_DIR="$ROOT/models/diarization-camplus"
export MEETING_COPILOT_RUNTIME_MANIFEST="$ROOT/runtime-bundle-manifest.json"
: "${MEETING_COPILOT_DATA_DIR:=$HOME/Library/Application Support/Meeting Copilot}"
export MEETING_COPILOT_DATA_DIR
: "${MEETING_COPILOT_PORT:=8765}"
exec "$ROOT/runtime/backend-python/bin/python{backend_version}" -m uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port "$MEETING_COPILOT_PORT" --timeout-graceful-shutdown {graceful_shutdown_timeout}
""".replace("{backend_version}", backend_version).replace("{funasr_version}", funasr_version).replace("{graceful_shutdown_timeout}", str(GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)),
        encoding="utf-8",
    )
    worker = bin_dir / "meeting-copilot-asr-worker"
    worker.write_text(
        """#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONHOME="$ROOT/runtime/funasr-python"
export PYTHONPATH="$ROOT/runtime/funasr-venv/lib/python{funasr_version}/site-packages:$ROOT/app/code/asr_runtime/scripts"
exec "$ROOT/runtime/funasr-python/bin/python{funasr_version}" "$ROOT/app/code/asr_runtime/scripts/funasr_stream_worker.py" --model "$ROOT/models/funasr-online" "$@"
""".replace("{backend_version}", backend_version).replace("{funasr_version}", funasr_version),
        encoding="utf-8",
    )
    backend.chmod(0o755)
    worker.chmod(0o755)


def native_mic_build_command(repo_root: Path, bundle: Path) -> list[str]:
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        raise RuntimeError(f"unsupported macOS architecture: {architecture}")
    output = bundle / "bin/meeting-copilot-native-mic"
    return [
        "xcrun",
        "swiftc",
        "-swift-version",
        "5",
        "-parse-as-library",
        "-O",
        "-target",
        f"{architecture}-apple-macos13.0",
        "-framework",
        "AVFoundation",
        "-framework",
        "Foundation",
        "-Xlinker",
        "-sectcreate",
        "-Xlinker",
        "__TEXT",
        "-Xlinker",
        "__info_plist",
        "-Xlinker",
        str(repo_root / NATIVE_MIC_INFO_PLIST_RELATIVE),
        str(repo_root / NATIVE_MIC_SOURCE_RELATIVE),
        "-o",
        str(output),
    ]


def build_native_mic_helper(repo_root: Path, bundle: Path) -> None:
    output = bundle / "bin/meeting-copilot-native-mic"
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        native_mic_build_command(repo_root, bundle),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"native microphone helper build failed: {completed.stderr[-2000:]}"
        )
    output.chmod(0o755)


def native_system_audio_build_command(repo_root: Path, bundle: Path) -> list[str]:
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        raise RuntimeError(f"unsupported macOS architecture: {architecture}")
    output = bundle / "bin/meeting-copilot-native-system-audio"
    return [
        "xcrun",
        "swiftc",
        "-swift-version",
        "5",
        "-parse-as-library",
        "-O",
        "-target",
        f"{architecture}-apple-macos13.0",
        "-framework",
        "AVFoundation",
        "-framework",
        "CoreGraphics",
        "-framework",
        "CoreMedia",
        "-framework",
        "Foundation",
        "-framework",
        "ScreenCaptureKit",
        "-Xlinker",
        "-sectcreate",
        "-Xlinker",
        "__TEXT",
        "-Xlinker",
        "__info_plist",
        "-Xlinker",
        str(repo_root / NATIVE_SYSTEM_AUDIO_INFO_PLIST_RELATIVE),
        str(repo_root / NATIVE_SYSTEM_AUDIO_SOURCE_RELATIVE),
        "-o",
        str(output),
    ]


def build_native_system_audio_helper(repo_root: Path, bundle: Path) -> None:
    output = bundle / "bin/meeting-copilot-native-system-audio"
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        native_system_audio_build_command(repo_root, bundle),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"native system audio helper build failed: {completed.stderr[-4000:]}"
        )
    output.chmod(0o755)


def clean_probe_environment(bundle: Path, probe_root: Path) -> dict[str, str]:
    manifest = load_embedded_runtime_manifest(bundle)
    backend_runtime = manifest["runtimes"]["backend"]
    funasr_runtime = manifest["runtimes"]["funasr"]
    home = probe_root / "home"
    data_dir = probe_root / "data"
    cache = probe_root / "model-cache"
    for path in (home, data_dir, cache):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHOME": str(bundle / "runtime/backend-python"),
        "PYTHONPATH": os.pathsep.join(
            [
                str(bundle / backend_runtime["site_packages"]),
                str(bundle / "app/code/web_mvp/backend"),
                str(bundle / "app/code/core"),
            ]
        ),
        "MEETING_COPILOT_DATA_DIR": str(data_dir),
        "MEETING_COPILOT_FUNASR_PYTHON": str(bundle / funasr_runtime["executable"]),
        "MEETING_COPILOT_FUNASR_PYTHON_HOME": str(bundle / "runtime/funasr-python"),
        "MEETING_COPILOT_FUNASR_PYTHONPATH": os.pathsep.join(
            [
                str(bundle / funasr_runtime["site_packages"]),
                str(bundle / "app/code/asr_runtime/scripts"),
            ]
        ),
        "MEETING_COPILOT_FUNASR_WORKER": str(
            bundle / "app/code/asr_runtime/scripts/funasr_stream_worker.py"
        ),
        "MEETING_COPILOT_FUNASR_MODEL_DIR": str(bundle / "models/funasr-online"),
        "MEETING_COPILOT_DIARIZATION_WORKER": str(
            bundle / "app/code/asr_runtime/scripts/funasr_diarization_worker.py"
        ),
        "MEETING_COPILOT_DIARIZATION_VAD_DIR": str(bundle / "models/diarization-vad"),
        "MEETING_COPILOT_DIARIZATION_CAMPLUS_DIR": str(bundle / "models/diarization-camplus"),
        "MEETING_COPILOT_RUNTIME_MANIFEST": str(bundle / "runtime-bundle-manifest.json"),
        "MODELSCOPE_CACHE": str(cache),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
    }


def probe_backend(bundle: Path, probe_root: Path, *, repo_root: Path) -> dict[str, Any]:
    manifest = load_embedded_runtime_manifest(bundle)
    environment = clean_probe_environment(bundle, probe_root)
    python = bundle / manifest["runtimes"]["backend"]["executable"]
    backend_root = bundle / "app/code/web_mvp/backend"
    path_probe = subprocess.run(
        [str(python), "-c", "import json,sys; print(json.dumps(sys.path))"],
        cwd=backend_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    sys_path = json.loads(path_probe.stdout) if path_probe.returncode == 0 else []
    forbidden_source_path_present = any(str(repo_root) in item for item in sys_path)
    port = free_port()
    command = [
        str(python),
        "-m",
        "uvicorn",
        "meeting_copilot_web_mvp.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "error",
        "--timeout-graceful-shutdown",
        str(GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS),
    ]
    process = subprocess.Popen(
        command,
        cwd=backend_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = time.monotonic()
    responses: dict[str, dict[str, Any]] = {}
    try:
        deadline = started + BACKEND_STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            health = http_get(port, "/health")
            if health["status_code"] == 200:
                responses["health"] = health
                responses["workbench"] = http_get(port, "/workbench")
                responses["providers"] = http_get(port, "/providers/health")
                break
            time.sleep(0.1)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    stderr_tail = process.stderr.read()[-2000:] if process.stderr else ""
    passed = (
        responses.get("health", {}).get("status_code") == 200
        and responses.get("workbench", {}).get("status_code") == 200
        and responses.get("providers", {}).get("status_code") == 200
        and not forbidden_source_path_present
    )
    return {
        "status": "passed" if passed else "failed",
        "duration_seconds": round(time.monotonic() - started, 3),
        "responses": responses,
        "sys_path": sys_path,
        "forbidden_source_path_present": forbidden_source_path_present,
        "return_code": process.returncode,
        "stderr_tail": stderr_tail,
    }


def probe_funasr(bundle: Path, probe_root: Path, *, timeout_seconds: float) -> dict[str, Any]:
    manifest = load_embedded_runtime_manifest(bundle)
    funasr_runtime = manifest["runtimes"]["funasr"]
    environment = clean_probe_environment(bundle, probe_root)
    environment.update(
        {
            "PYTHONHOME": str(bundle / "runtime/funasr-python"),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(bundle / funasr_runtime["site_packages"]),
                    str(bundle / "app/code/asr_runtime/scripts"),
                ]
            ),
        }
    )
    command = [
        str(bundle / funasr_runtime["executable"]),
        str(bundle / "app/code/asr_runtime/scripts/funasr_stream_worker.py"),
        "--model",
        str(bundle / "models/funasr-online"),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=bundle,
            env=environment,
            input=b"",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        ready = any(
            isinstance(item, dict) and item.get("event_type") == "ready"
            for item in parse_json_lines(stdout)
        )
        status = "passed" if completed.returncode == 0 and ready else "failed"
        return {
            "status": status,
            "duration_seconds": round(time.monotonic() - started, 3),
            "return_code": completed.returncode,
            "worker_ready": ready,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-4000:],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "status": "timed_out",
            "duration_seconds": round(time.monotonic() - started, 3),
            "return_code": None,
            "worker_ready": False,
            "stdout_tail": _timeout_text(error.stdout)[-2000:],
            "stderr_tail": _timeout_text(error.stderr)[-4000:],
        }


def spike_decision(
    *,
    backend_probe: dict[str, Any],
    asr_probe: dict[str, Any],
    native_mic_probe: dict[str, Any] | None = None,
    native_system_audio_probe: dict[str, Any] | None = None,
    external_link_count: int,
) -> dict[str, Any]:
    ready = (
        backend_probe.get("status") == "passed"
        and asr_probe.get("status") == "passed"
        and (native_mic_probe is None or native_mic_probe.get("status") == "passed")
        and (
            native_system_audio_probe is None
            or native_system_audio_probe.get("status") == "passed"
        )
        and external_link_count == 0
    )
    return {
        "status": (
            "go_local_relocatable_runtime_spike_not_public_release"
            if ready
            else "no_go_local_relocatable_runtime_spike"
        ),
        "counts_as_local_relocation_evidence": ready,
        "counts_as_clean_mac_evidence": False,
        "counts_as_public_release_package": False,
    }


def probe_native_mic(bundle: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(bundle / "bin/meeting-copilot-native-mic"), "--help"],
            cwd=bundle,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "return_code": None,
            "stderr_tail": str(exc),
        }
    help_text = f"{completed.stdout}\n{completed.stderr}"
    passed = completed.returncode == 0 and "meeting-copilot-native-mic" in help_text
    return {
        "status": "passed" if passed else "failed",
        "duration_seconds": round(time.monotonic() - started, 3),
        "return_code": completed.returncode,
        "stdout_tail": completed.stdout[-1000:],
        "stderr_tail": completed.stderr[-1000:],
    }


def probe_native_system_audio(bundle: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(bundle / "bin/meeting-copilot-native-system-audio"), "--describe"],
            cwd=bundle,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "return_code": None,
            "protocol": None,
            "stderr_tail": str(exc),
        }
    try:
        protocol = json.loads(completed.stdout)
    except json.JSONDecodeError:
        protocol = None
    passed = bool(
        completed.returncode == 0
        and isinstance(protocol, dict)
        and protocol.get("schema_version")
        == "meeting_copilot.native_system_audio_protocol.v1"
        and protocol.get("capture_framework") == "ScreenCaptureKit"
        and protocol.get("source") == "system_audio"
        and protocol.get("accepts_remote_websocket") is False
        and protocol.get("raw_audio_files_written") is False
    )
    return {
        "status": "passed" if passed else "failed",
        "duration_seconds": round(time.monotonic() - started, 3),
        "return_code": completed.returncode,
        "protocol": protocol,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-1000:],
        "permission_requested": False,
        "audio_captured": False,
    }


def external_symlinks(root: Path) -> list[str]:
    external: list[str] = []
    for candidate in root.rglob("*"):
        if not candidate.is_symlink():
            continue
        try:
            candidate.resolve(strict=False).relative_to(root.resolve())
        except ValueError:
            external.append(candidate.relative_to(root).as_posix())
    return sorted(external)


def key_artifacts(bundle: Path) -> list[dict[str, Any]]:
    paths = load_embedded_runtime_manifest(bundle)["required_files"]
    artifacts: list[dict[str, Any]] = []
    for relative in paths:
        path = bundle / relative
        target = path.resolve() if path.is_symlink() else path
        artifacts.append(
            {
                "path": relative,
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return artifacts


def directory_size(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            total += candidate.stat().st_size
    return total


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_size_and_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    for candidate in sorted(value for value in path.rglob("*") if value.is_file()):
        relative = candidate.relative_to(path).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                digest.update(chunk)
    return total, digest.hexdigest()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def http_get(port: int, path: str) -> dict[str, Any]:
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            port,
            timeout=BACKEND_PROBE_REQUEST_TIMEOUT_SECONDS,
        )
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(1024 * 1024)
        connection.close()
        return {
            "status_code": response.status,
            "body_size_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
    except OSError:
        return {"status_code": None, "body_size_bytes": 0, "body_sha256": None}


def parse_json_lines(value: str) -> list[Any]:
    parsed: list[Any] = []
    for line in value.splitlines():
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return parsed


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--file-asr-offline-model-dir", type=Path)
    parser.add_argument("--file-asr-vad-model-dir", type=Path)
    parser.add_argument("--file-asr-punc-model-dir", type=Path)
    parser.add_argument("--file-asr-model-pack-version")
    parser.add_argument("--diarization-vad-model-dir", type=Path)
    parser.add_argument("--diarization-camplus-model-dir", type=Path)
    parser.add_argument("--diarization-model-pack-manifest", type=Path)
    parser.add_argument("--asr-timeout-seconds", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    diarization_requested = any(
        value is not None
        for value in (
            args.diarization_vad_model_dir,
            args.diarization_camplus_model_dir,
            args.diarization_model_pack_manifest,
        )
    )
    try:
        file_asr_model_dirs = {
            name: path
            for name, path in {
                "offline": args.file_asr_offline_model_dir,
                "vad": args.file_asr_vad_model_dir,
                "punc": args.file_asr_punc_model_dir,
            }.items()
            if path is not None
        }
        result = build_and_probe(
            repo_root=args.repo_root,
            output_root=args.output_root,
            run_id=args.run_id,
            model_dir=args.model_dir,
            file_asr_model_dirs=file_asr_model_dirs,
            file_asr_model_pack_version=args.file_asr_model_pack_version,
            diarization_vad_model_dir=args.diarization_vad_model_dir,
            diarization_camplus_model_dir=args.diarization_camplus_model_dir,
            diarization_model_pack_manifest=args.diarization_model_pack_manifest,
            asr_timeout_seconds=args.asr_timeout_seconds,
        )
    except Exception as exc:
        result = {
            "schema_version": "meeting_copilot.macos_bundled_runtime_spike.v1",
            "run_id": args.run_id,
            "decision": {
                "status": "no_go_local_relocatable_runtime_spike",
                "counts_as_local_relocation_evidence": False,
                "counts_as_clean_mac_evidence": False,
                "counts_as_public_release_package": False,
            },
            "diarization_capability": {
                "status": runtime_packager.DIARIZATION_INVALID_STATUS,
                "available": False,
                "fail_open": False,
                "invalid_fail_closed": True,
                "recording_and_asr_continue": False,
                "counts_as_public_release": False,
            }
            if diarization_requested
            else None,
            "error": {
                "class": type(exc).__name__,
                "message": str(exc),
            },
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"]["counts_as_local_relocation_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
