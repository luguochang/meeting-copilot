"""Unit tests for the LLM execution service (real suggestion card generation).

Uses a fake LLM client — no network, no real gateway calls.
"""
from pathlib import Path

import pytest

from meeting_copilot_web_mvp import llm_service


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
    assert "transcript_correction" not in run


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
