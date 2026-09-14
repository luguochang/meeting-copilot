from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import wave

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import pi_stage0_production_replay as replay  # noqa: E402


def _ready_asr_runtime() -> dict:
    return {
        "schema_version": "asr_runtime_status.v1",
        "realtime_available": True,
        "resident_enabled": True,
        "resident": {
            "spawned": True,
            "process_running": True,
            "process_ready": True,
            "pid": 101,
            "process_start_count": 1,
            "last_exit_code": None,
            "last_error": None,
        },
        "offline_refinement": {
            "capability": {
                "status": "ready",
                "process_resident": True,
                "realtime_policy": {
                    "schema_version": "realtime_refiner_policy.v1",
                    "mode": "prewarm",
                    "source": "environment",
                    "realtime_refinement_enabled": True,
                    "prewarm_enabled": True,
                    "degradation_reason": None,
                },
            },
            "worker": {
                "spawned": True,
                "process_running": True,
                "process_ready": True,
                "pid": 102,
                "process_start_count": 1,
            },
        },
    }


def _online_only_asr_runtime() -> dict:
    runtime = _ready_asr_runtime()
    runtime["offline_refinement"] = {
        "capability": {
            "status": "ready",
            "process_resident": True,
            "realtime_policy": {
                "schema_version": "realtime_refiner_policy.v1",
                "mode": "online_only",
                "source": "default_resource_guard",
                "realtime_refinement_enabled": False,
                "prewarm_enabled": False,
                "degradation_reason": "offline_refinement_bypassed_by_resource_policy",
            },
        },
        "worker": {
            "spawned": False,
            "process_running": False,
            "process_ready": False,
            "pid": None,
            "process_start_count": 0,
        },
    }
    return runtime


class _RuntimeValidationClient:
    def __init__(self, asr_runtime: dict):
        self.asr_runtime = asr_runtime

    def request(self, method, path, *, payload=None, timeout_seconds=None):
        del method, payload, timeout_seconds
        if path == "/health":
            return replay.HttpResult(200, {"status": "ok"}, 1.0)
        if path == "/providers/health":
            return replay.HttpResult(
                200,
                {
                    "llm": {
                        "configured": True,
                        "is_mock": False,
                        "model": "gpt-5.5",
                        "realtime_model": "gpt-5.4-mini",
                        "realtime_model_source": "runtime_realtime_model",
                        "realtime_model_explicit": True,
                        "realtime_model_warning": None,
                    },
                    "asr": {"realtime_asr_available": True},
                },
                1.0,
            )
        if path == "/providers/asr/runtime":
            return replay.HttpResult(200, self.asr_runtime, 1.0)
        if path == "/settings":
            return replay.HttpResult(
                200,
                {
                    "asr": {
                        "l2_correction_enabled": True,
                        "l3_normalize_enabled": True,
                    }
                },
                1.0,
            )
        raise AssertionError(f"unexpected request: {path}")


def test_validate_runtime_requires_ready_resident_and_refiner_workers():
    result = replay.validate_runtime(
        _RuntimeValidationClient(_ready_asr_runtime()),
        allow_mock_llm=False,
    )

    assert result["validated"] is True
    assert result["realtime_model"] == {
        "general_model": "gpt-5.5",
        "selected_model": "gpt-5.4-mini",
        "source": "runtime_realtime_model",
        "explicit": True,
        "warning": None,
    }
    assert result["asr_runtime"]["resident"]["process_ready"] is True
    assert result["asr_runtime"]["offline_refinement"]["worker"]["process_ready"] is True


def test_prewarm_refiner_if_needed_restores_stale_prewarm_worker():
    runtime = _ready_asr_runtime()
    runtime["offline_refinement"]["worker"].update(
        {"spawned": False, "process_running": False, "process_ready": False, "pid": None}
    )

    class Client(_RuntimeValidationClient):
        def __init__(self):
            super().__init__(runtime)
            self.calls: list[tuple[str, str]] = []

        def request(self, method, path, *, payload=None, timeout_seconds=None):
            self.calls.append((method, path))
            if method == "POST" and path == "/providers/asr/prewarm":
                return replay.HttpResult(
                    200,
                    {
                        "ok": True,
                        "started": True,
                        "worker": {
                            "spawned": True,
                            "process_running": True,
                            "process_ready": True,
                        },
                    },
                    1.0,
                )
            return super().request(
                method,
                path,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )

    client = Client()
    result = replay.prewarm_refiner_if_needed(client)

    assert result == {
        "attempted": True,
        "mode": "prewarm",
        "started": True,
        "worker_ready": True,
    }
    assert client.calls == [
        ("GET", "/providers/asr/runtime"),
        ("POST", "/providers/asr/prewarm"),
    ]


def test_validate_runtime_accepts_online_only_without_spawning_refiner():
    result = replay.validate_runtime(
        _RuntimeValidationClient(_online_only_asr_runtime()),
        allow_mock_llm=False,
    )

    assert result["validated"] is True
    assert result["refiner_policy"]["mode"] == "online_only"


def test_validate_runtime_rejects_online_only_refiner_spawn():
    runtime = _online_only_asr_runtime()
    runtime["offline_refinement"]["worker"].update(
        {"spawned": True, "process_running": True, "process_ready": True, "pid": 102, "process_start_count": 1}
    )

    with pytest.raises(replay.ReplayFailure, match="violates online_only policy"):
        replay.validate_runtime(
            _RuntimeValidationClient(runtime),
            allow_mock_llm=False,
        )


