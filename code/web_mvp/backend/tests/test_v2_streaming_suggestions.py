from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import inspect
import json
import sqlite3
from typing import Any

import httpx
import pytest

from meeting_copilot_web_mvp.streaming_llm_provider import (
    ChatCompletionResult,
    CompletionDelta,
    OpenAICompatibleStreamingProvider,
    ProviderTimings,
    TokenUsage,
    TransportMode,
)
from meeting_copilot_web_mvp.v2_persistence import V2Persistence
from meeting_copilot_web_mvp.v2_streaming_suggestions import (
    StaleSuggestionEvidenceError,
    SuggestionValidationError,
    build_realtime_suggestion_messages,
    generate_streaming_suggestion,
)


class _Clock:
    def __init__(self, seconds: float = 10.0) -> None:
        self.seconds = seconds

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


class _NowMs:
    def __init__(self, milliseconds: int = 2_000) -> None:
        self.milliseconds = milliseconds

    def __call__(self) -> int:
        current = self.milliseconds
        self.milliseconds += 1
        return current


class _ScriptedProvider:
    def __init__(
        self,
        *,
        clock: _Clock,
        deltas: Sequence[tuple[float, str]],
        failure: BaseException | None = None,
        before_return: Callable[[], None] | None = None,
        content: str | None = None,
    ) -> None:
        self.clock = clock
        self.deltas = list(deltas)
        self.failure = failure
        self.before_return = before_return
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        on_delta=None,
        **parameters: Any,
    ) -> ChatCompletionResult:
        assert messages
        self.calls.append(dict(parameters))
        assert parameters["max_completion_tokens"] == 128
        emitted: list[str] = []
        for delay, text in self.deltas:
            self.clock.advance(delay)
            emitted.append(text)
            if on_delta is not None:
                callback_result = on_delta(
                    CompletionDelta(
                        text=text,
                        sequence=len(emitted),
                        transport_mode=TransportMode.STREAMING,
                    )
                )
                if inspect.isawaitable(callback_result):
                    await callback_result
        if self.failure is not None:
            raise self.failure
        if self.before_return is not None:
            self.before_return()
        return ChatCompletionResult(
            content=self.content if self.content is not None else "".join(emitted),
            transport_mode=TransportMode.STREAMING,
            timings=ProviderTimings(
                started_at=20.0,
                connected_at=20.02,
                first_token_at=20.08,
                completed_at=20.20,
            ),
            usage=TokenUsage(
                prompt_tokens=20,
                completion_tokens=8,
                total_tokens=28,
            ),
            response_id="response-1",
            model="test-model",
            finish_reason="stop",
        )


def _claimed_suggestion_job(persistence: V2Persistence) -> dict[str, Any]:
    committed = persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="final-1",
        segment_id="segment-1",
        text="我们需要确认数据库迁移的回滚负责人。",
        normalized_text="我们需要确认数据库迁移的回滚负责人。",
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash="hash-final-1",
        now_ms=1_000,
    )
    claimed = persistence.claim_next_job(
        worker_id="suggestion-worker",
        lane="suggestion",
        now_ms=1_100,
        lease_ms=60_000,
    )
    assert claimed is not None
    assert claimed["id"] == committed["job_ids"]["suggestion"]
    return claimed


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "给出一条现在最值得追问的建议"}]


