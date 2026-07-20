#!/usr/bin/env python3
"""Build a Tauri macOS app with the validated local runtime resource bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import macos_codesign  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "tauri_runtime_package"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNTIME_MANIFEST_SCHEMA = "meeting_copilot.runtime_bundle.v1"
COMPONENT_INVENTORY_SCHEMA = "meeting_copilot.runtime_component_inventory.v1"
CONTROLLED_MODEL_PACK_SCHEMA = "meeting_copilot.controlled_model_pack.v1"
CONTROLLED_DIARIZATION_MODEL_PACK_SCHEMA = (
    "meeting_copilot.controlled_diarization_model_pack.v1"
)
INTERNAL_CONTROLLED_SMOKE_STATUS = "internal_controlled_smoke"
PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS = "public_redistribution_unresolved"
DIARIZATION_ABSENT_STATUS = "absent_optional_fail_open"
DIARIZATION_BUNDLED_STATUS = "bundled_verified"
DIARIZATION_INVALID_STATUS = "invalid_fail_closed"
DIARIZATION_LICENSE_EVIDENCE_SCOPE = (
    "upstream_model_metadata_observed_not_public_redistribution_approval"
)
EXPECTED_APP_IDENTITY = {
    "product_name": "Meeting Copilot",
    "bundle_identifier": "com.meetingcopilot.desktop",
    "app_bundle_name": "Meeting Copilot.app",
    "executable_name": "meeting-copilot-desktop",
}
FILE_ASR_MODEL_NAMES = ("offline", "vad", "punc")
DIARIZATION_MODEL_NAMES = ("vad", "camplus")
DIARIZATION_MODEL_MANIFEST_KEYS = {
    "vad": "vad_model",
    "camplus": "model_pack",
}
FORBIDDEN_RUNTIME_PATH_PARTS = {".cache", ".venv", ".venv-funasr"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MUTABLE_REVISION_NAMES = {"head", "latest", "main", "master", "trunk"}


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    resolved = (
        output_root.resolve()
        if output_root.is_absolute()
        else (repo_root / output_root).resolve()
    )
    approved = (repo_root / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters")


def _safe_bundle_path(value: Any, *, field: str = "runtime path") -> str:
    relative = str(value or "").strip()
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"runtime bundle manifest contains unsafe {field}")
    if any(part.lower() in FORBIDDEN_RUNTIME_PATH_PARTS for part in path.parts):
        raise ValueError(
            f"runtime bundle manifest contains forbidden development/cache {field}"
        )
    return path.as_posix()


def _optional_sha256(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"runtime bundle manifest {field} sha256 is invalid")
    return normalized


def _validate_version(value: Any, *, field: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 128
        or not RUN_ID_PATTERN.fullmatch(normalized)
    ):
        raise ValueError(f"runtime bundle manifest {field} version is invalid")
    return normalized


def _validate_runtime_manifest_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    if "source_venv" in json.dumps(manifest, sort_keys=True):
        raise ValueError(
            "runtime bundle manifest must not expose source_venv development paths"
        )
    if manifest.get("app_identity") != EXPECTED_APP_IDENTITY:
        raise ValueError("runtime bundle manifest fixed app identity is invalid")
    runtimes = manifest.get("runtimes")
    if not isinstance(runtimes, dict):
        raise ValueError("runtime bundle manifest runtimes are missing")
    for runtime_name in ("backend", "funasr"):
        runtime = runtimes.get(runtime_name)
        if not isinstance(runtime, dict):
            raise ValueError(
                f"runtime bundle manifest {runtime_name} runtime is missing"
            )
        for field in ("executable", "venv_executable", "site_packages"):
            runtime[field] = _safe_bundle_path(
                runtime.get(field), field=f"runtimes.{runtime_name}.{field}"
            )
    funasr_runtime = runtimes["funasr"]
    funasr_runtime["component_version"] = _validate_version(
        funasr_runtime.get("component_version"),
        field="runtimes.funasr.component_version",
    )
    funasr_runtime["root"] = _safe_bundle_path(
        funasr_runtime.get("root"), field="runtimes.funasr.root"
    )
    funasr_runtime["sha256"] = _optional_sha256(
        funasr_runtime.get("sha256"), field="runtimes.funasr"
    )
    workers = manifest.get("workers")
    if not isinstance(workers, dict):
        raise ValueError("runtime bundle manifest workers are missing")
    for worker_name in ("realtime", "file_asr", "diarization"):
        workers[worker_name] = _safe_bundle_path(
            workers.get(worker_name), field=f"workers.{worker_name}"
        )
    worker_inventory = manifest.get("worker_inventory")
    if not isinstance(worker_inventory, dict):
        raise ValueError("runtime bundle manifest worker_inventory is missing")
    for worker_name in ("realtime", "file_asr", "diarization"):
        worker = worker_inventory.get(worker_name)
        if not isinstance(worker, dict):
            raise ValueError(
                f"runtime bundle manifest worker_inventory.{worker_name} is missing"
            )
        worker["version"] = _validate_version(
            worker.get("version"), field=f"worker_inventory.{worker_name}"
        )
        worker["path"] = _safe_bundle_path(
            worker.get("path"), field=f"worker_inventory.{worker_name}.path"
        )
        if worker["path"] != workers[worker_name]:
            raise ValueError(
                f"runtime bundle manifest {worker_name} worker paths disagree"
            )
        worker["sha256"] = _optional_sha256(
            worker.get("sha256"), field=f"worker_inventory.{worker_name}"
        )
    realtime_model = manifest.get("realtime_model")
    if not isinstance(realtime_model, dict):
        raise ValueError("runtime bundle manifest realtime_model is missing")
    if not str(realtime_model.get("model_id") or "").strip():
        raise ValueError("runtime bundle manifest realtime model_id is missing")
    realtime_model["version"] = _validate_version(
        realtime_model.get("version"), field="realtime_model"
    )
    realtime_model["root"] = _safe_bundle_path(
        realtime_model.get("root"), field="realtime_model.root"
    )
    realtime_model["sha256"] = _optional_sha256(
        realtime_model.get("sha256"), field="realtime_model"
    )
    diarization = manifest.get("diarization")
    if not isinstance(diarization, dict) or diarization.get("optional") is not True:
        raise ValueError(
            "runtime bundle manifest optional diarization component is invalid"
        )
    if diarization.get("requires_network") is not False:
        raise ValueError(
            "runtime bundle manifest diarization must be explicitly local-only"
        )
    if not str(diarization.get("missing_user_message") or "").strip():
        raise ValueError(
            "runtime bundle manifest diarization missing user message is invalid"
        )
    diarization_worker = diarization.get("worker")
    if not isinstance(diarization_worker, dict):
        raise ValueError("runtime bundle manifest diarization worker is missing")
    diarization_worker["version"] = _validate_version(
        diarization_worker.get("version"), field="diarization.worker"
    )
    diarization_worker["path"] = _safe_bundle_path(
        diarization_worker.get("path"), field="diarization.worker.path"
    )
    if diarization_worker["path"] != workers["diarization"]:
        raise ValueError("runtime bundle manifest diarization worker paths disagree")
    if worker_inventory["diarization"]["path"] != workers["diarization"]:
        raise ValueError(
            "runtime bundle manifest diarization worker inventory paths disagree"
        )

    diarization_package = diarization.get("package")
    if not isinstance(diarization_package, dict):
        raise ValueError(
            "runtime bundle manifest diarization package metadata is missing"
        )
    install_status = diarization_package.get("install_status")
    verification_status = diarization_package.get("verification_status")
    if install_status not in {"not_bundled", "bundled"}:
        raise ValueError(
            "runtime bundle manifest diarization install status is invalid"
        )
    expected_verification = (
        DIARIZATION_BUNDLED_STATUS
        if install_status == "bundled"
        else DIARIZATION_ABSENT_STATUS
    )
    if verification_status != expected_verification:
        raise ValueError(
            "runtime bundle manifest diarization verification status is invalid"
        )
    if diarization_package.get("pack_id") != "diarization-zh-cn":
        raise ValueError("runtime bundle manifest diarization package id is invalid")
    if (
        diarization_package.get("redistribution_status")
        != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
    ):
        raise ValueError(
            "runtime bundle manifest diarization redistribution status is unresolved"
        )
    if diarization_package.get("counts_as_public_release") is not False:
        raise ValueError(
            "runtime bundle manifest diarization cannot count as public release"
        )

    diarization_models: dict[str, dict[str, Any]] = {}
    for model_name, manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.items():
        model = diarization.get(manifest_key)
        if not isinstance(model, dict):
            raise ValueError(
                f"runtime bundle manifest diarization {model_name} model is missing"
            )
        model["root"] = _safe_bundle_path(
            model.get("root"), field=f"diarization.{manifest_key}.root"
        )
        if not str(model.get("model_id") or "").strip():
            raise ValueError(
                f"runtime bundle manifest diarization {model_name} model_id is missing"
            )
        required_files = model.get("required_files")
        if not isinstance(required_files, list) or not required_files:
            raise ValueError(
                f"runtime bundle manifest diarization {model_name} required_files are missing"
            )
        model["required_files"] = [
            _safe_bundle_path(
                value,
                field=f"diarization.{manifest_key}.required_files",
            )
            for value in required_files
        ]
        model["version"] = _validate_version(
            model.get("version"),
            field=f"diarization.{manifest_key}",
            optional=install_status != "bundled",
        )
        model["sha256"] = _optional_sha256(
            model.get("sha256"), field=f"diarization.{manifest_key}"
        )
        model["source_inventory_sha256"] = _optional_sha256(
            model.get("source_inventory_sha256"),
            field=f"diarization.{manifest_key}.source_inventory",
        )
        expected_model_status = (
            "bundled" if install_status == "bundled" else "not_bundled"
        )
        if model.get("install_status") != expected_model_status:
            raise ValueError(
                f"runtime bundle manifest diarization {model_name} install status is invalid"
            )
        if model.get("public_release_approved") is not False:
            raise ValueError(
                f"runtime bundle manifest diarization {model_name} public release flag is invalid"
            )
        if install_status == "bundled":
            if not isinstance(model.get("size_bytes"), int) or model["size_bytes"] <= 0:
                raise ValueError(
                    f"runtime bundle manifest diarization {model_name} size is invalid"
                )
            if model["sha256"] is None or model["source_inventory_sha256"] is None:
                raise ValueError(
                    f"runtime bundle manifest diarization {model_name} inventory is missing"
                )
            provenance = model.get("provenance")
            if not isinstance(provenance, dict):
                raise ValueError(
                    f"runtime bundle manifest diarization {model_name} provenance is missing"
                )
            if provenance.get("public_redistribution_approved") is not False:
                raise ValueError(
                    f"runtime bundle manifest diarization {model_name} provenance is invalid"
                )
        diarization_models[model_name] = model
    if diarization_models["vad"]["root"] == diarization_models["camplus"]["root"]:
        raise ValueError(
            "runtime bundle manifest diarization model roots must be distinct"
        )

    diarization_redistribution = diarization.get("redistribution")
    if not isinstance(diarization_redistribution, dict):
        raise ValueError(
            "runtime bundle manifest diarization redistribution metadata is missing"
        )
    if (
        diarization_redistribution.get("status")
        != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
        or diarization_redistribution.get("public_redistribution_approved") is not False
    ):
        raise ValueError(
            "runtime bundle manifest diarization redistribution is unresolved"
        )
    if install_status == "bundled":
        diarization_package["version"] = _validate_version(
            diarization_package.get("version"), field="diarization.package"
        )
        if (
            not isinstance(diarization_package.get("size_bytes"), int)
            or diarization_package["size_bytes"] <= 0
        ):
            raise ValueError(
                "runtime bundle manifest diarization package size is invalid"
            )
        for field in ("sha256", "control_manifest_sha256"):
            if not SHA256_PATTERN.fullmatch(str(diarization_package.get(field) or "")):
                raise ValueError(
                    f"runtime bundle manifest diarization package {field} is invalid"
                )
        diarization_redistribution["notices_path"] = _safe_bundle_path(
            diarization_redistribution.get("notices_path"),
            field="diarization.redistribution.notices_path",
        )
        if not SHA256_PATTERN.fullmatch(
            str(diarization_redistribution.get("control_manifest_sha256") or "")
        ):
            raise ValueError(
                "runtime bundle manifest diarization redistribution control manifest is invalid"
            )
        license_ids = diarization_redistribution.get("license_ids")
        if (
            not isinstance(license_ids, list)
            or not license_ids
            or any(
                not isinstance(value, str) or not value.strip() for value in license_ids
            )
        ):
            raise ValueError(
                "runtime bundle manifest diarization observed license ids are invalid"
            )
    file_asr = manifest.get("file_asr")
    if not isinstance(file_asr, dict) or file_asr.get("optional") is not True:
        raise ValueError(
            "runtime bundle manifest optional file_asr component is invalid"
        )
    if file_asr.get("requires_network") is not False:
        raise ValueError(
            "runtime bundle manifest file_asr must be explicitly local-only"
        )
    if str(file_asr.get("missing_user_message") or "").strip() != "文件导入组件未安装":
        raise ValueError(
            "runtime bundle manifest file_asr missing user message is invalid"
        )
    file_runtime = file_asr.get("runtime")
    if not isinstance(file_runtime, dict):
        raise ValueError("runtime bundle manifest file_asr runtime is missing")
    file_runtime["version"] = _validate_version(
        file_runtime.get("version"), field="file_asr.runtime"
    )
    for field in ("root", "executable"):
        file_runtime[field] = _safe_bundle_path(
            file_runtime.get(field), field=f"file_asr.runtime.{field}"
        )
    if file_runtime["root"] != funasr_runtime["root"]:
        raise ValueError(
            "runtime bundle manifest realtime and file ASR runtime roots disagree"
        )
    file_runtime["sha256"] = _optional_sha256(
        file_runtime.get("sha256"), field="file_asr.runtime"
    )
    file_worker = file_asr.get("worker")
    if not isinstance(file_worker, dict):
        raise ValueError("runtime bundle manifest file_asr worker is missing")
    file_worker["version"] = _validate_version(
        file_worker.get("version"), field="file_asr.worker"
    )
    file_worker["path"] = _safe_bundle_path(
        file_worker.get("path"), field="file_asr.worker.path"
    )
    if file_worker["path"] != workers["file_asr"]:
        raise ValueError("runtime bundle manifest file_asr worker paths disagree")
    file_worker["sha256"] = _optional_sha256(
        file_worker.get("sha256"), field="file_asr.worker"
    )
    converter = file_asr.get("converter")
    if not isinstance(converter, dict):
        raise ValueError("runtime bundle manifest file_asr converter is missing")
    converter["version"] = _validate_version(
        converter.get("version"), field="file_asr.converter"
    )
    for field in ("path", "license_path"):
        converter[field] = _safe_bundle_path(
            converter.get(field), field=f"file_asr.converter.{field}"
        )
    converter["sha256"] = _optional_sha256(
        converter.get("sha256"), field="file_asr.converter"
    )
    converter_provenance = converter.get("provenance")
    if not isinstance(converter_provenance, dict):
        raise ValueError(
            "runtime bundle manifest file_asr converter provenance is missing"
        )
    if converter_provenance.get("status") != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS:
        raise ValueError(
            "runtime bundle manifest file_asr converter provenance is unresolved"
        )
    if converter_provenance.get("public_redistribution_approved") is not False:
        raise ValueError(
            "runtime bundle manifest file_asr converter public redistribution approval is invalid"
        )
    models = file_asr.get("models")
    if not isinstance(models, dict):
        raise ValueError("runtime bundle manifest file_asr models are missing")
    for model_name in FILE_ASR_MODEL_NAMES:
        model = models.get(model_name)
        if not isinstance(model, dict):
            raise ValueError(
                f"runtime bundle manifest file_asr {model_name} model is missing"
            )
        model["root"] = _safe_bundle_path(
            model.get("root"), field=f"file_asr.models.{model_name}.root"
        )
        if not str(model.get("model_id") or "").strip():
            raise ValueError(
                f"runtime bundle manifest file_asr {model_name} model_id is missing"
            )
        model["version"] = _validate_version(
            model.get("version"), field=f"file_asr.models.{model_name}"
        )
        model["sha256"] = _optional_sha256(
            model.get("sha256"), field=f"file_asr.models.{model_name}"
        )
        required_files = model.get("required_files")
        if not isinstance(required_files, list) or not required_files:
            raise ValueError(
                f"runtime bundle manifest file_asr {model_name} required_files are missing"
            )
        model["required_files"] = [
            _safe_bundle_path(
                value, field=f"file_asr.models.{model_name}.required_files"
            )
            for value in required_files
        ]
    package = file_asr.get("package")
    if not isinstance(package, dict) or package.get("install_status") not in {
        "not_bundled",
        "bundled",
    }:
        raise ValueError("runtime bundle manifest file_asr package metadata is invalid")
    if package.get("counts_as_public_release") is not False:
        raise ValueError(
            "runtime bundle manifest file_asr package public release flag is invalid"
        )
    if package["install_status"] == "bundled":
        if (
            not isinstance(package.get("version"), str)
            or not package["version"].strip()
        ):
            raise ValueError("bundled file_asr package version is missing")
        if not isinstance(package.get("size_bytes"), int) or package["size_bytes"] <= 0:
            raise ValueError("bundled file_asr package size_bytes is invalid")
        if not SHA256_PATTERN.fullmatch(str(package.get("sha256") or "")):
            raise ValueError("bundled file_asr package sha256 is invalid")
        if (
            package.get("redistribution_status")
            != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
        ):
            raise ValueError(
                "bundled file_asr package public redistribution is unresolved"
            )
        if (
            package.get("internal_controlled_smoke_status")
            != INTERNAL_CONTROLLED_SMOKE_STATUS
        ):
            raise ValueError(
                "bundled file_asr package internal smoke status is invalid"
            )
        if not SHA256_PATTERN.fullmatch(
            str(package.get("control_manifest_sha256") or "")
        ):
            raise ValueError("bundled file_asr control manifest sha256 is invalid")
    redistribution = file_asr.get("redistribution")
    if not isinstance(redistribution, dict):
        raise ValueError(
            "runtime bundle manifest file_asr redistribution metadata is missing"
        )
    if package["install_status"] == "bundled":
        if redistribution.get("status") != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS:
            raise ValueError(
                "bundled file_asr public redistribution status is unresolved"
            )
        if redistribution.get("public_redistribution_approved") is not False:
            raise ValueError(
                "bundled file_asr public redistribution approval is invalid"
            )
        for field in ("license_path", "notices_path"):
            redistribution[field] = _safe_bundle_path(
                redistribution.get(field), field=f"file_asr.redistribution.{field}"
            )
        redistribution["control_manifest_sha256"] = _optional_sha256(
            redistribution.get("control_manifest_sha256"),
            field="file_asr.redistribution.control_manifest",
        )
    inventory = manifest.get("component_inventory")
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != COMPONENT_INVENTORY_SCHEMA
    ):
        raise ValueError(
            "runtime bundle manifest component_inventory schema is invalid"
        )
    if inventory.get("status") not in {"unsealed", "sealed"}:
        raise ValueError(
            "runtime bundle manifest component_inventory status is invalid"
        )
    if not isinstance(inventory.get("components"), dict):
        raise ValueError(
            "runtime bundle manifest component_inventory components are invalid"
        )
    return manifest


def validate_runtime_manifest_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    """Public contract used by both runtime construction and final app packaging."""

    return _validate_runtime_manifest_contract(manifest)


def load_runtime_bundle_manifest(bundle: Path) -> dict[str, Any]:
    manifest_path = bundle / "runtime-bundle-manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            "runtime bundle missing required files: runtime-bundle-manifest.json"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("runtime bundle manifest is invalid JSON") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA
    ):
        raise ValueError("runtime bundle manifest schema is invalid")
    required = manifest.get("required_files")
    if not isinstance(required, list) or not required:
        raise ValueError("runtime bundle manifest required_files are missing")
    manifest["required_files"] = [
        _safe_bundle_path(relative, field="required_files") for relative in required
    ]
    return _validate_runtime_manifest_contract(manifest)


def _read_json_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{description} must be a JSON object")
    return payload


def _validate_runtime_tree_boundary(bundle: Path, *, description: str) -> None:
    """Inspect symlinks without descending through symlinked directories."""

    try:
        root = bundle.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{description} is missing") from exc
    if not root.is_dir():
        raise ValueError(f"{description} is missing")

    external_links: list[str] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directory_names, *file_names):
            candidate = current_path / name
            if not candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                external_links.append(candidate.relative_to(root).as_posix())
    if external_links:
        raise ValueError(
            f"{description} contains external symlinks: {', '.join(sorted(external_links))}"
        )


def _resolve_non_symlinked_destination(path: Path) -> Path:
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    candidate = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError(
                "prepared runtime bundle destination contains a symlinked path component"
            )
    return absolute.resolve(strict=False)


def _validate_bundle_mutation_path(
    bundle: Path,
    relative: Any,
    *,
    field: str,
) -> str:
    safe_relative = _safe_bundle_path(relative, field=field)
    candidate = bundle
    for part in Path(safe_relative).parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError(
                f"runtime bundle {field} contains a symlinked path component"
            )
        if not os.path.lexists(candidate):
            break
    return safe_relative


def _validate_runtime_mutation_targets(
    bundle: Path,
    manifest: dict[str, Any],
    *,
    additional_paths: tuple[str, ...] = (),
) -> None:
    targets: list[tuple[Any, str]] = []
    for name, spec in manifest["file_asr"]["models"].items():
        targets.append((spec["root"], f"file_asr.models.{name}.root"))
    for name, manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.items():
        targets.append(
            (
                manifest["diarization"][manifest_key]["root"],
                f"diarization.{name}.root",
            )
        )
    for section in ("file_asr", "diarization"):
        notices_path = manifest[section]["redistribution"].get("notices_path")
        if notices_path:
            targets.append((notices_path, f"{section}.redistribution.notices_path"))
    targets.extend(
        (value, "packaging notice destination") for value in additional_paths
    )
    for relative, field in targets:
        _validate_bundle_mutation_path(bundle, relative, field=field)


def _file_inventory(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            f"runtime component file is missing or is a symlink: {path.name}"
        )
    return {
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _directory_inventory(
    path: Path, *, allowed_root: Path | None = None
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(
            f"runtime component directory is missing or is a symlink: {path.name}"
        )
    allowed = (allowed_root or path).resolve()
    entries: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path).as_posix()
        if candidate.is_symlink():
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(allowed)
            except ValueError as exc:
                raise ValueError(
                    f"runtime component contains external symlink: {relative}"
                ) from exc
            if not resolved.is_file():
                raise ValueError(
                    f"runtime component symlink target is not a file: {relative}"
                )
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size_bytes": resolved.stat().st_size,
                    "sha256": sha256_file(resolved),
                }
            )
        elif candidate.is_file():
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size_bytes": candidate.stat().st_size,
                    "sha256": sha256_file(candidate),
                }
            )
    digest_payload = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "size_bytes": sum(int(item.get("size_bytes") or 0) for item in entries),
        "sha256": hashlib.sha256(digest_payload).hexdigest(),
        "file_count": sum(item.get("kind") == "file" for item in entries),
        "symlink_count": sum(candidate.is_symlink() for candidate in path.rglob("*")),
    }


def _allowlisted_files_inventory(root: Path, files: dict[str, str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for relative, expected_sha256 in sorted(files.items()):
        safe_relative = _safe_bundle_path(relative, field="controlled model pack file")
        path = root / safe_relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"controlled model pack file is missing or is a symlink: {relative}"
            )
        actual_sha256 = sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"controlled model pack file hash mismatch: {relative}")
        entries.append(
            {
                "path": safe_relative,
                "size_bytes": path.stat().st_size,
                "sha256": actual_sha256,
            }
        )
    digest_payload = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "entries": entries,
        "size_bytes": sum(item["size_bytes"] for item in entries),
        "sha256": hashlib.sha256(digest_payload).hexdigest(),
    }


def _sealed_allowlisted_files_inventory(
    root: Path, files: dict[str, str]
) -> dict[str, Any]:
    """Hash an allowlist with the same canonical entries used after bundle staging."""

    allowlisted = _allowlisted_files_inventory(root, files)
    entries = [dict(item, kind="file") for item in allowlisted["entries"]]
    digest_payload = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "entries": entries,
        "size_bytes": allowlisted["size_bytes"],
        "sha256": hashlib.sha256(digest_payload).hexdigest(),
    }


def validate_controlled_model_pack(
    *,
    model_pack_root: Path,
    model_pack_manifest: Path,
) -> dict[str, Any]:
    root = model_pack_root.expanduser().resolve()
    manifest_path = model_pack_manifest.expanduser().resolve()
    payload = _read_json_object(
        manifest_path, description="controlled model pack manifest"
    )
    if payload.get("schema_version") != CONTROLLED_MODEL_PACK_SCHEMA:
        raise ValueError("controlled model pack manifest schema is invalid")
    _validate_version(payload.get("version"), field="controlled model pack")
    if str(payload.get("pack_id") or "").strip() != "file-asr-zh-cn":
        raise ValueError("controlled model pack id is invalid")
    internal_smoke = payload.get("internal_controlled_smoke")
    if not isinstance(internal_smoke, dict):
        raise ValueError("controlled model pack internal smoke status is missing")
    if internal_smoke.get("status") != INTERNAL_CONTROLLED_SMOKE_STATUS:
        raise ValueError("controlled model pack internal smoke status is invalid")
    if internal_smoke.get("counts_as_public_release") is not False:
        raise ValueError(
            "controlled model pack internal smoke cannot count as public release"
        )
    redistribution = payload.get("redistribution")
    if (
        not isinstance(redistribution, dict)
        or redistribution.get("status") != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
    ):
        raise ValueError("controlled model pack public redistribution is unresolved")
    if redistribution.get("public_redistribution_approved") is not False:
        raise ValueError(
            "controlled model pack public redistribution approval is invalid"
        )
    if redistribution.get("license_id") != "Apache-2.0":
        raise ValueError("controlled model pack observed license is invalid")
    license_relative = str(redistribution.get("license_text") or "").strip()
    if not license_relative or Path(license_relative).is_absolute():
        raise ValueError("controlled model pack license path is invalid")
    policy_scope = (
        manifest_path.parent.parent
        if manifest_path.parent.name == "model_packs"
        else manifest_path.parent
    )
    license_path = (manifest_path.parent / license_relative).resolve()
    try:
        license_path.relative_to(policy_scope.resolve())
    except ValueError as exc:
        raise ValueError(
            "controlled model pack license path escapes its policy root"
        ) from exc
    if not license_path.is_file() or license_path.is_symlink():
        raise ValueError("controlled model pack license text is missing")
    expected_license_sha256 = str(redistribution.get("license_sha256") or "").strip()
    if not SHA256_PATTERN.fullmatch(expected_license_sha256):
        raise ValueError("controlled model pack license sha256 is invalid")
    if sha256_file(license_path) != expected_license_sha256:
        raise ValueError("controlled model pack license hash mismatch")
    notice_destination = _safe_bundle_path(
        redistribution.get("notice_destination"),
        field="controlled model pack notice destination",
    )
    models = payload.get("models")
    if not isinstance(models, dict) or set(models) != set(FILE_ASR_MODEL_NAMES):
        raise ValueError(
            "controlled model pack must contain offline, vad, and punc models"
        )
    verified_models: dict[str, dict[str, Any]] = {}
    for name in FILE_ASR_MODEL_NAMES:
        spec = models[name]
        if not isinstance(spec, dict):
            raise ValueError(f"controlled model pack {name} metadata is invalid")
        source_relative = _safe_bundle_path(
            spec.get("source_directory"), field=f"controlled model pack {name} source"
        )
        source = (root / source_relative).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"controlled model pack {name} source escapes pack root"
            ) from exc
        if not source.is_dir() or source.is_symlink():
            raise ValueError(
                f"controlled model pack {name} source directory is missing"
            )
        files = spec.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError(f"controlled model pack {name} file allowlist is missing")
        normalized_files = {
            str(key): str(value).lower() for key, value in files.items()
        }
        if not {"model.pt", "config.yaml"}.issubset(normalized_files):
            raise ValueError(
                f"controlled model pack {name} required runtime files are missing"
            )
        if any(
            not SHA256_PATTERN.fullmatch(value) for value in normalized_files.values()
        ):
            raise ValueError(f"controlled model pack {name} file sha256 is invalid")
        inventory = _allowlisted_files_inventory(source, normalized_files)
        if inventory["size_bytes"] != spec.get("size_bytes"):
            raise ValueError(f"controlled model pack {name} size mismatch")
        if inventory["sha256"] != spec.get("sha256"):
            raise ValueError(f"controlled model pack {name} inventory hash mismatch")
        evidence = spec.get("license_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(
                f"controlled model pack {name} license evidence is missing"
            )
        if evidence.get("scope") != "model_readme_metadata_only":
            raise ValueError(
                f"controlled model pack {name} license evidence scope is not public approval"
            )
        if evidence.get("public_redistribution_approved") is True:
            raise ValueError(
                f"controlled model pack {name} README cannot approve public redistribution"
            )
        evidence_relative = _safe_bundle_path(
            evidence.get("path"), field=f"controlled model pack {name} license evidence"
        )
        evidence_path = source / evidence_relative
        if evidence_path.is_symlink() or not evidence_path.is_file():
            raise ValueError(
                f"controlled model pack {name} license evidence file is missing"
            )
        if sha256_file(evidence_path) != str(evidence.get("sha256") or ""):
            raise ValueError(
                f"controlled model pack {name} license evidence hash mismatch"
            )
        required_text = str(evidence.get("required_text") or "").strip()
        if not required_text or required_text not in evidence_path.read_text(
            encoding="utf-8"
        ):
            raise ValueError(
                f"controlled model pack {name} README license metadata is missing"
            )
        verified_models[name] = {
            **spec,
            "source": source,
            "files": normalized_files,
            "inventory": inventory,
            "license_evidence_path": evidence_path,
        }
    return {
        **payload,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_file(manifest_path),
        "license_path": license_path,
        "notice_destination": notice_destination,
        "models": verified_models,
    }


def _validate_explicit_model_source(source: Path, *, component: str) -> Path:
    expanded = source.expanduser()
    if not expanded.is_absolute():
        raise ValueError(
            f"diarization {component} source directory must be explicit and absolute"
        )
    if expanded.is_symlink():
        raise ValueError(
            f"diarization {component} source directory cannot be a symlink"
        )
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            f"diarization {component} source directory is missing"
        ) from exc
    if not resolved.is_dir():
        raise ValueError(f"diarization {component} source directory is missing")
    symlinks = [
        candidate.relative_to(resolved).as_posix()
        for candidate in resolved.rglob("*")
        if candidate.is_symlink()
    ]
    if symlinks:
        raise ValueError(
            f"diarization {component} source contains symlinks: {', '.join(symlinks)}"
        )
    return resolved


def _validate_provenance_url(value: Any, *, component: str) -> str:
    normalized = str(value or "").strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"diarization {component} provenance URL is invalid")
    return normalized


def require_complete_diarization_model_inputs(
    *,
    vad_model_dir: Path | None,
    camplus_model_dir: Path | None,
    model_pack_manifest: Path | None,
) -> bool:
    values = (vad_model_dir, camplus_model_dir, model_pack_manifest)
    if any(value is not None for value in values) and not all(
        value is not None for value in values
    ):
        raise ValueError(
            "diarization VAD source, CAM++ source, and control manifest must be provided together"
        )
    return all(value is not None for value in values)


def _aggregate_inventory(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entries = [
        {
            "name": name,
            "size_bytes": int(record["size_bytes"]),
            "sha256": str(record["sha256"]),
        }
        for name, record in sorted(records.items())
    ]
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {
        "size_bytes": sum(item["size_bytes"] for item in entries),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def validate_controlled_diarization_model_pack(
    *,
    vad_model_dir: Path,
    camplus_model_dir: Path,
    model_pack_manifest: Path,
) -> dict[str, Any]:
    """Validate an explicit, local-only VAD/CAM++ pair before any bundle copy."""

    sources = {
        "vad": _validate_explicit_model_source(vad_model_dir, component="VAD"),
        "camplus": _validate_explicit_model_source(
            camplus_model_dir, component="CAM++"
        ),
    }
    if (
        sources["vad"] == sources["camplus"]
        or sources["vad"] in sources["camplus"].parents
        or sources["camplus"] in sources["vad"].parents
    ):
        raise ValueError(
            "diarization VAD and CAM++ source directories must be distinct"
        )

    expanded_manifest = model_pack_manifest.expanduser()
    if not expanded_manifest.is_absolute():
        raise ValueError(
            "diarization model pack manifest must be explicit and absolute"
        )
    if expanded_manifest.is_symlink() or not expanded_manifest.is_file():
        raise ValueError("diarization model pack manifest is missing or is a symlink")
    manifest_path = expanded_manifest.resolve()
    payload = _read_json_object(
        manifest_path,
        description="controlled diarization model pack manifest",
    )
    if payload.get("schema_version") != CONTROLLED_DIARIZATION_MODEL_PACK_SCHEMA:
        raise ValueError("controlled diarization model pack manifest schema is invalid")
    if payload.get("pack_id") != "diarization-zh-cn":
        raise ValueError("controlled diarization model pack id is invalid")
    version = _validate_version(
        payload.get("version"), field="controlled diarization model pack"
    )
    offline = payload.get("offline_boundary")
    if offline != {
        "requires_network": False,
        "runtime_downloads_allowed": False,
        "remote_asr_used": False,
    }:
        raise ValueError(
            "controlled diarization model pack offline boundary is invalid"
        )
    verification = payload.get("verification")
    if not isinstance(verification, dict):
        raise ValueError("controlled diarization model pack verification is missing")
    if (
        verification.get("status") != "verified_for_internal_packaging"
        or verification.get("counts_as_public_release") is not False
    ):
        raise ValueError(
            "controlled diarization model pack verification status is invalid"
        )
    redistribution = payload.get("redistribution")
    if (
        not isinstance(redistribution, dict)
        or redistribution.get("status") != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
        or redistribution.get("public_redistribution_approved") is not False
    ):
        raise ValueError(
            "controlled diarization model pack redistribution is unresolved"
        )
    notice_destination = _safe_bundle_path(
        redistribution.get("notice_destination"),
        field="controlled diarization model pack notice destination",
    )
    if not notice_destination.startswith("licenses/models/diarization-"):
        raise ValueError(
            "controlled diarization model pack notice destination is outside its namespace"
        )

    models = payload.get("models")
    if not isinstance(models, dict) or set(models) != set(DIARIZATION_MODEL_NAMES):
        raise ValueError("controlled diarization model pack must contain VAD and CAM++")
    required_by_component = {
        "vad": {"model.pt", "config.yaml"},
        "camplus": {"campplus_cn_common.bin", "config.yaml"},
    }
    verified_models: dict[str, dict[str, Any]] = {}
    for name in DIARIZATION_MODEL_NAMES:
        spec = models[name]
        if not isinstance(spec, dict):
            raise ValueError(f"controlled diarization {name} metadata is invalid")
        model_id = str(spec.get("model_id") or "").strip()
        if (
            not model_id
            or len(model_id) > 256
            or any(value in model_id for value in ("\\", "..", "\n", "\r"))
        ):
            raise ValueError(f"controlled diarization {name} model id is invalid")
        model_version = _validate_version(
            spec.get("version"), field=f"controlled diarization {name}"
        )
        provenance = spec.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"controlled diarization {name} provenance is missing")
        provider = _validate_version(
            provenance.get("provider"), field=f"controlled diarization {name} provider"
        )
        immutable_revision = _validate_version(
            provenance.get("immutable_revision"),
            field=f"controlled diarization {name} immutable revision",
        )
        if immutable_revision.lower() in MUTABLE_REVISION_NAMES:
            raise ValueError(
                f"controlled diarization {name} revision is mutable and cannot be packaged"
            )
        if model_version != immutable_revision:
            raise ValueError(
                f"controlled diarization {name} version and immutable revision disagree"
            )
        source_url = _validate_provenance_url(
            provenance.get("source_url"), component=name
        )
        license_id = _validate_version(
            provenance.get("license_id"),
            field=f"controlled diarization {name} observed license",
        )
        if (
            provenance.get("license_evidence_scope")
            != DIARIZATION_LICENSE_EVIDENCE_SCOPE
        ):
            raise ValueError(
                f"controlled diarization {name} license evidence scope is invalid"
            )
        if provenance.get("public_redistribution_approved") is not False:
            raise ValueError(
                f"controlled diarization {name} provenance cannot approve public redistribution"
            )
        files = spec.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError(f"controlled diarization {name} file allowlist is missing")
        normalized_files = {
            _safe_bundle_path(
                relative, field=f"controlled diarization {name} file"
            ): str(digest).lower()
            for relative, digest in files.items()
        }
        if not required_by_component[name].issubset(normalized_files):
            raise ValueError(
                f"controlled diarization {name} required runtime files are missing"
            )
        if any(
            not SHA256_PATTERN.fullmatch(value) for value in normalized_files.values()
        ):
            raise ValueError(f"controlled diarization {name} file sha256 is invalid")
        inventory = _sealed_allowlisted_files_inventory(sources[name], normalized_files)
        if inventory["size_bytes"] != spec.get("size_bytes"):
            raise ValueError(f"controlled diarization {name} size mismatch")
        if inventory["sha256"] != spec.get("sha256"):
            raise ValueError(f"controlled diarization {name} inventory hash mismatch")
        verified_models[name] = {
            "model_id": model_id,
            "version": model_version,
            "source": sources[name],
            "files": normalized_files,
            "inventory": inventory,
            "provenance": {
                "provider": provider,
                "source_url": source_url,
                "immutable_revision": immutable_revision,
                "license_id": license_id,
                "license_evidence_scope": DIARIZATION_LICENSE_EVIDENCE_SCOPE,
                "public_redistribution_approved": False,
            },
        }
    aggregate = _aggregate_inventory(
        {name: spec["inventory"] for name, spec in verified_models.items()}
    )
    if aggregate["size_bytes"] != payload.get("size_bytes"):
        raise ValueError("controlled diarization model pack size mismatch")
    if aggregate["sha256"] != payload.get("sha256"):
        raise ValueError("controlled diarization model pack inventory hash mismatch")
    return {
        "schema_version": CONTROLLED_DIARIZATION_MODEL_PACK_SCHEMA,
        "pack_id": "diarization-zh-cn",
        "version": version,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_file(manifest_path),
        "notice_destination": notice_destination,
        "offline_boundary": dict(offline),
        "verification": dict(verification),
        "redistribution": dict(redistribution),
        "inventory": aggregate,
        "models": verified_models,
    }


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _sanitized_diarization_control_manifest(
    verified_pack: dict[str, Any],
) -> dict[str, Any]:
    models: dict[str, dict[str, Any]] = {}
    for name in DIARIZATION_MODEL_NAMES:
        model = verified_pack["models"][name]
        models[name] = {
            "model_id": model["model_id"],
            "version": model["version"],
            "provenance": dict(model["provenance"]),
            "files": dict(sorted(model["files"].items())),
            "size_bytes": model["inventory"]["size_bytes"],
            "sha256": model["inventory"]["sha256"],
        }
    return {
        "schema_version": CONTROLLED_DIARIZATION_MODEL_PACK_SCHEMA,
        "pack_id": "diarization-zh-cn",
        "version": verified_pack["version"],
        "size_bytes": verified_pack["inventory"]["size_bytes"],
        "sha256": verified_pack["inventory"]["sha256"],
        "offline_boundary": {
            "requires_network": False,
            "runtime_downloads_allowed": False,
            "remote_asr_used": False,
        },
        "verification": {
            "status": "verified_for_internal_packaging",
            "counts_as_public_release": False,
        },
        "redistribution": {
            "status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
            "notice_destination": verified_pack["notice_destination"],
            "public_redistribution_approved": False,
        },
        "models": models,
    }


def inspect_file_asr_capability(
    bundle: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    component_paths = {
        "funasr_python": (
            manifest["file_asr"]["runtime"]["executable"],
            [],
            "file",
        ),
        "worker": (manifest["workers"]["file_asr"], [], "file"),
    }
    for name in FILE_ASR_MODEL_NAMES:
        model = manifest["file_asr"]["models"][name]
        component_paths[f"{name}_model"] = (
            model["root"],
            list(model["required_files"]),
            "directory",
        )
    components: dict[str, dict[str, Any]] = {}
    for name, (relative, required_files, kind) in component_paths.items():
        path = bundle / relative
        exists = path.is_file() if kind == "file" else path.is_dir()
        missing = [value for value in required_files if not (path / value).is_file()]
        components[name] = {
            "status": "ready" if exists and not missing else "missing",
            "missing_required_files": missing or ([] if exists else [relative]),
        }
    converter = manifest["file_asr"]["converter"]
    converter_path = bundle / converter["path"]
    converter_license = bundle / converter["license_path"]
    converter_ready = converter_path.is_file() and converter_license.is_file()
    components["ffmpeg_converter"] = {
        "status": "ready" if converter_ready else "missing",
        "missing_required_files": [
            *([] if converter_path.is_file() else [converter["path"]]),
            *([] if converter_license.is_file() else [converter["license_path"]]),
        ],
    }
    core_component_names = [
        "funasr_python",
        "worker",
        "offline_model",
        "vad_model",
        "punc_model",
    ]
    missing_components = [
        name for name in core_component_names if components[name]["status"] != "ready"
    ]
    only_models_missing = bool(missing_components) and all(
        name.endswith("_model") for name in missing_components
    )
    core_available = not missing_components
    format_capabilities = {
        "wav": {
            "available": core_available,
            "converter_required": False,
            "missing_components": list(missing_components),
        },
        "m4a": {
            "available": core_available and converter_ready,
            "converter_required": True,
            "missing_components": [
                *missing_components,
                *([] if converter_ready else ["ffmpeg_converter"]),
            ],
        },
        "mp3": {
            "available": core_available and converter_ready,
            "converter_required": True,
            "missing_components": [
                *missing_components,
                *([] if converter_ready else ["ffmpeg_converter"]),
            ],
        },
    }
    status = (
        "ready"
        if core_available and converter_ready
        else "file_asr_converter_not_installed"
        if core_available
        else "file_asr_models_not_installed"
        if only_models_missing
        else "file_asr_runtime_not_installed"
    )
    return {
        "schema_version": "meeting_copilot.file_asr_bundle_capability.v1",
        "status": status,
        "available": core_available and converter_ready,
        "components": components,
        "missing_components": missing_components,
        "optional_component": True,
        "formats": format_capabilities,
        "user_message": None
        if status == "ready"
        else "部分文件格式转换组件未安装"
        if status == "file_asr_converter_not_installed"
        else str(manifest["file_asr"]["missing_user_message"]),
        "requires_network": False,
        "network_offline": None,
        "internal_controlled_smoke_status": INTERNAL_CONTROLLED_SMOKE_STATUS
        if core_available and converter_ready
        else "internal_controlled_smoke_blocked",
        "public_redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "counts_as_public_release": False,
    }


def inspect_diarization_capability(
    bundle: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    diarization = manifest["diarization"]
    component_specs = {
        "funasr_python": (
            manifest["runtimes"]["funasr"]["executable"],
            [],
            "file",
        ),
        "worker": (manifest["workers"]["diarization"], [], "file"),
        "vad_model": (
            diarization["vad_model"]["root"],
            list(diarization["vad_model"]["required_files"]),
            "directory",
        ),
        "camplus_model": (
            diarization["model_pack"]["root"],
            list(diarization["model_pack"]["required_files"]),
            "directory",
        ),
    }
    components: dict[str, dict[str, Any]] = {}
    for name, (relative, required_files, kind) in component_specs.items():
        path = bundle / relative
        physically_present = os.path.lexists(path)
        exists = (
            path.is_file() and not path.is_symlink()
            if kind == "file"
            else path.is_dir() and not path.is_symlink()
        )
        missing = [
            value
            for value in required_files
            if not (path / value).is_file() or (path / value).is_symlink()
        ]
        components[name] = {
            "status": "ready" if exists and not missing else "missing",
            "missing_required_files": missing or ([] if exists else [relative]),
            "physically_present": physically_present,
        }

    model_names = ("vad_model", "camplus_model")
    ready_models = [
        name for name in model_names if components[name]["status"] == "ready"
    ]
    runtime_ready = all(
        components[name]["status"] == "ready" for name in ("funasr_python", "worker")
    )
    package = diarization["package"]
    package_bundled = package["install_status"] == "bundled"
    incomplete_present_models = [
        name
        for name in model_names
        if components[name]["physically_present"]
        and components[name]["status"] != "ready"
    ]
    invalid = bool(incomplete_present_models)
    invalid = invalid or (bool(ready_models) and len(ready_models) != len(model_names))
    invalid = invalid or (len(ready_models) == len(model_names)) != package_bundled
    invalid = invalid or (
        package_bundled
        and package.get("verification_status") != DIARIZATION_BUNDLED_STATUS
    )
    if invalid:
        status = DIARIZATION_INVALID_STATUS
    elif package_bundled and runtime_ready:
        status = DIARIZATION_BUNDLED_STATUS
    elif package_bundled:
        status = "runtime_unavailable_fail_open"
    else:
        status = DIARIZATION_ABSENT_STATUS
    available = status == DIARIZATION_BUNDLED_STATUS
    missing_components = [
        name for name, component in components.items() if component["status"] != "ready"
    ]
    return {
        "schema_version": "meeting_copilot.diarization_bundle_capability.v1",
        "status": status,
        "available": available,
        "optional_component": True,
        "fail_open": not available and status != DIARIZATION_INVALID_STATUS,
        "invalid_fail_closed": status == DIARIZATION_INVALID_STATUS,
        "speaker_attribution_fallback": None if available else "unknown",
        "recording_and_asr_continue": status != DIARIZATION_INVALID_STATUS,
        "components": components,
        "missing_components": missing_components,
        "requires_network": False,
        "runtime_downloads_allowed": False,
        "remote_asr_used": False,
        "verification_status": package.get("verification_status"),
        "public_redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "counts_as_public_release": False,
        "user_message": None if available else str(diarization["missing_user_message"]),
    }


def build_file_asr_package_decision(
    manifest: dict[str, Any], capability: dict[str, Any]
) -> dict[str, Any]:
    file_asr = manifest["file_asr"]
    package = file_asr["package"]
    converter = file_asr["converter"]
    blockers: list[str] = []
    if package.get("install_status") != "bundled":
        blockers.append("file_asr_models_not_bundled")
    for component_name, blocker in (
        ("funasr_python", "file_asr_runtime_missing"),
        ("worker", "file_asr_worker_missing"),
        ("offline_model", "offline_model_missing"),
        ("vad_model", "vad_model_missing"),
        ("punc_model", "punc_model_missing"),
    ):
        if capability["components"][component_name]["status"] != "ready":
            blockers.append(blocker)

    converter_missing_files = capability["components"]["ffmpeg_converter"][
        "missing_required_files"
    ]
    converter_missing = bool(converter_missing_files)
    if converter_missing:
        if converter["path"] in converter_missing_files:
            blockers.append("converter_missing")
        if converter["license_path"] in converter_missing_files:
            blockers.append("converter_license_missing")
    converter_provenance = converter.get("provenance")
    if not isinstance(converter_provenance, dict) or not converter_provenance.get(
        "status"
    ):
        blockers.append("converter_provenance_missing")
    elif (
        converter_provenance.get("status") != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
        or converter_provenance.get("public_redistribution_approved") is not False
    ):
        blockers.append("converter_provenance_invalid")
    if not SHA256_PATTERN.fullmatch(str(package.get("control_manifest_sha256") or "")):
        blockers.append("model_pack_provenance_missing")
    public_status = str(
        file_asr.get("redistribution", {}).get("status")
        or PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS
    )
    if public_status != PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS:
        blockers.append("public_redistribution_status_invalid")

    # One package decision covers all three supported formats. A missing shared
    # converter/license/provenance gate therefore cannot leave a partial Go claim.
    internal_ready = not blockers and capability["available"]
    format_decisions = {
        extension: {
            "runtime_available": bool(capability["formats"][extension]["available"]),
            "package_ready": internal_ready
            and bool(capability["formats"][extension]["available"]),
            "missing_components": list(
                capability["formats"][extension]["missing_components"]
            ),
            "package_blockers": list(blockers),
        }
        for extension in ("wav", "m4a", "mp3")
    }
    return {
        "schema_version": "meeting_copilot.file_asr_package_decision.v1",
        "status": INTERNAL_CONTROLLED_SMOKE_STATUS
        if internal_ready
        else "internal_controlled_smoke_blocked",
        "internal_controlled_smoke": {
            "status": INTERNAL_CONTROLLED_SMOKE_STATUS
            if internal_ready
            else "internal_controlled_smoke_blocked",
            "counts_as_public_release": False,
        },
        "public_redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "counts_as_internal_controlled_smoke": internal_ready,
        "counts_as_public_release": False,
        "formats": format_decisions,
        "blockers": blockers,
    }


def validate_fixed_app_identity(
    repo_root: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    config_path = repo_root / "code/desktop_tauri/src-tauri/tauri.conf.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Tauri fixed identity config: {exc}") from exc
    observed = {
        "product_name": config.get("productName"),
        "bundle_identifier": config.get("identifier"),
        "app_bundle_name": f"{config.get('productName')}.app",
        "executable_name": "meeting-copilot-desktop",
    }
    if observed != EXPECTED_APP_IDENTITY or manifest.get("app_identity") != observed:
        raise ValueError("Tauri and runtime manifest fixed app identities do not match")
    return {
        **observed,
        "configuration_matches": True,
        "stable_install_name": True,
        "stable_signing_identity_verified": False,
    }


def _component_record(
    bundle: Path,
    *,
    relative: str,
    kind: str,
    version: str,
) -> dict[str, Any]:
    path = bundle / relative
    inventory = (
        _file_inventory(path)
        if kind == "file"
        else _directory_inventory(path, allowed_root=bundle)
    )
    return {
        "path": relative,
        "kind": kind,
        "version": version,
        **inventory,
    }


def _verify_sealed_component_inventory(bundle: Path, manifest: dict[str, Any]) -> None:
    inventory = manifest["component_inventory"]
    if inventory.get("status") != "sealed":
        raise ValueError("runtime bundle component inventory is not sealed")
    components = inventory.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError("runtime bundle sealed component inventory is empty")
    for name, expected in components.items():
        if not isinstance(expected, dict):
            raise ValueError(f"runtime bundle component inventory is invalid: {name}")
        relative = _safe_bundle_path(
            expected.get("path"), field=f"component_inventory.{name}.path"
        )
        kind = str(expected.get("kind") or "")
        if kind not in {"file", "directory"}:
            raise ValueError(
                f"runtime bundle component inventory kind is invalid: {name}"
            )
        observed = _component_record(
            bundle,
            relative=relative,
            kind=kind,
            version=str(expected.get("version") or ""),
        )
        if any(
            observed.get(field) != expected.get(field)
            for field in ("size_bytes", "sha256")
        ):
            raise ValueError(f"runtime bundle component hash mismatch: {name}")


def _require_exact_keys(
    payload: dict[str, Any],
    expected: set[str],
    *,
    description: str,
) -> None:
    if set(payload) != expected:
        raise ValueError(f"{description} contains unknown or missing fields")


def _load_packaged_diarization_control_manifest(
    bundle: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    redistribution = manifest["diarization"]["redistribution"]
    control_relative = f"{redistribution['notices_path']}/model-pack.manifest.json"
    control_path = bundle / control_relative
    if control_path.is_symlink() or not control_path.is_file():
        raise ValueError("runtime bundle diarization provenance notice is missing")
    control = _read_json_object(
        control_path,
        description="packaged diarization control manifest",
    )
    if control_path.read_bytes() != _canonical_json_bytes(control):
        raise ValueError("packaged diarization control manifest is not canonical")
    _require_exact_keys(
        control,
        {
            "schema_version",
            "pack_id",
            "version",
            "size_bytes",
            "sha256",
            "offline_boundary",
            "verification",
            "redistribution",
            "models",
        },
        description="packaged diarization control manifest",
    )
    if (
        control["schema_version"] != CONTROLLED_DIARIZATION_MODEL_PACK_SCHEMA
        or control["pack_id"] != "diarization-zh-cn"
    ):
        raise ValueError("packaged diarization control manifest identity is invalid")
    if control["offline_boundary"] != {
        "requires_network": False,
        "runtime_downloads_allowed": False,
        "remote_asr_used": False,
    }:
        raise ValueError(
            "packaged diarization control manifest offline boundary is invalid"
        )
    if control["verification"] != {
        "status": "verified_for_internal_packaging",
        "counts_as_public_release": False,
    }:
        raise ValueError(
            "packaged diarization control manifest verification is invalid"
        )
    control_redistribution = control["redistribution"]
    if not isinstance(control_redistribution, dict):
        raise ValueError(
            "packaged diarization control manifest redistribution is invalid"
        )
    _require_exact_keys(
        control_redistribution,
        {"status", "notice_destination", "public_redistribution_approved"},
        description="packaged diarization control manifest redistribution",
    )
    if control_redistribution != {
        "status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "notice_destination": redistribution["notices_path"],
        "public_redistribution_approved": False,
    }:
        raise ValueError(
            "packaged diarization control manifest redistribution is invalid"
        )

    control_models = control["models"]
    if not isinstance(control_models, dict) or set(control_models) != set(
        DIARIZATION_MODEL_NAMES
    ):
        raise ValueError("packaged diarization control manifest models are invalid")
    for name, model in control_models.items():
        if not isinstance(model, dict):
            raise ValueError(f"packaged diarization control manifest {name} is invalid")
        _require_exact_keys(
            model,
            {"model_id", "version", "provenance", "files", "size_bytes", "sha256"},
            description=f"packaged diarization control manifest {name}",
        )
        provenance = model["provenance"]
        if not isinstance(provenance, dict):
            raise ValueError(
                f"packaged diarization control manifest {name} provenance is invalid"
            )
        _require_exact_keys(
            provenance,
            {
                "provider",
                "source_url",
                "immutable_revision",
                "license_id",
                "license_evidence_scope",
                "public_redistribution_approved",
            },
            description=f"packaged diarization control manifest {name} provenance",
        )
        files = model["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError(
                f"packaged diarization control manifest {name} files are invalid"
            )
        for relative, digest in files.items():
            _safe_bundle_path(
                relative,
                field=f"packaged diarization control manifest {name} file",
            )
            if not SHA256_PATTERN.fullmatch(str(digest)):
                raise ValueError(
                    f"packaged diarization control manifest {name} file hash is invalid"
                )
    return control, control_relative


def _validate_sealed_diarization_bindings(
    bundle: Path,
    manifest: dict[str, Any],
) -> None:
    diarization = manifest["diarization"]
    package = diarization["package"]
    redistribution = diarization["redistribution"]
    control, control_relative = _load_packaged_diarization_control_manifest(
        bundle, manifest
    )
    components = manifest["component_inventory"]["components"]
    control_component = components.get("diarization.control_manifest")
    if not isinstance(control_component, dict):
        raise ValueError(
            "runtime bundle diarization control manifest inventory is missing"
        )

    physical_records: dict[str, dict[str, Any]] = {}
    license_ids: set[str] = set()
    for name, manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.items():
        model = diarization[manifest_key]
        control_model = control["models"][name]
        component = components.get(f"diarization.model.{name}")
        if not isinstance(component, dict):
            raise ValueError(
                f"runtime bundle diarization model inventory is missing: {name}"
            )
        if any(
            control_model[field] != model[field]
            for field in ("model_id", "version", "provenance", "size_bytes", "sha256")
        ):
            raise ValueError(
                f"runtime bundle diarization model metadata mismatch: {name}"
            )
        if model["sha256"] != model["source_inventory_sha256"]:
            raise ValueError(
                f"runtime bundle diarization source inventory mismatch: {name}"
            )
        expected_component = {
            "path": model["root"],
            "kind": "directory",
            "version": model["version"],
            "size_bytes": model["size_bytes"],
            "sha256": model["sha256"],
            "model_id": model["model_id"],
            "source_inventory_sha256": model["source_inventory_sha256"],
            "provenance": model["provenance"],
        }
        if any(
            component.get(field) != value for field, value in expected_component.items()
        ):
            raise ValueError(
                f"runtime bundle diarization model inventory metadata mismatch: {name}"
            )
        allowlisted = _sealed_allowlisted_files_inventory(
            bundle / model["root"],
            {str(key): str(value) for key, value in control_model["files"].items()},
        )
        record = _component_record(
            bundle,
            relative=model["root"],
            kind="directory",
            version=model["version"],
        )
        if any(
            record[field] != allowlisted[field] for field in ("size_bytes", "sha256")
        ):
            raise ValueError(
                f"runtime bundle diarization physical inventory mismatch: {name}"
            )
        if not set(model["required_files"]).issubset(control_model["files"]):
            raise ValueError(
                f"runtime bundle diarization required files are not controlled: {name}"
            )
        physical_records[name] = record
        license_ids.add(str(model["provenance"]["license_id"]))

    aggregate = _aggregate_inventory(physical_records)
    if any(
        package[field] != aggregate[field] or control[field] != aggregate[field]
        for field in ("size_bytes", "sha256")
    ):
        raise ValueError("runtime bundle diarization aggregate package hash mismatch")
    if package["version"] != control["version"]:
        raise ValueError("runtime bundle diarization package version mismatch")
    if redistribution["license_ids"] != sorted(license_ids):
        raise ValueError("runtime bundle diarization observed license ids mismatch")

    control_hash = sha256_file(bundle / control_relative)
    if {
        package["control_manifest_sha256"],
        redistribution["control_manifest_sha256"],
        control_component.get("sha256"),
        control_hash,
    } != {control_hash}:
        raise ValueError("runtime bundle diarization control manifest hash mismatch")
    expected_control_component = {
        "path": control_relative,
        "kind": "file",
        "version": package["version"],
        "pack_id": package["pack_id"],
        "package_size_bytes": aggregate["size_bytes"],
        "package_sha256": aggregate["sha256"],
        "package_control_manifest_sha256": control_hash,
        "redistribution_control_manifest_sha256": control_hash,
    }
    if any(
        control_component.get(field) != value
        for field, value in expected_control_component.items()
    ):
        raise ValueError(
            "runtime bundle diarization control inventory metadata mismatch"
        )


def validate_runtime_bundle(bundle: Path) -> dict[str, Any]:
    manifest = load_runtime_bundle_manifest(bundle)
    _validate_runtime_tree_boundary(bundle, description="runtime bundle")
    required = manifest["required_files"]
    missing = [relative for relative in required if not (bundle / relative).is_file()]
    if missing:
        raise ValueError(f"runtime bundle missing required files: {', '.join(missing)}")
    forbidden_references = []
    for launcher in manifest.get("launchers", {}).values():
        launcher_relative = _safe_bundle_path(launcher, field="launcher")
        launcher_path = bundle / launcher_relative
        if not launcher_path.is_file():
            continue
        text = launcher_path.read_text(encoding="utf-8", errors="replace")
        markers = (
            str(Path.home()),
            "/.cache/modelscope/",
            "/.cache/huggingface/",
            "/.venv",
        )
        if any(marker in text for marker in markers):
            forbidden_references.append(launcher_relative)
    if forbidden_references:
        raise ValueError(
            "runtime bundle launchers contain development repository or user cache paths: "
            + ", ".join(forbidden_references)
        )
    file_asr_capability = inspect_file_asr_capability(bundle, manifest)
    package_status = manifest["file_asr"]["package"]["install_status"]
    models_available = all(
        file_asr_capability["components"][f"{name}_model"]["status"] == "ready"
        for name in FILE_ASR_MODEL_NAMES
    )
    if models_available != (package_status == "bundled"):
        raise ValueError(
            "runtime bundle file_asr assets and package install_status are inconsistent"
        )
    if package_status == "bundled":
        redistribution = manifest["file_asr"]["redistribution"]
        if not (bundle / redistribution["license_path"]).is_file():
            raise ValueError(
                "runtime bundle file_asr redistribution license is missing"
            )
        if not (bundle / redistribution["notices_path"]).is_dir():
            raise ValueError(
                "runtime bundle file_asr redistribution notices are missing"
            )
    diarization_capability = inspect_diarization_capability(bundle, manifest)
    if diarization_capability["invalid_fail_closed"]:
        raise ValueError(
            "runtime bundle diarization assets are partial or inconsistent"
        )
    diarization_package = manifest["diarization"]["package"]
    diarization_models_available = all(
        diarization_capability["components"][name]["status"] == "ready"
        for name in ("vad_model", "camplus_model")
    )
    if diarization_models_available != (
        diarization_package["install_status"] == "bundled"
    ):
        raise ValueError(
            "runtime bundle diarization assets and package install_status are inconsistent"
        )
    if diarization_package["install_status"] == "bundled":
        notice_root = bundle / manifest["diarization"]["redistribution"]["notices_path"]
        if notice_root.is_symlink() or not notice_root.is_dir():
            raise ValueError("runtime bundle diarization provenance notice is missing")
    _verify_sealed_component_inventory(bundle, manifest)
    if diarization_package["install_status"] == "bundled":
        _validate_sealed_diarization_bindings(bundle, manifest)
    return manifest


def build_overlay(bundle: Path, overlay_path: Path) -> dict[str, Any]:
    overlay = {
        "bundle": {
            "resources": {
                str(bundle.resolve()): "MeetingCopilotRuntime.bundle",
            }
        }
    }
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(
        json.dumps(overlay, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return overlay


def build_command(
    *, overlay_path: Path, rust_target_dir: Path, cargo_tauri: str = "cargo-tauri"
) -> list[str]:
    return [
        cargo_tauri,
        "build",
        "--bundles",
        "app",
        "--ci",
        "--no-sign",
        "--config",
        str(overlay_path),
        "--",
        "--locked",
        "--target-dir",
        str(rust_target_dir),
    ]


def resolve_cargo_executable(repo_root: Path, cargo_tauri: str) -> str:
    cargo_path = Path(cargo_tauri)
    if not cargo_path.is_absolute() and ("/" in cargo_tauri or "\\" in cargo_tauri):
        return str((repo_root / cargo_path).resolve())
    return cargo_tauri


def build_toolchain_environment(
    *,
    repo_root: Path,
    cargo_executable: str,
    target_dir: Path,
    base_environment: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base_environment is None else base_environment)
    cargo_binary = Path(cargo_executable)
    inferred_cargo_home: Path | None = None
    inferred_rustup_home: Path | None = None
    if cargo_binary.is_absolute() and cargo_binary.parent.name == "bin":
        candidate_cargo_home = cargo_binary.parent.parent
        candidate_rustup_home = candidate_cargo_home.parent / "rustup-home"
        if (
            candidate_cargo_home / "bin/cargo"
        ).exists() and candidate_rustup_home.is_dir():
            inferred_cargo_home = candidate_cargo_home
            inferred_rustup_home = candidate_rustup_home

    cargo_home = Path(
        environment.get(
            "MEETING_COPILOT_CARGO_HOME",
            str(
                inferred_cargo_home or repo_root / "artifacts/tmp/rust_toolchain/cargo"
            ),
        )
    ).expanduser()
    rustup_home = Path(
        environment.get(
            "MEETING_COPILOT_RUSTUP_HOME",
            str(
                inferred_rustup_home
                or repo_root / "artifacts/tmp/rust_toolchain/rustup"
            ),
        )
    ).expanduser()
    toolchain_bin = Path(
        environment.get(
            "MEETING_COPILOT_TOOLCHAIN_BIN",
            str(
                rustup_home / f"toolchains/stable-{platform.machine()}-apple-darwin/bin"
            ),
        )
    ).expanduser()
    environment.update(
        {
            "CARGO_HOME": str(cargo_home),
            "RUSTUP_HOME": str(rustup_home),
            "CARGO_TARGET_DIR": str(target_dir),
            "PATH": ":".join(
                [
                    str(toolchain_bin),
                    str(cargo_home / "bin"),
                    "/usr/bin",
                    "/bin",
                    "/usr/sbin",
                    "/sbin",
                ]
            ),
        }
    )
    return environment


def find_built_app(target_dir: Path) -> Path:
    candidates = sorted((target_dir / "release/bundle/macos").glob("*.app"))
    if not candidates:
        raise FileNotFoundError(f"no macOS .app found below {target_dir}")
    return next(
        (
            candidate
            for candidate in candidates
            if candidate.name == "Meeting Copilot.app"
        ),
        candidates[0],
    )


def clone_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cp", "-cR", str(source), str(destination)], capture_output=True, text=True
    )
    if result.returncode != 0:
        shutil.copytree(source, destination, symlinks=True)


def directory_size(path: Path) -> int:
    return sum(
        candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file()
    )


def remove_python_bytecode(runtime_bundle: Path) -> dict[str, int]:
    deleted_file_count = 0
    deleted_size_bytes = 0
    deleted_pycache_directory_count = 0
    if not runtime_bundle.is_dir():
        return {
            "deleted_file_count": 0,
            "deleted_size_bytes": 0,
            "deleted_pycache_directory_count": 0,
            "bytecode_remaining": 0,
        }

    cache_directories = sorted(
        (
            candidate
            for candidate in runtime_bundle.rglob("__pycache__")
            if candidate.is_dir() and not candidate.is_symlink()
        ),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    )
    for cache_directory in cache_directories:
        if not cache_directory.exists():
            continue
        for candidate in cache_directory.rglob("*"):
            if candidate.is_file() or candidate.is_symlink():
                deleted_file_count += 1
                deleted_size_bytes += candidate.lstat().st_size
        shutil.rmtree(cache_directory)
        deleted_pycache_directory_count += 1

    for candidate in sorted(runtime_bundle.rglob("*")):
        if candidate.suffix not in {".pyc", ".pyo"}:
            continue
        if not candidate.is_file() and not candidate.is_symlink():
            continue
        deleted_file_count += 1
        deleted_size_bytes += candidate.lstat().st_size
        candidate.unlink()

    bytecode_remaining = sum(
        candidate.name == "__pycache__" or candidate.suffix in {".pyc", ".pyo"}
        for candidate in runtime_bundle.rglob("*")
    )
    return {
        "deleted_file_count": deleted_file_count,
        "deleted_size_bytes": deleted_size_bytes,
        "deleted_pycache_directory_count": deleted_pycache_directory_count,
        "bytecode_remaining": bytecode_remaining,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_runtime_manifest(bundle: Path, manifest: dict[str, Any]) -> None:
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def refresh_runtime_manifest_after_nested_signing(bundle: Path) -> dict[str, Any]:
    """Re-seal resource measurements after nested Mach-O signing.

    Tauri may materialize or normalize bundled Mach-O resources while creating
    the app. The nested signing pass can then change their bytes again, so the
    sealed manifest must be refreshed before the app principal is signed.
    """

    manifest_path = bundle / "runtime-bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seal_runtime_bundle_inventory(bundle, manifest)
    return validate_runtime_bundle(bundle)


def _reset_file_asr_package_metadata(manifest: dict[str, Any]) -> None:
    manifest["file_asr"]["package"] = {
        "install_status": "not_bundled",
        "version": None,
        "size_bytes": None,
        "sha256": None,
        "control_manifest_sha256": None,
        "redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "internal_controlled_smoke_status": "not_ready",
        "counts_as_public_release": False,
    }
    manifest["file_asr"]["redistribution"] = {
        "status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "public_redistribution_approved": False,
        "license_id": None,
        "license_path": None,
        "notices_path": None,
        "control_manifest_sha256": None,
    }
    for spec in manifest["file_asr"]["models"].values():
        spec["size_bytes"] = None
        spec["sha256"] = None
    manifest["required_files"] = [
        value
        for value in manifest["required_files"]
        if not str(value).startswith("models/funasr-file/")
    ]


def reset_diarization_package_metadata(manifest: dict[str, Any]) -> None:
    diarization = manifest["diarization"]
    previous_notice = diarization.get("redistribution", {}).get("notices_path")
    diarization["package"] = {
        "pack_id": "diarization-zh-cn",
        "install_status": "not_bundled",
        "verification_status": DIARIZATION_ABSENT_STATUS,
        "version": None,
        "size_bytes": None,
        "sha256": None,
        "control_manifest_sha256": None,
        "redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "counts_as_public_release": False,
    }
    diarization["redistribution"] = {
        "status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "public_redistribution_approved": False,
        "license_ids": [],
        "notices_path": None,
        "control_manifest_sha256": None,
    }
    model_roots: list[str] = []
    for manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.values():
        model = diarization[manifest_key]
        model_roots.append(str(model["root"]))
        model.update(
            {
                "version": None,
                "install_status": "not_bundled",
                "model_download_status": "not_performed",
                "size_bytes": None,
                "sha256": None,
                "source_inventory_sha256": None,
                "provenance": None,
                "public_release_approved": False,
            }
        )
    filtered_required: list[str] = []
    for value in manifest["required_files"]:
        normalized = str(value)
        if any(
            normalized == root or normalized.startswith(f"{root}/")
            for root in model_roots
        ):
            continue
        if (
            isinstance(previous_notice, str)
            and previous_notice
            and (
                normalized == previous_notice
                or normalized.startswith(f"{previous_notice}/")
            )
        ):
            continue
        filtered_required.append(normalized)
    manifest["required_files"] = filtered_required


def validate_diarization_model_pack_compatibility(
    manifest: dict[str, Any], verified_pack: dict[str, Any]
) -> None:
    diarization = manifest.get("diarization")
    if not isinstance(diarization, dict):
        raise ValueError("runtime bundle manifest diarization metadata is missing")
    for name, manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.items():
        destination_spec = diarization.get(manifest_key)
        if not isinstance(destination_spec, dict):
            raise ValueError(
                f"runtime bundle manifest diarization {name} model is missing"
            )
        source_spec = verified_pack["models"][name]
        if source_spec["model_id"] != destination_spec.get("model_id"):
            raise ValueError(
                f"controlled diarization {name} model id does not match runtime manifest"
            )
        required_files = destination_spec.get("required_files")
        if not isinstance(required_files, list) or not set(required_files).issubset(
            source_spec["files"]
        ):
            raise ValueError(
                f"controlled diarization {name} omits runtime required files"
            )


def stage_controlled_diarization_model_pack(
    *,
    bundle: Path,
    manifest: dict[str, Any],
    verified_pack: dict[str, Any],
) -> None:
    diarization = manifest["diarization"]
    validate_diarization_model_pack_compatibility(manifest, verified_pack)
    required_files = set(manifest["required_files"])
    notice_root_relative = str(verified_pack["notice_destination"])
    _validate_bundle_mutation_path(
        bundle,
        notice_root_relative,
        field="diarization control notice destination",
    )
    notice_root = bundle / notice_root_relative
    if notice_root.exists():
        shutil.rmtree(notice_root)
    notice_root.mkdir(parents=True)
    control_manifest_relative = f"{notice_root_relative}/model-pack.manifest.json"
    control_manifest_payload = _sanitized_diarization_control_manifest(verified_pack)
    control_manifest_bytes = _canonical_json_bytes(control_manifest_payload)
    (bundle / control_manifest_relative).write_bytes(control_manifest_bytes)
    control_manifest_sha256 = hashlib.sha256(control_manifest_bytes).hexdigest()
    verified_pack["packaged_manifest_sha256"] = control_manifest_sha256

    destination_records: dict[str, dict[str, Any]] = {}
    license_ids: set[str] = set()
    for name, manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.items():
        source_spec = verified_pack["models"][name]
        destination_spec = diarization[manifest_key]
        _validate_bundle_mutation_path(
            bundle,
            destination_spec["root"],
            field=f"diarization {name} destination",
        )
        destination = bundle / destination_spec["root"]
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        for relative in source_spec["files"]:
            source_file = source_spec["source"] / relative
            destination_file = destination / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
            required_files.add((Path(destination_spec["root"]) / relative).as_posix())
        destination_inventory = _directory_inventory(destination, allowed_root=bundle)
        if any(
            destination_inventory[field] != source_spec["inventory"][field]
            for field in ("size_bytes", "sha256")
        ):
            raise ValueError(
                f"staged controlled diarization model hash mismatch: {name}"
            )
        sanitized_provenance = dict(source_spec["provenance"])
        destination_spec.update(
            {
                "version": source_spec["version"],
                "install_status": "bundled",
                "model_download_status": "not_performed_during_packaging",
                "size_bytes": destination_inventory["size_bytes"],
                "sha256": destination_inventory["sha256"],
                "source_inventory_sha256": source_spec["inventory"]["sha256"],
                "provenance": sanitized_provenance,
                "public_release_approved": False,
            }
        )
        destination_records[name] = destination_inventory
        license_ids.add(str(sanitized_provenance["license_id"]))

    aggregate = _aggregate_inventory(destination_records)
    if aggregate != verified_pack["inventory"]:
        raise ValueError("staged controlled diarization model pack hash mismatch")
    diarization["package"] = {
        "pack_id": "diarization-zh-cn",
        "install_status": "bundled",
        "verification_status": DIARIZATION_BUNDLED_STATUS,
        "version": verified_pack["version"],
        "size_bytes": aggregate["size_bytes"],
        "sha256": aggregate["sha256"],
        "control_manifest_sha256": control_manifest_sha256,
        "redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "counts_as_public_release": False,
    }
    diarization["redistribution"] = {
        "status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "public_redistribution_approved": False,
        "license_ids": sorted(license_ids),
        "notices_path": notice_root_relative,
        "control_manifest_sha256": control_manifest_sha256,
    }
    required_files.add(control_manifest_relative)
    manifest["required_files"] = sorted(required_files)


def _install_file_asr_python_launcher(bundle: Path, manifest: dict[str, Any]) -> str:
    launcher_relative = "bin/meeting-copilot-file-asr-python"
    launcher = bundle / launcher_relative
    funasr_runtime = manifest["runtimes"]["funasr"]
    python_executable = funasr_runtime["executable"]
    site_packages = funasr_runtime["site_packages"]
    python_home = str(Path(python_executable).parents[1])
    launcher.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\n'
        "export PYTHONNOUSERSITE=1\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        "export HF_HUB_OFFLINE=1\n"
        "export TRANSFORMERS_OFFLINE=1\n"
        f'export PYTHONHOME="$ROOT/{python_home}"\n'
        f'export PYTHONPATH="$ROOT/{site_packages}:$ROOT/app/code/asr_runtime/scripts"\n'
        f'exec "$ROOT/{python_executable}" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    funasr_runtime["venv_executable"] = launcher_relative
    manifest["file_asr"]["runtime"]["executable"] = launcher_relative
    manifest["required_files"] = sorted(
        {*manifest["required_files"], launcher_relative}
    )
    return launcher_relative


def _stage_controlled_model_pack(
    *,
    bundle: Path,
    manifest: dict[str, Any],
    verified_pack: dict[str, Any],
) -> None:
    required_files = set(manifest["required_files"])
    notice_root_relative = str(verified_pack["notice_destination"])
    notice_root = bundle / notice_root_relative
    if notice_root.exists():
        shutil.rmtree(notice_root)
    notice_root.mkdir(parents=True)
    license_destination_relative = f"{notice_root_relative}/Apache-2.0.txt"
    license_destination = bundle / license_destination_relative
    shutil.copy2(verified_pack["license_path"], license_destination)
    control_destination = notice_root / "model-pack.manifest.json"
    shutil.copy2(verified_pack["manifest_path"], control_destination)

    for name in FILE_ASR_MODEL_NAMES:
        source_spec = verified_pack["models"][name]
        destination_spec = manifest["file_asr"]["models"][name]
        if source_spec["model_id"] != destination_spec["model_id"]:
            raise ValueError(
                f"controlled model pack {name} model id does not match runtime manifest"
            )
        destination = bundle / destination_spec["root"]
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        for relative in source_spec["files"]:
            source_file = source_spec["source"] / relative
            destination_file = destination / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
            required_files.add((Path(destination_spec["root"]) / relative).as_posix())
        notice_destination = notice_root / f"{name}-README.md"
        shutil.copy2(source_spec["license_evidence_path"], notice_destination)
        destination_inventory = _directory_inventory(destination, allowed_root=bundle)
        destination_spec.update(
            {
                "version": source_spec["version"],
                "size_bytes": destination_inventory["size_bytes"],
                "sha256": destination_inventory["sha256"],
                "source_inventory_sha256": source_spec["inventory"]["sha256"],
            }
        )

    package_inventory = _directory_inventory(
        bundle / "models/funasr-file",
        allowed_root=bundle,
    )
    control_manifest_sha256 = str(verified_pack["manifest_sha256"])
    manifest["file_asr"]["package"] = {
        "install_status": "bundled",
        "version": verified_pack["version"],
        "size_bytes": package_inventory["size_bytes"],
        "sha256": package_inventory["sha256"],
        "control_manifest_sha256": control_manifest_sha256,
        "redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "internal_controlled_smoke_status": INTERNAL_CONTROLLED_SMOKE_STATUS,
        "counts_as_public_release": False,
    }
    manifest["file_asr"]["redistribution"] = {
        "status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        "public_redistribution_approved": False,
        "license_id": verified_pack["redistribution"]["license_id"],
        "license_path": license_destination_relative,
        "notices_path": notice_root_relative,
        "control_manifest_sha256": control_manifest_sha256,
    }
    required_files.update(
        {
            license_destination_relative,
            f"{notice_root_relative}/model-pack.manifest.json",
            *(
                f"{notice_root_relative}/{name}-README.md"
                for name in FILE_ASR_MODEL_NAMES
            ),
        }
    )
    manifest["required_files"] = sorted(required_files)


def seal_runtime_bundle_inventory(
    bundle: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    missing = [
        relative
        for relative in manifest["required_files"]
        if not (bundle / relative).is_file()
    ]
    if missing:
        raise ValueError(f"runtime bundle missing required files: {', '.join(missing)}")
    components: dict[str, dict[str, Any]] = {}
    funasr_runtime = manifest["runtimes"]["funasr"]
    runtime_record = _component_record(
        bundle,
        relative=funasr_runtime["root"],
        kind="directory",
        version=funasr_runtime["component_version"],
    )
    components["shared_asr.runtime"] = runtime_record
    funasr_runtime.update(
        {
            "size_bytes": runtime_record["size_bytes"],
            "sha256": runtime_record["sha256"],
        }
    )
    manifest["file_asr"]["runtime"].update(
        {
            "size_bytes": runtime_record["size_bytes"],
            "sha256": runtime_record["sha256"],
        }
    )
    file_asr_launcher = manifest["file_asr"]["runtime"]["executable"]
    components["file_asr.python_launcher"] = _component_record(
        bundle,
        relative=file_asr_launcher,
        kind="file",
        version=manifest["file_asr"]["runtime"]["version"],
    )

    for name, inventory_name in (
        ("realtime", "realtime_asr.worker"),
        ("file_asr", "file_asr.worker"),
        ("diarization", "diarization.worker"),
    ):
        worker = manifest["worker_inventory"][name]
        record = _component_record(
            bundle,
            relative=worker["path"],
            kind="file",
            version=worker["version"],
        )
        components[inventory_name] = record
        worker.update({"size_bytes": record["size_bytes"], "sha256": record["sha256"]})
        if name == "file_asr":
            manifest["file_asr"]["worker"].update(
                {
                    "size_bytes": record["size_bytes"],
                    "sha256": record["sha256"],
                }
            )
        elif name == "diarization":
            manifest["diarization"]["worker"].update(
                {
                    "size_bytes": record["size_bytes"],
                    "sha256": record["sha256"],
                }
            )

    realtime_model = manifest["realtime_model"]
    realtime_record = _component_record(
        bundle,
        relative=realtime_model["root"],
        kind="directory",
        version=realtime_model["version"],
    )
    components["realtime_asr.model"] = realtime_record | {
        "model_id": realtime_model["model_id"]
    }
    realtime_model.update(
        {
            "size_bytes": realtime_record["size_bytes"],
            "sha256": realtime_record["sha256"],
        }
    )

    converter = manifest["file_asr"]["converter"]
    converter_path = bundle / converter["path"]
    converter_license = bundle / converter["license_path"]
    if converter_path.is_file() and converter_license.is_file():
        converter_record = _component_record(
            bundle,
            relative=converter["path"],
            kind="file",
            version=converter["version"],
        )
        components["file_asr.converter"] = converter_record
        converter.update(
            {
                "size_bytes": converter_record["size_bytes"],
                "sha256": converter_record["sha256"],
            }
        )
    else:
        converter.update({"size_bytes": None, "sha256": None})

    if manifest["file_asr"]["package"]["install_status"] == "bundled":
        for name in FILE_ASR_MODEL_NAMES:
            model = manifest["file_asr"]["models"][name]
            record = _component_record(
                bundle,
                relative=model["root"],
                kind="directory",
                version=model["version"],
            )
            if (
                record["size_bytes"] != model["size_bytes"]
                or record["sha256"] != model["sha256"]
            ):
                raise ValueError(f"staged controlled model pack hash mismatch: {name}")
            components[f"file_asr.model.{name}"] = record | {
                "model_id": model["model_id"]
            }

    if manifest["diarization"]["package"]["install_status"] == "bundled":
        diarization_records: dict[str, dict[str, Any]] = {}
        for name, manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.items():
            model = manifest["diarization"][manifest_key]
            record = _component_record(
                bundle,
                relative=model["root"],
                kind="directory",
                version=model["version"],
            )
            if any(record[field] != model[field] for field in ("size_bytes", "sha256")):
                raise ValueError(
                    f"staged controlled diarization model hash mismatch: {name}"
                )
            if record["sha256"] != model["source_inventory_sha256"]:
                raise ValueError(
                    f"staged controlled diarization source inventory mismatch: {name}"
                )
            components[f"diarization.model.{name}"] = record | {
                "model_id": model["model_id"],
                "source_inventory_sha256": model["source_inventory_sha256"],
                "provenance": dict(model["provenance"]),
            }
            diarization_records[name] = record
        aggregate = _aggregate_inventory(diarization_records)
        package = manifest["diarization"]["package"]
        if any(
            aggregate[field] != package[field] for field in ("size_bytes", "sha256")
        ):
            raise ValueError("staged controlled diarization package hash mismatch")
        redistribution = manifest["diarization"]["redistribution"]
        control_relative = f"{redistribution['notices_path']}/model-pack.manifest.json"
        control_record = _component_record(
            bundle,
            relative=control_relative,
            kind="file",
            version=package["version"],
        )
        control_hashes = {
            package["control_manifest_sha256"],
            redistribution["control_manifest_sha256"],
            control_record["sha256"],
        }
        if len(control_hashes) != 1:
            raise ValueError(
                "staged controlled diarization control manifest hash mismatch"
            )
        components["diarization.control_manifest"] = control_record | {
            "pack_id": package["pack_id"],
            "package_size_bytes": package["size_bytes"],
            "package_sha256": package["sha256"],
            "package_control_manifest_sha256": package["control_manifest_sha256"],
            "redistribution_control_manifest_sha256": redistribution[
                "control_manifest_sha256"
            ],
        }

    manifest["component_inventory"] = {
        "schema_version": COMPONENT_INVENTORY_SCHEMA,
        "status": "sealed",
        "components": components,
    }
    _write_runtime_manifest(bundle, manifest)
    return manifest


def prepare_runtime_bundle_for_packaging(
    *,
    source_bundle: Path,
    destination_bundle: Path,
    model_pack_root: Path | None = None,
    model_pack_manifest: Path | None = None,
    diarization_vad_model_dir: Path | None = None,
    diarization_camplus_model_dir: Path | None = None,
    diarization_model_pack_manifest: Path | None = None,
) -> dict[str, Any]:
    source = source_bundle.expanduser().resolve()
    destination = _resolve_non_symlinked_destination(destination_bundle)
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise ValueError(
            "prepared runtime bundle destination must be separate from source"
        )
    if (model_pack_root is None) != (model_pack_manifest is None):
        raise ValueError(
            "controlled model pack root and manifest must be provided together"
        )
    complete_diarization_inputs = require_complete_diarization_model_inputs(
        vad_model_dir=diarization_vad_model_dir,
        camplus_model_dir=diarization_camplus_model_dir,
        model_pack_manifest=diarization_model_pack_manifest,
    )
    verified_diarization_pack: dict[str, Any] | None = None
    if complete_diarization_inputs:
        assert diarization_vad_model_dir is not None
        assert diarization_camplus_model_dir is not None
        assert diarization_model_pack_manifest is not None
        verified_diarization_pack = validate_controlled_diarization_model_pack(
            vad_model_dir=diarization_vad_model_dir,
            camplus_model_dir=diarization_camplus_model_dir,
            model_pack_manifest=diarization_model_pack_manifest,
        )
        diarization_source_paths = [
            verified_diarization_pack["manifest_path"],
            *(
                model["source"]
                for model in verified_diarization_pack["models"].values()
            ),
        ]
        for source_path in diarization_source_paths:
            if source_path == destination or destination in source_path.parents:
                raise ValueError(
                    "diarization model source cannot be inside the prepared bundle destination"
                )

    verified_pack: dict[str, Any] | None = None
    if model_pack_root is not None and model_pack_manifest is not None:
        verified_pack = validate_controlled_model_pack(
            model_pack_root=model_pack_root,
            model_pack_manifest=model_pack_manifest,
        )
        file_asr_source_paths = [
            verified_pack["manifest_path"],
            verified_pack["license_path"],
            *(model["source"] for model in verified_pack["models"].values()),
        ]
        for source_path in file_asr_source_paths:
            if source_path == destination or destination in source_path.parents:
                raise ValueError(
                    "controlled model pack source cannot be inside the prepared bundle destination"
                )

    _validate_runtime_tree_boundary(source, description="source runtime bundle")
    raw_manifest = _read_json_object(
        source / "runtime-bundle-manifest.json",
        description="runtime bundle manifest",
    )
    physical_file_models = False
    for spec in (raw_manifest.get("file_asr", {}).get("models", {}) or {}).values():
        if not isinstance(spec, dict):
            continue
        try:
            relative = _safe_bundle_path(
                spec.get("root"), field="file ASR source model root"
            )
        except ValueError:
            continue
        physical_file_models = physical_file_models or os.path.lexists(
            source / relative
        )
    package_status = str(
        raw_manifest.get("file_asr", {}).get("package", {}).get("install_status") or ""
    )
    if model_pack_manifest is None and (
        physical_file_models or package_status == "bundled"
    ):
        raise ValueError(
            "file ASR assets require a validated controlled model pack manifest before packaging"
        )
    raw_diarization = raw_manifest.get("diarization", {})
    raw_diarization_models = [
        raw_diarization.get(manifest_key)
        for manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.values()
    ]
    physical_diarization_models = False
    for spec in raw_diarization_models:
        if not isinstance(spec, dict):
            continue
        try:
            relative = _safe_bundle_path(
                spec.get("root"), field="diarization source model root"
            )
        except ValueError:
            continue
        physical_diarization_models = physical_diarization_models or os.path.lexists(
            source / relative
        )
    raw_diarization_status = str(
        raw_diarization.get("package", {}).get("install_status") or ""
    )
    if verified_diarization_pack is None and (
        physical_diarization_models or raw_diarization_status == "bundled"
    ):
        raise ValueError(
            "diarization assets require explicit VAD/CAM++ sources and a validated control manifest"
        )

    raw_manifest = load_runtime_bundle_manifest(source)
    missing_source_files = [
        relative
        for relative in raw_manifest["required_files"]
        if not (source / relative).is_file()
    ]
    if missing_source_files:
        raise ValueError(
            "source runtime bundle missing required files: "
            + ", ".join(missing_source_files)
        )
    if verified_diarization_pack is not None:
        validate_diarization_model_pack_compatibility(
            raw_manifest, verified_diarization_pack
        )

    additional_mutation_paths = tuple(
        str(pack["notice_destination"])
        for pack in (verified_pack, verified_diarization_pack)
        if pack is not None
    )
    _validate_runtime_mutation_targets(
        source,
        raw_manifest,
        additional_paths=additional_mutation_paths,
    )

    if destination.exists():
        shutil.rmtree(destination)
    clone_tree(source, destination)
    _validate_runtime_tree_boundary(
        destination,
        description="cloned runtime bundle",
    )
    _validate_runtime_mutation_targets(
        destination,
        raw_manifest,
        additional_paths=additional_mutation_paths,
    )
    python_bytecode_cleanup = remove_python_bytecode(destination)
    if python_bytecode_cleanup["bytecode_remaining"] != 0:
        raise RuntimeError("prepared runtime bundle still contains Python bytecode")
    manifest = _read_json_object(
        destination / "runtime-bundle-manifest.json",
        description="runtime bundle manifest",
    )
    manifest["component_inventory"] = {
        "schema_version": COMPONENT_INVENTORY_SCHEMA,
        "status": "unsealed",
        "components": {},
    }
    _reset_file_asr_package_metadata(manifest)
    previous_diarization_notice = (
        manifest.get("diarization", {}).get("redistribution", {}).get("notices_path")
    )
    reset_diarization_package_metadata(manifest)
    # Tauri materializes venv interpreter symlinks while copying resources.
    # A bundle-relative launcher preserves PYTHONHOME/PYTHONPATH explicitly.
    _install_file_asr_python_launcher(destination, manifest)
    for spec in manifest.get("file_asr", {}).get("models", {}).values():
        if isinstance(spec, dict):
            candidate = destination / str(spec.get("root") or "")
            if candidate.is_dir():
                shutil.rmtree(candidate)
    for manifest_key in DIARIZATION_MODEL_MANIFEST_KEYS.values():
        spec = manifest["diarization"][manifest_key]
        candidate = destination / str(spec["root"])
        if candidate.is_dir():
            shutil.rmtree(candidate)
    if isinstance(previous_diarization_notice, str) and previous_diarization_notice:
        notice_relative = _safe_bundle_path(
            previous_diarization_notice,
            field="diarization redistribution notices path",
        )
        notice_candidate = destination / notice_relative
        if notice_candidate.is_dir():
            shutil.rmtree(notice_candidate)
    _write_runtime_manifest(destination, manifest)
    manifest = load_runtime_bundle_manifest(destination)

    if verified_pack is not None:
        _stage_controlled_model_pack(
            bundle=destination,
            manifest=manifest,
            verified_pack=verified_pack,
        )
    if verified_diarization_pack is not None:
        stage_controlled_diarization_model_pack(
            bundle=destination,
            manifest=manifest,
            verified_pack=verified_diarization_pack,
        )
    sealed = seal_runtime_bundle_inventory(destination, manifest)
    validate_runtime_bundle(destination)
    capability = inspect_file_asr_capability(destination, sealed)
    diarization_capability = inspect_diarization_capability(destination, sealed)
    package_decision = build_file_asr_package_decision(sealed, capability)
    return {
        "bundle_path": str(destination),
        "manifest": sealed,
        "file_asr_capability": capability,
        "diarization_capability": diarization_capability,
        "package_decision": package_decision,
        "python_bytecode_cleanup": python_bytecode_cleanup,
        "controlled_model_pack": None
        if verified_pack is None
        else {
            "pack_id": verified_pack["pack_id"],
            "version": verified_pack["version"],
            "manifest_sha256": verified_pack["manifest_sha256"],
            "redistribution_status": verified_pack["redistribution"]["status"],
            "internal_controlled_smoke_status": verified_pack[
                "internal_controlled_smoke"
            ]["status"],
            "counts_as_public_release": False,
        },
        "controlled_diarization_model_pack": None
        if verified_diarization_pack is None
        else {
            "pack_id": verified_diarization_pack["pack_id"],
            "version": verified_diarization_pack["version"],
            "manifest_sha256": verified_diarization_pack["packaged_manifest_sha256"],
            "source_manifest_sha256": verified_diarization_pack["manifest_sha256"],
            "verification_status": DIARIZATION_BUNDLED_STATUS,
            "offline_boundary": verified_diarization_pack["offline_boundary"],
            "redistribution_status": verified_diarization_pack["redistribution"][
                "status"
            ],
            "counts_as_public_release": False,
        },
    }


def _build_local_signing_evidence(
    sign_and_verify_result: dict[str, Any],
) -> dict[str, Any]:
    """Keep committed signing evidence free of paths, commands, and identities."""

    plan = sign_and_verify_result.get("signing_plan")
    execution = sign_and_verify_result.get("signing")
    verification = sign_and_verify_result.get("verification")
    if (
        not isinstance(plan, dict)
        or not isinstance(execution, dict)
        or not isinstance(verification, dict)
    ):
        raise ValueError("macOS signing result is incomplete")
    steps = plan.get("signing_steps")
    inventory = plan.get("macho_inventory")
    if not isinstance(steps, list) or not isinstance(inventory, list) or not steps:
        raise ValueError("macOS signing plan summary is incomplete")
    if (
        plan.get("hardened_runtime") is not True
        or plan.get("uses_deep_signing") is not False
    ):
        raise ValueError("macOS signing plan is not hardened and non-recursive")
    roles = sorted({str(step.get("role") or "") for step in steps})
    if "" in roles:
        raise ValueError("macOS signing plan contains an unclassified target")
    entitlement_hashes: dict[str, str] = {}
    for step in steps:
        role = str(step.get("role") or "")
        digest = step.get("entitlements_sha256")
        if digest is None:
            continue
        normalized = str(digest).lower()
        if not SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("macOS signing entitlement hash is invalid")
        previous = entitlement_hashes.get(role)
        if previous is not None and previous != normalized:
            raise ValueError(f"macOS signing entitlement hashes disagree for {role}")
        entitlement_hashes[role] = normalized

    verification_results = verification.get("results")
    if (
        verification.get("status") != "passed"
        or verification.get("uses_deep_verification") is not False
        or not isinstance(verification_results, list)
        or len(verification_results) != len(steps)
    ):
        raise ValueError("macOS signing verification is incomplete")
    if any(
        item.get("strict_verification_return_code") != 0
        or item.get("runtime_verified") is not True
        or item.get("identity_verified") is not True
        or item.get("entitlements_verified") is not True
        for item in verification_results
        if isinstance(item, dict)
    ):
        raise ValueError("macOS signing verification did not pass for every target")
    if any(not isinstance(item, dict) for item in verification_results):
        raise ValueError("macOS signing verification result is invalid")
    if (
        execution.get("status") != "passed"
        or execution.get("hardened_runtime") is not True
    ):
        raise ValueError("macOS signing execution is incomplete")
    return {
        "schema_version": "meeting_copilot.internal_macos_signing_evidence.v1",
        "mode": str(plan.get("mode") or ""),
        "stable_identity_across_builds": plan.get("mode") == "developer-id",
        "plan_summary": {
            "schema_version": str(plan.get("schema_version") or ""),
            "macho_count": len(inventory),
            "signing_step_count": len(steps),
            "roles": roles,
            "entitlement_hashes": entitlement_hashes,
            "uses_deep": False,
            "runtime": True,
        },
        "execution": {
            "schema_version": str(execution.get("schema_version") or ""),
            "status": "passed",
            "signed_target_count": len(steps),
            "uses_deep": False,
            "runtime": True,
        },
        "verification": {
            "schema_version": str(verification.get("schema_version") or ""),
            "status": "passed",
            "verified_target_count": len(verification_results),
            "strict": True,
            "uses_deep": False,
            "runtime": True,
            "identity": True,
            "entitlements": True,
        },
    }


def package_runtime_app(
    *,
    repo_root: Path,
    runtime_bundle: Path,
    output_root: Path,
    run_id: str,
    cargo_tauri: str = "cargo-tauri",
    model_pack_root: Path | None = None,
    model_pack_manifest: Path | None = None,
    diarization_vad_model_dir: Path | None = None,
    diarization_camplus_model_dir: Path | None = None,
    diarization_model_pack_manifest: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    runtime_bundle = runtime_bundle.resolve()
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    require_complete_diarization_model_inputs(
        vad_model_dir=diarization_vad_model_dir,
        camplus_model_dir=diarization_camplus_model_dir,
        model_pack_manifest=diarization_model_pack_manifest,
    )

    run_root = output_root / run_id
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    prepared_bundle = run_root / "prepared-runtime/MeetingCopilotRuntime.bundle"
    prepared = prepare_runtime_bundle_for_packaging(
        source_bundle=runtime_bundle,
        destination_bundle=prepared_bundle,
        model_pack_root=model_pack_root,
        model_pack_manifest=model_pack_manifest,
        diarization_vad_model_dir=diarization_vad_model_dir,
        diarization_camplus_model_dir=diarization_camplus_model_dir,
        diarization_model_pack_manifest=diarization_model_pack_manifest,
    )
    runtime_manifest = prepared["manifest"]
    fixed_app_identity = validate_fixed_app_identity(repo_root, runtime_manifest)
    file_asr_capability = prepared["file_asr_capability"]
    diarization_capability = prepared["diarization_capability"]
    package_decision = prepared["package_decision"]
    overlay_path = run_root / "tauri.runtime.overlay.json"
    overlay = build_overlay(prepared_bundle, overlay_path)
    target_dir = run_root / "cargo-target"
    command = build_command(
        overlay_path=overlay_path, rust_target_dir=target_dir, cargo_tauri=cargo_tauri
    )
    cargo_executable = resolve_cargo_executable(repo_root, cargo_tauri)
    environment = build_toolchain_environment(
        repo_root=repo_root,
        cargo_executable=cargo_executable,
        target_dir=target_dir,
    )
    command[0] = cargo_executable
    completed = subprocess.run(
        command,
        cwd=DESKTOP_ROOT / "src-tauri",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    (run_root / "build.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_root / "build.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Tauri app build failed with exit {completed.returncode}")

    built_app = find_built_app(target_dir)
    packaged_app = run_root / "Meeting Copilot.app"
    clone_tree(built_app, packaged_app)
    resource_root = packaged_app / "Contents/Resources/MeetingCopilotRuntime.bundle"
    pre_sign_bytecode_cleanup = remove_python_bytecode(resource_root)
    if pre_sign_bytecode_cleanup["bytecode_remaining"] != 0:
        raise RuntimeError(
            "packaged runtime bundle still contains Python bytecode before signing"
        )
    prepared_bytecode_cleanup = prepared["python_bytecode_cleanup"]
    python_bytecode_cleanup = {
        "prepared_runtime": prepared_bytecode_cleanup,
        "pre_sign_runtime": pre_sign_bytecode_cleanup,
        "deleted_file_count": (
            prepared_bytecode_cleanup["deleted_file_count"]
            + pre_sign_bytecode_cleanup["deleted_file_count"]
        ),
        "deleted_size_bytes": (
            prepared_bytecode_cleanup["deleted_size_bytes"]
            + pre_sign_bytecode_cleanup["deleted_size_bytes"]
        ),
        "deleted_pycache_directory_count": (
            prepared_bytecode_cleanup["deleted_pycache_directory_count"]
            + pre_sign_bytecode_cleanup["deleted_pycache_directory_count"]
        ),
        "bytecode_remaining": pre_sign_bytecode_cleanup["bytecode_remaining"],
    }
    try:
        packaged_manifest = validate_runtime_bundle(resource_root)
        missing_packaged: list[str] = []
    except ValueError as exc:
        packaged_manifest = None
        missing_packaged = [str(exc)]

    def refresh_manifest_before_main_app_sign(step: Mapping[str, Any]) -> None:
        """Seal post-nested-sign resources before signing the app principal."""

        nonlocal packaged_manifest
        if step.get("role") != "main-app":
            return
        packaged_manifest = refresh_runtime_manifest_after_nested_signing(resource_root)

    signing_result = macos_codesign.sign_and_verify(
        packaged_app,
        mode="ad-hoc",
        before_step=refresh_manifest_before_main_app_sign,
    )
    signing = _build_local_signing_evidence(signing_result)
    evidence = {
        "schema_version": "meeting_copilot.tauri_runtime_package.v1",
        "run_id": run_id,
        "host_platform": platform.platform(),
        "architecture": platform.machine(),
        "runtime_bundle_source": str(runtime_bundle),
        "prepared_runtime_bundle": str(prepared_bundle.relative_to(repo_root)),
        "controlled_model_pack": prepared["controlled_model_pack"],
        "controlled_diarization_model_pack": prepared[
            "controlled_diarization_model_pack"
        ],
        "runtime_manifest": runtime_manifest,
        "file_asr_capability": file_asr_capability,
        "diarization_capability": diarization_capability,
        "package_decision": package_decision,
        "fixed_app_identity": fixed_app_identity,
        "packaged_runtime_manifest": packaged_manifest,
        "overlay": overlay,
        "build_command": command,
        "build_return_code": completed.returncode,
        "app_path": str(packaged_app.relative_to(repo_root)),
        "app_logical_size_bytes": directory_size(packaged_app),
        "resource_root_present": resource_root.is_dir(),
        "required_packaged_missing": missing_packaged,
        "python_bytecode_cleanup": python_bytecode_cleanup,
        "local_signing": signing,
        "app_binary": {
            "path": "Contents/MacOS/meeting-copilot-desktop",
            "sha256": sha256_file(
                packaged_app / "Contents/MacOS/meeting-copilot-desktop"
            ),
        },
        "decision": {
            "status": "go_internal_controlled_smoke_not_public_release"
            if not missing_packaged
            and package_decision["counts_as_internal_controlled_smoke"]
            else "no_go_packaged_runtime_resource_app",
            "counts_as_packaged_runtime_resource_evidence": not missing_packaged,
            "counts_as_packaged_mainline_evidence": False,
            "counts_as_internal_controlled_smoke": package_decision[
                "counts_as_internal_controlled_smoke"
            ],
            "counts_as_public_release_package": False,
            "counts_as_public_release": False,
            "counts_as_verified_diarization_model_evidence": (
                diarization_capability["status"] == DIARIZATION_BUNDLED_STATUS
            ),
            "public_redistribution_status": PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        },
        "remaining_blockers": [
            *(
                []
                if package_decision["counts_as_internal_controlled_smoke"]
                else ["file_asr_package_decision_not_ready"]
            ),
            *(
                ["diarization_model_redistribution_unresolved"]
                if diarization_capability["status"] == DIARIZATION_BUNDLED_STATUS
                else ["optional_diarization_models_not_bundled"]
            ),
            "stable_developer_id_signing_identity_not_configured",
            "packaged_app_runtime_supervisor_execution_not_yet_verified",
            "not_signed_or_notarized",
            "separate_clean_mac_not_verified",
            PUBLIC_REDISTRIBUTION_UNRESOLVED_STATUS,
        ],
    }
    evidence_path = run_root / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return evidence | {"evidence_path": str(evidence_path.relative_to(repo_root))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--runtime-bundle", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cargo-tauri", default="cargo-tauri")
    parser.add_argument("--file-asr-model-pack-root", type=Path)
    parser.add_argument("--file-asr-model-pack-manifest", type=Path)
    parser.add_argument("--diarization-vad-model-dir", type=Path)
    parser.add_argument("--diarization-camplus-model-dir", type=Path)
    parser.add_argument("--diarization-model-pack-manifest", type=Path)
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
        result = package_runtime_app(
            repo_root=args.repo_root,
            runtime_bundle=args.runtime_bundle,
            output_root=args.output_root,
            run_id=args.run_id,
            cargo_tauri=args.cargo_tauri,
            model_pack_root=args.file_asr_model_pack_root,
            model_pack_manifest=args.file_asr_model_pack_manifest,
            diarization_vad_model_dir=args.diarization_vad_model_dir,
            diarization_camplus_model_dir=args.diarization_camplus_model_dir,
            diarization_model_pack_manifest=args.diarization_model_pack_manifest,
        )
    except Exception as exc:
        result = {
            "schema_version": "meeting_copilot.tauri_runtime_package.v1",
            "run_id": args.run_id,
            "diarization_capability": {
                "status": DIARIZATION_INVALID_STATUS,
                "available": False,
                "fail_open": False,
                "invalid_fail_closed": True,
                "recording_and_asr_continue": False,
                "counts_as_public_release": False,
            }
            if diarization_requested
            else None,
            "decision": {
                "status": "no_go_packaged_runtime_resource_app",
                "counts_as_packaged_runtime_resource_evidence": False,
                "counts_as_verified_diarization_model_evidence": False,
                "counts_as_public_release_package": False,
                "counts_as_public_release": False,
            },
            "error": {
                "class": type(exc).__name__,
                "message": str(exc)[:1000],
            },
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return (
        0 if result["decision"]["counts_as_packaged_runtime_resource_evidence"] else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
