"""Unit tests for the LLM execution service (real suggestion card generation).

Uses a fake LLM client — no network, no real gateway calls.
"""
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


def test_build_enabled_execution_runs_success():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    fake = FakeClient()
    runs = llm_service.build_enabled_execution_runs([_preview()], config, client=fake)
    assert len(runs) == 1
    assert runs[0]["run_status"] == "completed"
    assert runs[0]["card"]["card_status"] == "new"


def test_build_enabled_execution_runs_handles_failure_without_aborting_batch():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    fake = FakeClient(error=RuntimeError("network down"))
    runs = llm_service.build_enabled_execution_runs([_preview(), _preview()], config, client=fake)
    assert len(runs) == 2
    assert all(r["run_status"] == "failed" for r in runs)
    assert all(r["llm_call_status"] == "error" for r in runs)
    assert all(r["card_status"] == "not_created" for r in runs)
    assert all("error" in r for r in runs)


def test_llm_config_from_env_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
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
