from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools/prepare_offline_capability_bundle.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("prepare_offline_capability_bundle", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_offline_package_uses_zip64_container_and_verified_pack(tmp_path):
    tool = load_tool_module()
    upload_root = tmp_path / "upload"
    pack_path = upload_root / "private-bucket-upload/meeting-copilot/packs/fixture/pack.zip"
    pack_path.parent.mkdir(parents=True)
    pack_manifest = {
        "schema_version": tool.PACK_ARCHIVE_SCHEMA,
        "pack_id": "fixture",
        "version": "fixture-v1",
        "depends_on": [],
    }
    with zipfile.ZipFile(pack_path, "w") as archive:
        archive.writestr("pack-manifest.json", json.dumps(pack_manifest))
        archive.writestr("payload/runtime/fixture.txt", "fixture")
    pack_sha256 = tool.sha256_file(pack_path)
    inventory_path = upload_root / "upload-inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
                "private_capability_packs": [
                    {
                        "pack_id": "fixture",
                        "version": "fixture-v1",
                        "archive_sha256": pack_sha256,
                        "archive_size_bytes": pack_path.stat().st_size,
                        "unpacked_size_bytes": 7,
                        "local_path": str(pack_path),
                        "object_key": "meeting-copilot/packs/fixture/pack.zip",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_manifest = tmp_path / "runtime-bundle-manifest.json"
    runtime_manifest.write_text(
        json.dumps(
            {
                "schema_version": "meeting_copilot.runtime_bundle.v1",
                "platform": "windows",
                "component_inventory": {"status": "sealed", "components": {}},
            }
        ),
        encoding="utf-8",
    )

    result = tool.build_offline_package(
        inventory_path=inventory_path,
        runtime_manifest_path=runtime_manifest,
        output_directory=tmp_path / "out",
    )

    package_path = Path(result["package_path"])
    assert tool.sha256_file(package_path) == result["sha256"]
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "offline-package-manifest.json",
            "runtime-bundle-manifest.json",
            "OFFLINE-PACK-NOTICE.txt",
            f"packs/fixture/{pack_sha256}.zip",
        }
        manifest = json.loads(archive.read("offline-package-manifest.json"))
        pack_info = archive.getinfo(f"packs/fixture/{pack_sha256}.zip")
    assert manifest["schema_version"] == tool.OFFLINE_PACKAGE_SCHEMA
    assert manifest["packs"][0]["archive_sha256"] == pack_sha256
    assert pack_info.compress_type == zipfile.ZIP_STORED
