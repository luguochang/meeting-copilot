#!/usr/bin/env python3
"""Build a Tauri macOS app with the validated local runtime resource bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "tauri_runtime_package"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNTIME_MANIFEST_SCHEMA = "meeting_copilot.runtime_bundle.v1"


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    resolved = output_root.resolve() if output_root.is_absolute() else (repo_root / output_root).resolve()
    approved = (repo_root / "artifacts/tmp").resolve()
    try:
        resolved.relative_to(approved)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters")


def _safe_bundle_path(value: Any) -> str:
    relative = str(value or "").strip()
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError("runtime bundle manifest contains unsafe required path")
    return path.as_posix()


def load_runtime_bundle_manifest(bundle: Path) -> dict[str, Any]:
    manifest_path = bundle / "runtime-bundle-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("runtime bundle missing required files: runtime-bundle-manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("runtime bundle manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise ValueError("runtime bundle manifest schema is invalid")
    required = manifest.get("required_files")
    if not isinstance(required, list) or not required:
        raise ValueError("runtime bundle manifest required_files are missing")
    manifest["required_files"] = [_safe_bundle_path(relative) for relative in required]
    return manifest


def validate_runtime_bundle(bundle: Path) -> dict[str, Any]:
    manifest = load_runtime_bundle_manifest(bundle)
    required = manifest["required_files"]
    missing = [relative for relative in required if not (bundle / relative).is_file()]
    if missing:
        raise ValueError(f"runtime bundle missing required files: {', '.join(missing)}")
    external_links = []
    for candidate in bundle.rglob("*"):
        if candidate.is_symlink():
            try:
                candidate.resolve(strict=False).relative_to(bundle.resolve())
            except ValueError:
                external_links.append(candidate.relative_to(bundle).as_posix())
    if external_links:
        raise ValueError(f"runtime bundle contains external symlinks: {', '.join(external_links)}")
    return manifest


def build_overlay(bundle: Path, overlay_path: Path) -> dict[str, Any]:
    overlay = {
        "bundle": {
            "resources": {
                str(bundle.resolve()): "MeetingCopilotRuntime.bundle",
            }
        }
    }
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps(overlay, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return overlay


def build_command(*, overlay_path: Path, rust_target_dir: Path, cargo_tauri: str = "cargo-tauri") -> list[str]:
    return [
        cargo_tauri,
        "build",
        "--bundles",
        "app",
        "--ci",
        "--no-sign",
        "--config",
        str(overlay_path),
        "--",
        "--locked",
        "--target-dir",
        str(rust_target_dir),
    ]


def find_built_app(target_dir: Path) -> Path:
    candidates = sorted((target_dir / "release/bundle/macos").glob("*.app"))
    if not candidates:
        raise FileNotFoundError(f"no macOS .app found below {target_dir}")
    return next((candidate for candidate in candidates if candidate.name == "Meeting Copilot.app"), candidates[0])


def clone_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["cp", "-cR", str(source), str(destination)], capture_output=True, text=True)
    if result.returncode != 0:
        shutil.copytree(source, destination, symlinks=True)


def directory_size(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_runtime_app(
    *,
    repo_root: Path,
    runtime_bundle: Path,
    output_root: Path,
    run_id: str,
    cargo_tauri: str = "cargo-tauri",
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    runtime_bundle = runtime_bundle.resolve()
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    runtime_manifest = validate_runtime_bundle(runtime_bundle)

    run_root = output_root / run_id
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    overlay_path = run_root / "tauri.runtime.overlay.json"
    overlay = build_overlay(runtime_bundle, overlay_path)
    target_dir = run_root / "cargo-target"
    command = build_command(overlay_path=overlay_path, rust_target_dir=target_dir, cargo_tauri=cargo_tauri)
    environment = dict(os.environ)
    cargo_home = repo_root / "artifacts/tmp/rust_toolchain/cargo"
    rustup_home = repo_root / "artifacts/tmp/rust_toolchain/rustup"
    toolchain_bin = rustup_home / "toolchains/stable-aarch64-apple-darwin/bin"
    environment.update(
        {
            "CARGO_HOME": str(cargo_home),
            "RUSTUP_HOME": str(rustup_home),
            "CARGO_TARGET_DIR": str(target_dir),
            "PATH": ":".join([str(toolchain_bin), str(cargo_home / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        }
    )
    cargo_executable = cargo_tauri
    cargo_path = Path(cargo_tauri)
    if not cargo_path.is_absolute() and ("/" in cargo_tauri or "\\" in cargo_tauri):
        cargo_executable = str((repo_root / cargo_path).resolve())
    command[0] = cargo_executable
    completed = subprocess.run(
        command,
        cwd=DESKTOP_ROOT / "src-tauri",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    (run_root / "build.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_root / "build.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Tauri app build failed with exit {completed.returncode}")

    built_app = find_built_app(target_dir)
    packaged_app = run_root / "Meeting Copilot.app"
    clone_tree(built_app, packaged_app)
    resource_root = packaged_app / "Contents/Resources/MeetingCopilotRuntime.bundle"
    try:
        packaged_manifest = validate_runtime_bundle(resource_root)
        missing_packaged: list[str] = []
    except ValueError as exc:
        packaged_manifest = None
        missing_packaged = [str(exc)]
    evidence = {
        "schema_version": "meeting_copilot.tauri_runtime_package.v1",
        "run_id": run_id,
        "host_platform": platform.platform(),
        "architecture": platform.machine(),
        "runtime_bundle_source": str(runtime_bundle),
        "runtime_manifest": runtime_manifest,
        "packaged_runtime_manifest": packaged_manifest,
        "overlay": overlay,
        "build_command": command,
        "build_return_code": completed.returncode,
        "app_path": str(packaged_app.relative_to(repo_root)),
        "app_logical_size_bytes": directory_size(packaged_app),
        "resource_root_present": resource_root.is_dir(),
        "required_packaged_missing": missing_packaged,
        "app_binary": {
            "path": "Contents/MacOS/meeting-copilot-desktop",
            "sha256": sha256_file(packaged_app / "Contents/MacOS/meeting-copilot-desktop"),
        },
        "decision": {
            "status": "go_packaged_runtime_resource_app_not_public_release"
            if not missing_packaged
            else "no_go_packaged_runtime_resource_app",
            "counts_as_packaged_runtime_resource_evidence": not missing_packaged,
            "counts_as_packaged_mainline_evidence": False,
            "counts_as_public_release_package": False,
        },
        "remaining_blockers": [
            "packaged_app_runtime_supervisor_execution_not_yet_verified",
            "not_signed_or_notarized",
            "separate_clean_mac_not_verified",
            "model_and_dependency_redistribution_not_approved",
        ],
    }
    evidence_path = run_root / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence | {"evidence_path": str(evidence_path.relative_to(repo_root))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--runtime-bundle", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cargo-tauri", default="cargo-tauri")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = package_runtime_app(
        repo_root=args.repo_root,
        runtime_bundle=args.runtime_bundle,
        output_root=args.output_root,
        run_id=args.run_id,
        cargo_tauri=args.cargo_tauri,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"]["counts_as_packaged_runtime_resource_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
