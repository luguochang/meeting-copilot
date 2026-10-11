"""Constructed resident-protocol regressions; these do not measure real ASR quality."""

import asyncio
import json
import struct
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.asr_live_repository import InMemoryAsrLiveSessionRepository
from meeting_copilot_web_mvp.asr_refiner import REALTIME_REFINER_POLICY_ENV
from meeting_copilot_web_mvp.canonical_transcript import project_canonical_transcript


SPEECH = struct.pack("<f", 0.05) * 4_800
SILENCE = struct.pack("<f", 0.0) * 4_800
FIRST_TEXT = "支付成功但短信通知失败，是否应该回滚付款？"
SECOND_TEXT = "权益先幂等生效，短信独立重试，页面显示已确认状态。"


def _failed_refinement(reason="asr_capacity_unavailable"):
    return SimpleNamespace(
        text="", status="failed", model_id=None, reason=reason, authoritative=False,
    )


def _warming_refinement():
    return SimpleNamespace(text="", status="unavailable", model_id=None,
        reason=asr_stream.WARMING_REFINEMENT_REASON, authoritative=False)


class _AcknowledgedOnlineRecognizer:
    """A resident-style online preview with explicit closed-boundary ACKs."""

    provider = "funasr_realtime"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, texts=(FIRST_TEXT, SECOND_TEXT)):
        self._seq = 0
        self.texts = list(texts)
        self.utterance_count = 0
        self.flush_calls = []
        self.finalize_calls = 0

    def recognize_chunk(self, pcm):
        self._seq += 1
        if struct.unpack("<f", pcm[:4])[0] <= asr_stream.VAD_SILENCE_RMS_THRESHOLD:
            return []
        self.utterance_count += 1
        start_ms = (self._seq - 1) * 300
        text = self.texts[self.utterance_count - 1]
        if not text:
            return []
        return [{
            "event_type": "partial",
            "partial_semantics": "incremental_chunk",
            "segment_id": f"resident-utterance-{self.utterance_count}",
            "source_segment_id": f"resident-utterance-{self.utterance_count}",
            "text": text,
            "start_ms": start_ms,
            "end_ms": start_ms + 300,
            "confidence": 0.91,
            "authoritative": False,
        }]

    def flush_utterance(self, boundary_id, timeout=1.25):
        self.flush_calls.append((boundary_id, timeout))
        return []

    def finalize(self):
        self.finalize_calls += 1
        return []


class _CapturedWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = False

    async def accept(self):
        return None

    async def receive(self):
        return self.messages.pop(0)

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        self.closed = True


@pytest.fixture
def on_demand_stream(monkeypatch):
    monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, "on_demand")
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda _sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda _sid: None)
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", lambda _pcm: _failed_refinement())
    # This suite isolates closed-boundary behavior; separate readiness tests
    # exercise real lease semantics with a controlled resident process.
    monkeypatch.setattr(asr_stream, "retain_refiner_for_meeting", lambda _sid: False)
    monkeypatch.setattr(asr_stream, "DiarizationRuntime", lambda *_args, **_kwargs: SimpleNamespace(
        start=lambda: None, submit_pcm=lambda _pcm: None,
        observe_final=lambda _event: None, finish=lambda: None, abort=lambda: None,
    ))


def _canonical(record):
    return project_canonical_transcript(session_id=record["session_id"], events=record["events"])


def _run_closed_boundary(monkeypatch, recognizer, reason="asr_capacity_unavailable"):
    repository = InMemoryAsrLiveSessionRepository()
    committed = []
    websocket = _CapturedWebSocket([
        {"bytes": SPEECH}, *({"bytes": SILENCE} for _ in range(3)), {"text": "END"},
    ])
    refinement_payloads = []

    def refuse_optional_refinement(pcm):
        refinement_payloads.append(pcm)
        return _failed_refinement(reason)

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", refuse_optional_refinement)
    asyncio.run(asr_stream.handle_stream(
        websocket, "closed-capacity-boundary", asr_live_repo=repository,
        on_final_committed=committed.append,
    ))
    return websocket, repository.get("closed-capacity-boundary"), committed, refinement_payloads


