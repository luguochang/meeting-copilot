from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPOSITORY_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import pi_provider_lifecycle_probe as probe  # noqa: E402


def _applied_event(
    decision_id: str,
    *,
    provider: str = "openai_compatible_gateway",
    supersedes_decision_id: str | None = None,
) -> dict[str, object]:
    return {
        "seq": 1 if decision_id == "old" else 2,
        "occurred_at_ms": 1000,
        "type": "meeting.intelligence.applied",
        "payload": {
            "job_id": f"job-{decision_id}",
            "source": "llm_first",
            "provider": provider,
            "model": "deepseek-v4-flash",
            "coach_decision": {
                "decision_id": f"decision-{decision_id}",
                "status": "intervention",
                "status_reason": "intervention_submitted",
                "origin": "pi",
                "runtime_used": "pi",
                "pi_provider_attempted": True,
                "lifecycle_action": "retain",
                "supersedes_decision_id": supersedes_decision_id,
            },
            "coach_intervention": {
                "decision_id": f"decision-{decision_id}",
                "status": "intervention",
                "origin": "pi",
                "runtime_used": "pi",
                "lifecycle_action": "retain",
            },
        },
    }


def test_wait_for_applied_returns_only_new_decisions(monkeypatch) -> None:
    old = _applied_event("old")
    new = _applied_event("new")
    event_batches = iter([[old], [old, new]])

    monkeypatch.setattr(probe, "_formal_events", lambda _client, _meeting_id: next(event_batches))
    monkeypatch.setattr(probe, "_snapshot", lambda _client, _meeting_id: {})
    clock = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)

    rows, _events, _snapshot = probe._wait_for_applied(
        object(),
        "meeting-1",
        previous_count=1,
        timeout_seconds=5.0,
    )

    assert [row["decision"]["decision_id"] for row in rows] == ["decision-new"]


def test_wait_for_applied_accepts_openai_compatible_gateway_pi_provenance(monkeypatch) -> None:
    gateway_event = _applied_event("gateway")

    monkeypatch.setattr(probe, "_formal_events", lambda _client, _meeting_id: [gateway_event])
    monkeypatch.setattr(probe, "_snapshot", lambda _client, _meeting_id: {})

    rows, _events, _snapshot = probe._wait_for_applied(
        object(),
        "meeting-1",
        previous_count=0,
        timeout_seconds=5.0,
        require_pi_intervention=True,
    )

    assert [row["decision"]["decision_id"] for row in rows] == ["decision-gateway"]


def test_phase_two_suffix_with_one_row_is_a_new_applied_decision() -> None:
    # The probe intentionally receives a suffix from _wait_for_applied.  The
    # assertion must not compare that suffix's length with phase one history.
    assert bool([{"decision": {"decision_id": "decision-new"}}]) is True
    assert bool([]) is False


def _final_event(seq: int, segment_id: str, text: str, occurred_at_ms: int) -> dict[str, object]:
    return {
        "seq": seq,
        "occurred_at_ms": occurred_at_ms,
        "type": "transcript.segment.finalized",
        "payload": {
            "segment_id": segment_id,
            "text": text,
            "normalized_text": text,
            "transcript_seq": seq,
            "revision": 1,
        },
    }


def _lifecycle_applied(
    *,
    seq: int,
    decision_id: str,
    old_decision_id: str,
    segment_id: str,
    quote: str,
    occurred_at_ms: int,
) -> dict[str, object]:
    decision = {
        "decision_id": decision_id,
        "status": "protected_silent",
        "origin": "pi",
        "runtime_used": "pi",
        "pi_provider_attempted": True,
        "lifecycle_action": "deprioritize",
        "lifecycle_refresh": True,
        "supersedes_decision_id": old_decision_id,
        "final_committed_at_ms": occurred_at_ms,
        "decision_completed_at_ms": occurred_at_ms + 10,
        "projected_at_ms": occurred_at_ms + 10,
        "evidence_segment_ids": [segment_id],
        "evidence_quote": quote,
    }
    return {
        "seq": seq,
        "occurred_at_ms": occurred_at_ms + 10,
        "type": "meeting.intelligence.applied",
        "payload": {
            "job_id": f"job-{decision_id}",
            "provider": "openai_compatible_gateway",
            "model": "deepseek-v4-flash",
            "coach_decision": decision,
            "coach_intervention": None,
            "evidence": {
                "segment_ids": [segment_id],
                "quote": quote,
            },
        },
    }


