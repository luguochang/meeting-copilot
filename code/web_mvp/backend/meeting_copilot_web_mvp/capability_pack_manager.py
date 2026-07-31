"""Install validated offline capability bundles into a user-owned runtime."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, BinaryIO, Iterable
import uuid
import zipfile


OFFLINE_PACKAGE_SCHEMA = "meeting_copilot.offline_capability_bundle.v1"
PACK_ARCHIVE_SCHEMA = "meeting_copilot.capability_pack_archive.v1"
ACTIVE_RUNTIME_SCHEMA = "meeting_copilot.active_runtime.v1"
CAPABILITY_STATE_SCHEMA = "meeting_copilot.local_capability_state.v1"
STATUS_SCHEMA = "meeting_copilot.local_capability_status.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SUPPORTED_PLATFORM = "windows-x86_64" if os.name == "nt" else "unsupported"


class CapabilityPackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class InstalledRuntime:
    relative_path: str
    absolute_path: Path
    state: dict[str, Any]


def _sha256_stream(handle: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _io_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    absolute = str(path.resolve(strict=False))
    if absolute.startswith("\\\\?\\"):
        return path
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _sha256_file(path: Path) -> str:
    with _io_path(path).open("rb") as handle:
        return _sha256_stream(handle)[0]


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _read_json_bytes(raw: bytes, *, code: str, message: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityPackError(code, message) from exc
    if not isinstance(value, dict):
        raise CapabilityPackError(code, message)
    return value


def _safe_relative(value: Any, *, code: str = "unsafe_package_path") -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if (
        not text
        or text.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise CapabilityPackError(code, "离线包包含不安全的文件路径")
    return path.as_posix()


def _safe_identifier(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise CapabilityPackError("invalid_package_manifest", f"离线包 {field} 无效")
    return text


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _validated_infos(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        normalized = _safe_relative(info.filename)
        if normalized in infos:
            raise CapabilityPackError("duplicate_package_member", "离线包包含重复文件")
        if info.flag_bits & 0x1:
            raise CapabilityPackError("encrypted_package_member", "离线包不能包含加密文件")
        if _zip_member_is_symlink(info):
            raise CapabilityPackError("symlink_package_member", "离线包不能包含符号链接")
        infos[normalized] = info
    return infos


def _directory_inventory(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        raise CapabilityPackError("installed_component_missing", "能力包组件安装不完整")
    entries: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path).as_posix()
        if candidate.is_symlink():
            raise CapabilityPackError("installed_component_symlink", "能力包组件包含符号链接")
        if candidate.is_file():
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size_bytes": candidate.stat().st_size,
                    "sha256": _sha256_file(candidate),
                }
            )
    payload = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "size_bytes": sum(int(item["size_bytes"]) for item in entries),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "file_count": len(entries),
        "symlink_count": 0,
    }


def _measure_component(runtime_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    relative = _safe_relative(record.get("path"))
    path = runtime_root / relative
    kind = record.get("kind")
    if kind == "file":
        if path.is_symlink() or not path.is_file():
            raise CapabilityPackError("installed_component_missing", "能力包组件安装不完整")
        return {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    if kind == "directory":
        return _directory_inventory(path)
    raise CapabilityPackError("pack_component_invalid", "能力包组件类型无效")


def _verify_components(runtime_root: Path, manifests: Iterable[dict[str, Any]]) -> None:
    for manifest in manifests:
        components = manifest.get("components")
        if not isinstance(components, dict) or not components:
            raise CapabilityPackError("pack_components_missing", "能力包缺少组件校验清单")
        for name, record in components.items():
            if not isinstance(record, dict):
                raise CapabilityPackError("pack_component_invalid", "能力包组件清单无效")
            observed = _measure_component(runtime_root, record)
            for field in ("size_bytes", "sha256", "file_count", "symlink_count"):
                if field in record and observed.get(field) != record.get(field):
                    raise CapabilityPackError(
                        "installed_component_hash_mismatch",
                        f"能力包组件校验失败：{name}",
                    )


def _component_inventory(manifest: dict[str, Any], *, code: str) -> dict[str, Any]:
    inventory = manifest.get("component_inventory")
    if not isinstance(inventory, dict):
        raise CapabilityPackError(code, "运行时组件清单无效")
    components = inventory.get("components")
    if inventory.get("status") != "sealed" or not isinstance(components, dict) or not components:
        raise CapabilityPackError(code, "运行时组件清单未密封")
    return inventory


def _merge_runtime_manifest(
    full_manifest: dict[str, Any],
    base_manifest: dict[str, Any],
    pack_manifests: Iterable[dict[str, Any]],
    runtime_root: Path,
) -> dict[str, Any]:
    merged = copy.deepcopy(full_manifest)
    full_inventory = _component_inventory(merged, code="runtime_component_inventory_invalid")
    base_inventory = _component_inventory(base_manifest, code="base_component_inventory_invalid")
    merged_components = copy.deepcopy(full_inventory["components"])
    verified_components: dict[str, dict[str, Any]] = {}
    for source in [base_inventory, *pack_manifests]:
        components = source.get("components")
        if not isinstance(components, dict):
            raise CapabilityPackError("pack_components_missing", "能力包缺少组件校验清单")
        for name, record in components.items():
            if not isinstance(record, dict):
                raise CapabilityPackError("pack_component_invalid", "能力包组件清单无效")
            verified_components[name] = copy.deepcopy(record)
    merged_components.update(verified_components)
    for name, record in list(merged_components.items()):
        if name in verified_components:
            continue
        if not isinstance(record, dict):
            raise CapabilityPackError("runtime_component_inventory_invalid", "运行时组件清单无效")
        resealed = copy.deepcopy(record)
        resealed.update(_measure_component(runtime_root, record))
        merged_components[name] = resealed
    full_inventory["status"] = "sealed"
    full_inventory["components"] = merged_components
    merged["distribution_profile"] = "full"
    merged["required_files"] = sorted(
        {
            str(item)
            for item in [
                *(full_manifest.get("required_files") or []),
                *(base_manifest.get("required_files") or []),
            ]
            if str(item).strip()
        }
    )
    merged["launchers"] = {
        **(full_manifest.get("launchers") or {}),
        **(base_manifest.get("launchers") or {}),
    }
    packaged_python = merged.get("packaged_python")
    if isinstance(packaged_python, dict):
        for field, component_name in (
            ("backend", "backend.python_runtime"),
            ("backend_site_packages", "backend.site_packages"),
        ):
            if component_name in merged_components:
                packaged_python[field] = copy.deepcopy(merged_components[component_name])
    return merged


class CapabilityPackManager:
    def __init__(
        self,
        capability_root: Path,
        *,
        source_runtime_bundle: Path | None,
        app_version: str = "0.1.0",
        platform_name: str = _SUPPORTED_PLATFORM,
        download_page_url: str | None = None,
    ) -> None:
        self.capability_root = capability_root.expanduser().resolve()
        self.source_runtime_bundle = (
            source_runtime_bundle.expanduser().resolve()
            if source_runtime_bundle is not None
            else None
        )
        self.app_version = app_version
        self.platform_name = platform_name
        self.download_page_url = str(download_page_url or "").strip() or None
        self.runtime_root = self.capability_root / "runtimes"
        self.staging_root = self.capability_root / "staging"
        self.active_path = self.capability_root / "active.json"

    def _active_runtime(self) -> InstalledRuntime | None:
        if not self.active_path.is_file():
            return None
        try:
            pointer = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(pointer, dict) or pointer.get("schema_version") != ACTIVE_RUNTIME_SCHEMA:
            return None
        try:
            relative = _safe_relative(pointer.get("runtime_path"), code="invalid_active_runtime")
        except CapabilityPackError:
            return None
        absolute = (self.capability_root / relative).resolve(strict=False)
        try:
            absolute.relative_to(self.capability_root)
        except ValueError:
            return None
        state_path = absolute / ".meeting-copilot-capability-state.json"
        manifest_path = absolute / "runtime-bundle-manifest.json"
        if not absolute.is_dir() or not state_path.is_file() or not manifest_path.is_file():
            return None
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(state, dict) or state.get("schema_version") != CAPABILITY_STATE_SCHEMA:
            return None
        return InstalledRuntime(relative, absolute, state)

    def status(self) -> dict[str, Any]:
        active = self._active_runtime()
        capabilities = set(active.state.get("capabilities") or []) if active else set()
        current_runtime = self.source_runtime_bundle
        current_runtime_is_active = False
        if active is not None and current_runtime is not None:
            try:
                current_runtime_is_active = current_runtime.samefile(active.absolute_path)
            except OSError:
                current_runtime_is_active = (
                    current_runtime.resolve() == active.absolute_path.resolve()
                )
        restart_required = bool(
            active is not None
            and (current_runtime is None or not current_runtime_is_active)
        )
        return {
            "schema_version": STATUS_SCHEMA,
            "platform": self.platform_name,
            "base_app_ready": True,
            "installed": active is not None,
            "package_id": active.state.get("package_id") if active else None,
            "package_version": active.state.get("package_version") if active else None,
            "installed_at": active.state.get("installed_at") if active else None,
            "realtime_asr_ready": "realtime_asr_zh_cn" in capabilities,
            "file_asr_ready": "file_asr_zh_cn" in capabilities,
            "restart_required": restart_required,
            "signature_status": active.state.get("signature_status") if active else None,
            "release_scope": active.state.get("release_scope") if active else None,
            "download_page_url": self.download_page_url,
            "import_available": bool(
                self.source_runtime_bundle is not None and self.source_runtime_bundle.is_dir()
            ),
            "errors": [] if active is not None or not self.active_path.exists() else ["active_runtime_invalid"],
        }

    def install_file(self, handle: BinaryIO, *, filename: str) -> dict[str, Any]:
        if not filename.lower().endswith(".mcpkg"):
            raise CapabilityPackError("unsupported_package_type", "请选择 .mcpkg 离线完整包")
        if self.source_runtime_bundle is None or not self.source_runtime_bundle.is_dir():
            raise CapabilityPackError("base_runtime_unavailable", "当前基础运行时不可用于安装能力包")
        try:
            handle.seek(0)
            with zipfile.ZipFile(handle) as outer:
                result = self._install_archive(outer)
        except zipfile.BadZipFile as exc:
            raise CapabilityPackError("invalid_offline_package", "离线包损坏或格式不正确") from exc
        return result

    def _install_archive(self, outer: zipfile.ZipFile) -> dict[str, Any]:
        infos = _validated_infos(outer)
        manifest_info = infos.get("offline-package-manifest.json")
        if manifest_info is None or manifest_info.file_size > 1024 * 1024:
            raise CapabilityPackError("offline_manifest_missing", "离线包清单缺失或过大")
        manifest_bytes = outer.read(manifest_info)
        manifest = _read_json_bytes(
            manifest_bytes,
            code="offline_manifest_invalid",
            message="离线包清单无效",
        )
        if manifest.get("schema_version") != OFFLINE_PACKAGE_SCHEMA:
            raise CapabilityPackError("offline_manifest_schema_invalid", "离线包清单版本不受支持")
        package_id = _safe_identifier(manifest.get("package_id"), field="package_id")
        package_version = _safe_identifier(manifest.get("version"), field="version")
        if manifest.get("platform") != self.platform_name:
            raise CapabilityPackError("offline_package_platform_mismatch", "离线包与当前系统平台不匹配")
        pack_records = manifest.get("packs")
        if not isinstance(pack_records, list) or not pack_records:
            raise CapabilityPackError("offline_pack_list_invalid", "离线包没有能力包内容")
        runtime_record = manifest.get("runtime_manifest")
        if not isinstance(runtime_record, dict):
            raise CapabilityPackError("runtime_manifest_record_invalid", "离线包运行时清单记录无效")
        runtime_relative = _safe_relative(runtime_record.get("path"))
        runtime_info = infos.get(runtime_relative)
        expected_runtime_size = int(runtime_record.get("size_bytes") or -1)
        expected_runtime_hash = str(runtime_record.get("sha256") or "").lower()
        if runtime_info is None or runtime_info.file_size != expected_runtime_size:
            raise CapabilityPackError("runtime_manifest_size_mismatch", "离线包运行时清单大小不匹配")
        runtime_bytes = outer.read(runtime_info)
        if hashlib.sha256(runtime_bytes).hexdigest() != expected_runtime_hash:
            raise CapabilityPackError("runtime_manifest_hash_mismatch", "离线包运行时清单校验失败")
        runtime_manifest = _read_json_bytes(
            runtime_bytes,
            code="runtime_manifest_invalid",
            message="离线包运行时清单无效",
        )
        if runtime_manifest.get("schema_version") != "meeting_copilot.runtime_bundle.v1":
            raise CapabilityPackError("runtime_manifest_schema_invalid", "离线包运行时清单版本不受支持")
        base_manifest_path = self.source_runtime_bundle / "runtime-bundle-manifest.json"
        try:
            if _io_path(base_manifest_path).stat().st_size > 4 * 1024 * 1024:
                raise CapabilityPackError("base_runtime_manifest_invalid", "基础运行时清单过大")
            base_manifest_bytes = _io_path(base_manifest_path).read_bytes()
        except OSError as exc:
            raise CapabilityPackError("base_runtime_manifest_invalid", "基础运行时清单不可用") from exc
        base_manifest = _read_json_bytes(
            base_manifest_bytes,
            code="base_runtime_manifest_invalid",
            message="基础运行时清单无效",
        )
        if (
            base_manifest.get("schema_version") != "meeting_copilot.runtime_bundle.v1"
            or base_manifest.get("platform") != "windows"
        ):
            raise CapabilityPackError("base_runtime_manifest_invalid", "基础运行时清单不受支持")
        base_inventory = _component_inventory(
            base_manifest,
            code="base_component_inventory_invalid",
        )
        _verify_components(_io_path(self.source_runtime_bundle), [base_inventory])

        required_free = int(manifest.get("required_free_space_bytes") or 0)
        self.capability_root.mkdir(parents=True, exist_ok=True)
        if required_free > 0 and shutil.disk_usage(self.capability_root).free < required_free:
            raise CapabilityPackError("insufficient_disk_space", "可用磁盘空间不足，完整导入建议预留 10 GiB")

        identity = hashlib.sha256(manifest_bytes).hexdigest()[:12]
        target_name = f"{package_version}-{identity}"
        target_relative = f"runtimes/{target_name}"
        target = self.capability_root / target_relative
        if target.is_dir():
            self._activate(target_relative, package_id, package_version)
            return self.status()

        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        staging = self.staging_root / uuid.uuid4().hex
        staging_runtime = staging / "runtime"
        nested_cache = staging / "archives"
        pack_manifests: list[dict[str, Any]] = []
        extracted_payloads: set[str] = set()
        try:
            shutil.copytree(
                _io_path(self.source_runtime_bundle),
                _io_path(staging_runtime),
            )
            _io_path(nested_cache).mkdir(parents=True)
            seen_pack_ids: set[str] = set()
            for raw_record in pack_records:
                if not isinstance(raw_record, dict):
                    raise CapabilityPackError("offline_pack_record_invalid", "能力包记录无效")
                pack_id = _safe_identifier(raw_record.get("pack_id"), field="pack_id")
                if pack_id in seen_pack_ids:
                    raise CapabilityPackError("duplicate_pack_id", "离线包包含重复能力包")
                seen_pack_ids.add(pack_id)
                archive_relative = _safe_relative(raw_record.get("archive_path"))
                archive_info = infos.get(archive_relative)
                expected_size = int(raw_record.get("archive_size_bytes") or -1)
                expected_hash = str(raw_record.get("archive_sha256") or "").lower()
                if archive_info is None or archive_info.file_size != expected_size:
                    raise CapabilityPackError("pack_archive_size_mismatch", f"能力包大小校验失败：{pack_id}")
                nested_path = nested_cache / f"{pack_id}.zip"
                with outer.open(archive_info) as source, _io_path(nested_path).open("wb") as destination:
                    digest = hashlib.sha256()
                    copied = 0
                    for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                        digest.update(chunk)
                        copied += len(chunk)
                        destination.write(chunk)
                if copied != expected_size or digest.hexdigest() != expected_hash:
                    raise CapabilityPackError("pack_archive_hash_mismatch", f"能力包校验失败：{pack_id}")
                with zipfile.ZipFile(_io_path(nested_path)) as nested:
                    nested_infos = _validated_infos(nested)
                    pack_info = nested_infos.get("pack-manifest.json")
                    if pack_info is None or pack_info.file_size > 1024 * 1024:
                        raise CapabilityPackError("pack_manifest_missing", f"能力包清单缺失：{pack_id}")
                    pack_manifest = _read_json_bytes(
                        nested.read(pack_info),
                        code="pack_manifest_invalid",
                        message=f"能力包清单无效：{pack_id}",
                    )
                    if (
                        pack_manifest.get("schema_version") != PACK_ARCHIVE_SCHEMA
                        or pack_manifest.get("pack_id") != pack_id
                        or pack_manifest.get("version") != raw_record.get("version")
                    ):
                        raise CapabilityPackError("pack_manifest_mismatch", f"能力包清单不匹配：{pack_id}")
                    self._extract_payload(
                        nested,
                        nested_infos,
                        staging_runtime,
                        extracted_payloads,
                    )
                    pack_manifests.append(pack_manifest)
            _verify_components(_io_path(staging_runtime), pack_manifests)
            merged_runtime_manifest = _merge_runtime_manifest(
                runtime_manifest,
                base_manifest,
                pack_manifests,
                _io_path(staging_runtime),
            )
            installed_runtime_bytes = _json_bytes(merged_runtime_manifest)
            installed_runtime_hash = hashlib.sha256(installed_runtime_bytes).hexdigest()
            _io_path(staging_runtime / "runtime-bundle-manifest.json").write_bytes(
                installed_runtime_bytes
            )
            installed_at = datetime.now(timezone.utc).isoformat()
            state = {
                "schema_version": CAPABILITY_STATE_SCHEMA,
                "package_id": package_id,
                "package_version": package_version,
                "platform": self.platform_name,
                "capabilities": list(manifest.get("capabilities") or []),
                "signature_status": manifest.get("signature_status"),
                "release_scope": manifest.get("release_scope"),
                "runtime_manifest_sha256": installed_runtime_hash,
                "source_runtime_manifest_sha256": expected_runtime_hash,
                "base_runtime_manifest_sha256": hashlib.sha256(base_manifest_bytes).hexdigest(),
                "packs": [
                    {
                        "pack_id": item["pack_id"],
                        "version": item["version"],
                        "archive_sha256": item["archive_sha256"],
                    }
                    for item in pack_records
                ],
                "installed_at": installed_at,
            }
            _io_path(staging_runtime / ".meeting-copilot-capability-state.json").write_bytes(
                _json_bytes(state)
            )
            os.replace(_io_path(staging_runtime), _io_path(target))
            self._activate(target_relative, package_id, package_version)
        except Exception:
            io_target = _io_path(target)
            if io_target.exists() and not _io_path(
                target / ".meeting-copilot-capability-state.json"
            ).is_file():
                shutil.rmtree(io_target, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(_io_path(staging), ignore_errors=True)
        return self.status()

    def _extract_payload(
        self,
        archive: zipfile.ZipFile,
        infos: dict[str, zipfile.ZipInfo],
        runtime_root: Path,
        extracted_payloads: set[str],
    ) -> None:
        for name, info in infos.items():
            if not name.startswith("payload/") or name == "payload":
                continue
            relative = _safe_relative(name.removeprefix("payload/"))
            if relative in extracted_payloads and not info.is_dir():
                raise CapabilityPackError("duplicate_payload_path", "多个能力包包含重复文件")
            destination = (runtime_root / relative).resolve(strict=False)
            try:
                destination.relative_to(runtime_root.resolve())
            except ValueError as exc:
                raise CapabilityPackError("payload_path_escape", "能力包文件路径越界") from exc
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            extracted_payloads.add(relative)
            io_destination = _io_path(destination)
            io_destination.parent.mkdir(parents=True, exist_ok=True)
            if io_destination.exists() and io_destination.is_dir():
                raise CapabilityPackError("payload_type_conflict", "能力包文件与基础运行时目录冲突")
            with archive.open(info) as source, io_destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=4 * 1024 * 1024)

    def _activate(self, relative: str, package_id: str, package_version: str) -> None:
        _write_json_atomic(
            self.active_path,
            {
                "schema_version": ACTIVE_RUNTIME_SCHEMA,
                "runtime_path": relative,
                "package_id": package_id,
                "package_version": package_version,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
