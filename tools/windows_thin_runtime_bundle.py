#!/usr/bin/env python3
"""Build the Windows base runtime without optional local ASR capability packs."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import package_tauri_runtime_app as package_runtime  # noqa: E402
import windows_bundled_runtime as windows_runtime  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "artifacts/tmp/wrb/MeetingCopilotRuntime.bundle"
DEFAULT_OUTPUT = (
    REPO_ROOT / "artifacts/tmp/windows-thin/MeetingCopilotRuntime.bundle"
)
CATALOG_SCHEMA = "meeting_copilot.capability_pack_catalog.v1"
REPORT_SCHEMA = "meeting_copilot.windows_thin_runtime.v1"
BASE_COMPONENT_PATHS = (
    "runtime/backend-python",
    "runtime/backend-venv",
)
BASE_ANCILLARY_FILES = ("bin/meeting-copilot-asr-worker.cmd",)
BASE_ANCILLARY_DIRECTORIES = ("licenses",)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON contract {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON contract must be an object: {path}")
    return value


def _component(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    value = (
        manifest.get("component_inventory", {})
        .get("components", {})
        .get(name)
    )
    if not isinstance(value, dict):
        raise ValueError(f"source runtime component is missing: {name}")
    return copy.deepcopy(value)


def _pack_url(base_url: str | None, pack_id: str, version: str) -> list[str]:
    if not base_url:
        return []
    root = base_url.rstrip("/")
    filename = f"{pack_id}-{version}-windows-x86_64.zip"
    return [f"{root}/packs/{pack_id}/{version}/windows-x86_64/{filename}"]


def _pack_record(
    *,
    pack_id: str,
    version: str,
    components: dict[str, dict[str, Any]],
    depends_on: list[str],
    base_url: str | None,
    user_visible: bool,
) -> dict[str, Any]:
    size = sum(int(component.get("size_bytes") or 0) for component in components.values())
    return {
        "pack_id": pack_id,
        "version": version,
        "platform": "windows-x86_64",
        "publish_status": "not_uploaded",
        "user_visible": user_visible,
        "depends_on": depends_on,
        "components": components,
        "unpacked_size_bytes": size,
        "archive_size_bytes": None,
        "archive_sha256": None,
        "urls": _pack_url(base_url, pack_id, version),
        "auto_install_allowed": False,
    }


def build_capability_pack_catalog(
    source_manifest: dict[str, Any], *, base_url: str | None = None
) -> dict[str, Any]:
    shared_id = "asr-runtime-windows-x86_64"
    shared_version = "asr-runtime-windows-20260724-v1"
    realtime_version = str(source_manifest["realtime_model"]["version"])
    file_version = str(source_manifest["file_asr"]["package"]["version"])
    if not realtime_version or not file_version:
        raise ValueError("source runtime ASR pack versions are missing")

    packs = {
        shared_id: _pack_record(
            pack_id=shared_id,
            version=shared_version,
            components={
                "shared_asr.python_runtime": _component(
                    source_manifest, "shared_asr.python_runtime"
                ),
                "shared_asr.sitecustomize": _component(
                    source_manifest, "shared_asr.sitecustomize"
                ),
            },
            depends_on=[],
            base_url=base_url,
            user_visible=False,
        ),
        "realtime-asr-zh-cn": _pack_record(
            pack_id="realtime-asr-zh-cn",
            version=realtime_version,
            components={
                "realtime_asr.onnx_runtime": _component(
                    source_manifest, "realtime_asr.onnx_runtime"
                ),
                "realtime_asr.model": _component(
                    source_manifest, "realtime_asr.model"
                ),
            },
            depends_on=[shared_id],
            base_url=base_url,
            user_visible=True,
        ),
        "file-asr-zh-cn": _pack_record(
            pack_id="file-asr-zh-cn",
            version=file_version,
            components={
                "shared_asr.runtime": _component(
                    source_manifest, "shared_asr.runtime"
                ),
                "file_asr.model.offline": _component(
                    source_manifest, "file_asr.model.offline"
                ),
                "file_asr.model.vad": _component(
                    source_manifest, "file_asr.model.vad"
                ),
                "file_asr.model.punc": _component(
                    source_manifest, "file_asr.model.punc"
                ),
            },
            depends_on=[shared_id],
            base_url=base_url,
            user_visible=True,
        ),
    }
    return {
        "schema_version": CATALOG_SCHEMA,
        "distribution_profile": "base",
        "install_root_policy": {
            "windows": "%LOCALAPPDATA%\\MeetingCopilot\\capability-packs",
            "macos": "~/Library/Application Support/Meeting Copilot/capability-packs",
            "application_directory_writable": False,
        },
        "install_protocol": {
            "download_suffix": ".partial",
            "verify_archive_sha256": True,
            "verify_component_inventory": True,
            "activate_by_atomic_rename": True,
            "retain_previous_version": True,
        },
        "packs": packs,
    }


def _copy_base_payload(source: Path, destination: Path) -> None:
    for relative in BASE_COMPONENT_PATHS:
        candidate = source / relative
        if not candidate.is_dir():
            raise ValueError(f"source runtime base component is missing: {relative}")
        windows_runtime._copy_tree(candidate, destination / relative)
    for relative in BASE_ANCILLARY_FILES:
        candidate = source / relative
        if not candidate.is_file():
            raise ValueError(f"source runtime ancillary file is missing: {relative}")
        windows_runtime._copy_file(candidate, destination / relative)
    for relative in BASE_ANCILLARY_DIRECTORIES:
        candidate = source / relative
        if not candidate.is_dir():
            raise ValueError(f"source runtime ancillary directory is missing: {relative}")
        windows_runtime._copy_tree(candidate, destination / relative)


def _write_backend_launcher(bundle: Path) -> str:
    relative = "bin/meeting-copilot-backend.cmd"
    launcher = bundle / relative
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        """@echo off\r
setlocal\r
for %%I in (\"%~dp0..\") do set \"ROOT=%%~fI\"\r
set \"PYTHONNOUSERSITE=1\"\r
set \"PYTHONDONTWRITEBYTECODE=1\"\r
set \"PYTHONUTF8=1\"\r
set \"PYTHONIOENCODING=utf-8\"\r
set \"PYTHONHOME=%ROOT%\\runtime\\backend-python\"\r
set \"PYTHONPATH=%ROOT%\\runtime\\backend-venv\\Lib\\site-packages;%ROOT%\\app\\code\\web_mvp\\backend;%ROOT%\\app\\code\\core\"\r
set \"MEETING_COPILOT_RUNTIME_MANIFEST=%ROOT%\\runtime-bundle-manifest.json\"\r
\"%ROOT%\\runtime\\backend-python\\python.exe\" -m uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port %MEETING_COPILOT_PORT% --log-level warning --timeout-graceful-shutdown 8\r
""",
        encoding="ascii",
        newline="",
    )
    return relative


def _base_manifest(
    *,
    source_manifest: dict[str, Any],
    bundle: Path,
    launcher: str,
    base_url: str | None,
) -> dict[str, Any]:
    manifest = copy.deepcopy(source_manifest)
    manifest["app_identity"] = copy.deepcopy(
        package_runtime.EXPECTED_WINDOWS_APP_IDENTITY
    )
    manifest["distribution_profile"] = "base"
    manifest["capability_packs"] = build_capability_pack_catalog(
        source_manifest, base_url=base_url
    )
    manifest["launchers"] = {"backend": launcher}
    package_runtime._reset_file_asr_package_metadata(manifest)
    package_runtime.reset_diarization_package_metadata(manifest)
    manifest["required_files"] = sorted(
        {
            "runtime-bundle-manifest.json",
            launcher,
            "runtime/backend-python/python.exe",
            "app/code/web_mvp/backend/meeting_copilot_web_mvp/app.py",
            "app/code/web_mvp/frontend_v2/dist/index.html",
            "app/code/core/meeting_copilot_core/__init__.py",
        }
    )
    components = {
        "backend.python_runtime": package_runtime._component_record(
            bundle,
            relative="runtime/backend-python",
            kind="directory",
            version="python-3.13-win_amd64",
        ),
        "backend.site_packages": package_runtime._component_record(
            bundle,
            relative="runtime/backend-venv",
            kind="directory",
            version="backend-base-20260724",
        ),
        "app.application": package_runtime._component_record(
            bundle,
            relative="app",
            kind="directory",
            version="meeting-copilot-0.1.0",
        ),
        "launcher.backend": package_runtime._component_record(
            bundle,
            relative=launcher,
            kind="file",
            version="windows-thin-launcher-v1",
        ),
        "launcher.funasr": package_runtime._component_record(
            bundle,
            relative="bin/meeting-copilot-asr-worker.cmd",
            kind="file",
            version="windows-asr-launcher-v1",
        ),
        "asr.licenses": package_runtime._component_record(
            bundle,
            relative="licenses",
            kind="directory",
            version="asr-license-evidence-20260727",
        ),
    }
    manifest["component_inventory"] = {
        "schema_version": package_runtime.COMPONENT_INVENTORY_SCHEMA,
        "status": "sealed",
        "components": components,
    }
    manifest["packaged_python"] = {
        "backend": components["backend.python_runtime"],
        "backend_site_packages": components["backend.site_packages"],
    }
    return manifest


def build_windows_thin_runtime(
    *,
    source: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
    pack_base_url: str | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    output = windows_runtime._resolve_output(output)
    if source == output or output in source.parents:
        raise ValueError("thin runtime output must not replace or contain its source")
    source_manifest = _read_json(source / "runtime-bundle-manifest.json")
    if source_manifest.get("platform") != "windows":
        raise ValueError("source runtime is not a Windows bundle")

    if output.exists():
        shutil.rmtree(windows_runtime._extended_windows_path(output))
    windows_runtime._extended_windows_path(output).mkdir(parents=True)
    _copy_base_payload(source, output)
    windows_runtime._stage_application(output)
    launcher = _write_backend_launcher(output)
    package_runtime.remove_python_bytecode(output)
    manifest = _base_manifest(
        source_manifest=source_manifest,
        bundle=output,
        launcher=launcher,
        base_url=pack_base_url,
    )
    package_runtime._write_runtime_manifest(output, manifest)
    validated = package_runtime.validate_runtime_bundle(output)
    inventory = package_runtime._directory_inventory(
        output, allowed_root=(REPO_ROOT / "artifacts/tmp").resolve()
    )
    if inventory["size_bytes"] >= 400 * 1024 * 1024:
        raise ValueError("Windows base runtime exceeds the 400 MiB release budget")
    report = {
        "schema_version": REPORT_SCHEMA,
        "status": "built_base_runtime",
        "platform": "windows",
        "architecture": "x86_64",
        "bundle_path": str(output.relative_to(REPO_ROOT)),
        "bundle_inventory": inventory,
        "distribution_profile": validated["distribution_profile"],
        "capability_pack_count": len(validated["capability_packs"]["packs"]),
        "capability_pack_publish_status": "credentials_and_archive_hashes_required",
        "audio_capture_performed": False,
        "counts_as_signed_installer": False,
    }
    report_path = output.parent / "windows-thin-runtime-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pack-base-url")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_windows_thin_runtime(
        source=args.source,
        output=args.output,
        pack_base_url=args.pack_base_url,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