def test_validate_runtime_rejects_inherited_realtime_model():
    client = _RuntimeValidationClient(_ready_asr_runtime())
    original_request = client.request

    def request(method, path, *, payload=None, timeout_seconds=None):
        result = original_request(
            method,
            path,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if path == "/providers/health":
            result.payload["llm"].update(
                {
                    "realtime_model": "gpt-5.5",
                    "realtime_model_source": "general_model_fallback",
                    "realtime_model_explicit": False,
                    "realtime_model_warning": "realtime_model_inherits_general_model",
                }
            )
        return result

    client.request = request

    with pytest.raises(replay.ReplayFailure, match="must be configured explicitly"):
        replay.validate_runtime(client, allow_mock_llm=False)


def test_validate_runtime_rejects_disabled_l2_correction():
    client = _RuntimeValidationClient(_ready_asr_runtime())
    original_request = client.request

    def request(method, path, *, payload=None, timeout_seconds=None):
        result = original_request(
            method,
            path,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if path == "/settings":
            result.payload["asr"]["l2_correction_enabled"] = False
        return result

    client.request = request

    with pytest.raises(replay.ReplayFailure, match="L2 transcript correction must be enabled"):
        replay.validate_runtime(client, allow_mock_llm=False)


def test_snapshot_jobs_settled_requires_every_durable_job_to_be_terminal():
    snapshot = {
        "runtime": {"phase": "ended"},
        "audio": {"status": "saved"},
        "jobs": [
            {"id": "intelligence-1", "status": "succeeded"},
            {"id": "correction-1", "status": "running"},
        ],
        "review_jobs": {"minutes": {"status": "succeeded"}},
    }

    assert replay.snapshot_jobs_settled(snapshot) is False
    snapshot["jobs"][1]["status"] = "cancelled"
    assert replay.snapshot_jobs_settled(snapshot) is True
    del snapshot["jobs"]
    assert replay.snapshot_jobs_settled(snapshot) is False


def test_wait_after_end_does_not_ignore_active_correction_job():
    snapshots = [
        {
            "runtime": {"phase": "ended"},
            "audio": {"status": "saved"},
            "jobs": [{"id": "correction-1", "kind": "correction", "status": "retry_wait"}],
            "review_jobs": {},
        },
        {
            "runtime": {"phase": "ended"},
            "audio": {"status": "saved"},
            "jobs": [{"id": "correction-1", "kind": "correction", "status": "succeeded"}],
            "review_jobs": {},
        },
    ]

    class Client:
        def __init__(self):
            self.calls = 0

        def request(self, method, path, *, payload=None, timeout_seconds=None):
            del method, path, payload, timeout_seconds
            snapshot = snapshots[min(self.calls, len(snapshots) - 1)]
            self.calls += 1
            return replay.HttpResult(200, snapshot, 1.0)

    client = Client()
    snapshot, settled = replay.wait_after_end(
        client,
        meeting_id="meeting-1",
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    assert settled is True
    assert client.calls == 2
    assert snapshot["jobs"][0]["status"] == "succeeded"


@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        (
            lambda runtime: runtime["resident"].update(
                {"spawned": False, "process_running": False, "process_ready": False, "pid": None}
            ),
            "realtime ASR resident worker is not healthy",
        ),
        (
            lambda runtime: runtime["resident"].update(
                {"last_exit_code": 9, "last_error": "worker crashed"}
            ),
            "last_exit_code, last_error",
        ),
        (
            lambda runtime: runtime["offline_refinement"]["worker"].update(
                {"spawned": False, "process_running": False, "process_ready": False, "pid": None}
            ),
            "offline ASR refiner worker is not healthy",
        ),
        (
            lambda runtime: runtime["offline_refinement"]["capability"].update(
                {"status": "degraded", "process_resident": False}
            ),
            "offline ASR refiner capability is not resident and ready",
        ),
    ],
)
def test_validate_runtime_fails_closed_when_required_process_is_not_healthy(
    mutate,
    expected_message,
):
    asr_runtime = _ready_asr_runtime()
    mutate(asr_runtime)

    with pytest.raises(replay.ReplayFailure, match=expected_message):
        replay.validate_runtime(
            _RuntimeValidationClient(asr_runtime),
            allow_mock_llm=False,
        )


def _write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def test_wav_contract_and_pcm_conversion(tmp_path: Path):
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path, [-32768, -16384, 0, 16384, 32767])

    info = replay.inspect_wav(wav_path)
    chunks = list(replay.iter_float32le_chunks(wav_path, chunk_frames=3))
    decoded = [
        value for payload, _frames in chunks for value in struct.unpack("<" + "f" * (len(payload) // 4), payload)
    ]

    assert info.compression == "PCM_S16LE"
    assert info.sample_rate_hz == 16_000
    assert info.channels == 1
    assert info.frame_count == 5
    assert [frames for _payload, frames in chunks] == [3, 2]
    assert decoded == [-1.0, -0.5, 0.0, 0.5, 32767 / 32768.0]


def test_extract_decisions_distinguishes_intervention_and_explicit_silence():
    events = [
        {
            "seq": 4,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-1",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {
                    "status": "protected_silent",
                    "origin": "pi",
                    "decision_reason": "No high-value interruption.",
                },
                "coach_intervention": None,
            },
        },
        {
            "seq": 8,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-2",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {"status": "intervention", "origin": "pi"},
                "coach_intervention": {
                    "recommendation": "Confirm the rollback owner.",
                    "reason": "The owner is still missing.",
                },
            },
        },
    ]

    decisions = replay.extract_decisions(events)

    assert [item["outcome"] for item in decisions] == [
        "explicit_silence",
        "intervention",
    ]


@pytest.mark.parametrize(
    ("job_status", "error_class", "expected_projection", "expected_drop_reason"),
    [
        ("cancelled", "deadline_exceeded", "timed_out", "deadline_exceeded"),
        ("cancelled", "evidence_superseded", "stale", "evidence_superseded"),
        ("failed", "NonRetryableProviderError", "failed", "NonRetryableProviderError"),
        ("cancelled", None, "cancelled", "cancelled"),
    ],
)
def test_unapplied_terminal_intelligence_jobs_are_audited(
    job_status,
    error_class,
    expected_projection,
    expected_drop_reason,
):
    snapshot = {
        "jobs": [
            {
                "id": "job-terminal",
                "kind": "intelligence",
                "status": job_status,
                "attempts": 1,
                "max_attempts": 3,
                "error_class": error_class,
                "created_at_ms": 1_000,
                "updated_at_ms": 2_100,
                "completed_at_ms": 2_000,
            }
        ]
    }

    jobs = replay.build_intelligence_job_audit(snapshot, [])
    decisions = replay.build_decision_audit(snapshot, [], job_audit=jobs)

    assert jobs == [
        {
            "schema_version": "meeting_copilot.pi_stage0_intelligence_job_audit.v1",
            "record_type": "intelligence_job",
            "job_id": "job-terminal",
            "kind": "intelligence",
            "job_status": job_status,
            "projection_status": expected_projection,
            "applied": False,
            "applied_event_seq": None,
            "coach_status": None,
            "created_at_ms": 1_000,
            "completed_at_ms": 2_000,
            "dropped_at_ms": 2_000,
            "drop_reason": expected_drop_reason,
            "error_class": error_class,
            "deadline_at_ms": 11_000,
            "deadline_source": "derived_from_job_created_at",
            "attempts": 1,
            "max_attempts": 3,
        }
    ]
    assert decisions[0]["record_type"] == "terminal_without_applied_decision"
    assert decisions[0]["outcome"] == expected_projection
    assert decisions[0]["created_at_ms"] == 1_000
    assert decisions[0]["completed_at_ms"] == 2_000
    assert decisions[0]["dropped_at_ms"] == 2_000
    assert decisions[0]["drop_reason"] == expected_drop_reason
    assert decisions[0]["error_class"] == error_class
    assert decisions[0]["deadline_at_ms"] == 11_000


def test_terminal_job_past_derived_deadline_is_classified_as_timeout_without_error_field():
    snapshot = {
        "jobs": [
            {
                "id": "job-late",
                "kind": "intelligence",
                "status": "cancelled",
                "error_class": None,
                "created_at_ms": 1_000,
                "updated_at_ms": 11_500,
                "completed_at_ms": 11_500,
            }
        ]
    }

    record = replay.build_intelligence_job_audit(snapshot, [])[0]

    assert record["projection_status"] == "timed_out"
    assert record["drop_reason"] == "deadline_exceeded"
    assert record["deadline_at_ms"] == 11_000
    assert record["dropped_at_ms"] == 11_500


def test_e2e_latency_audit_joins_durable_final_job_decision_and_projection():
    snapshot = {
        "jobs": [
            {
                "id": "job-e2e",
                "kind": "intelligence",
                "status": "succeeded",
                "evidence_segment_id": "segment-e2e",
                "created_at_ms": 1_150,
                "completed_at_ms": 2_650,
            }
        ]
    }
    events = [
        {
            "seq": 1,
            "type": "transcript.segment.finalized",
            "occurred_at_ms": 1_000,
            "payload": {"segment_id": "segment-e2e"},
        },
        {
            "seq": 2,
            "type": "meeting.intelligence.applied",
            "occurred_at_ms": 2_700,
            "payload": {
                "job_id": "job-e2e",
                "coach_decision": {
                    "status": "intervention",
                    "created_at_ms": 1_250,
                    "completed_at_ms": 2_600,
                    "projected_at_ms": 2_700,
                },
                "coach_intervention": {
                    "recommendation": "确认负责人。",
                    "reason": "负责人尚未明确。",
                },
            },
        },
    ]

    audit = replay.build_e2e_latency_audit(snapshot, events)

    assert audit["valid_count"] == 1
    assert audit["invalid_count"] == 0
    assert audit["e2e_latency_ms"] == {"p50": 1_700.0, "p95": 1_700.0, "max": 1_700.0}
    assert audit["enqueue_latency_ms"] == {"p50": 150.0, "p95": 150.0, "max": 150.0}
    assert audit["queue_latency_ms"] == {"p50": 100.0, "p95": 100.0, "max": 100.0}
    assert audit["execution_latency_ms"] == {"p50": 1_350.0, "p95": 1_350.0, "max": 1_350.0}
    assert audit["projection_latency_ms"] == {"p50": 100.0, "p95": 100.0, "max": 100.0}
    assert audit["records"][0]["intervention"] is True


