#!/usr/bin/env python3
"""ASGI wrapper that captures validated Tauri no-op run evidence for PCWEB-119."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "desktop_tauri_noop_run_results"
CAPTURE_VERSION = "desktop_tauri_noop_webview_run_capture.v1"
VALIDATION_ENDPOINT = "/desktop/tauri-noop-run-results/validations"
SAFE_RUN_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def validate_output_root(output_root: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(output_root, suffix_parts):
            errors.append(f"path is blocked: {label}")
    resolved = output_root.resolve(strict=False)
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        error = f"path is blocked: {label}"
        if _path_has_suffix_parts(resolved, suffix_parts) and error not in errors:
            errors.append(error)
    return errors


def _repo_relative_path(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _validate_default_output_root(output_root: Path, *, allow_non_repo_output_root_for_tests: bool) -> None:
    errors = validate_output_root(output_root)
    if errors:
        raise ValueError(errors[0])
    if allow_non_repo_output_root_for_tests:
        return
    relative = _repo_relative_path(output_root)
    if relative is None:
        raise ValueError("output_root must be under repository artifacts/tmp")
    relative_text = relative.as_posix()
    if not (
        relative_text == "artifacts/tmp/desktop_tauri_noop_run_results"
        or relative_text.startswith("artifacts/tmp/desktop_tauri_noop_run_results/")
    ):
        raise ValueError("output_root must be under artifacts/tmp/desktop_tauri_noop_run_results")


def _safe_run_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "unknown-tauri-noop-run"
    sanitized = SAFE_RUN_ID_PATTERN.sub("-", value).strip(".-")
    return sanitized[:128] or "unknown-tauri-noop-run"


def _evidence_payload(request_payload: dict[str, Any], validation_report: dict[str, Any]) -> dict[str, Any]:
    run_result = request_payload.get("run_result")
    if not isinstance(run_result, dict):
        run_result = {}
    return {
        "capture_version": CAPTURE_VERSION,
        "capture_status": "captured_validated_tauri_noop_run",
        "source_endpoint": VALIDATION_ENDPOINT,
        "run_id": _safe_run_id(run_result.get("run_id")),
        "run_result": run_result,
        "validation_report": validation_report,
        "validated_command_count": validation_report.get("validated_command_count", 0),
        "returned_command_count": validation_report.get("returned_command_count", 0),
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_audio_now": False,
        "safe_to_start_asr_worker_now": False,
        "safe_to_read_audio_chunk_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def _write_evidence(output_root: Path, evidence: dict[str, Any]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = _safe_run_id(evidence.get("run_id"))
    output_path = output_root / f"{run_id}.pcweb-119-tauri-noop-run-validation.json"
    content = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output_path.exists() and output_path.read_text(encoding="utf-8") == content:
        return output_path
    output_path.write_text(content, encoding="utf-8")
    return output_path


def create_capture_app(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    allow_non_repo_output_root_for_tests: bool = False,
) -> FastAPI:
    _validate_default_output_root(
        output_root,
        allow_non_repo_output_root_for_tests=allow_non_repo_output_root_for_tests,
    )

    backend_path = REPO_ROOT / "code" / "web_mvp" / "backend"
    core_path = REPO_ROOT / "code" / "core"
    for path in (backend_path, core_path):
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)

    from meeting_copilot_web_mvp.app import create_app

    app = create_app()

    @app.middleware("http")
    async def capture_validated_tauri_noop_run(request: Request, call_next):  # type: ignore[no-untyped-def]
        body = await request.body()

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)
        response = await call_next(request)
        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk

        if request.url.path == VALIDATION_ENDPOINT and response.status_code == 200:
            request_payload = json.loads(body.decode("utf-8"))
            validation_report = json.loads(response_body.decode("utf-8"))
            evidence = _evidence_payload(request_payload, validation_report)
            _write_evidence(output_root, evidence)

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    return app


app = create_capture_app()
