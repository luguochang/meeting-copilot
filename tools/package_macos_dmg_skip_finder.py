#!/usr/bin/env python3
"""Package a local macOS development DMG without Finder AppleScript automation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_TMP = (REPO_ROOT / "artifacts" / "tmp").resolve()
DEFAULT_APP = REPO_ROOT / "artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app"
DEFAULT_BUNDLE_DMG_SCRIPT = REPO_ROOT / "artifacts/tmp/desktop_tauri_target/release/bundle/dmg/bundle_dmg.sh"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/desktop_dmg_skip_finder_current"


def parse_mount_dir(hdiutil_attach_output: str) -> Path:
    for line in hdiutil_attach_output.splitlines():
        match = re.search(r"(/Volumes/.+)$", line)
        if match:
            return Path(match.group(1).strip())
    raise ValueError("mount dir not found in hdiutil attach output")


def build_bundle_command(
    *,
    script: Path,
    output_dmg: Path,
    source_dir: Path,
    volume_name: str,
) -> list[str]:
    return [
        str(script),
        "--skip-jenkins",
        "--volname",
        volume_name,
        "--app-drop-link",
        "360",
        "170",
        "--no-internet-enable",
        str(output_dmg),
        str(source_dir),
    ]


def resolve_output_root(path: Path) -> Path:
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        resolved.relative_to(ARTIFACTS_TMP)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def remaining_release_blockers() -> list[str]:
    return [
        "developer_id_codesign_not_done",
        "notarization_not_done",
        "gatekeeper_rejects_unsigned_or_adhoc_artifacts",
        "packaged_screenshot_evidence_still_missing",
    ]


def package_dmg(
    *,
    app_path: Path,
    bundle_dmg_script: Path,
    output_root: Path,
    volume_name: str,
    dmg_name: str,
    adhoc_sign: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    app_path = _resolve_existing_path(app_path, label="app")
    bundle_dmg_script = _resolve_existing_path(bundle_dmg_script, label="bundle_dmg_script")
    output_root = resolve_output_root(output_root)
    if output_root.exists():
        if not force:
            raise FileExistsError(f"output_root already exists: {output_root}")
        shutil.rmtree(output_root)
    source_dir = output_root / "source"
    staged_app = source_dir / app_path.name
    output_dmg = output_root / dmg_name
    output_root.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    command_results: dict[str, dict[str, Any]] = {}
    mount_dir: Path | None = None
    try:
        command_results["copy_app"] = _run(["ditto", str(app_path), str(staged_app)])
        if adhoc_sign:
            command_results["adhoc_sign_app"] = _run([
                "codesign",
                "--force",
                "--deep",
                "--sign",
                "-",
                "--timestamp=none",
                str(staged_app),
            ])
        command_results["verify_staged_app_signature"] = _run([
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(staged_app),
        ])
        command_results["create_dmg"] = _run(build_bundle_command(
            script=bundle_dmg_script,
            output_dmg=output_dmg,
            source_dir=source_dir,
            volume_name=volume_name,
        ))
        attach = _run(["hdiutil", "attach", "-nobrowse", "-readonly", str(output_dmg)])
        command_results["hdiutil_attach"] = attach
        mount_dir = parse_mount_dir(attach["stdout"])
        mounted_app = mount_dir / app_path.name
        applications_link = mount_dir / "Applications"
        if not mounted_app.is_dir():
            raise FileNotFoundError(f"mounted app missing: {mounted_app}")
        if not applications_link.is_symlink():
            raise FileNotFoundError(f"Applications symlink missing: {applications_link}")
        command_results["verify_mounted_app_signature"] = _run([
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(mounted_app),
        ])
    finally:
        if mount_dir is not None and mount_dir.exists():
            command_results["hdiutil_detach"] = _run(["hdiutil", "detach", str(mount_dir)], check=False)

    spctl_app = _run([
        "spctl",
        "--assess",
        "--type",
        "execute",
        "--verbose=4",
        str(staged_app),
    ], check=False)
    spctl_dmg = _run([
        "spctl",
        "--assess",
        "--type",
        "open",
        "--context",
        "context:primary-signature",
        "--verbose=4",
        str(output_dmg),
    ], check=False)
    command_results["spctl_app"] = spctl_app
    command_results["spctl_dmg"] = spctl_dmg

    evidence = {
        "schema_version": "desktop_macos_dmg_skip_finder_evidence.v1",
        "run_id": output_root.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "go_development_dmg_not_public_release",
        "source_app": _display_path(app_path),
        "staged_app": _display_path(staged_app),
        "dmg_path": _display_path(output_dmg),
        "dmg_sha256": _sha256(output_dmg),
        "dmg_size_bytes": output_dmg.stat().st_size,
        "packaging_method": "tauri_generated_create_dmg_script_with_skip_jenkins",
        "finder_applescript_skipped": True,
        "counts_as_dmg_packaging_evidence": True,
        "counts_as_public_release_package": False,
        "results": {
            "app_adhoc_codesign_exit_code": command_results.get("adhoc_sign_app", {}).get("returncode"),
            "app_codesign_verify_exit_code": command_results["verify_staged_app_signature"]["returncode"],
            "dmg_create_exit_code": command_results["create_dmg"]["returncode"],
            "dmg_mount_smoke_exit_code": command_results["verify_mounted_app_signature"]["returncode"],
            "dmg_contains_app": True,
            "dmg_contains_applications_symlink": True,
            "mounted_app_codesign_verify_exit_code": command_results["verify_mounted_app_signature"]["returncode"],
            "spctl_app_exit_code": spctl_app["returncode"],
            "spctl_dmg_exit_code": spctl_dmg["returncode"],
        },
        "remaining_blockers": remaining_release_blockers(),
        "commands": command_results,
    }
    _write_json(output_root / "evidence.json", evidence)
    return evidence


def _resolve_existing_path(path: Path, *, label: str) -> Path:
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    return resolved


def _run(command: list[str], *, check: bool = True) -> dict[str, Any]:
    proc = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    result = {
        "command": " ".join(command),
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, command, proc.stdout, proc.stderr)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--bundle-dmg-script", type=Path, default=DEFAULT_BUNDLE_DMG_SCRIPT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--volume-name", default="Meeting Copilot")
    parser.add_argument("--dmg-name", default="Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg")
    parser.add_argument("--no-adhoc-sign", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    evidence = package_dmg(
        app_path=args.app,
        bundle_dmg_script=args.bundle_dmg_script,
        output_root=args.output_root,
        volume_name=args.volume_name,
        dmg_name=args.dmg_name,
        adhoc_sign=not args.no_adhoc_sign,
        force=args.force,
    )
    print(json.dumps({
        "status": evidence["status"],
        "dmg_path": evidence["dmg_path"],
        "dmg_sha256": evidence["dmg_sha256"],
        "counts_as_public_release_package": evidence["counts_as_public_release_package"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