def test_e2e_latency_audit_is_fail_closed_when_projection_timing_is_missing():
    snapshot = {
        "jobs": [
            {
                "id": "job-missing-timing",
                "kind": "intelligence",
                "status": "succeeded",
                "evidence_segment_id": "segment-missing-timing",
                "created_at_ms": 1_000,
                "completed_at_ms": 1_500,
            }
        ]
    }
    events = [
        {
            "type": "transcript.segment.finalized",
            "occurred_at_ms": 900,
            "payload": {"segment_id": "segment-missing-timing"},
        },
        {
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-missing-timing",
                "coach_decision": {
                    "status": "protected_silent",
                    "created_at_ms": 1_100,
                    "completed_at_ms": 1_500,
                },
            },
        },
    ]

    audit = replay.build_e2e_latency_audit(snapshot, events)

    assert audit["valid_count"] == 0
    assert audit["invalid_count"] == 1
    assert audit["e2e_latency_ms"] == {"p50": None, "p95": None, "max": None}
    assert audit["records"][0]["valid"] is False
    assert "missing_projected_at" in audit["records"][0]["invalid_reasons"]


def test_acceptance_adds_e2e_latency_gates_when_audit_is_supplied():
    base = {
        "jobs": [{"id": "job-e2e-gate", "kind": "intelligence", "status": "succeeded"}],
        "runtime": {"phase": "live"},
    }
    events = [
        {
            "type": "recording.export.ready",
            "payload": {"output": {"source_type": "simulated_realtime_wav"}},
        },
        {
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-e2e-gate",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {"status": "protected_silent", "origin": "pi"},
                "coach_intervention": None,
            },
        },
    ]
    e2e = {
        "invalid_count": 0,
        "e2e_latency_ms": {"p50": 2_000.0, "p95": 4_000.0, "max": 8_000.0},
    }
    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={"create_meeting": 201, "save_preparation": 200, "end_meeting": 200},
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 1,
            "event_counts": {"final": 1},
        },
        snapshot=base,
        transcript={"segments": [{"source_track": "microphone"}]},
        events=events,
        evidence_complete=True,
        post_end_settled=True,
        e2e_latency=e2e,
    )

    assert result["checks"]["e2e_latency_complete"] is True
    assert result["checks"]["e2e_latency_p50_ms"] is True
    assert result["checks"]["e2e_latency_p95_ms"] is True
    assert result["checks"]["e2e_latency_max_ms"] is True


@pytest.mark.parametrize(
    "decision, expected",
    [
        ({"llm_called": True}, True),
        ({"llm_call_status": "called"}, True),
        ({"agent_metrics": {"provider_attempted": True}}, True),
        ({"llm_call_status": "not_called", "agent_metrics": {"provider_attempted": False}}, False),
    ],
)
def test_acceptance_reads_pi_provider_call_from_nested_decision_provenance(decision, expected):
    snapshot = {
        "jobs": [{"id": "job-nested-call", "kind": "intelligence", "status": "succeeded"}],
        "runtime": {"phase": "live"},
    }
    events = [
        {
            "type": "recording.export.ready",
            "payload": {"output": {"source_type": "simulated_realtime_wav"}},
        },
        {
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-nested-call",
                "source": "llm_first",
                "coach_decision": {"status": "timed_out", "origin": "pi", **decision},
                "coach_intervention": None,
            },
        },
    ]
    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={"create_meeting": 201, "save_preparation": 200, "end_meeting": 200},
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 1,
            "event_counts": {"final": 1},
        },
        snapshot=snapshot,
        transcript={"segments": [{"source_track": "microphone"}]},
        events=events,
        evidence_complete=True,
        post_end_settled=True,
    )

    assert result["checks"]["llm_called"] is expected


def test_acceptance_uses_decorated_ui_snapshot_for_end_projection_gate():
    durable_snapshot = {
        "jobs": [{"id": "job-late", "kind": "intelligence", "status": "succeeded"}],
        "runtime": {"phase": "ended"},
        "coach_decision": {"status": "timed_out", "origin": "pi"},
    }
    ui_snapshot = {
        "jobs": durable_snapshot["jobs"],
        "runtime": {"phase": "ended"},
        "follow_up": None,
        "semantic_follow_up": None,
        "coach_decision": None,
    }
    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={"create_meeting": 201, "save_preparation": 200, "end_meeting": 200},
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 1,
            "event_counts": {"final": 1},
        },
        snapshot=durable_snapshot,
        ui_snapshot=ui_snapshot,
        transcript={"segments": [{"source_track": "microphone"}]},
        events=[],
        evidence_complete=True,
        post_end_settled=True,
    )

    assert result["checks"]["ended_coach_projection_cleared"] is True
    assert result["projection_snapshot_source"] == "ui_snapshot"


def test_explicit_failure_class_wins_over_derived_deadline():
    snapshot = {
        "jobs": [
            {
                "id": "job-provider-failure",
                "kind": "intelligence",
                "status": "failed",
                "error_class": "NonRetryableProviderError",
                "created_at_ms": 1_000,
                "updated_at_ms": 12_000,
                "completed_at_ms": 12_000,
            }
        ]
    }

    record = replay.build_intelligence_job_audit(snapshot, [])[0]

    assert record["projection_status"] == "failed"
    assert record["drop_reason"] == "NonRetryableProviderError"
    assert record["deadline_at_ms"] == 11_000


def test_applied_decision_contract_is_preserved_with_lifecycle_audit_fields():
    event = {
        "seq": 8,
        "occurred_at_ms": 2_100,
        "type": "meeting.intelligence.applied",
        "payload": {
            "job_id": "job-applied",
            "batch_id": "batch-applied",
            "source": "llm_first",
            "llm_called": True,
            "provider": "provider",
            "model": "model",
            "evidence": {"segment_ids": ["segment-1"]},
            "coach_decision": {
                "status": "protected_silent",
                "origin": "pi",
                "created_at_ms": 2_000,
                "completed_at_ms": 2_050,
                "deadline_at_ms": 11_000,
            },
            "coach_intervention": None,
            "semantic_follow_up": {"question": "Who owns rollback?", "reason": "Owner missing."},
        },
    }
    snapshot = {
        "jobs": [
            {
                "id": "job-applied",
                "kind": "intelligence",
                "status": "succeeded",
                "error_class": None,
                "created_at_ms": 1_000,
                "updated_at_ms": 2_100,
                "completed_at_ms": 2_100,
            }
        ]
    }
    original = replay.extract_decisions([event])[0]

    jobs = replay.build_intelligence_job_audit(snapshot, [event])
    audited = replay.build_decision_audit(snapshot, [event], job_audit=jobs)[0]

    assert {key: audited[key] for key in original} == original
    assert audited["record_type"] == "applied_decision"
    assert audited["job_status"] == "succeeded"
    assert audited["created_at_ms"] == 2_000
    assert audited["completed_at_ms"] == 2_050
    assert audited["dropped_at_ms"] is None
    assert audited["drop_reason"] is None
    assert audited["error_class"] is None
    assert audited["deadline_at_ms"] == 11_000
    assert jobs[0]["projection_status"] == "applied"
    assert jobs[0]["dropped_at_ms"] is None
    assert jobs[0]["deadline_source"] == "coach_decision"


