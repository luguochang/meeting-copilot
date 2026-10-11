from __future__ import annotations

import asyncio
import queue
import subprocess
import sys
import threading
import time
from dataclasses import replace
from io import StringIO

import pytest

from meeting_copilot_web_mvp.coach_contract import COACH_ROLE_CONTRACT, COACH_ROLE_CONTRACT_VERSION
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


def _serialize_context(request):
    return build_pi_coach_request(
        request, request_id="role-context", base_url="https://gateway.example.test",
        api_key="private-test-key", model="coach-model", api_style="chat_completions",
        timeout_seconds=10,
    )["context"]


def test_pi_role_contract_carries_explicit_role_focus_glossary_and_full_goal():
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="pi-role-context", state_revision=1,
        new_paragraphs=[{"id": "s1", "text": "我们在核对发布条件。", "revision": 2,
                         "correction_status": "changed"}],
        context_paragraphs=[], rolling_state={}, meeting_goal="核对发布条件",
        participant_role="项目主持人", focus_points=["放行条件", "未完成事项"],
        glossary=["P99", "checkout-service"], coach_skill_id="project",
    )
    context = _serialize_context(request)

    assert context["role_contract"] == COACH_ROLE_CONTRACT
    assert context["role_contract_version"] == COACH_ROLE_CONTRACT_VERSION
    assert context["meeting_goal"] == "核对发布条件"
    assert context["participant_role"] == "项目主持人"
    assert context["focus_points"] == ["放行条件", "未完成事项"]
    assert context["glossary"] == ["P99", "checkout-service"]
    assert context["new_paragraphs"][0]["correction_status"] == "changed"
    assert context["new_paragraphs"][0]["revision"] == 2


def test_pi_legacy_delimited_goal_is_preserved_and_explicit_role_wins():
    goal = "了解真实需求；参会角色：旧主持人；重点关注：频率、替代方案；输出格式：brief"
    request = replace(_request(), meeting_goal=goal)
    legacy = _serialize_context(request)
    assert legacy["meeting_goal"] == goal
    assert legacy["participant_role"] == "旧主持人"
    assert legacy["focus_points"] == ["频率", "替代方案"]

    explicit = _serialize_context(replace(request, participant_role="新访谈者", focus_points=("具体行为",)))
    assert explicit["participant_role"] == "新访谈者"
    assert explicit["focus_points"] == ["具体行为"]


def test_pi_mapping_goal_is_not_dropped_and_plain_goal_does_not_infer_identity():
    structured = {"meeting_goal": "核对交付范围", "participant_role": "主持人",
                  "focus_points": ["验收前提"]}
    context = _serialize_context(replace(_request(), meeting_goal=structured))
    assert context["meeting_goal"] == structured
    assert context["participant_role"] == "主持人"
    assert context["focus_points"] == ["验收前提"]

    plain = _serialize_context(replace(_request(), meeting_goal="说明参会角色：主持人如何推进讨论"))
    assert plain["participant_role"] is None
    assert plain["focus_points"] == []


def test_pi_preserves_all_host_validated_glossary_terms():
    glossary = tuple(f"业务术语{index}" for index in range(100))
    assert _serialize_context(replace(_request(), glossary=glossary))["glossary"] == list(glossary)


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
    assert payload["provider"]["timeout_ms"] == 25_000
    assert payload["provider"]["decision_timeout_ms"] == 25_000


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
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

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


def _silent_pipe_sidecar() -> PiCoachSidecar:
    """Real child/production reader threads, with no SDK or Provider work."""
    code = (
        "import json,time;print(json.dumps("
        + repr({"protocol": PROTOCOL, "event": "ready", "capabilities": []})
        + "),flush=True);time.sleep(30)"
    )
    sidecar = PiCoachSidecar(command=[sys.executable, "-u", "-c", code])
    with sidecar._lock:
        sidecar._ensure_started_unlocked()
    assert sidecar._process is not None and sidecar._process.poll() is None
    assert sidecar._ready.is_set()
    # After the ready line, stdout waits for its next line while stderr's
    # read(4096) waits on a live, empty pipe. Closing either first can deadlock.
    time.sleep(0.05)
    return sidecar


def test_real_blocking_pipe_cancellation_exits_promptly_and_cannot_poison_next_channel() -> None:
    sidecar = _silent_pipe_sidecar()
    process = sidecar._process
    old_queue = sidecar._responses
    next_queue: queue.Queue[dict] = queue.Queue(maxsize=8)
    next_ready = threading.Event()
    sidecar._responses = next_queue
    sidecar._ready = next_ready
    watchdog_fired = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def watchdog() -> None:
        watchdog_fired.set()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)

    def cancel() -> None:
        try:
            sidecar.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    guard = threading.Timer(3.0, watchdog)
    guard.daemon = True
    worker = threading.Thread(target=cancel, daemon=True)
    started = time.perf_counter()
    guard.start()
    worker.start()
    try:
        assert finished.wait(timeout=4), "stop did not return after watchdog cleanup"
        assert time.perf_counter() - started < 1
        assert not watchdog_fired.is_set()
        assert not errors
        assert process.poll() is not None
        assert sidecar._process is None
        for reader in (sidecar._reader, sidecar._stderr_reader):
            reader.join(timeout=1)
            assert not reader.is_alive()
        assert old_queue.get_nowait() == {"_bridge_error": "Pi bridge output closed"}
        assert next_ready.is_set() is False
        with pytest.raises(queue.Empty):
            next_queue.get_nowait()
        sidecar.close()  # Repeated cancellation has no process left to stop.
    finally:
        guard.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        worker.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def test_real_already_exited_bridge_can_be_closed_without_restarting() -> None:
    sidecar = _silent_pipe_sidecar()
    process = sidecar._process
    try:
        process.terminate()
        process.wait(timeout=1)
        sidecar.close()
        assert sidecar._process is None
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        sidecar.close()


def test_stop_escalates_to_kill_and_closes_only_after_confirmed_exit() -> None:
    class Stream:
        closed = False

        def close(self) -> None:
            assert process.poll() is not None
            self.closed = True

    class Process:
        def __init__(self) -> None:
            self.stdin, self.stdout, self.stderr = Stream(), Stream(), Stream()
            self.returncode = None
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminate_calls += 1

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fixed-test-child", timeout)
            return self.returncode

        def kill(self) -> None:
            self.kill_calls += 1
            self.returncode = -9

    process = Process()
    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = process
    sidecar.close()
    assert process.terminate_calls == process.kill_calls == 1
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    assert sidecar._process is None


def test_unconfirmed_exit_retains_process_and_never_closes_live_pipes() -> None:
    class Stream:
        closed = False

        def close(self) -> None:
            assert process.poll() is not None
            self.closed = True

    class Process:
        def __init__(self) -> None:
            self.stdin, self.stdout, self.stderr = Stream(), Stream(), Stream()
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            raise OSError("fixed-test-termination-denied")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("fixed-test-child", timeout)

        def kill(self) -> None:
            raise OSError("fixed-test-kill-denied")

    process = Process()
    sidecar = PiCoachSidecar(command=["unused-test-command"])
    sidecar._process = process
    sidecar._ready.set()
    with pytest.raises(PiCoachRuntimeError) as caught:
        sidecar.close()
    assert caught.value.code == "pi_stop_unconfirmed"
    assert sidecar._process is process
    assert not sidecar._ready.is_set()
    assert not any(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    # A later cleanup can confirm exit and release the retained pipe handles.
    process.returncode = -9
    sidecar.close()
    assert sidecar._process is None
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
