"""Resolve and run the local file-transcription runtime."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.batch_transcribe")

_RUNTIME_MANIFEST_SCHEMA = "meeting_copilot.runtime_bundle.v1"
_COMPONENT_INVENTORY_SCHEMA = "meeting_copilot.runtime_component_inventory.v1"
_RUNTIME_MANIFEST_ENV = "MEETING_COPILOT_RUNTIME_MANIFEST"
_COMPONENT_ENV = {
    "funasr_python": "MEETING_COPILOT_BATCH_FUNASR_PYTHON",
    "worker": "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER",
    "offline_model": "MEETING_COPILOT_FILE_ASR_MODEL_DIR",
    "vad_model": "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR",
    "punc_model": "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR",
}
_COMPONENT_KINDS = {
    "funasr_python": "file",
    "worker": "file",
    "offline_model": "directory",
    "vad_model": "directory",
    "punc_model": "directory",
    "converter": "file",
}
_CONVERTER_ENV = ("MEETING_COPILOT_FFMPEG", "IMAGEIO_FFMPEG_EXE")
_SEALED_COMPONENT_NAMES = {
    "funasr_python": "file_asr.python_launcher",
    "worker": "file_asr.worker",
    "offline_model": "file_asr.model.offline",
    "vad_model": "file_asr.model.vad",
    "punc_model": "file_asr.model.punc",
    "converter": "file_asr.converter",
}
_MODEL_COMPONENTS = ("offline_model", "vad_model", "punc_model")
_SUPPORTED_IMPORT_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".mp4", ".mov")
_VIDEO_EXTENSIONS = {".mp4", ".mov"}
_AUDIO_NEEDS_CONVERT = {".mp3", ".m4a", ".aac", ".flac"}
_FORBIDDEN_PATH_MARKERS = (
    "/.cache/modelscope/",
    "/.cache/huggingface/",
    "/.cache/torch/",
    "/.venv/",
    "/.venv-funasr/",
)
_ffmpeg_path: str | None = None
_ffmpeg_packaged_mode: bool | None = None


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _safe_manifest_relative_path(value: Any, *, field: str) -> str:
    relative = str(value or "").strip()
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"runtime manifest {field} must be a safe bundle-relative path")
    return path.as_posix()


def _read_runtime_manifest(
    environ: Mapping[str, str],
) -> tuple[Path | None, Path | None, dict[str, Any], list[str]]:
    raw_path = str(environ.get(_RUNTIME_MANIFEST_ENV) or "").strip()
    if not raw_path:
        return None, None, {}, []
    manifest_path = Path(raw_path).expanduser().resolve(strict=False)
    errors: list[str] = []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return manifest_path, manifest_path.parent, {}, [f"runtime_manifest_unreadable:{exc}"]
    if not isinstance(payload, dict) or payload.get("schema_version") != _RUNTIME_MANIFEST_SCHEMA:
        errors.append("runtime_manifest_schema_invalid")
        payload = {}
    return manifest_path, manifest_path.parent, payload, errors


def _manifest_component_specs(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "funasr_python": {
            "paths": [
                _nested(payload, "file_asr", "runtime", "executable"),
                _nested(payload, "runtimes", "funasr", "venv_executable"),
            ],
            "hashes": [],
            "required_files": [],
        },
        "worker": {
            "paths": [
                _nested(payload, "workers", "file_asr"),
                _nested(payload, "worker_inventory", "file_asr", "path"),
                _nested(payload, "file_asr", "worker", "path"),
            ],
            "hashes": [
                _nested(payload, "worker_inventory", "file_asr", "sha256"),
                _nested(payload, "file_asr", "worker", "sha256"),
            ],
            "required_files": [],
        },
        "offline_model": {
            "paths": [_nested(payload, "file_asr", "models", "offline", "root")],
            "hashes": [_nested(payload, "file_asr", "models", "offline", "sha256")],
            "required_files": list(
                _nested(payload, "file_asr", "models", "offline", "required_files") or []
            ),
        },
        "vad_model": {
            "paths": [_nested(payload, "file_asr", "models", "vad", "root")],
            "hashes": [_nested(payload, "file_asr", "models", "vad", "sha256")],
            "required_files": list(
                _nested(payload, "file_asr", "models", "vad", "required_files") or []
            ),
        },
        "punc_model": {
            "paths": [_nested(payload, "file_asr", "models", "punc", "root")],
            "hashes": [_nested(payload, "file_asr", "models", "punc", "sha256")],
            "required_files": list(
                _nested(payload, "file_asr", "models", "punc", "required_files") or []
            ),
        },
        "converter": {
            "paths": [_nested(payload, "file_asr", "converter", "path")],
            "hashes": [_nested(payload, "file_asr", "converter", "sha256")],
            "required_files": [],
        },
    }


def _valid_sha256(value: Any) -> bool:
    normalized = str(value or "")
    return len(normalized) == 64 and all(character in "0123456789abcdef" for character in normalized)


def _sealed_inventory_components(
    payload: Mapping[str, Any],
    errors: list[str],
) -> Mapping[str, Any] | None:
    inventory = _nested(payload, "component_inventory")
    if not isinstance(inventory, Mapping) or inventory.get("schema_version") != _COMPONENT_INVENTORY_SCHEMA:
        errors.append("runtime_component_inventory_schema_invalid")
        return None
    if inventory.get("status") != "sealed":
        errors.append("runtime_component_inventory_not_sealed")
        return None
    components = inventory.get("components")
    if not isinstance(components, Mapping):
        errors.append("runtime_component_inventory_components_invalid")
        return None
    return components


def _resolve_sealed_path(
    bundle_root: Path,
    raw_path: Any,
    *,
    field: str,
    errors: list[str],
) -> tuple[Path | None, str | None]:
    try:
        relative = _safe_manifest_relative_path(raw_path, field=field)
    except ValueError:
        errors.append("unsafe_manifest_path")
        return None, None
    logical_path = Path(os.path.abspath(bundle_root / relative))
    resolved_root = bundle_root.resolve(strict=False)
    resolved_target = logical_path.resolve(strict=False)
    try:
        logical_path.relative_to(resolved_root)
        resolved_target.relative_to(resolved_root)
    except ValueError:
        errors.append("path_escapes_runtime_bundle")
    return logical_path, relative


def _sealed_component(
    *,
    name: str,
    bundle_root: Path,
    inventory_components: Mapping[str, Any] | None,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    required_files = [str(value) for value in spec.get("required_files") or []]
    component_errors: list[str] = []
    inventory_name = _SEALED_COMPONENT_NAMES[name]
    record = inventory_components.get(inventory_name) if inventory_components is not None else None
    if not isinstance(record, Mapping):
        if name in {"funasr_python", "worker"} and inventory_components is not None:
            component_errors.append("sealed_component_missing")
        return {
            "path": None,
            "source": "sealed_manifest",
            "required_files": required_files,
            "configuration_errors": component_errors,
        }
    if record.get("kind") != _COMPONENT_KINDS[name]:
        component_errors.append("sealed_component_kind_mismatch")
    resolved, relative = _resolve_sealed_path(
        bundle_root,
        record.get("path"),
        field=f"component_inventory.{inventory_name}.path",
        errors=component_errors,
    )
    if relative is not None:
        for manifest_path in spec.get("paths") or []:
            try:
                mirrored_relative = _safe_manifest_relative_path(
                    manifest_path,
                    field=f"{name}.path",
                )
            except ValueError:
                component_errors.append("manifest_component_path_mismatch")
                break
            if mirrored_relative != relative:
                component_errors.append("manifest_component_path_mismatch")
                break
    sealed_hash = str(record.get("sha256") or "")
    if not _valid_sha256(sealed_hash):
        component_errors.append("sealed_component_hash_invalid")
    elif any(str(value or "").lower() != sealed_hash for value in spec.get("hashes") or []):
        component_errors.append("manifest_component_hash_mismatch")
    return {
        "path": resolved,
        "source": "sealed_manifest",
        "required_files": required_files,
        "configuration_errors": component_errors,
        "sha256": sealed_hash or None,
    }


def _shared_runtime_errors(
    payload: Mapping[str, Any],
    bundle_root: Path,
    inventory_components: Mapping[str, Any] | None,
) -> list[str]:
    if inventory_components is None:
        return []
    record = inventory_components.get("shared_asr.runtime")
    if not isinstance(record, Mapping):
        return ["sealed_shared_runtime_missing"]
    errors: list[str] = []
    if record.get("kind") != "directory":
        errors.append("sealed_shared_runtime_kind_mismatch")
    path, relative = _resolve_sealed_path(
        bundle_root,
        record.get("path"),
        field="component_inventory.shared_asr.runtime.path",
        errors=errors,
    )
    if relative is not None:
        mirrored_paths = (
            _nested(payload, "runtimes", "funasr", "root"),
            _nested(payload, "file_asr", "runtime", "root"),
        )
        paths_match = True
        for value in mirrored_paths:
            try:
                mirrored_relative = _safe_manifest_relative_path(
                    value,
                    field="shared_asr.runtime.path",
                )
            except ValueError:
                paths_match = False
                break
            if mirrored_relative != relative:
                paths_match = False
                break
        if not paths_match:
            errors.append("manifest_component_path_mismatch")
    sealed_hash = str(record.get("sha256") or "")
    mirrored_hashes = (
        _nested(payload, "runtimes", "funasr", "sha256"),
        _nested(payload, "file_asr", "runtime", "sha256"),
    )
    if not _valid_sha256(sealed_hash):
        errors.append("sealed_component_hash_invalid")
    elif any(str(value or "").lower() != sealed_hash for value in mirrored_hashes):
        errors.append("manifest_component_hash_mismatch")
    if path is not None and not path.is_dir():
        errors.append("sealed_shared_runtime_missing")
    return errors


def _forbidden_runtime_path(path: Path) -> bool:
    normalized = f"/{path.expanduser().resolve(strict=False).as_posix().lstrip('/')}".lower()
    return any(marker in normalized for marker in _FORBIDDEN_PATH_MARKERS)


def _resolve_runtime_components(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    effective_env = os.environ if environ is None else environ
    manifest_path, bundle_root, manifest, errors = _read_runtime_manifest(effective_env)
    manifest_specs = _manifest_component_specs(manifest)
    components: dict[str, dict[str, Any]] = {}
    packaged_mode = manifest_path is not None
    inventory_components = (
        _sealed_inventory_components(manifest, errors)
        if packaged_mode and manifest
        else None
    )
    if packaged_mode:
        assert bundle_root is not None
        for name, spec in manifest_specs.items():
            components[name] = _sealed_component(
                name=name,
                bundle_root=bundle_root,
                inventory_components=inventory_components,
                spec=spec,
            )
        components["funasr_python"]["configuration_errors"].extend(
            _shared_runtime_errors(manifest, bundle_root, inventory_components)
        )
    else:
        development_env = dict(_COMPONENT_ENV) | {"converter": _CONVERTER_ENV[0]}
        for name, env_name in development_env.items():
            env_value = str(effective_env.get(env_name) or "").strip()
            if name == "converter" and not env_value:
                env_value = str(effective_env.get(_CONVERTER_ENV[1]) or "").strip()
            resolved = Path(os.path.abspath(Path(env_value).expanduser())) if env_value else None
            component_errors: list[str] = []
            if resolved is not None and _forbidden_runtime_path(resolved):
                component_errors.append("development_or_user_cache_path_forbidden")
            components[name] = {
                "path": resolved,
                "source": "environment" if env_value else "unconfigured",
                "required_files": [],
                "configuration_errors": component_errors,
            }
    return {
        "manifest_path": manifest_path,
        "bundle_root": bundle_root,
        "packaged_mode": packaged_mode,
        "source": "manifest" if packaged_mode else "environment" if any(
            str(effective_env.get(value) or "").strip()
            for value in (*_COMPONENT_ENV.values(), *_CONVERTER_ENV)
        ) else "unconfigured",
        "manifest_errors": errors,
        "model_package": dict(_nested(manifest, "file_asr", "package") or {}),
        "components": components,
    }


def _component_status(name: str, component: Mapping[str, Any]) -> dict[str, Any]:
    path = component.get("path")
    errors = list(component.get("configuration_errors") or [])
    missing_required: list[str] = []
    if errors:
        status = "invalid"
    elif not isinstance(path, Path):
        status = "not_configured"
    elif _COMPONENT_KINDS[name] == "file" and not path.is_file():
        status = "missing"
    elif _COMPONENT_KINDS[name] == "directory" and not path.is_dir():
        status = "missing"
    else:
        for relative in component.get("required_files") or []:
            try:
                safe_relative = _safe_manifest_relative_path(relative, field=f"{name}.required_files")
            except ValueError:
                errors.append("unsafe_required_file_path")
                continue
            if not (path / safe_relative).is_file():
                missing_required.append(safe_relative)
        status = "invalid" if errors else "missing" if missing_required else "ready"
    return {
        "status": status,
        "source": str(component.get("source") or "unconfigured"),
        "reason": (
            errors[0]
            if errors
            else "required_files_missing"
            if missing_required
            else "path_missing"
            if status == "missing"
            else "path_not_configured"
            if status == "not_configured"
            else None
        ),
        "missing_required_files": missing_required,
    }


def _get_ffmpeg(
    *,
    packaged_mode: bool | None = None,
    bundle_root: Path | None = None,
    sealed_path: Path | None = None,
) -> str | None:
    global _ffmpeg_path, _ffmpeg_packaged_mode
    if packaged_mode is None:
        packaged_mode = bool(str(os.environ.get(_RUNTIME_MANIFEST_ENV) or "").strip())
    if packaged_mode:
        _ffmpeg_path = None
        _ffmpeg_packaged_mode = True
        if bundle_root is None or sealed_path is None:
            _log.warning("ffmpeg.packaged_sealed_component_missing")
            return None
        candidate = Path(os.path.abspath(sealed_path))
        try:
            candidate.resolve(strict=False).relative_to(bundle_root.resolve(strict=False))
        except ValueError:
            _log.warning("ffmpeg.packaged_path_outside_bundle", path=str(candidate))
            return None
        if not candidate.is_file():
            _log.warning("ffmpeg.packaged_path_missing", path=str(candidate))
            return None
        _ffmpeg_path = str(candidate)
        _log.info("ffmpeg.resolved_packaged", path=_ffmpeg_path)
        return _ffmpeg_path
    if _ffmpeg_path is not None and _ffmpeg_packaged_mode == packaged_mode:
        return _ffmpeg_path
    _ffmpeg_path = None
    _ffmpeg_packaged_mode = packaged_mode
    try:
        import imageio_ffmpeg

        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).expanduser().resolve(strict=False)
        allowed = True
        if _forbidden_runtime_path(candidate):
            _log.warning("ffmpeg.forbidden_path", path=str(candidate))
            allowed = False
        if allowed:
            _ffmpeg_path = str(candidate)
            _log.info("ffmpeg.resolved", path=_ffmpeg_path)
            return _ffmpeg_path
    except ImportError:
        _log.warning("ffmpeg.imageio_not_installed")
    except Exception as exc:
        _log.warning("ffmpeg.resolve_failed", error=str(exc))
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        _ffmpeg_path = system_ffmpeg
        _log.info("ffmpeg.resolved_system_binary", path=system_ffmpeg)
        return _ffmpeg_path
    return None


def ensure_wav_16k_mono(input_path: Path) -> Path:
    """Convert any audio/video file to 16kHz mono WAV using ffmpeg.

    All files (including .wav) are converted to ensure correct sample rate
    and channel count for FunASR's 16kHz mono requirement.
    """
    suffix = input_path.suffix.lower()
    if suffix not in _VIDEO_EXTENSIONS and suffix not in _AUDIO_NEEDS_CONVERT and suffix != ".wav":
        return input_path  # unknown format, let FunASR handle it

    runtime = _resolve_runtime_components()
    converter = runtime["components"]["converter"]
    converter_ready = _component_status("converter", converter)["status"] == "ready"
    ffmpeg = _get_ffmpeg(
        packaged_mode=runtime["packaged_mode"],
        bundle_root=runtime["bundle_root"],
        sealed_path=converter["path"]
        if runtime["packaged_mode"] and converter_ready
        else None,
    )
    if ffmpeg is None:
        if suffix == ".wav":
            return input_path
        raise RuntimeError(
            f"文件格式 {suffix} 需要ffmpeg转换，但ffmpeg未安装。"
            "请运行 pip install imageio-ffmpeg 安装。"
        )
    output_path = input_path.with_suffix(".16k.wav")
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", str(input_path), "-vn", "-ar", "16000",
             "-ac", "1", "-f", "wav", "-y", str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg转码失败: {proc.stderr[-400:]}")
        _log.info("ffmpeg.converted", input=str(input_path), output=str(output_path))
        return output_path
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg转码超时（超过120秒）")


def capability_status(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return concrete local file-ASR and advertised import-format capabilities."""
    runtime = _resolve_runtime_components(environ)
    components = {
        name: _component_status(name, component)
        for name, component in runtime["components"].items()
    }
    invalid_components = [name for name, item in components.items() if item["status"] == "invalid"]
    missing_components = [
        name for name in (*_COMPONENT_ENV.keys(),) if components[name]["status"] != "ready"
    ]
    model_missing = [name for name in _MODEL_COMPONENTS if components[name]["status"] != "ready"]
    runtime_missing = [
        name for name in ("funasr_python", "worker") if components[name]["status"] != "ready"
    ]
    manifest_errors = list(runtime["manifest_errors"])
    if manifest_errors or invalid_components:
        status = "invalid_runtime_configuration"
    elif runtime_missing:
        status = "file_asr_runtime_not_installed"
    elif model_missing:
        status = "file_asr_models_not_installed"
    else:
        status = "ready"
    file_asr_available = status == "ready"

    packaged_mode = runtime["packaged_mode"]
    ffmpeg_path: str | None
    converter_status = components["converter"]
    if packaged_mode:
        ffmpeg_path = _get_ffmpeg(
            packaged_mode=True,
            bundle_root=runtime["bundle_root"],
            sealed_path=runtime["components"]["converter"]["path"]
            if converter_status["status"] == "ready"
            else None,
        )
    elif environ is not None and environ is not os.environ:
        converter_path = runtime["components"]["converter"]["path"]
        ffmpeg_path = str(converter_path) if converter_status["status"] == "ready" else None
    else:
        ffmpeg_path = _get_ffmpeg(packaged_mode=False)
    format_capabilities: dict[str, dict[str, Any]] = {}
    for extension in _SUPPORTED_IMPORT_EXTENSIONS:
        converter_required = extension != ".wav"
        available = file_asr_available and (not converter_required or ffmpeg_path is not None)
        format_capabilities[extension.lstrip(".")] = {
            "available": available,
            "converter_required": converter_required,
            "missing_components": [
                *missing_components,
                *(["ffmpeg"] if converter_required and ffmpeg_path is None else []),
            ],
        }
    return {
        "schema_version": "meeting_copilot.file_asr_capability.v1",
        "status": status,
        "available": file_asr_available,
        "configuration_source": runtime["source"],
        "manifest_status": "invalid" if manifest_errors else "loaded" if runtime["manifest_path"] else "not_configured",
        "components": {
            name: item for name, item in components.items() if name != "converter"
        }
        | {
            "ffmpeg": {
                "status": "ready" if ffmpeg_path else "missing",
                "source": "bundled" if packaged_mode and ffmpeg_path else "environment_or_system" if ffmpeg_path else "unavailable",
                "reason": None if ffmpeg_path else converter_status["reason"] or "path_missing",
                "missing_required_files": [],
            }
        },
        "missing_components": missing_components,
        "invalid_components": invalid_components,
        "manifest_errors": manifest_errors,
        "model_package": runtime["model_package"],
        "supported_import_formats": list(_SUPPORTED_IMPORT_EXTENSIONS),
        "formats": format_capabilities,
        "network_offline": None,
        "remote_asr_used": False,
        "model_download_performed": False,
    }