def test_latest_applied_decision_ignores_newer_terminal_only_audit_rows():
    decisions = [
        {
            "record_type": "applied_decision",
            "job_id": "job-applied",
            "seq": 21,
            "occurred_at_ms": 8_100,
            "completed_at_ms": 8_000,
            "outcome": "intervention",
            "coach_decision": {"status": "intervention", "origin": "pi"},
            "coach_intervention": {"recommendation": "Confirm the owner."},
        },
        {
            "record_type": "terminal_without_applied_decision",
            "job_id": "job-old-terminal",
            "seq": None,
            "completed_at_ms": 7_000,
            "outcome": "stale",
            "coach_decision": None,
            "coach_intervention": None,
        },
    ]

    assert replay.latest_applied_decision(decisions) == decisions[0]

    notes = replay.build_notes(
        meeting_id="pi_stage0_notes",
        acceptance={"passed": True, "checks": {}},
        wav_info=None,
        ws_stats={"sent_audio_seconds": 1, "pace": 1, "non_empty_final_count": 1},
        decisions=decisions,
        snapshot={
            "jobs": [
                {
                    "id": "job-old-terminal",
                    "kind": "intelligence",
                    "status": "cancelled",
                    "created_at_ms": 1_000,
                    "updated_at_ms": 7_000,
                },
                {
                    "id": "job-applied",
                    "kind": "intelligence",
                    "status": "succeeded",
                    "created_at_ms": 8_000,
                    "updated_at_ms": 8_100,
                },
            ]
        },
        post_end_settled=True,
        failure=None,
    )

    assert "Latest intelligence job: `job-applied`" in notes
    assert "Latest intelligence job status: `succeeded`" in notes
    assert "Current-turn coach outcome: `intervention`" in notes
    assert "Current-turn coach status/origin: `intervention` / `pi`" in notes
    assert "Recommendation: Confirm the owner." in notes
    assert "Latest intelligence projection: `applied`" in notes


@pytest.mark.parametrize(
    ("job_status", "error_class", "expected_projection", "expected_error_text"),
    [
        ("cancelled", "deadline_exceeded", "timed_out", "deadline_exceeded"),
        ("failed", "NonRetryableProviderError", "failed", "NonRetryableProviderError"),
        ("cancelled", "evidence_superseded", "stale", "evidence_superseded"),
        ("cancelled", None, "cancelled", "unavailable"),
    ],
)
def test_notes_bind_current_turn_to_latest_terminal_job_and_mark_old_applied_as_history(
    job_status,
    error_class,
    expected_projection,
    expected_error_text,
):
    snapshot = {
        "jobs": [
            {
                "id": "job-historical-applied",
                "kind": "intelligence",
                "status": "succeeded",
                "created_at_ms": 1_000,
                "completed_at_ms": 2_000,
            },
            {
                "id": "job-latest-terminal",
                "kind": "intelligence",
                "status": job_status,
                "error_class": error_class,
                "created_at_ms": 3_000,
                "updated_at_ms": 4_000,
                "completed_at_ms": 4_000,
            },
        ]
    }
    historical_event = {
        "seq": 4,
        "occurred_at_ms": 2_000,
        "type": "meeting.intelligence.applied",
        "payload": {
            "job_id": "job-historical-applied",
            "coach_decision": {"status": "intervention", "origin": "pi"},
            "coach_intervention": {
                "recommendation": "Historical recommendation must not be current.",
                "reason": "Historical reason.",
            },
        },
    }
    decisions = replay.build_decision_audit(snapshot, [historical_event])

    latest_result = replay.latest_intelligence_result(snapshot, decisions)
    notes = replay.build_notes(
        meeting_id="pi_stage0_latest_terminal",
        acceptance={"passed": False, "checks": {}},
        wav_info=None,
        ws_stats={"sent_audio_seconds": 1, "pace": 1, "non_empty_final_count": 1},
        decisions=decisions,
        snapshot=snapshot,
        post_end_settled=True,
        failure=None,
    )

    assert latest_result["job_id"] == "job-latest-terminal"
    assert latest_result["job_status"] == job_status
    assert latest_result["projection_status"] == expected_projection
    assert latest_result["error_class"] == error_class
    assert latest_result["applied"] is False
    assert latest_result["decision"] is None
    assert "Latest intelligence job: `job-latest-terminal`" in notes
    assert f"Latest intelligence job status: `{job_status}`" in notes
    assert f"Latest intelligence projection: `{expected_projection}`" in notes
    assert f"Latest intelligence error_class: `{expected_error_text}`" in notes
    assert "Current-turn coach outcome: `unavailable`" in notes
    assert "No coach decision was applied for this latest intelligence job." in notes
    assert "## Coach intervention" not in notes
    assert "Recommendation: Historical recommendation must not be current." not in notes
    assert "## Historical coach state" in notes
    assert "Historical applied job: `job-historical-applied`" in notes
    assert "not the current-turn Pi result" in notes


def test_decision_audit_is_chronological_when_terminal_rows_are_mixed_with_applied():
    snapshot = {
        "jobs": [
            {
                "id": "job-stale",
                "kind": "intelligence",
                "status": "cancelled",
                "created_at_ms": 1_000,
                "completed_at_ms": 2_000,
                "error_class": "evidence_superseded",
            },
            {
                "id": "job-applied",
                "kind": "intelligence",
                "status": "succeeded",
                "created_at_ms": 3_000,
                "completed_at_ms": 4_000,
            },
        ]
    }
    events = [
        {
            "seq": 9,
            "occurred_at_ms": 4_100,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-applied",
                "coach_decision": {"status": "intervention", "origin": "pi"},
                "coach_intervention": {"recommendation": "Confirm the owner."},
            },
        }
    ]

    decisions = replay.build_decision_audit(snapshot, events)

    assert [item["job_id"] for item in decisions] == ["job-stale", "job-applied"]
    assert replay.latest_applied_decision(decisions) == decisions[1]


def test_acceptance_treats_protected_silence_as_completed_decision():
    snapshot = {
        "jobs": [
            {
                "id": "job-1",
                "kind": "intelligence",
                "status": "succeeded",
                "created_at_ms": 10,
                "updated_at_ms": 20,
            }
        ],
        "runtime": {"phase": "ended"},
    }
    events = [
        {
            "seq": 4,
            "type": "recording.export.ready",
            "payload": {
                "output": {"source_type": "simulated_realtime_wav"},
                "recording": {"source_type": "simulated_realtime_wav"},
            },
        },
        {
            "seq": 5,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "job-1",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {"status": "protected_silent", "origin": "pi"},
                "coach_intervention": None,
            },
        }
    ]
    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={
            "create_meeting": 201,
            "save_preparation": 200,
            "end_meeting": 200,
        },
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 1,
            "event_counts": {"final": 1},
        },
        snapshot=snapshot,
        transcript={"segments": [{"source_track": "microphone"}]},
        events=events,
        evidence_complete=True,
        post_end_settled=True,
    )

    assert result["passed"] is True
    assert result["latest_coach_outcome"] == "explicit_silence"
    assert result["checks"]["microphone_provenance_present"] is False
    assert result["required_checks"]["audio_provenance_present"] is True
    assert result["skipped_checks"] == {
        "microphone_provenance_present": "not_applicable_for_audio_source:simulated_realtime_wav"
    }
    assert result["failed_checks"] == []
    assert result["fixture_quality"] == {
        "applicable": False,
        "checks": {},
        "contract_id": None,
        "fixture_sha256": None,
        "reason": "no_quality_contract_for_fixture",
    }


