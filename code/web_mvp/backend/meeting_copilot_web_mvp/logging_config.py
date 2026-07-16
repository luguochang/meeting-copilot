"""Structured logging configuration for the Meeting Copilot web MVP backend.

Configures structlog to emit JSON lines to stdout, and routes stdlib logging
(uvicorn, fastapi) through the same renderer so all logs share one format.

Usage:
    from meeting_copilot_web_mvp.logging_config import configure_logging, get_logger
    configure_logging()
    log = get_logger("meeting_copilot_web_mvp.app")
    log.info("request.end", path="/health", status_code=200, duration_ms=3.2)
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Any, TextIO

import structlog

from .storage_governance import ManagedLogRotator


_IDENTIFIER_KEYS = {"meeting_id", "session_id"}
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "secret",
    "prompt",
    "transcript",
    "normalized_text",
    "corrected_text",
    "draft_text",
    "meeting_content",
    "meeting_body",
    "segment_text",
    "source_text",
    "raw_text",
    "utterance",
    "audio_bytes",
)
_SENSITIVE_EXACT_KEYS = {
    "body",
    "content",
    "line",
    "message",
    "stderr",
    "stdout",
    "text",
}
_TOKEN_USAGE_KEYS = {
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "total_tokens",
}
_ERROR_DETAIL_KEYS = {"error", "exception"}
_ROUTE_IDENTIFIER = re.compile(
    r"(?P<prefix>/(?:v2/meetings|live/asr/sessions|live/asr/stream/ws)/)[^/?#]+"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"""
    (?<![A-Za-z0-9_])
    (?P<label>
        (?P<label_quote>["']?)
        (?:
            [A-Za-z0-9_-]*api[_-]?key
            | authorization
            | credential
            | password
            | (?:access|refresh|auth|id|session)?[_-]?token
            | secret(?:[_-]?[A-Za-z0-9]+)?
            | prompt
            | transcript(?:[_-]?(?:text|body|content))?
            | normalized[_-]?text
            | corrected[_-]?text
            | draft[_-]?text
            | meeting[_-]?(?:text|body|content)
            | segment[_-]?text
            | source[_-]?text
            | raw[_-]?text
            | utterance
            | text
            | content
            | body
            | message
            | stderr
            | stdout
            | line
            | (?:relative|absolute|config|audio|recording|events|file)?[_-]?path
            | data[_-]?dir
        )
        (?P=label_quote)
        \s*[:=]\s*
    )
    (?P<value>
        "(?:\\.|[^"\\])*"
        | '(?:\\.|[^'\\])*'
        | [^\r\n]*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer(?:\s+|%20)[^\s,;\"'{}\]]+")
