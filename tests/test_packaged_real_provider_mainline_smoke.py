from __future__ import annotations

import json
from pathlib import Path
import stat
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import packaged_real_provider_mainline_smoke as smoke  # noqa: E402


def _config_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "meeting_copilot.local_provider_test.v1",
        "base_url": "https://relay.example/v1-root",
        "api_key": "sk-test-secret-not-for-evidence",
        "model": "gpt-test",
        "realtime_model": "gpt-realtime-test",
        "api_style": "responses",
    }
    payload.update(overrides)
    return payload


def _write_config(path: Path, payload: dict[str, object], mode: int = 0o600) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(mode)
    return path


def test_provider_config_requires_schema_and_owner_only_permissions(tmp_path: Path):
    config_path = _write_config(
        tmp_path / "provider.local.json", _config_payload(), 0o644
    )

    with pytest.raises(ValueError, match="0600"):
        smoke.load_provider_config(config_path)

    config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    config = smoke.load_provider_config(config_path)
    assert config == {
        "base_url": "https://relay.example/v1-root",
        "api_key": "sk-test-secret-not-for-evidence",
        "model": "gpt-test",
        "realtime_model": "gpt-realtime-test",
        "api_style": "responses",
    }


@pytest.mark.parametrize(
    "payload",
    [
        _config_payload(schema_version="wrong.v1"),
        _config_payload(api_style="chat_completions"),
        _config_payload(base_url="http://remote.example"),
        _config_payload(api_key=""),
        _config_payload(model=123),
        _config_payload(realtime_model=123),
        _config_payload(realtime_model=""),
        _config_payload(api_style=None),
        _config_payload(extra="forbidden"),
    ],
)
def test_provider_config_rejects_unsafe_or_non_responses_payloads(
    tmp_path: Path, payload: dict[str, object]
):
    config_path = _write_config(tmp_path / "provider.local.json", payload)

    with pytest.raises(ValueError):
        smoke.load_provider_config(config_path)


def test_provider_config_defaults_api_style_to_responses(tmp_path: Path):
    payload = _config_payload()
    payload.pop("api_style")
    config_path = _write_config(tmp_path / "provider.local.json", payload)

    assert smoke.load_provider_config(config_path)["api_style"] == "responses"


def test_provider_config_defaults_realtime_model_to_general_model(tmp_path: Path):
    payload = _config_payload()
    payload.pop("realtime_model")
    config_path = _write_config(tmp_path / "provider.local.json", payload)

    assert smoke.load_provider_config(config_path)["realtime_model"] == "gpt-test"


def test_config_can_require_gitignored_path_without_echoing_secret(
    tmp_path: Path, monkeypatch
):
    config_path = _write_config(tmp_path / "provider.local.json", _config_payload())
    monkeypatch.setattr(smoke, "_is_gitignored", lambda _path, _repo: False)

    with pytest.raises(ValueError, match="gitignored"):
        smoke.load_provider_config(
            config_path, repo_root=tmp_path, require_gitignored=True
        )


def test_local_responses_fixture_supports_streaming_and_records_safe_metadata():
    provider = smoke.LocalResponsesFixture(api_key="fixture-secret")
    provider.start()
    try:
        result = smoke.post_responses_fixture(
            provider.base_url,
            api_key="fixture-secret",
            model="fixture-model",
            prompt="只回复 OK",
        )
    finally:
        provider.stop()

    assert result["text"]
    assert result["usage"]["total_tokens"] > 0
    assert result["ttft_ms"] >= 0
    metadata = provider.metadata()
    assert metadata == {
        "request_count": 1,
        "authenticated_request_count": 1,
        "api_style": "responses",
        "is_mock": True,
        "gateway": "loopback_fixture",
        "usage_total_tokens": result["usage"]["total_tokens"],
    }


def test_evidence_redaction_rejects_secret_url_absolute_path_and_private_audio():
    raw = {
        "api_key": "sk-never-write",
        "authorization": "Bearer sk-never-write",
        "base_url": "https://relay.example/private/v1",
        "path": "/Users/chase/private.wav",
        "audio_text": "受控公开音频文本可以保留",
        "audio_bytes": "raw-private-audio",
    }

    redacted = smoke.redact_evidence(raw)
    encoded = json.dumps(redacted, ensure_ascii=False)
    assert "sk-never-write" not in encoded
    assert "Bearer" not in encoded
    assert "https://relay.example/private/v1" not in encoded
    assert "/Users/chase/private.wav" not in encoded
    assert "raw-private-audio" not in encoded
    assert redacted["audio_text"] == "受控公开音频文本可以保留"
    assert "audio_bytes" not in redacted