def test_known_release_incident_fixture_requires_semantic_coverage_and_pi_activity():
    snapshot = {
        "jobs": [
            {
                "id": f"correction-{index}",
                "kind": "correction",
                "status": "failed",
                "error_class": "job_failed",
                "created_at_ms": index,
                "updated_at_ms": index + 1,
            }
            for index in range(1, 4)
        ]
        + [
            {
                "id": "intelligence-latest",
                "kind": "intelligence",
                "status": "succeeded",
                "created_at_ms": 10,
                "updated_at_ms": 20,
            }
        ],
        "runtime": {"phase": "ended"},
    }
    events = [
        {
            "seq": 4,
            "type": "recording.export.ready",
            "payload": {
                "output": {"source_type": "simulated_realtime_wav"},
                "recording": {"source_type": "simulated_realtime_wav"},
            },
        },
        {
            "seq": 5,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "intelligence-latest",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {
                    "status": "not_triggered",
                    "origin": "direct_intelligence",
                    "runtime_requested": "pi",
                    "runtime_used": None,
                    "triggered": False,
                },
                "coach_intervention": None,
            },
        },
    ]
    transcript = {
        "segments": [
            {
                "segment_id": f"segment-{index}",
                "source_track": "microphone",
                "text": text,
                "correction_status": "failed_preserved_original",
                "correction_error_class": "job_failed",
            }
            for index, text in enumerate(
                ("先看error", "ging跑过", "lag最高到"),
                start=1,
            )
        ]
    }

    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={
            "create_meeting": 201,
            "save_preparation": 200,
            "end_meeting": 200,
        },
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 3,
            "event_counts": {"final": 3},
        },
        snapshot=snapshot,
        transcript=transcript,
        events=events,
        evidence_complete=True,
        post_end_settled=True,
        fixture_sha256=replay.RELEASE_INCIDENT_FIXTURE_SHA256,
    )

    quality = result["fixture_quality"]
    assert result["passed"] is False
    assert quality["applicable"] is True
    assert quality["passed"] is False
    assert quality["transcript"]["matched_anchor_ids"] == ["monitoring_metric"]
    assert quality["transcript"]["anchor_coverage"] == 0.125
    assert quality["transcript"]["missing_required_anchor_ids"] == [
        "monitor_threshold_owner"
    ]
    assert quality["corrections"]["failed_job_count"] == 3
    assert quality["corrections"]["failed_segment_count"] == 3
    assert quality["pi"]["runtime_activity_count"] == 0
    assert quality["pi"]["intervention_count"] == 0
    assert set(result["failed_checks"]) == {
        "known_fixture_transcript_anchor_coverage",
        "known_fixture_required_anchors",
        "known_fixture_corrections_healthy",
        "known_fixture_pi_runtime_activity",
        "known_fixture_pi_intervention",
    }
    notes = replay.build_notes(
        meeting_id="known-fixture-bad-asr",
        acceptance=result,
        wav_info=None,
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "audio_provenance": "controlled_wav_fixture",
            "sent_audio_seconds": 55.853,
            "pace": 1,
            "non_empty_final_count": 3,
        },
        decisions=replay.build_decision_audit(snapshot, events),
        snapshot=snapshot,
        post_end_settled=True,
        failure=None,
    )
    assert "Fixture quality contract: `release_incident_55s.v1`" in notes
    assert "Transcript anchor coverage: 1/8" in notes
    assert "Failed correction jobs/segments: 3/3" in notes
    assert "Pi runtime activity/interventions: 0/0" in notes


def test_known_release_incident_fixture_accepts_anchor_variants_with_pi_intervention():
    snapshot = {
        "jobs": [
            {
                "id": "correction-1",
                "kind": "correction",
                "status": "succeeded",
                "created_at_ms": 1,
                "updated_at_ms": 2,
            },
            {
                "id": "correction-superseded",
                "kind": "correction",
                "status": "cancelled",
                "error_class": "evidence_superseded",
                "created_at_ms": 3,
                "updated_at_ms": 4,
            },
            {
                "id": "intelligence-latest",
                "kind": "intelligence",
                "status": "succeeded",
                "created_at_ms": 10,
                "updated_at_ms": 20,
            },
        ],
        "runtime": {"phase": "ended"},
    }
    events = [
        {
            "seq": 4,
            "type": "recording.export.ready",
            "payload": {
                "output": {"source_type": "simulated_realtime_wav"},
                "recording": {"source_type": "simulated_realtime_wav"},
            },
        },
        {
            "seq": 5,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "intelligence-latest",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {
                    "status": "intervention",
                    "origin": "pi",
                    "runtime_requested": "pi",
                    "runtime_used": "pi",
                    "triggered": True,
                },
                "coach_intervention": {
                    "recommendation": "先确认监控阈值的负责人和完成时间。",
                    "reason": "发布前仍缺明确 owner。",
                },
            },
        },
    ]
    transcript = {
        "segments": [
            {
                "segment_id": "segment-1",
                "source_track": "microphone",
                "text": "raw-1",
                "correction_status": "changed",
                "correction_after_text": (
                    "这次 checkout service 周五灰度百分之十，观察 error rate 和 P99，"
                    "回滚脚本尚未预演。"
                ),
            },
            {
                "segment_id": "segment-2",
                "source_track": "microphone",
                "text": "CI 验收还差支付失败重试。",
                "correction_status": "no_change",
            },
            {
                "segment_id": "segment-3",
                "source_track": "microphone",
                "text": "order worker 消费堆积，lag 上升，告警延迟六分钟。",
                "correction_status": "no_change",
            },
            {
                "segment_id": "segment-4",
                "source_track": "microphone",
                "text": "复盘下周一发，监控阈值谁来改还没定。",
                "correction_status": "no_change",
            },
        ]
    }

    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={
            "create_meeting": 201,
            "save_preparation": 200,
            "end_meeting": 200,
        },
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 4,
            "event_counts": {"final": 4},
        },
        snapshot=snapshot,
        transcript=transcript,
        events=events,
        evidence_complete=True,
        post_end_settled=True,
        fixture_sha256=replay.RELEASE_INCIDENT_FIXTURE_SHA256,
    )

    quality = result["fixture_quality"]
    assert result["passed"] is True
    assert quality["passed"] is True
    assert quality["transcript"]["matched_anchor_count"] == 8
    assert quality["transcript"]["anchor_coverage"] == 1.0
    assert quality["corrections"]["failed_job_count"] == 0
    assert quality["pi"]["runtime_activity_count"] == 1
    assert quality["pi"]["intervention_count"] == 1
    assert quality["pi"]["reliability_error_job_count"] == 0
    assert quality["pi"]["reliability_error_event_count"] == 0
    assert result["failed_checks"] == []


