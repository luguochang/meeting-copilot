from __future__ import annotations

from io import StringIO
import logging

import structlog

from meeting_copilot_web_mvp.logging_config import (
    RedactingFormatter,
    configure_logging,
)


_STDLIB_LOGGER_NAMES = ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi")


def test_configured_stdlib_handlers_redact_rendered_secrets_content_and_paths():
    output = StringIO()
    root = logging.getLogger()
    root_state = (list(root.handlers), root.level, root.propagate)
    stdlib_logger = logging.getLogger("meeting_copilot_web_mvp.logging_redaction_test")
    stdlib_logger_state = (
        list(stdlib_logger.handlers),
        stdlib_logger.level,
        stdlib_logger.propagate,
    )
    logger_states = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in _STDLIB_LOGGER_NAMES
    }
    structlog_state = structlog.get_config()

    api_key = "sk-stdlib-handler-secret"
    auth_token = "authorization-token-value"
    generic_token = "generic-token-value"
    generic_secret = "generic-secret-value"
    meeting_body = "private meeting transcript body"
    meeting_id = "private-meeting-id"
    private_path = "/Users/alice/meeting-copilot/configs/local/provider-secret.json"
    linux_private_path = "/root/workspace/audio.wav"
    opaque_secret = "opaque-provider-secret"

    try:
        root_probe_handler = logging.StreamHandler(output)
        root_probe_handler.setFormatter(logging.Formatter("stdlib %(message)s"))
        root.addHandler(root_probe_handler)
        configure_logging(stream=output)
        error_logger = logging.getLogger("uvicorn.error")
        access_logger = logging.getLogger("uvicorn.access")

        assert isinstance(root_probe_handler.formatter, RedactingFormatter)
        assert error_logger.handlers
        assert all(
            isinstance(handler.formatter, RedactingFormatter)
            for handler in error_logger.handlers
        )

        error_logger.error("Authorization=%s", f"Bearer {auth_token}")
        error_logger.error("api_key=%s", api_key)
        error_logger.error("token=%s", generic_token)
        error_logger.error("secret=%s", generic_secret)
        error_logger.error("transcript=%s", meeting_body)
        error_logger.error("metadata=%s", {"content": meeting_body})
        error_logger.error("loading local config from %s", private_path)
        stdlib_logger.error("content=%s", meeting_body)
        stdlib_logger.error("loading local config from %s", private_path)
        stdlib_logger.error("loading runtime asset from %s", linux_private_path)
        try:
            raise RuntimeError(
                f"private-meeting-transcript-body at {linux_private_path} "
                f"with {opaque_secret}"
            )
        except RuntimeError:
            error_logger.exception("provider call failed")
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1234",
            "GET",
            f"/v2/meetings/{meeting_id}/snapshot?token={generic_token}",
            "1.1",
            200,
        )
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1234",
            "GET",
            (
                "/health?note=private-meeting-transcript-body"
                f"&key={opaque_secret}&location={linux_private_path}"
            ),
            "1.1",
            200,
        )
    finally:
        root.handlers, root.level, root.propagate = root_state
        stdlib_logger.handlers, stdlib_logger.level, stdlib_logger.propagate = (
            stdlib_logger_state
        )
        for name, (handlers, level, propagate) in logger_states.items():
            logger = logging.getLogger(name)
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
        structlog.configure(**structlog_state)

    rendered = output.getvalue()
    assert "<redacted>" in rendered
    assert "<redacted_path>" in rendered
    assert "/v2/meetings/<meeting>/snapshot?<redacted>" in rendered
    assert "/health?<redacted>" in rendered
    for sensitive_value in (
        api_key,
        auth_token,
        generic_token,
        generic_secret,
        meeting_body,
        meeting_id,
        private_path,
        linux_private_path,
        opaque_secret,
        "private-meeting-transcript-body",
        "configs/local",
    ):
        assert sensitive_value not in rendered
