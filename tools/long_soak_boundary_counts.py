"""Count only fixed structured ASR events in an isolated soak's server log."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


EVENTS = frozenset({
    "asr.stream.funasr_boundary_timeout",
    "asr.stream.funasr_boundary_failed",
    "asr.stream.funasr_boundary_recovered",
    "asr.stream.aborted",
})


def boundary_counts(log_text: str, *, meeting_id: str) -> dict:
    """Ignore malformed/unrelated records and never return their raw contents."""
    counts = {event: 0 for event in sorted(EVENTS)}
    normal_timeouts = 0
    recovery_timeouts = 0
    timeout_boundary_ids: set[int | str] = set()
    recovered_boundary_ids: set[int | str] = set()
    matching_malformed_records = 0
    matching_unscoped_records = 0
    meeting_hash = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()[:16]
    decoder = json.JSONDecoder()
    for line in log_text.splitlines():
        if not any(event in line for event in EVENTS):
            continue
        start = line.find("{")
        if start < 0:
            matching_malformed_records += 1
            continue
        try:
            record, _ = decoder.raw_decode(line[start:])
        except (ValueError, TypeError):
            matching_malformed_records += 1
            continue
        if not isinstance(record, dict):
            continue
        event = record.get("event")
        if event not in EVENTS:
            continue
        if record.get("session_id") != meeting_id and record.get("meeting_id_hash") != meeting_hash:
            if "session_id" not in record and "meeting_id_hash" not in record:
                matching_unscoped_records += 1
            continue
        counts[event] += 1
        boundary_id = record.get("boundary_id")
        valid_boundary = (
            isinstance(boundary_id, int) and not isinstance(boundary_id, bool) and boundary_id >= 0
        ) or (isinstance(boundary_id, str) and 0 < len(boundary_id) <= 160)
        if event == "asr.stream.funasr_boundary_timeout":
            if record.get("recovering") is True:
                recovery_timeouts += 1
            else:
                normal_timeouts += 1
            if valid_boundary:
                timeout_boundary_ids.add(boundary_id)
        elif event == "asr.stream.funasr_boundary_recovered" and valid_boundary:
            recovered_boundary_ids.add(boundary_id)
    return {
        "scope": "fixed_structured_events_only",
        "ordinary_ack_timeout_attempts": normal_timeouts,
        "recovery_ack_timeout_attempts": recovery_timeouts,
        "ack_timeout_distinct_boundary_count": len(timeout_boundary_ids),
        "observed_explicit_recovery_success_event_count": counts["asr.stream.funasr_boundary_recovered"],
        "observed_explicitly_recovered_boundary_token_count": len(recovered_boundary_ids),
        "total_successful_timeout_resolution_count": None,
        "ordinary_timeout_late_ack_resolution_count": None,
        "timeout_resolution_caveat": (
            "Only the explicit recovery path emits success events. An ACK arriving after an ordinary timeout "
            "may resume the stream without this event; total successful resolutions are unknown from these logs."
        ),
        "boundary_failed_event_count": counts["asr.stream.funasr_boundary_failed"],
        "stream_terminated_event_count": counts["asr.stream.aborted"],
        "fixed_event_counts": counts,
        "matching_malformed_record_count": matching_malformed_records,
        "matching_unscoped_record_count": matching_unscoped_records,
        "recorded_text_or_raw_logs_saved": False,
        "zero_counts_mean": "No matching fixed events in the observed log; not a proof about unlogged worker behavior.",
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parent = root / "artifacts/realtime-participation-20261007"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != parent.resolve() or not re.fullmatch(r"long-soak-r[0-9]{2}", output.name):
        raise ValueError("requires an explicit isolated long-soak-rNN directory")
    if not re.fullmatch(r"long_audio_soak_[0-9]{8}_r[0-9]{2}", args.meeting_id):
        raise ValueError("requires an explicit synthetic long-soak meeting id")
    path = output / "server.log"
    result = boundary_counts(path.read_text(encoding="utf-8", errors="replace"), meeting_id=args.meeting_id)
    result["meeting_id"] = args.meeting_id
    result["observed_log_bytes"] = path.stat().st_size
    if args.save:
        (output / "asr-boundary-counts.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
