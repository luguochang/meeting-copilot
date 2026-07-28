#!/usr/bin/env python3
"""Build a sealed, relocatable Windows runtime bundle for internal packaging."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import package_tauri_runtime_app as package_runtime  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_MANIFEST = REPO_ROOT / "code/desktop_tauri/runtime-bundle-manifest.json"
ONNX_PACK_MANIFEST = (
    REPO_ROOT
    / "code/asr_runtime/model_packs/realtime-asr-onnx-zh-cn-20260722.manifest.json"
)
FILE_ASR_PACK_MANIFEST = (
    REPO_ROOT / "code/asr_runtime/model_packs/file-asr-zh-cn-20260718.manifest.json"
)
DEFAULT_OUTPUT = (
    REPO_ROOT / "artifacts/tmp/wrb/MeetingCopilotRuntime.bundle"
)
DEFAULT_ONNX_RUNTIME = REPO_ROOT / "artifacts/tmp/asr_preview_bakeoff/runtime"
DEFAULT_ONNX_MODEL = REPO_ROOT / "artifacts/tmp/asr_preview_bakeoff/onnx-online"
DEFAULT_FILE_ASR_MODELS = REPO_ROOT / "artifacts/tmp/asr_chinese_repair/models"
DEFAULT_BACKEND_VENV = REPO_ROOT / "code/web_mvp/backend/.venv"
DEFAULT_FUNASR_VENV = REPO_ROOT / "code/asr_runtime/.venv-funasr"
DEFAULT_MODEL_README = (
    Path.home()
    / ".cache/modelscope/hub/models/iic/"
    "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online/README.md"
)
ONNX_PACK_SCHEMA = "meeting_copilot.controlled_realtime_onnx_pack.v1"
WINDOWS_RUNTIME_REPORT_SCHEMA = "meeting_copilot.windows_runtime_bundle.v1"
COPY_IGNORED_NAMES = {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON contract {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON contract {path.name} must be an object")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_output(output: Path) -> Path:
    resolved = output.expanduser().resolve()
    approved = (REPO_ROOT / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("Windows runtime output must be below artifacts/tmp") from exc
    if resolved == approved:
        raise ValueError("Windows runtime output cannot replace artifacts/tmp")
    return resolved


def _python_home_from_venv(venv: Path) -> Path:
    config = venv / "pyvenv.cfg"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Python venv metadata is unavailable: {config}") from exc
    values = {
        key.strip().casefold(): value.strip()
        for line in lines
        if "=" in line
        for key, value in [line.split("=", 1)]
    }
    home = Path(values.get("home", "")).expanduser()
    if not home.is_dir() or not (home / "python.exe").is_file():
        raise ValueError(f"Python home from {config} is incomplete")
    return home.resolve()


def _copy_ignore(source: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name in COPY_IGNORED_NAMES or name.endswith((".pyc", ".pyo"))
    }
    if Path(source).name.casefold() == "lib":
        ignored.add("site-packages")
    return ignored


def _extended_windows_path(path: Path) -> Path:
    """Use the Win32 extended path namespace for deep packaged dependencies."""

    resolved = path.expanduser().resolve()
    if sys.platform != "win32":
        return resolved
    value = str(resolved)
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def _copy_tree(source: Path, destination: Path, *, python_home: bool = False) -> None:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"runtime source directory is missing: {source.name}")
    shutil.copytree(
        _extended_windows_path(source),
        _extended_windows_path(destination),
        dirs_exist_ok=True,
        symlinks=True,
        ignore=_copy_ignore if python_home else shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".ruff_cache"
        ),
    )


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ValueError(f"runtime source file is missing: {source.name}")
    extended_destination = _extended_windows_path(destination)
    extended_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_extended_windows_path(source), extended_destination)


def validate_onnx_pack(
    *,
    runtime_root: Path,
    model_root: Path,
    model_readme: Path,
    manifest_path: Path = ONNX_PACK_MANIFEST,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != ONNX_PACK_SCHEMA:
        raise ValueError("ONNX pack schema is invalid")
    if manifest.get("engine") != "onnx" or manifest.get("platform") != "windows":
        raise ValueError("ONNX pack platform/engine contract is invalid")
    if manifest.get("architecture") != "x86_64":
        raise ValueError("ONNX pack architecture is unsupported")

    runtime_root = runtime_root.expanduser().resolve()
    model_root = model_root.expanduser().resolve()
    runtime_inventory = package_runtime._directory_inventory(
        runtime_root,
        allowed_root=runtime_root.parent,
    )
    if runtime_inventory != manifest.get("runtime", {}).get("source_inventory"):
        raise ValueError("ONNX runtime source inventory does not match its control manifest")

    expected_files = manifest.get("model", {}).get("files")
    if not isinstance(expected_files, dict) or not expected_files:
        raise ValueError("ONNX model file inventory is missing")
    for relative, expected_sha256 in expected_files.items():
        source = model_root / str(relative)
        if not source.is_file() or _sha256_file(source) != expected_sha256:
            raise ValueError(f"ONNX model file hash mismatch: {relative}")
    model_inventory = package_runtime._directory_inventory(
        model_root,
        allowed_root=model_root.parent,
    )
    if model_inventory != manifest.get("model", {}).get("inventory"):
        raise ValueError("ONNX model directory inventory does not match its control manifest")
    expected_readme = manifest.get("redistribution", {}).get("license_evidence_sha256")
    if not model_readme.is_file() or _sha256_file(model_readme) != expected_readme:
        raise ValueError("ONNX model license evidence is missing or changed")
    if "license: Apache License 2.0" not in model_readme.read_text(
        encoding="utf-8", errors="replace"
    ):
        raise ValueError("ONNX model license evidence text is missing")
    return {
        "manifest": manifest,
        "manifest_path": manifest_path.resolve(),
        "runtime_root": runtime_root,
        "runtime_inventory": runtime_inventory,
        "model_root": model_root,
        "model_inventory": model_inventory,
        "model_readme": model_readme.resolve(),
    }


def windows_manifest(
    base_manifest: dict[str, Any],
    onnx_pack: dict[str, Any],
) -> dict[str, Any]:
    manifest = copy.deepcopy(base_manifest)
    model_contract = onnx_pack["manifest"]["model"]
    manifest["platform"] = "windows"
    manifest["architectures"] = ["x86_64"]
    manifest["app_identity"] = {
        "product_name": "Meeting Copilot",
        "bundle_identifier": "com.meetingcopilot.desktop",
        "app_bundle_name": "Meeting Copilot.exe",
        "executable_name": "meeting-copilot-desktop.exe",
    }
    manifest["runtimes"]["backend"].update(
        {
            "python_version": "3.13",
            "executable": "runtime/backend-python/python.exe",
            "venv_executable": "runtime/backend-python/python.exe",
            "site_packages": "runtime/backend-venv/Lib/site-packages",
        }
    )
    manifest["runtimes"]["funasr"].update(
        {
            "python_version": "3.12",
            "component_version": "funasr-1.3.10-py312-win_amd64",
            "executable": "runtime/funasr-python/python.exe",
            "venv_executable": "runtime/funasr-python/python.exe",
            "site_packages": "runtime/funasr-venv/Lib/site-packages",
            "root": "runtime/funasr-venv",
            "size_bytes": None,
            "sha256": None,
        }
    )
    manifest["launchers"] = {
        "backend": "bin/meeting-copilot-backend.cmd",
        "funasr": "bin/meeting-copilot-asr-worker.cmd",
    }
    manifest["workers"]["offline_refiner"] = (
        "app/code/asr_runtime/scripts/funasr_offline_refiner_worker.py"
    )
    manifest["realtime_model"] = {
        "engine": "onnx",
        "model_id": model_contract["model_id"],
        "version": onnx_pack["manifest"]["version"],
        "root": model_contract["root"],
        "required_files": sorted(model_contract["files"]),
        "size_bytes": None,
        "sha256": None,
        "source_inventory_sha256": model_contract["inventory"]["sha256"],
    }
    manifest["realtime_runtime"] = {
        "engine": "onnx",
        "version": onnx_pack["manifest"]["version"],
        "root": "runtime/funasr-onnx",
        "required_imports": list(onnx_pack["manifest"]["runtime"]["required_imports"]),
        "source_inventory_sha256": onnx_pack["runtime_inventory"]["sha256"],
        "size_bytes": None,
        "sha256": None,
    }
    manifest["file_asr"]["runtime"].update(
        {
            "version": "funasr-1.3.10-py312-win_amd64",
            "root": "runtime/funasr-venv",
            "executable": "runtime/funasr-python/python.exe",
            "size_bytes": None,
            "sha256": None,
        }
    )
    manifest["file_asr"]["converter"].update(
        {
            "path": (
                "runtime/backend-venv/Lib/site-packages/imageio_ffmpeg/"
                "binaries/ffmpeg-win-x86_64-v7.1.exe"
            ),
            "license_path": (
                "runtime/backend-venv/Lib/site-packages/"
                "imageio_ffmpeg-0.6.0.dist-info/LICENSE"
            ),
        }
    )
    manifest["component_inventory"] = {
        "schema_version": package_runtime.COMPONENT_INVENTORY_SCHEMA,
        "status": "unsealed",
        "components": {},
    }
    manifest["required_files"] = sorted(
        {
            "runtime-bundle-manifest.json",
            manifest["launchers"]["backend"],
            manifest["launchers"]["funasr"],
            manifest["runtimes"]["backend"]["executable"],
            manifest["runtimes"]["funasr"]["executable"],
            "app/code/web_mvp/backend/meeting_copilot_web_mvp/app.py",
            "app/code/web_mvp/frontend_v2/dist/index.html",
            "app/code/core/meeting_copilot_core/__init__.py",
            manifest["workers"]["realtime"],
            manifest["workers"]["file_asr"],
            manifest["workers"]["diarization"],
            manifest["workers"]["offline_refiner"],
            "app/code/asr_runtime/scripts/sitecustomize.py",
            "runtime/funasr-onnx/funasr_onnx/__init__.py",
            "runtime/funasr-onnx/onnxruntime/__init__.py",
            *(
                f"{manifest['realtime_model']['root']}/{relative}"
                for relative in manifest["realtime_model"]["required_files"]
            ),
        }
    )
    return manifest


def write_windows_launchers(bundle: Path, manifest: dict[str, Any]) -> None:
    backend = bundle / manifest["launchers"]["backend"]
    worker = bundle / manifest["launchers"]["funasr"]
    backend.parent.mkdir(parents=True, exist_ok=True)
    backend.write_text(
        """@echo off\r
