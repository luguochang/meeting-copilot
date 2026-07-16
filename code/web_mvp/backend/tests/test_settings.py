from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import asr_stream, llm_service
from meeting_copilot_web_mvp.app import create_app


DEFAULT_SETTINGS = {
    "asr": {
        "l2_correction_enabled": True,
        "l3_normalize_enabled": True,
    },
    "suggestions": {
        "enabled": True,
        "window_seconds": 20,
        "cooldown_minutes": 5,
        "confidence_threshold": 0.7,
    },
    "budget": {
        "session_limit_cny": 10.0,
        "daily_limit_cny": 50.0,
        "l3_value_policy": "when_needed",
    },
}


UPDATED_SETTINGS = {
    "asr": {
        "l2_correction_enabled": True,
        "l3_normalize_enabled": False,
    },
    "suggestions": {
        "enabled": False,
        "window_seconds": 45,
        "cooldown_minutes": 8,
        "confidence_threshold": 0.82,
    },
    "budget": {
        "session_limit_cny": 12.5,
        "daily_limit_cny": 75.0,
        "l3_value_policy": "always",
    },
}


def _live_session_payload(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "provider": "local_mock_asr",
        "streaming_events": [
            {
                "event_type": "final",
                "segment_id": "seg_001",
                "text": "checkout-service 先灰度 5%，张三负责回滚。",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3400,
                "confidence": 0.95,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "eos",
                "text": "",
                "start_ms": 3200,
                "end_ms": 3200,
                "received_at_ms": 3500,
            },
        ],
    }


class _FakeMinutesClient:
    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, url, headers, body, timeout):
        self.calls += 1
        system_prompt = str(body["messages"][0]["content"])
        if "方案考量生成器" in system_prompt:
            content = (
                '[{"card_type":"approach.consideration",'
                '"suggestion_text":"建议确认回滚负责人",'
                '"confidence":0.9,"trigger_reason":"灰度方案待闭环",'
                '"evidence_quote":"checkout-service 先灰度 5%"}]'
            )
        elif "建议生成器" in system_prompt:
            content = (
                '{"suggestion_text":"建议确认回滚负责人",'
                '"confidence":0.9,"trigger_reason":"负责人未确认",'
                '"corrected_transcript":null}'
            )
        else:
            content = (
                '{"background":"发布评审","decisions":["先灰度 5%"],'
                '"action_items":[],"risks":[],"open_questions":[],'
                '"evidence_quotes":["checkout-service 先灰度 5%"]}'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }


class _SettingsAutoSuggestionRecognizer:
    provider = "test_contract_realtime_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id: str, *, confidence: float = 0.95) -> None:
        self.session_id = session_id
        self.confidence = confidence
        self._seq = 0

    def recognize_chunk(self, pcm):
        self._seq += 1
        return [{
            "event_type": "partial",
            "segment_id": "settings_auto_seg",
            "text": "checkout-service 先灰度",
            "start_ms": 0,
            "end_ms": 300,
            "confidence": self.confidence,
        }]

    def finalize(self):
        return [{
            "event_type": "final",
            "segment_id": "settings_auto_seg",
            "text": "checkout-service 先灰度 5%，还没有确认谁负责监控和回滚。",
            "start_ms": 0,
            "end_ms": 6200,
            "confidence": self.confidence,
        }]


def _configure_real_provider(monkeypatch, fake: _FakeMinutesClient) -> None:
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-settings-test-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "settings-test-model")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "settings-test-provider")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)


def _create_live_session(client: TestClient, session_id: str) -> None:
    response = client.post("/live/asr/mock/sessions", json=_live_session_payload(session_id))
    assert response.status_code == 201