@pytest.mark.parametrize("reason", [
    "asr_capacity_unavailable", "asr_resource_guard_triggered", "asr_cold_start_busy",
])
def test_on_demand_capacity_refusal_commits_only_acknowledged_online_utterance(
    monkeypatch, on_demand_stream, reason,
):
    recognizer = _AcknowledgedOnlineRecognizer(texts=(FIRST_TEXT,))
    websocket, record, committed, pcm_inputs = _run_closed_boundary(
        monkeypatch, recognizer, reason,
    )
    finals = [event for event in websocket.sent if event.get("event_type") == "final"]
    assert len(finals) == len(committed) == len(pcm_inputs) == 1
    assert pcm_inputs[0].startswith(SPEECH)
    assert len(recognizer.flush_calls) == 1
    final = finals[0]
    assert final["text"] == FIRST_TEXT
    assert final["authoritative"] is True
    assert final["final_source"] == "local_realtime_online_final"
    assert final["endpoint_source"] == "server_vad_online_final_resource_guard"
    assert final["refinement_status"] == "failed"
    assert final["refinement_reason"] == reason
    assert final["confidence"] == 0.91
    assert final["confidence_source"] == asr_stream.ASR_CONFIDENCE_SOURCE_REALTIME_REPORTED
    assert asr_stream.RESOURCE_GUARD_ONLINE_FINAL_REASON in record["degradation_reasons"]
    assert "offline_refinement_unavailable" not in record["degradation_reasons"]
    canonical = _canonical(record)
    assert [segment["display_text"] for segment in canonical["segments"]] == [FIRST_TEXT]
    assert canonical["active_tail"] is None


@pytest.mark.parametrize("refinement_kind", ["capacity", "capacity_prewarm", "warming_after_empty"])
def test_unavailable_refinement_retains_two_vad_finals_in_public_history_without_ai_blocker(
    monkeypatch, tmp_path, on_demand_stream, refinement_kind,
):
    warming = refinement_kind == "warming_after_empty"
    if refinement_kind == "capacity_prewarm":
        monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, "prewarm")
    recognizer = _AcknowledgedOnlineRecognizer(texts=("", FIRST_TEXT, SECOND_TEXT) if warming else (FIRST_TEXT, SECOND_TEXT))
    refinement_payloads = []

    def capacity_refusal(pcm):
        refinement_payloads.append(pcm)
        return _warming_refinement() if warming else _failed_refinement()

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", capacity_refusal)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    # No lifespan/executor: public projection is exercised without provider calls.
    client = TestClient(app)
    meeting_id = "constructed-capacity-two-closed-utterances"
    preparation = client.put(f"/v2/meetings/{meeting_id}/preparation", json={
        "hotwords": [], "input_source": "microphone", "notice_acknowledged": True,
        "live_analysis_profile": "participation", "proactive_suggestion_policy": "standard",
    })
    assert preparation.status_code == 200
    with client.websocket_connect(
        f"/live/asr/stream/ws/{meeting_id}?audio_source=browser_live_mic&capture_epoch=1",
    ) as websocket:
        if warming:
            # A real r01-shaped initial noise endpoint has voiced PCM but no
            # online words. It may prepare the refiner, never invent a final.
            for pcm in (SPEECH, SILENCE, SILENCE, SILENCE):
                websocket.send_bytes(pcm)
        for pcm in (SPEECH, SILENCE, SILENCE, SILENCE):
            websocket.send_bytes(pcm)
        first_final = None
        for _ in range(8):
            event = json.loads(websocket.receive_text())
            if event.get("event_type") == "final":
                first_final = event
                break
        assert first_final is not None
        assert first_final["endpoint_source"] == (
            "server_vad_online_final_warming" if warming else "server_vad_online_final_resource_guard"
        )
        assert first_final["refinement_status"] == ("unavailable" if warming else "failed")
        first_snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()
        assert [segment["text"] for segment in first_snapshot["segments"]] == [FIRST_TEXT]
        first_public_segment = first_snapshot["segments"][0]
        for pcm in (SPEECH, SILENCE, SILENCE, SILENCE):
            websocket.send_bytes(pcm)
        second_final = None
        for _ in range(8):
            event = json.loads(websocket.receive_text())
            if event.get("event_type") == "final":
                second_final = event
                break
        assert second_final is not None
        second_snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()
        assert [segment["text"] for segment in second_snapshot["segments"]] == [
            FIRST_TEXT, SECOND_TEXT,
        ]
        assert second_snapshot["segments"][0] == first_public_segment
        assert second_snapshot["segments"][0]["segment_id"] != second_snapshot["segments"][1]["segment_id"]
        capabilities = second_snapshot["runtime"]["ai"]["capabilities"]
        assert capabilities["transcript"]["state"] == "active"
        assert capabilities["transcript"]["label"] == "在线正文已保存，精修未完成"
        expected_reason = asr_stream.WARMING_REFINEMENT_REASON if warming else "asr_capacity_unavailable"
        assert capabilities["transcript"]["error_class"] == expected_reason
        assert second_snapshot["runtime"]["ai"].get("error_class") != "asr_capacity_unavailable"
        assert capabilities["proactive_suggestions"].get("error_class") != "asr_capacity_unavailable"
        queued = app.state.v2_persistence.enqueue_realtime_participation(
            meeting_id=meeting_id, now_ms=time.time_ns() // 1_000_000 + 11_000,
        )
        assert queued is not None
        assert queued["request_context"]["automatic_direct"] is True
        assert queued["request_context"]["current_need_basis"]["focus_segment_ids"] == [
            segment["segment_id"] for segment in second_snapshot["segments"]
        ]
        websocket.send_text("END")
        with pytest.raises(WebSocketDisconnect):
            while True:
                websocket.receive_text()
    assert len(recognizer.flush_calls) == len(refinement_payloads) == (3 if warming else 2)
    assert recognizer.flush_calls[0][0] != recognizer.flush_calls[1][0]
    record = client.get(f"/live/asr/sessions/{meeting_id}/events").json()
    persisted_finals = [event for event in record["events"] if event["event_type"] == "transcript_final"]
    assert len(persisted_finals) == 2
    assert all(event["payload"]["refinement_reason"] == expected_reason for event in persisted_finals)
    assert (asr_stream.WARMING_ONLINE_FINAL_REASON if warming else asr_stream.RESOURCE_GUARD_ONLINE_FINAL_REASON) in record["degradation_reasons"]
    assert "offline_refinement_unavailable" not in record["degradation_reasons"]
    if warming:
        assert asr_stream.BACKGROUND_REFINEMENT_WARMING_REASON in record["degradation_reasons"]


