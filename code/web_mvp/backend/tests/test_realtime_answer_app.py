from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import ProviderRuntimeNotConfiguredDeferred, create_app


def test_continuous_statements_schedule_discussion_without_question_and_cool_down(tmp_path):
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    texts = [
        "财政贴息可以降低居民购房的实际融资成本，但贴息对象和适用范围目前还没有明确。银行也可能受益于贷款需求恢复。",
        "目前讨论主要围绕居民负担以及银行收益，居民利息减少和银行利润增加之间仍然存在传导条件，还要关注补贴承担方以及新增贷款的信用风险。",
        "对于政策效果我们需要继续观察，不应直接把贷款规模增长等同于利润改善。",
    ]
    try:
        results = [app.state.commit_v2_final("discussion-meeting", {
            "segment_id": f"speech-{index}", "text": text,
            "start_ms": index * 12_000, "end_ms": (index + 1) * 12_000,
            "source_track": "system_audio",
        }) for index, text in enumerate(texts)]
        assert "answer" not in results[0]["job_ids"]
        discussion = app.state.v2_persistence.get_job(results[1]["job_ids"]["answer"])
        assert discussion["trigger_type"] == "discussion"
        assert "answer" not in results[2]["job_ids"]
        question = app.state.commit_v2_final("discussion-meeting", {
            "segment_id": "question", "text": "那么银行实际受益的前提是什么？",
            "start_ms": 36_000, "end_ms": 39_000, "source_track": "system_audio",
        })
        assert app.state.v2_persistence.get_job(question["job_ids"]["answer"])["trigger_type"] == "delta"
    finally:
        app.state.v2_persistence.close()


def test_llm_first_system_question_runs_independent_streaming_answer_lane(
    monkeypatch,
    tmp_path,
) -> None:
    requests: list[dict] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"type":"response.output_text.delta","delta":"周四下班前由王工负责闭环，"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"我们优先复用了现有 Redis 基础设施，并把吞吐和消息保留需求作为迁移 Kafka 的边界。"}\n\n'
                'data: {"type":"response.completed","response":{"id":"answer-1","model":"fast-model","status":"completed","usage":{"input_tokens":80,"output_tokens":30,"total_tokens":110}}}\n\n'
            ),
        )

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "review-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "fast-model")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "responses")
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    client = httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False)
    app.state.answer_llm_client = client
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    committed = persistence.commit_final_and_enqueue(
        meeting_id="meeting-answer",
        final_id="final-answer",
        segment_id="segment-answer",
        text="为什么选择 Redis Stream，而不是 Kafka？",
        normalized_text="为什么选择 Redis Stream，而不是 Kafka？",
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash="hash-answer",
        source_track="system_audio",
        now_ms=now_ms,
    )
    claimed = persistence.claim_next_job(
        worker_id="answer-worker",
        lane="answer",
        now_ms=now_ms + 10,
        lease_ms=60_000,
    )
    assert claimed is not None
    assert claimed["id"] == committed["job_ids"]["answer"]

    try:
        result = asyncio.run(app.state.v2_answer_job_handler_impl(claimed))
        answer = result["suggestion"]
        snapshot_answer = persistence.get_snapshot("meeting-answer")["suggestions"][0]

        assert result["generated_answer_count"] == 1
        assert answer["kind"] == "answer"
        assert answer["status"] == "committed"
        assert answer["question_text"] == "为什么选择 Redis Stream，而不是 Kafka？"
        assert answer["text"].startswith("我们优先复用了现有 Redis")
        assert "周四" not in answer["text"]
        assert "王工" not in answer["text"]
        assert result["final_text_transformed"] is True
        assert result["grounding_removed_claim_types"] == ["deadline", "owner"]
        assert answer["model"] == "fast-model"
        assert answer["ttft_ms"] is not None
        deep_job = persistence.get_job(result["deep_coach_job_id"])
        assert deep_job["trigger_type"] == "answer_ready"
        assert deep_job["work_item_id"] == answer["suggestion_id"]
        assert deep_job["evidence_segment_id"] == "segment-answer"
        assert snapshot_answer == answer
        hydrated = TestClient(app).get("/v2/meetings/meeting-answer/snapshot")
        assert hydrated.status_code == 200
        assert hydrated.json()["suggestions"] == [answer]
        assert len(requests) == 1
        assert requests[0]["stream"] is True
        assert requests[0]["reasoning"] == {"effort": "low"}
        assert requests[0]["max_output_tokens"] == 480
        prompt_text = json.dumps(requests[0]["input"], ensure_ascii=False)
        assert "只输出用户现在可以直接说出口的中文回答" in requests[0]["instructions"]
        assert "为什么选择 Redis Stream" in prompt_text
    finally:
        asyncio.run(client.aclose())
        persistence.close()


