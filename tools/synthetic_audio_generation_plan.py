#!/usr/bin/env python3
"""Build a local-only synthetic audio generation plan without generating audio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "data" / "asr_eval" / "synthetic_meetings" / "scripts"
PLAN_VERSION = "synthetic_audio_generation_plan.v1"

ALLOWED_LOCAL_TTS_ENGINES = {"macos_say", "offline_tts_placeholder"}
ALLOWED_TARGET_ROOTS = {"artifacts/tmp/synthetic_audio", "data/asr_eval/public_raw"}
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


def _approved_script_ids() -> set[str]:
    script_ids: set[str] = set()
    for path in sorted(SCRIPT_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        script_id = payload.get("script_id")
        if isinstance(script_id, str):
            script_ids.add(script_id)
    return script_ids


def build_synthetic_audio_generation_plan(
    *,
    script_id: str,
    tts_engine: str,
    target_root: str,
    max_duration_seconds: int,
) -> dict[str, object]:
    errors: list[str] = []
    approved_script_ids = _approved_script_ids()
    if script_id not in approved_script_ids:
        errors.append("script_id is not approved")
    if tts_engine not in ALLOWED_LOCAL_TTS_ENGINES:
        errors.append("tts_engine is not an approved local engine")
    if not _is_under_any_root(target_root, ALLOWED_TARGET_ROOTS):
        errors.append("target_root is not allowed")
    if _is_under_any_root(target_root, FORBIDDEN_TARGET_ROOTS):
        errors.append("target_root is forbidden")
    if not 1 <= max_duration_seconds <= 1800:
        errors.append("max_duration_seconds must be between 1 and 1800")

    blocked = bool(errors)
    return {
        "plan_mode": "synthetic_audio_generation_plan_only",
        "plan_version": PLAN_VERSION,
        "plan_status": "blocked" if blocked else "ready_for_manual_generation_review",
        "generation_status": "not_started",
        "safe_to_generate_audio_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_tts": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_generated_audio": False,
        "script_id": script_id,
        "approved_script_ids": sorted(approved_script_ids),
        "tts_engine": tts_engine,
        "allowed_local_tts_engines": sorted(ALLOWED_LOCAL_TTS_ENGINES),
        "target_root": target_root,
        "allowed_target_roots": sorted(ALLOWED_TARGET_ROOTS),
        "forbidden_target_roots": sorted(FORBIDDEN_TARGET_ROOTS),
        "max_duration_seconds": max_duration_seconds,
        "audio_artifact_policy": "ignored_local_artifact_only",
        "cleanup_policy": "delete or keep ignored local only after validation",
        "validation_errors": errors,
        "next_action": "fix_validation_errors" if blocked else "manual_local_tts_smoke",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script-id", required=True)
    parser.add_argument("--tts-engine", default="macos_say")
    parser.add_argument("--target-root", default="artifacts/tmp/synthetic_audio")
    parser.add_argument("--max-duration-seconds", type=int, default=240)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_synthetic_audio_generation_plan(
        script_id=args.script_id,
        tts_engine=args.tts_engine,
        target_root=args.target_root,
        max_duration_seconds=args.max_duration_seconds,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["plan_status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
