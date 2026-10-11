"""Bounded durable reasons for permanent post-meeting request failures."""
from __future__ import annotations

from fastapi import HTTPException


class PermanentPostMeetingFailure(RuntimeError):
    retryable = False

    def __init__(self, *, error_class: str, status_code: int) -> None:
        self.durable_error_class = error_class
        self.status_code = status_code
        # Never retain upstream detail, credentials, transcript or an arbitrary
        # HTTP error body in durable output or executor messages.
        super().__init__(error_class)


def permanent_post_meeting_failure(error: HTTPException) -> PermanentPostMeetingFailure | None:
    status = error.status_code
    if not 400 <= status < 500 or status in {408, 425, 429}:
        return None
    detail = error.detail if isinstance(error.detail, str) else ""
    if status == 404 and detail.startswith("ASR live session not found:"):
        code = "post_meeting_source_missing"
    elif status == 409 and detail.startswith("ASR live session ") and "is not eligible for enabled LLM execution; blockers:" in detail:
        code = "post_meeting_source_ineligible"
    elif status == 409 and detail == "mock LLM provider cannot create production derivations":
        code = "post_meeting_provider_ineligible"
    elif status in {401, 403}:
        code = "post_meeting_provider_auth_invalid"
    else:
        code = "post_meeting_request_invalid"
    return PermanentPostMeetingFailure(error_class=code, status_code=status)