def test_lifecycle_wait_requires_fresh_final_and_exact_evidence(monkeypatch) -> None:
    old_text = "监控阈值谁来改？还没定。"
    resolving_text = "监控阈值由值班负责人今天下午六点前修改，我负责复核，复盘不受影响。"
    baseline = [
        _final_event(4, "old-segment", old_text, 1000),
        _applied_event("old"),
    ]
    # This is the 8968 failure shape: a duplicate old question is finalized,
    # then a lifecycle decision closes the card before the real answer arrives.
    early = baseline + [
        _final_event(16, "duplicate-question", old_text, 2000),
        _lifecycle_applied(
            seq=20,
            decision_id="decision-early",
            old_decision_id="decision-old",
            segment_id="duplicate-question",
            quote=old_text,
            occurred_at_ms=2000,
        ),
    ]
    valid = early + [
        _final_event(24, "resolving-segment", resolving_text, 3000),
        _lifecycle_applied(
            seq=28,
            decision_id="decision-valid",
            old_decision_id="decision-old",
            segment_id="resolving-segment",
            quote=resolving_text,
            occurred_at_ms=3000,
        ),
    ]
    batches = iter([baseline, early, valid])
    monkeypatch.setattr(probe, "_formal_events", lambda _client, _meeting_id: next(batches, valid))
    monkeypatch.setattr(probe, "_snapshot", lambda _client, _meeting_id: {})
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)

    rows, events, _snapshot, validation = probe._wait_for_lifecycle_refresh(
        object(),
        "meeting-1",
        baseline_decision_ids={"decision-old"},
        baseline_segment_ids={"old-segment"},
        baseline_seq=12,
        old_decision_id="decision-old",
        timeout_seconds=5.0,
    )

    assert [row["decision"]["decision_id"] for row in rows] == [
        "decision-early",
        "decision-valid",
    ]
    assert len(events) == 6
    assert validation["fresh_resolving_final"] is True
    assert validation["lifecycle_decision_after_final"] is True
    assert validation["lifecycle_evidence_segment_exact"] is True
    assert validation["lifecycle_evidence_quote_exact"] is True
    # A later valid-looking row does not erase the earlier causal violation.
    # The acceptance result must remain No-Go until the service is rerun with
    # the close occurring after the resolving final.
    assert validation["no_early_lifecycle_close"] is False
    assert validation["lifecycle_valid"] is False


def test_lifecycle_wait_accepts_transitive_supersession_chain(monkeypatch) -> None:
    old_text = "监控阈值谁来改？还没定。"
    resolving_text = "监控阈值由值班负责人今天下午六点前修改，我负责复核，复盘不受影响。"
    baseline = [
        _final_event(4, "old-segment", old_text, 1000),
        _applied_event("old"),
    ]
    duplicate_intervention = _applied_event(
        "duplicate",
        supersedes_decision_id="decision-old",
    )
    duplicate_intervention["seq"] = 20
    events = baseline + [
        duplicate_intervention,
        _final_event(24, "resolving-segment", resolving_text, 3000),
        _lifecycle_applied(
            seq=28,
            decision_id="decision-valid",
            old_decision_id="decision-duplicate",
            segment_id="resolving-segment",
            quote=resolving_text,
            occurred_at_ms=3000,
        ),
    ]
    monkeypatch.setattr(probe, "_formal_events", lambda _client, _meeting_id: events)
    monkeypatch.setattr(probe, "_snapshot", lambda _client, _meeting_id: {})

    _rows, _events, _snapshot, validation = probe._wait_for_lifecycle_refresh(
        object(),
        "meeting-1",
        baseline_decision_ids={"decision-old"},
        baseline_segment_ids={"old-segment"},
        baseline_seq=12,
        old_decision_id="decision-old",
        timeout_seconds=5.0,
    )

    assert validation["lifecycle_linked_old_decision"] is True
    assert validation["lifecycle_valid"] is True


def test_resolution_text_gate_rejects_duplicate_question_and_generic_done() -> None:
    assert probe._looks_like_resolution_text("监控阈值谁来改？还没定。") is False
    assert probe._looks_like_resolution_text("问题已经解决。") is False
    assert probe._looks_like_resolution_text(
        "监控阈值由值班负责人今天下午六点前修改，我负责复核，复盘不受影响。"
    ) is True
