#!/usr/bin/env python3
"""Fail-closed packaged API acceptance for the implemented full-roadmap surface."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import http.client
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
import wave
from zipfile import BadZipFile, ZipFile

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
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/full_roadmap_packaged_acceptance"
REPORT_SCHEMA_VERSION = "meeting_copilot.full_roadmap_packaged_acceptance.v1"
FAKE_MODEL = "packaged-acceptance-fake-model"
MAX_HTTP_RESPONSE_BYTES = 32 * 1024 * 1024
REVIEW_DOCUMENT_KINDS = (
    "minutes",
    "decisions",
    "action_items",
    "risks",
    "transcript",
)
REVIEW_JOB_KINDS = ("minutes", "approach", "index")
REQUIRED_CHECKS = (
    "packaged_binary_started",
    "rust_supervisor_backend",
    "authenticated_bootstrap_api",
    "controlled_local_audio",
    "explicit_fake_openai_gateway",
    "named_meeting",
    "realtime_asr_final",
    "ai_correction",
    "ai_intelligence",
    "ai_follow_up",
    "meeting_end",
    "minutes",
    "approach",
    "index",
    "retry_minutes",
    "retry_approach",
    "retry_index",
    "review_minutes_user_final",
    "review_decisions_user_final",
    "review_action_items_user_final",
    "review_risks_user_final",
    "review_transcript_user_final",
    "export_markdown",
    "export_docx",
    "export_json",
    "history_reopen",
    "recording_range",
    "diagnostic_bundle_redaction",
    "delete_derived",
    "delete_recording",
    "delete_transcript",
    "delete_all",
    "app_cleanup",
    "backend_cleanup",
    "worker_cleanup",
    "backend_port_cleanup",
    "fake_gateway_cleanup",
)

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+\S+")
_API_SECRET_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9._-]{4,}")
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var|Volumes|root|etc|opt|mnt|workspace)"
    r"(?:/[^\s,;:'\"]*)?"
)
_SENSITIVE_REPORT_KEYS = re.compile(
    r"(?i)(?:api[_-]?key|authorization|credential|password|raw[_-]?secret|access[_-]?token)"
)
_CONTENT_RANGE_PATTERN = re.compile(r"^bytes\s+0-\d+/\d+$")


def _usage() -> dict[str, int]:
    return {"prompt_tokens": 32, "completion_tokens": 24, "total_tokens": 56}


def _correct_text(text: str) -> tuple[str, int]:
    corrected = str(text)
    change_count = 0
    for source, target in (
        ("摩哒社区", "魔搭社区"),
        ("摩达社区", "魔搭社区"),
        ("布数服务", "部署服务"),
        ("百分之五", "5%"),
    ):
        occurrences = corrected.count(source)
        if occurrences:
            corrected = corrected.replace(source, target)
            change_count += occurrences
    return corrected, change_count


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
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {}
        paragraph = dict((payload.get("new_paragraphs") or [{}])[0])
        paragraph_id = str(paragraph.get("id") or "")
        revision = max(1, int(paragraph.get("revision") or 1))
        text = str(paragraph.get("text") or "").strip()
        corrected, change_count = _correct_text(text)
        return json.dumps(
            {
                "paragraph_revisions": [
                    {
                        "target_id": paragraph_id,
                        "expected_revision": revision,
                        "corrected_text": corrected,
                        "change_count": change_count,
                    }
                ],
                "topic_update": {
                    "operation": "add",
                    "title": "Packaged API acceptance",
                    "summary": corrected,
                    "evidence_segment_ids": [paragraph_id],
                    "evidence_quote": text,
                },
                "state_changes": [],
                "follow_up": {
                    "question": "请确认负责人、验收标准和回滚边界是否已明确？",
                    "reason": "受控验收语音中需要明确可执行的闭环信息。",
                    "evidence_segment_ids": [paragraph_id],
                    "evidence_quote": text,
                    "urgency": "medium",
                },
            },
            ensure_ascii=False,
        )
    if purpose == "transcript_correction":
        return _correct_text(user)[0]
    if purpose == "minutes":
        return json.dumps(
            {
                "background": "Packaged API acceptance with controlled local audio",
                "decisions": ["使用本地受控音频和显式 fake gateway 执行验收"],
                "action_items": [
                    {
                        "item": "确认导出、历史、录音与删除闭环",
                        "owner": "acceptance-runner",
                        "deadline": "current-run",
                    }
                ],
                "risks": ["任一缺项必须保持 no_go"],
                "open_questions": ["公开发布门禁不在本 fake-LLM 证据范围内"],
                "evidence_quotes": [user.strip()[:120]] if user.strip() else [],
            },
            ensure_ascii=False,
        )
    if purpose == "approach":
        return json.dumps(
            [
                {
                    "card_type": "approach.consideration",
                    "suggestion_text": "建议保持分项验证和 fail-closed 总结论。",
                    "confidence": 0.98,
                    "trigger_reason": "全路线 packaged API 验收",
                    "evidence_quote": user.strip()[:120],
                }
            ],
            ensure_ascii=False,
        )
    if purpose in {"legacy_suggestion", "streaming_suggestion"}:
        return json.dumps(
            {
                "suggestion_text": "建议确认负责人、回滚边界和验收标准。",
                "confidence": 0.95,
                "trigger_reason": "受控 packaged API 验收",
                "corrected_transcript": None,
            },
            ensure_ascii=False,
        )
    return "OK"


class AcceptanceOpenAIProvider:
    """A loopback-only fake gateway with an explicit non-real-LLM identity."""

    def __init__(self, *, credential: str) -> None:
        self._credential = str(credential)
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                try:
                    size = int(self.headers.get("content-length") or 0)
                except ValueError:
                    self.send_error(400)
                    return
                if size < 0 or size > 4 * 1024 * 1024:
                    self.send_error(413)
                    return
                try:
                    payload = json.loads(self.rfile.read(size) or b"{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                if not isinstance(payload, dict):
                    self.send_error(400)
                    return
                authorization_valid = secrets.compare_digest(
                    str(self.headers.get("authorization") or ""),
                    f"Bearer {owner._credential}",
                )
                if not authorization_valid:
                    self.send_error(401)
                    return
                messages = list(payload.get("messages") or [])
                system = str((messages[0] if messages else {}).get("content") or "")
                user = str((messages[-1] if messages else {}).get("content") or "")
                streaming = bool(payload.get("stream"))
                purpose = _purpose(system, streaming)
                with owner._lock:
                    owner.requests.append(
                        {
                            "purpose": purpose,
                            "stream": streaming,
                            "model": str(payload.get("model") or ""),
                            "authenticated": authorization_valid,
                        }
                    )
                content = _completion_for(purpose, user)
                if streaming:
                    self._stream_completion(content)
                else:
                    self._json_completion(content)

            def _stream_completion(self, content: str) -> None:
                midpoint = max(1, len(content) // 2)
                events = [
                    {
                        "id": "packaged-acceptance",
                        "choices": [{"delta": {"role": "assistant"}}],
                    },
                    {
                        "id": "packaged-acceptance",
                        "choices": [{"delta": {"content": content[:midpoint]}}],
                    },
                    {
                        "id": "packaged-acceptance",
                        "choices": [{"delta": {"content": content[midpoint:]}}],
                    },
                    {
                        "id": "packaged-acceptance",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    },
                    {"id": "packaged-acceptance", "choices": [], "usage": _usage()},
                ]
                body = (
                    "".join(
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        for event in events
                    )
                    + "data: [DONE]\n\n"
                )
                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _json_completion(self, content: str) -> None:
                encoded = json.dumps(
                    {
                        "id": "packaged-acceptance",
                        "choices": [
                            {"message": {"role": "assistant", "content": content}}
                        ],
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
        self._port = int(self.server.server_address[1])
        self._started = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        if not self._started:
            self.thread.start()
            self._started = True

    def stop(self) -> None:
        if not self._started:
            self.server.server_close()
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._started = False

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            requests = list(self.requests)
        purposes = Counter(str(item.get("purpose") or "") for item in requests)
        return {
            "kind": "fake_openai_compatible_gateway",
            "is_fake": True,
            "is_real_llm": False,
            "request_count": len(requests),
            "purposes": dict(sorted(purposes.items())),
            "all_requests_authenticated": bool(requests)
            and all(bool(item.get("authenticated")) for item in requests),
        }


@dataclass(frozen=True)
class HttpResult:
    status: int | None
    headers: dict[str, str]
    body: bytes
    truncated: bool = False
    error_class: str | None = None

    def json_value(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


class AuthenticatedApiClient:
    def __init__(
        self, *, port: int, cookie: str | None = None, token: str | None = None
    ) -> None:
        self.port = int(port)
        self.cookie = cookie
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
        max_bytes: int = MAX_HTTP_RESPONSE_BYTES,
    ) -> HttpResult:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        body = (
            json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request_headers = {
            "Accept": "application/json",
            "Origin": f"http://127.0.0.1:{self.port}",
        }
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        if self.token:
            request_headers["X-Meeting-Copilot-Token"] = self.token
        request_headers.update(
            {str(key): str(value) for key, value in (headers or {}).items()}
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read(max_bytes + 1)
            return HttpResult(
                status=response.status,
                headers={key.lower(): value for key, value in response.getheaders()},
                body=raw[:max_bytes],
                truncated=len(raw) > max_bytes,
            )
        except OSError as exc:
            return HttpResult(
                status=None,
                headers={},
                body=b"",
                error_class=type(exc).__name__,
            )
        finally:
            connection.close()

    def json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
    ) -> tuple[HttpResult, dict[str, Any]]:
        result = self.request(
            method,
            path,
            payload=payload,
            headers=headers,
            timeout=timeout,
        )
        value = result.json_value()
        return result, value if isinstance(value, dict) else {}


class AcceptanceRecorder:
    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        self.checks = {
            name: {"status": "not_run", "evidence": {}} for name in REQUIRED_CHECKS
        }
        self.blockers: set[str] = set()
        self.summary: dict[str, Any] = {}

    def pass_check(self, name: str, **evidence: Any) -> None:
        self._require_check(name)
        self.checks[name] = {"status": "passed", "evidence": dict(evidence)}

    def fail_check(self, name: str, blocker: str, **evidence: Any) -> None:
        self._require_check(name)
        self.checks[name] = {"status": "failed", "evidence": dict(evidence)}
        self.add_blocker(blocker)

    def add_blocker(self, blocker: str) -> None:
        normalized = str(blocker or "").strip()
        if normalized:
            self.blockers.add(normalized)

    def _require_check(self, name: str) -> None:
        if name not in self.checks:
            raise KeyError(f"unknown acceptance check: {name}")

    def finalize(self, *, duration_seconds: float) -> dict[str, Any]:
        blockers = set(self.blockers)
        for name, check in self.checks.items():
            if check["status"] != "passed":
                blockers.add(f"required_check_not_passed:{name}")
        passed = not blockers and all(
            check["status"] == "passed" for check in self.checks.values()
        )
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": "go" if passed else "no_go",
            "acceptance_scope": {
                "surface": "packaged_authenticated_backend_api",
                "app_binary_and_rust_supervisor_required": True,
                "audio": "controlled_local_fixture",
                "llm": "explicit_fake_openai_compatible_gateway",
                "non_public": True,
                "non_real_llm": True,
            },
            "checks": self.checks,
            "blockers": sorted(blockers),
            "summary": self.summary,
            "privacy_cost_flags": {
                "remote_asr_called": False,
                "remote_llm_called": False,
                "paid_service_called": False,
                "user_private_audio_read": False,
                "raw_meeting_content_in_report": False,
            },
            "decision": {
                "passed": passed,
                "counts_as_packaged_authenticated_api_evidence": passed,
                "counts_as_ui_evidence": False,
                "counts_as_real_llm_evidence": False,
                "counts_as_public_release_evidence": False,
            },
            "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
            "host": {
                "platform": platform.system(),
                "architecture": platform.machine(),
            },
        }


def sanitize_report(value: Any, *, secrets_to_remove: set[str] | None = None) -> Any:
    secrets_set = {item for item in (secrets_to_remove or set()) if item}

    def sanitize(item: Any) -> Any:
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            redacted_index = 0
            for raw_key, raw_value in item.items():
                key = str(raw_key)
                if _SENSITIVE_REPORT_KEYS.search(key):
                    redacted_index += 1
                    result[f"redacted_field_{redacted_index}"] = "<redacted>"
                    continue
                result[key] = sanitize(raw_value)
            return result
        if isinstance(item, (list, tuple, set)):
            return [sanitize(child) for child in item]
        if isinstance(item, Path):
            item = str(item)
        if isinstance(item, str):
            text = item
            for secret_value in secrets_set:
                text = text.replace(secret_value, "<redacted>")
            text = _BEARER_PATTERN.sub("<redacted>", text)
            text = _API_SECRET_PATTERN.sub("<redacted>", text)
            text = _ABSOLUTE_PATH_PATTERN.sub("<absolute-path-redacted>", text)
            return text
        if isinstance(item, bytes):
            return {"binary_redacted": True, "size_bytes": len(item)}
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return str(type(item).__name__)

    return sanitize(value)


def _contains_marker(value: Any, marker: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_marker(item, marker) for item in value)
    return marker in str(value or "")


def validate_downloads(
    *,
    markdown: bytes,
    docx: bytes,
    json_bytes: bytes,
    markers: Mapping[str, str],
    title: str,
) -> dict[str, dict[str, Any]]:
    result = {
        "markdown": {"passed": False, "reason": "invalid_utf8"},
        "docx": {"passed": False, "reason": "invalid_docx"},
        "json": {"passed": False, "reason": "invalid_json"},
    }
    try:
        markdown_text = markdown.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if title not in markdown_text:
            result["markdown"]["reason"] = "title_missing"
        elif not all(marker in markdown_text for marker in markers.values()):
            result["markdown"]["reason"] = "user_final_marker_missing"
        else:
            result["markdown"] = {"passed": True, "reason": None}

    try:
        with ZipFile(BytesIO(docx)) as archive:
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not required <= names:
                result["docx"]["reason"] = "missing_ooxml_entries"
            else:
                document = archive.read("word/document.xml").decode("utf-8")
                if title not in document:
                    result["docx"]["reason"] = "title_missing"
                elif not all(marker in document for marker in markers.values()):
                    result["docx"]["reason"] = "user_final_marker_missing"
                else:
                    result["docx"] = {"passed": True, "reason": None}
    except (BadZipFile, KeyError, UnicodeDecodeError, OSError):
        pass

    try:
        payload = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    else:
        if not isinstance(payload, dict) or payload.get("schema_version") != (
            "meeting_copilot.meeting_export.v1"
        ):
            result["json"]["reason"] = "invalid_schema"
        elif str((payload.get("meeting") or {}).get("title") or "") != title:
            result["json"]["reason"] = "title_missing"
        else:
            documents = payload.get("documents") or {}
            valid = True
            for kind, marker in markers.items():
                document = (
                    documents.get(kind) if isinstance(documents, Mapping) else None
                )
                final = (
                    document.get("user_final")
                    if isinstance(document, Mapping)
                    else None
                )
                if (
                    not isinstance(final, Mapping)
                    or final.get("modified") is not True
                    or not _contains_marker(final.get("content"), marker)
                ):
                    valid = False
                    break
            if not valid:
                result["json"]["reason"] = "user_final_document_missing"
            elif not all(
                _contains_marker(payload, marker) for marker in markers.values()
            ):
                result["json"]["reason"] = "user_final_marker_missing"
            else:
                result["json"] = {"passed": True, "reason": None}
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _audio_fixture_metadata(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        metadata = {
            "channels": handle.getnchannels(),
            "sample_width_bytes": handle.getsampwidth(),
            "sample_rate_hz": handle.getframerate(),
            "frame_count": handle.getnframes(),
        }
    if metadata["channels"] != 1:
        raise ValueError("audio fixture must be mono")
    if metadata["sample_width_bytes"] != 2:
        raise ValueError("audio fixture must be 16-bit PCM")
    if metadata["sample_rate_hz"] != 16_000:
        raise ValueError("audio fixture must be 16kHz")
    if metadata["frame_count"] <= 0:
        raise ValueError("audio fixture must contain frames")
    return metadata


def _clean_environment(isolated_home: Path, token: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_API_KEY")
        and not key.upper().startswith("LLM_GATEWAY_")
        and key.upper()
        not in {
            "AUTHORIZATION",
            "OPENAI_API_KEY",
            "MEETING_COPILOT_LOCAL_API_TOKEN",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE",
        }
    }
    environment.update(
        {
            "HOME": str(isolated_home),
            "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": token,
        }
    )
    return environment


def _wait_for_backend(
    *,
    app: subprocess.Popen[bytes],
    app_path: Path,
    token: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    backend: dict[str, Any] | None = None
    while time.monotonic() < deadline and app.poll() is None:
        backend = find_backend_process(
            read_process_table(), app_pid=app.pid, app_path=app_path
        )
        if backend is not None:
            health = http_response(int(backend["port"]), "/health")
            if (
                health["status"] == 200
                and health_proof(token).encode("ascii") in health["body"]
            ):
                return backend
        time.sleep(0.1)
    return None


def _wait_for_worker(
    *,
    backend_pid: int,
    app_path: Path,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        worker = find_funasr_process(
            read_process_table(),
            backend_pid=backend_pid,
            app_path=app_path,
        )
        if worker is not None:
            return worker
        time.sleep(0.1)
    return None


def _poll_json(
    client: AuthenticatedApiClient,
    path: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float,
    interval_seconds: float = 0.25,
) -> tuple[HttpResult, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_result = HttpResult(None, {}, b"", error_class="not_requested")
    last_body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_result, last_body = client.json_request("GET", path)
        if last_result.status == 200 and predicate(last_body):
            return last_result, last_body
        time.sleep(interval_seconds)
    return last_result, last_body


def _event_types(client: AuthenticatedApiClient, meeting_id: str) -> list[str]:
    result, body = client.json_request(
        "GET", f"/v2/meetings/{meeting_id}/events?after_seq=0&limit=1000"
    )
    if result.status != 200:
        return []
    return [str(event.get("type") or "") for event in body.get("events") or []]


def realtime_final_evidence(
    *,
    asr_result: Mapping[str, Any],
    segments: list[dict[str, Any]],
    event_types: list[str],
) -> tuple[bool, dict[str, Any]]:
    websocket_final_count = int(asr_result.get("non_empty_final_count") or 0)
    canonical_final_count = len(
        [
            segment
            for segment in segments
            if str(segment.get("segment_id") or "").strip()
            and int(segment.get("transcript_seq") or 0) > 0
        ]
    )
    finalized_event_observed = "transcript.segment.finalized" in event_types
    passed = (
        asr_result.get("ready") is True
        and asr_result.get("rejected") is not True
        and not asr_result.get("transport_error")
        and (
            websocket_final_count > 0
            or (canonical_final_count > 0 and finalized_event_observed)
        )
    )
    return passed, {
        "provider": "packaged_local_funasr",
        "ready": asr_result.get("ready") is True,
        "websocket_final_count": websocket_final_count,
        "canonical_api_final_count": canonical_final_count,
        "finalized_event_observed": finalized_event_observed,
    }


def _wait_for_live_ai(
    client: AuthenticatedApiClient,
    meeting_id: str,
    *,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    deadline = time.monotonic() + timeout_seconds
    snapshot: dict[str, Any] = {}
    transcript: dict[str, Any] = {}
    event_types: list[str] = []
    while time.monotonic() < deadline:
        snapshot_result, snapshot = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/snapshot"
        )
        transcript_result, transcript = client.json_request(
            "GET",
            f"/v2/meetings/{meeting_id}/transcript?after_transcript_seq=0&limit=500",
        )
        event_types = _event_types(client, meeting_id)
        segments = list(transcript.get("segments") or [])
        correction_terminal = bool(segments) and all(
            str(item.get("correction_status") or "") in {"changed", "no_change"}
            for item in segments
        )
        follow_up = snapshot.get("follow_up")
        if (
            snapshot_result.status == 200
            and transcript_result.status == 200
            and correction_terminal
            and isinstance(follow_up, Mapping)
            and "meeting.intelligence.applied" in event_types
        ):
            return snapshot, transcript, event_types
        time.sleep(0.25)
    return snapshot, transcript, event_types


def _wait_for_review_jobs(
    client: AuthenticatedApiClient,
    meeting_id: str,
    *,
    expected_job_ids: Mapping[str, str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        result, snapshot = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/snapshot"
        )
        review_jobs = snapshot.get("review_jobs") or {}
        complete = result.status == 200 and all(
            kind in review_jobs
            and str(review_jobs[kind].get("status") or "")
            in {"succeeded", "failed", "cancelled"}
            for kind in REVIEW_JOB_KINDS
        )
        if complete and expected_job_ids is not None:
            complete = all(
                str(review_jobs[kind].get("id") or "") == expected_job_ids[kind]
                for kind in REVIEW_JOB_KINDS
            )
        if complete:
            return snapshot
        time.sleep(0.25)
    return snapshot


def _markers(run_id: str) -> dict[str, str]:
    suffix = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12].upper()
    return {
        "minutes": f"ACCEPTANCE_MINUTES_{suffix}",
        "decisions": f"ACCEPTANCE_DECISIONS_{suffix}",
        "action_items": f"ACCEPTANCE_ACTIONS_{suffix}",
        "risks": f"ACCEPTANCE_RISKS_{suffix}",
        "transcript": f"ACCEPTANCE_TRANSCRIPT_{suffix}",
    }


def _review_contents(
    markers: Mapping[str, str],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    first = dict(segments[0]) if segments else {}
    return {
        "minutes": {"markdown": f"# 用户最终纪要\n\n{markers['minutes']}"},
        "decisions": {
            "decisions": [{"text": markers["decisions"], "status": "confirmed"}]
        },
        "action_items": {
            "action_items": [
                {
                    "text": markers["action_items"],
                    "status": "open",
                    "owner": "acceptance-runner",
                    "deadline": "current-run",
                }
            ]
        },
        "risks": {
            "risks": [
                {
                    "text": markers["risks"],
                    "status": "open",
                    "mitigation": "fail-closed",
                }
            ]
        },
        "transcript": {
            "segments": [
                {
                    "segment_id": str(first.get("segment_id") or "acceptance-segment"),
                    "transcript_seq": first.get("transcript_seq"),
                    "text": markers["transcript"],
                    "started_at_ms": int(first.get("started_at_ms") or 0),
                    "ended_at_ms": first.get("ended_at_ms"),
                    "speaker_id": first.get("speaker_id"),
                    "speaker_label": first.get("speaker_label"),
                }
            ]
        },
    }


def _save_and_verify_review_documents(
    *,
    client: AuthenticatedApiClient,
    meeting_id: str,
    contents: Mapping[str, Any],
    recorder: AcceptanceRecorder,
) -> dict[str, int]:
    revisions: dict[str, int] = {}
    snapshot_result, snapshot = client.json_request(
        "GET", f"/v2/meetings/{meeting_id}/snapshot"
    )
    documents = snapshot.get("documents") or {}
    if snapshot_result.status != 200:
        documents = {}
    for kind in REVIEW_DOCUMENT_KINDS:
        check_name = f"review_{kind}_user_final"
        current = documents.get(kind) if isinstance(documents, Mapping) else None
        expected_revision = (
            int(current.get("revision") or 0) if isinstance(current, Mapping) else 0
        )
        result, body = client.json_request(
            "PATCH",
            f"/v2/meetings/{meeting_id}/documents/{kind}",
            payload={
                "expected_revision": expected_revision,
                "content_json": contents[kind],
            },
        )
        document = body.get("document") or {}
        saved = (
            result.status == 200
            and int(document.get("revision") or -1) == expected_revision + 1
            and (document.get("user_final") or {}).get("modified") is True
            and (document.get("user_final") or {}).get("content") == contents[kind]
        )
        get_result, get_body = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/documents/{kind}"
        )
        stored = get_body.get("document") or {}
        revisions_result, revisions_body = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/documents/{kind}/revisions"
        )
        history = list(revisions_body.get("revisions") or [])
        verified = (
            saved
            and get_result.status == 200
            and (stored.get("user_final") or {}).get("content") == contents[kind]
            and revisions_result.status == 200
            and bool(history)
            and history[0].get("version_kind") == "user_final"
            and history[0].get("content") == contents[kind]
        )
        if verified:
            revisions[kind] = int(
                stored.get("revision") or document.get("revision") or 0
            )
            recorder.pass_check(
                check_name,
                saved=True,
                persisted=True,
                revision_history_verified=True,
            )
        else:
            recorder.fail_check(
                check_name,
                f"review_user_final_verification_failed:{kind}",
                patch_status=result.status,
                get_status=get_result.status,
                revisions_status=revisions_result.status,
            )
    return revisions


def _verify_user_final_preserved(
    *,
    client: AuthenticatedApiClient,
    meeting_id: str,
    contents: Mapping[str, Any],
    recorder: AcceptanceRecorder,
) -> dict[str, bool]:
    preserved: dict[str, bool] = {}
    for kind in REVIEW_DOCUMENT_KINDS:
        result, body = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/documents/{kind}"
        )
        document = body.get("document") or {}
        valid = (
            result.status == 200
            and (document.get("user_final") or {}).get("modified") is True
            and (document.get("user_final") or {}).get("content") == contents[kind]
        )
        preserved[kind] = valid
        if not valid:
            recorder.fail_check(
                f"review_{kind}_user_final",
                f"review_user_final_overwritten_after_retry:{kind}",
                preserved=False,
            )
    return preserved


def _validate_diagnostic_bundle(
    payload: bytes,
    *,
    forbidden_values: list[bytes],
) -> tuple[bool, str | None, dict[str, Any]]:
    lowered = payload.lower()
    if any(value and value in payload for value in forbidden_values):
        return False, "forbidden_value_present", {}
    if b"bearer " in lowered or re.search(rb"sk-[a-z0-9._-]{4,}", lowered):
        return False, "secret_pattern_present", {}
    if re.search(rb"/(users|home|private|tmp|var|volumes|root|etc|opt|mnt)/", lowered):
        return False, "absolute_path_present", {}
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if archive.namelist() != ["diagnostics.json", "manifest.json"]:
                return False, "unexpected_archive_entries", {}
            diagnostics = json.loads(archive.read("diagnostics.json"))
            manifest = json.loads(archive.read("manifest.json"))
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return False, "invalid_diagnostic_archive", {}
    privacy = manifest.get("privacy") or {}
    sanitization = diagnostics.get("sanitization") or {}
    valid = (
        diagnostics.get("schema_version") == "meeting_copilot.diagnostic_bundle.v1"
        and privacy.get("binary_payloads_included") is False
        and privacy.get("database_contents_included") is False
        and privacy.get("freeform_meeting_content_included") is False
        and privacy.get("private_paths_included") is False
        and privacy.get("secret_values_included") is False
        and sanitization.get("private_paths_included") is False
        and sanitization.get("freeform_error_text_included") is False
    )
    return (
        valid,
        None if valid else "privacy_manifest_invalid",
        {
            "entry_count": 2,
            "privacy_flags_verified": valid,
            "sha256": _sha256_bytes(payload),
        },
    )


def _verify_deletion_job(
    client: AuthenticatedApiClient,
    response_body: Mapping[str, Any],
) -> bool:
    job = response_body.get("deletion_job") or {}
    job_id = str(job.get("id") or "")
    if not job_id or job.get("status") != "completed":
        return False
    result, body = client.json_request("GET", f"/v2/data-governance/deletions/{job_id}")
    return result.status == 200 and body.get("status") == "completed"


def _perform_scoped_deletions(
    *,
    client: AuthenticatedApiClient,
    meeting_id: str,
    recorder: AcceptanceRecorder,
) -> None:
    for scope in ("derived", "recording", "transcript", "all"):
        result, body = client.json_request(
            "DELETE",
            f"/v2/meetings/{meeting_id}?scope={scope}",
            headers={"Idempotency-Key": f"packaged-acceptance-{scope}"},
            timeout=60,
        )
        base_valid = (
            result.status == 200
            and body.get("deleted") is True
            and body.get("deletion_scope") == scope
            and _verify_deletion_job(client, body)
        )
        valid = base_valid
        if scope == "derived" and base_valid:
            transcript_result, transcript = client.json_request(
                "GET",
                f"/v2/meetings/{meeting_id}/transcript?after_transcript_seq=0&limit=500",
            )
            audio_result, audio = client.json_request(
                "GET", f"/v2/meetings/{meeting_id}/audio"
            )
            snapshot_result, snapshot = client.json_request(
                "GET", f"/v2/meetings/{meeting_id}/snapshot"
            )
            valid = (
                transcript_result.status == 200
                and bool(transcript.get("segments"))
                and audio_result.status == 200
                and audio.get("assembled") is True
                and snapshot_result.status == 200
                and not snapshot.get("minutes")
                and not snapshot.get("approach_cards")
                and not snapshot.get("documents")
            )
        elif scope == "recording" and base_valid:
            transcript_result, transcript = client.json_request(
                "GET",
                f"/v2/meetings/{meeting_id}/transcript?after_transcript_seq=0&limit=500",
            )
            audio_result, audio = client.json_request(
                "GET", f"/v2/meetings/{meeting_id}/audio"
            )
            content_result = client.request(
                "GET", f"/v2/meetings/{meeting_id}/audio/content"
            )
            valid = (
                transcript_result.status == 200
                and bool(transcript.get("segments"))
                and audio_result.status == 200
                and audio.get("assembled") is False
                and content_result.status == 404
            )
        elif scope == "transcript" and base_valid:
            transcript_result, transcript = client.json_request(
                "GET",
                f"/v2/meetings/{meeting_id}/transcript?after_transcript_seq=0&limit=500",
            )
            history_result, history = client.json_request(
                "GET", "/v2/meetings?limit=100"
            )
            valid = (
                transcript_result.status == 200
                and transcript.get("segments") == []
                and history_result.status == 200
                and any(
                    item.get("id") == meeting_id
                    for item in history.get("meetings") or []
                )
            )
        elif scope == "all" and base_valid:
            history_result, history = client.json_request(
                "GET", "/v2/meetings?limit=100"
            )
            export_result = client.request(
                "GET", f"/v2/meetings/{meeting_id}/export?format=json"
            )
            valid = (
                body.get("meeting_deleted") is True
                and history_result.status == 200
                and all(
                    item.get("id") != meeting_id
                    for item in history.get("meetings") or []
                )
                and export_result.status == 404
            )
        check_name = f"delete_{scope}"
        if valid:
            recorder.pass_check(
                check_name,
                deletion_job_completed=True,
                scope_isolated=True,
            )
        else:
            recorder.fail_check(
                check_name,
                f"scoped_deletion_failed:{scope}",
                status=result.status,
                deletion_job_completed=base_valid,
            )
            return


def _write_report(
    *,
    report: dict[str, Any],
    run_root: Path,
    repo_root: Path,
    secrets_to_remove: set[str],
) -> dict[str, Any]:
    sanitized = sanitize_report(report, secrets_to_remove=secrets_to_remove)
    report_path = run_root / "report.json"
    report_path.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sanitized["report_path"] = str(report_path.relative_to(repo_root))
    return sanitized


def run_acceptance(
    *,
    repo_root: Path,
    app_path: Path,
    audio_path: Path,
    output_root: Path,
    run_id: str,
    startup_timeout_seconds: float = 60,
    ai_timeout_seconds: float = 60,
    review_timeout_seconds: float = 90,
    cleanup_timeout_seconds: float = 15,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    app_path = app_path.resolve()
    audio_path = audio_path.resolve()
    validate_run_id(run_id)
    output_root = resolve_output_root(repo_root, output_root)
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    isolated_home = run_root / "home"
    isolated_home.mkdir()
    recorder = AcceptanceRecorder(run_id=run_id)
    started = time.monotonic()
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    manifest = (
        app_path
        / "Contents/Resources/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
    )
    secrets_to_remove: set[str] = set()
    provider: AcceptanceOpenAIProvider | None = None
    app: subprocess.Popen[bytes] | None = None
    backend: dict[str, Any] | None = None
    worker: dict[str, Any] | None = None
    port: int | None = None
    app_forced_cleanup = False
    worker_forced_cleanup = False
    meeting_id = (
        "full_acceptance_" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:20]
    )
    title = f"Packaged API Acceptance {run_id}"[:200]
    markers = _markers(run_id)
    captured_transcript_values: list[str] = []

    if not binary.is_file() or not os.access(binary, os.X_OK):
        recorder.fail_check(
            "packaged_binary_started",
            "packaged_app_binary_missing",
            app_bundle_name=app_path.name,
        )
    if not manifest.is_file():
        recorder.fail_check(
            "rust_supervisor_backend",
            "packaged_runtime_manifest_missing",
            manifest_present=False,
        )
    try:
        audio_metadata = _audio_fixture_metadata(audio_path)
    except (FileNotFoundError, OSError, ValueError, wave.Error):
        recorder.fail_check(
            "controlled_local_audio",
            "controlled_audio_fixture_invalid",
            file_name=audio_path.name,
        )
    else:
        recorder.pass_check(
            "controlled_local_audio",
            file_name=audio_path.name,
            sha256=_sha256(audio_path),
            source="controlled_local_pcm_wav",
            private_user_audio=False,
            **audio_metadata,
        )
        recorder.summary["audio_fixture"] = {
            "file_name": audio_path.name,
            "sha256": _sha256(audio_path),
            "source": "controlled_local_pcm_wav",
        }

    preflight_ok = (
        recorder.checks["packaged_binary_started"]["status"] != "failed"
        and recorder.checks["rust_supervisor_backend"]["status"] != "failed"
        and recorder.checks["controlled_local_audio"]["status"] == "passed"
    )
    if not preflight_ok:
        report = recorder.finalize(duration_seconds=time.monotonic() - started)
        return _write_report(
            report=report,
            run_root=run_root,
            repo_root=repo_root,
            secrets_to_remove=secrets_to_remove,
        )

    token = secrets.token_hex(32)
    provider_credential = secrets.token_urlsafe(32)
    secrets_to_remove.update({token, provider_credential})
    provider = AcceptanceOpenAIProvider(credential=provider_credential)
    try:
        provider.start()
        environment = _clean_environment(isolated_home, token)
        app = subprocess.Popen(
            packaged_app_launch_command(binary),
            cwd=app_path.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
        recorder.pass_check(
            "packaged_binary_started",
            launched=True,
            app_bundle_name=app_path.name,
            binary_sha256=_sha256(binary),
        )
        recorder.summary["package"] = {
            "app_bundle_name": app_path.name,
            "binary_sha256": _sha256(binary),
            "runtime_manifest_sha256": _sha256(manifest),
        }
        backend = _wait_for_backend(
            app=app,
            app_path=app_path,
            token=token,
            timeout_seconds=startup_timeout_seconds,
        )
        if backend is None:
            recorder.fail_check(
                "rust_supervisor_backend",
                "packaged_backend_not_ready",
                app_alive=app.poll() is None,
            )
            raise RuntimeError("packaged_backend_not_ready")
        port = int(backend["port"])
        recorder.pass_check(
            "rust_supervisor_backend",
            backend_child_of_app=True,
            health_instance_proof_verified=True,
            random_loopback_port=True,
        )
        worker = _wait_for_worker(
            backend_pid=int(backend["pid"]),
            app_path=app_path,
            timeout_seconds=10,
        )

        bootstrap_status, cookie = bootstrap_cookie(port, token)
        token_client = AuthenticatedApiClient(port=port, token=token)
        provider_result, _ = token_client.json_request(
            "PUT",
            "/desktop/provider/config",
            payload={
                "base_url": provider.base_url,
                "api_key": provider_credential,
                "model": FAKE_MODEL,
                "provider_label": "packaged_acceptance_fake_non_real_llm",
            },
        )
        client = AuthenticatedApiClient(port=port, cookie=cookie)
        settings_result, settings = client.json_request("GET", "/settings")
        settings_patch_status: int | None = None
        if settings_result.status == 200 and isinstance(
            settings.get("suggestions"), dict
        ):
            settings["suggestions"]["cooldown_minutes"] = 0
            settings["suggestions"]["window_seconds"] = 1
            patch_result, _ = client.json_request(
                "PATCH", "/settings", payload=settings
            )
            settings_patch_status = patch_result.status
        auth_valid = (
            bootstrap_status == 303
            and bool(cookie)
            and provider_result.status == 200
            and settings_result.status == 200
            and settings_patch_status == 200
        )
        if auth_valid:
            recorder.pass_check(
                "authenticated_bootstrap_api",
                bootstrap_status=bootstrap_status,
                provider_config_status=provider_result.status,
                authenticated_settings_status=settings_result.status,
            )
        else:
            recorder.fail_check(
                "authenticated_bootstrap_api",
                "authenticated_bootstrap_api_failed",
                bootstrap_status=bootstrap_status,
                provider_config_status=provider_result.status,
                settings_status=settings_result.status,
                settings_patch_status=settings_patch_status,
            )

        create_result, create_body = client.json_request(
            "POST",
            "/v2/meetings",
            payload={
                "meeting_id": meeting_id,
                "title": title,
                "expected_duration_seconds": 300,
                "track_count": 1,
            },
        )
        created_meeting = create_body.get("meeting") or {}
        if (
            create_result.status == 201
            and created_meeting.get("id") == meeting_id
            and created_meeting.get("title") == title
            and created_meeting.get("title_source") == "user"
        ):
            recorder.pass_check(
                "named_meeting",
                create_status=create_result.status,
                readable_title=True,
                title_source="user",
            )
        else:
            recorder.fail_check(
                "named_meeting",
                "named_meeting_creation_failed",
                create_status=create_result.status,
            )

        preparation_result, _ = client.json_request(
            "PUT",
            f"/v2/meetings/{meeting_id}/preparation",
            payload=meeting_preparation_payload(),
        )
        if preparation_result.status != 200:
            recorder.add_blocker("meeting_preparation_failed")
        asr_result: dict[str, Any] = {}
        if create_result.status == 201 and preparation_result.status == 200:
            asr_result = stream_packaged_funasr(
                port,
                meeting_id=meeting_id,
                cookie=str(cookie),
                audio_path=audio_path,
                audio_source="controlled_acceptance_wav",
            )
        if worker is None:
            worker = _wait_for_worker(
                backend_pid=int(backend["pid"]),
                app_path=app_path,
                timeout_seconds=5,
            )
        live_snapshot, transcript, live_event_types = _wait_for_live_ai(
            client,
            meeting_id,
            timeout_seconds=ai_timeout_seconds,
        )
        segments = list(transcript.get("segments") or [])
        final_verified, final_evidence = realtime_final_evidence(
            asr_result=asr_result,
            segments=segments,
            event_types=live_event_types,
        )
        if final_verified:
            recorder.pass_check("realtime_asr_final", **final_evidence)
        else:
            recorder.fail_check(
                "realtime_asr_final",
                "realtime_asr_final_missing",
                **final_evidence,
                rejected=asr_result.get("rejected"),
                transport_error_class=asr_result.get("transport_error"),
            )
        for segment in segments:
            for field in (
                "text",
                "normalized_text",
                "correction_before_text",
                "correction_after_text",
            ):
                value = str(segment.get(field) or "").strip()
                if value:
                    captured_transcript_values.append(value)
        correction_statuses = [
            str(segment.get("correction_status") or "") for segment in segments
        ]
        terminal_corrections = bool(correction_statuses) and all(
            value in {"changed", "no_change"} for value in correction_statuses
        )
        if terminal_corrections:
            recorder.pass_check(
                "ai_correction",
                checked_segment_count=len(correction_statuses),
                changed_count=correction_statuses.count("changed"),
                no_change_count=correction_statuses.count("no_change"),
            )
        else:
            recorder.fail_check(
                "ai_correction",
                "ai_correction_not_terminal",
                segment_count=len(segments),
            )
        if "meeting.intelligence.applied" in live_event_types and bool(
            live_snapshot.get("current_topic")
        ):
            recorder.pass_check(
                "ai_intelligence",
                intelligence_event_observed=True,
                topic_projection_present=True,
            )
        else:
            recorder.fail_check(
                "ai_intelligence",
                "ai_intelligence_projection_missing",
                intelligence_event_observed=(
                    "meeting.intelligence.applied" in live_event_types
                ),
            )
        follow_up = live_snapshot.get("follow_up") or {}
        if (
            isinstance(follow_up, Mapping)
            and bool(str(follow_up.get("question") or "").strip())
            and bool(follow_up.get("evidence_segment_ids"))
            and bool(str(follow_up.get("evidence_quote") or "").strip())
        ):
            recorder.pass_check(
                "ai_follow_up",
                question_present=True,
                evidence_ids_present=True,
                evidence_quote_present=True,
            )
        else:
            recorder.fail_check(
                "ai_follow_up",
                "ai_follow_up_missing",
                follow_up_present=bool(follow_up),
            )

        end_result, end_body = client.json_request(
            "POST",
            f"/v2/meetings/{meeting_id}/end",
            payload={"action": "end_and_review"},
            timeout=30,
        )
        ended_meeting = end_body.get("meeting") or {}
        if end_result.status in {200, 202} and ended_meeting.get("state") == "ended":
            recorder.pass_check(
                "meeting_end",
                status=end_result.status,
                state="ended",
            )
        else:
            recorder.fail_check(
                "meeting_end",
                "meeting_end_failed",
                status=end_result.status,
            )

        review_snapshot = _wait_for_review_jobs(
            client,
            meeting_id,
            timeout_seconds=review_timeout_seconds,
        )
        review_jobs = review_snapshot.get("review_jobs") or {}
        documents = review_snapshot.get("documents") or {}
        for kind in REVIEW_JOB_KINDS:
            job = review_jobs.get(kind) or {}
            if kind == "minutes":
                artifact_present = bool(review_snapshot.get("minutes")) and all(
                    document_kind in documents
                    for document_kind in (
                        "minutes",
                        "decisions",
                        "action_items",
                        "risks",
                    )
                )
            elif kind == "approach":
                artifact_present = bool(review_snapshot.get("approach_cards"))
            else:
                artifact_present = (
                    bool((review_snapshot.get("review") or {}).get("indexed"))
                    and "transcript" in documents
                )
            if job.get("status") == "succeeded" and artifact_present:
                recorder.pass_check(
                    kind,
                    job_status="succeeded",
                    artifact_present=True,
                )
            else:
                recorder.fail_check(
                    kind,
                    f"review_artifact_failed:{kind}",
                    job_status=job.get("status"),
                    artifact_present=artifact_present,
                )

        contents = _review_contents(markers, segments)
        _save_and_verify_review_documents(
            client=client,
            meeting_id=meeting_id,
            contents=contents,
            recorder=recorder,
        )

        retry_ids: dict[str, str] = {}
        for kind in REVIEW_JOB_KINDS:
            result, body = client.json_request(
                "POST", f"/v2/meetings/{meeting_id}/jobs/{kind}/retry", payload={}
            )
            job = body.get("job") or {}
            job_id = str(job.get("id") or "")
            if (
                result.status == 200
                and body.get("created") is True
                and job.get("kind") == kind
                and job_id
            ):
                retry_ids[kind] = job_id
            else:
                recorder.fail_check(
                    f"retry_{kind}",
                    f"independent_retry_enqueue_failed:{kind}",
                    status=result.status,
                    created=body.get("created"),
                )
        if len(retry_ids) == len(REVIEW_JOB_KINDS) and len(
            set(retry_ids.values())
        ) == len(REVIEW_JOB_KINDS):
            retry_snapshot = _wait_for_review_jobs(
                client,
                meeting_id,
                expected_job_ids=retry_ids,
                timeout_seconds=review_timeout_seconds,
            )
            retry_jobs = retry_snapshot.get("review_jobs") or {}
            preserved = _verify_user_final_preserved(
                client=client,
                meeting_id=meeting_id,
                contents=contents,
                recorder=recorder,
            )
            for kind in REVIEW_JOB_KINDS:
                job = retry_jobs.get(kind) or {}
                if kind == "minutes":
                    preservation_valid = all(
                        preserved.get(document_kind) is True
                        for document_kind in (
                            "minutes",
                            "decisions",
                            "action_items",
                            "risks",
                        )
                    )
                elif kind == "index":
                    preservation_valid = preserved.get("transcript") is True
                else:
                    preservation_valid = True
                if (
                    job.get("id") == retry_ids[kind]
                    and job.get("status") == "succeeded"
                    and preservation_valid
                ):
                    recorder.pass_check(
                        f"retry_{kind}",
                        independent_job=True,
                        job_status="succeeded",
                        user_final_preserved=preservation_valid,
                    )
                else:
                    recorder.fail_check(
                        f"retry_{kind}",
                        f"independent_retry_failed:{kind}",
                        job_status=job.get("status"),
                        user_final_preserved=preservation_valid,
                    )

        downloads: dict[str, HttpResult] = {}
        for export_format in ("markdown", "docx", "json"):
            downloads[export_format] = client.request(
                "GET",
                f"/v2/meetings/{meeting_id}/export?format={export_format}",
                headers={"Accept": "*/*"},
                timeout=60,
            )
        download_validation = validate_downloads(
            markdown=downloads["markdown"].body,
            docx=downloads["docx"].body,
            json_bytes=downloads["json"].body,
            markers=markers,
            title=title,
        )
        expected_media = {
            "markdown": "text/markdown",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "json": "application/json",
        }
        for export_format in ("markdown", "docx", "json"):
            response = downloads[export_format]
            validation = download_validation[export_format]
            disposition = str(response.headers.get("content-disposition") or "")
            valid = (
                response.status == 200
                and not response.truncated
                and str(response.headers.get("content-type") or "").startswith(
                    expected_media[export_format]
                )
                and "attachment" in disposition.lower()
                and validation["passed"] is True
            )
            if valid:
                recorder.pass_check(
                    f"export_{export_format}",
                    downloaded=True,
                    user_final_content_verified=True,
                    size_bytes=len(response.body),
                    sha256=_sha256_bytes(response.body),
                )
            else:
                recorder.fail_check(
                    f"export_{export_format}",
                    f"export_validation_failed:{export_format}",
                    status=response.status,
                    reason=validation["reason"],
                    truncated=response.truncated,
                )

        history_query = urlencode({"limit": 100, "query": title, "status": "ready"})
        history_result, history = client.json_request(
            "GET", f"/v2/meetings?{history_query}"
        )
        reopened_result, reopened = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/snapshot"
        )
        reopened_documents = reopened.get("documents") or {}
        history_valid = (
            history_result.status == 200
            and any(
                item.get("id") == meeting_id and item.get("title") == title
                for item in history.get("meetings") or []
            )
            and reopened_result.status == 200
            and reopened.get("title") == title
            and reopened.get("title_source") == "user"
            and all(
                _contains_marker(
                    ((reopened_documents.get(kind) or {}).get("user_final") or {}).get(
                        "content"
                    ),
                    markers[kind],
                )
                for kind in REVIEW_DOCUMENT_KINDS
            )
        )
        if history_valid:
            recorder.pass_check(
                "history_reopen",
                server_search_found=True,
                snapshot_reopened=True,
                user_final_reloaded=True,
            )
        else:
            recorder.fail_check(
                "history_reopen",
                "history_reopen_failed",
                history_status=history_result.status,
                snapshot_status=reopened_result.status,
            )

        audio_result, audio = client.json_request(
            "GET", f"/v2/meetings/{meeting_id}/audio"
        )
        range_result = client.request(
            "GET",
            f"/v2/meetings/{meeting_id}/audio/content",
            headers={"Range": "bytes=0-63", "Accept": "audio/wav"},
        )
        range_valid = (
            audio_result.status == 200
            and audio.get("assembled") is True
            and range_result.status == 206
            and range_result.headers.get("accept-ranges") == "bytes"
            and bool(
                _CONTENT_RANGE_PATTERN.fullmatch(
                    str(range_result.headers.get("content-range") or "")
                )
            )
            and 1 <= len(range_result.body) <= 64
            and range_result.body.startswith(b"RIFF")
        )
        if range_valid:
            recorder.pass_check(
                "recording_range",
                assembled=True,
                partial_status=206,
                content_range_verified=True,
                wav_header_verified=True,
            )
        else:
            recorder.fail_check(
                "recording_range",
                "recording_range_contract_failed",
                audio_status=audio_result.status,
                assembled=audio.get("assembled"),
                partial_status=range_result.status,
                accept_ranges=range_result.headers.get("accept-ranges"),
                content_range_present=bool(range_result.headers.get("content-range")),
            )

        diagnostic_result = client.request(
            "GET", "/v2/diagnostics/bundle", headers={"Accept": "application/zip"}
        )
        forbidden_values = [
            token.encode("utf-8"),
            provider_credential.encode("utf-8"),
            title.encode("utf-8"),
            meeting_id.encode("utf-8"),
            str(app_path).encode("utf-8"),
            str(audio_path).encode("utf-8"),
            *[marker.encode("utf-8") for marker in markers.values()],
            *[value.encode("utf-8") for value in captured_transcript_values],
        ]
        diagnostic_valid, diagnostic_reason, diagnostic_summary = (
            _validate_diagnostic_bundle(
                diagnostic_result.body,
                forbidden_values=forbidden_values,
            )
            if diagnostic_result.status == 200 and not diagnostic_result.truncated
            else (False, "diagnostic_download_failed", {})
        )
        if diagnostic_valid:
            recorder.pass_check(
                "diagnostic_bundle_redaction",
                downloaded=True,
                allowlist_privacy_verified=True,
                **diagnostic_summary,
            )
        else:
            recorder.fail_check(
                "diagnostic_bundle_redaction",
                "diagnostic_bundle_redaction_failed",
                status=diagnostic_result.status,
                reason=diagnostic_reason,
                truncated=diagnostic_result.truncated,
            )

        provider_metadata = provider.metadata()
        purposes = provider_metadata.get("purposes") or {}
        provider_valid = (
            provider_metadata.get("kind") == "fake_openai_compatible_gateway"
            and provider_metadata.get("is_fake") is True
            and provider_metadata.get("is_real_llm") is False
            and provider_metadata.get("all_requests_authenticated") is True
            and int(purposes.get("realtime_intelligence") or 0) > 0
            and int(purposes.get("minutes") or 0) > 0
            and int(purposes.get("approach") or 0) > 0
        )
        recorder.summary["provider"] = provider_metadata
        if provider_valid:
            recorder.pass_check(
                "explicit_fake_openai_gateway",
                fake_identity_explicit=True,
                real_llm_used=False,
                all_requests_authenticated=True,
                purposes=purposes,
            )
        else:
            recorder.fail_check(
                "explicit_fake_openai_gateway",
                "explicit_fake_gateway_contract_failed",
                metadata=provider_metadata,
            )

        _perform_scoped_deletions(
            client=client,
            meeting_id=meeting_id,
            recorder=recorder,
        )
    except Exception as exc:
        recorder.add_blocker(f"acceptance_runtime_error:{type(exc).__name__}")
    finally:
        if provider is not None and "provider" not in recorder.summary:
            recorder.summary["provider"] = provider.metadata()
        if app is not None:
            if app.poll() is None:
                try:
                    os.killpg(app.pid, signal.SIGTERM)
                    app.wait(timeout=cleanup_timeout_seconds)
                except subprocess.TimeoutExpired:
                    app_forced_cleanup = True
                    os.killpg(app.pid, signal.SIGKILL)
                    app.wait(timeout=5)
                except ProcessLookupError:
                    pass
            if app.poll() is not None and not app_forced_cleanup:
                recorder.pass_check("app_cleanup", exited=True, forced_cleanup=False)
            else:
                recorder.fail_check(
                    "app_cleanup",
                    "app_cleanup_failed",
                    exited=app.poll() is not None,
                    forced_cleanup=app_forced_cleanup,
                )

        backend_exited = backend is not None and not pid_exists(int(backend["pid"]))
        worker_exited = worker is not None and not pid_exists(int(worker["pid"]))
        port_closed = port is not None and not port_is_listening(port)
        deadline = time.monotonic() + cleanup_timeout_seconds
        while time.monotonic() < deadline:
            if backend is not None:
                backend_exited = not pid_exists(int(backend["pid"]))
            if worker is not None:
                worker_exited = not pid_exists(int(worker["pid"]))
            if port is not None:
                port_closed = not port_is_listening(port)
            if backend_exited and worker_exited and port_closed:
                break
            time.sleep(0.1)
        if worker is not None and not worker_exited:
            worker_forced_cleanup = True
            try:
                os.kill(int(worker["pid"]), signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 3
            while pid_exists(int(worker["pid"])) and time.monotonic() < deadline:
                time.sleep(0.05)
            if pid_exists(int(worker["pid"])):
                try:
                    os.kill(int(worker["pid"]), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            worker_exited = not pid_exists(int(worker["pid"]))

        if backend is not None and backend_exited:
            recorder.pass_check("backend_cleanup", exited=True)
        elif backend is not None:
            recorder.fail_check(
                "backend_cleanup", "backend_cleanup_failed", exited=False
            )
        if worker is not None and worker_exited and not worker_forced_cleanup:
            recorder.pass_check("worker_cleanup", exited=True, forced_cleanup=False)
        elif worker is not None:
            recorder.fail_check(
                "worker_cleanup",
                "worker_cleanup_failed",
                exited=worker_exited,
                forced_cleanup=worker_forced_cleanup,
            )
        if port is not None and port_closed:
            recorder.pass_check("backend_port_cleanup", closed=True)
        elif port is not None:
            recorder.fail_check(
                "backend_port_cleanup", "backend_port_cleanup_failed", closed=False
            )

        if provider is not None:
            try:
                provider.stop()
            except Exception as exc:
                recorder.fail_check(
                    "fake_gateway_cleanup",
                    "fake_gateway_cleanup_failed",
                    error_class=type(exc).__name__,
                )
            else:
                gateway_closed = not port_is_listening(provider.port)
                if gateway_closed:
                    recorder.pass_check("fake_gateway_cleanup", closed=True)
                else:
                    recorder.fail_check(
                        "fake_gateway_cleanup",
                        "fake_gateway_port_still_open",
                        closed=False,
                    )

    report = recorder.finalize(duration_seconds=time.monotonic() - started)
    return _write_report(
        report=report,
        run_root=run_root,
        repo_root=repo_root,
        secrets_to_remove=secrets_to_remove,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run fail-closed full-roadmap acceptance through a packaged Meeting Copilot app."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--app-path", type=Path, required=True)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=60)
    parser.add_argument("--ai-timeout-seconds", type=float, default=60)
    parser.add_argument("--review-timeout-seconds", type=float, default=90)
    parser.add_argument("--cleanup-timeout-seconds", type=float, default=15)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_acceptance(
        repo_root=args.repo_root,
        app_path=args.app_path,
        audio_path=args.audio_path,
        output_root=args.output_root,
        run_id=args.run_id,
        startup_timeout_seconds=args.startup_timeout_seconds,
        ai_timeout_seconds=args.ai_timeout_seconds,
        review_timeout_seconds=args.review_timeout_seconds,
        cleanup_timeout_seconds=args.cleanup_timeout_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
