from __future__ import annotations

import asyncio
import queue
import threading
from io import StringIO

import pytest

from meeting_copilot_web_mvp.pi_coach_runtime import (
    PROTOCOL,
    PiCoachRuntimeError,
    PiCoachSidecar,
    build_pi_coach_request,
    configured_coach_runtime,
    pi_bridge_prewarm_enabled,
)
from meeting_copilot_web_mvp.realtime_intelligence import RealtimeIntelligenceRequest


def _request() -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="pi-runtime-meeting",
        state_revision=3,
        new_paragraphs=[
            {
                "id": "remote-1",
                "text": "Can you commit to Friday?",
                "revision": 1,
                "source_track": "system_audio",
            }
        ],
        context_paragraphs=[],
        semantic_windows=[
            {
                "id": "window-1",
                "text": "[remote_mix] Can you commit to Friday?",
                "revision": 1,
                "status": "stable",
                "segment_ids": ["remote-1"],
                "source_tracks": ["system_audio"],
                "role_hints": ["remote_mix"],
            }
        ],
        rolling_state={"topic": "release"},
        meeting_goal="Avoid an unconditional date commitment.",
    )


def test_runtime_flag_defaults_to_the_pi_coach_branch(monkeypatch) -> None:
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", raising=False)
    assert configured_coach_runtime() == "pi"
    assert configured_coach_runtime("pi") == "pi"
    assert configured_coach_runtime("unsupported") == "direct"


def test_pi_bridge_prewarm_is_explicitly_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("MEETING_COPILOT_PI_BRIDGE_PREWARM", raising=False)
    assert pi_bridge_prewarm_enabled() is False
    assert pi_bridge_prewarm_enabled("1") is True
    assert pi_bridge_prewarm_enabled("true") is True
    assert pi_bridge_prewarm_enabled("off") is False


def test_sidecar_prewarm_reuses_ready_bridge_without_provider_payload() -> None:
    class Process:
        stdin = None

        def poll(self):
            return None

    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = Process()
    sidecar._ready.set()

    result = sidecar.prewarm()

    assert result["ready"] is True
    assert result["bridge_process_reused"] is True
    assert result["bridge_startup_ms"] >= 0


def test_pi_request_keeps_source_roles_and_never_places_secrets_in_ids() -> None:
    supported_event_types = (
        "question_pending",
        "commitment_without_condition",
        "objection_detected",
        "goal_at_risk",
        "missing_next_step",
        "topic_drift",
        "repetition",
        "monologue_duration",
    )
    candidate_events = [
        {
            "event_type": supported_event_types[index % len(supported_event_types)],
            "candidate_key": f"candidate-key-{index}",
            "evidence_segment_ids": ["remote-1"],
            "reason": "The latest evidence requires a bounded coach decision.",
        }
        for index in range(10)
    ]
    payload = build_pi_coach_request(
        _request(),
        request_id="request-1",
        base_url="https://gateway.example.test",
        api_key="private-test-key",
        model="coach-model",
        api_style="chat_completions",
        timeout_seconds=25,
        candidate_events=candidate_events,
    )

    assert payload["session_id"] == "pi-runtime-meeting"
    assert payload["context"]["new_paragraphs"][0]["source_track"] == "system_audio"
    assert payload["context"]["new_paragraphs"][0]["role_hint"] == "remote_mix"
    assert payload["context"]["semantic_windows"][0]["segment_ids"] == ("remote-1",)
    assert "private-test-key" not in payload["request_id"]
    assert payload["provider"]["api_key"] == "private-test-key"
    assert payload["provider"]["timeout_ms"] == 10_000
    assert payload["provider"]["decision_timeout_ms"] == 10_000
    assert payload["context"]["candidate_events"] == candidate_events[:8]
    assert payload["context"]["priority_mode"] == "realtime"
    assert payload["context"]["compact_terminal_contract"] is True


def test_pi_request_without_candidates_keeps_full_terminal_contract() -> None:
    payload = build_pi_coach_request(
        _request(),
        request_id="request-full-1",
        base_url="https://gateway.example.test",
        api_key="private-test-key",
        model="coach-model",
        api_style="chat_completions",
        timeout_seconds=25,
        candidate_events=None,
    )

    assert payload["context"]["candidate_events"] == []
    assert payload["context"]["compact_terminal_contract"] is False


def test_pi_request_can_select_the_explicit_deep_lane() -> None:
    payload = build_pi_coach_request(
        _request(),
        request_id="request-deep-1",
        base_url="https://gateway.example.test",
        api_key="private-test-key",
        model="coach-model",
        api_style="chat_completions",
        timeout_seconds=25,
        priority_mode="deep",
    )

    assert payload["context"]["priority_mode"] == "deep"