def test_summarize_remote_proof_requires_non_mock_usage_and_traces():
    summary = smoke.summarize_remote_proof(
        provider_health={
            "llm": {
                "configured": True,
                "is_mock": False,
                "api_style": "responses",
                "provider": "relay",
                "model": "gpt-test",
            }
        },
        slo={
            "token_usage": {"call_count": 4, "total_tokens": 123},
            "lanes": {
                "correction": {"count": 1},
                "intelligence": {"count": 1},
                "suggestion": {"count": 1},
            },
        },
        traces=[
            {
                "lane": "correction",
                "stages": {
                    "provider_connected": 1,
                    "first_token": 2,
                    "provider_completed": 3,
                },
            }
        ],
        events=[
            {"type": "meeting.intelligence.applied"},
            {"type": "transcript.correction.applied"},
        ],
    )

    assert summary["remote_llm_proof"] is True
    assert summary["remote_call_observed"] is True
    assert summary["is_mock"] is False
    assert summary["api_style"] == "responses"
    assert summary["gateway"] == "remote"
    assert summary["token_usage_total"] == 123
    assert summary["ttft_observation_count"] == 1


def test_remote_call_is_recorded_as_paid_even_when_timing_proof_is_missing():
    summary = smoke.summarize_remote_proof(
        provider_health={
            "llm": {
                "configured": True,
                "is_mock": False,
                "api_style": "responses",
            }
        },
        slo={
            "token_usage": {"call_count": 3, "total_tokens": 2474},
            "lanes": {"intelligence": {"count": 1}},
        },
        traces=[],
        events=[],
    )

    assert summary["remote_call_observed"] is True
    assert summary["remote_llm_proof"] is False


def test_trace_stage_shape_and_token_usage_are_preserved_in_remote_proof():
    traces = smoke._safe_traces(
        {
            "traces": [
                {
                    "lane": "correction",
                    "stages": {
                        "provider_connected": {"monotonic_ns": 10},
                        "first_token": {"monotonic_ns": 25},
                    },
                }
            ]
        }
    )
    evidence = smoke.redact_evidence(
        {"token_usage": {"total_tokens": 42}, "traces": traces}
    )

    assert traces[0]["stages"] == {"provider_connected": 10, "first_token": 25}
    assert evidence["token_usage"]["total_tokens"] == 42


def test_remote_proof_records_provider_total_latency():
    summary = smoke.summarize_remote_proof(
        provider_health={
            "llm": {"configured": True, "is_mock": False, "api_style": "responses"}
        },
        slo={
            "token_usage": {"call_count": 1, "total_tokens": 10},
            "lanes": {"correction": {"count": 1}},
        },
        traces=[
            {
                "stages": {
                    "provider_connected": 1_000_000,
                    "first_token": 1_500_000,
                    "provider_completed": 2_250_000,
                }
            }
        ],
        events=[],
    )

    assert summary["ttft_ms"] == [0.5]
    assert summary["provider_total_ms"] == [1.25]


def test_no_change_is_a_terminal_correction_without_a_fabricated_revision_event():
    segments = [
        {"correction_status": "no_change"},
        {"correction_status": "changed"},
        {"correction_status": "processing"},
    ]

    assert smoke.correction_terminal_count(segments) == 2


def _changed_segment(**overrides: object) -> dict[str, object]:
    segment: dict[str, object] = {
        "segment_id": "segment-1",
        "text": "接口先恢度百分之五。",
        "normalized_text": "接口先灰度百分之五。",
        "correction_status": "changed",
        "correction_before_text": "接口先恢度百分之五。",
        "correction_after_text": "接口先灰度百分之五。",
    }
    segment.update(overrides)
    return segment


def _revision_event(**payload_overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        **_changed_segment(),
        "original_text": "接口先恢度百分之五。",
        "corrected_text": "接口先灰度百分之五。",
    }
    payload.update(payload_overrides)
    return {
        "type": "transcript.segment.revised",
        "aggregate_type": "transcript_segment",
        "aggregate_id": "segment-1",
        "payload": payload,
    }


def test_require_changed_mode_rejects_an_all_no_change_run_as_no_go():
    segments = [
        {
            "segment_id": "segment-1",
            "text": "无需修正。",
            "normalized_text": "无需修正。",
            "correction_status": "no_change",
            "correction_before_text": "无需修正。",
            "correction_after_text": "无需修正。",
        }
    ]

    default_acceptance = smoke.summarize_correction_acceptance(
        segments=segments,
        events=[],
    )
    require_changed_acceptance = smoke.summarize_correction_acceptance(
        segments=segments,
        events=[],
        require_changed=True,
    )

    assert default_acceptance["passed"] is True
    assert require_changed_acceptance["required_changed"] is True
    assert require_changed_acceptance["changed_segment_count"] == 0
    assert require_changed_acceptance["changed_requirement_satisfied"] is False
    assert require_changed_acceptance["passed"] is False


def test_require_changed_mode_accepts_a_consistent_canonical_revision_and_audit():
    acceptance = smoke.summarize_correction_acceptance(
        segments=[_changed_segment()],
        events=[_revision_event()],
        require_changed=True,
    )

    assert acceptance == {
        "required_changed": True,
        "terminal_count": 1,
        "all_segments_terminal": True,
        "changed_segment_count": 1,
        "valid_changed_segment_count": 1,
        "revision_event_count": 1,
        "before_after_non_empty_and_different": True,
        "canonical_normalized_text_matches_after": True,
        "original_text_preserved": True,
        "revision_event_audit_observed": True,
        "changed_requirement_satisfied": True,
        "passed": True,
    }


