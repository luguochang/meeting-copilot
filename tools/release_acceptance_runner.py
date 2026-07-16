#!/usr/bin/env python3
"""Run the Meeting Copilot release acceptance gate.

This runner is intentionally a coordinator: it reuses
``mainline_evidence_bundle_runner`` for ASR/LLM/UI lane evidence instead of
duplicating business logic.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "release_acceptance"
DEFAULT_AUDIO_PATH = REPO_ROOT / "code" / "asr_runtime" / "outputs" / "simulated-release-review.16k.wav"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8765/health"
DEFAULT_WORKBENCH_URL = "http://127.0.0.1:8765/workbench"

for import_root in (TOOLS_ROOT, WEB_BACKEND_ROOT, CORE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import mainline_evidence_bundle_runner as mainline_runner  # noqa: E402


REQUIRED_CHECKS = [
    "pytest_backend_mainline",
    "workbench_smoke",
    "git_diff_check",
    "health_endpoint",
    "workbench_js_version",
]
REQUIRED_LANES = [
    "file_lane",
    "simulated_realtime",
    "browser_live_mic",
]
OPTIONAL_LANES = [
    "real_mic_recorded_realtime",
]


def run_release_acceptance(
    *,
    run_id: str | None = None,
    artifact_root: Path | None = None,
    data_dir: Path | None = None,
    quality_checks: list[dict[str, Any]] | None = None,
    lane_results: dict[str, dict[str, Any]] | None = None,
    file_audio: Path | None = None,
    simulated_audio: Path | None = None,
    real_mic_recorded_audio: Path | None = None,
    real_mic_health_report: Path | None = None,
    browser_live_mic_bundle: Path | None = None,
    health_url: str = DEFAULT_HEALTH_URL,
    workbench_url: str = DEFAULT_WORKBENCH_URL,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-release-acceptance")
    artifact_root = artifact_root or DEFAULT_ARTIFACT_ROOT / run_id
    data_dir = data_dir or artifact_root / "runtime_data"
    artifact_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    checks = quality_checks if quality_checks is not None else _run_default_quality_checks(
        artifact_root=artifact_root,
        health_url=health_url,
        workbench_url=workbench_url,
    )
    lanes = lane_results if lane_results is not None else _run_default_lanes(
        artifact_root=artifact_root,
        data_dir=data_dir,
        file_audio=file_audio or DEFAULT_AUDIO_PATH,
        simulated_audio=simulated_audio or DEFAULT_AUDIO_PATH,
        real_mic_recorded_audio=real_mic_recorded_audio,
        real_mic_health_report=real_mic_health_report,
        browser_live_mic_bundle=browser_live_mic_bundle,
    )

    summary = _build_summary(
        run_id=run_id,
        artifact_root=artifact_root,
        checks=checks,
        lane_results=lanes,
    )
    _write_json(artifact_root / "summary.json", summary)
    (artifact_root / "report.md").write_text(_render_report(summary), encoding="utf-8")
    return {"artifact_root": str(artifact_root), "summary": summary}


def _run_default_quality_checks(
    *,
    artifact_root: Path,
    health_url: str,
    workbench_url: str,
) -> list[dict[str, Any]]:
    return [
        _run_command_check(
            name="pytest_backend_mainline",
            command=[
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_mainline_evidence_bundle_runner.py",
                "code/web_mvp/backend/tests/test_app.py",
                "code/web_mvp/backend/tests/test_asr_stream.py",
                "code/web_mvp/backend/tests/test_workbench.py",
                "code/web_mvp/backend/tests/test_file_convert.py",
                "code/web_mvp/backend/tests/test_real_asr_to_cards.py",
                "code/web_mvp/backend/tests/test_approach_cards.py",
                "code/web_mvp/backend/tests/test_minutes.py",
                "code/web_mvp/backend/tests/test_llm_service.py",
                "code/web_mvp/backend/tests/test_real_llm_path.py",
                "code/web_mvp/backend/tests/test_metrics.py",
                "code/web_mvp/backend/tests/test_g3_g4_g5.py",
                "code/web_mvp/backend/tests/test_e2e_mainline.py",
                "code/web_mvp/backend/tests/test_shadow_trial.py",
            ],
            artifact_root=artifact_root,
            timeout_seconds=240,
        ),
        _run_command_check(
            name="workbench_smoke",
            command=["node", "code/web_mvp/e2e/workbench_smoke.mjs"],
            artifact_root=artifact_root,
            timeout_seconds=120,
        ),
        _run_command_check(
            name="git_diff_check",
            command=["git", "diff", "--check"],
            artifact_root=artifact_root,
            timeout_seconds=60,
        ),
        _run_http_json_check(name="health_endpoint", url=health_url),
        _run_workbench_js_version_check(workbench_url),
    ]


def _run_default_lanes(
    *,
    artifact_root: Path,
    data_dir: Path,
    file_audio: Path,
    simulated_audio: Path,
    real_mic_recorded_audio: Path | None,
    real_mic_health_report: Path | None,
    browser_live_mic_bundle: Path | None,
) -> dict[str, dict[str, Any]]:
    lane_root = artifact_root / "lanes"
    lanes: dict[str, dict[str, Any]] = {}
    lanes["file_lane"] = mainline_runner.run_file_lane_bundle(
        audio_path=file_audio,
        artifact_root=lane_root / "file-lane",
        data_dir=data_dir / "file-lane",
        run_id="release-file-lane",
        ui_verifier=mainline_runner.verify_workbench_same_session,
    )
    lanes["simulated_realtime"] = mainline_runner.run_simulated_realtime_lane_bundle(
        audio_path=simulated_audio,
        artifact_root=lane_root / "simulated-realtime",
        data_dir=data_dir / "simulated-realtime",
        run_id="release-simulated-realtime",
        ui_verifier=mainline_runner.verify_workbench_same_session,
    )
    lanes["real_mic_recorded_realtime"] = _run_real_mic_recorded_lane_or_blocked(
        audio_path=real_mic_recorded_audio,
        health_report_path=real_mic_health_report,
        artifact_root=lane_root / "real-mic-recorded-realtime",
        data_dir=data_dir / "real-mic-recorded-realtime",
    )
    lanes["browser_live_mic"] = _load_browser_live_mic_bundle_or_blocked(
        browser_live_mic_bundle or _find_latest_browser_live_mic_bundle(),
    )
    return lanes


def _run_real_mic_recorded_lane_or_blocked(
    *,
    audio_path: Path | None,
    health_report_path: Path | None,
    artifact_root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    if audio_path is None or health_report_path is None or not audio_path.exists() or not health_report_path.exists():
        manifest = _blocked_lane_manifest(
            audio_source="real_mic_recorded_wav",
            blocker="real_mic_recorded_inputs_missing",
        )
        artifact_root.mkdir(parents=True, exist_ok=True)
        _write_json(artifact_root / "manifest.json", manifest)
        return {"artifact_root": str(artifact_root), "manifest": manifest}
    health_report = json.loads(health_report_path.read_text(encoding="utf-8"))
    return mainline_runner.run_real_mic_recorded_realtime_lane_bundle(
        audio_path=audio_path,
        health_report=health_report,
        artifact_root=artifact_root,
        data_dir=data_dir,
        run_id="release-real-mic-recorded-realtime",
        ui_verifier=mainline_runner.verify_workbench_same_session,
    )


def _load_browser_live_mic_bundle_or_blocked(bundle_root: Path | None) -> dict[str, Any]:
    if bundle_root is None or not (bundle_root / "manifest.json").exists():
        return {
            "artifact_root": str(bundle_root or ""),
            "manifest": _blocked_lane_manifest(
                audio_source="browser_live_mic",
                blocker="blocked_browser_live_mic_not_proven",
                browser_live_mic_go_evidence=False,
                counts_as_real_mic_go_evidence=False,
            ),
        }
    manifest = json.loads((bundle_root / "manifest.json").read_text(encoding="utf-8"))
    return {"artifact_root": str(bundle_root), "manifest": manifest}


def _find_latest_browser_live_mic_bundle() -> Path | None:
    acceptance_root = REPO_ROOT / "artifacts" / "tmp" / "acceptance"
    if not acceptance_root.exists():
        return None
    candidates: list[Path] = []
    for manifest_path in acceptance_root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if manifest.get("audio_source") == "browser_live_mic":
            candidates.append(manifest_path.parent)
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1] if candidates else None


def _build_summary(
    *,
    run_id: str,
    artifact_root: Path,
    checks: list[dict[str, Any]],
    lane_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized_checks = {str(check.get("name")): dict(check) for check in checks}
    normalized_lanes = {
        lane: _manifest_from_result(result)
        for lane, result in lane_results.items()
    }
    blockers = _quality_check_blockers(normalized_checks)
    blockers.extend(_lane_blockers(normalized_lanes))
    blockers = _dedupe(blockers)
    return {
        "schema_version": "release_acceptance.v1",
        "run_id": run_id,
        "started_at": _now(),
        "ended_at": _now(),
        "git_commit": _git_commit(),
        "artifact_root": _display_path(artifact_root),
        "verdict": "go" if not blockers else "no_go",
        "blockers": blockers,
        "privacy_cost_flags": _aggregate_privacy_cost_flags(normalized_lanes),
        "llm_call_count": sum(int(manifest.get("llm_call_count") or 0) for manifest in normalized_lanes.values()),
        "llm_usage_total_tokens": sum(int(manifest.get("llm_usage_total_tokens") or 0) for manifest in normalized_lanes.values()),
        "artifacts": {
            "summary_json": "summary.json",
            "report_md": "report.md",
        },
        "checks": normalized_checks,
        "lanes": normalized_lanes,
    }


def _quality_check_blockers(checks: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for name in REQUIRED_CHECKS:
        check = checks.get(name)
        if check is None:
            blockers.append(f"check_{name}_missing")
        elif check.get("status") != "passed":
            blockers.append(f"check_{name}_failed")
    return blockers


def _lane_blockers(lanes: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for lane in REQUIRED_LANES:
        manifest = lanes.get(lane)
        if manifest is None:
            blockers.append(f"lane_{lane}_missing")
            if lane == "browser_live_mic":
                blockers.append("blocked_browser_live_mic_not_proven")
            continue
        if manifest.get("verdict") != "go":
            blockers.append(f"lane_{lane}_no_go")
        blockers.extend(str(reason) for reason in list(manifest.get("degradation_reasons") or []))
        if lane == "real_mic_recorded_realtime" and manifest.get("counts_as_real_mic_go_evidence") is not True:
            blockers.append("real_mic_recorded_realtime_not_proven")
        if lane == "browser_live_mic":
            if manifest.get("browser_live_mic_go_evidence") is not True or manifest.get("verdict") != "go":
                blockers.append("blocked_browser_live_mic_not_proven")
            blockers.extend(_browser_live_mic_production_llm_blockers(manifest))
    return blockers


def _browser_live_mic_production_llm_blockers(manifest: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    llm_called = manifest.get("llm_called") is True
    call_count = int(manifest.get("llm_call_count") or 0)
    usage_total = int(manifest.get("llm_usage_total_tokens") or 0)
    if llm_called and (call_count <= 0 or usage_total <= 0):
        blockers.append("browser_live_mic_llm_usage_evidence_missing")

    production_requested = manifest.get("derivation_mode") == "production_enabled"
    production_evidence_complete = (
        manifest.get("counts_as_production_llm_evidence") is True
        and llm_called
        and manifest.get("llm_provider") == "real_gateway"
        and manifest.get("gateway_base_url_kind") == "remote"
        and call_count > 0
        and usage_total > 0
    )
    if production_requested and not production_evidence_complete:
        blockers.append("browser_live_mic_production_llm_evidence_missing")
    return blockers


def _manifest_from_result(result: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(result.get("manifest") or result)
    return {
        "verdict": manifest.get("verdict", "no_go"),
        "audio_source": manifest.get("audio_source", "unknown"),
        "asr_provider": manifest.get("asr_provider", "unknown"),
        "asr_provider_mode": manifest.get("asr_provider_mode", "unknown"),
        "asr_fallback_used": manifest.get("asr_fallback_used"),
        "llm_provider": manifest.get("llm_provider", "unknown"),
        "llm_called": bool(manifest.get("llm_called", False)),
        "llm_call_count": int(manifest.get("llm_call_count") or 0),
        "llm_usage_total_tokens": int(manifest.get("llm_usage_total_tokens") or 0),
        "derivation_mode": str(manifest.get("derivation_mode") or "unknown"),
        "gateway_base_url_kind": str(manifest.get("gateway_base_url_kind") or "unknown"),
        "counts_as_production_llm_evidence": bool(manifest.get("counts_as_production_llm_evidence", False)),
        "transcript_char_count": int(manifest.get("transcript_char_count") or 0),
        "final_segment_count": int(manifest.get("final_segment_count") or 0),
        "suggestion_card_count": int(manifest.get("suggestion_card_count") or 0),
        "approach_card_count": int(manifest.get("approach_card_count") or 0),
        "minutes_char_count": int(manifest.get("minutes_char_count") or 0),
        "delete_verified": bool(manifest.get("delete_verified", False)),
        "browser_live_mic_go_evidence": bool(manifest.get("browser_live_mic_go_evidence", False)),
        "counts_as_real_mic_go_evidence": bool(manifest.get("counts_as_real_mic_go_evidence", False)),
        "asr_semantic_quality_status": str(manifest.get("asr_semantic_quality_status") or "not_evaluated"),
        "asr_semantic_quality_blocked": bool(manifest.get("asr_semantic_quality_blocked", False)),
        "degradation_reasons": list(manifest.get("degradation_reasons") or []),
        "privacy_cost_flags": dict(manifest.get("privacy_cost_flags") or {}),
        "artifact_root": str(result.get("artifact_root") or manifest.get("artifact_root") or ""),
        "manifest_path": _artifact_child_path(result, manifest, "manifest.json"),
        "go_no_go_path": _artifact_child_path(result, manifest, "go_no_go.md"),
    }


def _artifact_child_path(result: dict[str, Any], manifest: dict[str, Any], filename: str) -> str:
    artifact_root = str(result.get("artifact_root") or manifest.get("artifact_root") or "").strip()
    return f"{artifact_root.rstrip('/')}/{filename}" if artifact_root else filename


def _aggregate_privacy_cost_flags(lanes: dict[str, dict[str, Any]]) -> dict[str, bool]:
    keys = [
        "raw_audio_uploaded",
        "remote_asr_called",
        "llm_called",
        "configs_local_read",
        "user_audio_committed_to_repo",
    ]
    aggregated = {key: False for key in keys}
    for manifest in lanes.values():
        flags = manifest.get("privacy_cost_flags") or {}
        for key in keys:
            aggregated[key] = bool(aggregated[key] or flags.get(key, False))
        aggregated["llm_called"] = bool(aggregated["llm_called"] or manifest.get("llm_called", False))
    return aggregated


def _run_command_check(
    *,
    name: str,
    command: list[str],
    artifact_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    env = {
        **os.environ,
        "PYTHONPATH": "code/web_mvp/backend:code/core:code/asr_bakeoff",
    }
    log_base = artifact_root / f"{name}.log"
    try:
        proc = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        log_base.with_suffix(".stdout.log").write_text(stdout, encoding="utf-8")
        log_base.with_suffix(".stderr.log").write_text(stderr, encoding="utf-8")
        return {
            "name": name,
            "status": "failed",
            "returncode": None,
            "error": "timeout",
            "timeout_seconds": timeout_seconds,
            "command": " ".join(command),
            "stdout_log": log_base.with_suffix(".stdout.log").name,
            "stderr_log": log_base.with_suffix(".stderr.log").name,
        }
    log_base.with_suffix(".stdout.log").write_text(proc.stdout, encoding="utf-8")
    log_base.with_suffix(".stderr.log").write_text(proc.stderr, encoding="utf-8")
    return {
        "name": name,
        "status": "passed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "command": " ".join(command),
        "stdout_log": log_base.with_suffix(".stdout.log").name,
        "stderr_log": log_base.with_suffix(".stderr.log").name,
    }


def _run_http_json_check(*, name: str, url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - local release health URL.
            body = response.read().decode("utf-8")
            status_code = int(response.status)
    except (URLError, TimeoutError, OSError) as exc:
        return {"name": name, "status": "failed", "error": _safe_error(exc), "url": url}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body[:300]}
    return {
        "name": name,
        "status": "passed" if status_code == 200 else "failed",
        "status_code": status_code,
        "url": url,
        "payload": payload,
    }


def _run_workbench_js_version_check(workbench_url: str) -> dict[str, Any]:
    try:
        with urlopen(workbench_url, timeout=5) as response:  # noqa: S310 - local release workbench URL.
            html = response.read().decode("utf-8")
            status_code = int(response.status)
    except (URLError, TimeoutError, OSError) as exc:
        return {"name": "workbench_js_version", "status": "failed", "error": _safe_error(exc), "url": workbench_url}
    match = re.search(r"/static/workbench\.js\?v=([^\"']+)", html)
    return {
        "name": "workbench_js_version",
        "status": "passed" if status_code == 200 and match else "failed",
        "status_code": status_code,
        "url": workbench_url,
        "version": match.group(1) if match else "",
    }


def _blocked_lane_manifest(audio_source: str, blocker: str, **extra: Any) -> dict[str, Any]:
    manifest = {
        "verdict": "no_go",
        "audio_source": audio_source,
        "asr_provider": "not_started",
        "asr_provider_mode": "unknown",
        "asr_fallback_used": None,
        "llm_provider": "not_configured",
        "transcript_char_count": 0,
        "final_segment_count": 0,
        "suggestion_card_count": 0,
        "approach_card_count": 0,
        "minutes_char_count": 0,
        "delete_verified": False,
        "degradation_reasons": [blocker],
    }
    manifest.update(extra)
    return manifest


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# Release Acceptance {summary['run_id']}",
        "",
        f"Verdict: {summary['verdict']}",
        "",
        "## Blockers",
        "",
    ]
    blockers = list(summary.get("blockers") or [])
    lines.extend([f"- {blocker}" for blocker in blockers] or ["- none"])
    lines.extend(["", "## Checks", ""])
    for name, check in summary["checks"].items():
        lines.append(f"- {name}: {check.get('status')}")
    lines.extend(["", "## Lanes", ""])
    for name, manifest in summary["lanes"].items():
        lines.append(
            f"- {name}: {manifest.get('verdict')} "
            f"(audio_source={manifest.get('audio_source')}, asr={manifest.get('asr_provider')}, llm={manifest.get('llm_provider')})"
        )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_for_evidence(data), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _sanitize_for_evidence(data: Any) -> Any:
    secret_values = [
        value
        for value in [os.environ.get("LLM_GATEWAY_API_KEY")]
        if value
    ]
    return _sanitize_value(data, secret_values=secret_values)


def _sanitize_value(value: Any, *, secret_values: list[str]) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"api_key", "authorization", "token", "secret"}:
                sanitized[key_text] = "<redacted>" if item else item
            else:
                sanitized[key_text] = _sanitize_value(item, secret_values=secret_values)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item, secret_values=secret_values) for item in value]
    if isinstance(value, str):
        sanitized_text = value
        for secret in secret_values:
            sanitized_text = sanitized_text.replace(secret, "<redacted>")
        return sanitized_text
    return value


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def _git_commit() -> str:
    git_head = REPO_ROOT / ".git" / "HEAD"
    if not git_head.exists():
        return "unknown"
    head = git_head.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref = REPO_ROOT / ".git" / head.removeprefix("ref: ").strip()
        return ref.read_text(encoding="utf-8").strip() if ref.exists() else "unknown"
    return head


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    secret = os.environ.get("LLM_GATEWAY_API_KEY")
    return text.replace(secret, "<redacted>") if secret else text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--file-audio", type=Path, default=DEFAULT_AUDIO_PATH)
    parser.add_argument("--simulated-audio", type=Path, default=DEFAULT_AUDIO_PATH)
    parser.add_argument("--real-mic-recorded-audio", type=Path, default=None)
    parser.add_argument("--real-mic-health-report", type=Path, default=None)
    parser.add_argument("--browser-live-mic-bundle", type=Path, default=None)
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--workbench-url", default=DEFAULT_WORKBENCH_URL)
    args = parser.parse_args(argv)

    result = run_release_acceptance(
        run_id=args.run_id,
        artifact_root=args.artifact_root,
        data_dir=args.data_dir,
        file_audio=args.file_audio,
        simulated_audio=args.simulated_audio,
        real_mic_recorded_audio=args.real_mic_recorded_audio,
        real_mic_health_report=args.real_mic_health_report,
        browser_live_mic_bundle=args.browser_live_mic_bundle,
        health_url=args.health_url,
        workbench_url=args.workbench_url,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["summary"]["verdict"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
