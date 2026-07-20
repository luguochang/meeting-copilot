#!/usr/bin/env python3
"""Run a local, no-Finder macOS DMG install and packaged runtime smoke."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import signal
import subprocess
import time
from typing import Any

from package_macos_dmg_skip_finder import build_direct_hdiutil_command
from packaged_runtime_supervisor_smoke import (
    bootstrap_cookie,
    find_backend_process,
    find_funasr_process,
    health_proof,
    http_response,
    packaged_app_launch_command,
    pid_exists,
    port_is_listening,
    read_process_table,
    validate_run_id,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_TMP = (REPO_ROOT / "artifacts" / "tmp").resolve()
DEFAULT_APP = (
    REPO_ROOT
    / "artifacts/tmp/tauri_runtime_package/phase0-2-mainline-r7-tauri-20260717/Meeting Copilot.app"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/macos_dmg_install_smoke"
DEFAULT_VOLUME_NAME = "Meeting Copilot"
DEFAULT_DMG_NAME = "Meeting Copilot_0.1.0_aarch64.install-smoke.dmg"


def resolve_repo_path(repo_root: Path, path: Path) -> Path:
    return (path if path.is_absolute() else repo_root / path).resolve()


def resolve_output_root(repo_root: Path, output_root: Path) -> Path:
    resolved = resolve_repo_path(repo_root, output_root)
    try:
        resolved.relative_to((repo_root / "artifacts" / "tmp").resolve())
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def parse_attach_plist(raw: bytes | str) -> tuple[Path, str | None]:
    payload = plistlib.loads(raw.encode("utf-8") if isinstance(raw, str) else raw)
    for entity in payload.get("system-entities", []):
        mount_point = entity.get("mount-point")
        if mount_point:
            return Path(str(mount_point)), str(entity.get("dev-entry") or "") or None
    raise ValueError("mount point not found in hdiutil attach plist")


def build_attach_command(dmg_path: Path) -> list[str]:
    return ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(dmg_path)]


def build_detach_command(mount_dir: Path, *, force: bool = False) -> list[str]:
    force_args = ["-force"] if force else []
    return ["hdiutil", "detach", *force_args, str(mount_dir)]


def sanitized_environment(home: Path, token: str) -> dict[str, str]:
    blocked_fragments = (
        "API_KEY",
        "AUTHORIZATION",
        "PASSWORD",
        "SECRET",
        "KEYCHAIN",
    )
    blocked_prefixes = ("AWS_", "AZURE_", "GOOGLE_", "ANTHROPIC_", "OPENAI_", "LLM_GATEWAY_")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in blocked_fragments)
        and not key.upper().startswith(blocked_prefixes)
        and key not in {"MEETING_COPILOT_LOCAL_API_TOKEN", "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE"}
    }
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": token,
        }
    )
    return environment


def _validate_app(app_path: Path) -> Path:
    app_path = app_path.resolve()
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    if not app_path.is_dir() or app_path.suffix != ".app" or not binary.is_file():
        raise FileNotFoundError(f"r7 app bundle or executable is missing: {app_path}")
    return app_path


def _validate_dmg(dmg_path: Path) -> Path:
    dmg_path = dmg_path.resolve()
    if not dmg_path.is_file() or dmg_path.suffix.lower() != ".dmg":
        raise FileNotFoundError(f"DMG is missing: {dmg_path}")
    return dmg_path


def _validate_volume_name(volume_name: str) -> None:
    if not volume_name or "/" in volume_name or "\x00" in volume_name:
        raise ValueError("volume_name must be a non-empty path-safe value")


def _run(command: list[str], *, check: bool = True) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result = {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            completed.stdout,
            completed.stderr,
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _create_dmg(
    *,
    app_path: Path,
    run_root: Path,
    dmg_name: str,
    volume_name: str,
    command_results: dict[str, dict[str, Any]],
) -> Path:
    source_dir = run_root / "dmg-source"
    staged_app = source_dir / app_path.name
    output_dmg = run_root / dmg_name
    source_dir.mkdir(parents=True, exist_ok=True)
    command_results["stage_app"] = _run(["ditto", str(app_path), str(staged_app)])
    command_results["verify_staged_app_codesign"] = _run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(staged_app)]
    )
    os.symlink("/Applications", source_dir / "Applications")
    command_results["create_dmg"] = _run(
        build_direct_hdiutil_command(
            output_dmg=output_dmg,
            source_dir=source_dir,
            volume_name=volume_name,
        )
    )
    return output_dmg


def _probe_local_backend_workbench(
    *,
    app_path: Path,
    app_process: subprocess.Popen[bytes],
    token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    started_at = time.monotonic()
    backend: dict[str, Any] | None = None
    health_identity_verified = False
    while time.monotonic() - started_at < timeout_seconds:
        if app_process.poll() is not None:
            break
        backend = find_backend_process(read_process_table(), app_pid=app_process.pid, app_path=app_path)
        if backend is not None:
            health = http_response(int(backend["port"]), "/health")
            if health["status"] == 200 and health_proof(token).encode("ascii") in health["body"]:
                health_identity_verified = True
                break
        time.sleep(0.1)

    responses: dict[str, int | None] = {}
    workbench_body_bytes = 0
    workbench_loopback = False
    bootstrap_authenticated = False
    resident_ready = False
    funasr_process: dict[str, Any] | None = None
    if backend is not None:
        port = int(backend["port"])
        health = http_response(port, "/health")
        bootstrap_status, cookie = bootstrap_cookie(port, token)
        bootstrap_authenticated = bootstrap_status == 303 and bool(cookie)
        headers = {"Cookie": cookie} if cookie else {}
        workbench = http_response(port, "/workbench", headers)
        workbench_body_bytes = len(workbench["body"])
        workbench_loopback = "--host 127.0.0.1" in str(backend["command"])
        providers = http_response(port, "/providers/health", headers)
        runtime = http_response(port, "/providers/asr/runtime", headers)
        if runtime["status"] == 200:
            try:
                runtime_payload = json.loads(runtime["body"].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                runtime_payload = {}
            resident_ready = dict(runtime_payload.get("resident") or {}).get("process_ready") is True
            funasr_process = find_funasr_process(
                read_process_table(),
                backend_pid=int(backend["pid"]),
                app_path=app_path,
            )
        responses = {
            "health": health["status"],
            "bootstrap": bootstrap_status,
            "workbench": workbench["status"],
            "providers": providers["status"],
            "asr_runtime": runtime["status"],
        }
    return {
        "backend": backend,
        "responses": responses,
        "health_identity_verified": health_identity_verified,
        "bootstrap_authenticated": bootstrap_authenticated,
        "workbench_loopback": workbench_loopback,
        "workbench_body_bytes": workbench_body_bytes,
        "resident_ready": resident_ready,
        "funasr_process": funasr_process,
    }


def _stop_process(
    process: subprocess.Popen[bytes] | None,
    *,
    timeout_seconds: float,
) -> bool | None:
    if process is None:
        return None
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.poll() is not None


def smoke_passed(
    *,
    cleanup_errors: list[str],
    resolved_dmg: Path | None,
    command_results: dict[str, dict[str, Any]],
    applications_link_present: bool,
    mount_detached: bool,
    installed_removed: bool,
    backend: dict[str, Any] | None,
    responses: dict[str, int | None],
    probe: dict[str, Any],
    app_exited: bool | None,
    backend_exited: bool | None,
    port_closed: bool | None,
    funasr_exited: bool | None,
    funasr_forced_cleanup: bool,
) -> bool:
    return (
        not cleanup_errors
        and resolved_dmg is not None
        and resolved_dmg.is_file()
        and command_results.get("verify_source_app_codesign", {}).get("returncode") == 0
        and command_results.get("verify_mounted_app_codesign", {}).get("returncode") == 0
        and command_results.get("verify_installed_app_codesign", {}).get("returncode") == 0
        and applications_link_present
        and mount_detached
        and installed_removed
        and backend is not None
        and responses == {
            "health": 200,
            "bootstrap": 303,
            "workbench": 200,
            "providers": 200,
            "asr_runtime": 200,
        }
        and probe.get("health_identity_verified") is True
        and probe.get("bootstrap_authenticated") is True
        and probe.get("resident_ready") is True
        and probe.get("workbench_loopback") is True
        and probe.get("workbench_body_bytes", 0) > 0
        and app_exited is True
        and backend_exited is True
        and port_closed is True
        and funasr_exited is True
        and not funasr_forced_cleanup
    )


def run_smoke(
    *,
    repo_root: Path = REPO_ROOT,
    app_path: Path = DEFAULT_APP,
    dmg_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    run_id: str,
    dmg_name: str = DEFAULT_DMG_NAME,
    volume_name: str = DEFAULT_VOLUME_NAME,
    startup_timeout_seconds: float = 60.0,
    cleanup_timeout_seconds: float = 15.0,
    force: bool = False,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    app_path = _validate_app(resolve_repo_path(repo_root, app_path))
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    _validate_volume_name(volume_name)
    if "/" in dmg_name or not dmg_name.endswith(".dmg"):
        raise ValueError("dmg_name must be a path-safe .dmg filename")

    run_root = output_root / run_id
    if run_root.exists():
        if not force:
            raise FileExistsError(f"run root already exists: {run_root}")
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    logs_dir = run_root / "logs"
    logs_dir.mkdir()
    install_root = run_root / "Applications"
    installed_app = install_root / app_path.name
    command_results: dict[str, dict[str, Any]] = {}
    mounted_app: Path | None = None
    mount_dir: Path | None = None
    app_process: subprocess.Popen[bytes] | None = None
    backend: dict[str, Any] | None = None
    probe: dict[str, Any] = {}
    cleanup_errors: list[str] = []
    app_exited: bool | None = None
    backend_exited: bool | None = None
    port_closed: bool | None = None
    funasr_process: dict[str, Any] | None = None
    funasr_exited: bool | None = None
    funasr_forced_cleanup = False
    mount_detached = False
    applications_link_present = False
    token = secrets.token_hex(32)
    mode = "reused" if dmg_path is not None else "generated"
    resolved_dmg: Path | None = None
    app_log = (logs_dir / "app.log").open("wb")
    started_at = time.monotonic()
    try:
        command_results["verify_source_app_codesign"] = _run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_path)]
        )
        if dmg_path is None:
            resolved_dmg = _create_dmg(
                app_path=app_path,
                run_root=run_root,
                dmg_name=dmg_name,
                volume_name=volume_name,
                command_results=command_results,
            )
        else:
            resolved_dmg = _validate_dmg(resolve_repo_path(repo_root, dmg_path))
        command_results["verify_dmg_file"] = {
            "command": "file " + str(resolved_dmg),
            "returncode": 0 if resolved_dmg.is_file() else 1,
            "stdout": "regular file",
            "stderr": "",
        }
        attach = _run(build_attach_command(resolved_dmg))
        command_results["hdiutil_attach"] = attach
        mount_dir, _device = parse_attach_plist(attach["stdout"])
        mounted_app = mount_dir / app_path.name
        applications_link = mount_dir / "Applications"
        if not mounted_app.is_dir():
            raise FileNotFoundError(f"mounted app missing: {mounted_app}")
        if not applications_link.is_symlink():
            raise FileNotFoundError(f"mounted Applications link missing: {applications_link}")
        applications_link_present = True
        command_results["verify_mounted_app_codesign"] = _run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(mounted_app)]
        )
        install_root.mkdir(parents=True, exist_ok=True)
        command_results["copy_to_temporary_applications"] = _run(
            ["ditto", str(mounted_app), str(installed_app)]
        )
        command_results["verify_installed_app_codesign"] = _run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(installed_app)]
        )
        detach = _run(build_detach_command(mount_dir), check=False)
        command_results["hdiutil_detach"] = detach
        if detach["returncode"] != 0 and mount_dir.exists():
            command_results["hdiutil_detach_force"] = _run(
                build_detach_command(mount_dir, force=True),
                check=False,
            )
        mount_detached = not mount_dir.exists()

        smoke_home = run_root / "home"
        smoke_home.mkdir()
        environment = sanitized_environment(smoke_home, token)
        app_process = subprocess.Popen(
            packaged_app_launch_command(
                installed_app / "Contents/MacOS/meeting-copilot-desktop"
            ),
            cwd=installed_app.parent,
            stdin=subprocess.DEVNULL,
            stdout=app_log,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        probe = _probe_local_backend_workbench(
            app_path=installed_app,
            app_process=app_process,
            token=token,
            timeout_seconds=startup_timeout_seconds,
        )
        backend = probe.get("backend")
        funasr_process = probe.get("funasr_process")
    except Exception as exc:  # evidence is still written for a failed smoke
        cleanup_errors.append(f"smoke_error: {type(exc).__name__}: {exc}")
    finally:
        app_exited = _stop_process(app_process, timeout_seconds=cleanup_timeout_seconds)
        if backend is not None:
            deadline = time.monotonic() + cleanup_timeout_seconds
            while time.monotonic() < deadline:
                backend_exited = not pid_exists(int(backend["pid"]))
                port_closed = not port_is_listening(int(backend["port"]))
                funasr_exited = bool(
                    funasr_process is not None
                    and not pid_exists(int(funasr_process["pid"]))
                )
                if backend_exited and port_closed and funasr_exited:
                    break
                time.sleep(0.1)
            if not backend_exited and pid_exists(int(backend["pid"])):
                try:
                    os.kill(int(backend["pid"]), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                backend_exited = not pid_exists(int(backend["pid"]))
        if funasr_process is not None and pid_exists(int(funasr_process["pid"])):
            funasr_forced_cleanup = True
            cleanup_errors.append("funasr_worker_survived_app_exit")
            os.kill(int(funasr_process["pid"]), signal.SIGTERM)
            deadline = time.monotonic() + 3
            while pid_exists(int(funasr_process["pid"])) and time.monotonic() < deadline:
                time.sleep(0.05)
            if pid_exists(int(funasr_process["pid"])):
                os.kill(int(funasr_process["pid"]), signal.SIGKILL)
            funasr_exited = not pid_exists(int(funasr_process["pid"]))
        if mount_dir is not None and mount_dir.exists():
            detach = _run(build_detach_command(mount_dir, force=True), check=False)
            command_results["hdiutil_detach_cleanup"] = detach
            mount_detached = not mount_dir.exists()
        app_log.close()
        try:
            shutil.rmtree(install_root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_errors.append(f"temporary_install_cleanup: {exc}")
        try:
            shutil.rmtree(run_root / "dmg-source")
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_errors.append(f"dmg_source_cleanup: {exc}")
        for temporary_dir in (run_root / "home", logs_dir):
            try:
                shutil.rmtree(temporary_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_errors.append(f"temporary_runtime_cleanup: {exc}")

    installed_removed = not installed_app.exists()
    backend = probe.get("backend") if probe else backend
    responses = probe.get("responses", {})
    passed = smoke_passed(
        cleanup_errors=cleanup_errors,
        resolved_dmg=resolved_dmg,
        command_results=command_results,
        applications_link_present=applications_link_present,
        mount_detached=mount_detached,
        installed_removed=installed_removed,
        backend=backend,
        responses=responses,
        probe=probe,
        app_exited=app_exited,
        backend_exited=backend_exited,
        port_closed=port_closed,
        funasr_exited=funasr_exited,
        funasr_forced_cleanup=funasr_forced_cleanup,
    )
    evidence = {
        "schema_version": "meeting_copilot.macos_dmg_install_smoke.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "go_local_macos_dmg_install_smoke" if passed else "no_go_local_macos_dmg_install_smoke",
        "dmg_mode": mode,
        "source_app": _display_path(app_path, repo_root),
        "dmg_path": _display_path(resolved_dmg, repo_root) if resolved_dmg else None,
        "dmg_sha256": _sha256(resolved_dmg) if resolved_dmg and resolved_dmg.is_file() else None,
        "dmg_size_bytes": resolved_dmg.stat().st_size if resolved_dmg and resolved_dmg.is_file() else None,
        "temporary_install_path": _display_path(installed_app, repo_root),
        "temporary_install_removed": installed_removed,
        "mounted_app": _display_path(mounted_app, repo_root) if mounted_app else None,
        "mount_dir": str(mount_dir) if mount_dir else None,
        "backend_pid": backend.get("pid") if backend else None,
        "backend_port": backend.get("port") if backend else None,
        "responses": responses,
        "checks": {
            "source_app_codesign_deep_strict": command_results.get("verify_source_app_codesign", {}).get("returncode") == 0,
            "mounted_app_codesign_deep_strict": command_results.get("verify_mounted_app_codesign", {}).get("returncode") == 0,
            "temporary_app_codesign_deep_strict": command_results.get("verify_installed_app_codesign", {}).get("returncode") == 0,
            "mounted_app_present": mounted_app is not None,
            "applications_link_present": applications_link_present,
            "backend_health_identity_verified": probe.get("health_identity_verified") is True,
            "bootstrap_authenticated": probe.get("bootstrap_authenticated") is True,
            "resident_ready": probe.get("resident_ready") is True,
            "local_workbench_responded": responses.get("workbench") == 200,
            "workbench_loopback_only": probe.get("workbench_loopback") is True,
            "app_exited_after_sigterm": app_exited,
            "backend_exited_after_parent": backend_exited,
            "backend_port_closed": port_closed,
            "funasr_worker_exited_after_parent": funasr_exited,
            "funasr_worker_forced_cleanup": funasr_forced_cleanup,
            "mount_detached": mount_detached,
            "temporary_install_cleaned": installed_removed,
        },
        "cleanup_errors": cleanup_errors,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "privacy_cost_flags": {
            "sudo_used": False,
            "keychain_accessed": False,
            "password_or_admin_prompt_requested": False,
            "remote_service_called": False,
            "remote_asr_called": False,
            "remote_llm_called": False,
            "microphone_started": False,
            "screen_capture_started": False,
        },
        "commands": command_results,
        "decision": {
            "counts_as_local_install_smoke_evidence": passed,
            "counts_as_public_release_package": False,
        },
    }
    evidence_path = run_root / "evidence.json"
    _write_json(evidence_path, evidence)
    return evidence | {"evidence_path": _display_path(evidence_path, repo_root)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--run-id",
        default="macos-dmg-install-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    parser.add_argument("--dmg-name", default=DEFAULT_DMG_NAME)
    parser.add_argument("--volume-name", default=DEFAULT_VOLUME_NAME)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--cleanup-timeout", type=float, default=15.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence = run_smoke(
        app_path=args.app,
        dmg_path=args.dmg,
        output_root=args.output_root,
        run_id=args.run_id,
        dmg_name=args.dmg_name,
        volume_name=args.volume_name,
        startup_timeout_seconds=args.startup_timeout,
        cleanup_timeout_seconds=args.cleanup_timeout,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "dmg_mode": evidence["dmg_mode"],
                "dmg_path": evidence["dmg_path"],
                "evidence_path": evidence["evidence_path"],
                "temporary_install_removed": evidence["temporary_install_removed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if evidence["decision"]["counts_as_local_install_smoke_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
