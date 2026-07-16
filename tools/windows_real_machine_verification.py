#!/usr/bin/env python3
"""Validate caller-provided Windows real-machine verification evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_TMP = (REPO_ROOT / "artifacts" / "tmp").resolve()
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/windows_real_machine_verification_current"

REQUIRED_CHECKS = {
    "tauri_webview_opened": "windows_tauri_webview_not_verified",
    "backend_health_ok": "windows_backend_health_not_verified",
    "workbench_loaded": "windows_workbench_not_verified",
    "provider_health_visible": "windows_provider_health_not_verified",
    "microphone_permission_path_verified": "windows_microphone_permission_path_not_verified",
    "realtime_asr_to_suggestions_minutes_history_delete_go": "windows_realtime_mainline_not_verified",
    "file_import_export_go": "windows_file_import_export_not_verified",
    "installer_or_portable_launch_smoke_go": "windows_installer_or_portable_launch_not_verified",
    "delete_verified": "windows_delete_not_verified",
    "no_secret_values_included": "windows_secret_redaction_not_verified",
}


def resolve_artifacts_tmp_path(path: Path, *, label: str) -> Path:
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        resolved.relative_to(ARTIFACTS_TMP)
    except ValueError as exc:
        raise ValueError(f"{label} must be under artifacts/tmp") from exc
    return resolved


def build_evidence(*, input_path: Path | None, run_id: str) -> dict[str, Any]:
    observation = _load_observation(input_path)
    checks = dict(observation.get("checks") or {})
    privacy = dict(observation.get("privacy_safety") or {})

    remaining_blockers: list[str] = []
    if observation.get("machine_kind") != "real_windows_machine":
        remaining_blockers.append("windows_real_machine_not_observed")
    if "Windows" not in str(observation.get("host_os") or ""):
        remaining_blockers.append("windows_host_os_not_observed")
    for check_name, blocker in REQUIRED_CHECKS.items():
        if checks.get(check_name) is not True:
            remaining_blockers.append(blocker)
    if privacy.get("secret_values_included") is True:
        remaining_blockers.append("windows_input_includes_secret_values")
    if privacy.get("raw_user_audio_included") is True:
        remaining_blockers.append("windows_input_includes_raw_user_audio")
    if privacy.get("configs_local_included") is True:
        remaining_blockers.append("windows_input_includes_configs_local")

    verified = not remaining_blockers
    return {
        "schema_version": "windows_real_machine_verification.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host_platform": platform.platform(),
        "input_path": _display_path(input_path) if input_path else None,
        "status": "go_windows_real_machine_verified" if verified else "blocked_windows_real_machine_verification",
        "windows_real_machine_verified": verified,
        "host_os": observation.get("host_os", ""),
        "machine_kind": observation.get("machine_kind", "missing"),
        "app_package_kind": observation.get("app_package_kind", "missing"),
        "checks": {name: bool(checks.get(name) is True) for name in REQUIRED_CHECKS},
        "privacy_safety": {
            "secret_values_included": bool(privacy.get("secret_values_included") is True),
            "raw_user_audio_included": bool(privacy.get("raw_user_audio_included") is True),
            "configs_local_included": bool(privacy.get("configs_local_included") is True),
        },
        "remaining_blockers": remaining_blockers,
    }


def write_evidence(evidence: dict[str, Any], output_root: Path) -> Path:
    output_root = resolve_artifacts_tmp_path(output_root, label="output_root")
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "evidence.json"
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def _load_observation(input_path: Path | None) -> dict[str, Any]:
    if input_path is None:
        return {}
    resolved = resolve_artifacts_tmp_path(input_path, label="input_path")
    if not resolved.is_file():
        raise FileNotFoundError(f"input_path does not exist: {resolved}")
    return json.loads(resolved.read_text(encoding="utf-8"))


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default="windows-real-machine-verification-current")
    args = parser.parse_args(argv)
    evidence = build_evidence(input_path=args.input, run_id=args.run_id)
    output_path = write_evidence(evidence, args.output_root)
    print(json.dumps({"status": evidence["status"], "evidence": str(output_path)}, ensure_ascii=False))
    return 0 if evidence["windows_real_machine_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
