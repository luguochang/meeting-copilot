"""Unit tests for the LLM execution service (real suggestion card generation).

Uses a fake LLM client — no network, no real gateway calls.
"""
import asyncio
from pathlib import Path

import pytest

from meeting_copilot_web_mvp import llm_service


class FakeAsyncClient:
    def __init__(self, response=None):
        self.response = response or {
            "choices": [{"message": {"content": "修正后的文本"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.is_closed = False
        self.calls = []

    async def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "json": json, "timeout": timeout})
        self.started.set()
        await self.release.wait()

        class Response:
            status_code = 200

            def json(self_inner):
                return self.response

        return Response()

    async def aclose(self):
        self.is_closed = True


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response or {
            "choices": [{"message": {"content": '{"suggestion_text":"建议确认 rollback 负责人","confidence":0.85,"trigger_reason":"rollback owner 缺失"}'}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }
        self.error = error
        self.calls = []

    def post_json(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.error:
            raise self.error
        return self.response


def _preview():
    return {
        "execution_id": "asr_llm_execution_preview_req_1",
        "request_id": "req_1",
        "target_candidate_id": "cand_1",
        "target_type": "Risk",
        "target_id": "risk_001",
        "gap_rule_id": "risk.rollback.validation",
        "evidence_span_ids": ["ev_1"],
        "source_event_ids": ["se_1"],
        "candidate_confidence": 0.6,
        "input_summary": "Risk risk_001 from seg_001 using ev_1",
        "suggested_prompt": "确认 rollback 验证",
    }


def test_async_provider_success_uses_shared_transport_and_returns_payload():
    async def run():
        transport = FakeAsyncClient()
        transport.release.set()
        client = llm_service.AsyncHttpxLlmClient(client=transport)
        handle = llm_service.ProviderAbortHandle()

        payload = await client.post_json(
            "https://gw.example/v1/chat/completions",
            {"Authorization": "Bearer redacted"},
            {"model": "m1", "messages": []},
            4.0,
            abort=handle,
        )

        assert payload["choices"][0]["message"]["content"] == "修正后的文本"
        assert handle.abort_requested is False
        assert handle.abort_acknowledged is False
        assert transport.calls[0]["timeout"] == 4.0

    asyncio.run(run())


def test_async_provider_abort_acknowledges_only_after_request_task_unwinds():
    async def run():
        transport = FakeAsyncClient()
        client = llm_service.AsyncHttpxLlmClient(client=transport)
        handle = llm_service.ProviderAbortHandle()
        task = asyncio.create_task(
            client.post_json(
                "https://gw.example/v1/chat/completions",
                {"Authorization": "Bearer redacted"},
                {"model": "m1", "messages": []},
                4.0,
                abort=handle,
            )
        )
        await transport.started.wait()
        assert handle.abort_acknowledged is False

        handle.request_abort("deadline")
        with pytest.raises(asyncio.CancelledError):
            await task

        assert handle.abort_requested is True
        assert handle.abort_acknowledged is True
        assert handle.reason == "deadline"

    asyncio.run(run())


def test_execute_candidate_creates_real_card_with_usage():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    fake = FakeClient()
    run = llm_service.execute_candidate(_preview(), config, client=fake)
    assert run["run_status"] == "completed"
    assert run["llm_call_status"] == "called"
    assert run["card_status"] == "new"
    card = run["card"]
    assert card["card_status"] == "new"
    assert card["suggestion_text"] == "建议确认 rollback 负责人"
    assert card["confidence"] == 0.85
    assert card["llm_trace"]["model"] == "m1"
    assert card["llm_trace"]["prompt_version"] == llm_service.PROMPT_VERSION
    assert card["llm_trace"]["usage"]["total_tokens"] == 160
    assert run["llm_usage"]["total_tokens"] == 160
    assert fake.calls[0]["url"] == "https://gw.example/v1/chat/completions"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-x"
    assert fake.calls[0]["body"]["model"] == "m1"
    assert fake.calls[0]["body"]["temperature"] == 0
    assert fake.calls[0]["body"]["reasoning_effort"] == "low"
    assert fake.calls[0]["body"]["max_completion_tokens"] == 512
    assert "thinking" not in fake.calls[0]["body"]
    assert "transcript_correction" not in run


def test_execute_candidate_uses_deepseek_max_tokens_for_deepseek_hostname():
    config = llm_service.LlmConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test-deepseek",
        model="deepseek-v4-flash",
    )
    fake = FakeClient()

    run = llm_service.execute_candidate(_preview(), config, client=fake)

    assert run["run_status"] == "completed"
    assert fake.calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert fake.calls[0]["body"]["max_tokens"] == 512
    assert "max_completion_tokens" not in fake.calls[0]["body"]
    assert fake.calls[0]["body"]["thinking"] == {"type": "disabled"}


def test_probe_gateway_uses_max_tokens_for_deepseek_labelled_relay():
    calls = []

    class ProbeClient:
        def post_json(self, url, headers, body, timeout):
            calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            }

    config = llm_service.LlmConfig(
        base_url="https://relay.example/deepseek",
        api_key="sk-test-deepseek",
        model="deepseek-v4-flash",
        provider_label="deepseek",
    )

    result = llm_service.probe_gateway(config, client=ProbeClient())

    assert result["operational"] is True
    assert calls[0]["url"] == "https://relay.example/deepseek/v1/chat/completions"
    assert calls[0]["body"]["max_tokens"] == 16
    assert "max_completion_tokens" not in calls[0]["body"]
    assert calls[0]["body"]["thinking"] == {"type": "disabled"}
    assert calls[0]["timeout"] == 10.0


def test_reasoning_compatibility_parameters_only_target_deepseek():
    deepseek = llm_service.LlmConfig(
        base_url="https://relay.example/api",
        api_key="sk-test-deepseek",
        model="deepseek-v4-flash",
        provider_label="deepseek",
    )
    other = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )

    assert llm_service._reasoning_compatibility_parameters(deepseek) == {
        "thinking": {"type": "disabled"}
    }
    assert llm_service._reasoning_compatibility_parameters(other) == {}


def test_execute_candidate_accepts_versioned_base_url_without_duplicate_v1():
    config = llm_service.LlmConfig(
        base_url="https://gw.example/v1/",
        api_key="sk-x",
        model="m1",
    )
    fake = FakeClient()

    run = llm_service.execute_candidate(_preview(), config, client=fake)

    assert config.base_url == "https://gw.example"
    assert run["run_status"] == "completed"
    assert fake.calls[0]["url"] == "https://gw.example/v1/chat/completions"


def test_llm_config_preserves_non_version_path_prefix():
    config = llm_service.LlmConfig(
        base_url="https://gw.example/v1-root/",
        api_key="sk-x",
        model="m1",
    )

    assert config.base_url == "https://gw.example/v1-root"


def test_execute_candidate_reuses_redacted_idempotency_header_across_retry(monkeypatch):
    class RetryOnceClient(FakeClient):
        def post_json(self, url, headers, body, timeout):
            self.calls.append({"url": url, "headers": dict(headers), "body": body})
            if len(self.calls) == 1:
                raise llm_service.LlmProviderTransportError("transport")
            return self.response

    monkeypatch.setattr(llm_service.time, "sleep", lambda _seconds: None)
    preview = {
        **_preview(),
        "idempotency_key": "live_asr_execution:private-meeting-id:req-1",
    }
    fake = RetryOnceClient()

    run = llm_service.execute_candidate(
        preview,
        llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        client=fake,
    )

    keys = [call["headers"]["Idempotency-Key"] for call in fake.calls]
    assert run["run_status"] == "completed"
    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert keys[0].startswith("meeting-copilot-")
    assert "private-meeting-id" not in keys[0]


def test_execute_candidate_exposes_provider_label_instead_of_raw_base_url():
    raw_base_url = "https://private-gateway.example/internal"
    config = llm_service.LlmConfig(
        base_url=raw_base_url,
        api_key="sk-x",
        model="m1",
        provider_label="team_gateway",
    )

    run = llm_service.execute_candidate(_preview(), config, client=FakeClient())

    assert run["provider"] == "team_gateway"
    assert run["card"]["llm_trace"]["provider"] == "team_gateway"
    assert raw_base_url not in str(run)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@gw.example/v1",
        "https://gw.example/v1?tenant=secret",
        "https://gw.example/v1#private-fragment",
    ],
)
def test_llm_config_rejects_url_userinfo_query_and_fragment(base_url):
    with pytest.raises(ValueError, match="LLM gateway base_url"):
        llm_service.LlmConfig(base_url=base_url, api_key="sk-x", model="m1")