def test_low_information_same_track_tail_does_not_replace_prior_answer(
    tmp_path,
) -> None:
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence

    first = app.state.commit_v2_final(
        "meeting-question-tail",
        {
            "segment_id": "question-main",
            "text": "本周五到底能不能上线？前提条件是什么？",
            "normalized_text": "本周五到底能不能上线？前提条件是什么？",
            "start_ms": 100,
            "end_ms": 1_000,
            "source_track": "system_audio",
        },
    )
    tail = app.state.commit_v2_final(
        "meeting-question-tail",
        {
            "segment_id": "question-tail",
            "text": "你吗？",
            "normalized_text": "你吗？",
            "start_ms": 1_150,
            "end_ms": 1_400,
            "source_track": "system_audio",
        },
    )

    try:
        assert first is not None
        assert first["job_ids"].get("answer")
        assert tail is not None
        assert "answer" not in tail["job_ids"]
        answer_jobs = persistence.list_jobs(
            meeting_id="meeting-question-tail",
            lane="answer",
        )
        assert len(answer_jobs) == 1
        assert answer_jobs[0]["evidence_segment_id"].endswith("question-main")
    finally:
        persistence.close()


def test_browser_microphone_question_runs_single_track_answer_and_queues_pi_deep(
    monkeypatch,
    tmp_path,
) -> None:
    requests: list[dict] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"type":"response.output_text.delta","delta":"当前规模下先复用 Redis 能降低接入和运维成本，"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"高吞吐或长周期留存时再评估 Kafka。"}\n\n'
                'data: {"type":"response.completed","response":{"id":"answer-mic-1","model":"fast-model","status":"completed","usage":{"input_tokens":80,"output_tokens":30,"total_tokens":110}}}\n\n'
            ),
        )

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "review-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "fast-model")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "responses")
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    streaming_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway),
        trust_env=False,
    )
    app.state.answer_llm_client = streaming_client
    persistence = app.state.v2_persistence
    app.state.meeting_preparation_store.save(
        "meeting-browser-mic",
        input_source="microphone",
        notice_acknowledged=True,
        preset_id="interview",
        meeting_goal="验证会议回答副驾",
        participant_role="候选人",
        updated_at_ms=time.time_ns() // 1_000_000,
    )

    committed = app.state.commit_v2_final(
        "meeting-browser-mic",
        {
            "segment_id": "browser-segment-1",
            "text": "你们为什么选择 Redis Stream，而不是 Kafka？",
            "normalized_text": "你们为什么选择 Redis Stream，而不是 Kafka？",
            "start_ms": 100,
            "end_ms": 900,
            "source_track": "microphone",
        },
    )
    assert committed is not None
    assert committed["source_track"] == "microphone"
    assert committed["job_ids"].get("answer")
    claimed = persistence.claim_next_job(
        worker_id="browser-answer-worker",
        lane="answer",
        now_ms=time.time_ns() // 1_000_000,
        lease_ms=60_000,
    )
    assert claimed is not None
    assert claimed["id"] == committed["job_ids"]["answer"]

    try:
        result = asyncio.run(app.state.v2_answer_job_handler_impl(claimed))
        answer = result["suggestion"]
        deep_job = persistence.get_job(result["deep_coach_job_id"])

        assert result["trigger_reason"] == "single_track_question_mark"
        assert answer["status"] == "committed"
        assert deep_job["trigger_type"] == "answer_ready"
        assert deep_job["work_item_id"] == answer["suggestion_id"]
        assert len(requests) == 1
        prompt_payload = json.loads(requests[0]["input"][-1]["content"])
        assert prompt_payload["speaker_attribution"] == "mixed_single_track"
        assert prompt_payload["user_fact_available"] is False
        assert prompt_payload["recent_dialogue"][-1]["speaker"] == "参会者（单轨混合）"
    finally:
        asyncio.run(streaming_client.aclose())
        persistence.close()


def test_unconfigured_provider_is_visible_on_the_answer_draft(monkeypatch, tmp_path) -> None:
    for name in (
        "LLM_GATEWAY_BASE_URL",
        "LLM_GATEWAY_API_KEY",
        "LLM_GATEWAY_MODEL",
        "LLM_GATEWAY_REALTIME_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    persistence = app.state.v2_persistence
    now_ms = time.time_ns() // 1_000_000
    committed = persistence.commit_final_and_enqueue(
        meeting_id="meeting-unconfigured",
        final_id="final-unconfigured",
        segment_id="segment-unconfigured",
        text="请介绍一下你负责的项目",
        normalized_text="请介绍一下你负责的项目",
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash="hash-unconfigured",
        source_track="system_audio",
        now_ms=now_ms,
    )
    claimed = persistence.claim_next_job(
        worker_id="answer-worker",
        lane="answer",
        now_ms=now_ms + 10,
        lease_ms=60_000,
    )
    assert claimed is not None
    assert claimed["id"] == committed["job_ids"]["answer"]

    try:
        with pytest.raises(ProviderRuntimeNotConfiguredDeferred):
            asyncio.run(app.state.v2_answer_job_handler_impl(claimed))
        answer = persistence.get_snapshot("meeting-unconfigured")["suggestions"][0]
        assert answer["kind"] == "answer"
        assert answer["status"] == "draft"
        assert answer["question_text"] == "请介绍一下你负责的项目"
        assert answer["error_class"] == "provider_not_configured"
    finally:
        persistence.close()
