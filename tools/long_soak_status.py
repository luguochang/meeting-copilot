"""Read counts only from the active isolated soak; no model or device calls."""
from pathlib import Path
import argparse
import json
import re
import sqlite3
import time

from pi_stage0_production_replay import JsonHttpClient


def main():
    root = Path(__file__).resolve().parents[1]
    parent = root / "artifacts/realtime-participation-20261007"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=parent / "long-soak-r01")
    parser.add_argument("--meeting-id", default="long_audio_soak_20261008_r01")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != parent.resolve() or not re.fullmatch(r"long-soak-r[0-9]{2}", output.name):
        raise ValueError("status requires an explicit isolated long-soak-rNN directory")
    path = output / "progress.json"
    progress = json.loads(path.read_text()) if path.exists() else {}
    boundary = JsonHttpClient("http://127.0.0.1:8790").request("GET", "/status").payload
    with sqlite3.connect((output / "runtime/meeting_copilot.db").resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        meeting_id = args.meeting_id
        count = connection.execute("SELECT COUNT(*) FROM transcript_segments WHERE meeting_id=?", (meeting_id,)).fetchone()[0]
        jobs = [dict(x) for x in connection.execute(
            "SELECT kind,status,COUNT(*) AS n FROM jobs WHERE meeting_id=? GROUP BY kind,status", (meeting_id,))]
        usage = [dict(x) for x in connection.execute(
            "SELECT purpose,COUNT(*) AS n,SUM(total_tokens) AS tokens FROM llm_usage_ledger WHERE session_id=? GROUP BY purpose", (meeting_id,))]
    print(json.dumps({"at_ms": time.time_ns() // 1_000_000,
                      "latest": (progress.get("samples") or [None])[-1], "durable_segments_now": count,
                      "jobs_now": jobs, "usage_now": usage,
                      "actual_forward_attempts": boundary.get("reserved_forward_attempts"),
                      "budget_rejections": boundary.get("budget_rejections"),
                      "summary_present": (output / "summary.json").exists(),
                      "stderr_bytes": next((path.stat().st_size for path in (
                          output / "replay-stderr.log", output / "replay-r02-stderr.log") if path.exists()), 0)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