def test_known_release_incident_fixture_rejects_earlier_pi_timeout():
    snapshot = {
        "jobs": [
            {
                "id": "correction-1",
                "kind": "correction",
                "status": "succeeded",
            },
            {
                "id": "intelligence-timeout",
                "kind": "intelligence",
                "status": "cancelled",
                "error_class": "deadline_exceeded",
                "created_at_ms": 1,
                "completed_at_ms": 10_001,
            },
            {
                "id": "intelligence-latest",
                "kind": "intelligence",
                "status": "succeeded",
                "created_at_ms": 20_000,
                "completed_at_ms": 22_000,
            },
        ],
        "runtime": {"phase": "ended"},
    }
    events = [
        {
            "seq": 4,
            "type": "recording.export.ready",
            "payload": {
                "output": {"source_type": "simulated_realtime_wav"},
                "recording": {"source_type": "simulated_realtime_wav"},
            },
        },
        {
            "seq": 5,
            "type": "meeting.intelligence.applied",
            "payload": {
                "job_id": "intelligence-latest",
                "source": "llm_first",
                "llm_called": True,
                "coach_decision": {
                    "status": "intervention",
                    "origin": "pi",
                    "runtime_requested": "pi",
                    "runtime_used": "pi",
                    "triggered": True,
                },
                "coach_intervention": {
                    "recommendation": "先确认监控阈值负责人。",
                    "reason": "负责人尚未确认。",
                },
            },
        },
    ]
    transcript = {
        "segments": [
            {
                "segment_id": "segment-1",
                "source_track": "microphone",
                "text": (
                    "周五灰度百分之十，观察 error rate 和 P99，回滚脚本尚未预演；"
                    "支付失败重试还没验收；order worker lag 八万，告警延迟六分钟；"
                    "复盘下周一发，监控阈值谁来改还没定。"
                ),
                "correction_status": "no_change",
            }
        ]
    }

    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={
            "create_meeting": 201,
            "save_preparation": 200,
            "end_meeting": 200,
        },
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 1,
            "event_counts": {"final": 1},
        },
        snapshot=snapshot,
        transcript=transcript,
        events=events,
        evidence_complete=True,
        post_end_settled=True,
        fixture_sha256=replay.RELEASE_INCIDENT_FIXTURE_SHA256,
    )

    assert result["passed"] is False
    assert result["failed_checks"] == ["known_fixture_pi_reliability_healthy"]
    assert result["fixture_quality"]["pi"]["intervention_count"] == 1
    assert result["fixture_quality"]["pi"]["reliability_error_job_ids"] == [
        "intelligence-timeout"
    ]


def test_acceptance_rejects_ended_snapshot_that_keeps_current_coach_card():
    snapshot = {
        "jobs": [{"id": "job-1", "kind": "intelligence", "status": "succeeded"}],
        "runtime": {"phase": "ended"},
        "follow_up": {"question": "stale card"},
        "semantic_follow_up": {"question": "stale question"},
        "coach_decision": {"status": "intervention"},
    }
    result = replay.evaluate_acceptance(
        procedural_failure=None,
        runtime_validation={"validated": True},
        response_statuses={"create_meeting": 201, "save_preparation": 200, "end_meeting": 200},
        ws_stats={
            "audio_source": "simulated_realtime_wav",
            "ready": True,
            "non_empty_final_count": 1,
            "event_counts": {"final": 1},
        },
        snapshot=snapshot,
        transcript={"segments": [{"source_track": "microphone"}]},
        events=[],
        evidence_complete=True,
        post_end_settled=False,
    )

    assert result["passed"] is False
    assert result["checks"]["ended_coach_projection_cleared"] is False


def test_sanitize_removes_known_and_credential_shaped_secrets():
    secret = "local-token-value-123"
    payload = {
        "token_usage": {"total_tokens": 12},
        "api_key": "sk-super-secret",
        "nested": [f"Bearer {secret}", f"prefix {secret} suffix"],
    }

    sanitized = replay.sanitize(payload, secret_values=(secret,))
    encoded = json.dumps(sanitized)

    assert sanitized["token_usage"] == {"total_tokens": 12}
    assert sanitized["api_key"] == "[REDACTED]"
    assert secret not in encoded
    assert "sk-super-secret" not in encoded


def test_cli_exposes_required_production_replay_arguments(tmp_path: Path):
    wav_path = tmp_path / "fixture.wav"
    output_dir = tmp_path / "evidence"
    args = replay.parse_args(
        [
            "--base-url",
            "http://127.0.0.1:8878",
            "--wav",
            str(wav_path),
            "--output-dir",
            str(output_dir),
            "--meeting-id",
            "pi_stage0_test",
            "--pace",
            "1.25",
        ]
    )

    assert isinstance(args, argparse.Namespace)
    assert args.base_url == "http://127.0.0.1:8878"
    assert args.wav == wav_path
    assert args.output_dir == output_dir
    assert args.meeting_id == "pi_stage0_test"
    assert args.pace == 1.25


def test_http_client_rejects_non_loopback_service_before_sending_credentials():
    with pytest.raises(replay.ReplayFailure, match="loopback"):
        replay.JsonHttpClient("https://example.com", token="must-not-be-sent")


def test_end_meeting_retries_only_transient_asr_finalization_conflict():
    class Client:
        def __init__(self):
            self.calls = 0

        def request(self, method, path, *, payload, timeout_seconds):
            del method, path, payload, timeout_seconds
            self.calls += 1
            if self.calls == 1:
                return replay.HttpResult(
                    409,
                    {"detail": {"error": "asr_finalization_pending"}},
                    1.0,
                )
            return replay.HttpResult(200, {"meeting": {"state": "ended"}}, 1.0)

    client = Client()
    result = replay.end_meeting_with_retry(
        client,
        meeting_id="pi_stage0_test",
        timeout_seconds=1.0,
        poll_interval_seconds=0.001,
    )

    assert result.status == 200
    assert client.calls == 2


def test_stream_wav_uses_production_microphone_websocket_and_f32le(tmp_path: Path, monkeypatch):
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path, [-32768, 0, 16384])
    wav_info = replay.inspect_wav(wav_path)

    class FakeWebSocket:
        def __init__(self):
            self.sent_binary = []
            self.sent_text = []
            self.ready_sent = False
            self.final_sent = False
            self.closed = False

        def settimeout(self, _timeout):
            return None

        def recv(self):
            if not self.ready_sent:
                assert self.sent_binary, "client must send PCM before waiting for cold-start readiness"
                self.ready_sent = True
                return json.dumps({"event_type": "asr_ready", "ready": True})
            if self.sent_binary and not self.final_sent:
                self.final_sent = True
                return json.dumps({"event_type": "final", "text": "release ready"})
            if self.sent_text:
                return ""
            raise replay.websocket.WebSocketTimeoutException()

        def send_binary(self, payload):
            self.sent_binary.append(payload)

        def send(self, payload):
            self.sent_text.append(payload)

        def close(self):
            self.closed = True

    class FakeClient:
        origin = "http://127.0.0.1:8878"
        cookie = "meeting_copilot_session=redacted"

        def websocket_url(self, path, query):
            assert path == "/live/asr/stream/ws/pi_stage0_transport"
            assert query["audio_source"] == "browser_live_mic"
            assert query["expected_duration_seconds"] == 90
            return "ws://127.0.0.1:8878/live/asr/stream/ws/pi_stage0_transport?audio_source=browser_live_mic"

    fake_ws = FakeWebSocket()

    def create_connection(url, **kwargs):
        assert "audio_source=browser_live_mic" in url
        assert kwargs["origin"] == FakeClient.origin
        assert kwargs["cookie"] == FakeClient.cookie
        return fake_ws

    monkeypatch.setattr(replay.websocket, "create_connection", create_connection)
    events = []
    result = replay.stream_wav(
        FakeClient(),
        meeting_id="pi_stage0_transport",
        wav_path=wav_path,
        wav_info=wav_info,
        pace=1_000.0,
        chunk_seconds=0.3,
        tail_silence_seconds=0,
        ready_timeout_seconds=1,
        finalize_timeout_seconds=1,
        event_sink=events,
    )

    assert fake_ws.sent_text == ["END"]
    assert fake_ws.closed is True
    assert len(fake_ws.sent_binary) == 1
    assert struct.unpack("<fff", fake_ws.sent_binary[0]) == (-1.0, 0.0, 0.5)
    assert result["non_empty_final_count"] == 1
    assert result["sent_audio_frames_before_ready"] == 3
    assert result["sent_audio_chunk_count"] == 1
    assert result["end_send_attempted"] is True
    assert result["end_send_succeeded"] is True
    assert result["sent_text_frame_count"] == 1
    assert result["partial_count"] == 0
    assert result["final_count"] == 1
    assert result["termination"] == "server_close"
    assert [event["event_type"] for event in events] == ["asr_ready", "final"]