def test_realtime_suggestion_prompt_is_single_sourced_and_evidence_bound() -> None:
    messages = build_realtime_suggestion_messages(
        gap="确认回滚负责人",
        evidence="支付服务周五灰度，但回滚负责人还没确定。",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "只输出一句" in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {
        "gap": "确认回滚负责人",
        "evidence": "支付服务周五灰度，但回滚负责人还没确定。",
    }


def test_first_delta_and_time_or_character_thresholds_checkpoint_in_sequence(tmp_path) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting.db")
        job = _claimed_suggestion_job(persistence)
        clock = _Clock()
        provider = _ScriptedProvider(
            clock=clock,
            deltas=[
                (0.0, "问"),
                (0.10, "甲" * 63),
                (0.10, "乙"),
                (0.25, "丙"),
                (0.01, "结尾"),
            ],
        )
        try:
            output = await generate_streaming_suggestion(
                job=job,
                messages=_messages(),
                provider=provider,
                persistence=persistence,
                monotonic=clock,
                now_ms=_NowMs(),
            )
            suggestion = output["suggestion"]
            assert suggestion["status"] == "committed"
            assert suggestion["draft_seq"] == 4
            assert suggestion["final_draft_seq"] == 4
            assert output["transport_mode"] == "streaming"
            assert output["ttft_ms"] == pytest.approx(80.0)
            assert output["usage"] == {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
            }
            assert provider.calls[0]["idempotency_key"] == job["idempotency_key"]
            suggestion_event_types = [
                event["type"]
                for event in persistence.list_events("meeting-1")
                if event["type"].startswith("suggestion.")
            ]
            assert suggestion_event_types == [
                "suggestion.draft.started",
                "suggestion.draft.delta",
                "suggestion.draft.delta",
                "suggestion.draft.delta",
                "suggestion.committed",
            ]
        finally:
            persistence.close()

    asyncio.run(scenario())


def test_non_streaming_provider_fallback_is_explicit_and_committed(tmp_path) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting.db")
        job = _claimed_suggestion_job(persistence)

        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "id": "fallback-1",
                    "choices": [
                        {
                            "message": {"content": "请确认数据库回滚负责人。"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 6,
                        "total_tokens": 16,
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        provider = OpenAICompatibleStreamingProvider(
            base_url="https://gateway.example/openai",
            api_key="sk-test",
            model="test-model",
            client=client,
        )
        try:
            output = await generate_streaming_suggestion(
                job=job,
                messages=_messages(),
                provider=provider,
                persistence=persistence,
                now_ms=_NowMs(),
            )
            assert output["transport_mode"] == "non_streaming_fallback"
            assert output["fallback_reason"] == "provider_returned_non_streaming_response"
            assert output["suggestion"]["status"] == "committed"
            assert output["suggestion"]["draft_seq"] == 1
        finally:
            await client.aclose()
            persistence.close()

    asyncio.run(scenario())


def test_stale_evidence_commit_barrier_rejects_formal_result(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "meeting.db"
        persistence = V2Persistence(database_path)
        job = _claimed_suggestion_job(persistence)
        clock = _Clock()

        def make_evidence_stale() -> None:
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "UPDATE transcript_segments SET evidence_hash = ? "
                    "WHERE meeting_id = ? AND segment_id = ?",
                    ("replacement-hash", "meeting-1", "segment-1"),
                )

        provider = _ScriptedProvider(
            clock=clock,
            deltas=[(0.0, "请确认回滚负责人。")],
            before_return=make_evidence_stale,
        )
        try:
            with pytest.raises(StaleSuggestionEvidenceError):
                await generate_streaming_suggestion(
                    job=job,
                    messages=_messages(),
                    provider=provider,
                    persistence=persistence,
                    monotonic=clock,
                    now_ms=_NowMs(),
                )
            suggestion = persistence.get_snapshot("meeting-1")["suggestions"][0]
            assert suggestion["status"] == "draft"
            assert "suggestion.committed" not in {
                event["type"] for event in persistence.list_events("meeting-1")
            }
        finally:
            persistence.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("content", "max_characters", "message"),
    [
        ("   ", 120, "empty"),
        ("过" * 21, 20, "exceeds"),
    ],
)
def test_invalid_final_text_is_not_committed(
    tmp_path,
    content: str,
    max_characters: int,
    message: str,
) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting.db")
        job = _claimed_suggestion_job(persistence)
        clock = _Clock()
        provider = _ScriptedProvider(
            clock=clock,
            deltas=[(0.0, content)],
            content=content,
        )
        try:
            with pytest.raises(SuggestionValidationError, match=message):
                await generate_streaming_suggestion(
                    job=job,
                    messages=_messages(),
                    provider=provider,
                    persistence=persistence,
                    monotonic=clock,
                    now_ms=_NowMs(),
                    max_characters=max_characters,
                )
            assert not any(
                suggestion["status"] == "committed"
                for suggestion in persistence.get_snapshot("meeting-1")["suggestions"]
            )
        finally:
            persistence.close()

    asyncio.run(scenario())


def test_provider_failure_after_draft_never_commits(tmp_path) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting.db")
        job = _claimed_suggestion_job(persistence)
        clock = _Clock()
        provider = _ScriptedProvider(
            clock=clock,
            deltas=[(0.0, "请确认")],
            failure=ConnectionError("provider disconnected"),
        )
        try:
            with pytest.raises(ConnectionError, match="disconnected"):
                await generate_streaming_suggestion(
                    job=job,
                    messages=_messages(),
                    provider=provider,
                    persistence=persistence,
                    monotonic=clock,
                    now_ms=_NowMs(),
                )
            suggestion = persistence.get_snapshot("meeting-1")["suggestions"][0]
            assert suggestion["status"] == "draft"
            assert suggestion["text"] is None
        finally:
            persistence.close()

    asyncio.run(scenario())


def test_retry_continues_the_persisted_draft_sequence_and_commits(tmp_path) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting.db")
        first_job = _claimed_suggestion_job(persistence)
        first_provider = _ScriptedProvider(
            clock=_Clock(),
            deltas=[(0.0, "建议确认")],
            failure=ConnectionError("provider disconnected"),
        )
        try:
            with pytest.raises(ConnectionError, match="disconnected"):
                await generate_streaming_suggestion(
                    job=first_job,
                    messages=_messages(),
                    provider=first_provider,
                    persistence=persistence,
                    monotonic=first_provider.clock,
                    now_ms=_NowMs(2_000),
                )
            first_draft = persistence.get_snapshot("meeting-1")["suggestions"][0]
            assert first_draft["draft_seq"] == 1

            retried = persistence.retry_job(
                job_id=str(first_job["id"]),
                worker_id=str(first_job["lease_owner"]),
                now_ms=2_100,
                next_attempt_at_ms=2_100,
                error_class="ConnectionError",
            )
            assert retried is not None
            second_job = persistence.claim_next_job(
                worker_id="suggestion-worker-retry",
                lane="suggestion",
                now_ms=2_100,
                lease_ms=60_000,
            )
            assert second_job is not None
            second_provider = _ScriptedProvider(
                clock=_Clock(),
                deltas=[(0.0, "建议确认回滚负责人。")],
            )
            output = await generate_streaming_suggestion(
                job=second_job,
                messages=_messages(),
                provider=second_provider,
                persistence=persistence,
                monotonic=second_provider.clock,
                now_ms=_NowMs(2_200),
            )

            assert output["suggestion"]["status"] == "committed"
            assert output["suggestion"]["draft_seq"] == 2
            assert output["suggestion"]["final_draft_seq"] == 2
            assert output["suggestion"]["text"] == "建议确认回滚负责人。"
        finally:
            persistence.close()

    asyncio.run(scenario())


def test_provider_cancellation_after_draft_never_commits(tmp_path) -> None:
    async def scenario() -> None:
        persistence = V2Persistence(tmp_path / "meeting.db")
        job = _claimed_suggestion_job(persistence)
        clock = _Clock()
        provider = _ScriptedProvider(
            clock=clock,
            deltas=[(0.0, "请确认")],
            failure=asyncio.CancelledError(),
        )
        try:
            with pytest.raises(asyncio.CancelledError):
                await generate_streaming_suggestion(
                    job=job,
                    messages=_messages(),
                    provider=provider,
                    persistence=persistence,
                    monotonic=clock,
                    now_ms=_NowMs(),
                )
            suggestion = persistence.get_snapshot("meeting-1")["suggestions"][0]
            assert suggestion["status"] == "draft"
            assert suggestion["text"] is None
        finally:
            persistence.close()

    asyncio.run(scenario())