def test_execute_candidate_returns_optional_single_segment_transcript_correction():
    response = {
        "choices": [{
            "message": {
                "content": (
                    '{"suggestion_text":"建议确认回滚负责人",'
                    '"confidence":0.88,'
                    '"trigger_reason":"回滚负责人缺失",'
                    '"corrected_transcript":"接口先灰度 5%，如果 P99 延迟超过 900 毫秒就回滚。"}'
                )
            }
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }
    preview = {
        **_preview(),
        "segment_batch": ["seg_1"],
        "evidence_span_ids": ["ev_1"],
        "evidence_spans": [{
            "id": "ev_1",
            "segment_id": "seg_1",
            "quote": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。",
            "start_ms": 0,
            "end_ms": 2_000,
            "status": "active",
        }],
    }

    run = llm_service.execute_candidate(
        preview,
        llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        client=FakeClient(response=response),
    )

    assert run["transcript_correction"] == {
        "segment_id": "seg_1",
        "evidence_span_id": "ev_1",
        "original_text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。",
        "corrected_text": "接口先灰度 5%，如果 P99 延迟超过 900 毫秒就回滚。",
        "source": "combined_suggestion",
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def test_execute_candidate_ignores_correction_when_evidence_crosses_segments():
    response = {
        "choices": [{"message": {"content": '{"suggestion_text":"建议确认", "confidence":0.8, "trigger_reason":"待确认", "corrected_transcript":"修正文本"}'}}],
        "usage": {"total_tokens": 1},
    }
    preview = {
        **_preview(),
        "segment_batch": ["seg_1", "seg_2"],
        "evidence_span_ids": ["ev_1", "ev_2"],
        "evidence_spans": [
            {"id": "ev_1", "segment_id": "seg_1", "quote": "第一段"},
            {"id": "ev_2", "segment_id": "seg_2", "quote": "第二段"},
        ],
    }

    run = llm_service.execute_candidate(
        preview,
        llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        client=FakeClient(response=response),
    )

    assert "transcript_correction" not in run


def test_build_enabled_execution_runs_success():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    fake = FakeClient()
    runs = llm_service.build_enabled_execution_runs([_preview()], config, client=fake)
    assert len(runs) == 1
    assert runs[0]["run_status"] == "completed"
    assert runs[0]["card"]["card_status"] == "new"


def test_build_enabled_execution_runs_handles_failure_without_aborting_batch():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    leaked = "https://private-gateway.example/v1?api_key=sk-super-secret"
    fake = FakeClient(error=RuntimeError(leaked))
    runs = llm_service.build_enabled_execution_runs([_preview(), _preview()], config, client=fake)
    assert len(runs) == 2
    assert all(r["run_status"] == "failed" for r in runs)
    assert all(r["llm_call_status"] == "error" for r in runs)
    assert all(r["card_status"] == "not_created" for r in runs)
    assert all(r["error_code"] == "llm_provider_failed" for r in runs)
    assert all(r["message"] == "LLM provider request failed" for r in runs)
    assert leaked not in str(runs)
    assert "sk-super-secret" not in str(runs)


def test_llm_config_from_env_returns_none_when_unset_and_no_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", tmp_path / "missing.env")
    assert llm_service.LlmConfig.from_env() is None


def test_llm_config_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example/")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    monkeypatch.setenv("LLM_GATEWAY_TIMEOUT_SECONDS", "30")
    cfg = llm_service.LlmConfig.from_env()
    assert cfg is not None
    assert cfg.base_url == "https://gw.example"  # trailing slash stripped
    assert cfg.api_key == "sk-x"
    assert cfg.model == "m1"
    assert cfg.timeout_seconds == 30.0


def test_explicit_realtime_model_env_is_prioritized_with_provenance(monkeypatch):
    llm_service.clear_runtime_config()
    monkeypatch.delenv("MEETING_COPILOT_DESKTOP_RUNTIME", raising=False)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "general-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "realtime-model")

    config = llm_service.LlmConfig.from_env()

    assert config is not None
    assert config.model == "general-model"
    assert config.realtime_model == "realtime-model"
    assert config.realtime_model_source == llm_service.REALTIME_MODEL_SOURCE_ENV
    assert llm_service.realtime_config(config).model == "realtime-model"
    assert llm_service.provider_metadata(config) == {
        "provider": "openai_compatible_gateway",
        "model": "general-model",
        "realtime_model": "realtime-model",
        "realtime_model_source": "llm_gateway_realtime_model",
        "realtime_model_explicit": True,
        "realtime_model_warning": None,
        "correction_model": "general-model",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "is_mock": False,
        "configured_from_env": True,
        "api_style": "chat_completions",
    }


def test_correction_model_does_not_inherit_explicit_realtime_model(monkeypatch):
    llm_service.clear_runtime_config()
    monkeypatch.delenv("MEETING_COPILOT_DESKTOP_RUNTIME", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_CORRECTION_MODEL", raising=False)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "general-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "realtime-mini")

    config = llm_service.LlmConfig.from_env()

    assert config is not None
    assert llm_service.realtime_config(config).model == "realtime-mini"
    assert llm_service.correction_config(config).model == "general-model"
    assert config.correction_model_source == llm_service.CORRECTION_MODEL_SOURCE_FALLBACK
    assert llm_service.correction_model_provenance(config) == {
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
    }


def test_explicit_correction_model_env_has_lane_provenance(monkeypatch):
    llm_service.clear_runtime_config()
    monkeypatch.delenv("MEETING_COPILOT_DESKTOP_RUNTIME", raising=False)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "general-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "realtime-mini")
    monkeypatch.setenv("LLM_GATEWAY_CORRECTION_MODEL", "correction-model")

    config = llm_service.LlmConfig.from_env()
    assert config is not None
    correction = llm_service.correction_config(config)
    audit = llm_service.provider_audit_metadata(
        correction,
        purpose="realtime_transcript_correction",
        provider_lane="correction",
        model_source=str(correction.correction_model_source),
    )

    assert correction.model == "correction-model"
    assert correction.correction_model_source == llm_service.CORRECTION_MODEL_SOURCE_ENV
    assert audit == {
        "provider": "openai_compatible_gateway",
        "model": "correction-model",
        "purpose": "realtime_transcript_correction",
        "provider_lane": "correction",
        "model_source": "llm_gateway_correction_model",
    }


def test_runtime_correction_model_is_selected_without_inheriting_realtime(monkeypatch):
    llm_service.clear_runtime_config()
    monkeypatch.delenv("MEETING_COPILOT_DESKTOP_RUNTIME", raising=False)
    config_metadata = llm_service.configure_runtime(
        base_url="https://gw.example",
        api_key="sk-runtime-correction-test",
        model="general-model",
        realtime_model="realtime-model",
        correction_model="correction-model",
    )
    config = llm_service.LlmConfig.from_env()
    assert config is not None
    assert config.realtime_model == "realtime-model"
    assert config.correction_model == "correction-model"
    assert config.correction_model_source == llm_service.CORRECTION_MODEL_SOURCE_RUNTIME
    assert llm_service.correction_config(config).model == "correction-model"
    assert config_metadata["correction_model"] == "correction-model"
    assert config_metadata["correction_model_source"] == "runtime_correction_model"
    assert config_metadata["correction_model_explicit"] is True
    assert config_metadata["correction_model_warning"] is None
    llm_service.clear_runtime_config()


def test_inherited_realtime_model_is_observable_and_warned_once(monkeypatch):
    events = []

    class CapturingLogger:
        def warning(self, event, **values):
            events.append((event, values))

    config = llm_service.LlmConfig(
        base_url="https://private-gateway.example",
        api_key="sk-must-not-appear",
        model="general-model",
    )
    monkeypatch.setattr(llm_service, "_log", CapturingLogger())
    with llm_service._REALTIME_MODEL_WARNING_LOCK:
        llm_service._REALTIME_MODEL_WARNING_FINGERPRINTS.clear()

    first = llm_service.realtime_config(config)
    second = llm_service.realtime_config(config)
    metadata = llm_service.provider_metadata(config)

    assert first.model == "general-model"
    assert second.model == "general-model"
    assert config.realtime_model_source == llm_service.REALTIME_MODEL_SOURCE_FALLBACK
    assert metadata["realtime_model_explicit"] is False
    assert metadata["realtime_model_warning"] == "realtime_model_inherits_general_model"
    assert events == [
        (
            "llm.realtime_model.inherits_general_model",
            {
                "diagnostic_code": "realtime_model_inherits_general_model",
                "provider": "openai_compatible_gateway",
                "selected_model": "general-model",
                "realtime_model_source": "general_model_fallback",
            },
        )
    ]
    assert "sk-must-not-appear" not in str(events)
    assert "private-gateway.example" not in str(events)


def test_runtime_realtime_model_entry_keeps_explicit_source():
    llm_service.clear_runtime_config()
    try:
        metadata = llm_service.configure_runtime(
            base_url="https://gw.example",
            api_key="sk-runtime",
            model="general-model",
            realtime_model="runtime-realtime-model",
        )
        config = llm_service.LlmConfig.from_env()

        assert config is not None
        assert config.realtime_model_source == llm_service.REALTIME_MODEL_SOURCE_RUNTIME
        assert llm_service.realtime_config(config).model == "runtime-realtime-model"
        assert metadata["realtime_model_source"] == "runtime_realtime_model"
        assert metadata["realtime_model_explicit"] is True
        assert metadata["realtime_model_warning"] is None
    finally:
        llm_service.clear_runtime_config()


def test_realtime_model_source_and_selection_cannot_disagree():
    fallback = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-runtime",
        model="general-model",
        realtime_model="different-model",
        realtime_model_source=llm_service.REALTIME_MODEL_SOURCE_FALLBACK,
    )

    assert fallback.realtime_model == "general-model"
    with pytest.raises(ValueError, match="explicit LLM realtime model is missing"):
        llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-runtime",
            model="general-model",
            realtime_model_source=llm_service.REALTIME_MODEL_SOURCE_RUNTIME,
        )


@pytest.mark.parametrize(
    "base_url",
    ["https://gw.example/v1", "https://gw.example/v1/"],
)
def test_llm_config_from_env_normalizes_terminal_v1(monkeypatch, base_url):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", base_url)
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-x")

    cfg = llm_service.LlmConfig.from_env()

    assert cfg is not None
    assert cfg.base_url == "https://gw.example"


def test_llm_config_from_env_does_not_read_dotenv_when_process_env_is_complete(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-x")
    monkeypatch.setattr(
        llm_service,
        "load_dotenv",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dotenv must not be read")),
    )

    cfg = llm_service.LlmConfig.from_env()

    assert cfg is not None
    assert cfg.base_url == "https://gw.example"


def test_llm_config_from_env_loads_repo_dotenv_when_process_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_MODEL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_TIMEOUT_SECONDS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LLM_GATEWAY_BASE_URL=https://dotenv.example/",
                "LLM_GATEWAY_API_KEY=sk-dotenv",
                "LLM_GATEWAY_MODEL=dotenv-model",
                "LLM_GATEWAY_TIMEOUT_SECONDS=45",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", Path(env_file))

    cfg = llm_service.LlmConfig.from_env()

    assert cfg is not None
    assert cfg.base_url == "https://dotenv.example"
    assert cfg.api_key == "sk-dotenv"
    assert cfg.model == "dotenv-model"
    assert cfg.timeout_seconds == 45.0


def test_httpx_llm_client_does_not_inherit_system_proxy(monkeypatch):
    created = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class FakeHttpxClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers, json):
            return FakeResponse()

    monkeypatch.setattr(llm_service.httpx, "Client", FakeHttpxClient)

    body = llm_service.HttpxLlmClient().post_json(
        "http://127.0.0.1:18767/v1/chat/completions",
        headers={},
        body={},
        timeout=5,
    )

    assert body == {"ok": True}
    assert created["timeout"] == 5
    assert created["trust_env"] is False


def test_httpx_llm_client_uses_explicit_responses_style_and_normalizes_result(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "resp_1",
                "model": "gpt-5.5",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "OK"}],
                    }
                ],
                "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            }

    class FakeHttpxClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers, json):
            calls.append((url, headers, json))
            return FakeResponse()

    monkeypatch.setattr(llm_service.httpx, "Client", FakeHttpxClient)
    result = llm_service.HttpxLlmClient(api_style="responses").post_json(
        "https://gateway.example/v1/chat/completions",
        headers={"Authorization": "Bearer redacted"},
        body={
            "model": "gpt-5.5",
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "reasoning_effort": "low",
            "max_completion_tokens": 16,
        },
        timeout=5,
    )

    assert calls[0][0] == "https://gateway.example/v1/responses"
    assert calls[0][2]["input"] == [{"role": "user", "content": "只回复 OK"}]
    assert result["choices"][0]["message"]["content"] == "OK"
    assert result["usage"]["total_tokens"] == 5