def _create_realtime_auto_suggestion_session(
    monkeypatch,
    client: TestClient,
    session_id: str,
    *,
    confidence: float = 0.95,
) -> None:
    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: _SettingsAutoSuggestionRecognizer(sid, confidence=confidence),
    )
    monkeypatch.setattr(
        asr_stream,
        "_correct_transcript",
        lambda raw, cfg: (raw, {"total_tokens": 0}, False),
    )
    with client.websocket_connect(
        f"/live/asr/stream/ws/{session_id}?audio_source=browser_live_mic"
    ) as websocket:
        websocket.send_bytes(b"\x00" * 3200)
        websocket.send_text("END")
        while True:
            event = json.loads(websocket.receive_text())
            if event.get("event_type") == "final":
                break


def _generate_minutes(client: TestClient, session_id: str):
    return client.post(
        f"/live/asr/demo/sessions/{session_id}/minutes",
        json={"mode": "enabled"},
    )


def test_settings_defaults_match_the_fixed_non_sensitive_contract():
    response = TestClient(create_app()).get("/settings")

    assert response.status_code == 200
    assert response.json() == DEFAULT_SETTINGS


def test_settings_patch_persists_in_sqlite_and_in_memory_apps_are_isolated(tmp_path):
    data_dir = tmp_path / "data"
    first = TestClient(create_app(data_dir=data_dir))

    patched = first.patch("/settings", json=UPDATED_SETTINGS)

    assert patched.status_code == 200
    assert patched.json() == UPDATED_SETTINGS
    assert TestClient(create_app(data_dir=data_dir)).get("/settings").json() == UPDATED_SETTINGS

    memory_one = TestClient(create_app())
    memory_two = TestClient(create_app())
    assert memory_one.patch("/settings", json=UPDATED_SETTINGS).json() == UPDATED_SETTINGS
    assert memory_two.get("/settings").json() == DEFAULT_SETTINGS


def test_l3_normalization_setting_is_snapshotted_for_each_new_websocket(monkeypatch):
    snapshots = []

    async def capture_stream(websocket, session_id, **kwargs):
        snapshots.append(kwargs.get("l3_normalize_enabled"))
        await websocket.accept()
        await websocket.send_text(json.dumps({"event_type": "captured"}))
        await websocket.close()

    monkeypatch.setattr(asr_stream, "handle_stream", capture_stream)
    client = TestClient(create_app())

    with client.websocket_connect("/live/asr/stream/ws/l3_default") as websocket:
        assert json.loads(websocket.receive_text())["event_type"] == "captured"

    disabled = {
        **DEFAULT_SETTINGS,
        "asr": {
            **DEFAULT_SETTINGS["asr"],
            "l3_normalize_enabled": False,
        },
    }
    assert client.patch("/settings", json=disabled).status_code == 200

    with client.websocket_connect("/live/asr/stream/ws/l3_disabled") as websocket:
        assert json.loads(websocket.receive_text())["event_type"] == "captured"

    assert snapshots == [True, False]


def test_disabling_suggestions_applies_to_existing_session_on_next_run_once(
    monkeypatch,
    tmp_path,
):
    fake = _FakeMinutesClient()
    _configure_real_provider(monkeypatch, fake)
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "settings_disable_existing_session"
    _create_realtime_auto_suggestion_session(monkeypatch, client, session_id)
    disabled = {
        **DEFAULT_SETTINGS,
        "suggestions": {
            **DEFAULT_SETTINGS["suggestions"],
            "enabled": False,
        },
    }

    patched = client.patch("/settings", json=disabled)
    response = client.post(
        f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
    )

    assert patched.status_code == 200
    assert response.status_code == 200
    assert response.json()["reason"] == "disabled_by_setting"
    assert response.json()["status"]["effective_policy"]["enabled"] is False
    assert response.json()["status"]["last_suppression_reason"] == "disabled_by_setting"
    assert response.json()["generated_card_count"] == 0
    assert fake.calls == 0

    reenabled = client.patch("/settings", json=DEFAULT_SETTINGS)
    generated = client.post(
        f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
    )

    assert reenabled.status_code == 200
    assert generated.status_code == 200
    assert generated.json()["generated_card_count"] == 1
    assert generated.json()["status"]["effective_policy"]["enabled"] is True
    assert fake.calls == 1