def test_pi_request_includes_only_exact_cross_batch_candidate_evidence() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="pi-runtime-cross-batch",
        state_revision=5,
        retrieval_paragraphs=[
            {
                "id": "clarity-prior",
                "text": "我先继续补充背景，结论先放到后面。",
                "revision": 1,
                "start_ms": 0,
                "end_ms": 30_000,
                "source_track": "microphone",
            },
            {
                "id": "unrelated-prior",
                "text": "这段历史不属于当前候选证据。",
                "revision": 1,
                "start_ms": 30_000,
                "end_ms": 31_000,
                "source_track": "microphone",
            },
        ],
        context_paragraphs=[],
        new_paragraphs=[
            {
                "id": "clarity-fresh",
                "text": "我继续补充流程背景，仍然没有给出明确结论或下一步。",
                "revision": 1,
                "start_ms": 31_000,
                "end_ms": 61_000,
                "source_track": "microphone",
            }
        ],
        rolling_state={},
        allow_paragraph_revisions=False,
    )
    candidate_events = [
        {
            "event_type": "monologue_duration",
            "candidate_key": "coach-candidate:monologue_duration:episode-1",
            "evidence_segment_ids": ["clarity-prior", "clarity-fresh"],
            "reason": "连续表达较长且仍有未收束信号。",
        }
    ]

    payload = build_pi_coach_request(
        request,
        request_id="request-cross-batch",
        base_url="https://gateway.example.test",
        api_key="private-test-key",
        model="coach-model",
        api_style="chat_completions",
        timeout_seconds=2.0,
        candidate_events=candidate_events,
    )

    assert [
        item["id"] for item in payload["context"]["candidate_evidence_paragraphs"]
    ] == ["clarity-prior"]
    assert "unrelated-prior" not in {
        item["id"] for item in payload["context"]["candidate_evidence_paragraphs"]
    }


def test_sidecar_response_validation_rejects_non_terminal_actions() -> None:
    response = {
        "protocol": PROTOCOL,
        "request_id": "request-1",
        "ok": True,
        "action": "text",
    }
    try:
        PiCoachSidecar._validate_response(response)
    except PiCoachRuntimeError as exc:
        assert exc.code == "pi_protocol_error"
    else:
        raise AssertionError("invalid Pi action must be rejected")


def test_sidecar_error_preserves_bounded_failure_metrics() -> None:
    response = {
        "protocol": PROTOCOL,
        "request_id": "request-1",
        "ok": False,
        "error": {
            "code": "agent_deadline_exceeded",
            "message": "deadline exceeded",
            "metrics": {
                "elapsed_ms": 10_001.0,
                "ttft_ms": None,
                "provider_status_code": 503,
                "turns": 1,
                "tool_names": ["submit_intervention"],
                "usage": {"prompt_tokens": 123, "total_tokens": 123},
            },
        },
    }

    with pytest.raises(PiCoachRuntimeError) as caught:
        PiCoachSidecar._validate_response(response)

    assert caught.value.code == "agent_deadline_exceeded"
    assert caught.value.metrics["elapsed_ms"] == 10_001.0
    assert caught.value.metrics["provider_status_code"] == 503
    assert caught.value.metrics["tool_names"] == ["submit_intervention"]
    assert caught.value.metrics["usage"]["prompt_tokens"] == 123


def test_sidecar_success_reports_safe_queue_startup_and_round_trip_metrics() -> None:
    class Stdin:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, value: str) -> None:
            self.writes.append(value)

        def flush(self) -> None:
            return None

    class Process:
        def __init__(self) -> None:
            self.stdin = Stdin()

        def poll(self):
            return None

    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = Process()
    sidecar._ready.set()
    sidecar._responses.put_nowait(
        {
            "protocol": PROTOCOL,
            "request_id": "request-1",
            "ok": True,
            "action": "silent",
            "intervention": None,
            "metrics": {"turns": 1},
        }
    )

    result = sidecar._evaluate_sync(
        {
            "request_id": "request-1",
            "provider": {"decision_timeout_ms": 1_000},
        }
    )

    assert result["metrics"]["turns"] == 1
    assert result["metrics"]["bridge_process_reused"] is True
    assert result["metrics"]["sidecar_queue_ms"] >= 0
    assert result["metrics"]["bridge_startup_ms"] >= 0
    assert result["metrics"]["bridge_round_trip_ms"] >= 0
    assert result["metrics"]["response_validation_ms"] >= 0
    assert "unused-test-command" not in str(result["metrics"])


