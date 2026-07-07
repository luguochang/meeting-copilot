#!/usr/bin/env python3
"""Run repeatable local quality gates for Meeting Copilot."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]


class GateStep(NamedTuple):
    step_id: str
    cwd_relative: str
    command: list[str]


def build_steps(profile: str, *, include_browser: bool) -> list[GateStep]:
    if profile not in {"pc-web", "all-local"}:
        raise ValueError(f"unknown quality gate profile: {profile}")

    steps: list[GateStep] = []
    if profile == "all-local":
        steps.extend(
            [
                GateStep("asr-runtime-pytest", "code/asr_runtime", ["python3", "-m", "pytest", "-q"]),
                GateStep(
                    "asr-bakeoff-pytest",
                    "code/asr_bakeoff",
                    ["python3", "-m", "pytest", "tests", "-q"],
                ),
            ]
        )

    steps.extend(
        [
            GateStep("root-pytest", ".", ["python3", "-m", "pytest", "tests", "-q"]),
            GateStep("core-pytest", "code/core", ["python3", "-m", "pytest", "-q"]),
            GateStep("web-backend-pytest", "code/web_mvp/backend", ["python3", "-m", "pytest", "-q"]),
        ]
    )
    if include_browser:
        steps.append(GateStep("web-browser-smoke", "code/web_mvp", ["node", "e2e/browser_smoke.mjs"]))
    return steps


def run_step(step: GateStep) -> int:
    completed = subprocess.run(step.command, cwd=REPO_ROOT / step.cwd_relative, check=False)
    return completed.returncode


def run_gate(
    *,
    profile: str,
    dry_run: bool,
    include_browser: bool,
    runner: Callable[[GateStep], int] = run_step,
    out: TextIO = sys.stdout,
) -> int:
    steps = build_steps(profile, include_browser=include_browser)
    mode = "dry-run" if dry_run else "run"
    print(f"[{mode}] Meeting Copilot quality gate profile={profile}", file=out, flush=True)

    for index, step in enumerate(steps, start=1):
        command_text = " ".join(step.command)
        print(
            f"[{mode}] {index}/{len(steps)} {step.step_id}: {step.cwd_relative} $ {command_text}",
            file=out,
            flush=True,
        )
        if dry_run:
            continue

        try:
            exit_code = runner(step)
        except FileNotFoundError as exc:
            executable = exc.filename or step.command[0]
            print(
                f"[fail] {step.step_id} could not start missing executable: {executable}",
                file=out,
                flush=True,
            )
            return 127

        if exit_code != 0:
            print(f"[fail] {step.step_id} failed with exit code {exit_code}", file=out, flush=True)
            return exit_code
        print(f"[pass] {step.step_id}", file=out, flush=True)

    print(f"[pass] quality gate profile={profile}", file=out, flush=True)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["pc-web", "all-local"],
        default="pc-web",
        help="pc-web runs the current PC Web MVP gate; all-local also runs local ASR tool tests.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument("--no-browser", action="store_true", help="Skip the Chrome/CDP browser smoke.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_gate(
        profile=args.profile,
        dry_run=args.dry_run,
        include_browser=not args.no_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