def test_suggestion_runtime_policy_survives_sqlite_app_restart(
    monkeypatch,
    tmp_path,
):
    fake = _FakeMinutesClient()
    _configure_real_provider(monkeypatch, fake)
    session_id = "settings_runtime_policy_restart"
    strict_settings = {
        **DEFAULT_SETTINGS,
        "suggestions": {
            "enabled": True,
            "window_seconds": 45,
            "cooldown_minutes": 8,
            "confidence_threshold": 0.99,
        },
    }

    with TestClient(create_app(data_dir=tmp_path)) as first:
        _create_realtime_auto_suggestion_session(
            monkeypatch,
            first,
            session_id,
            confidence=0.95,
        )
        assert first.patch("/settings", json=strict_settings).status_code == 200

    with TestClient(create_app(data_dir=tmp_path)) as restarted:
        response = restarted.post(
            f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
        )
        status = restarted.get(
            f"/live/asr/sessions/{session_id}/auto-suggestions/status"
        ).json()["status"]

    assert response.status_code == 200
    assert response.json()["reason"] == "low_confidence"
    assert response.json()["generated_card_count"] == 0
    assert fake.calls == 0
    assert status["effective_policy"] == {
        "policy_version": "auto-suggestion-orchestrator.v2",
        "enabled": True,
        "degradation_level": 0,
        "user_confidence_threshold": 0.99,
        "effective_confidence_threshold": 0.99,
        "window_seconds": 45,
        "window_ms": 45_000,
        "cooldown_minutes": 8,
        "cooldown_ms": 480_000,
        "reservation_lease_seconds": 60,
        "reservation_lease_ms": 60_000,
    }
    assert status["last_suppression_reason"] == "low_confidence"


