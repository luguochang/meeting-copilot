from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


RUNTIME_MANIFEST_ENV = "MEETING_COPILOT_RUNTIME_MANIFEST"
RUNTIME_MANIFEST_SCHEMA = "meeting_copilot.runtime_bundle.v1"
COMPONENT_INVENTORY_SCHEMA = "meeting_copilot.runtime_component_inventory.v1"


@dataclass(frozen=True)
class RuntimeManifest:
    path: Path | None
    bundle_root: Path | None
    payload: Mapping[str, Any]
    errors: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class RuntimePathResolution:
    path: Path | None
    source: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeEnvironmentResolution:
    values: Mapping[str, str]
    errors: tuple[str, ...] = ()


def venv_python_path(venv_root: Path, *, platform_name: str | None = None) -> Path:
    """Return the native interpreter path for a virtual environment."""

    platform = os.name if platform_name is None else str(platform_name)
    if platform == "nt":
        return Path(venv_root) / "Scripts" / "python.exe"
    return Path(venv_root) / "bin" / "python"


def manifest_value(payload: Mapping[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def read_runtime_manifest(
    environ: Mapping[str, str] | None = None,
) -> RuntimeManifest:
    effective_env = os.environ if environ is None else environ
    raw_path = str(effective_env.get(RUNTIME_MANIFEST_ENV) or "").strip()
    if not raw_path:
        return RuntimeManifest(path=None, bundle_root=None, payload={})
    manifest_path = Path(raw_path).expanduser().resolve(strict=False)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return RuntimeManifest(
            path=manifest_path,
            bundle_root=manifest_path.parent,
            payload={},
            errors=(f"runtime_manifest_unreadable:{exc}",),
        )
    if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        return RuntimeManifest(
            path=manifest_path,
            bundle_root=manifest_path.parent,
            payload={},
            errors=("runtime_manifest_schema_invalid",),
        )
    return RuntimeManifest(
        path=manifest_path,
        bundle_root=manifest_path.parent,
        payload=payload,
    )


def safe_manifest_relative_path(value: Any, *, field: str) -> str:
    relative = str(value or "").strip()
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"runtime manifest {field} must be a safe bundle-relative path")
    return path.as_posix()


def resolve_manifest_path(
    manifest: RuntimeManifest,
    value: Any,
    *,
    field: str,
) -> RuntimePathResolution:
    errors = list(manifest.errors)
    if not manifest.configured or manifest.bundle_root is None:
        return RuntimePathResolution(None, "unconfigured", tuple(errors))
    try:
        relative = safe_manifest_relative_path(value, field=field)
    except ValueError:
        errors.append("unsafe_manifest_path")
        return RuntimePathResolution(None, "sealed_manifest", tuple(errors))

    logical_root = Path(os.path.abspath(manifest.bundle_root))
    logical_path = Path(os.path.abspath(logical_root / relative))
    resolved_root = manifest.bundle_root.resolve(strict=False)
    resolved_path = logical_path.resolve(strict=False)
    try:
        logical_path.relative_to(logical_root)
        resolved_path.relative_to(resolved_root)
    except ValueError:
        errors.append("path_escapes_runtime_bundle")
        return RuntimePathResolution(None, "sealed_manifest", tuple(errors))
    return RuntimePathResolution(logical_path, "sealed_manifest", tuple(errors))


def resolve_manifest_component(
    manifest: RuntimeManifest,
    *,
    component_name: str,
    expected_kind: str,
    mirrored_fields: Sequence[Sequence[str]] = (),
) -> RuntimePathResolution:
    errors = list(manifest.errors)
    if not manifest.configured:
        return RuntimePathResolution(None, "unconfigured", tuple(errors))
    inventory = manifest_value(manifest.payload, "component_inventory")
    if not isinstance(inventory, Mapping) or inventory.get("schema_version") != COMPONENT_INVENTORY_SCHEMA:
        errors.append("runtime_component_inventory_schema_invalid")
        return RuntimePathResolution(None, "sealed_manifest", tuple(errors))
    if inventory.get("status") != "sealed":
        errors.append("runtime_component_inventory_not_sealed")
        return RuntimePathResolution(None, "sealed_manifest", tuple(errors))
    components = inventory.get("components")
    if not isinstance(components, Mapping):
        errors.append("runtime_component_inventory_components_invalid")
        return RuntimePathResolution(None, "sealed_manifest", tuple(errors))
    record = components.get(component_name)
    if not isinstance(record, Mapping):
        errors.append("sealed_component_missing")
        return RuntimePathResolution(None, "sealed_manifest", tuple(errors))
    if record.get("kind") != expected_kind:
        errors.append("sealed_component_kind_mismatch")

    resolved = resolve_manifest_path(
        manifest,
        record.get("path"),
        field=f"component_inventory.{component_name}.path",
    )
    errors.extend(error for error in resolved.errors if error not in errors)
    try:
        component_relative = safe_manifest_relative_path(
            record.get("path"),
            field=f"component_inventory.{component_name}.path",
        )
    except ValueError:
        component_relative = None
    if component_relative is not None:
        for keys in mirrored_fields:
            try:
                mirrored = safe_manifest_relative_path(
                    manifest_value(manifest.payload, *keys),
                    field=".".join(keys),
                )
            except ValueError:
                errors.append("manifest_component_path_mismatch")
                break
            if mirrored != component_relative:
                errors.append("manifest_component_path_mismatch")
                break
    return RuntimePathResolution(
        resolved.path if not errors else None,
        "sealed_manifest",
        tuple(dict.fromkeys(errors)),
    )


def packaged_funasr_environment(
    manifest: RuntimeManifest,
    *,
    worker_path: Path | None,
    include_realtime_runtime: bool,
) -> RuntimeEnvironmentResolution:
    if not manifest.configured:
        return RuntimeEnvironmentResolution({})

    errors: list[str] = list(manifest.errors)
    python_home = resolve_manifest_component(
        manifest,
        component_name="shared_asr.python_runtime",
        expected_kind="directory",
        mirrored_fields=(("packaged_python", "funasr", "path"),),
    )
    shared_runtime = resolve_manifest_component(
        manifest,
        component_name="shared_asr.runtime",
        expected_kind="directory",
        mirrored_fields=(
            ("runtimes", "funasr", "root"),
            ("file_asr", "runtime", "root"),
        ),
    )
    site_packages = resolve_manifest_path(
        manifest,
        manifest_value(manifest.payload, "runtimes", "funasr", "site_packages"),
        field="runtimes.funasr.site_packages",
    )
    realtime_runtime = None
    if include_realtime_runtime:
        realtime_runtime = resolve_manifest_component(
            manifest,
            component_name="realtime_asr.onnx_runtime",
            expected_kind="directory",
            mirrored_fields=(("realtime_runtime", "root"),),
        )
    for result in (python_home, shared_runtime, site_packages, realtime_runtime):
        if result is not None:
            errors.extend(error for error in result.errors if error not in errors)

    if shared_runtime.path is not None and site_packages.path is not None:
        try:
            site_packages.path.resolve(strict=False).relative_to(
                shared_runtime.path.resolve(strict=False)
            )
        except ValueError:
            errors.append("funasr_site_packages_outside_shared_runtime")
    if worker_path is None:
        errors.append("funasr_worker_missing")
    elif manifest.bundle_root is not None:
        try:
            worker_path.resolve(strict=False).relative_to(
                manifest.bundle_root.resolve(strict=False)
            )
        except ValueError:
            errors.append("funasr_worker_outside_runtime_bundle")

    if errors:
        return RuntimeEnvironmentResolution({}, tuple(dict.fromkeys(errors)))
    python_paths = [
        *([realtime_runtime.path] if realtime_runtime is not None else []),
        site_packages.path,
        worker_path.parent if worker_path is not None else None,
    ]
    return RuntimeEnvironmentResolution(
        {
            "PYTHONHOME": str(python_home.path),
            "PYTHONPATH": os.pathsep.join(str(path) for path in python_paths if path is not None),
            "MEETING_COPILOT_FUNASR_SITE_PACKAGES": str(site_packages.path),
        }
    )
