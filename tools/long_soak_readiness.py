"""Read isolated startup readiness metadata without exposing private configuration."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
import time

try:
    from .pi_stage0_production_replay import JsonHttpClient
    from .workbench_server import status_report
except ImportError:
    from pi_stage0_production_replay import JsonHttpClient
    from workbench_server import status_report


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parent = root / "artifacts/realtime-participation-20261007"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=parent / "long-soak-r02")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if output.parent != parent.resolve() or not re.fullmatch(r"long-soak-r[0-9]{2}", output.name):
        raise ValueError("requires an explicit isolated long-soak-rNN directory")
    if (output / "readiness.json").exists():
        raise ValueError("refusing to replace preserved startup readiness")
    identity = status_report(port=8769, pid_file=output / "server.pid")
    client = JsonHttpClient("http://127.0.0.1:8769", timeout_seconds=10)
    runtime = client.request("GET", "/providers/asr/runtime").payload
    config = client.request("GET", "/providers/config").payload
    settings = client.request("GET", "/settings").payload
    boundary = JsonHttpClient("http://127.0.0.1:8790").request("GET", "/status").payload
    with sqlite3.connect((output / "runtime/meeting_copilot.db").resolve().as_uri() + "?mode=ro", uri=True) as connection:
        meeting_count = connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        ledger_count = connection.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0]
    automatic_enabled = None
    try:
        import psutil
        # Never serialize the process environment or any credential values.
        flag = psutil.Process(identity["pid"]).environ().get("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
        automatic_enabled = flag.lower() in {"1", "true", "on", "yes"}
    except (ImportError, OSError, KeyError):
        pass
    refinement = runtime.get("offline_refinement") or {}
    capability = refinement.get("capability") or {}
    worker = refinement.get("worker") or {}
    conditions = {
        "managed_current_source_assets_schema_pid_identity": identity.get("runtime_identity_verified") is True,
        "empty_isolated_meetings_and_ledger": meeting_count == 0 and ledger_count == 0,
        "private_base_routes_only_to_existing_proxy": config.get("base_url") in {"http://127.0.0.1:8790", "http://127.0.0.1:8790/v1"},
        "same_user_model_all_lanes": all(config.get(key) == "gpt-6-sol" for key in ("model", "realtime_model", "correction_model")),
        "responses_with_private_credentials_present": config.get("api_style") == "responses" and config.get("api_key_present") is True,
        "existing_proxy_still_exhausted": boundary.get("reserved_forward_attempts") == 12 and boundary.get("maximum_forward_attempts") == 12,
        "resident_ready": (runtime.get("resident") or {}).get("process_ready") is True,
        "refiner_guarded_prewarm_ready_or_healthy_idle": (capability.get("realtime_policy") or {}).get("mode") == "prewarm"
            and capability.get("status") == "ready" and (worker.get("process_ready") is True
                or not worker.get("spawned") and worker.get("last_exit_code") == 0 and not worker.get("requires_recovery")),
        "correction_enabled": (settings.get("asr") or {}).get("l2_correction_enabled") is True,
    }
    production_files = ["code/asr_runtime/scripts/funasr_stream_worker.py",
                        "code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py",
                        "code/web_mvp/backend/meeting_copilot_web_mvp/app.py",
                        "code/web_mvp/backend/meeting_copilot_web_mvp/v2_persistence.py"]
    fingerprints = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in production_files}
    result = {"captured_at_ms": time.time_ns() // 1_000_000, "service_pid": identity.get("pid"),
              "conditions": conditions, "ready_for_root_audio_signal": all(conditions.values()),
              "meeting_count": meeting_count, "usage_ledger_count": ledger_count,
              "proxy_forward_attempts": boundary.get("reserved_forward_attempts"),
              "proxy_budget_rejections_at_start": boundary.get("budget_rejections"),
              "resident_pid": (runtime.get("resident") or {}).get("pid"), "refiner_pid": worker.get("pid"),
              "refiner_ready_at_observation": worker.get("process_ready") is True,
              "healthy_idle_formal_prewarm_required_before_audio": worker.get("process_ready") is not True,
              "automatic_discussion_environment_direct_read": automatic_enabled,
              "automatic_flag_provenance": "parent environment explicitly set to1; managed build_env preserves inherited value; final durable state-job creation must confirm runtime execution",
              "production_source_sha256_at_start": fingerprints,
              "source_identity_scope": "startup versions only; later state-validator-only edits must not be claimed loaded by this running process",
              "actual_audio_started": False, "credentials_or_upstream_address_archived": False,
              "managed_identity": identity.get("health", {}).get("runtime_identity")}
    (output / "readiness.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
