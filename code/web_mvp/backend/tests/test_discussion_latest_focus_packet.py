"""Native request mechanics; model relevance is evaluated separately with real probes."""

from __future__ import annotations

import asyncio
import json

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.realtime_intelligence import MAX_PARAGRAPH_CHARACTERS
from test_discussion_state_background import Provider, append, prepare, state_app as _state_app


@pytest.fixture
def state_app(tmp_path, monkeypatch):
    yield from _state_app.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("count", [1, 3, 12])
def test_latest_focus_refs_keep_pending_sources_and_one_native_attempt(state_app, monkeypatch, count):
    app, now = state_app
    text = "通知可以稍后发送，本轮先确认支付后的权益发放。" * 6 if count == 1 else None
    job = prepare(app, now, count, text=text)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)

    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))

    assert len(provider.calls) == result["provider_attempt_count"] == 1
    assert result["repair_attempted"] is False
    assert result["applied"]["coach_intervention"] is None
    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    task = packet["state_task"]
    assert task["pending_segment_ids"] == [f"s{i}" for i in range(1, count + 1)]
    assert task["semantic_from_seq"] == 0
    focus = task["latest_focus_paragraphs"]
    assert [item["id"] for item in focus] == [f"s{i}" for i in range(max(1, count - 1), count + 1)]
    assert [item["transcript_seq"] for item in focus] == list(range(max(1, count - 1), count + 1))
    for item in focus:
        source = next(p for p in packet[item["source_group"]] if p["id"] == item["id"])
        assert set(item) == {"id", "transcript_seq", "revision", "start_ms", "end_ms", "source_group"}
        assert item == {**{key: source[key] for key in item if key != "source_group"},
                        "source_group": item["source_group"]}
        assert item["source_group"] in {"new_paragraphs", "context_paragraphs", "retrieval_paragraphs"}
        assert item["revision"] == 1
        assert len(source["text"]) <= MAX_PARAGRAPH_CHARACTERS
    assert app.state.v2_persistence.get_current_discussion("review")["context"]["last_semantic_seq"] == count


def test_focus_cannot_include_a_new_final_arriving_after_frozen_request(state_app, monkeypatch):
    app, now = state_app
    job = prepare(app, now, 6)
    provider = Provider(on_complete=lambda: append(app.state.v2_persistence, 7,
        "冻结请求之后的新交流，留给下一轮理解。", now=now + 10))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)

    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))

    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    assert [item["id"] for item in packet["state_task"]["latest_focus_paragraphs"]] == ["s5", "s6"]
    assert packet["state_task"]["pending_segment_ids"] == [f"s{i}" for i in range(1, 7)]
    assert all(p["id"] != "s7" for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs")
               for p in packet[group])
    assert result["provider_attempt_count"] == 1 and result["repair_attempted"] is False
    assert app.state.v2_persistence.get_current_discussion("review")["context"]["last_semantic_seq"] == 6


def test_long_focus_is_a_reference_not_an_extra_copy_of_source_text(state_app, monkeypatch):
    app, now = state_app
    text = ("通知可以稍后发送。" + "long source data. " * 300).rstrip()
    job = prepare(app, now, 1, text=text)
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)

    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))

    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    sources = [p for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs") for p in packet[group]]
    assert len(sources) == 1 and sources[0]["text"] == text
    assert packet["state_task"]["latest_focus_paragraphs"] == [{
        "id": "s1", "transcript_seq": 1, "revision": 1, "start_ms": 1_000, "end_ms": 1_800,
        "source_group": "new_paragraphs",
    }]
    assert provider.calls[0]["messages"][1]["content"].count(text) == 1
    assert result["provider_attempt_count"] == 1


def test_native_focus_uses_transcript_order_even_when_audio_offsets_reverse(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    for seq in range(1, 4):
        text = "通知可以稍后发送，本轮先确认支付后的权益发放。"
        p.commit_final_and_enqueue(meeting_id="review", final_id=f"reverse-{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=(4 - seq) * 1_000,
            ended_at_ms=(4 - seq) * 1_000 + 800, evidence_hash=f"h{seq}", now_ms=now - 4_000 + seq)
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    job = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=now, lease_ms=5_000)
    assert job["id"] == queued["id"]
    app.state.provider_lane_registry.sync_realtime_work(set())
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE meeting_id='review' AND kind='intelligence' AND id!=?", (job["id"],))
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)

    result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))

    packet = json.loads(provider.calls[0]["messages"][1]["content"])
    focus = packet["state_task"]["latest_focus_paragraphs"]
    assert [item["id"] for item in focus] == ["s2", "s3"]
    assert [item["transcript_seq"] for item in focus] == [2, 3]
    assert [item["start_ms"] for item in focus] == [2_000, 1_000]
    assert result["provider_attempt_count"] == 1