@pytest.mark.parametrize(
    ("segments", "events", "failed_check"),
    [
        (
            [
                _changed_segment(
                    normalized_text="接口先灰度百分之五。",
                    correction_before_text="接口先灰度百分之五。",
                )
            ],
            [
                _revision_event(
                    original_text="接口先灰度百分之五。",
                    correction_before_text="接口先灰度百分之五。",
                )
            ],
            "before_after_non_empty_and_different",
        ),
        (
            [_changed_segment()],
            [],
            "revision_event_audit_observed",
        ),
        (
            [_changed_segment(normalized_text="伪造的 canonical 文字")],
            [_revision_event()],
            "canonical_normalized_text_matches_after",
        ),
    ],
)
def test_require_changed_mode_rejects_forged_changed_evidence(
    segments: list[dict[str, object]],
    events: list[dict[str, object]],
    failed_check: str,
):
    default_acceptance = smoke.summarize_correction_acceptance(
        segments=segments,
        events=events,
    )
    acceptance = smoke.summarize_correction_acceptance(
        segments=segments,
        events=events,
        require_changed=True,
    )

    assert default_acceptance["passed"] is False
    assert acceptance[failed_check] is False
    assert acceptance["valid_changed_segment_count"] == 0
    assert acceptance["changed_requirement_satisfied"] is False
    assert acceptance["passed"] is False


def test_require_changed_correction_cli_is_explicit_and_defaults_off():
    required = [
        "--app-path",
        "Meeting Copilot.app",
        "--audio-path",
        "controlled.wav",
        "--config",
        "provider.local.json",
        "--run-id",
        "next-016",
    ]

    assert smoke.parse_args(required).require_changed_correction is False
    assert (
        smoke.parse_args([*required, "--require-changed-correction"])
        .require_changed_correction
        is True
    )


def test_main_forwards_require_changed_correction_to_runner(monkeypatch, capsys):
    observed: dict[str, object] = {}

    def fake_run_smoke(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"decision": {"passed": False}}

    monkeypatch.setattr(smoke, "run_smoke", fake_run_smoke)

    exit_code = smoke.main(
        [
            "--app-path",
            "Meeting Copilot.app",
            "--audio-path",
            "controlled.wav",
            "--config",
            "provider.local.json",
            "--run-id",
            "next-016",
            "--require-changed-correction",
        ]
    )

    assert exit_code == 1
    assert observed["require_changed_correction"] is True
    assert json.loads(capsys.readouterr().out)["decision"]["passed"] is False


def test_require_changed_poll_waits_for_a_terminal_correction(monkeypatch):
    transcript_calls = 0

    def fake_request_json(
        _port: int, _method: str, path: str, **_kwargs: object
    ) -> tuple[int, dict[str, object]]:
        nonlocal transcript_calls
        if path.endswith("/snapshot"):
            return 200, {
                "follow_up": {"question": "谁负责回滚？"},
                "review_jobs": {"minutes": {"status": "succeeded"}},
            }
        if "/transcript?" in path:
            transcript_calls += 1
            status = "processing" if transcript_calls == 1 else "no_change"
            return 200, {
                "segments": [
                    {
                        "segment_id": "segment-1",
                        "correction_status": status,
                    }
                ]
            }
        if "/events?" in path:
            return 200, {"events": []}
        if path.endswith("/traces"):
            return 200, {"traces": []}
        if path.endswith("/realtime-ai-slo"):
            return 200, {}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(smoke, "_request_json", fake_request_json)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)

    _, transcript, _, _, _ = smoke._poll_mainline_state(
        12345,
        "session=cookie",
        "meeting-1",
        deadline_seconds=1,
        require_review=True,
        require_terminal_corrections=True,
    )

    assert transcript_calls == 2
    assert transcript["segments"][0]["correction_status"] == "no_change"


def test_packaged_launch_command_contains_no_provider_secret():
    binary = Path(
        "/Applications/Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop"
    )
    command = smoke.build_packaged_launch_command(binary, "sk-never-process-arg")

    assert command == [
        str(binary),
        "-ApplePersistenceIgnoreState",
        "YES",
        "-NSQuitAlwaysKeepsWindows",
        "NO",
    ]
    assert "sk-never-process-arg" not in command


def test_child_environment_drops_all_provider_secret_named_variables(tmp_path: Path):
    environment = smoke.build_child_environment(
        {
            "PATH": "/usr/bin",
            "API_KEY": "sk-api-key",
            "PROVIDER_SECRET": "secret",
            "OPENAI_AUTHORIZATION": "Bearer secret",
            "LLM_GATEWAY_MODEL": "model",
            "APP_MODE": "test",
        },
        home=tmp_path / "home",
        token="local-token",
    )

    assert environment["PATH"] == "/usr/bin"
    assert environment["APP_MODE"] == "test"
    assert "API_KEY" not in environment
    assert "PROVIDER_SECRET" not in environment
    assert "OPENAI_AUTHORIZATION" not in environment
    assert "LLM_GATEWAY_MODEL" not in environment
    assert environment["MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE"] == "local-token"