def test_auto_suggestion_status_get_overlays_current_settings_without_writing_session(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    repository = app.state.asr_live_repository
    session_id = "settings_status_refresh"
    _create_live_session(client, session_id)

    before_initial_get = repository.get(session_id)
    initial = client.get(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status"
    )
    after_initial_get = repository.get(session_id)
    strict_settings = {
        **DEFAULT_SETTINGS,
        "suggestions": {
            "enabled": True,
            "window_seconds": 45,
            "cooldown_minutes": 8,
            "confidence_threshold": 0.99,
        },
    }
    assert client.patch("/settings", json=strict_settings).status_code == 200
    before_refreshed_get = repository.get(session_id)
    refreshed = client.get(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status"
    )
    after_refreshed_get = repository.get(session_id)

    assert initial.status_code == 200
    assert initial.json()["status"]["effective_policy"]["enabled"] is True
    assert initial.json()["status"]["effective_policy"]["window_seconds"] == 20
    assert initial.json()["status"]["last_evaluated_at_ms"] == 0
    assert initial.json()["status"]["suppressed"] == []
    assert after_initial_get == before_initial_get
    assert refreshed.status_code == 200
    assert refreshed.json()["status"]["effective_policy"]["window_seconds"] == 45
    assert refreshed.json()["status"]["effective_policy"]["cooldown_minutes"] == 8
    assert (
        refreshed.json()["status"]["effective_policy"][
            "effective_confidence_threshold"
        ]
        == 0.99
    )
    assert refreshed.json()["status"]["last_evaluated_at_ms"] == 0
    assert refreshed.json()["status"]["suppressed"] == []
    assert after_refreshed_get == before_refreshed_get


@pytest.mark.parametrize(
    "payload",
    [
        {**DEFAULT_SETTINGS, "apiKey": "sk-secret"},
        {**DEFAULT_SETTINGS, "api_key": "sk-secret"},
        {**DEFAULT_SETTINGS, "base_url": "https://secret.example"},
        {**DEFAULT_SETTINGS, "credential": "secret"},
        {**DEFAULT_SETTINGS, "secret": "secret"},
        {
            **DEFAULT_SETTINGS,
            "asr": {**DEFAULT_SETTINGS["asr"], "api_key": "sk-secret"},
        },
    ],
)
def test_settings_reject_sensitive_or_extra_fields_without_persisting_them(tmp_path, payload):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.patch("/settings", json=payload)

    assert response.status_code == 422
    assert "sk-secret" not in response.text
    assert "secret.example" not in response.text
    assert client.get("/settings").json() == DEFAULT_SETTINGS
    database_bytes = (tmp_path / "meeting_copilot.db").read_bytes().lower()
    assert b"sk-secret" not in database_bytes
    assert b"secret.example" not in database_bytes


def test_real_enabled_provider_usage_is_persisted_and_missing_rates_are_unavailable(
    monkeypatch,
    tmp_path,
):
    fake = _FakeMinutesClient()
    _configure_real_provider(monkeypatch, fake)
    monkeypatch.delenv("LLM_PROMPT_CNY_PER_1M_TOKENS", raising=False)
    monkeypatch.delenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", raising=False)
    client = TestClient(create_app(data_dir=tmp_path))
    _create_live_session(client, "settings_usage_unavailable")

    generated = _generate_minutes(client, "settings_usage_unavailable")
    stats = client.get("/settings/cost-stats")

    assert generated.status_code == 200
    assert fake.calls == 1
    assert stats.status_code == 200
    assert stats.json()["currentSession"] is None
    assert stats.json()["today"] is None
    assert stats.json()["month"] is None
    assert stats.json()["costStatus"] == "unavailable"
    assert stats.json()["breakdown"] == [
        {
            "name": "minutes_markdown",
            "purpose": "minutes_markdown",
            "provider": "settings-test-provider",
            "model": "settings-test-model",
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "tokens": 30,
            "estimated_cost_cny": None,
            "costStatus": "unavailable",
        }
    ]

    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        row = connection.execute(
            "SELECT session_id, purpose, provider, model, prompt_tokens, "
            "completion_tokens, total_tokens, timestamp_ms FROM llm_usage_ledger"
        ).fetchone()
    assert row[:7] == (
        "settings_usage_unavailable",
        "minutes_markdown",
        "settings-test-provider",
        "settings-test-model",
        10,
        20,
        30,
    )
    assert row[7] > 0
    assert b"sk-settings-test-secret" not in (tmp_path / "meeting_copilot.db").read_bytes()


@pytest.mark.parametrize(
    ("budget_limits", "expected_scope"),
    [
        ({"session_limit_cny": 1.0, "daily_limit_cny": 50.0}, "session"),
        ({"session_limit_cny": 10.0, "daily_limit_cny": 1.0}, "daily"),
    ],
)
def test_cost_stats_are_estimated_from_explicit_rates_and_budget_gate_blocks_before_call(
    monkeypatch,
    tmp_path,
    budget_limits,
    expected_scope,
):
    fake = _FakeMinutesClient()
    _configure_real_provider(monkeypatch, fake)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "100000")
    client = TestClient(create_app(data_dir=tmp_path))
    _create_live_session(client, "settings_budget_gate")

    first = _generate_minutes(client, "settings_budget_gate")
    assert first.status_code == 200
    assert fake.calls == 1

    limited = {
        **DEFAULT_SETTINGS,
        "budget": {
            **DEFAULT_SETTINGS["budget"],
            **budget_limits,
        },
    }
    assert client.patch("/settings", json=limited).status_code == 200

    blocked = _generate_minutes(client, "settings_budget_gate")
    stats = client.get("/settings/cost-stats").json()

    assert blocked.status_code == 429
    assert blocked.json()["detail"]["error"] == "llm_budget_exceeded"
    assert blocked.json()["detail"]["scope"] == expected_scope
    assert fake.calls == 1
    assert stats["currentSession"] == pytest.approx(3.0)
    assert stats["today"] == pytest.approx(3.0)
    assert stats["month"] == pytest.approx(3.0)
    assert stats["costStatus"] == "estimated"
    assert stats["estimated"] is True
    assert stats["breakdown"][0]["estimated_cost_cny"] == pytest.approx(3.0)
    assert stats["breakdown"][0]["costStatus"] == "estimated"


