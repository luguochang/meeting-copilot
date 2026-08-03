from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from tools.prepare_server_upload import (
    GITHUB_INSTALLER_URL,
    INSTALLER_NAME,
    build_server_upload,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fixture_inputs(tmp_path: Path) -> tuple[Path, Path]:
    website = tmp_path / "website-dist"
    release = tmp_path / "release"
    (website / "assets").mkdir(parents=True)
    (website / "releases").mkdir()
    release.mkdir()
    (website / "index.html").write_text("<main>Talktrace</main>", encoding="utf-8")
    (website / "assets/app.js").write_text("console.log('ok')", encoding="utf-8")
    (website / "releases/latest.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "platforms": [
                    {
                        "id": "windows-x64",
                        "url": (
                            "/downloads/meeting-copilot/windows/0.1.0/"
                            + INSTALLER_NAME
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    installer = b"installer-fixture"
    digest = _sha256(installer)
    (release / INSTALLER_NAME).write_bytes(installer)
    (release / f"{INSTALLER_NAME}.sha256").write_text(
        f"{digest}  {INSTALLER_NAME}\n", encoding="utf-8"
    )
    (release / "release-evidence.json").write_text("{}\n", encoding="utf-8")
    (release / "latest-windows-x64.json").write_text(
        json.dumps(
            {
                "file": INSTALLER_NAME,
                "sha256": digest,
                "size_bytes": len(installer),
            }
        ),
        encoding="utf-8",
    )
    (release / "offline-pack-latest-windows-x64.json").write_text(
        "{}\n", encoding="utf-8"
    )
    return website, release


def test_build_server_upload_contains_site_and_download_tree(tmp_path: Path) -> None:
    website, release = _fixture_inputs(tmp_path)
    output = tmp_path / "server-upload.zip"

    result = build_server_upload(
        website_dist=website,
        release_dir=release,
        output=output,
    )

    assert result["offline_package_included"] is False
    assert result["sha256"] == _sha256(output.read_bytes())
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "index.html" in names
        assert "assets/app.js" in names
        assert (
            "downloads/meeting-copilot/windows/0.1.0/" + INSTALLER_NAME
        ) in names
        assert "downloads/meeting-copilot/channels/latest-windows-x64.json" in names
        assert "downloads/meeting-copilot/offline-packs/latest-windows-x64.json" in names
        assert "SERVER-UPLOAD-INVENTORY.json" in names


def test_build_server_upload_accepts_matching_github_release_url(tmp_path: Path) -> None:
    website, release = _fixture_inputs(tmp_path)
    manifest_path = website / "releases/latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"][0]["url"] = GITHUB_INSTALLER_URL
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = build_server_upload(
        website_dist=website,
        release_dir=release,
        output=tmp_path / "server-upload.zip",
    )

    assert result["website_download_url"] == GITHUB_INSTALLER_URL


def test_build_server_upload_rejects_unrelated_download_url(tmp_path: Path) -> None:
    website, release = _fixture_inputs(tmp_path)
    manifest_path = website / "releases/latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"][0]["url"] = "https://example.com/other-installer.exe"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="does not point to packaged installer"):
        build_server_upload(
            website_dist=website,
            release_dir=release,
            output=tmp_path / "server-upload.zip",
        )


def test_build_server_upload_rejects_mismatched_channel_hash(tmp_path: Path) -> None:
    website, release = _fixture_inputs(tmp_path)
    channel = json.loads((release / "latest-windows-x64.json").read_text())
    channel["sha256"] = "0" * 64
    (release / "latest-windows-x64.json").write_text(
        json.dumps(channel), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="channel checksum"):
        build_server_upload(
            website_dist=website,
            release_dir=release,
            output=tmp_path / "server-upload.zip",
        )
