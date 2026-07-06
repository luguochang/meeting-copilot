from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from asr_bakeoff.bakeoff import run_bakeoff
from asr_bakeoff.providers.command import CommandProvider
from asr_bakeoff.providers.mock import MockProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Chinese technical meeting ASR bake-off.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--provider", default="mock", choices=["mock", "command"])
    parser.add_argument("--mock-transcripts", type=Path)
    parser.add_argument("--provider-name", default="command")
    parser.add_argument("--command")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.provider == "mock":
        transcripts = {}
        if args.mock_transcripts:
            transcripts = json.loads(args.mock_transcripts.read_text(encoding="utf-8"))
        provider = MockProvider(transcripts)
    else:
        if not args.command:
            parser.error("--command is required when --provider command")
        provider = CommandProvider(
            name=args.provider_name,
            command=shlex.split(args.command),
            timeout_seconds=args.timeout_seconds,
        )

    report = run_bakeoff(args.manifest, provider, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
