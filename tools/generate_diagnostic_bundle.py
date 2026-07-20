#!/usr/bin/env python3
"""Generate one deterministic, redacted Meeting Copilot diagnostic bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
MAX_INPUT_BYTES = 5 * 1024 * 1024

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from meeting_copilot_web_mvp.diagnostic_bundle import create_diagnostic_bundle  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Runtime diagnostic snapshot JSON")
    parser.add_argument("--output", type=Path, required=True, help="Output .zip path")
    return parser.parse_args(argv)


def load_snapshot(path: Path) -> dict[str, Any]:
    if path.suffix.casefold() != ".json":
        raise ValueError("input must be a .json file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"input JSON exceeds {MAX_INPUT_BYTES} bytes")
    with path.open("r", encoding="utf-8") as handle:
        snapshot = json.load(handle)
    if not isinstance(snapshot, dict):
        raise ValueError("input JSON must contain an object")
    return snapshot


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = create_diagnostic_bundle(load_snapshot(args.input), args.output)
    json.dump(result, out, ensure_ascii=True, indent=2, sort_keys=True)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
