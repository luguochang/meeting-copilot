"""Background accounting keys never become meeting IDs or filesystem names."""
from __future__ import annotations

import asyncio

import pytest

from meeting_copilot_web_mvp.sqlite_repository import (
    InMemorySettingsUsageRepository,
    SqliteSettingsUsageRepository,
    _validate_session_id,
)


PROJECT_ID = "project_" + "0123456789abcdef" * 2


@pytest.fixture(params=["sqlite", "memory"])
def ledger(request, tmp_path):
    if request.param == "sqlite":
        repository = SqliteSettingsUsageRepository(tmp_path, {})
    else:
        repository = InMemorySettingsUsageRepository({})
    yield repository
    if request.param == "sqlite":
        repository.close()


def record(repository, account_id, *, timestamp=1000):
    return repository.record_usage(
        session_id=account_id, purpose="fixture", provider="fixture", model="fixture",
        prompt_tokens=10, completion_tokens=20, total_tokens=30, timestamp_ms=timestamp,
    )


@pytest.mark.parametrize("account_id", ["ordinary_session-1.2", "memory:" + PROJECT_ID, "preparation:" + PROJECT_ID])
def test_usage_record_filter_and_restart_preserve_the_exact_account(ledger, account_id):
    saved = record(ledger, account_id)
    record(ledger, "other_meeting", timestamp=2000)
    assert saved["session_id"] == account_id
    assert ledger.list_usage(session_id=account_id) == [saved]
    assert ledger.list_usage(session_id=account_id, since_ms=1001) == []
    if isinstance(ledger, SqliteSettingsUsageRepository):
        reopened = SqliteSettingsUsageRepository(ledger._data_dir, {})
        try:
            assert reopened.list_usage(session_id=account_id) == [saved]
        finally:
            reopened.close()


@pytest.mark.parametrize("account_id", [
    "memory:project_" + "a" * 31,
    "preparation:project_" + "a" * 33,
    "memory:project_" + "A" * 32,
    "preparation:project_" + "g" * 32,
    "memory:project_" + "a" * 32 + ":extra",
    "memory:../" + PROJECT_ID,
    "preparation:" + PROJECT_ID + "/../escape",
    "memory:" + PROJECT_ID + "\\escape",
    "preparation:C:\\private",
    "memory:C:" + PROJECT_ID,
    "memory:CON", "preparation:NUL", "memory:COM1", "preparation:PRN",
    "memory:project_CON", "preparation:project_LPT1",
    "memory:" + PROJECT_ID + " ", "memory:" + PROJECT_ID + "\n",
    "other:" + PROJECT_ID, "Memory:" + PROJECT_ID, "memory:synthetic-project",
])
def test_usage_rejects_invalid_scoped_accounts_for_writes_and_reads(ledger, account_id):
    with pytest.raises(ValueError):
        record(ledger, account_id)
    with pytest.raises(ValueError):
        ledger.list_usage(session_id=account_id)
    assert ledger.list_usage() == []


@pytest.mark.parametrize("kind", ["memory", "preparation"])
def test_background_account_is_still_rejected_by_the_meeting_path_validator(kind):
    with pytest.raises(ValueError):
        _validate_session_id(kind + ":" + PROJECT_ID)
    assert _validate_session_id("ordinary_session-1.2") == "ordinary_session-1.2"


@pytest.mark.parametrize("storage", ["sqlite", "json_session"])
@pytest.mark.parametrize("kind", ["memory", "preparation"])
@pytest.mark.parametrize("outcome", ["success", "deadline"])
def test_real_app_records_and_budget_reads_the_same_background_account_without_a_meeting(tmp_path, monkeypatch, storage, kind, outcome):
    # app's module-level runtime must be isolated even when this file runs alone.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "isolated-module-runtime"))
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "100000")
    from meeting_copilot_web_mvp import llm_service
    from meeting_copilot_web_mvp.app import create_app
    from meeting_copilot_web_mvp.project_reconcile_jobs import BackgroundReconcileDeferred
    from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachRuntimeError
    from meeting_copilot_web_mvp.repository import JsonFileSessionRepository

    config = llm_service.LlmConfig(base_url="https://unused.invalid", api_key="fixture-never-sent", model="gpt-6-sol", is_mock=False)
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda _: config))
    if storage == "sqlite":
        app = create_app(data_dir=tmp_path / "app", prewarm_funasr=False)
    else:
        app = create_app(repository=JsonFileSessionRepository(tmp_path / "json-sessions"), prewarm_funasr=False)
    usage = app.state.settings_usage_repository
    account = kind + ":" + PROJECT_ID
    run = (app.state.run_memory_reconcile_with_admission if kind == "memory"
           else app.state.run_preparation_with_admission)
    calls = []

    async def callback(payload, job, provider_config):
        calls.append(job["meeting_id"])
        metrics = {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        if outcome == "deadline":
            raise PiCoachRuntimeError("private body must not enter ledger", code="agent_deadline_exceeded", metrics=metrics)
        return {"metrics": metrics}

    if outcome == "deadline":
        with pytest.raises(PiCoachRuntimeError, match="private body") as caught:
            asyncio.run(run(callback, {}, {"meeting_id": account}))
        assert caught.value.code == "agent_deadline_exceeded"
    else:
        asyncio.run(run(callback, {}, {"meeting_id": account}))
    saved = usage.list_usage(session_id=account)
    assert len(saved) == 1 and saved[0]["total_tokens"] == 30
    assert saved[0]["purpose"] == ("memory_reconcile" if kind == "memory" else "meeting_preparation")
    settings = usage.get_settings()
    settings["budget"]["session_limit_cny"] = 1.0  # Reported split costs 3 CNY at the explicit fixture rates.
    usage.replace_settings(settings)
    with pytest.raises(BackgroundReconcileDeferred, match="budget_or_runtime_deferred"):
        asyncio.run(run(callback, {}, {"meeting_id": account}))
    assert calls == [account] and usage.list_usage(session_id=account) == saved
    if storage == "sqlite":
        assert usage._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
    else:
        assert list((tmp_path / "json-sessions").rglob("*.json")) == []
