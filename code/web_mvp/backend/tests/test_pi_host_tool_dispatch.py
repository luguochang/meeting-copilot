import json
from io import StringIO

import pytest

from meeting_copilot_web_mvp.pi_coach_runtime import PROTOCOL, PiCoachSidecar, PiCoachRuntimeError


ROW = {"segment_id": "older", "normalized_text": "Approval is required.", "revision": 1,
       "source_track": "system_audio", "correction_status": "no_change"}


class Registry:
    def search(self, query, *, limit):
        assert query == "Approval"
        return [dict(ROW)]

    def validate(self, quotes):
        assert quotes == {"older": ROW["normalized_text"]}
        return [dict(ROW)]

    def read_span(self, segment_id, *, before, after):
        assert segment_id == "older"
        assert (before, after) == (1, 1)
        return [dict(ROW), {**ROW, "segment_id": "neighbor", "normalized_text": "Adjacent."}]


def sidecar(registry=None):
    class Process:
        def __init__(self):
            self.stdin = StringIO()

        def poll(self):
            return None

    runtime = PiCoachSidecar(command=["unused"], evidence_registry_factory=(
        (lambda meeting_id: registry) if registry is not None else None
    ))
    runtime._process = Process()
    runtime._ready.set()
    runtime._stop_unlocked = lambda: None
    return runtime


def tool(**overrides):
    return {"protocol": PROTOCOL, "event": "host_tool_request", "request_id": "run",
            "call_id": "evidence-1", "tool": "search_prior_evidence",
            "arguments": {"query": "Approval", "max_results": 4}, **overrides}


def span_tool(**overrides):
    return {"protocol": PROTOCOL, "event": "host_tool_request", "request_id": "run",
            "call_id": "evidence-1", "tool": "read_transcript_span",
            "arguments": {"segment_id": "older", "before": 1, "after": 1}, **overrides}


def terminal():
    return {"protocol": PROTOCOL, "request_id": "run", "ok": True, "action": "intervention",
            "intervention": {"evidence_segment_ids": ["older"]},
            "host_evidence": [{"text": "untrusted bridge text"}]}


def test_dispatch_returns_only_host_revalidated_evidence():
    runtime = sidecar(Registry())
    runtime._responses.put(tool())
    runtime._responses.put(terminal())
    result = runtime._evaluate_sync({"request_id": "run", "session_id": "meeting"})
    assert result["host_evidence"] == [ROW]
    messages = [json.loads(line) for line in runtime._process.stdin.getvalue().splitlines()]
    assert messages[0]["host_evidence_enabled"] is True
    assert messages[1]["results"][0]["text"] == ROW["normalized_text"]


def test_dispatch_supports_read_transcript_span_and_registers_results():
    runtime = sidecar(Registry())
    runtime._responses.put(span_tool())
    runtime._responses.put(terminal())
    result = runtime._evaluate_sync({"request_id": "run", "session_id": "meeting"})
    assert result["host_evidence"] == [ROW]
    messages = [json.loads(line) for line in runtime._process.stdin.getvalue().splitlines()]
    assert [row["id"] for row in messages[1]["results"]] == ["older", "neighbor"]


@pytest.mark.parametrize("overrides", [{"request_id": "other"}, {"call_id": "evidence-9"},
                                       {"tool": "read_file"}])
def test_dispatch_rejects_unbound_or_unsupported_requests(overrides):
    runtime = sidecar(Registry())
    runtime._responses.put(tool(**overrides))
    with pytest.raises(PiCoachRuntimeError, match="request|mismatch"):
        runtime._evaluate_sync({"request_id": "run", "session_id": "meeting"})


def test_bridge_cannot_inject_evidence_without_host_registry():
    runtime = sidecar()
    runtime._responses.put(terminal())
    result = runtime._evaluate_sync({"request_id": "run", "host_evidence_enabled": True})
    assert "host_evidence" not in result
    assert json.loads(runtime._process.stdin.getvalue())["host_evidence_enabled"] is False


def test_revised_evidence_fails_before_leaving_sidecar():
    class ChangedRegistry(Registry):
        def validate(self, quotes):
            raise ValueError("private database details")
    runtime = sidecar(ChangedRegistry())
    runtime._responses.put(tool())
    runtime._responses.put(terminal())
    with pytest.raises(PiCoachRuntimeError) as caught:
        runtime._evaluate_sync({"request_id": "run", "session_id": "meeting"})
    assert caught.value.code == "pi_evidence_superseded"
    assert "private" not in str(caught.value)


def test_oversized_evidence_returns_bounded_failure_not_partial_text():
    class LargeRegistry(Registry):
        def search(self, query, *, limit):
            return [{**ROW, "normalized_text": "x" * 50000}]
    runtime = sidecar(LargeRegistry())
    runtime._responses.put(tool())
    runtime._responses.put(terminal())
    result = runtime._evaluate_sync({"request_id": "run", "session_id": "meeting"})
    assert "host_evidence" not in result
    reply = json.loads(runtime._process.stdin.getvalue().splitlines()[1])
    assert reply["ok"] is False
    assert "results" not in reply


def test_cancel_during_query_does_not_send_evidence():
    class CancellingRegistry(Registry):
        def search(self, query, *, limit):
            runtime._cancel_requested.set()
            return [ROW]
    runtime = sidecar(CancellingRegistry())
    runtime._responses.put(tool())
    with pytest.raises(PiCoachRuntimeError) as caught:
        runtime._evaluate_sync({"request_id": "run", "session_id": "meeting"})
    assert caught.value.code == "pi_timeout"
    assert len(runtime._process.stdin.getvalue().splitlines()) == 1