def import_format_capability(
    path_or_extension: Path | str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    raw = str(path_or_extension).strip().lower()
    extension = raw if raw.startswith(".") and "/" not in raw else Path(raw).suffix.lower()
    capability = capability_status(environ)
    item = capability["formats"].get(extension.lstrip("."))
    if item is None:
        return {
            "format": extension,
            "supported": False,
            "available": False,
            "status": "unsupported_import_format",
            "missing_components": [],
        }
    return {
        "format": extension,
        "supported": True,
        "available": bool(item["available"]),
        "status": "ready"
        if item["available"]
        else "format_converter_not_installed"
        if capability["available"]
        else capability["status"],
        "missing_components": list(item["missing_components"]),
    }


def is_available() -> bool:
    return bool(capability_status()["available"])


def transcribe_file_report(
    audio_path: Path,
    timeout: int = 180,
    *,
    preserve_preprocessed: bool = False,
) -> dict[str, Any]:
    runtime = _resolve_runtime_components()
    capability = capability_status()
    if not capability["available"]:
        missing = ", ".join(capability["missing_components"] or capability["invalid_components"])
        raise RuntimeError(f"本地文件转写组件未安装或配置无效: {missing}")
    resolved = {
        name: item["path"]
        for name, item in runtime["components"].items()
    }
    wav_path = ensure_wav_16k_mono(audio_path)
    _log.info("batch.transcribe.start", audio=str(wav_path), original=str(audio_path))
    try:
        child_env = os.environ.copy()
        child_env.pop("PYTHONHOME", None)
        child_env.pop("PYTHONPATH", None)
        if runtime["packaged_mode"]:
            for env_name in (*_COMPONENT_ENV.values(), *_CONVERTER_ENV):
                child_env.pop(env_name, None)
        child_env.update({
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        })
        proc = subprocess.run(
            [
                str(resolved["funasr_python"]),
                str(resolved["worker"]),
                str(wav_path),
                "--offline-batch",
                "--model",
                str(resolved["offline_model"]),
                "--vad-model",
                str(resolved["vad_model"]),
                "--punc-model",
                str(resolved["punc_model"]),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
        if proc.returncode != 0:
            _log.error("batch.transcribe.failed", returncode=proc.returncode, stderr=proc.stderr[-400:])
            raise RuntimeError(f"本地文件转写失败: {proc.stderr[-300:]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("本地文件转写组件返回了无效结果") from exc
        items = list(payload.get("items") or [])
        if not items:
            raise RuntimeError("本地文件转写未返回文字结果")
        item = dict(items[0])
        batch = {key: value for key, value in payload.items() if key not in {"items"}}
        item["batch"] = batch
        if preserve_preprocessed:
            item["normalized_audio_path"] = str(wav_path)
        _log.info("batch.transcribe.end", chars=len(str(item.get("text") or "")))
        return item
    finally:
        if wav_path != audio_path and not preserve_preprocessed:
            wav_path.unlink(missing_ok=True)


def transcribe_file(audio_path: Path, timeout: int = 180) -> str:
    return str(transcribe_file_report(audio_path, timeout=timeout).get("text") or "")
