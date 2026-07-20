#!/usr/bin/env python3
"""Verify packaged local ASR plus a local OpenAI-compatible provider end to end."""

from __future__ import annotations

import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import subprocess
import threading
import time
from typing import Any

from packaged_runtime_supervisor_smoke import (
    bootstrap_cookie,
    find_backend_process,
    find_funasr_process,
    health_proof,
    http_response,
    meeting_preparation_payload,
    packaged_app_launch_command,
    pid_exists,
    port_is_listening,
    read_process_table,
    resolve_output_root,
    stream_packaged_funasr,
    validate_run_id,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_ai_mainline_smoke"
FAKE_API_KEY = "sk-local-packaged-smoke-only"


def _usage() -> dict[str, int]:
    return {"prompt_tokens": 24, "completion_tokens": 12, "total_tokens": 36}


class LocalOpenAIProvider:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("content-length") or 0)
                payload = json.loads(self.rfile.read(size) or b"{}")
                messages = list(payload.get("messages") or [])
                system = str((messages[0] if messages else {}).get("content") or "")
                user = str((messages[-1] if messages else {}).get("content") or "")
                purpose = _purpose(system, bool(payload.get("stream")))
                owner.requests.append(
                    {
                        "purpose": purpose,
                        "stream": bool(payload.get("stream")),
                        "model": str(payload.get("model") or ""),
                        "authorization_present": self.headers.get("authorization")
                        == f"Bearer {FAKE_API_KEY}",
                    }
                )
                if payload.get("stream"):
                    self._stream_completion(_completion_for(purpose, user))
                    return
                self._json_completion(_completion_for(purpose, user))

            def _stream_completion(self, content: str) -> None:
                midpoint = max(1, len(content) // 2)
                parts = [content[:midpoint], content[midpoint:]]
                events = [
                    {"id": "local-smoke", "choices": [{"delta": {"role": "assistant"}}]},
                    *[
                        {"id": "local-smoke", "choices": [{"delta": {"content": part}}]}
                        for part in parts
                    ],
                    {
                        "id": "local-smoke",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    },
                    {"id": "local-smoke", "choices": [], "usage": _usage()},
                ]
                body = "".join(
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events
                ) + "data: [DONE]\n\n"
                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _json_completion(self, content: str) -> None:
                encoded = json.dumps(
                    {
                        "id": "local-smoke",
                        "choices": [{"message": {"role": "assistant", "content": content}}],
                        "usage": _usage(),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _purpose(system: str, streaming: bool) -> str:
    if "中文会议实时理解引擎" in system:
        return "realtime_intelligence"
    if "ASR 转写修正器" in system:
        return "transcript_correction"
    if "会议纪要生成器" in system:
        return "minutes"
    if "方案考量生成器" in system:
        return "approach"
    if "建议生成器" in system:
        return "legacy_suggestion"
    if streaming:
        return "streaming_suggestion"
    return "probe"


def _completion_for(purpose: str, user: str) -> str:
    if purpose == "realtime_intelligence":
        payload = json.loads(user)
        paragraph = dict((payload.get("new_paragraphs") or [{}])[0])
        paragraph_id = str(paragraph.get("id") or "")
        text = str(paragraph.get("text") or "").strip()
        return json.dumps(
            {
                "paragraph_revisions": [
                    {
                        "target_id": paragraph_id,
                        "expected_revision": int(paragraph.get("revision") or 1),
                        "corrected_text": text,
                        "change_count": 0,
                    }
                ],
                "topic_update": {
                    "operation": "add",
                    "title": "体验说明",
                    "summary": text,
                },
                "state_changes": [],
                "follow_up": {
                    "question": "建议确认本次体验的负责人和验收标准是否明确？",
                    "reason": "当前原话只说明正在体验，尚未说明负责人和验收标准。",
                    "evidence_segment_ids": [paragraph_id],
                    "evidence_quote": text,
                    "urgency": "medium",
                },
            },
            ensure_ascii=False,
        )
    if purpose == "transcript_correction":
        return user
    if purpose == "minutes":
        quote = user.strip().replace("\n", " ")[:80]
        return json.dumps(
            {
                "background": "技术架构评审",
                "decisions": [],
                "action_items": [
                    {"item": "确认降级边界", "owner": "待确认", "deadline": "待确认"}
                ],
                "risks": ["缓存穿透与峰值容量需要验证"],
                "open_questions": ["回滚负责人是谁？"],
                "evidence_quotes": [quote] if quote else [],
            },
            ensure_ascii=False,
        )
    if purpose == "approach":
        return "[]"
    if purpose == "legacy_suggestion":
        return json.dumps(
            {
                "suggestion_text": "建议确认回滚负责人和降级边界。",
                "confidence": 0.9,
                "trigger_reason": "风险处理仍待确认",
                "corrected_transcript": None,
            },
            ensure_ascii=False,
        )
    return "OK"


def _request_json(
    port: int,
    method: str,
    path: str,
    *,
    token: str | None = None,
    cookie: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 15,
) -> tuple[int | None, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"Origin": f"http://127.0.0.1:{port}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Meeting-Copilot-Token"] = token
    if cookie:
        headers["Cookie"] = cookie
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(4 * 1024 * 1024)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
        return response.status, parsed if isinstance(parsed, dict) else {}
    except OSError:
        return None, {}
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_smoke(
    *,
    repo_root: Path,
    app_path: Path,
    audio_path: Path,
    output_root: Path,
    run_id: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    app_path = app_path.resolve()
    audio_path = audio_path.resolve()
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    if not binary.is_file() or not audio_path.is_file():
        raise FileNotFoundError(binary if not binary.is_file() else audio_path)
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    isolated_home = run_root / "home"
    isolated_home.mkdir()
    token = secrets.token_hex(32)
    provider = LocalOpenAIProvider()
    provider.start()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_API_KEY")
        and not key.upper().startswith("LLM_GATEWAY_")
        and key.upper() not in {"AUTHORIZATION", "MEETING_COPILOT_LOCAL_API_TOKEN"}
    }
    environment.update(
        {
            "HOME": str(isolated_home),
            "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": token,
        }
    )
    app = subprocess.Popen(
        packaged_app_launch_command(binary),
        cwd=app_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    backend = None
    responses: dict[str, int | None] = {}
    asr_result: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    transcript: dict[str, Any] = {}
    audio: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    app_exited = backend_exited = port_closed = False
    funasr_process: dict[str, Any] | None = None
    funasr_exited = False
    funasr_forced_cleanup = False
    started = time.monotonic()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and app.poll() is None:
            backend = find_backend_process(read_process_table(), app_pid=app.pid, app_path=app_path)
            if backend is not None:
                health = http_response(int(backend["port"]), "/health")
                if health["status"] == 200 and health_proof(token).encode() in health["body"]:
                    break
            time.sleep(0.1)
        if backend is None:
            raise RuntimeError("packaged backend did not become ready")
        funasr_process = find_funasr_process(
            read_process_table(),
            backend_pid=int(backend["pid"]),
            app_path=app_path,
        )
        port = int(backend["port"])
        bootstrap_status, cookie = bootstrap_cookie(port, token)
        if bootstrap_status != 303 or not cookie:
            raise RuntimeError("packaged bootstrap authentication failed")
        responses["bootstrap"] = bootstrap_status
        status, provider_body = _request_json(
            port,
            "PUT",
            "/desktop/provider/config",
            token=token,
            payload={
                "base_url": provider.base_url,
                "api_key": FAKE_API_KEY,
                "model": "local-smoke-model",
                "provider_label": "local_packaged_smoke",
            },
        )
        responses["provider_config"] = status
        if status != 200 or FAKE_API_KEY in json.dumps(provider_body):
            raise RuntimeError("packaged provider runtime configuration failed")
        settings_status, settings = _request_json(port, "GET", "/settings", cookie=cookie)
        responses["settings_get"] = settings_status
        settings["suggestions"]["cooldown_minutes"] = 0
        settings["suggestions"]["window_seconds"] = 1
        settings_status, _ = _request_json(
            port, "PATCH", "/settings", cookie=cookie, payload=settings
        )
        responses["settings_patch"] = settings_status
        meeting_id = f"packaged_ai_{run_id}"
        created_status, _ = _request_json(
            port,
            "POST",
            "/v2/meetings",
            cookie=cookie,
            payload={
                "meeting_id": meeting_id,
                "expected_duration_seconds": 300,
                "track_count": 1,
            },
        )
        responses["create_meeting"] = created_status
        preparation_status, _ = _request_json(
            port,
            "PUT",
            f"/v2/meetings/{meeting_id}/preparation",
            cookie=cookie,
            payload=meeting_preparation_payload(),
        )
        responses["preparation"] = preparation_status
        if preparation_status == 200:
            asr_result = stream_packaged_funasr(
                port,
                meeting_id=meeting_id,
                cookie=cookie,
                audio_path=audio_path,
                audio_source="simulated_realtime_wav",
            )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            _, snapshot = _request_json(
                port, "GET", f"/v2/meetings/{meeting_id}/snapshot", cookie=cookie
            )
            if isinstance(snapshot.get("follow_up"), dict):
                break
            time.sleep(0.25)
        end_status, _ = _request_json(
            port,
            "POST",
            f"/v2/meetings/{meeting_id}/end",
            cookie=cookie,
            payload={"action": "end_and_review"},
            timeout=30,
        )
        responses["end_meeting"] = end_status
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            _, snapshot = _request_json(
                port, "GET", f"/v2/meetings/{meeting_id}/snapshot", cookie=cookie
            )
            _, transcript = _request_json(
                port,
                "GET",
                f"/v2/meetings/{meeting_id}/transcript?after_transcript_seq=0&limit=500",
                cookie=cookie,
            )
            _, audio = _request_json(
                port, "GET", f"/v2/meetings/{meeting_id}/audio", cookie=cookie
            )
            _, event_page = _request_json(
                port, "GET", f"/v2/meetings/{meeting_id}/events?after_seq=0", cookie=cookie
            )
            events = list(event_page.get("events") or [])
            review_jobs = snapshot.get("review_jobs") or {}
            review_done = review_jobs and all(
                job.get("status") in {"succeeded", "failed", "cancelled"}
                for job in review_jobs.values()
            )
            if audio.get("assembled") and review_done:
                break
            time.sleep(0.25)
    finally:
        if app.poll() is None:
            os.killpg(app.pid, signal.SIGTERM)
            try:
                app.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(app.pid, signal.SIGKILL)
                app.wait(timeout=5)
        app_exited = app.poll() is not None
        provider.stop()
        deadline = time.monotonic() + 15
        while backend is not None and time.monotonic() < deadline:
            backend_exited = not pid_exists(int(backend["pid"]))
            port_closed = not port_is_listening(int(backend["port"]))
            funasr_exited = bool(
                funasr_process is not None
                and not pid_exists(int(funasr_process["pid"]))
            )
            if backend_exited and port_closed and funasr_exited:
                break
            time.sleep(0.1)
        if funasr_process is not None and pid_exists(int(funasr_process["pid"])):
            funasr_forced_cleanup = True
            os.kill(int(funasr_process["pid"]), signal.SIGTERM)
            cleanup_deadline = time.monotonic() + 3
            while pid_exists(int(funasr_process["pid"])) and time.monotonic() < cleanup_deadline:
                time.sleep(0.05)
            if pid_exists(int(funasr_process["pid"])):
                os.kill(int(funasr_process["pid"]), signal.SIGKILL)
            funasr_exited = not pid_exists(int(funasr_process["pid"]))

    suggestions = list(snapshot.get("suggestions") or [])
    segments = list(transcript.get("segments") or [])
    follow_up = dict(snapshot.get("follow_up") or {})
    event_types = [str(event.get("type") or "") for event in events]
    provider_purposes = Counter(str(item["purpose"]) for item in provider.requests)
    passed = (
        responses.get("provider_config") == 200
        and responses.get("create_meeting") == 201
        and responses.get("preparation") == 200
        and responses.get("end_meeting") in {200, 202}
        and asr_result.get("ready") is True
        and int(asr_result.get("non_empty_final_count") or 0) > 0
        and bool(segments)
        and bool(str(follow_up.get("question") or "").strip())
        and bool(follow_up.get("evidence_segment_ids"))
        and bool(str(follow_up.get("evidence_quote") or "").strip())
        and "meeting.intelligence.applied" in event_types
        and all(
            str(item.get("correction_status") or "") in {"changed", "no_change"}
            for item in segments
        )
        and bool(audio.get("assembled"))
        and bool(snapshot.get("minutes"))
        and provider_purposes["realtime_intelligence"] > 0
        and all(item.get("authorization_present") for item in provider.requests)
        and app_exited
        and backend_exited
        and port_closed
        and funasr_process is not None
        and funasr_exited
        and not funasr_forced_cleanup
    )
    evidence = {
        "schema_version": "meeting_copilot.packaged_ai_mainline_smoke.v1",
        "run_id": run_id,
        "status": "go_packaged_local_ai_mainline_not_ui_not_public_release" if passed else "no_go_packaged_local_ai_mainline",
        "app_path": str(app_path.relative_to(repo_root)),
        "app_binary_sha256": _sha256(binary),
        "audio_fixture": {
            "path": str(audio_path.relative_to(repo_root)),
            "sha256": _sha256(audio_path),
            "source": "controlled_synthetic_technical_chinese",
        },
        "responses": responses,
        "asr": {
            "ready": asr_result.get("ready"),
            "final_count": asr_result.get("non_empty_final_count"),
            "final_texts": asr_result.get("non_empty_final_texts"),
        },
        "projection": {
            "segment_count": len(segments),
            "segments": [
                {
                    "text": str(item.get("normalized_text") or item.get("text") or ""),
                    "correction_status": item.get("correction_status"),
                }
                for item in segments
            ],
            "follow_up": follow_up,
            "suggestions": [
                {"status": item.get("status"), "text": item.get("text") or item.get("draft_text")}
                for item in suggestions
            ],
            "event_types": event_types,
            "audio": audio,
            "minutes_present": bool(snapshot.get("minutes")),
            "review_jobs": snapshot.get("review_jobs"),
        },
        "provider": {
            "kind": "local_openai_compatible_fake",
            "request_count": len(provider.requests),
            "purposes": dict(provider_purposes),
            "all_authorized": all(item.get("authorization_present") for item in provider.requests),
            "secret_persisted_in_evidence": False,
        },
        "cleanup": {
            "app_exited": app_exited,
            "backend_exited": backend_exited,
            "backend_port_closed": port_closed,
            "funasr_worker_pid": funasr_process.get("pid") if funasr_process else None,
            "funasr_worker_exited": funasr_exited,
            "funasr_worker_forced_cleanup": funasr_forced_cleanup,
        },
        "duration_seconds": round(time.monotonic() - started, 3),
        "host": {"platform": platform.platform(), "architecture": platform.machine()},
        "decision": {
            "passed": passed,
            "counts_as_packaged_ai_mainline_evidence": passed,
            "counts_as_tauri_ipc_or_ui_evidence": False,
            "counts_as_real_remote_llm_evidence": False,
            "counts_as_public_release_package": False,
        },
        "privacy_cost_flags": {
            "remote_asr_called": False,
            "remote_llm_called": False,
            "paid_service_called": False,
            "user_private_audio_read": False,
        },
    }
    evidence_path = run_root / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence["evidence_path"] = str(evidence_path.relative_to(repo_root))
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--app-path", type=Path, required=True)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    evidence = run_smoke(
        repo_root=args.repo_root,
        app_path=args.app_path,
        audio_path=args.audio_path,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0 if evidence["decision"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
