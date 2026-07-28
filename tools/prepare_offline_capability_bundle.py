#!/usr/bin/env python3
"""Build a single Zip64 Meeting Copilot offline capability package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UPLOAD_ROOT = REPO_ROOT / "artifacts/tencent-cos-upload/2026-07-27"
DEFAULT_INVENTORY = DEFAULT_UPLOAD_ROOT / "upload-inventory.json"
DEFAULT_RUNTIME_MANIFEST = (
    REPO_ROOT / "artifacts/tmp/wrb/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
)
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/offline-release/2026-07-27"
PACKAGE_NAME = "Meeting-Copilot-ASR-Full-0.1.0-Windows-x86_64.mcpkg"
OFFLINE_PACKAGE_SCHEMA = "meeting_copilot.offline_capability_bundle.v1"
PACK_ARCHIVE_SCHEMA = "meeting_copilot.capability_pack_archive.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _safe_relative(value: Any, *, field: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe relative path in {field}: {text}")
    return path.as_posix()


def _resolve_inventory_file(inventory_path: Path, record: dict[str, Any]) -> Path:
    raw_local_path = str(record.get("local_path") or "").strip()
    if raw_local_path:
        candidate = Path(raw_local_path).expanduser().resolve()
        if candidate.is_file():
            return candidate
    object_key = _safe_relative(record.get("object_key"), field="pack object_key")
    candidate = inventory_path.parent / "private-bucket-upload" / object_key
    if candidate.is_file():
        return candidate.resolve()
    raise ValueError(f"capability pack archive is missing: {record.get('pack_id')}")


def _validate_pack_archive(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    expected_size = int(record.get("archive_size_bytes") or 0)
    expected_sha256 = str(record.get("archive_sha256") or "").lower()
    if path.stat().st_size != expected_size:
        raise ValueError(f"capability pack size mismatch: {record.get('pack_id')}")
    observed_sha256 = sha256_file(path)
    if observed_sha256 != expected_sha256:
        raise ValueError(f"capability pack hash mismatch: {record.get('pack_id')}")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"capability pack ZIP CRC failed: {record.get('pack_id')}")
        try:
            manifest = json.loads(archive.read("pack-manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"capability pack manifest is invalid: {record.get('pack_id')}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PACK_ARCHIVE_SCHEMA:
        raise ValueError(f"capability pack manifest schema is invalid: {record.get('pack_id')}")
    if manifest.get("pack_id") != record.get("pack_id"):
        raise ValueError(f"capability pack manifest ID mismatch: {record.get('pack_id')}")
    return manifest


def build_offline_package(
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    runtime_manifest_path: Path = DEFAULT_RUNTIME_MANIFEST,
    output_directory: Path = DEFAULT_OUTPUT,
    force: bool = False,
) -> dict[str, Any]:
    inventory_path = inventory_path.expanduser().resolve()
    runtime_manifest_path = runtime_manifest_path.expanduser().resolve()
    output_directory = output_directory.expanduser().resolve()
    inventory = _read_json(inventory_path)
    runtime_manifest = _read_json(runtime_manifest_path)
    if runtime_manifest.get("platform") != "windows":
        raise ValueError("offline package runtime manifest is not for Windows")
    if runtime_manifest.get("component_inventory", {}).get("status") != "sealed":
        raise ValueError("offline package runtime manifest is not sealed")

    package_path = output_directory / PACKAGE_NAME
    checksum_path = package_path.with_suffix(package_path.suffix + ".sha256")
    evidence_path = output_directory / "offline-package-evidence.json"
    if output_directory.exists():
        if not force:
            raise ValueError(f"output already exists; use --force to rebuild: {output_directory}")
        shutil.rmtree(output_directory)
    output_directory.mkdir(parents=True)

    pack_inputs: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for record in inventory.get("private_capability_packs") or []:
        if not isinstance(record, dict):
            raise ValueError("offline package inventory contains an invalid pack record")
        archive_path = _resolve_inventory_file(inventory_path, record)
        pack_manifest = _validate_pack_archive(archive_path, record)
        pack_inputs.append((archive_path, record, pack_manifest))
    if not pack_inputs:
        raise ValueError("offline package inventory has no capability packs")

    runtime_manifest_bytes = runtime_manifest_path.read_bytes()
    runtime_manifest_sha256 = hashlib.sha256(runtime_manifest_bytes).hexdigest()
    pack_records = []
    for archive_path, record, pack_manifest in pack_inputs:
        archive_relative = (
            f"packs/{record['pack_id']}/{record['archive_sha256']}.zip"
        )
        pack_records.append(
            {
                "pack_id": record["pack_id"],
                "version": record["version"],
                "archive_path": archive_relative,
                "archive_size_bytes": archive_path.stat().st_size,
                "archive_sha256": record["archive_sha256"],
                "unpacked_size_bytes": int(record.get("unpacked_size_bytes") or 0),
                "depends_on": list(pack_manifest.get("depends_on") or []),
            }
        )
    required_free_space = (
        sum(record["unpacked_size_bytes"] for record in pack_records)
        + sum(record["archive_size_bytes"] for record in pack_records)
        + 1024 * 1024 * 1024
    )
    package_manifest = {
        "schema_version": OFFLINE_PACKAGE_SCHEMA,
        "package_id": "meeting-copilot-asr-full-windows-x86_64",
        "version": "0.1.0-20260727-v1",
        "platform": "windows-x86_64",
        "minimum_app_version": "0.1.0",
        "release_scope": "private_preview_only",
        "public_release_approved": False,
        "redistribution_status": "public_redistribution_unresolved",
        "signature_status": "unsigned",
        "capabilities": ["realtime_asr_zh_cn", "file_asr_zh_cn"],
        "runtime_manifest": {
            "path": "runtime-bundle-manifest.json",
            "size_bytes": len(runtime_manifest_bytes),
            "sha256": runtime_manifest_sha256,
        },
        "packs": pack_records,
        "required_free_space_bytes": required_free_space,
        "safety": {
            "audio_capture_performed": False,
            "audio_playback_performed": False,
            "device_enumeration_performed": False,
            "cloud_upload_performed": False,
        },
    }
    notice = (
        "Meeting Copilot Windows x86_64 offline ASR package.\n"
        "Import this .mcpkg from the Local Capabilities page; do not extract it manually.\n"
        "This build is unsigned and public model redistribution review is unresolved.\n"
    )
    temporary = output_directory / f".{PACKAGE_NAME}.building"
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
        strict_timestamps=False,
    ) as archive:
        archive.writestr(
            "offline-package-manifest.json",
            json.dumps(package_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        archive.writestr("runtime-bundle-manifest.json", runtime_manifest_bytes)
        archive.writestr("OFFLINE-PACK-NOTICE.txt", notice)
        for archive_path, record, _pack_manifest in pack_inputs:
            archive.write(
                archive_path,
                f"packs/{record['pack_id']}/{record['archive_sha256']}.zip",
            )
    temporary.replace(package_path)
    package_sha256 = sha256_file(package_path)
    checksum_path.write_text(
        f"{package_sha256}  {package_path.name}\n",
        encoding="utf-8",
    )
    evidence = {
        "schema_version": "meeting_copilot.offline_capability_release_evidence.v1",
        "package": {
            "file": package_path.name,
            "size_bytes": package_path.stat().st_size,
            "sha256": package_sha256,
            "manifest": package_manifest,
        },
        "verification": {
            "source_pack_count": len(pack_records),
            "source_pack_hashes_verified": True,
            "source_pack_zip_crc_verified": True,
            "runtime_manifest_sealed": True,
            **package_manifest["safety"],
        },
    }
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "package_path": str(package_path),
        "checksum_path": str(checksum_path),
        "evidence_path": str(evidence_path),
        "size_bytes": package_path.stat().st_size,
        "sha256": package_sha256,
        "manifest": package_manifest,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--runtime-manifest", type=Path, default=DEFAULT_RUNTIME_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_offline_package(
        inventory_path=args.inventory,
        runtime_manifest_path=args.runtime_manifest,
        output_directory=args.output,
        force=args.force,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("package_path", "size_bytes", "sha256", "checksum_path")
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
