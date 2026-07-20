#!/usr/bin/env python3
"""Run the packaged Meeting Copilot mainline against a real OpenAI-compatible relay.

The runner is intentionally separate from the fake-provider acceptance runners. It
reads one local, owner-only JSON file, configures the already launched packaged
backend through its authenticated desktop API, streams a controlled WAV through
the bundled FunASR runtime, and records only redacted proof of the remote LLM
calls. It never puts provider credentials in a child-process environment or
command line.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import http.client
import ipaddress
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
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from packaged_runtime_supervisor_smoke import (
    bootstrap_cookie,
    find_backend_process,
    find_funasr_process,
    health_proof,
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
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/packaged_real_provider_mainline_smoke"
CONFIG_SCHEMA_VERSION = "meeting_copilot.local_provider_test.v1"
REPORT_SCHEMA_VERSION = "meeting_copilot.packaged_real_provider_mainline_smoke.v1"
_SECRET_RE = re.compile(r"(?i)\b(?:sk|rk)-[A-Za-z0-9._-]{6,}")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var|Volumes|root|etc|opt|mnt)"
    r"(?:/[^\s,;:'\"]*)?"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|secret|credential|password|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|raw[_-]?audio|audio[_-]?bytes)"
)
_REQUIRED_CONFIG_KEYS = frozenset({"schema_version", "base_url", "api_key", "model"})
_OPTIONAL_CONFIG_KEYS = frozenset({"api_style", "realtime_model"})


def _normalize_base_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() in {"localhost"}
        if not loopback:
            raise ValueError("remote base_url must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _is_gitignored(path: Path, repo_root: Path) -> bool:
    """Check ignore status without passing an absolute path to git."""

    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    )
    return completed.returncode == 0


def load_provider_config(
    path: Path,
    *,
    repo_root: Path | None = None,
    require_gitignored: bool = False,
) -> dict[str, str]:
    """Load and validate the only accepted local provider config shape."""

    path = Path(path)
    if path.is_symlink():
        raise ValueError("provider config must not be a symlink")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise ValueError("provider config is not readable") from exc
    if mode != 0o600:
        raise ValueError("provider config must have 0600 permissions")
    if require_gitignored:
        if repo_root is None or not _is_gitignored(path, repo_root):
            raise ValueError("provider config must be gitignored")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("provider config is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("provider config must be a JSON object")
    if not _REQUIRED_CONFIG_KEYS.issubset(payload) or set(payload) - (
        _REQUIRED_CONFIG_KEYS | _OPTIONAL_CONFIG_KEYS
    ):
        raise ValueError("provider config keys do not match the required schema")
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("provider config schema_version is invalid")
    if not isinstance(payload.get("base_url"), str):
        raise ValueError("provider config base_url must be a string")
    if not isinstance(payload.get("api_key"), str):
        raise ValueError("provider config api_key must be a string")
    if not isinstance(payload.get("model"), str):
        raise ValueError("provider config model must be a string")
    if "api_style" in payload and not isinstance(payload["api_style"], str):
        raise ValueError("provider config api_style must be a string")
    if "realtime_model" in payload and not isinstance(payload["realtime_model"], str):
        raise ValueError("provider config realtime_model must be a string")
    base_url = _normalize_base_url(payload["base_url"])
    api_key = payload["api_key"].strip()
    model = payload["model"].strip()
    realtime_model = payload.get("realtime_model", model).strip()
    api_style = payload.get("api_style", "responses").strip().lower()
    if not api_key:
        raise ValueError("provider config api_key is required")
    if not model or len(model) > 128:
        raise ValueError("provider config model is invalid")
    if not realtime_model or len(realtime_model) > 128:
        raise ValueError("provider config realtime_model is invalid")
    if api_style != "responses":
        raise ValueError("real provider runner requires api_style=responses")
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "realtime_model": realtime_model,
        "api_style": api_style,
    }


def build_packaged_launch_command(
    binary: Path, _api_key: str | None = None
) -> list[str]:
    """Return the AppKit-safe command; the second argument is never used."""

    return packaged_app_launch_command(binary)


def build_child_environment(
    base: Mapping[str, str], *, home: Path, token: str
) -> dict[str, str]:
    """Remove inherited provider credentials before starting the packaged app."""

    blocked_fragments = (
        "API_KEY",
        "AUTHORIZATION",
        "BEARER",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
    )
    environment = {
        key: value
        for key, value in base.items()
        if not any(fragment in key.upper() for fragment in blocked_fragments)
        and not key.upper().startswith("LLM_GATEWAY_")
        and key.upper() != "MEETING_COPILOT_LOCAL_API_TOKEN"
    }
    environment.update(
        {
            "HOME": str(home),
            "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE": "1",
            "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE": token,
            "MEETING_COPILOT_DESKTOP_RUNTIME": "1",
        }
    )
    return environment


def _safe_string(value: Any) -> str:
    value = str(value or "")
    value = _BEARER_RE.sub("Bearer [redacted]", value)
    value = _SECRET_RE.sub("[redacted-secret]", value)
    value = _URL_RE.sub("[redacted-url]", value)
    value = _ABSOLUTE_PATH_RE.sub("[redacted-path]", value)
    return value


def redact_evidence(value: Any, *, _key: str = "") -> Any:
    """Recursively remove credentials, URLs, local paths and raw audio."""

    if _SENSITIVE_KEY_RE.search(_key):
        return value if isinstance(value, (bool, int, float)) else "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(key): redact_evidence(item, _key=str(key))
            for key, item in value.items()
            if not _SENSITIVE_KEY_RE.search(str(key))
            or isinstance(item, (bool, int, float))
        }
    if isinstance(value, list):
        return [redact_evidence(item, _key=_key) for item in value]
    if isinstance(value, tuple):
        return [redact_evidence(item, _key=_key) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _safe_string(value) if isinstance(value, str) else value
    return "[redacted-value]"


def _usage_from_response(payload: Mapping[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return {
        "prompt_tokens": max(0, prompt),
        "completion_tokens": max(0, completion),
        "total_tokens": max(0, total),
    }


class LocalResponsesFixture:
    """Loopback-only Responses fixture used by unit tests, never by real runs."""

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key
        self._requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/responses":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("content-length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                authenticated = hmac.compare_digest(
                    str(self.headers.get("authorization") or ""),
                    f"Bearer {owner._api_key}",
                )
                if not authenticated:
                    self.send_error(401)
                    return
                started = time.monotonic()
                usage = {"input_tokens": 9, "output_tokens": 7, "total_tokens": 16}
                with owner._lock:
                    owner._requests.append(
                        {
                            "authenticated": True,
                            "started": started,
                            "usage": usage,
                            "model": str(payload.get("model") or ""),
                        }
                    )
                content = "OK"
                events = [
                    {
                        "type": "response.created",
                        "response": {"id": "fixture-response"},
                    },
                    {"type": "response.output_text.delta", "delta": content},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "fixture-response",
                            "model": str(payload.get("model") or "fixture-model"),
                            "status": "completed",
                            "output_text": content,
                            "usage": usage,
                        },
                    },
                ]
                body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
                body += "data: [DONE]\n\n"
                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._started = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> None:
        if not self._started:
            self.thread.start()
            self._started = True

    def stop(self) -> None:
        if self._started:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self._started = False
        self.server.server_close()

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            requests = list(self._requests)
        return {
            "request_count": len(requests),
            "authenticated_request_count": sum(
                bool(item["authenticated"]) for item in requests
            ),
            "api_style": "responses",
            "is_mock": True,
            "gateway": "loopback_fixture",
            "usage_total_tokens": sum(
                int(item["usage"]["total_tokens"]) for item in requests
            ),
        }


def post_responses_fixture(
    base_url: str, *, api_key: str, model: str, prompt: str
) -> dict[str, Any]:
    started = time.monotonic()
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    payload = {
        "model": model,
        "instructions": "只返回简短结果",
        "input": [{"role": "user", "content": prompt}],
        "store": False,
        "stream": True,
    }
    connection.request(
        "POST",
        "/v1/responses",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    response = connection.getresponse()
    raw = response.read(4 * 1024 * 1024).decode("utf-8")
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"fixture response status {response.status}")
    text_parts: list[str] = []
    completed: dict[str, Any] = {}
    first_token_at: float | None = None
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ")
        if data == "[DONE]":
            continue
        event = json.loads(data)
        if event.get("type") == "response.output_text.delta":
            if first_token_at is None:
                first_token_at = time.monotonic()
            text_parts.append(str(event.get("delta") or ""))
        if event.get("type") == "response.completed":
            completed = dict(event.get("response") or {})
    usage = _usage_from_response(completed)
    return {
        "text": "".join(text_parts),
        "usage": usage,
        "ttft_ms": round(((first_token_at or time.monotonic()) - started) * 1000, 3),
    }


def _ttft_observations(traces: list[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for trace in traces:
        stages = trace.get("stages")
        if not isinstance(stages, Mapping):
            continue
        connected = stages.get("provider_connected")
        first_token = stages.get("first_token")
        if (
            isinstance(connected, int)
            and isinstance(first_token, int)
            and first_token >= connected
        ):
            values.append(round((first_token - connected) / 1_000_000, 3))
    return values


def _provider_total_observations(traces: list[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for trace in traces:
        stages = trace.get("stages")
        if not isinstance(stages, Mapping):
            continue
        connected = stages.get("provider_connected")
        completed = stages.get("provider_completed")
        if (
            isinstance(connected, int)
            and isinstance(completed, int)
            and completed >= connected
        ):
            values.append(round((completed - connected) / 1_000_000, 3))
    return values


def _gateway_kind(base_url: str) -> str:
    hostname = str(urlsplit(base_url).hostname or "").lower()
    if hostname in {"127.0.0.1", "localhost", "::1"}:
        return "local"
    try:
        return "local" if ipaddress.ip_address(hostname).is_loopback else "remote"
    except ValueError:
        return "remote"


def correction_terminal_count(segments: list[Mapping[str, Any]]) -> int:
    return sum(
        1
        for item in segments
        if str(item.get("correction_status") or "") in {"changed", "no_change"}
    )


def summarize_correction_acceptance(
    *,
    segments: list[Mapping[str, Any]],
    events: list[Mapping[str, Any]],
    require_changed: bool = False,
) -> dict[str, Any]:
    """Validate terminal corrections and, when requested, a durable revision."""

    canonical_segments = [item for item in segments if isinstance(item, Mapping)]
    changed_segments = [
        item
        for item in canonical_segments
        if str(item.get("correction_status") or "") == "changed"
    ]
    revision_events = [
        item
        for item in events
        if isinstance(item, Mapping)
        and str(item.get("type") or item.get("event_type") or "")
        == "transcript.segment.revised"
    ]

    before_after_checks: list[bool] = []
    canonical_checks: list[bool] = []
    original_text_checks: list[bool] = []
    event_checks: list[bool] = []
    valid_changed_count = 0
    for segment in changed_segments:
        segment_id = str(segment.get("segment_id") or "").strip()
        before = segment.get("correction_before_text")
        after = segment.get("correction_after_text")
        canonical_text = segment.get("normalized_text")
        original_text = segment.get("text")
        before_after_valid = (
            isinstance(before, str)
            and bool(before.strip())
            and isinstance(after, str)
            and bool(after.strip())
            and before != after
        )
        canonical_valid = (
            isinstance(canonical_text, str)
            and isinstance(after, str)
            and canonical_text == after
        )
        original_text_non_empty = isinstance(original_text, str) and bool(
            original_text.strip()
        )

        matching_event = False
        original_text_preserved = False
        for event in revision_events:
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if (
                not segment_id
                or str(event.get("aggregate_type") or "") != "transcript_segment"
                or str(event.get("aggregate_id") or "") != segment_id
                or str(payload.get("segment_id") or "") != segment_id
            ):
                continue
            matching_event = (
                str(payload.get("correction_status") or "") == "changed"
                and payload.get("original_text") == before
                and payload.get("corrected_text") == after
                and payload.get("correction_before_text") == before
                and payload.get("correction_after_text") == after
                and payload.get("normalized_text") == after
            )
            original_text_preserved = (
                original_text_non_empty and payload.get("text") == original_text
            )
            if matching_event and original_text_preserved:
                break

        before_after_checks.append(before_after_valid)
        canonical_checks.append(canonical_valid)
        original_text_checks.append(original_text_preserved)
        event_checks.append(matching_event)
        if (
            before_after_valid
            and canonical_valid
            and original_text_preserved
            and matching_event
        ):
            valid_changed_count += 1

    changed_count = len(changed_segments)
    terminal_count = correction_terminal_count(canonical_segments)
    all_segments_terminal = terminal_count == len(canonical_segments)
    before_after_valid = bool(changed_segments) and all(before_after_checks)
    canonical_after_matches = bool(changed_segments) and all(canonical_checks)
    original_text_preserved = bool(changed_segments) and all(original_text_checks)
    revision_audit_observed = bool(changed_segments) and all(event_checks)
    changed_requirement_satisfied = (
        changed_count > 0
        and valid_changed_count == changed_count
        and before_after_valid
        and canonical_after_matches
        and original_text_preserved
        and revision_audit_observed
    )
    return {
        "required_changed": bool(require_changed),
        "terminal_count": terminal_count,
        "all_segments_terminal": all_segments_terminal,
        "changed_segment_count": changed_count,
        "valid_changed_segment_count": valid_changed_count,
        "revision_event_count": len(revision_events),
        "before_after_non_empty_and_different": before_after_valid,
        "canonical_normalized_text_matches_after": canonical_after_matches,
        "original_text_preserved": original_text_preserved,
        "revision_event_audit_observed": revision_audit_observed,
        "changed_requirement_satisfied": changed_requirement_satisfied,
        "passed": all_segments_terminal
        and (changed_count == 0 or changed_requirement_satisfied)
        and (not require_changed or changed_requirement_satisfied),
    }


def summarize_remote_proof(
    *,
    provider_health: Mapping[str, Any],
    slo: Mapping[str, Any],
    traces: list[Mapping[str, Any]],
    events: list[Mapping[str, Any]],
    gateway: str = "remote",
) -> dict[str, Any]:
    llm = provider_health.get("llm")
    llm = llm if isinstance(llm, Mapping) else {}
    usage = slo.get("token_usage")
    usage = usage if isinstance(usage, Mapping) else {}
    ttft = _ttft_observations(traces)
    provider_total = _provider_total_observations(traces)
    event_types = {
        str(event.get("type") or event.get("event_type") or "") for event in events
    }
    lanes = slo.get("lanes")
    lanes = lanes if isinstance(lanes, Mapping) else {}
    lane_counts = {
        name: int((value or {}).get("count") or 0)
        for name, value in lanes.items()
        if isinstance(value, Mapping)
    }
    configured = bool(llm.get("configured"))
    is_mock = bool(llm.get("is_mock"))
    api_style = str(llm.get("api_style") or "")
    total_tokens = int(usage.get("total_tokens") or 0)
    call_count = int(usage.get("call_count") or 0)
    remote_call_observed = (
        configured
        and not is_mock
        and gateway == "remote"
        and api_style == "responses"
        and call_count > 0
        and total_tokens > 0
    )
    remote_llm_proof = (
        remote_call_observed
        and bool(ttft)
        and any(count > 0 for count in lane_counts.values())
    )
    return {
        "remote_llm_proof": remote_llm_proof,
        "remote_call_observed": remote_call_observed,
        "gateway": gateway,
        "configured": configured,
        "is_mock": is_mock,
        "api_style": api_style,
        "provider": str(llm.get("provider") or "not_configured"),
        "model": str(llm.get("model") or "not_called"),
        "realtime_model": str(llm.get("realtime_model") or llm.get("model") or "not_called"),
        "token_usage_call_count": call_count,
        "token_usage_total": total_tokens,
        "lane_counts": lane_counts,
        "ttft_observation_count": len(ttft),
        "ttft_ms": ttft,
        "provider_total_observation_count": len(provider_total),
        "provider_total_ms": provider_total,
        "event_types": sorted(event_types),
        "intelligence_event_observed": "meeting.intelligence.applied" in event_types,
        "correction_event_observed": any("correction" in item for item in event_types),
    }


def _request_json(
    port: int,
    method: str,
    path: str,
    *,
    token: str | None = None,
    cookie: str | None = None,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 30,
) -> tuple[int | None, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    body = (
        json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {"Accept": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Meeting-Copilot-Token"] = token
    if cookie:
        headers["Cookie"] = cookie
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(32 * 1024 * 1024)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {}
        return response.status, value if isinstance(value, dict) else {}
    except OSError:
        return None, {}
    finally:
        connection.close()


def _relative_app_binary(app_path: Path) -> Path:
    return Path(app_path.name) / "Contents/MacOS/meeting-copilot-desktop"


def _safe_provider_health(payload: Mapping[str, Any]) -> dict[str, Any]:
    llm = payload.get("llm")
    llm = llm if isinstance(llm, Mapping) else {}
    return {
        "configured": bool(llm.get("configured")),
        "provider": str(llm.get("provider") or "not_configured"),
        "model": str(llm.get("model") or "not_called"),
        "api_style": str(llm.get("api_style") or "not_configured"),
        "is_mock": bool(llm.get("is_mock")),
        "credential_configured": bool(llm.get("credential_configured")),
    }


def _safe_traces(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    traces = payload.get("traces")
    if not isinstance(traces, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in traces:
        if not isinstance(raw, Mapping):
            continue
        stages = raw.get("stages")
        if not isinstance(stages, Mapping):
            stages = {}
        safe_stages: dict[str, int | float] = {}
        for name, value in stages.items():
            if str(name) not in {
                "provider_connected",
                "first_token",
                "provider_completed",
            }:
                continue
            if isinstance(value, Mapping):
                value = value.get("monotonic_ns")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                safe_stages[str(name)] = value
        result.append(
            {"lane": str(raw.get("lane") or "unknown"), "stages": safe_stages}
        )
    return result


def _safe_events(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    return [
        {"type": str(item.get("type") or item.get("event_type") or "")}
        for item in events
        if isinstance(item, Mapping)
    ]


def _poll_mainline_state(
    port: int,
    cookie: str,
    meeting_id: str,
    *,
    deadline_seconds: float,
    require_review: bool = False,
    require_terminal_corrections: bool = False,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    snapshot: dict[str, Any] = {}
    transcript: dict[str, Any] = {}
    events: dict[str, Any] = {}
    traces: dict[str, Any] = {}
    slo: dict[str, Any] = {}
    deadline = time.monotonic() + deadline_seconds
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
        _, events = _request_json(
            port,
            "GET",
            f"/v2/meetings/{meeting_id}/events?after_seq=0&limit=1000",
            cookie=cookie,
        )
        _, traces = _request_json(
            port, "GET", f"/v2/meetings/{meeting_id}/traces", cookie=cookie
        )
        _, slo = _request_json(
            port, "GET", f"/v2/meetings/{meeting_id}/realtime-ai-slo", cookie=cookie
        )
        review_jobs = snapshot.get("review_jobs") or {}
        review_done = bool(review_jobs) and all(
            str(job.get("status") or "") in {"succeeded", "failed", "cancelled"}
            for job in review_jobs.values()
            if isinstance(job, Mapping)
        )
        segments = [
            item
            for item in (transcript.get("segments") or [])
            if isinstance(item, Mapping)
        ]
        corrections_terminal = bool(segments) and (
            correction_terminal_count(segments) == len(segments)
        )
        if (
            snapshot.get("follow_up")
            and segments
            and (review_done or not require_review)
            and (corrections_terminal or not require_terminal_corrections)
        ):
            break
        time.sleep(0.25)
    return snapshot, transcript, events, traces, slo


def run_smoke(
    *,
    repo_root: Path,
    app_path: Path,
    audio_path: Path,
    config_path: Path,
    output_root: Path,
    run_id: str,
    require_changed_correction: bool = False,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    app_path = app_path.resolve()
    audio_path = audio_path.resolve()
    config = load_provider_config(
        config_path, repo_root=repo_root, require_gitignored=True
    )
    if _gateway_kind(config["base_url"]) != "remote":
        raise ValueError("real provider runner requires a remote gateway")
    output_root = resolve_output_root(repo_root, output_root)
    validate_run_id(run_id)
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    if not binary.is_file():
        raise FileNotFoundError("packaged app binary is missing")
    if not audio_path.is_file():
        raise FileNotFoundError("controlled audio fixture is missing")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    isolated_home = run_root / "home"
    isolated_home.mkdir()
    token = secrets.token_hex(32)
    environment = build_child_environment(os.environ, home=isolated_home, token=token)
    # Use a relative app argument so the child command line carries no local path.
    app_process = subprocess.Popen(
        build_packaged_launch_command(_relative_app_binary(app_path)),
        cwd=app_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    backend: dict[str, Any] | None = None
    funasr_process: dict[str, Any] | None = None
    responses: dict[str, int | None] = {}
    asr_result: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    transcript: dict[str, Any] = {}
    events_payload: dict[str, Any] = {}
    traces_payload: dict[str, Any] = {}
    slo: dict[str, Any] = {}
    provider_health_body: dict[str, Any] = {}
    app_exited = False
    backend_exited = False
    port_closed = False
    funasr_exited = False
    funasr_forced_cleanup = False
    started = time.monotonic()
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and app_process.poll() is None:
            backend = find_backend_process(
                read_process_table(), app_pid=app_process.pid, app_path=app_path
            )
            if backend is not None:
                health = _request_json(int(backend["port"]), "GET", "/health")[1]
                if health_proof(token) in json.dumps(health):
                    break
            time.sleep(0.1)
        if backend is None:
            raise RuntimeError("packaged backend did not become ready")
        port = int(backend["port"])
        funasr_process = find_funasr_process(
            read_process_table(), backend_pid=int(backend["pid"]), app_path=app_path
        )
        bootstrap_status, cookie = bootstrap_cookie(port, token)
        responses["bootstrap"] = bootstrap_status
        if bootstrap_status != 303 or not cookie:
            raise RuntimeError("packaged bootstrap authentication failed")
        responses["provider_health_before"] = _request_json(
            port, "GET", "/providers/health", cookie=cookie
        )[0]
        provider_status, provider_body = _request_json(
            port,
            "PUT",
            "/desktop/provider/config",
            token=token,
            payload={
                "base_url": config["base_url"],
                "api_key": config["api_key"],
                "model": config["model"],
                "realtime_model": config["realtime_model"],
                "api_style": "responses",
                "provider_label": "real_openai_compatible_relay",
            },
        )
        responses["provider_config"] = provider_status
        if provider_status != 200 or config["api_key"] in json.dumps(provider_body):
            raise RuntimeError("real provider configuration failed")
        provider_health_status, provider_health_body = _request_json(
            port, "GET", "/providers/health", cookie=cookie
        )
        responses["provider_health_after_config"] = provider_health_status
        settings_status, settings = _request_json(
            port, "GET", "/settings", cookie=cookie
        )
        responses["settings_get"] = settings_status
        if isinstance(settings.get("suggestions"), dict):
            settings["suggestions"]["cooldown_minutes"] = 0
            settings["suggestions"]["window_seconds"] = 1
        settings_status, _ = _request_json(
            port, "PATCH", "/settings", cookie=cookie, payload=settings
        )
        responses["settings_patch"] = settings_status
        meeting_id = f"real_provider_{run_id}"
        create_status, _ = _request_json(
            port,
            "POST",
            "/v2/meetings",
            cookie=cookie,
            payload={
                "meeting_id": meeting_id,
                "title": "Real provider packaged mainline",
                "expected_duration_seconds": 300,
                "track_count": 1,
            },
        )
        responses["create_meeting"] = create_status
        preparation_status, _ = _request_json(
            port,
            "PUT",
            f"/v2/meetings/{meeting_id}/preparation",
            cookie=cookie,
            payload=meeting_preparation_payload(),
        )
        responses["preparation"] = preparation_status
        if preparation_status != 200:
            raise RuntimeError("meeting preparation failed")
        asr_result = stream_packaged_funasr(
            port,
            meeting_id=meeting_id,
            cookie=cookie,
            audio_path=audio_path,
            audio_source="controlled_public_wav",
            timeout_seconds=120,
        )
        funasr_process = funasr_process or find_funasr_process(
            read_process_table(), backend_pid=int(backend["pid"]), app_path=app_path
        )
        snapshot, transcript, events_payload, traces_payload, slo = (
            _poll_mainline_state(
                port, cookie, meeting_id, deadline_seconds=90, require_review=False
            )
        )
        end_status, _ = _request_json(
            port,
            "POST",
            f"/v2/meetings/{meeting_id}/end",
            cookie=cookie,
            payload={"action": "end_and_review"},
            timeout=45,
        )
        responses["end_meeting"] = end_status
        snapshot, transcript, events_payload, traces_payload, slo = (
            _poll_mainline_state(
                port,
                cookie,
                meeting_id,
                deadline_seconds=120,
                require_review=True,
                require_terminal_corrections=require_changed_correction,
            )
        )
        responses["provider_health_after_mainline"] = _request_json(
            port, "GET", "/providers/health", cookie=cookie
        )[0]
        provider_health_body = _request_json(
            port, "GET", "/providers/health", cookie=cookie
        )[1]
    finally:
        if app_process.poll() is None:
            os.killpg(app_process.pid, signal.SIGTERM)
            try:
                app_process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(app_process.pid, signal.SIGKILL)
                app_process.wait(timeout=5)
        app_exited = app_process.poll() is not None
        deadline = time.monotonic() + 20
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
            cleanup_deadline = time.monotonic() + 5
            while (
                pid_exists(int(funasr_process["pid"]))
                and time.monotonic() < cleanup_deadline
            ):
                time.sleep(0.05)
            if pid_exists(int(funasr_process["pid"])):
                os.kill(int(funasr_process["pid"]), signal.SIGKILL)
            funasr_exited = not pid_exists(int(funasr_process["pid"]))

    provider_health = _safe_provider_health(provider_health_body)
    safe_traces = _safe_traces(traces_payload)
    safe_events = _safe_events(events_payload)
    remote_proof = summarize_remote_proof(
        provider_health=provider_health_body,
        slo=slo,
        traces=safe_traces,
        events=safe_events,
        gateway=_gateway_kind(config["base_url"]),
    )
    segments = list(transcript.get("segments") or [])
    raw_events = list(events_payload.get("events") or [])
    correction_acceptance = summarize_correction_acceptance(
        segments=segments,
        events=raw_events,
        require_changed=require_changed_correction,
    )
    follow_up = snapshot.get("follow_up")
    event_types = [str(item.get("type") or "") for item in safe_events]
    review_jobs = snapshot.get("review_jobs") or {}
    review_done = bool(review_jobs) and all(
        str(job.get("status") or "") in {"succeeded", "failed", "cancelled"}
        for job in review_jobs.values()
        if isinstance(job, Mapping)
    )
    terminal_correction_count = correction_acceptance["terminal_count"]
    passed = (
        responses.get("provider_config") == 200
        and responses.get("provider_health_after_config") == 200
        and responses.get("create_meeting") == 201
        and responses.get("preparation") == 200
        and responses.get("end_meeting") in {200, 202}
        and asr_result.get("ready") is True
        and int(asr_result.get("non_empty_final_count") or 0) > 0
        and bool(segments)
        and isinstance(follow_up, Mapping)
        and bool(str(follow_up.get("question") or "").strip())
        and "meeting.intelligence.applied" in event_types
        and correction_acceptance["passed"] is True
        and bool(snapshot.get("minutes"))
        and review_done
        and remote_proof["gateway"] == "remote"
        and remote_proof["remote_llm_proof"] is True
        and app_exited
        and backend_exited
        and port_closed
        and funasr_process is not None
        and funasr_exited
        and not funasr_forced_cleanup
    )
    evidence = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "status": (
            "go_packaged_real_remote_llm_mainline_not_ui_not_public_release"
            if passed
            else "no_go_packaged_real_remote_llm_mainline"
        ),
        "app": {
            "bundle_name": app_path.name,
            "binary": "Contents/MacOS/meeting-copilot-desktop",
        },
        "audio": {
            "source": "controlled_public_wav",
            "fixture_kind": "controlled_wav",
            "raw_audio_in_evidence": False,
            "transcript_text_in_evidence": False,
            "final_count": int(asr_result.get("non_empty_final_count") or 0),
        },
        "responses": responses,
        "asr": {
            "ready": asr_result.get("ready"),
            "final_count": asr_result.get("non_empty_final_count"),
            "provider": "funasr_realtime",
            "raw_events_in_evidence": False,
        },
        "provider_health": provider_health,
        "remote_provider": remote_proof,
        "meeting": {
            "id": meeting_id,
            "segment_count": len(segments),
            "follow_up_present": isinstance(follow_up, Mapping),
            "correction_terminal_count": terminal_correction_count,
            "correction_changed_event_observed": any(
                "correction" in item for item in event_types
            ),
            "correction_acceptance": correction_acceptance,
            "minutes_present": bool(snapshot.get("minutes")),
            "review_jobs_terminal": review_done,
            "event_types": event_types,
            "trace_count": len(safe_traces),
            "traces": safe_traces,
            "slo": {
                "token_usage": slo.get("token_usage", {}),
                "lane_names": sorted(str(name) for name in (slo.get("lanes") or {})),
            },
        },
        "cleanup": {
            "app_exited": app_exited,
            "backend_exited": backend_exited,
            "backend_port_closed": port_closed,
            "funasr_worker_exited": funasr_exited,
            "funasr_worker_forced_cleanup": funasr_forced_cleanup,
        },
        "duration_seconds": round(time.monotonic() - started, 3),
        "host": {"platform": platform.system(), "architecture": platform.machine()},
        "decision": {
            "passed": passed,
            "counts_as_real_remote_llm_evidence": passed,
            "counts_as_packaged_mainline_evidence": passed,
            "counts_as_tauri_ipc_or_ui_evidence": False,
            "counts_as_public_release_package": False,
        },
        "privacy_cost_flags": {
            "remote_llm_called": bool(remote_proof["remote_call_observed"]),
            "remote_asr_called": False,
            "paid_service_called": bool(remote_proof["remote_call_observed"]),
            "raw_audio_uploaded": False,
            "private_audio_written_to_evidence": False,
        },
    }
    evidence = redact_evidence(evidence)
    evidence_path = run_root / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    evidence["evidence_path"] = str(evidence_path.relative_to(repo_root))
    return evidence


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--app-path", type=Path, required=True)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--config", dest="config_path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--require-changed-correction",
        action="store_true",
        help=(
            "require at least one internally consistent changed correction and "
            "its transcript.segment.revised audit event"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        evidence = run_smoke(
            repo_root=args.repo_root,
            app_path=args.app_path,
            audio_path=args.audio_path,
            config_path=args.config_path,
            output_root=args.output_root,
            run_id=args.run_id,
            require_changed_correction=args.require_changed_correction,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "status": "error",
                    "error_class": type(exc).__name__,
                }
            )
        )
        return 2
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0 if evidence["decision"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
