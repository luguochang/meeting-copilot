from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.capability_pack_manager import (
    CapabilityPackError,
    CapabilityPackManager,
    OFFLINE_PACKAGE_SCHEMA,
    PACK_ARCHIVE_SCHEMA,
)


def _directory_record(path: Path) -> dict[str, object]:
    entries = []
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file():
            entries.append(
                {
                    "path": candidate.relative_to(path).as_posix(),
                    "kind": "file",
                    "size_bytes": candidate.stat().st_size,
                    "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                }
            )
    payload = json.dumps(entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return {
        "kind": "directory",
        "size_bytes": sum(int(item["size_bytes"]) for item in entries),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "file_count": len(entries),
        "symlink_count": 0,
    }


def _fixture_package(tmp_path: Path) -> tuple[bytes, Path]:
    base = tmp_path / "base"
    (base / "app").mkdir(parents=True)
    (base / "app/backend.txt").write_text("base", encoding="utf-8")
    long_base_path = base / "runtime/backend-python/Lib/test"
    for index in range(3):
        long_base_path /= f"long-runtime-directory-{index:02d}"
    long_base_path.mkdir(parents=True)
    (long_base_path / "fixture.txt").write_text("long-path", encoding="utf-8")
    base_manifest = {
        "schema_version": "meeting_copilot.runtime_bundle.v1",
        "platform": "windows",
        "distribution_profile": "base",
        "required_files": ["app/backend.txt"],
        "launchers": {"backend": "bin/base-backend.cmd"},
        "component_inventory": {
            "status": "sealed",
            "components": {
                "app.application": {
                    "path": "app",
                    "version": "fixture-base-v1",
                    **_directory_record(base / "app"),
                },
                "backend.python_runtime": {
                    "path": "runtime/backend-python",
                    "version": "fixture-python-v1",
                    **_directory_record(base / "runtime/backend-python"),
                },
            },
        },
    }
    (base / "runtime-bundle-manifest.json").write_text(
        json.dumps(base_manifest),
        encoding="utf-8",
    )
    payload_root = tmp_path / "payload/runtime/fixture"
    payload_root.mkdir(parents=True)
    (payload_root / "model.bin").write_bytes(b"model")
    component = {
        "path": "runtime/fixture",
        "version": "fixture-v1",
        **_directory_record(payload_root),
    }
    pack_manifest = {
        "schema_version": PACK_ARCHIVE_SCHEMA,
        "pack_id": "fixture-pack",
        "version": "fixture-v1",
        "depends_on": [],
        "components": {"fixture.component": component},
    }
    nested_bytes = io.BytesIO()
    with zipfile.ZipFile(nested_bytes, "w") as archive:
        archive.writestr("pack-manifest.json", json.dumps(pack_manifest))
        archive.writestr("payload/runtime/fixture/model.bin", b"model")
    nested = nested_bytes.getvalue()
    nested_sha = hashlib.sha256(nested).hexdigest()
    runtime_manifest = {
        "schema_version": "meeting_copilot.runtime_bundle.v1",
        "platform": "windows",
        "required_files": ["app/backend.txt", "runtime/fixture/model.bin"],
        "component_inventory": {"status": "sealed", "components": {"fixture.component": component}},
    }
    runtime_bytes = json.dumps(runtime_manifest).encode("utf-8")
    package_manifest = {
        "schema_version": OFFLINE_PACKAGE_SCHEMA,
        "package_id": "fixture-full",
        "version": "fixture-1",
        "platform": "windows-x86_64",
        "minimum_app_version": "0.1.0",
        "release_scope": "private_preview_only",
        "signature_status": "unsigned",
        "capabilities": ["realtime_asr_zh_cn", "file_asr_zh_cn"],
        "runtime_manifest": {
            "path": "runtime-bundle-manifest.json",
            "size_bytes": len(runtime_bytes),
            "sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        },
        "packs": [
            {
                "pack_id": "fixture-pack",
                "version": "fixture-v1",
                "archive_path": f"packs/fixture-pack/{nested_sha}.zip",
                "archive_size_bytes": len(nested),
                "archive_sha256": nested_sha,
                "unpacked_size_bytes": 5,
                "depends_on": [],
            }
        ],
        "required_free_space_bytes": 1,
    }
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", allowZip64=True) as archive:
        archive.writestr("offline-package-manifest.json", json.dumps(package_manifest))
        archive.writestr("runtime-bundle-manifest.json", runtime_bytes)
        archive.writestr(f"packs/fixture-pack/{nested_sha}.zip", nested)
    return package.getvalue(), base


def test_installs_verified_offline_package_and_activates_runtime(tmp_path):
    package, base = _fixture_package(tmp_path)
    manager = CapabilityPackManager(
        tmp_path / "capabilities",
        source_runtime_bundle=base,
        platform_name="windows-x86_64",
    )

    result = manager.install_file(io.BytesIO(package), filename="fixture.mcpkg")

    assert result["installed"] is True
    assert result["realtime_asr_ready"] is True
    assert result["file_asr_ready"] is True
    assert result["restart_required"] is True
    active = json.loads((tmp_path / "capabilities/active.json").read_text(encoding="utf-8"))
    runtime = tmp_path / "capabilities" / active["runtime_path"]
    assert (runtime / "app/backend.txt").read_text(encoding="utf-8") == "base"
    assert (runtime / "runtime/fixture/model.bin").read_bytes() == b"model"
    installed_manifest_bytes = (runtime / "runtime-bundle-manifest.json").read_bytes()
    installed_manifest = json.loads(installed_manifest_bytes)
    assert set(installed_manifest["component_inventory"]["components"]) == {
        "app.application",
        "backend.python_runtime",
        "fixture.component",
    }
    assert installed_manifest["launchers"] == {
        "backend": "bin/base-backend.cmd",
    }
    installed_state = json.loads(
        (runtime / ".meeting-copilot-capability-state.json").read_text(encoding="utf-8")
    )
    assert installed_state["runtime_manifest_sha256"] == hashlib.sha256(
        installed_manifest_bytes
    ).hexdigest()
    assert installed_state["source_runtime_manifest_sha256"] != installed_state[
        "runtime_manifest_sha256"
    ]


def test_rejects_tampered_pack_without_changing_active_pointer(tmp_path):
    package, base = _fixture_package(tmp_path)
    manager = CapabilityPackManager(
        tmp_path / "capabilities",
        source_runtime_bundle=base,
        platform_name="windows-x86_64",
    )
    manager.capability_root.mkdir(parents=True)
    active_path = manager.capability_root / "active.json"
    active_path.write_text('{"existing":true}', encoding="utf-8")
    tampered = bytearray(package)
    marker = tampered.find(b"model")
    assert marker >= 0
    tampered[marker : marker + 5] = b"wrong"

    with pytest.raises(CapabilityPackError):
        manager.install_file(io.BytesIO(tampered), filename="fixture.mcpkg")

    assert active_path.read_text(encoding="utf-8") == '{"existing":true}'


def test_rejects_wrong_platform(tmp_path):
    package, base = _fixture_package(tmp_path)
    manager = CapabilityPackManager(
        tmp_path / "capabilities",
        source_runtime_bundle=base,
        platform_name="macos-arm64",
    )

    with pytest.raises(CapabilityPackError, match="平台不匹配"):
        manager.install_file(io.BytesIO(package), filename="fixture.mcpkg")


def test_local_capability_api_imports_package_and_reports_status(tmp_path, monkeypatch):
    package, base = _fixture_package(tmp_path)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv(
        "MEETING_COPILOT_RUNTIME_MANIFEST",
        str(base / "runtime-bundle-manifest.json"),
    )
    monkeypatch.setenv(
        "MEETING_COPILOT_ASR_OFFLINE_DOWNLOAD_URL",
        "https://example.invalid/meeting-copilot-asr",
    )
    monkeypatch.setattr(
        "meeting_copilot_web_mvp.app.CapabilityPackManager",
        lambda capability_root, **kwargs: CapabilityPackManager(
            capability_root,
            platform_name="windows-x86_64",
            **kwargs,
        ),
    )

    with TestClient(create_app(data_dir=data_dir)) as client:
        initial = client.get("/v2/local-capabilities")
        imported = client.post(
            "/v2/local-capabilities/import",
            files={"file": ("fixture.mcpkg", package, "application/octet-stream")},
        )
        current = client.get("/v2/local-capabilities")

    assert initial.status_code == 200
    assert initial.json() == {
        "schema_version": "meeting_copilot.local_capability_status.v1",
        "platform": "windows-x86_64",
        "base_app_ready": True,
        "installed": False,
        "package_id": None,
        "package_version": None,
        "installed_at": None,
        "realtime_asr_ready": False,
        "file_asr_ready": False,
        "restart_required": False,
        "signature_status": None,
        "release_scope": None,
        "download_page_url": "https://example.invalid/meeting-copilot-asr",
        "import_available": True,
        "errors": [],
    }
    assert imported.status_code == 200
    assert imported.json()["installed"] is True
    assert imported.json()["restart_required"] is True
    assert current.json()["package_id"] == "fixture-full"
    assert (tmp_path / "capability-packs" / "active.json").is_file()


def test_local_capability_api_rejects_non_package_with_safe_error(tmp_path, monkeypatch):
    _, base = _fixture_package(tmp_path)
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv(
        "MEETING_COPILOT_RUNTIME_MANIFEST",
        str(base / "runtime-bundle-manifest.json"),
    )

    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.post(
            "/v2/local-capabilities/import",
            files={"file": ("wrong.zip", b"not-a-package", "application/zip")},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "unsupported_package_type",
            "message": "请选择 .mcpkg 离线完整包",
        }
    }