_API_KEY_SECRET = re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9][A-Za-z0-9._-]*")
_URL_QUERY = re.compile(
    r"(?P<path>(?:https?://[^\s/\"']+)?/[^\s?\"']*)\?[^\s\"']+",
    re.IGNORECASE,
)
_LOCAL_ABSOLUTE_PATH = re.compile(
    r"""
    (?:
        file:///(?:Users|home|private|tmp|var|opt|Volumes|root|app|srv|etc|mnt|workspace)/
        | /(?:Users|home|private|tmp|var|opt|Volumes|root|app|srv|etc|mnt|workspace)/
        | [A-Za-z]:[\\/]
        | \\\\[^\\/\s]+[\\/]
    )
    [^\r\n,;}\]]*
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PRIVATE_RELATIVE_PATH = re.compile(
    r"""
    (?<![A-Za-z0-9_])
    (?:\.\.?[\\/])*
    (?:
        (?:
            configs[\\/]local
            | data[\\/]local_runtime
            | data[\\/]asr_eval[\\/]local_samples
        )
        (?:[\\/][^\r\n,;}\]]*)?
        |
        (?:audio_assets|live_asr_sessions|recordings?)[\\/][^\r\n,;}\]]*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _identifier_hash(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _redact_sensitive_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    if value[:1] in {'"', "'"}:
        replacement = f"{value[0]}<redacted>{value[0]}"
    else:
        replacement = "<redacted>"
    return f"{match.group('label')}{replacement}"


def _sanitize_log_string(value: str) -> str:
    value = _ROUTE_IDENTIFIER.sub(r"\g<prefix><meeting>", value)
    value = _URL_QUERY.sub(r"\g<path>?<redacted>", value)
    value = _SENSITIVE_ASSIGNMENT.sub(_redact_sensitive_assignment, value)
    value = _BEARER_SECRET.sub("Bearer <redacted>", value)
    value = _API_KEY_SECRET.sub("<redacted>", value)
    value = _LOCAL_ABSOLUTE_PATH.sub("<redacted_path>", value)
    return _PRIVATE_RELATIVE_PATH.sub("<redacted_path>", value)


def _normalize_log_key(key: str) -> str:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return snake_case.lower().replace("-", "_")


def _is_sensitive_log_key(key: str) -> bool:
    normalized = _normalize_log_key(key)
    if normalized in _TOKEN_USAGE_KEYS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized in {"token", "access_token", "refresh_token", "relative_path"}:
        return True
    if normalized.endswith("_token"):
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _redact_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = _normalize_log_key(key)
            if normalized in _IDENTIFIER_KEYS:
                sanitized["meeting_id_hash"] = _identifier_hash(raw_value)
            elif normalized in _ERROR_DETAIL_KEYS:
                sanitized[f"{key}_redacted"] = True
            elif _is_sensitive_log_key(key):
                sanitized[f"{key}_redacted"] = True
            else:
                sanitized[key] = _redact_log_value(raw_value)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_log_string(value)
    return value


def redact_sensitive_log_data(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Remove meeting content and secrets while preserving stable correlation."""

    return _redact_log_value(event_dict)


class RedactingFormatter(logging.Formatter):
    """Sanitize the fully rendered output of any stdlib logging handler."""

    def __init__(
        self,
        fmt: str | None = None,
        *,
        delegate: logging.Formatter | None = None,
    ) -> None:
        super().__init__(fmt=fmt)
        self._delegate = delegate

    def format(self, record: logging.LogRecord) -> str:
        rendered_record = record
        if record.exc_info is not None:
            error_type = record.exc_info[0]
            error_class = getattr(error_type, "__name__", "Exception")
            record_data = dict(record.__dict__)
            record_data.update({
                "msg": "exception captured error_class=%s",
                "args": (error_class,),
                "exc_info": None,
                "exc_text": None,
                "stack_info": None,
            })
            rendered_record = logging.makeLogRecord(record_data)
        rendered = (
            self._delegate.format(rendered_record)
            if self._delegate is not None
            else super().format(rendered_record)
        )
        return _sanitize_log_string(rendered)


def _install_redacting_formatter(handler: logging.Handler) -> None:
    if isinstance(handler.formatter, RedactingFormatter):
        return
    handler.setFormatter(RedactingFormatter(delegate=handler.formatter))


def _redacting_stream_handler(stream: TextIO) -> logging.StreamHandler[TextIO]:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    return handler


class ManagedRotatingLogStream:
    """Mirror runtime logs to stdout and the bounded managed log directory."""

    def __init__(
        self,
        *,
        data_dir: str | Path,
        mirror: TextIO | None = None,
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ) -> None:
        options = {}
        if max_bytes is not None:
            options["max_bytes"] = max_bytes
        if backup_count is not None:
            options["backup_count"] = backup_count
        self.rotator = ManagedLogRotator(
            data_dir=data_dir,
            log_name="backend.log",
            **options,
        )
        self._mirror = mirror or sys.stdout
        self.last_error: OSError | None = None

    def write(self, payload: str) -> int:
        written = self._mirror.write(payload)
        if payload:
            try:
                self.rotator.append(payload)
                self.last_error = None
            except OSError as exc:
                self.last_error = exc
        return written

    def flush(self) -> None:
        self._mirror.flush()

    def isatty(self) -> bool:
        return False


def configure_logging(json_output: bool = True, stream=None) -> None:
    """Configure structlog + stdlib logging. Safe to call multiple times.

    Args:
        json_output: True emits JSON lines (production); False emits console
            renderer (local dev). Defaults to JSON.
        stream: output stream; defaults to sys.stdout (captured at call time).
    """
    out = stream or sys.stdout
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_sensitive_log_data,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=True,
    )
    # Protect both the root logger and uvicorn's non-propagating handlers at the
    # final rendering boundary, including %-args and exception tracebacks.
    logging.basicConfig(level=logging.INFO, stream=out, format="%(message)s")
    for handler in logging.getLogger().handlers:
        _install_redacting_formatter(handler)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.handlers = [_redacting_stream_handler(out)]
        logger.propagate = False


def get_logger(name: str | None = None):
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
