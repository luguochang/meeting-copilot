#!/usr/bin/env python3
"""Build and validate auditable inside-out macOS code-signing plans.

It inventories Mach-O files by magic bytes, applies a narrow entitlement policy,
and emits and executes explicit per-target commands without relying on codesign's
recursive ``--deep`` behavior.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
from pathlib import Path, PurePosixPath
import plistlib
import stat
import subprocess
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAIN_ENTITLEMENTS = REPO_ROOT / "code/desktop_tauri/src-tauri/Entitlements.plist"
DEFAULT_NATIVE_MIC_ENTITLEMENTS = (
    REPO_ROOT / "code/desktop_tauri/native_mic/Entitlements.plist"
)

SIGNING_PLAN_SCHEMA = "meeting_copilot.macos_codesign_plan.v1"
VERIFICATION_PLAN_SCHEMA = "meeting_copilot.macos_codesign_verification_plan.v1"
NATIVE_MIC_EXECUTABLE_NAME = "meeting-copilot-native-mic"
NATIVE_MIC_RELATIVE_PATH = (
    f"Contents/Resources/MeetingCopilotRuntime.bundle/bin/{NATIVE_MIC_EXECUTABLE_NAME}"
)
MAIN_EXECUTABLE_RELATIVE_PREFIX = "Contents/MacOS/"
AUDIO_INPUT_ENTITLEMENT = "com.apple.security.device.audio-input"
MINIMAL_AUDIO_INPUT_ENTITLEMENTS = {AUDIO_INPUT_ENTITLEMENT: True}

MACHO_MAGICS: dict[bytes, str] = {
    bytes.fromhex("ce fa ed fe"): "MH_MAGIC",
    bytes.fromhex("fe ed fa ce"): "MH_CIGAM",
    bytes.fromhex("cf fa ed fe"): "MH_MAGIC_64",
    bytes.fromhex("fe ed fa cf"): "MH_CIGAM_64",
    bytes.fromhex("ca fe ba be"): "FAT_MAGIC",
    bytes.fromhex("be ba fe ca"): "FAT_CIGAM",
    bytes.fromhex("ca fe ba bf"): "FAT_MAGIC_64",
    bytes.fromhex("bf ba fe ca"): "FAT_CIGAM_64",
}

FORBIDDEN_ENTITLEMENTS = frozenset(
    {
        "com.apple.security.get-task-allow",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.app-sandbox",
    }
)
ENTITLED_ROLES = frozenset({"main-app", "native-mic"})


def macho_magic_name(path: Path | str) -> str | None:
    """Return a known Mach-O magic name for a regular, non-symlink file."""

    candidate = Path(path)
    try:
        mode = candidate.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    try:
        with candidate.open("rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        raise ValueError(f"cannot read potential Mach-O file: {candidate}") from exc
    return MACHO_MAGICS.get(magic)


def is_macho(path: Path | str) -> bool:
    return macho_magic_name(path) is not None


def enumerate_macho_files(app_path: Path | str) -> list[Path]:
    """Enumerate real Mach-O files below an app without following symlinks."""

    app = _validated_app_path(app_path)
    return sorted(
        (candidate for candidate in app.rglob("*") if macho_magic_name(candidate)),
        key=lambda candidate: candidate.as_posix(),
    )


def load_entitlements(source: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        payload = dict(source)
    else:
        path = Path(source)
        try:
            payload = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ValueError(f"cannot read entitlement plist: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("entitlement plist root must be a dictionary")
    if any(not isinstance(key, str) for key in payload):
        raise ValueError("entitlement keys must be strings")
    return payload


def validate_entitlements(
    source: Path | str | Mapping[str, Any],
    *,
    target_role: str,
) -> dict[str, Any]:
    """Enforce the exact entitlement allowlist for a signing target role."""

    role = target_role.replace("_", "-").lower()
    payload = load_entitlements(source)
    forbidden = sorted(set(payload) & FORBIDDEN_ENTITLEMENTS)
    unknown = sorted(set(payload) - {AUDIO_INPUT_ENTITLEMENT})
    if forbidden:
        raise ValueError(f"forbidden entitlement(s): {', '.join(forbidden)}")
    if unknown:
        raise ValueError(f"unknown entitlement(s): {', '.join(unknown)}")
    if role not in ENTITLED_ROLES:
        if payload:
            raise ValueError(f"{role} must not use entitlements")
        return payload
    if payload != MINIMAL_AUDIO_INPUT_ENTITLEMENTS or payload.get(
        AUDIO_INPUT_ENTITLEMENT
    ) is not True:
        raise ValueError(
            f"{role} entitlements must be exactly "
            f"{{'{AUDIO_INPUT_ENTITLEMENT}': true}}"
        )
    return payload


def build_signing_plan(
    app_path: Path | str,
    *,
    mode: str,
    identity: str | None = None,
    main_entitlements: Path | str = DEFAULT_MAIN_ENTITLEMENTS,
    native_mic_entitlements: Path | str = DEFAULT_NATIVE_MIC_ENTITLEMENTS,
) -> dict[str, Any]:
    """Build a deterministic leaf-first signing plan with the app bundle last."""

    app = _validated_app_path(app_path)
    canonical_mode, resolved_identity = _resolve_identity(mode, identity)
    main_entitlements_path = Path(main_entitlements).expanduser().resolve()
    native_entitlements_path = Path(native_mic_entitlements).expanduser().resolve()
    main_payload = validate_entitlements(main_entitlements_path, target_role="main-app")

    macho_paths = enumerate_macho_files(app)
    native_mic_paths = [
        candidate
        for candidate in macho_paths
        if candidate.relative_to(app).as_posix() == NATIVE_MIC_RELATIVE_PATH
    ]
    native_payload: dict[str, Any] | None = None
    if native_mic_paths:
        native_payload = validate_entitlements(
            native_entitlements_path,
            target_role="native-mic",
        )

    inventory = [
        {
            "relative_path": candidate.relative_to(app).as_posix(),
            "magic": _require_macho_magic(candidate),
            "size_bytes": candidate.stat().st_size,
            "role": _target_role(app, candidate),
        }
        for candidate in macho_paths
    ]

    main_executable_paths = [
        candidate
        for candidate in macho_paths
        if _target_role(app, candidate) == "main-executable"
    ]
    if len(main_executable_paths) != 1:
        raise ValueError("app must contain exactly one main executable")

    # The app bundle is the signing principal for its main executable. Signing
    # that executable independently would be overwritten by the final app sign.
    leaf_paths = sorted(
        [
            candidate
            for candidate in macho_paths
            if _target_role(app, candidate) != "main-executable"
        ],
        key=lambda candidate: (
            -len(candidate.relative_to(app).parts),
            candidate.relative_to(app).as_posix(),
        ),
    )
    secure_timestamp = canonical_mode == "developer-id"
    signing_steps: list[dict[str, Any]] = []
    for candidate in leaf_paths:
        role = _target_role(app, candidate)
        if role == "native-mic":
            entitlement_path: Path | None = native_entitlements_path
            entitlement_payload = native_payload
        else:
            entitlement_path = None
            entitlement_payload = None
        signing_steps.append(
            _signing_step(
                order=len(signing_steps) + 1,
                app=app,
                target=candidate,
                role=role,
                identity=resolved_identity,
                secure_timestamp=secure_timestamp,
                entitlement_path=entitlement_path,
                entitlement_payload=entitlement_payload,
            )
        )

    signing_steps.append(
        _signing_step(
            order=len(signing_steps) + 1,
            app=app,
            target=app,
            role="main-app",
            identity=resolved_identity,
            secure_timestamp=secure_timestamp,
            entitlement_path=main_entitlements_path,
            entitlement_payload=main_payload,
        )
    )
    plan: dict[str, Any] = {
        "schema_version": SIGNING_PLAN_SCHEMA,
        "app_path": str(app),
        "mode": canonical_mode,
        "identity": resolved_identity,
        "hardened_runtime": True,
        "secure_timestamp": secure_timestamp,
        "uses_deep_signing": False,
        "native_mic_relative_path": NATIVE_MIC_RELATIVE_PATH,
        "main_executable_relative_path": main_executable_paths[0]
        .relative_to(app)
        .as_posix(),
        "entitlement_policy": _entitlement_policy(),
        "macho_inventory": inventory,
        "signing_steps": signing_steps,
    }
    return validate_signing_plan(plan)


def validate_signing_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed if an audit plan drifts from the signing policy."""

    if not isinstance(plan, Mapping):
        raise ValueError("signing plan must be a JSON object")
    checked = dict(plan)
    if checked.get("schema_version") != SIGNING_PLAN_SCHEMA:
        raise ValueError(f"signing plan schema must be {SIGNING_PLAN_SCHEMA}")
    mode, identity = _resolve_identity(
        str(checked.get("mode") or ""),
        str(checked.get("identity") or "") or None,
        validating_plan=True,
    )
    if checked.get("hardened_runtime") is not True:
        raise ValueError("signing plan must enable hardened runtime")
    if checked.get("uses_deep_signing") is not False:
        raise ValueError("signing plan must explicitly disable --deep")
    if checked.get("native_mic_relative_path") != NATIVE_MIC_RELATIVE_PATH:
        raise ValueError("signing plan native microphone helper path is not fixed")
    if checked.get("entitlement_policy") != _entitlement_policy():
        raise ValueError("signing plan entitlement policy declaration is not allowed")
    expected_timestamp = mode == "developer-id"
    if checked.get("secure_timestamp") is not expected_timestamp:
        raise ValueError("secure timestamp policy does not match signing mode")

    raw_steps = checked.get("signing_steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("signing plan must contain signing steps")
    if any(not isinstance(step, Mapping) for step in raw_steps):
        raise ValueError("each signing step must be a JSON object")
    if raw_steps[-1].get("role") != "main-app" or raw_steps[-1].get(
        "relative_path"
    ) != ".":
        raise ValueError("main app must be the last signing step")
    if any(step.get("role") == "main-app" for step in raw_steps[:-1]):
        raise ValueError("main app may only appear as the last signing step")
    if any(step.get("role") == "main-executable" for step in raw_steps):
        raise ValueError(
            "main executable is signed as the main-app principal and must not be an independent step"
        )

    expected_orders = list(range(1, len(raw_steps) + 1))
    if [step.get("order") for step in raw_steps] != expected_orders:
        raise ValueError("signing step orders must be contiguous")
    target_paths = [str(step.get("target") or "") for step in raw_steps]
    if len(target_paths) != len(set(target_paths)) or any(not path for path in target_paths):
        raise ValueError("signing step targets must be unique non-empty paths")
    if target_paths[-1] != str(checked.get("app_path") or ""):
        raise ValueError("last signing target must match app_path")
    app = _validated_app_path(str(checked["app_path"]))

    leaf_relative_paths = [str(step.get("relative_path") or "") for step in raw_steps[:-1]]
    expected_leaf_order = sorted(
        leaf_relative_paths,
        key=lambda relative: (-len(PurePosixPath(relative).parts), relative),
    )
    if leaf_relative_paths != expected_leaf_order:
        raise ValueError("nested Mach-O signing steps must be deterministically inside-out")

    for step in raw_steps:
        relative_path = str(step.get("relative_path") or "")
        if relative_path == ".":
            expected_target = app
            expected_role = "main-app"
        else:
            relative = PurePosixPath(relative_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("signing relative_path must stay inside app_path")
            expected_target = app.joinpath(*relative.parts)
            expected_role = _role_for_relative_path(relative_path)
        if Path(str(step.get("target") or "")) != expected_target:
            raise ValueError("signing target must resolve from relative_path inside app_path")
        if step.get("role") != expected_role:
            if step.get("role") == "native-mic" or expected_role == "native-mic":
                raise ValueError("native-mic role is restricted to the fixed packaged path")
            raise ValueError("signing target role does not match its app-relative path")
        _validate_signing_step(
            step,
            identity=identity,
            secure_timestamp=expected_timestamp,
        )

    raw_inventory = checked.get("macho_inventory")
    if not isinstance(raw_inventory, list):
        raise ValueError("macho_inventory must be a list")
    if any(not isinstance(item, Mapping) for item in raw_inventory):
        raise ValueError("each Mach-O inventory item must be a JSON object")
    inventory_paths = [str(item.get("relative_path") or "") for item in raw_inventory]
    known_magic_names = set(MACHO_MAGICS.values())
    if any(item.get("magic") not in known_magic_names for item in raw_inventory):
        raise ValueError("Mach-O inventory contains an unknown magic")
    for item in raw_inventory:
        relative_path = str(item.get("relative_path") or "")
        if item.get("role") != _role_for_relative_path(relative_path):
            raise ValueError("Mach-O inventory role does not match its path")
        if type(item.get("size_bytes")) is not int or item["size_bytes"] < 4:
            raise ValueError("Mach-O inventory size must be an integer of at least 4 bytes")
    main_executable_relative_paths = [
        str(item["relative_path"])
        for item in raw_inventory
        if item.get("role") == "main-executable"
    ]
    if len(main_executable_relative_paths) != 1:
        raise ValueError("Mach-O inventory must contain exactly one main executable")
    if checked.get("main_executable_relative_path") != main_executable_relative_paths[0]:
        raise ValueError("signing plan main executable path does not match inventory")
    inventory_leaf_paths = [
        path for path in inventory_paths if path not in main_executable_relative_paths
    ]
    if sorted(inventory_leaf_paths) != sorted(leaf_relative_paths):
        raise ValueError("Mach-O inventory and leaf signing steps must match")
    current_macho_paths = enumerate_macho_files(app)
    current_inventory = {
        candidate.relative_to(app).as_posix(): _require_macho_magic(candidate)
        for candidate in current_macho_paths
    }
    planned_inventory = {
        str(item["relative_path"]): str(item["magic"]) for item in raw_inventory
    }
    if current_inventory != planned_inventory:
        raise ValueError("Mach-O inventory changed after planning")
    return checked


def execute_signing_plan(
    signing_plan: Mapping[str, Any],
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    before_step: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute each signing step in order and stop at the first failure.

    ``before_step`` is called immediately before a step is executed. Packaging
    uses the boundary before the main-app principal to refresh resources that
    may have been materialized by the app bundler before signing.
    """

    plan = validate_signing_plan(signing_plan)
    results = []
    for step in plan["signing_steps"]:
        if before_step is not None:
            before_step(step)
        result = _run_codesign(command_runner, step["command"])
        if result.returncode != 0:
            raise RuntimeError(
                f"codesign signing failed for {step['relative_path']}: "
                f"{result.stderr.strip()}"
            )
        results.append(
            {
                "order": step["order"],
                "relative_path": step["relative_path"],
                "role": step["role"],
                "sign_return_code": result.returncode,
            }
        )
    return {
        "schema_version": "meeting_copilot.macos_codesign_execution_result.v1",
        "app_path": plan["app_path"],
        "mode": plan["mode"],
        "identity": plan["identity"],
        "uses_deep_signing": False,
        "hardened_runtime": True,
        "status": "passed",
        "signed_target_count": len(results),
        "results": results,
    }


def sign_and_verify(
    app_path: Path | str,
    *,
    mode: str,
    identity: str | None = None,
    main_entitlements: Path | str = DEFAULT_MAIN_ENTITLEMENTS,
    native_mic_entitlements: Path | str = DEFAULT_NATIVE_MIC_ENTITLEMENTS,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    before_step: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Build, execute, and strictly verify an inside-out signing plan."""

    signing_plan = build_signing_plan(
        app_path,
        mode=mode,
        identity=identity,
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    signing = execute_signing_plan(
        signing_plan,
        command_runner=command_runner,
        before_step=before_step,
    )
    verification = verify_signed_app(signing_plan, command_runner=command_runner)
    return {
        "signing_plan": signing_plan,
        "signing": signing,
        "verification": verification,
    }


def build_verification_plan(signing_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Build strict per-target verification commands from a validated signing plan."""

    checked = validate_signing_plan(signing_plan)
    main_executable_relative_path = checked["main_executable_relative_path"]
    main_executable_target = str(
        Path(checked["app_path"]) / main_executable_relative_path
    )
    verification_steps = []
    for signing_step in checked["signing_steps"]:
        target = str(signing_step["target"])
        verification_step = {
                "order": signing_step["order"],
                "relative_path": signing_step["relative_path"],
                "role": signing_step["role"],
                "target": target,
                "expected_entitlements": signing_step["entitlements_payload"],
                "command": ["codesign", "--verify", "--strict", "--verbose=2", target],
                "display_command": ["codesign", "--display", "--verbose=4", target],
                "entitlements_command": [
                    "codesign",
                    "--display",
                    "--entitlements",
                    ":-",
                    "--xml",
                    target,
                ],
            }
        if signing_step["role"] == "main-app":
            verification_step.update(
                {
                    "principal_executable_relative_path": main_executable_relative_path,
                    "principal_executable_target": main_executable_target,
                    "principal_command": [
                        "codesign",
                        "--verify",
                        "--strict",
                        "--verbose=2",
                        main_executable_target,
                    ],
                    "principal_display_command": [
                        "codesign",
                        "--display",
                        "--verbose=4",
                        main_executable_target,
                    ],
                    "principal_entitlements_command": [
                        "codesign",
                        "--display",
                        "--entitlements",
                        ":-",
                        "--xml",
                        main_executable_target,
                    ],
                }
            )
        verification_steps.append(verification_step)
    return {
        "schema_version": VERIFICATION_PLAN_SCHEMA,
        "signing_plan_schema_version": checked["schema_version"],
        "app_path": checked["app_path"],
        "mode": checked["mode"],
        "identity": checked["identity"],
        "requires_hardened_runtime": True,
        "uses_deep_verification": False,
        "verification_steps": verification_steps,
    }


def verify_signed_app(
    signing_plan: Mapping[str, Any],
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Strictly verify each planned target, runtime flag, identity, and entitlements."""

    plan = validate_signing_plan(signing_plan)
    verification = build_verification_plan(plan)
    results = []
    for step in verification["verification_steps"]:
        verify_result = _run_codesign(command_runner, step["command"])
        if verify_result.returncode != 0:
            raise RuntimeError(
                f"codesign verification failed for {step['relative_path']}: "
                f"{verify_result.stderr.strip()}"
            )
        display_result = _run_codesign(command_runner, step["display_command"])
        if display_result.returncode != 0:
            raise RuntimeError(
                f"codesign metadata read failed for {step['relative_path']}: "
                f"{display_result.stderr.strip()}"
            )
        metadata = f"{display_result.stdout}\n{display_result.stderr}"
        if not _metadata_has_runtime_flag(metadata):
            raise RuntimeError(
                f"hardened runtime flag missing for {step['relative_path']}"
            )
        _validate_reported_identity(
            metadata,
            mode=plan["mode"],
            identity=plan["identity"],
            relative_path=step["relative_path"],
        )

        entitlements_result = _run_codesign(command_runner, step["entitlements_command"])
        if entitlements_result.returncode != 0:
            raise RuntimeError(
                f"codesign entitlement read failed for {step['relative_path']}: "
                f"{entitlements_result.stderr.strip()}"
            )
        reported_entitlements = parse_codesign_entitlements(
            f"{entitlements_result.stdout}\n{entitlements_result.stderr}"
        )
        validated_entitlements = validate_entitlements(
            reported_entitlements,
            target_role=step["role"],
        )
        if validated_entitlements != (step["expected_entitlements"] or {}):
            raise RuntimeError(
                f"signed entitlements differ from plan for {step['relative_path']}"
            )
        result_entry = {
                "order": step["order"],
                "relative_path": step["relative_path"],
                "role": step["role"],
                "strict_verification_return_code": verify_result.returncode,
                "runtime_verified": True,
                "identity_verified": True,
                "entitlements_verified": True,
            }
        principal_target = step.get("principal_executable_target")
        if principal_target:
            principal_verify_result = _run_codesign(
                command_runner, step["principal_command"]
            )
            if principal_verify_result.returncode != 0:
                raise RuntimeError(
                    "codesign verification failed for main executable principal: "
                    f"{principal_verify_result.stderr.strip()}"
                )
            principal_display_result = _run_codesign(
                command_runner, step["principal_display_command"]
            )
            if principal_display_result.returncode != 0:
                raise RuntimeError(
                    "codesign metadata read failed for main executable principal: "
                    f"{principal_display_result.stderr.strip()}"
                )
            principal_metadata = (
                f"{principal_display_result.stdout}\n{principal_display_result.stderr}"
            )
            if not _metadata_has_runtime_flag(principal_metadata):
                raise RuntimeError("hardened runtime flag missing for main executable principal")
            _validate_reported_identity(
                principal_metadata,
                mode=plan["mode"],
                identity=plan["identity"],
                relative_path=step["principal_executable_relative_path"],
            )
            principal_entitlements_result = _run_codesign(
                command_runner, step["principal_entitlements_command"]
            )
            if principal_entitlements_result.returncode != 0:
                raise RuntimeError(
                    "codesign entitlement read failed for main executable principal: "
                    f"{principal_entitlements_result.stderr.strip()}"
                )
            principal_reported_entitlements = parse_codesign_entitlements(
                f"{principal_entitlements_result.stdout}\n"
                f"{principal_entitlements_result.stderr}"
            )
            principal_validated_entitlements = validate_entitlements(
                principal_reported_entitlements,
                target_role="main-app",
            )
            if principal_validated_entitlements != (step["expected_entitlements"] or {}):
                raise RuntimeError(
                    "main executable principal entitlements differ from app principal"
                )
            result_entry.update(
                {
                    "principal_runtime_verified": True,
                    "principal_identity_verified": True,
                    "principal_entitlements_verified": True,
                }
            )
        results.append(result_entry)
    return {
        "schema_version": "meeting_copilot.macos_codesign_verification_result.v1",
        "app_path": plan["app_path"],
        "mode": plan["mode"],
        "identity": plan["identity"],
        "uses_deep_verification": False,
        "status": "passed",
        "results": results,
    }


def parse_codesign_entitlements(output: str) -> dict[str, Any]:
    """Extract an XML plist from codesign output; no plist means no entitlements."""

    start = output.find("<?xml")
    end_marker = "</plist>"
    end = output.find(end_marker, start)
    if start < 0 or end < 0:
        return {}
    raw_plist = output[start : end + len(end_marker)].encode("utf-8")
    try:
        payload = plistlib.loads(raw_plist)
    except plistlib.InvalidFileException as exc:
        raise ValueError("codesign returned an invalid entitlement plist") from exc
    if not isinstance(payload, dict):
        raise ValueError("codesign entitlement plist root must be a dictionary")
    return payload


def _validated_app_path(app_path: Path | str) -> Path:
    raw_path = Path(app_path).expanduser()
    try:
        mode = raw_path.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"app path does not exist: {raw_path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ValueError("app path must be a real directory, not a symlink")
    app = raw_path.resolve()
    if app.suffix.lower() != ".app":
        raise ValueError("app path must end in .app")
    return app


def _resolve_identity(
    mode: str,
    identity: str | None,
    *,
    validating_plan: bool = False,
) -> tuple[str, str]:
    normalized_mode = mode.strip().lower().replace("_", "-")
    if normalized_mode in {"adhoc", "ad-hoc"}:
        if identity not in {None, "-"}:
            raise ValueError("ad-hoc mode identity must be omitted or '-'")
        return "ad-hoc", "-"
    if normalized_mode in {"developer-id", "developerid"}:
        if not identity or not identity.startswith("Developer ID Application:"):
            raise ValueError("developer-id mode requires a Developer ID Application identity")
        return "developer-id", identity
    context = "signing plan mode" if validating_plan else "mode"
    raise ValueError(f"{context} must be ad-hoc or developer-id")


def _entitlement_policy() -> dict[str, Any]:
    return {
        "allowed_entitlement": AUDIO_INPUT_ENTITLEMENT,
        "entitled_roles": sorted(ENTITLED_ROLES),
        "forbidden_entitlements": sorted(FORBIDDEN_ENTITLEMENTS),
        "unknown_entitlements_allowed": False,
    }


def _require_macho_magic(path: Path) -> str:
    magic = macho_magic_name(path)
    if magic is None:
        raise ValueError(f"signing target is not a Mach-O file: {path}")
    return magic


def _target_role(app: Path, target: Path) -> str:
    return _role_for_relative_path(target.relative_to(app).as_posix())


def _role_for_relative_path(relative_path: str) -> str:
    relative = PurePosixPath(relative_path)
    if relative.as_posix() == NATIVE_MIC_RELATIVE_PATH:
        return "native-mic"
    if len(relative.parts) >= 3 and relative.parts[:2] == ("Contents", "MacOS"):
        return "main-executable"
    return "nested-code"


def _signing_step(
    *,
    order: int,
    app: Path,
    target: Path,
    role: str,
    identity: str,
    secure_timestamp: bool,
    entitlement_path: Path | None,
    entitlement_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    entitlement_label = str(entitlement_path) if entitlement_path else None
    return {
        "order": order,
        "relative_path": "." if target == app else target.relative_to(app).as_posix(),
        "role": role,
        "target": str(target),
        "entitlements": entitlement_label,
        "entitlements_payload": entitlement_payload,
        "entitlements_sha256": _sha256(entitlement_path) if entitlement_path else None,
        "command": _sign_command(
            target=str(target),
            identity=identity,
            secure_timestamp=secure_timestamp,
            entitlements=entitlement_label,
        ),
    }


def _sign_command(
    *,
    target: str,
    identity: str,
    secure_timestamp: bool,
    entitlements: str | None,
) -> list[str]:
    command = ["codesign", "--force", "--sign", identity, "--options", "runtime"]
    if secure_timestamp:
        command.append("--timestamp")
    if entitlements:
        command.extend(["--entitlements", entitlements])
    command.append(target)
    return command


def _validate_signing_step(
    step: Mapping[str, Any],
    *,
    identity: str,
    secure_timestamp: bool,
) -> None:
    if not isinstance(step, Mapping):
        raise ValueError("each signing step must be a JSON object")
    command = step.get("command")
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
        raise ValueError("each signing command must be a string list")
    if any(item == "--deep" or item.startswith("--deep=") for item in command):
        raise ValueError("--deep is forbidden")
    if "--options" not in command:
        raise ValueError("every signing command must enable runtime options")
    options_index = command.index("--options")
    if options_index + 1 >= len(command) or command[options_index + 1] != "runtime":
        raise ValueError("every signing command must use --options runtime")

    role = str(step.get("role") or "")
    entitlement_path = step.get("entitlements")
    entitlement_payload = step.get("entitlements_payload")
    if role in ENTITLED_ROLES:
        if not isinstance(entitlement_path, str) or not entitlement_path:
            raise ValueError(f"{role} must use its minimal entitlement plist")
        validate_entitlements(entitlement_payload or {}, target_role=role)
        digest = step.get("entitlements_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"{role} entitlement plist must have an audit hash")
        try:
            current_digest = _sha256(Path(entitlement_path))
        except OSError as exc:
            raise ValueError(f"{role} entitlement plist changed after planning") from exc
        if current_digest != digest:
            raise ValueError(f"{role} entitlement plist changed after planning")
    else:
        if entitlement_path is not None or entitlement_payload is not None:
            raise ValueError(f"{role} must not use entitlements")
        if step.get("entitlements_sha256") is not None:
            raise ValueError(f"{role} must not record an entitlement hash")

    expected = _sign_command(
        target=str(step.get("target") or ""),
        identity=identity,
        secure_timestamp=secure_timestamp,
        entitlements=entitlement_path,
    )
    if command != expected:
        raise ValueError("signing command does not match runtime, identity, or entitlement policy")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_codesign(
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return command_runner(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )


def _validate_reported_identity(
    metadata: str,
    *,
    mode: str,
    identity: str,
    relative_path: str,
) -> None:
    if mode == "ad-hoc":
        if "Signature=adhoc" not in metadata:
            raise RuntimeError(f"ad-hoc identity missing for {relative_path}")
        return
    if f"Authority={identity}" not in metadata:
        raise RuntimeError(f"Developer ID identity missing for {relative_path}")


def _metadata_has_runtime_flag(metadata: str) -> bool:
    return any(
        "flags=" in line and "runtime" in line.lower()
        for line in metadata.splitlines()
    )


def _write_json(payload: Mapping[str, Any], output: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="build an auditable signing plan")
    plan_parser.add_argument("--app", type=Path, required=True)
    plan_parser.add_argument("--mode", choices=("ad-hoc", "developer-id"), required=True)
    plan_parser.add_argument("--identity")
    plan_parser.add_argument(
        "--main-entitlements",
        type=Path,
        default=DEFAULT_MAIN_ENTITLEMENTS,
    )
    plan_parser.add_argument(
        "--native-mic-entitlements",
        type=Path,
        default=DEFAULT_NATIVE_MIC_ENTITLEMENTS,
    )
    plan_parser.add_argument("--output", type=Path)

    verify_parser = subparsers.add_parser(
        "verification-plan",
        help="validate a signing plan and emit strict verification commands",
    )
    verify_parser.add_argument("--signing-plan", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        payload = build_signing_plan(
            args.app,
            mode=args.mode,
            identity=args.identity,
            main_entitlements=args.main_entitlements,
            native_mic_entitlements=args.native_mic_entitlements,
        )
    else:
        try:
            signing_plan = json.loads(args.signing_plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read signing plan: {args.signing_plan}") from exc
        payload = build_verification_plan(signing_plan)
    _write_json(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
