#!/usr/bin/env python3
"""Generate or preview local synthetic meeting audio for all approved scripts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from synthetic_audio_local_tts_smoke import (  # noqa: E402
    _load_scripts,
    build_synthetic_audio_local_tts_smoke_report,
)


REPORT_VERSION = "synthetic_audio_batch_smoke.v1"


def _script_ids() -> list[str]:
    return sorted(_load_scripts())


def _batch_status(items: list[dict[str, object]], execute_local_tts: bool) -> str:
    statuses = {str(item.get("smoke_status")) for item in items}
    if "blocked" in statuses or "failed" in statuses:
        return "blocked" if "blocked" in statuses else "failed"
    if execute_local_tts and statuses == {"ready_for_local_tts_execution"}:
        return "ready_for_local_tts_execution"
    if statuses == {"generated"}:
        return "generated"
    return "ready_for_explicit_local_tts_execution"


def build_synthetic_audio_batch_smoke_report(
    *,
    target_root: str,
    execute_local_tts: bool,
    run_commands: bool = True,
) -> dict[str, object]:
    items = [
        build_synthetic_audio_local_tts_smoke_report(
            script_id=script_id,
            target_root=target_root,
            execute_local_tts=execute_local_tts,
            run_commands=run_commands,
        )
        for script_id in _script_ids()
    ]
    validation_errors = sorted(
        {
            str(error)
            for item in items
            for error in item.get("validation_errors", [])
        }
    )
    execution_errors = [
        {
            "script_id": item.get("script_id"),
            "execution_errors": item.get("execution_errors", []),
        }
        for item in items
        if item.get("execution_errors")
    ]
    batch_status = _batch_status(items, execute_local_tts)
    safe_to_execute = execute_local_tts and batch_status in {
        "ready_for_local_tts_execution",
        "generated",
    }

    return {
        "report_mode": "synthetic_audio_batch_smoke",
        "report_version": REPORT_VERSION,
        "batch_status": batch_status,
        "script_count": len(items),
        "script_ids": [str(item.get("script_id")) for item in items],
        "target_root": target_root,
        "items": items,
        "safe_to_execute_local_tts_now": safe_to_execute,
        "safe_to_call_remote_tts": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_commit_generated_audio": False,
        "artifact_policy": "ignored_artifacts_tmp_only",
        "validation_errors": validation_errors,
        "execution_errors": execution_errors,
        "next_action": "fix_validation_errors"
        if validation_errors
        else "run_local_asr_event_plan"
        if batch_status == "generated"
        else "execute_local_tts_or_run_local_asr_event_plan"
        if batch_status == "ready_for_local_tts_execution"
        else "explicit_local_tts_execution",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root", default="artifacts/tmp/synthetic_audio")
    parser.add_argument("--execute-local-tts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_synthetic_audio_batch_smoke_report(
        target_root=args.target_root,
        execute_local_tts=args.execute_local_tts,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["batch_status"] in {"blocked", "failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
