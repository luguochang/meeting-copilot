#!/usr/bin/env python3
"""Generate or preview local synthetic meeting audio with macOS say."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "data" / "asr_eval" / "synthetic_meetings" / "scripts"

REPORT_VERSION = "synthetic_audio_local_tts_smoke.v1"
ALLOWED_TARGET_ROOTS = {"artifacts/tmp/synthetic_audio"}
FORBIDDEN_TARGET_ROOTS = {
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
}


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _load_scripts() -> dict[str, tuple[Path, dict[str, object]]]:
    scripts: dict[str, tuple[Path, dict[str, object]]] = {}
    for path in sorted(SCRIPT_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        script_id = payload.get("script_id")
        if isinstance(script_id, str):
            scripts[script_id] = (path, payload)
    return scripts


def _relative_to_repo(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _script_text(script: dict[str, object]) -> str:
    turns = script.get("turns")
    if not isinstance(turns, list):
        return ""
    lines = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        speaker = turn.get("speaker")
        text = turn.get("text")
        if isinstance(speaker, str) and isinstance(text, str):
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _run_local_tts(
    *,
    text: str,
    aiff_output_path: str,
    wav_output_path: str,
) -> tuple[str, list[str], dict[str, int | None]]:
    errors: list[str] = []
    artifact_sizes: dict[str, int | None] = {
        "aiff_bytes": None,
        "wav_bytes": None,
    }
    if shutil.which("say") is None:
        return "failed", ["macos say command is not available"], artifact_sizes

    aiff_path = REPO_ROOT / aiff_output_path
    wav_path = REPO_ROOT / wav_output_path
    aiff_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["say", "-o", str(aiff_path), text],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    artifact_sizes["aiff_bytes"] = aiff_path.stat().st_size if aiff_path.exists() else None

    if shutil.which("afconvert") is None:
        return "generated_aiff_only", errors, artifact_sizes

    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", str(aiff_path), str(wav_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    artifact_sizes["wav_bytes"] = wav_path.stat().st_size if wav_path.exists() else None
    return "generated_wav", errors, artifact_sizes


def build_synthetic_audio_local_tts_smoke_report(
    *,
    script_id: str,
    target_root: str,
    execute_local_tts: bool,
    run_commands: bool = True,
) -> dict[str, object]:
    scripts = _load_scripts()
    errors: list[str] = []
    script_tuple = scripts.get(script_id)
    if script_tuple is None:
        errors.append("script_id is not approved")
        script_file = None
        script = None
        turn_count = 0
        text_character_count = 0
    else:
        script_path, script = script_tuple
        script_file = _relative_to_repo(script_path)
        turn_count = len(script.get("turns", [])) if isinstance(script.get("turns"), list) else 0
        text_character_count = len(_script_text(script))

    if not _is_under_any_root(target_root, ALLOWED_TARGET_ROOTS):
        errors.append("target_root is not allowed")
    if _is_under_any_root(target_root, FORBIDDEN_TARGET_ROOTS):
        errors.append("target_root is forbidden")

    aiff_output_path = f"{target_root}/{script_id}.aiff"
    wav_output_path = f"{target_root}/{script_id}.wav"
    blocked = bool(errors)
    execution_errors: list[str] = []
    artifact_sizes: dict[str, int | None] = {"aiff_bytes": None, "wav_bytes": None}

    if blocked:
        smoke_status = "blocked"
        generation_status = "not_started"
        safe_to_execute_local_tts_now = False
    elif not execute_local_tts:
        smoke_status = "ready_for_explicit_local_tts_execution"
        generation_status = "not_started"
        safe_to_execute_local_tts_now = False
    elif not run_commands:
        smoke_status = "ready_for_local_tts_execution"
        generation_status = "execution_skipped_by_test_harness"
        safe_to_execute_local_tts_now = True
    else:
        safe_to_execute_local_tts_now = True
        assert script is not None
        try:
            generation_status, execution_errors, artifact_sizes = _run_local_tts(
                text=_script_text(script),
                aiff_output_path=aiff_output_path,
                wav_output_path=wav_output_path,
            )
            smoke_status = "generated" if generation_status.startswith("generated") else "failed"
        except subprocess.CalledProcessError as exc:
            generation_status = "failed"
            smoke_status = "failed"
            execution_errors = [f"local tts command failed with exit code {exc.returncode}"]

    return {
        "report_mode": "synthetic_audio_local_tts_smoke",
        "report_version": REPORT_VERSION,
        "smoke_status": smoke_status,
        "generation_status": generation_status,
        "script_id": script_id,
        "script_file": script_file,
        "target_root": target_root,
        "aiff_output_path": aiff_output_path,
        "wav_output_path": wav_output_path,
        "turn_count": turn_count,
        "text_character_count": text_character_count,
        "tts_engine": "macos_say",
        "tts_command_preview": ["say", "-o", aiff_output_path, "<synthetic_script_text>"],
        "wav_conversion_command_preview": [
            "afconvert",
            "-f",
            "WAVE",
            "-d",
            "LEI16@16000",
            aiff_output_path,
            wav_output_path,
        ],
        "safe_to_execute_local_tts_now": safe_to_execute_local_tts_now,
        "safe_to_call_remote_tts": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_generated_audio": False,
        "artifact_policy": "ignored_artifacts_tmp_only",
        "validation_errors": errors,
        "execution_errors": execution_errors,
        "artifact_sizes": artifact_sizes,
        "next_action": "fix_validation_errors"
        if blocked
        else "run_local_asr_event_plan"
        if generation_status == "generated_wav"
        else "explicit_local_tts_execution",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script-id", required=True)
    parser.add_argument("--target-root", default="artifacts/tmp/synthetic_audio")
    parser.add_argument("--execute-local-tts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_synthetic_audio_local_tts_smoke_report(
        script_id=args.script_id,
        target_root=args.target_root,
        execute_local_tts=args.execute_local_tts,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["smoke_status"] in {"blocked", "failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
