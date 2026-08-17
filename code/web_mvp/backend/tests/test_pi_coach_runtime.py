from __future__ import annotations

from meeting_copilot_web_mvp.pi_coach_runtime import (
    PROTOCOL,
    PiCoachRuntimeError,
    PiCoachSidecar,
    build_pi_coach_request,
    configured_coach_runtime,
)
from meeting_copilot_web_mvp.realtime_intelligence import RealtimeIntelligenceRequest


def _request() -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="pi-runtime-meeting",
        state_revision=3,
        new_paragraphs=[
            {
                "id": "remote-1",
                "text": "Can you commit to Friday?",
                "revision": 1,
                "source_track": "system_audio",
            }
        ],
        context_paragraphs=[],
        semantic_windows=[
            {
                "id": "window-1",
                "text": "[remote_mix] Can you commit to Friday?",
                "revision": 1,
                "status": "stable",
                "segment_ids": ["remote-1"],
                "source_tracks": ["system_audio"],
                "role_hints": ["remote_mix"],
            }
        ],
        rolling_state={"topic": "release"},
        meeting_goal="Avoid an unconditional date commitment.",
    )


def test_runtime_flag_defaults_to_the_pi_coach_branch(monkeypatch) -> None:
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", raising=False)
    assert configured_coach_runtime() == "pi"
    assert configured_coach_runtime("pi") == "pi"
    assert configured_coach_runtime("unsupported") == "direct"


def test_pi_request_keeps_source_roles_and_never_places_secrets_in_ids() -> None:
    payload = build_pi_coach_request(
        _request(),
        request_id="request-1",
        base_url="https://gateway.example.test",
        api_key="private-test-key",
        model="coach-model",
        api_style="chat_completions",
        timeout_seconds=25,
    )

    assert payload["session_id"] == "pi-runtime-meeting"
    assert payload["context"]["new_paragraphs"][0]["source_track"] == "system_audio"
    assert payload["context"]["new_paragraphs"][0]["role_hint"] == "remote_mix"
    assert payload["context"]["semantic_windows"][0]["segment_ids"] == ("remote-1",)
    assert "private-test-key" not in payload["request_id"]
    assert payload["provider"]["api_key"] == "private-test-key"


def test_sidecar_response_validation_rejects_non_terminal_actions() -> None:
    response = {
        "protocol": PROTOCOL,
        "request_id": "request-1",
        "ok": True,
        "action": "text",
    }
    try:
        PiCoachSidecar._validate_response(response)
    except PiCoachRuntimeError as exc:
        assert exc.code == "pi_protocol_error"
    else:
        raise AssertionError("invalid Pi action must be rejected")