def test_stream_wav_does_not_treat_an_early_final_then_quiet_as_terminal(
    tmp_path: Path,
    monkeypatch,
):
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path, [1000, -1000, 1000])
    wav_info = replay.inspect_wav(wav_path)

    class QuietAfterFinalWebSocket:
        def __init__(self):
            self.sent_binary = []
            self.sent_text = []
            self.ready_sent = False
            self.final_sent = False

        def settimeout(self, _timeout):
            return None

        def recv(self):
            if not self.ready_sent:
                self.ready_sent = True
                return json.dumps({"event_type": "asr_ready", "ready": True})
            if self.sent_binary and not self.final_sent:
                self.final_sent = True
                return json.dumps({"event_type": "final", "text": "early final"})
            raise replay.websocket.WebSocketTimeoutException()

        def send_binary(self, payload):
            self.sent_binary.append(payload)

        def send(self, payload):
            self.sent_text.append(payload)

        def close(self):
            return None

    class FakeClient:
        origin = "http://127.0.0.1:8878"
        cookie = "meeting_copilot_session=redacted"

        def websocket_url(self, _path, _query):
            return "ws://127.0.0.1:8878/live/asr/stream/ws/quiet"

    fake_ws = QuietAfterFinalWebSocket()
    monkeypatch.setattr(
        replay.websocket,
        "create_connection",
        lambda *_args, **_kwargs: fake_ws,
    )

    with pytest.raises(replay.ReplayFailure, match="terminal condition") as exc_info:
        replay.stream_wav(
            FakeClient(),
            meeting_id="pi_stage0_quiet_final",
            wav_path=wav_path,
            wav_info=wav_info,
            pace=1_000.0,
            chunk_seconds=0.3,
            tail_silence_seconds=0,
            ready_timeout_seconds=1,
            finalize_timeout_seconds=0.02,
            event_sink=[],
        )

    assert fake_ws.sent_text == ["END"]
    assert exc_info.value.transport_stats["termination"] == "finalize_timeout"
    assert exc_info.value.transport_stats["non_empty_final_count"] == 1


def test_stream_wav_preserves_transport_stats_when_server_closes_before_final(
    tmp_path: Path,
    monkeypatch,
):
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path, [1000, -1000, 1000])
    wav_info = replay.inspect_wav(wav_path)

    class ClosingWebSocket:
        def __init__(self):
            self.sent_binary = []
            self.sent_text = []
            self.closed = False
            self.ready_sent = False

        def settimeout(self, _timeout):
            return None

        def recv(self):
            if not self.ready_sent:
                self.ready_sent = True
                return json.dumps({"event_type": "asr_ready", "ready": True})
            if not self.sent_text:
                raise replay.websocket.WebSocketTimeoutException()
            return ""

        def send_binary(self, payload):
            self.sent_binary.append(payload)

        def send(self, payload):
            self.sent_text.append(payload)

        def close(self):
            self.closed = True

    class FakeClient:
        origin = "http://127.0.0.1:8878"
        cookie = "meeting_copilot_session=redacted"

        def websocket_url(self, path, query):
            del path, query
            return "ws://127.0.0.1:8878/live/asr/stream/ws/transport_failure"

    fake_ws = ClosingWebSocket()
    monkeypatch.setattr(replay.websocket, "create_connection", lambda *_args, **_kwargs: fake_ws)

    with pytest.raises(replay.ReplayFailure) as caught:
        replay.stream_wav(
            FakeClient(),
            meeting_id="transport_failure",
            wav_path=wav_path,
            wav_info=wav_info,
            pace=1_000.0,
            chunk_seconds=0.3,
            tail_silence_seconds=0,
            ready_timeout_seconds=1,
            finalize_timeout_seconds=0.05,
            event_sink=[],
        )

    stats = caught.value.transport_stats
    assert stats["sent_audio_frames"] == 3
    assert stats["sent_audio_chunk_count"] == 1
    assert stats["end_send_attempted"] is True
    assert stats["end_send_succeeded"] is True
    assert stats["partial_count"] == 0
    assert stats["final_count"] == 0
    assert stats["non_empty_final_count"] == 0
    assert stats["socket_closed_by_server"] is True
    assert stats["termination"] == "server_close"


def test_stream_wav_decodes_recv_data_text_opcode_bytes(tmp_path: Path, monkeypatch):
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path, [1000, -1000, 1000])
    wav_info = replay.inspect_wav(wav_path)

    class OpcodeWebSocket:
        def __init__(self):
            self.sent_binary = []
            self.sent_text = []
            self.ready_sent = False
            self.final_sent = False
            self.closed = False

        def settimeout(self, _timeout):
            return None

        def recv_data(self):
            if not self.ready_sent:
                self.ready_sent = True
                return 1, b'{"event_type":"asr_ready","ready":true}'
            if self.sent_text and not self.final_sent:
                self.final_sent = True
                return 1, b'{"event_type":"final","text":"release ready"}'
            if self.final_sent:
                return 8, (1000).to_bytes(2, "big") + b"normal"
            raise replay.websocket.WebSocketTimeoutException()

        def send_binary(self, payload):
            self.sent_binary.append(payload)

        def send(self, payload):
            self.sent_text.append(payload)

        def close(self):
            self.closed = True

    class FakeClient:
        origin = "http://127.0.0.1:8878"
        cookie = "meeting_copilot_session=redacted"

        def websocket_url(self, path, query):
            del path, query
            return "ws://127.0.0.1:8878/live/asr/stream/ws/opcode_text"

    fake_ws = OpcodeWebSocket()
    monkeypatch.setattr(replay.websocket, "create_connection", lambda *_args, **_kwargs: fake_ws)
    events = []

    result = replay.stream_wav(
        FakeClient(),
        meeting_id="opcode_text",
        wav_path=wav_path,
        wav_info=wav_info,
        pace=1_000.0,
        chunk_seconds=0.3,
        tail_silence_seconds=0,
        ready_timeout_seconds=1,
        finalize_timeout_seconds=1,
        event_sink=events,
    )

    assert result["ready"] is True
    assert result["non_empty_final_count"] == 1
    assert result["event_counts"] == {"asr_ready": 1, "final": 1}
    assert result["socket_close_code"] == 1000
    assert result["socket_close_reason"] == "normal"
    assert [event["event_type"] for event in events] == ["asr_ready", "final"]