@pytest.mark.parametrize("failure", [
    {"ok": False, "error": {"code": "pi_timeout", "metrics": {
        "ttft_ms": None, "provider_status_code": 503, "bridge_startup_ms": -999,
    }}},
    {"ok": True, "action": "invalid"},
])
def test_bridge_failure_keeps_host_phase_clocks(failure) -> None:
    class Process:
        stdin = StringIO()

        def poll(self):
            return None

    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = Process()
    sidecar._ready.set()
    sidecar._responses.put_nowait({
        "protocol": PROTOCOL, "request_id": "failure-1", **failure,
    })
    with pytest.raises(PiCoachRuntimeError) as caught:
        sidecar._evaluate_sync({"request_id": "failure-1"})
    metrics = caught.value.metrics
    for phase in ("sidecar_queue_ms", "bridge_startup_ms",
                  "bridge_round_trip_ms", "response_validation_ms"):
        assert metrics[phase] >= 0
    assert metrics["bridge_process_reused"] is True
    if failure["ok"] is False:
        assert caught.value.code == "pi_timeout"
        assert metrics["provider_status_code"] == 503
        assert metrics["ttft_ms"] is None
    else:
        assert caught.value.code == "pi_protocol_error"


def test_sidecar_timeout_keeps_local_phase_metrics_for_diagnosis() -> None:
    class Stream:
        def write(self, _value: str) -> None:
            return None

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    class Process:
        stdin = Stream()
        stdout = Stream()
        stderr = Stream()

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> None:
            return None

        def kill(self) -> None:
            return None

    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = Process()
    sidecar._ready.set()

    with pytest.raises(PiCoachRuntimeError) as caught:
        sidecar._evaluate_sync(
            {
                "request_id": "request-timeout",
                "provider": {"decision_timeout_ms": 1},
            }
        )

    assert caught.value.code == "pi_timeout"
    assert caught.value.metrics["sidecar_queue_ms"] >= 0
    assert caught.value.metrics["bridge_startup_ms"] >= 0
    assert caught.value.metrics["bridge_round_trip_ms"] >= 0
    assert caught.value.metrics["bridge_process_reused"] is True


def test_async_cancellation_signals_the_blocking_sidecar_worker() -> None:
    class BlockingSidecar(PiCoachSidecar):
        def __init__(self) -> None:
            super().__init__(command=["unused-test-command"])
            self.started = threading.Event()
            self.stopped = threading.Event()

        def _evaluate_sync(self, _payload: dict) -> dict:
            self.started.set()
            self._cancel_requested.wait(timeout=1)
            if self._cancel_requested.is_set():
                self.stopped.set()
                raise PiCoachRuntimeError("superseded", code="pi_cancelled")
            raise AssertionError("sidecar cancellation signal was not delivered")

    async def scenario() -> None:
        sidecar = BlockingSidecar()
        task = asyncio.create_task(sidecar.evaluate({"request_id": "request-1"}))
        assert await asyncio.to_thread(sidecar.started.wait, 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(sidecar.stopped.wait, 0.5)

    asyncio.run(scenario())


def test_sidecar_readers_treat_closed_pipes_as_normal_cancellation() -> None:
    class ClosedPipe:
        def __iter__(self):
            raise ValueError("I/O operation on closed file")

        def read(self, _size):
            raise ValueError("I/O operation on closed file")

    class Process:
        stdout = ClosedPipe()
        stderr = ClosedPipe()

    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = Process()

    sidecar._read_stdout()
    sidecar._read_stderr()

    assert sidecar._ready.is_set()
    assert sidecar._responses.get_nowait() == {"_bridge_error": "Pi bridge output closed"}
    assert sidecar.stderr_bytes_drained == 0


def test_stale_stdout_reader_cannot_inject_error_into_next_generation() -> None:
    """A timed-out bridge generation must not poison the replacement queue."""

    class ClosedPipe:
        def __iter__(self):
            raise ValueError("I/O operation on closed file")

    class Process:
        stdout = ClosedPipe()

    sidecar = PiCoachSidecar(command=["unused-test-command"])
    old_queue: queue.Queue[dict] = queue.Queue(maxsize=8)
    old_ready = threading.Event()
    new_queue: queue.Queue[dict] = queue.Queue(maxsize=8)
    new_ready = threading.Event()

    # Simulate _ensure_started_unlocked() having already replaced the shared
    # generation state while the old reader is still draining its closed pipe.
    sidecar._responses = new_queue
    sidecar._ready = new_ready

    sidecar._read_stdout(Process(), old_queue, old_ready)

    assert old_ready.is_set()
    assert old_queue.get_nowait() == {"_bridge_error": "Pi bridge output closed"}
    assert new_ready.is_set() is False
    with pytest.raises(queue.Empty):
        new_queue.get_nowait()