setlocal\r
for %%I in (\"%~dp0..\") do set \"ROOT=%%~fI\"\r
set \"PYTHONNOUSERSITE=1\"\r
set \"PYTHONDONTWRITEBYTECODE=1\"\r
set \"PYTHONUTF8=1\"\r
set \"PYTHONIOENCODING=utf-8\"\r
set \"PYTHONHOME=%ROOT%\\runtime\\backend-python\"\r
set \"PYTHONPATH=%ROOT%\\runtime\\backend-venv\\Lib\\site-packages;%ROOT%\\app\\code\\web_mvp\\backend;%ROOT%\\app\\code\\core\"\r
set \"MEETING_COPILOT_FUNASR_PYTHON=%ROOT%\\runtime\\funasr-python\\python.exe\"\r
set \"MEETING_COPILOT_FUNASR_PYTHON_HOME=%ROOT%\\runtime\\funasr-python\"\r
set \"MEETING_COPILOT_FUNASR_PYTHONPATH=%ROOT%\\runtime\\funasr-onnx;%ROOT%\\runtime\\funasr-venv\\Lib\\site-packages;%ROOT%\\app\\code\\asr_runtime\\scripts\"\r
set \"MEETING_COPILOT_FUNASR_WORKER=%ROOT%\\app\\code\\asr_runtime\\scripts\\funasr_stream_worker.py\"\r
set \"MEETING_COPILOT_FUNASR_MODEL_DIR=%ROOT%\\models\\funasr-online-onnx\"\r
set \"MEETING_COPILOT_FUNASR_ENGINE=onnx\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_PYTHON=%ROOT%\\runtime\\funasr-python\\python.exe\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_PYTHON_HOME=%ROOT%\\runtime\\funasr-python\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_PYTHONPATH=%ROOT%\\runtime\\funasr-venv\\Lib\\site-packages;%ROOT%\\app\\code\\asr_runtime\\scripts\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_WORKER=%ROOT%\\app\\code\\asr_runtime\\scripts\\funasr_offline_refiner_worker.py\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_MODEL=%ROOT%\\models\\funasr-file\\offline-paraformer\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_VAD_MODEL=%ROOT%\\models\\funasr-file\\vad\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_PUNC_MODEL=%ROOT%\\models\\funasr-file\\punc\"\r
set \"MEETING_COPILOT_REALTIME_REFINER_HOTWORDS=%ROOT%\\app\\configs\\asr_hotwords.json\"\r
set \"MEETING_COPILOT_FILE_ASR_MODEL_DIR=%ROOT%\\models\\funasr-file\\offline-paraformer\"\r
set \"MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR=%ROOT%\\models\\funasr-file\\vad\"\r
set \"MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR=%ROOT%\\models\\funasr-file\\punc\"\r
set \"MEETING_COPILOT_RUNTIME_MANIFEST=%ROOT%\\runtime-bundle-manifest.json\"\r
\"%ROOT%\\runtime\\backend-python\\python.exe\" -m uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port %MEETING_COPILOT_PORT% --log-level warning --timeout-graceful-shutdown 8\r
""",
        encoding="ascii",
        newline="",
    )
    worker.write_text(
        """@echo off\r
setlocal\r
for %%I in (\"%~dp0..\") do set \"ROOT=%%~fI\"\r
set \"PYTHONNOUSERSITE=1\"\r
set \"PYTHONDONTWRITEBYTECODE=1\"\r
set \"PYTHONUTF8=1\"\r
set \"PYTHONIOENCODING=utf-8\"\r
set \"PYTHONHOME=%ROOT%\\runtime\\funasr-python\"\r
set \"PYTHONPATH=%ROOT%\\runtime\\funasr-onnx;%ROOT%\\runtime\\funasr-venv\\Lib\\site-packages;%ROOT%\\app\\code\\asr_runtime\\scripts\"\r
\"%ROOT%\\runtime\\funasr-python\\python.exe\" \"%ROOT%\\app\\code\\asr_runtime\\scripts\\funasr_stream_worker.py\" --model \"%ROOT%\\models\\funasr-online-onnx\" --engine onnx %*\r
""",
        encoding="ascii",
        newline="",
    )
    site_packages_marker = b'set "PYTHONIOENCODING=utf-8"\r\n'
    site_packages_env = (
        site_packages_marker
        + b'set "MEETING_COPILOT_FUNASR_SITE_PACKAGES=%ROOT%\\runtime\\funasr-venv\\Lib\\site-packages"\r\n'
        + b'set "MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS=120"\r\n'
    )
    for launcher in (backend, worker):
        payload = launcher.read_bytes()
        if payload.count(site_packages_marker) != 1:
            raise ValueError("Windows launcher Python environment marker is invalid")
        launcher.write_bytes(
            payload.replace(site_packages_marker, site_packages_env, 1)
        )


def _stage_application(bundle: Path) -> None:
    mappings = (
        (
            REPO_ROOT / "code/web_mvp/backend/meeting_copilot_web_mvp",
            bundle / "app/code/web_mvp/backend/meeting_copilot_web_mvp",
        ),
        (
            REPO_ROOT / "code/web_mvp/frontend_v2/dist",
            bundle / "app/code/web_mvp/frontend_v2/dist",
        ),
        (
            REPO_ROOT / "code/core/meeting_copilot_core",
            bundle / "app/code/core/meeting_copilot_core",
        ),
        (
            REPO_ROOT / "code/asr_runtime/scripts",
            bundle / "app/code/asr_runtime/scripts",
        ),
    )
    for source, destination in mappings:
        _copy_tree(source, destination)
    config_root = bundle / "app/configs"
    config_root.mkdir(parents=True, exist_ok=True)
    for name in ("asr_hotwords.json", "asr_terms.json"):
        _copy_file(REPO_ROOT / "configs" / name, config_root / name)


def _add_component(
    *,
    bundle: Path,
    manifest: dict[str, Any],
    name: str,
    relative: str,
    kind: str,
    version: str,
) -> dict[str, Any]:
    record = package_runtime._component_record(
        bundle,
        relative=relative,
        kind=kind,
        version=version,
    )
    manifest["component_inventory"]["components"][name] = record
    return record


def build_windows_runtime(
    *,
    output: Path = DEFAULT_OUTPUT,
    backend_venv: Path = DEFAULT_BACKEND_VENV,
    funasr_venv: Path = DEFAULT_FUNASR_VENV,
    onnx_runtime: Path = DEFAULT_ONNX_RUNTIME,
    onnx_model: Path = DEFAULT_ONNX_MODEL,
    model_readme: Path = DEFAULT_MODEL_README,
    file_asr_models: Path = DEFAULT_FILE_ASR_MODELS,
    onnx_pack_manifest: Path = ONNX_PACK_MANIFEST,
    file_asr_pack_manifest: Path = FILE_ASR_PACK_MANIFEST,
) -> dict[str, Any]:
    output = _resolve_output(output)
    onnx_pack = validate_onnx_pack(
        runtime_root=onnx_runtime,
        model_root=onnx_model,
        model_readme=model_readme,
        manifest_path=onnx_pack_manifest,
    )
    verified_file_pack = package_runtime.validate_controlled_model_pack(
        model_pack_root=file_asr_models,
        model_pack_manifest=file_asr_pack_manifest,
    )
    backend_venv = backend_venv.resolve()
    funasr_venv = funasr_venv.resolve()
    backend_python = _python_home_from_venv(backend_venv)
    funasr_python = _python_home_from_venv(funasr_venv)
    manifest = windows_manifest(_read_json(BASE_MANIFEST), onnx_pack)

    if output.exists():
        shutil.rmtree(_extended_windows_path(output))
    _extended_windows_path(output).mkdir(parents=True)
    _copy_tree(backend_python, output / "runtime/backend-python", python_home=True)
    _copy_tree(
        backend_venv / "Lib/site-packages",
        output / "runtime/backend-venv/Lib/site-packages",
    )
    _copy_tree(funasr_python, output / "runtime/funasr-python", python_home=True)
    _copy_tree(
        funasr_venv / "Lib/site-packages",
        output / "runtime/funasr-venv/Lib/site-packages",
    )
    _copy_tree(onnx_pack["runtime_root"], output / "runtime/funasr-onnx")
    _stage_application(output)

    realtime_root = output / manifest["realtime_model"]["root"]
    realtime_root.mkdir(parents=True)
    for relative in manifest["realtime_model"]["required_files"]:
        _copy_file(onnx_pack["model_root"] / relative, realtime_root / relative)
    notice_root = output / "licenses/models/realtime-asr-onnx-zh-cn-20260722"
    notice_root.mkdir(parents=True)
    _copy_file(onnx_pack["model_readme"], notice_root / "upstream-README.md")
    _copy_file(onnx_pack["manifest_path"], notice_root / "model-pack.manifest.json")
    manifest["required_files"].extend(
        [
            "licenses/models/realtime-asr-onnx-zh-cn-20260722/upstream-README.md",
            "licenses/models/realtime-asr-onnx-zh-cn-20260722/model-pack.manifest.json",
        ]
    )
    manifest["required_files"] = sorted(set(manifest["required_files"]))
    package_runtime._stage_controlled_model_pack(
        bundle=output,
        manifest=manifest,
        verified_pack=verified_file_pack,
    )
    write_windows_launchers(output, manifest)
    package_runtime.remove_python_bytecode(output)
    package_runtime._write_runtime_manifest(output, manifest)
    manifest = package_runtime.seal_runtime_bundle_inventory(output, manifest)

    backend_python_record = _add_component(
        bundle=output,
        manifest=manifest,
        name="backend.python_runtime",
        relative="runtime/backend-python",
        kind="directory",
        version="python-3.13-win_amd64",
    )
    backend_venv_record = _add_component(
        bundle=output,
        manifest=manifest,
        name="backend.site_packages",
        relative="runtime/backend-venv",
        kind="directory",
        version="backend-lock-20260722",
    )
    funasr_python_record = _add_component(
        bundle=output,
        manifest=manifest,
        name="shared_asr.python_runtime",
        relative="runtime/funasr-python",
        kind="directory",
        version="python-3.12-win_amd64",
    )
    onnx_runtime_record = _add_component(
        bundle=output,
        manifest=manifest,
        name="realtime_asr.onnx_runtime",
        relative="runtime/funasr-onnx",
        kind="directory",
        version=onnx_pack["manifest"]["version"],
    )
    offline_worker_record = _add_component(
        bundle=output,
        manifest=manifest,
        name="realtime_asr.offline_refiner_worker",
        relative=manifest["workers"]["offline_refiner"],
        kind="file",
        version="funasr-offline-refiner-worker-v1",
    )
    _add_component(
        bundle=output,
        manifest=manifest,
        name="shared_asr.sitecustomize",
        relative="app/code/asr_runtime/scripts/sitecustomize.py",
        kind="file",
        version="split-site-packages-bootstrap-v1",
    )
    for name, relative in manifest["launchers"].items():
        _add_component(
            bundle=output,
            manifest=manifest,
            name=f"launcher.{name}",
            relative=relative,
            kind="file",
            version="windows-launcher-v1",
        )
    manifest["realtime_runtime"].update(
        {
            "size_bytes": onnx_runtime_record["size_bytes"],
            "sha256": onnx_runtime_record["sha256"],
        }
    )
    manifest["packaged_python"] = {
        "backend": backend_python_record,
        "backend_site_packages": backend_venv_record,
        "funasr": funasr_python_record,
    }
    manifest["offline_refiner"] = {
        "worker": offline_worker_record,
        "runtime": manifest["runtimes"]["funasr"]["executable"],
        "models": {
            name: details["root"]
            for name, details in manifest["file_asr"]["models"].items()
        },
    }
    package_runtime._write_runtime_manifest(output, manifest)
    package_runtime.validate_runtime_bundle(output)

    report = {
        "schema_version": WINDOWS_RUNTIME_REPORT_SCHEMA,
        "status": "built_internal_controlled_runtime",
        "platform": "windows",
        "architecture": "x86_64",
        "bundle_path": str(output.relative_to(REPO_ROOT)),
        "bundle_inventory": package_runtime._directory_inventory(
            output,
            allowed_root=(REPO_ROOT / "artifacts/tmp").resolve(),
        ),
        "realtime_engine": "onnx",
        "realtime_model": {
            "version": manifest["realtime_model"]["version"],
            "sha256": manifest["realtime_model"]["sha256"],
        },
        "offline_refiner_bundled": True,
        "component_count": len(manifest["component_inventory"]["components"]),
        "counts_as_internal_runtime_bundle": True,
        "counts_as_signed_installer": False,
        "counts_as_public_release": False,
        "remaining_blockers": [
            "public_model_redistribution_unresolved",
            "windows_installer_not_built_or_signed",
            "packaged_mainline_not_yet_executed",
            "macos_onnx_runtime_not_built_on_macos",
        ],
    }
    report_path = output.parent / "windows-runtime-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "build"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--backend-venv", type=Path, default=DEFAULT_BACKEND_VENV)
    parser.add_argument("--funasr-venv", type=Path, default=DEFAULT_FUNASR_VENV)
    parser.add_argument("--onnx-runtime", type=Path, default=DEFAULT_ONNX_RUNTIME)
    parser.add_argument("--onnx-model", type=Path, default=DEFAULT_ONNX_MODEL)
    parser.add_argument("--model-readme", type=Path, default=DEFAULT_MODEL_README)
    parser.add_argument("--file-asr-models", type=Path, default=DEFAULT_FILE_ASR_MODELS)
    parser.add_argument("--onnx-pack-manifest", type=Path, default=ONNX_PACK_MANIFEST)
    parser.add_argument("--file-asr-pack-manifest", type=Path, default=FILE_ASR_PACK_MANIFEST)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "preflight":
        onnx_pack = validate_onnx_pack(
            runtime_root=args.onnx_runtime,
            model_root=args.onnx_model,
            model_readme=args.model_readme,
            manifest_path=args.onnx_pack_manifest,
        )
        package_runtime.validate_controlled_model_pack(
            model_pack_root=args.file_asr_models,
            model_pack_manifest=args.file_asr_pack_manifest,
        )
        report = {
            "schema_version": WINDOWS_RUNTIME_REPORT_SCHEMA,
            "status": "ready_to_build",
            "platform": "windows",
            "architecture": "x86_64",
            "onnx_runtime_sha256": onnx_pack["runtime_inventory"]["sha256"],
            "onnx_model_sha256": onnx_pack["model_inventory"]["sha256"],
            "backend_python_home_ready": _python_home_from_venv(args.backend_venv).is_dir(),
            "funasr_python_home_ready": _python_home_from_venv(args.funasr_venv).is_dir(),
            "downloads_performed": False,
            "audio_capture_performed": False,
            "remote_provider_called": False,
        }
    else:
        report = build_windows_runtime(
            output=args.output,
            backend_venv=args.backend_venv,
            funasr_venv=args.funasr_venv,
            onnx_runtime=args.onnx_runtime,
            onnx_model=args.onnx_model,
            model_readme=args.model_readme,
            file_asr_models=args.file_asr_models,
            onnx_pack_manifest=args.onnx_pack_manifest,
            file_asr_pack_manifest=args.file_asr_pack_manifest,
        )
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