def test_run_replay_writes_complete_redacted_bundle(tmp_path: Path, monkeypatch):
    wav_path = tmp_path / "fixture.wav"
    output_dir = tmp_path / "evidence"
    _write_wav(wav_path, [0, 2000, -2000] * 100)
    secret = "local-api-token-that-must-not-leak"
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", secret)

    terminal_job = {
        "id": "job-1",
        "kind": "intelligence",
        "status": "succeeded",
        "evidence_segment_id": "segment-1",
        "created_at_ms": 10,
        "updated_at_ms": 20,
        "completed_at_ms": 20,
    }
    applied_event = {
        "seq": 5,
        "occurred_at_ms": 1_000,
        "type": "meeting.intelligence.applied",
        "payload": {
            "job_id": "job-1",
            "batch_id": "batch-1",
            "source": "llm_first",
            "llm_called": True,
            "provider": "test-provider",
            "model": "test-model",
            "evidence": {"segment_ids": ["segment-1"]},
            "coach_decision": {
                "status": "protected_silent",
                "origin": "pi",
                "decision_reason": "No high-value interruption.",
                "created_at_ms": 500,
                "completed_at_ms": 900,
                "projected_at_ms": 1_000,
                "deadline_at_ms": 10_000,
            },
            "coach_intervention": None,
        },
    }
    snapshot = {
        "meeting_id": "pi_stage0_bundle",
        "jobs": [terminal_job],
        "runtime": {
            "phase": "ended",
            "ai": {
                "capabilities": {
                    "proactive_suggestions": {
                        "state": "active",
                        "label": "Pi coach listening",
                        "detail": "Completed this turn.",
                    }
                }
            },
        },
        "audio": {"status": "saved", "duration_ms": 20},
        "review_jobs": {},
    }
    transcript = {
        "meeting_id": "pi_stage0_bundle",
        "segments": [
            {
                "segment_id": "segment-1",
                "transcript_seq": 1,
                "text": "Confirm the rollback owner.",
                "source_track": "microphone",
            }
        ],
        "page_count": 1,
    }
    recording_event = {
        "seq": 4,
        "type": "recording.export.ready",
        "payload": {
            "output": {"source_type": "simulated_realtime_wav"},
            "recording": {"source_type": "simulated_realtime_wav"},
        },
    }
    final_event = {
        "seq": 3,
        "occurred_at_ms": 10,
        "type": "transcript.segment.finalized",
        "payload": {"segment_id": "segment-1"},
    }
    collected = {
        "acceptance_evidence": {
            "schema_version": "meeting_copilot.acceptance_evidence.v1",
            "meeting_id": "pi_stage0_bundle",
            "lineage": {
                "meeting_revision": 1,
                "event_high_water_mark": 4,
                "transcript_high_water_mark": 1,
                "transcript_revision": 1,
                "transcript_sha256": "fixture",
                "usage_high_water_id": 0,
            },
            "snapshot": snapshot,
            "transcript": transcript,
            "events": [final_event, recording_event, applied_event],
            "live_session": {
                "session_id": "pi_stage0_bundle",
                "events": [{"event_type": "final", "text": "Confirm rollback."}],
            },
            "usage_ledger": [],
            "consistency": {"acceptance_eligible": True, "errors": []},
            "capture_sha256": "fixture",
        },
        "snapshot": snapshot,
        "events": [final_event, recording_event, applied_event],
        "transcript": transcript,
        "semantic_paragraphs": {"paragraphs": [{"paragraph_id": "paragraph-1"}]},
        "traces": {"traces": [{"trace_id": "trace-1"}]},
        "slo": {"meeting_id": "pi_stage0_bundle", "status": "measured"},
        "asr_live_events": {
            "session_id": "pi_stage0_bundle",
            "events": [{"event_type": "final", "text": "Confirm rollback."}],
        },
    }

    class FakeClient:
        def __init__(self, base_url, *, token=None, timeout_seconds=30.0):
            assert token == secret
            self.base_url = base_url
            self.request_log = []

        def request(self, method, path, *, payload=None, timeout_seconds=None):
            del timeout_seconds
            self.request_log.append({"method": method, "path": path, "status": 200})
            if path == "/health":
                return replay.HttpResult(200, {"status": "ok"}, 1.0)
            if path == "/providers/health":
                return replay.HttpResult(
                    200,
                    {
                        "llm": {
                            "configured": True,
                            "is_mock": False,
                            "model": "gpt-5.5",
                            "realtime_model": "gpt-5.4-mini",
                            "realtime_model_source": "runtime_realtime_model",
                            "realtime_model_explicit": True,
                            "realtime_model_warning": None,
                        },
                        "asr": {"realtime_asr_available": True},
                    },
                    1.0,
                )
            if path == "/providers/asr/runtime":
                return replay.HttpResult(200, _ready_asr_runtime(), 1.0)
            if path == "/settings":
                return replay.HttpResult(
                    200,
                    {
                        "asr": {
                            "l2_correction_enabled": True,
                            "l3_normalize_enabled": True,
                        }
                    },
                    1.0,
                )
            if path == "/v2/meetings":
                assert payload["meeting_id"] == "pi_stage0_bundle"
                return replay.HttpResult(201, {"meeting": {"id": "pi_stage0_bundle"}}, 1.0)
            if path.endswith("/preparation"):
                assert payload["notice_acknowledged"] is True
                return replay.HttpResult(200, dict(payload), 1.0)
            if path.endswith("/end"):
                assert payload == {"action": "end_and_review"}
                return replay.HttpResult(200, {"meeting": {"state": "ended"}}, 1.0)
            raise AssertionError(f"unexpected request: {method} {path}")

    def fake_stream(_client, **kwargs):
        kwargs["event_sink"].extend(
            [
                {"event_type": "asr_ready", "ready": True},
                {"event_type": "final", "text": "Confirm rollback."},
            ]
        )
        return {
            "audio_source": "simulated_realtime_wav",
            "audio_provenance": "controlled_wav_fixture",
            "ready": True,
            "pace": kwargs["pace"],
            "sent_audio_seconds": 0.01875,
            "non_empty_final_count": 1,
            "last_final_received_at_ms": 900,
            "event_counts": {"asr_ready": 1, "final": 1},
        }

    monkeypatch.setattr(replay, "JsonHttpClient", FakeClient)
    monkeypatch.setattr(replay, "stream_wav", fake_stream)
    monkeypatch.setattr(
        replay,
        "wait_for_intelligence",
        lambda *_args, **_kwargs: (snapshot, [applied_event], terminal_job, applied_event),
    )
    monkeypatch.setattr(
        replay,
        "wait_after_end",
        lambda *_args, **_kwargs: (snapshot, True),
    )
    monkeypatch.setattr(replay, "collect_evidence", lambda *_args, **_kwargs: collected)

    manifest = replay.run_replay(
        replay.parse_args(
            [
                "--base-url",
                "http://127.0.0.1:8878",
                "--wav",
                str(wav_path),
                "--output-dir",
                str(output_dir),
                "--meeting-id",
                "pi_stage0_bundle",
            ]
        )
    )

    assert manifest["status"] == "passed"
    for filename in (*replay.REQUIRED_ARTIFACTS, "manifest.json"):
        assert (output_dir / filename).is_file()
    assert json.loads((output_dir / "manifest.json").read_text())["status"] == "passed"
    assert "explicit_silence" in (output_dir / "decisions.jsonl").read_text()
    job_record = json.loads((output_dir / "jobs.jsonl").read_text())
    assert job_record["job_id"] == "job-1"
    assert job_record["projection_status"] == "applied"
    assert job_record["created_at_ms"] == 10
    assert job_record["completed_at_ms"] == 20
    assert job_record["dropped_at_ms"] is None
    assert job_record["drop_reason"] is None
    assert job_record["error_class"] is None
    assert job_record["deadline_at_ms"] == 10_000
    metrics = json.loads((output_dir / "metrics.json").read_text())
    assert metrics["intelligence"]["latest_result"] == {
        "applied": True,
        "drop_reason": None,
        "error_class": None,
        "job_id": "job-1",
        "job_status": "succeeded",
        "projection_status": "applied",
        "schema_version": "meeting_copilot.pi_stage0_latest_intelligence_result.v1",
    }
    assert metrics["decisions"]["latest"]["job_id"] == "job-1"
    assert metrics["decisions"]["latest_historical_applied"] is None
    assert secret not in "".join(path.read_text(encoding="utf-8") for path in output_dir.iterdir() if path.is_file())