def test_deterministic_and_mock_providers_do_not_enter_the_usage_ledger(monkeypatch, tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    _create_live_session(client, "settings_no_cost_demo")

    deterministic = client.post(
        "/live/asr/demo/sessions/settings_no_cost_demo/minutes",
        json={"mode": "deterministic_demo"},
    )
    assert deterministic.status_code == 200

    fake = _FakeMinutesClient()
    _configure_real_provider(monkeypatch, fake)
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "true")
    mock_enabled = _generate_minutes(client, "settings_no_cost_demo")

    assert mock_enabled.status_code == 200
    assert fake.calls == 1
    assert client.get("/settings/cost-stats").json()["breakdown"] == []
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        count = connection.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0]
    assert count == 0


def test_formal_suggestions_approach_and_minutes_record_distinct_usage_purposes(
    monkeypatch,
    tmp_path,
):
    fake = _FakeMinutesClient()
    _configure_real_provider(monkeypatch, fake)
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "settings_formal_boundaries"
    response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": session_id,
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "seg_001",
                    "text": "checkout-service 先灰度 5%。",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "received_at_ms": 3200,
                    "confidence": 0.95,
                },
                {
                    "event_type": "final",
                    "segment_id": "seg_002",
                    "text": "还没有确认谁负责回滚。",
                    "start_ms": 3000,
                    "end_ms": 6000,
                    "received_at_ms": 6300,
                    "confidence": 0.95,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "eos",
                    "text": "",
                    "start_ms": 6000,
                    "end_ms": 6000,
                    "received_at_ms": 6400,
                },
            ],
        },
    )
    assert response.status_code == 201

    suggestions = client.post(
        f"/live/asr/demo/sessions/{session_id}/llm-execution-runs",
        json={"mode": "enabled"},
    )
    approach = client.post(
        f"/live/asr/demo/sessions/{session_id}/approach-cards",
        json={"mode": "enabled"},
    )
    minutes = _generate_minutes(client, session_id)

    assert suggestions.status_code == 200
    assert suggestions.json()["run_count"] > 0
    assert approach.status_code == 200
    assert minutes.status_code == 200
    purposes = {
        item["purpose"]
        for item in client.get("/settings/cost-stats").json()["breakdown"]
    }
    assert {"formal_suggestion", "approach_cards", "minutes_markdown"} <= purposes


def test_cost_is_unavailable_when_provider_omits_prompt_completion_split(monkeypatch, tmp_path):
    fake = _FakeMinutesClient()
    original_post_json = fake.post_json

    def total_only_post_json(url, headers, body, timeout):
        response = original_post_json(url, headers, body, timeout)
        response["usage"] = {"total_tokens": 30}
        return response

    monkeypatch.setattr(fake, "post_json", total_only_post_json)
    _configure_real_provider(monkeypatch, fake)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "100000")
    client = TestClient(create_app(data_dir=tmp_path))
    _create_live_session(client, "settings_incomplete_usage")

    assert _generate_minutes(client, "settings_incomplete_usage").status_code == 200
    stats = client.get("/settings/cost-stats").json()

    assert stats["currentSession"] is None
    assert stats["today"] is None
    assert stats["month"] is None
    assert stats["costStatus"] == "unavailable"
    assert stats["breakdown"][0]["estimated_cost_cny"] is None
    assert stats["breakdown"][0]["costStatus"] == "unavailable"