@pytest.mark.parametrize("missing", ["no_ack", "no_pcm", "empty_text", "single_character", "wrong_status", "wrong_policy"])
def test_warming_cannot_promote_unconfirmed_or_unusable_online_text(on_demand_stream, monkeypatch, missing):
    refinement = _warming_refinement()
    text = FIRST_TEXT
    guards = {"boundary_acknowledged": True, "has_speech_audio": True}
    if missing == "no_ack":
        guards["boundary_acknowledged"] = False
    elif missing == "no_pcm":
        guards["has_speech_audio"] = False
    elif missing == "empty_text":
        text = ""
    elif missing == "single_character":
        text = "嗯"
    elif missing == "wrong_status":
        refinement.status = "failed"
    else:
        monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, "prewarm")
    assert not asr_stream._uses_online_final_warming(refinement, text, **guards)


@pytest.mark.parametrize("reason", [
    "asr_resource_probe_failed", "worker_dependency_unavailable", "offline_worker_timeout",
])
@pytest.mark.parametrize("policy", ["on_demand", "prewarm"])
def test_closed_boundary_non_capacity_failures_still_preserve_non_authoritative_draft(
    monkeypatch, on_demand_stream, reason, policy,
):
    monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, policy)
    recognizer = _AcknowledgedOnlineRecognizer(texts=(FIRST_TEXT,))
    websocket, record, committed, _pcm_inputs = _run_closed_boundary(
        monkeypatch, recognizer, reason,
    )
    assert committed == []
    assert not [event for event in websocket.sent if event.get("event_type") == "final"]
    assert not [event for event in record["events"] if event["event_type"] == "transcript_final"]
    assert _canonical(record)["segments"] == []
    assert "offline_refinement_unavailable" in record["degradation_reasons"]
    assert asr_stream.RESOURCE_GUARD_ONLINE_FINAL_REASON not in record["degradation_reasons"]


