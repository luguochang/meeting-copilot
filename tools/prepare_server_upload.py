#!/usr/bin/env python3
"""Build the static website and Windows download tree as one upload archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEBSITE_DIST = REPO_ROOT / "website/dist"
DEFAULT_RELEASE_DIR = REPO_ROOT / "artifacts/release/2026-07-27"
DEFAULT_OUTPUT = DEFAULT_RELEASE_DIR / "Meeting-Copilot-0.1.0-server-upload.zip"
INSTALLER_NAME = "Meeting-Copilot-0.1.0-windows-x64-base-unsigned.exe"
REQUIRED_RELEASE_FILES = (
    INSTALLER_NAME,
    f"{INSTALLER_NAME}.sha256",
    "release-evidence.json",
    "latest-windows-x64.json",
    "offline-pack-latest-windows-x64.json",
)
SCHEMA_VERSION = "meeting_copilot.server_upload.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def expected_checksum(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip().split()
    if not value or len(value[0]) != 64:
        raise ValueError(f"invalid SHA-256 file: {path}")
    return value[0].lower()


def validate_inputs(website_dist: Path, release_dir: Path) -> dict[str, object]:
    website_dist = website_dist.resolve()
    release_dir = release_dir.resolve()
    if not (website_dist / "index.html").is_file():
        raise ValueError("website production build is missing index.html")
    for filename in REQUIRED_RELEASE_FILES:
        if not (release_dir / filename).is_file():
            raise ValueError(f"release input is missing: {filename}")

    installer = release_dir / INSTALLER_NAME
    observed_sha256 = sha256_file(installer)
    if observed_sha256 != expected_checksum(release_dir / f"{INSTALLER_NAME}.sha256"):
        raise ValueError("installer checksum file does not match installer")

    channel = read_json(release_dir / "latest-windows-x64.json")
    if channel.get("file") != INSTALLER_NAME:
        raise ValueError("Windows release channel points to another installer")
    if channel.get("sha256") != observed_sha256:
        raise ValueError("Windows release channel checksum does not match installer")
    if channel.get("size_bytes") != installer.stat().st_size:
        raise ValueError("Windows release channel size does not match installer")

    website_release = read_json(website_dist / "releases/latest.json")
    expected_url = (
        "/downloads/meeting-copilot/windows/0.1.0/" + INSTALLER_NAME
    )
    platforms = website_release.get("platforms")
    windows = next(
        (
            item
            for item in platforms
            if isinstance(item, dict) and item.get("id") == "windows-x64"
        ),
        None,
    ) if isinstance(platforms, list) else None
    if not isinstance(windows, dict) or windows.get("url") != expected_url:
        raise ValueError("website release manifest does not point to packaged installer")
    return {
        "installer_sha256": observed_sha256,
        "installer_size_bytes": installer.stat().st_size,
        "website_version": website_release.get("version"),
    }


def archive_files(website_dist: Path, release_dir: Path) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for path in sorted(website_dist.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            entries.append((path, path.relative_to(website_dist).as_posix()))

    windows_prefix = PurePosixPath("downloads/meeting-copilot/windows/0.1.0")
    for filename in REQUIRED_RELEASE_FILES[:3]:
        entries.append((release_dir / filename, str(windows_prefix / filename)))
    entries.append(
        (
            release_dir / "latest-windows-x64.json",
            "downloads/meeting-copilot/channels/latest-windows-x64.json",
        )
    )
    entries.append(
        (
            release_dir / "offline-pack-latest-windows-x64.json",
            "downloads/meeting-copilot/offline-packs/latest-windows-x64.json",
        )
    )
    return entries


def build_server_upload(
    *,
    website_dist: Path = DEFAULT_WEBSITE_DIST,
    release_dir: Path = DEFAULT_RELEASE_DIR,
    output: Path = DEFAULT_OUTPUT,
    force: bool = False,
) -> dict[str, object]:
    website_dist = website_dist.resolve()
    release_dir = release_dir.resolve()
    output = output.resolve()
    validation = validate_inputs(website_dist, release_dir)
    if output.exists() and not force:
        raise ValueError(f"output already exists; use --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    temporary.unlink(missing_ok=True)

    entries = archive_files(website_dist, release_dir)
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "website_root": "/",
        "installer_path": (
            "/downloads/meeting-copilot/windows/0.1.0/" + INSTALLER_NAME
        ),
        "offline_package_included": False,
        "offline_package_distribution": "external_download_then_local_import",
        "file_count": len(entries) + 2,
        **validation,
    }
    upload_readme = """Meeting Copilot server upload package

Extract this archive directly into the HTTPS website document root.
The website is at the archive root and the Windows installer is under /downloads/.
The multi-gigabyte offline ASR .mcpkg is intentionally not included; distribute it
through the separately configured large-file download page and import it in the app.
"""
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for source, archive_name in entries:
            archive.write(source, archive_name)
        archive.writestr("README-UPLOAD.txt", upload_readme)
        archive.writestr(
            "SERVER-UPLOAD-INVENTORY.json",
            json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    temporary.replace(output)

    checksum = sha256_file(output)
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{checksum}  {output.name}\n", encoding="utf-8")
    return {
        **inventory,
        "output": str(output),
        "size_bytes": output.stat().st_size,
        "sha256": checksum,
        "sha256_file": str(checksum_path),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--website-dist", type=Path, default=DEFAULT_WEBSITE_DIST)
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_server_upload(
        website_dist=args.website_dist,
        release_dir=args.release_dir,
        output=args.output,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