def test_httpx_llm_client_reports_redacted_authentication_error(monkeypatch):
    class FakeResponse:
        status_code = 401

        def json(self):
            return {
                "code": "INVALID_API_KEY",
                "message": "credential sk-should-never-escape is invalid",
            }

    class FakeHttpxClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, _url, headers, json):
            return FakeResponse()

    monkeypatch.setattr(llm_service.httpx, "Client", FakeHttpxClient)

    with pytest.raises(llm_service.LlmProviderHttpError) as caught:
        llm_service.HttpxLlmClient(api_style="responses").post_json(
            "https://gateway.example/v1/chat/completions",
            headers={"Authorization": "Bearer redacted"},
            body={"model": "gpt-5.5", "messages": [{"role": "user", "content": "x"}]},
            timeout=5,
        )

    assert caught.value.status_code == 401
    assert caught.value.category == "authentication"
    assert caught.value.retryable is False
    assert caught.value.provider_code == "INVALID_API_KEY"
    assert caught.value.api_style == "responses"
    message = llm_service.provider_failure_message(caught.value)
    assert "HTTP 401" in message
    assert "INVALID_API_KEY" in message
    assert "Responses" in message
    assert "Chat Completions" in message
    assert "sk-should-never-escape" not in message


def test_call_with_retry_does_not_retry_permanent_provider_error(monkeypatch):
    fake = FakeClient(error=llm_service.LlmProviderHttpError(400, provider_code="INVALID_MODEL"))
    monkeypatch.setattr(llm_service.time, "sleep", lambda _seconds: None)

    with pytest.raises(llm_service.LlmProviderHttpError):
        llm_service._call_with_retry(fake, "https://gateway.example", {}, {}, 5, retries=2)

    assert len(fake.calls) == 1