def test_prewarm_capacity_refusal_keeps_policy_and_only_saves_unrefined_closed_original(
    monkeypatch, on_demand_stream,
):
    monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, "prewarm")
    websocket, record, committed, _pcm_inputs = _run_closed_boundary(
        monkeypatch, _AcknowledgedOnlineRecognizer(texts=(FIRST_TEXT,)),
    )
    assert asr_stream.realtime_refiner_policy()["mode"] == "prewarm"
    assert len(committed) == 1
    final = next(event for event in websocket.sent if event.get("event_type") == "final")
    assert final["text"] == FIRST_TEXT
    assert final["final_source"] == "local_realtime_online_final"
    assert final["refinement_status"] == "failed"
    assert final["refinement_reason"] == "asr_capacity_unavailable"
    assert "offline_refinement_unavailable" not in record["degradation_reasons"]
    assert asr_stream.RESOURCE_GUARD_ONLINE_FINAL_REASON in record["degradation_reasons"]


@pytest.mark.parametrize("missing_guard", ["boundary_acknowledged", "has_speech_audio"])
@pytest.mark.parametrize("policy", ["on_demand", "prewarm"])
def test_capacity_reason_alone_cannot_authorize_missing_boundary_or_pcm(
    on_demand_stream, missing_guard, policy, monkeypatch,
):
    monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, policy)
    guards = {"boundary_acknowledged": True, "has_speech_audio": True}
    guards[missing_guard] = False
    assert not asr_stream._uses_online_final_resource_guard(
        _failed_refinement(), FIRST_TEXT, **guards,
    )


def test_closed_boundary_capacity_failure_rejects_single_character_online_text(
    monkeypatch, on_demand_stream,
):
    websocket, record, committed, _pcm_inputs = _run_closed_boundary(
        monkeypatch, _AcknowledgedOnlineRecognizer(texts=("嗯",)),
    )
    assert committed == []
    assert not [event for event in websocket.sent if event.get("event_type") == "final"]
    assert _canonical(record)["segments"] == []


@pytest.mark.parametrize("policy", ["on_demand", "prewarm"])
def test_capacity_failure_without_resident_boundary_ack_never_promotes_partial(
    monkeypatch, on_demand_stream, policy,
):
    monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, policy)
    recognizer = _AcknowledgedOnlineRecognizer(texts=(FIRST_TEXT,))
    recognizer.flush_utterance = None
    websocket, record, committed, _pcm_inputs = _run_closed_boundary(monkeypatch, recognizer)
    assert committed == []
    assert not [event for event in websocket.sent if event.get("event_type") == "final"]
    assert _canonical(record)["segments"] == []


@pytest.mark.parametrize("policy", ["on_demand", "prewarm"])
def test_capacity_failure_without_pcm_cannot_turn_terminal_worker_text_into_history(
    monkeypatch, on_demand_stream, policy,
):
    monkeypatch.setenv(REALTIME_REFINER_POLICY_ENV, policy)
    recognizer = _AcknowledgedOnlineRecognizer(texts=(FIRST_TEXT,))
    recognizer.finalize = lambda: [{
        "event_type": "partial", "segment_id": "no-audio-terminal",
        "text": FIRST_TEXT, "start_ms": 0, "end_ms": 300, "authoritative": False,
    }]
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)
    websocket = _CapturedWebSocket([{"text": "END"}])
    repository = InMemoryAsrLiveSessionRepository()
    committed = []
    asyncio.run(asr_stream.handle_stream(
        websocket, "capacity-without-pcm", asr_live_repo=repository,
        on_final_committed=committed.append,
    ))
    assert committed == []
    assert not [event for event in websocket.sent if event.get("event_type") == "final"]
    record = repository.get("capacity-without-pcm")
    assert _canonical(record)["segments"] == []


def test_failed_refiner_text_cannot_replace_the_acknowledged_online_source(
    monkeypatch, on_demand_stream,
):
    recognizer = _AcknowledgedOnlineRecognizer(texts=(FIRST_TEXT,))
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)
    failed = _failed_refinement()
    failed.text = "这一段不是成功精修的文字，不能冒充正式来源。"
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", lambda _pcm: failed)
    websocket = _CapturedWebSocket([
        {"bytes": SPEECH}, *({"bytes": SILENCE} for _ in range(3)), {"text": "END"},
    ])
    repository = InMemoryAsrLiveSessionRepository()
    committed = []
    asyncio.run(asr_stream.handle_stream(
        websocket, "failed-refiner-text", asr_live_repo=repository,
        on_final_committed=committed.append,
    ))
    assert [event["text"] for event in committed] == [FIRST_TEXT]
    assert committed[0]["refinement_status"] == "failed"
    assert _canonical(repository.get("failed-refiner-text"))["committed_text"] == FIRST_TEXT
