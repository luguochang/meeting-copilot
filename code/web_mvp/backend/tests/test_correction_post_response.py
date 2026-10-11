"""Exercise paid-response local failures and cleanup in the actual app path."""

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp import llm_service
from test_correction_batch_backlog import _fixture, _segment


USAGE = {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13}


@pytest.mark.parametrize("value, expected, known", [
    ({"prompt_tokens": 8, "private_body": "discard"}, {"prompt_tokens": 8}, True),
    ({"prompt_tokens": True, "completion_tokens": "5", "total_tokens": -1}, {}, False),
    ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
     {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, False),
])
def test_safe_returned_usage_preserves_partial_known_counts_and_rejects_unreliable_values(value, expected, known):
    assert app_module._safe_returned_correction_usage(value) == (expected, known)


@pytest.mark.parametrize("failure", ["terminology", "ledger"])
def test_paid_response_local_rejection_retains_usage_and_never_calls_provider_again(monkeypatch, tmp_path, failure):
    app, persistence, job, hashes, now = _fixture(monkeypatch, tmp_path)
    calls = []
    ledger_calls = []
    terms_changed = False
    try:
        old_terms = [{"term_id": "confirmed-a", "source": "术语甲", "target": "术语甲"}]
        def resolve(*args, **kwargs):
            return {"terms": [*old_terms, {"term_id": "confirmed-b"}] if terms_changed else old_terms}
        app.state.user_memory = SimpleNamespace(resolve=resolve)
        real_record = app.state.settings_usage_repository.record_usage
        def record_usage(**kwargs):
            ledger_calls.append(dict(kwargs))
            if failure == "ledger":
                raise RuntimeError("private-ledger-diagnostic-must-not-persist")
            return real_record(**kwargs)
        monkeypatch.setattr(app.state.settings_usage_repository, "record_usage", record_usage)
        async def successful_provider(indexed_batch, *args, **kwargs):
            nonlocal terms_changed
            calls.append(True)
            terms_changed = failure == "terminology"
            return indexed_batch, {**USAGE, "private_provider_body": "must-not-persist"}, False
        monkeypatch.setattr(app_module.asr_correct, "async_correct_transcript", successful_provider)
        old_job = {**job, "created_at_ms": now - 60_000, "attempts": 2}
        with pytest.raises(HTTPException) as caught:
            asyncio.run(app.state.v2_correction_job_handler_async_impl(old_job))
        error = caught.value
        assert type(error).__name__ == "RealtimeCorrectionPostResponseError"
        expected_error = "correction_terminology_changed_after_response" if failure == "terminology" else "correction_usage_record_failed"
        assert error.durable_error_class == expected_error and error.retryable is False
        assert len(calls) == 1 and len(ledger_calls) == 1
        rows = app.state.settings_usage_repository.list_usage(session_id="meeting-1")
        assert len(rows) == (1 if failure == "terminology" else 0)
        audit = app.state.asr_live_repository.get("meeting-1")["realtime_transcript_correction"]["batch_audits"][-1]
        assert audit["status"] == "local_post_response_failed"
        assert audit["error_code"] == expected_error
        assert audit["failure_stage"] == "local_post_response"
        assert audit["provider_outcome"] == "success"
        assert audit["usage"] == USAGE and audit["usage_known"] is True
        assert audit["usage_recorded"] is (failure == "terminology")
        assert "private_provider_body" not in str(audit)
        assert "private-ledger-diagnostic" not in str(audit)
        assert audit["target_evidence_hashes"] == hashes
        for number in range(1, 5):
            segment = _segment(persistence, "meeting-1", f"s{number}")
            assert segment["correction_error_class"] == expected_error
            assert segment["correction_status"] == "failed_preserved_original"
            assert segment["text"] == segment["normalized_text"] and segment["revision"] == 1
        config = llm_service.correction_config(llm_service.LlmConfig.from_env())
        assert app.state.correction_provider_availability.snapshot(config).state == "closed"
        assert app.state.correction_provider_availability.snapshot(config).failure_count == 0
        output = app.state.v2_correction_job_handler_impl(old_job)
        assert output["called"] is False and len(calls) == 1 and len(ledger_calls) == 1
    finally:
        persistence.close()


def test_availability_release_failure_still_releases_provider_and_meeting_lanes(monkeypatch, tmp_path):
    app, persistence, job, _, now = _fixture(monkeypatch, tmp_path)
    try:
        real_admit = app.state.correction_provider_availability.admit_or_defer
        def admit(config):
            permit = real_admit(config)
            def release():
                raise RuntimeError("injected availability persistence release")
            return SimpleNamespace(record_success=permit.record_success,
                                   record_failure=permit.record_failure, release=release)
        monkeypatch.setattr(app.state.correction_provider_availability, "admit_or_defer", admit)
        async def successful_provider(indexed_batch, *args, **kwargs):
            return indexed_batch, USAGE, False
        monkeypatch.setattr(app_module.asr_correct, "async_correct_transcript", successful_provider)
        with pytest.raises(RuntimeError, match="injected availability persistence release"):
            asyncio.run(app.state.v2_correction_job_handler_async_impl(
                {**job, "created_at_ms": now - 60_000, "attempts": 2}))
        assert app.state.provider_lane_registry.active_correction_count == 0
        lease = app.state.llm_lane_locks.try_acquire("meeting-1", "correction")
        assert lease is not None
        lease.release()
    finally:
        persistence.close()
