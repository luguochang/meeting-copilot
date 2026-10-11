"""Save content-free known ASR boundary diagnostics for the failed isolated soak."""
from pathlib import Path
import json
import sqlite3

from pi_stage0_production_replay import JsonHttpClient
from long_audio_soak import formal_source_counts


def main():
    output = Path(__file__).resolve().parents[1] / "artifacts/realtime-participation-20261007/long-soak-r01"
    fields = {"event", "timestamp", "boundary_id", "elapsed_ms", "recovering", "error_class", "error_origin"}
    labels = {"asr.stream.funasr_boundary_timeout", "asr.stream.aborted", "asr.stream.funasr_boundary_recovered"}
    entries = []
    decoder = json.JSONDecoder()
    for line in (output / "server.log").read_text(errors="replace").splitlines():
        if not any(label in line for label in labels):
            continue
        start = line.find("{")
        try:
            value, _ = decoder.raw_decode(line[start:])
            entries.append({key: value[key] for key in fields if key in value})
        except (ValueError, TypeError):
            entries.append({"json_parse_failed": True, "brace_present": start >= 0})
    with sqlite3.connect((output / "runtime/meeting_copilot.db").resolve().as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT record_json FROM asr_live_sessions WHERE session_id=?",
                                 ("long_audio_soak_20261008_r01",)).fetchone()
    mirror = json.loads(row[0]) if row else {}
    runtime = JsonHttpClient("http://127.0.0.1:8769").request("GET", "/providers/asr/runtime").payload
    safe = {"scope": "read_only_failed_run_diagnostics", "known_asr_log_metadata": entries,
            "mirror_degradation_reasons": mirror.get("degradation_reasons"),
            "mirror_event_type_counts": {}, "recorded_text_saved": False,
            "readonly_followup_formal_final_source_counts": formal_source_counts(mirror.get("events") or []),
            "resident": {key: value for key, value in (runtime.get("resident") or {}).items()
                         if key in {"process_running", "process_ready", "pid", "active_session_id", "last_error"}},
            "refiner": {key: value for key, value in ((runtime.get("offline_refinement") or {}).get("worker") or {}).items()
                        if key in {"process_running", "process_ready", "pid", "requires_recovery"}}}
    for event in mirror.get("events") or []:
        kind = event.get("event_type") or "unknown"
        safe["mirror_event_type_counts"][kind] = safe["mirror_event_type_counts"].get(kind, 0) + 1
    (output / "failure-diagnosis.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(safe, ensure_ascii=True))


if __name__ == "__main__":
    main()
