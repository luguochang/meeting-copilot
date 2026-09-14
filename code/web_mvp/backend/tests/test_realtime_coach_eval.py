from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from meeting_copilot_web_mvp.llm_service import LlmConfig
from tools.realtime_coach_eval import replay as replay_module
from tools.realtime_coach_eval.replay import acceptance_from_score
from tools.realtime_coach_eval.score import score_predictions


DATASET_PATH = REPO_ROOT / "tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl"
FORMAL_DATASET_PATH = (
    REPO_ROOT / "tools/realtime_coach_eval/fixtures/stage0_formal_balanced_v1.jsonl"
)
ORDERED_DATASET_PATH = (
    REPO_ROOT / "tools/realtime_coach_eval/fixtures/stage0_ordered_lifecycle_v1.jsonl"
)
SCHEMA_PATH = REPO_ROOT / "tools/realtime_coach_eval/dataset.schema.json"


def _load_fixture_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in DATASET_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_formal_fixture_cases() -> list[dict[str, Any]]:
    return replay_module.load_dataset(FORMAL_DATASET_PATH)


def _load_ordered_fixture_cases() -> list[dict[str, Any]]:
    return replay_module.load_dataset(ORDERED_DATASET_PATH)


def test_replay_uses_the_configured_realtime_model(monkeypatch) -> None:
    config = LlmConfig(
        base_url="https://provider.example.test",
        api_key="test-only-key",
        model="general-model",
        realtime_model="realtime-model",
    )
    monkeypatch.setattr(
        replay_module.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )

    resolved = replay_module.load_replay_config()

    assert resolved.model == "realtime-model"
    assert resolved.realtime_model == "realtime-model"


def test_replay_preserves_compact_terminal_metric_without_arbitrary_runtime_data() -> None:
    assert replay_module.bounded_agent_metrics({
        "bridge_process_reused": True,
        "provider_connect_ms": 2_138.4,
        "response_validation_ms": 0.25,
        "provider_api_key": "must-not-leak",
    }) == {
        "bridge_process_reused": True,
        "provider_connect_ms": 2_138.4,
        "response_validation_ms": 0.25,
    }
    metrics = replay_module.bounded_agent_metrics(
        {
            "elapsed_ms": 1_250.0,
            "decision_latency_budget_ms": 2_500.0,
            "decision_timeout_ms": 5_000.0,
            "within_latency_budget": True,
            "prompt_profile": "candidate_fast",
            "compact_terminal_tools": True,
            "ttft_ms": 1_234.5,
            "provider_connect_ms": 2_138.4,
            "context_reads": 1,
            "tool_errors": [],
            "session_message_count_before": 2,
            "provider_api_key": "must-not-leak",
            "transcript": "must-not-leak",
        }
    )

    assert metrics == {
        "elapsed_ms": 1_250.0,
        "decision_latency_budget_ms": 2_500.0,
        "decision_timeout_ms": 5_000.0,
        "within_latency_budget": True,
        "prompt_profile": "candidate_fast",
        "compact_terminal_tools": True,
        "ttft_ms": 1_234.5,
        "provider_connect_ms": 2_138.4,
        "context_reads": 1,
        "tool_errors": [],
        "session_message_count_before": 2,
    }


def test_score_separates_event_hits_false_positives_and_silence() -> None:
    score = score_predictions(
        [
            {
                "expected": {
                    "action": "intervention",
                    "event_types": ["question_to_user", "commitment_risk"],
                    "required_evidence_ids": ["remote-1"],
                    "deadline_ms": 3_500,
                },
                "prediction": {
                    "action": "intervention",
                    "event_type": "question_to_user",
                    "evidence_segment_ids": ["remote-1"],
                },
                "latency_ms": 2_000,
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "agent_turns": 2,
            },
            {
                "expected": {"action": "silent", "deadline_ms": 3_500},
                "prediction": {"action": "silent"},
                "latency_ms": 1_000,
                "runtime_requested": "pi",
                "runtime_used": "direct",
                "agent_turns": 1,
            },
            {
                "expected": {"action": "silent"},
                "prediction": {
                    "action": "intervention",
                    "event_type": "goal_at_risk",
                    "evidence_segment_ids": ["remote-3"],
                },
                "latency_ms": 4_000,
                "runtime_requested": "direct",
                "runtime_used": "direct",
            },
        ]
    )

    assert score["precision"] == 0.5
    assert score["recall"] == 1.0
    assert score["silent_accuracy"] == 0.5
    assert score["required_evidence_accuracy"] == 1.0
    assert score["deadline_pass_rate"] == 1.0
    assert score["latency_p50_ms"] == 2_000
    assert score["latency_p95_ms"] == 4_000
    assert score["average_agent_turns"] == 1.5
    assert score["fallback_count"] == 1


def test_score_flags_missing_reasons_duplicate_interventions_and_evidence() -> None:
    score = score_predictions(
        [
            {
                "case_id": "case-1",
                "session_id": "session-1",
                "expected": {"action": "intervention"},
                "prediction": {
                    "action": "intervention",
                    "event_type": "goal_at_risk",
                    "evidence_segment_ids": ["p-1"],
                },
            },
            {
                "case_id": "case-2",
                "session_id": "session-1",
                "expected": {"action": "intervention"},
                "prediction": {
                    "action": "intervention",
                    "event_type": "goal_at_risk",
                    "evidence_segment_ids": ["p-1"],
                },
            },
            {
                "case_id": "case-3",
                "session_id": "session-1",
                "expected": {"action": "silent"},
                "prediction": {"action": "silent"},
            },
        ]
    )

    assert score["intervention_evidence_rate"] == 1.0
    assert score["silent_reason_rate"] == 0.0
    assert score["duplicate_intervention_count"] == 1


def test_score_separates_trigger_misses_from_protected_failures_and_latency() -> None:
    score = score_predictions(
        [
            {
                "case_id": "gate-miss",
                "expected": {"action": "intervention", "deadline_ms": 3_500},
                "prediction": {
                    "action": "silent",
                    "status": "not_triggered",
                    "decision_reason": "trigger gate",
                },
                "production_triggered": False,
                "decision_attempted": False,
                "latency_ms": 0,
            },
            {
                "case_id": "provider-timeout",
                "expected": {"action": "silent", "deadline_ms": 3_500},
                "prediction": {
                    "action": "silent",
                    "status": "timed_out",
                    "decision_reason": "provider timeout",
                },
                "production_triggered": True,
                "decision_attempted": True,
                "latency_ms": 4_000,
            },
        ]
    )

    assert score["production_trigger_recall"] == 0.0
    assert score["production_trigger_precision"] == 0.0
    assert score["production_trigger_false_positive_count"] == 1
    assert score["agent_decision_recall"] is None
    assert score["end_to_end_recall"] == 0.0
    assert score["timed_out_count"] == 1
    assert score["reliability_error_count"] == 1
    assert score["deadline_pass_rate"] == 0.0
    assert score["silent_accuracy"] == 0.0
    assert score["fallback_count"] == 0


def test_score_timeout_elapsed_is_not_successful_completion_latency() -> None:
    records = [
        {
            "expected": {"action": "silent", "deadline_ms": 2500},
            "prediction": {"action": "silent", "status": status},
            "latency_ms": latency,
            "decision_attempted": attempted,
        }
        for status, latency, attempted in [
            ("timed_out", 2000, True),
            ("failed", 50, True),
            ("protected_silent", 1000, True),
            ("not_triggered", 0, False),
        ]
    ]
    score = score_predictions(records)
    assert score["deadline_pass_rate"] == 0.3333
    assert score["latency_p50_ms"] == 1000
    assert score["completed_decision_latency_count"] == 1
    assert score["completed_decision_latency_p50_ms"] == 1000
    assert score["timeout_observed_latency_count"] == 1
    assert score["timeout_observed_latency_p50_ms"] == 2000

    timeout_only = score_predictions(records[:1])
    assert timeout_only["deadline_pass_rate"] == 0.0
    assert timeout_only["completed_decision_latency_p50_ms"] is None
    assert timeout_only["completed_decision_latency_count"] == 0


def test_score_does_not_count_failed_open_silent_as_correct_silence() -> None:
    score = score_predictions(
        [
            {
                "case_id": "pi-timeout-envelope",
                "expected": {"action": "silent"},
                "prediction": {
                    "action": "silent",
                    "status": "protected_silent",
                    "decision_reason": "Provider response was too late.",
                },
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": "agent_deadline_exceeded",
                "failure_class": "timeout",
                "latency_ms": 2_400,
            },
            {
                "case_id": "normal-protected-silent",
                "expected": {"action": "silent"},
                "prediction": {
                    "action": "silent",
                    "status": "protected_silent",
                    "decision_reason": "No reliable intervention.",
                },
                "failure_class": None,
                "latency_ms": 300,
            },
        ]
    )

    assert score["silent_accuracy"] == 0.5
    assert score["failed_open_silent_count"] == 1
    assert score["failure_class_counts"] == {"timeout": 1}
    assert score["reliability_error_count"] == 1


def test_replay_failure_classification_keeps_normal_silence_clean() -> None:
    assert replay_module.classify_replay_failure(
        {"status": "protected_silent", "fallback_error_code": "provider_timeout"}
    ) == "timeout"
    assert replay_module.classify_replay_failure(
        {"status": "protected_silent", "decision_reason": "No trigger."}
    ) is None
    assert replay_module.classify_replay_failure(
        {"status": "failed", "fallback_error_code": "provider_502"}
    ) == "provider_5xx"


def test_replay_failure_classification_keeps_gate_and_local_baseline_clean() -> None:
    assert replay_module.classify_replay_failure(
        {
            "runtime_requested": "pi",
            "decision_attempted": False,
            "status": "not_triggered",
            "status_reason": "trigger_gate",
        }
    ) is None
    assert replay_module.classify_replay_failure(
        {
            "runtime_requested": "local",
            "runtime_used": "local_reflex",
            "status": "protected_silent",
            "status_reason": "local_reflex_no_intervention",
        }
    ) is None


def test_score_overrides_stale_failure_markers_for_normal_gate_and_local_silence() -> None:
    score = score_predictions(
        [
            {
                "runtime_requested": "pi",
                "decision_attempted": False,
                "status": "not_triggered",
                "failure_class": "runtime_fallback",
                "prediction": {"action": "silent", "status": "not_triggered"},
                "expected": {"action": "silent"},
            },
            {
                "runtime_requested": "local",
                "runtime_used": "local_reflex",
                "status": "protected_silent",
                "failure_class": "runtime_fallback",
                "prediction": {"action": "silent", "status": "protected_silent"},
                "expected": {"action": "silent"},
            },
        ]
    )
    assert score["reliability_error_count"] == 0
    assert score["failed_open_silent_count"] == 0
    assert score["status_counts"] == {
        "not_triggered": 1,
        "protected_silent": 1,
    }


def test_score_invalid_latency_is_not_a_completion_measurement() -> None:
    for latency in (True, float("nan"), float("inf"), -1):
        score = score_predictions([{
            "prediction": {"action": "silent"},
            "latency_ms": latency,
        }])
        assert score["latency_p50_ms"] is None
        assert score["completed_decision_latency_count"] == 0


def test_acceptance_gate_fails_closed_for_missing_or_slow_metrics() -> None:
    score = {
        "precision": 1.0,
        "recall": 0.9,
        "silent_accuracy": 1.0,
        "required_evidence_accuracy": 0.9,
        "production_trigger_recall": 1.0,
        "latency_p50_ms": 8_081.0,
        "latency_p95_ms": 10_135.0,
        "latency_max_ms": 10_135.0,
        "reliability_error_count": 1,
        "fallback_count": 0,
    }

    acceptance = acceptance_from_score(score)

    assert acceptance["passed"] is False
    assert set(acceptance["failed_checks"]) == {
        "required_evidence_accuracy",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_max_ms",
        "reliability_error_count",
    }


def test_acceptance_gate_fails_closed_when_reliability_metrics_are_missing() -> None:
    score = {
        "precision": 1.0,
        "recall": 1.0,
        "silent_accuracy": 1.0,
        "required_evidence_accuracy": 1.0,
        "production_trigger_recall": 1.0,
        "latency_p50_ms": 1_000.0,
        "latency_p95_ms": 2_000.0,
        "latency_max_ms": 2_000.0,
    }

    acceptance = acceptance_from_score(score)

    assert acceptance["passed"] is False
    assert set(acceptance["failed_checks"]) == {
        "reliability_error_count",
        "fallback_count",
    }


def test_private_coach_smoke_dataset_is_balanced_and_covers_core_boundaries() -> None:
    cases = _load_fixture_cases()
    actions = Counter(str(case["expected"]["action"]) for case in cases)

    assert len(cases) >= 20
    assert actions["intervention"] >= 10
    assert actions["silent"] >= 10
    assert all(case["expected"].get("deadline_ms") == 3_500 for case in cases)

    core_capabilities = {
        "question_radar": "question_to_user",
        "commitment_firewall": "commitment_risk",
        "goal_guardian": "goal_at_risk",
        "contradiction": "contradiction",
    }
    for difficulty_tag, event_type in core_capabilities.items():
        assert any(
            case["expected"]["action"] == "intervention"
            and event_type in case["expected"].get("event_types", [])
            for case in cases
        )
        assert any(
            case["expected"]["action"] == "silent"
            and difficulty_tag in case.get("difficulty", [])
            for case in cases
        )

    silent_cases = [case for case in cases if case["expected"]["action"] == "silent"]
    assert all("should_stay_silent" in case.get("difficulty", []) for case in silent_cases)

    for skill_id in ("decision", "project", "interview", "brainstorm"):
        skill_actions = {
            case["expected"]["action"]
            for case in cases
            if case.get("coach_skill_id") == skill_id
        }
        assert skill_actions == {"intervention", "silent"}


def _assert_dataset_matches_static_schema_contract(cases: list[dict[str, Any]]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    expected_schema = schema["properties"]["expected"]["properties"]
    required_case_fields = set(schema["required"])
    allowed_expected_fields = set(expected_schema)
    allowed_actions = set(expected_schema["action"]["enum"])
    allowed_event_types = set(expected_schema["event_types"]["items"]["enum"])
    allowed_skill_ids = set(schema["properties"]["coach_skill_id"]["enum"])
    case_ids = [str(case.get("case_id") or "") for case in cases]
    session_ids = [str(case.get("session_id") or "") for case in cases]

    assert all(case_ids)
    assert len(case_ids) == len(set(case_ids))
    assert all(session_ids)
    if any("sequence_id" in case for case in cases):
        session_revisions = [
            (str(case.get("session_id") or ""), case.get("state_revision"))
            for case in cases
        ]
        assert len(session_revisions) == len(set(session_revisions))
    else:
        assert len(session_ids) == len(set(session_ids))

    for case in cases:
        assert required_case_fields <= set(case)
        trigger_type = str(case.get("trigger_type") or "delta")
        new_paragraphs = case.get("new_paragraphs") or []
        minimum_new_paragraphs = 1 if trigger_type in {"delta", "transcript_delta"} else 0
        if trigger_type in {"delta", "transcript_delta"}:
            assert "new_paragraphs" in case
        assert minimum_new_paragraphs <= len(new_paragraphs) <= schema["properties"]["new_paragraphs"]["maxItems"]
        assert len(case.get("context_paragraphs", [])) <= schema["properties"]["context_paragraphs"]["maxItems"]
        assert len(case.get("semantic_windows", [])) <= schema["properties"]["semantic_windows"]["maxItems"]
        assert len(case.get("difficulty", [])) == len(set(case.get("difficulty", [])))

        expected = case["expected"]
        action = expected["action"]
        event_types = expected.get("event_types", [])
        required_evidence_ids = expected.get("required_evidence_ids", [])
        new_id_values = [paragraph["id"] for paragraph in new_paragraphs]
        context_id_values = [paragraph["id"] for paragraph in case.get("context_paragraphs", [])]
        new_ids = set(new_id_values)
        context_ids = set(context_id_values)

        assert set(expected) <= allowed_expected_fields
        assert action in allowed_actions
        assert str(case.get("coach_skill_id") or "general") in allowed_skill_ids
        assert len(event_types) == len(set(event_types))
        assert len(required_evidence_ids) == len(set(required_evidence_ids))
        assert len(new_id_values + context_id_values) == len(new_ids | context_ids)
        if action == "intervention":
            assert event_types
            assert required_evidence_ids
        else:
            assert not event_types
            assert not required_evidence_ids
        assert set(event_types) <= allowed_event_types
        assert set(required_evidence_ids) <= new_ids | context_ids
        assert not required_evidence_ids or set(required_evidence_ids) & new_ids

        if "contradiction" in event_types:
            assert set(required_evidence_ids) & context_ids


def test_private_coach_smoke_dataset_matches_static_schema_contract() -> None:
    _assert_dataset_matches_static_schema_contract(_load_fixture_cases())


def test_dataset_schema_allows_non_delta_triggers_without_new_paragraphs() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert "new_paragraphs" not in schema["required"]
    delta_branch = next(
        branch["then"]
        for branch in schema["allOf"]
        if branch.get("if", {}).get("properties", {}).get("trigger_type", {}).get("enum")
        == ["delta", "transcript_delta"]
    )
    assert "new_paragraphs" in delta_branch["required"]
    assert delta_branch["properties"]["new_paragraphs"]["minItems"] == 1


def test_replay_preserves_non_delta_trigger_contract_and_fingerprint() -> None:
    evidence = {
        "id": "due-evidence-1",
        "text": "回滚负责人仍待确认。",
        "source_track": "microphone",
    }
    due_case = {
        "case_id": "task-due-1",
        "session_id": "task-due-session",
        "trigger_type": "task_due",
        "work_item_id": "decision-7",
        "new_paragraphs": [],
        "context_paragraphs": [evidence],
        "expected": {"action": "silent", "deadline_ms": 3500},
    }
    user_case = {
        "case_id": "user-request-1",
        "session_id": "user-request-session",
        "trigger_type": "user_request",
        "user_request": "发布前还缺哪些条件？",
        "new_paragraphs": [],
        "context_paragraphs": [],
        "expected": {"action": "silent", "deadline_ms": 3500},
    }

    due_request = replay_module.request_from_case(due_case)
    user_request = replay_module.request_from_case(user_case)
    assert due_request.trigger_type == "task_due"
    assert due_request.work_item_id == "decision-7"
    assert due_request.new_paragraphs == ()
    assert user_request.trigger_type == "user_request"
    assert user_request.user_request == "发布前还缺哪些条件？"
    assert replay_module.case_input_fingerprint(due_case) != replay_module.case_input_fingerprint(user_case)
    assert replay_module.should_run_realtime_coach(user_request, requested_runtime="pi") is True


@pytest.mark.parametrize(
    "case",
    [
        {
            "case_id": "invalid-task-due",
            "trigger_type": "task_due",
            "new_paragraphs": [],
            "context_paragraphs": [],
            "expected": {"action": "silent", "deadline_ms": 3500},
        },
        {
            "case_id": "invalid-user-request",
            "trigger_type": "user_request",
            "new_paragraphs": [],
            "context_paragraphs": [],
            "expected": {"action": "silent", "deadline_ms": 3500},
        },
    ],
)
def test_replay_rejects_incomplete_non_delta_trigger(case: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replay_module.request_from_case(case)


def test_stage0_formal_dataset_is_balanced_by_skill_and_boundary() -> None:
    cases = _load_formal_fixture_cases()
    skill_ids = ("general", "decision", "project", "interview", "brainstorm")
    core_boundaries = {
        "question_radar": "question_to_user",
        "commitment_firewall": "commitment_risk",
        "goal_guardian": "goal_at_risk",
        "contradiction": "contradiction",
        "communication_clarity": "communication_clarity",
    }
    scene_boundaries = {
        "decision": "decision_readiness",
        "project": "execution_gap",
        "interview": "discovery_gap",
        "brainstorm": "experiment_gap",
    }
    action_counts = Counter(
        (str(case["coach_skill_id"]), str(case["expected"]["action"]))
        for case in cases
    )

    assert len(cases) == 200
    assert all(
        action_counts[(skill_id, action)] == 20
        for skill_id in skill_ids
        for action in ("intervention", "silent")
    )
    assert all("formal_stage0" in case.get("difficulty", []) for case in cases)
    assert all(case["expected"].get("deadline_ms") == 3_500 for case in cases)
    assert all(
        "should_stay_silent" in case.get("difficulty", [])
        for case in cases
        if case["expected"]["action"] == "silent"
    )

    for skill_id in skill_ids:
        skill_cases = [case for case in cases if case["coach_skill_id"] == skill_id]
        expected_core_count = 4 if skill_id == "general" else 3
        for boundary_tag, event_type in core_boundaries.items():
            boundary_cases = [
                case for case in skill_cases if boundary_tag in case.get("difficulty", [])
            ]
            boundary_actions = Counter(case["expected"]["action"] for case in boundary_cases)
            assert boundary_actions == {
                "intervention": expected_core_count,
                "silent": expected_core_count,
            }
            assert all(
                case["expected"].get("event_types") == [event_type]
                for case in boundary_cases
                if case["expected"]["action"] == "intervention"
            )

    for skill_id, event_type in scene_boundaries.items():
        scene_cases = [
            case
            for case in cases
            if case["coach_skill_id"] == skill_id
            and event_type in case.get("difficulty", [])
        ]
        assert Counter(case["expected"]["action"] for case in scene_cases) == {
            "intervention": 5,
            "silent": 5,
        }
        assert all("scene_skill" in case.get("difficulty", []) for case in scene_cases)
        assert all(
            case["expected"].get("event_types") == [event_type]
            for case in scene_cases
            if case["expected"]["action"] == "intervention"
        )


def test_stage0_formal_dataset_matches_schema_identity_and_evidence_contracts() -> None:
    cases = _load_formal_fixture_cases()
    _assert_dataset_matches_static_schema_contract(cases)

    paragraph_ids: list[str] = []
    transcript_fingerprints: list[tuple[str, ...]] = []
    for case in cases:
        request = replay_module.request_from_case(case)
        assert request.coach_skill_id == case["coach_skill_id"]
        assert [item.id for item in request.retrieval_paragraphs] == [
            str(item["id"]) for item in case.get("retrieval_paragraphs", [])
        ]

        paragraphs = [
            *case["new_paragraphs"],
            *case.get("context_paragraphs", []),
        ]
        paragraph_ids.extend(str(paragraph["id"]) for paragraph in paragraphs)
        transcript_fingerprints.append(
            tuple(str(paragraph["text"]) for paragraph in case["new_paragraphs"])
        )

        expected = case["expected"]
        required_ids = set(expected.get("required_evidence_ids", []))
        new_ids = {str(paragraph["id"]) for paragraph in case["new_paragraphs"]}
        context_ids = {
            str(paragraph["id"]) for paragraph in case.get("context_paragraphs", [])
        }
        if expected["action"] == "intervention":
            assert len(expected["event_types"]) == 1
            assert required_ids
            assert required_ids <= new_ids | context_ids
            assert required_ids & new_ids
        else:
            assert "event_types" not in expected
            assert "required_evidence_ids" not in expected

        if expected.get("event_types") == ["contradiction"]:
            assert required_ids & new_ids
            assert required_ids & context_ids
        if expected.get("event_types") == ["communication_clarity"]:
            assert len(required_ids & new_ids) >= 2

    assert len(paragraph_ids) == len(set(paragraph_ids))
    assert len(transcript_fingerprints) == len(set(transcript_fingerprints))


def test_stage0_formal_expected_events_reach_the_matching_production_candidate() -> None:
    cases = _load_formal_fixture_cases()
    expected_candidate_types = {
        "question_to_user": {"question_pending"},
        "commitment_risk": {"commitment_without_condition"},
        "goal_at_risk": {"goal_at_risk"},
        "contradiction": {"objection_detected"},
        "communication_clarity": {"monologue_duration", "repetition"},
        "decision_readiness": {"commitment_without_condition"},
        "execution_gap": {"missing_next_step"},
        "discovery_gap": {"question_pending"},
        "experiment_gap": {"commitment_without_condition"},
    }
    triggered_counts: Counter[str] = Counter()
    clarity_candidate_types: set[str] = set()

    for case in cases:
        request = replay_module.request_from_case(case)
        candidates = replay_module.realtime_coach_candidate_events(request)
        candidate_types = {candidate.event_type for candidate in candidates}
        new_ids = {paragraph.id for paragraph in request.new_paragraphs}

        triggered = replay_module.should_run_realtime_coach(request, requested_runtime="pi")
        assert triggered == bool(candidates)
        assert all(set(candidate.evidence_segment_ids) <= new_ids for candidate in candidates)
        triggered_counts[f'{case["expected"]["action"]}:{"triggered" if triggered else "not_triggered"}'] += 1

        if case["expected"]["action"] == "intervention":
            event_type = str(case["expected"]["event_types"][0])
            assert expected_candidate_types[event_type] & candidate_types, case["case_id"]
            if event_type == "communication_clarity":
                clarity_candidate_types.update(
                    expected_candidate_types[event_type] & candidate_types
                )

    # Risk-gated sustained-discourse detection keeps every positive reachable
    # while avoiding Provider calls for the 16 clarity negatives that already
    # contain a structured conclusion and next step.
    assert triggered_counts == {
        "intervention:triggered": 100,
        "silent:triggered": 42,
        "silent:not_triggered": 58,
    }
    assert clarity_candidate_types == {"monologue_duration", "repetition"}


def test_stage0_formal_dataset_scores_balanced_perfect_predictions_without_provider() -> None:
    cases = _load_formal_fixture_cases()

    def record_from_case(case: dict[str, Any]) -> dict[str, Any]:
        expected = case["expected"]
        if expected["action"] == "intervention":
            prediction = {
                "action": "intervention",
                "status": "intervention",
                "event_type": expected["event_types"][0],
                "evidence_segment_ids": expected["required_evidence_ids"],
            }
        else:
            prediction = {
                "action": "silent",
                "status": "protected_silent",
                "decision_reason": "formal boundary is already resolved",
            }
        return {
            "case_id": case["case_id"],
            "session_id": case["session_id"],
            "coach_skill_id": case["coach_skill_id"],
            "expected": expected,
            "prediction": prediction,
            "production_triggered": True,
            "decision_attempted": True,
            "runtime_requested": "pi",
            "runtime_used": "pi",
            "latency_ms": 1_000,
        }

    records = [record_from_case(case) for case in cases]
    score = score_predictions(records)

    assert score["case_count"] == 200
    assert score["expected_intervention_count"] == 100
    assert score["correct_intervention_count"] == 100
    assert score["precision"] == 1.0
    assert score["recall"] == 1.0
    assert score["silent_accuracy"] == 1.0
    assert score["required_evidence_accuracy"] == 1.0
    assert score["duplicate_intervention_count"] == 0

    for skill_id in ("general", "decision", "project", "interview", "brainstorm"):
        skill_score = score_predictions(
            record for record in records if record["coach_skill_id"] == skill_id
        )
        assert skill_score["case_count"] == 40
        assert skill_score["expected_intervention_count"] == 20
        assert skill_score["precision"] == 1.0
        assert skill_score["recall"] == 1.0
        assert skill_score["silent_accuracy"] == 1.0


def test_ordered_lifecycle_fixture_replays_one_sequence_not_three_canaries() -> None:
    first_load = _load_ordered_fixture_cases()
    second_load = _load_ordered_fixture_cases()

    _assert_dataset_matches_static_schema_contract(first_load)
    first_summary = replay_module.ordered_lifecycle_summary(first_load)
    second_summary = replay_module.ordered_lifecycle_summary(second_load)

    assert first_summary == second_summary
    assert first_summary["case_count"] == 3
    assert first_summary["ordered_lifecycle_sequence_count"] == 1
    assert first_summary["ordered_lifecycle_turn_count"] == 3
    assert first_summary["single_turn_case_count"] == 0
    assert len(first_summary["dataset_contract_fingerprint"]) == 64
    assert first_summary["provider_canary_run_count"] == 0
    assert first_summary["provider_canary_evidence"] is False
    assert [case["turn_index"] for case in first_load] == [1, 2, 3]
    assert [case["sequence_stage"] for case in first_load] == [
        "open",
        "intervention",
        "resolving_evidence",
    ]
    assert len({case["session_id"] for case in first_load}) == 1
    assert [case["state_revision"] for case in first_load] == [1, 2, 3]

    requests = [replay_module.request_from_case(case) for case in first_load]
    assert len({request.meeting_id for request in requests}) == 1
    assert [request.state_revision for request in requests] == [1, 2, 3]


def test_ordered_lifecycle_fixture_closes_the_production_candidate_without_provider() -> None:
    cases = _load_ordered_fixture_cases()
    candidates = [
        replay_module.realtime_coach_candidate_events(
            replay_module.request_from_case(case)
        )
        for case in cases
    ]
    candidate_types = [{candidate.event_type for candidate in items} for items in candidates]

    assert candidate_types[0] == set()
    assert "missing_next_step" in candidate_types[1]
    assert candidate_types[2] == set()
    assert [
        replay_module.should_run_realtime_coach(
            replay_module.request_from_case(case),
            requested_runtime="pi",
        )
        for case in cases
    ] == [False, True, False]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda cases: cases[0].pop("sequence_stage"), "partial ordered lifecycle"),
        (
            lambda cases: cases[2].update({"turn_index": 4}),
            "turn_index must be exactly",
        ),
        (
            lambda cases: cases[2].update({"state_revision": 2}),
            "state_revision must strictly increase",
        ),
        (
            lambda cases: cases[2].update({"session_id": "wrong-session"}),
            "must use one session_id",
        ),
        (
            lambda cases: cases[1].update({"sequence_stage": "open"}),
            "stages must be",
        ),
        (
            lambda cases: cases[2]["expected"].update(
                {"supersedes_case_id": "wrong-case"}
            ),
            "must supersede the intervention case",
        ),
        (
            lambda cases: cases[2].update({"context_paragraphs": []}),
            "must reference prior evidence",
        ),
        (
            lambda cases: cases[2].update(
                {"new_paragraphs": [deepcopy(cases[1]["new_paragraphs"][0])]}
            ),
            "must contain fresh evidence",
        ),
    ],
)
def test_ordered_lifecycle_loader_fails_closed(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    cases = deepcopy(_load_ordered_fixture_cases())
    mutate(cases)
    dataset = tmp_path / "invalid-ordered.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        replay_module.load_dataset(dataset)


def test_ordered_lifecycle_score_is_deterministic_without_provider() -> None:
    cases = _load_ordered_fixture_cases()
    records = [
        {
            "case_id": cases[0]["case_id"],
            "session_id": cases[0]["session_id"],
            "expected": cases[0]["expected"],
            "prediction": {
                "action": "silent",
                "status": "not_triggered",
                "decision_reason": "No execution gap yet.",
            },
        },
        {
            "case_id": cases[1]["case_id"],
            "session_id": cases[1]["session_id"],
            "expected": cases[1]["expected"],
            "prediction": {
                "action": "intervention",
                "status": "intervention",
                "event_type": "execution_gap",
                "evidence_segment_ids": cases[1]["expected"]["required_evidence_ids"],
                "lifecycle_action": "retain",
            },
        },
        {
            "case_id": cases[2]["case_id"],
            "session_id": cases[2]["session_id"],
            "expected": cases[2]["expected"],
            "prediction": {
                "action": "silent",
                "status": "not_triggered",
                "decision_reason": "Fresh evidence resolves the execution gap.",
                "lifecycle_action": "deprioritize",
                "supersedes_case_id": cases[1]["case_id"],
            },
        },
    ]

    first_score = score_predictions(records)
    second_score = score_predictions(deepcopy(records))

    assert first_score == second_score
    assert first_score["expected_lifecycle_count"] == 2
    assert first_score["lifecycle_action_accuracy"] == 1.0
    assert first_score["expected_supersession_count"] == 1
    assert first_score["supersession_accuracy"] == 1.0


def test_prediction_only_propagates_observed_lifecycle_fields() -> None:
    prediction = replay_module.prediction_from_result(
        {
            "intervention": None,
            "status": "not_triggered",
            "decision_reason": "Fresh evidence resolves the execution gap.",
            "decision_id": "decision-3",
            "lifecycle_action": "deprioritize",
            "supersedes_decision_id": "decision-2",
            "supersedes_case_id": "case-2",
        }
    )
    plain_prediction = replay_module.prediction_from_result(
        {
            "intervention": None,
            "status": "not_triggered",
            "decision_reason": "No trigger.",
        }
    )

    assert prediction["decision_id"] == "decision-3"
    assert prediction["lifecycle_action"] == "deprioritize"
    assert prediction["supersedes_decision_id"] == "decision-2"
    assert prediction["supersedes_case_id"] == "case-2"
    assert "lifecycle_action" not in plain_prediction
    assert "supersedes_case_id" not in plain_prediction


def test_prediction_preserves_the_complete_product_card() -> None:
    intervention = SimpleNamespace(
        event_type="question_to_user",
        title="先明确当前状态",
        recommendation="目前只确认日志超时；负责人和反馈时间我核实后回复。",
        reason="对方正在等待可执行的进度边界。",
        confidence=0.91,
        evidence_segment_ids=("remote-1",),
        evidence_quote="这个故障今天谁来跟进，几点前能给结果？",
    )

    prediction = replay_module.prediction_from_result(
        {"intervention": intervention, "status": "intervention"}
    )

    assert prediction["recommendation"] == intervention.recommendation
    assert prediction["say_this"] == intervention.recommendation
    assert prediction["why_now"] == intervention.reason


def test_replay_record_preserves_sequence_metadata_without_provider(monkeypatch) -> None:
    case = _load_ordered_fixture_cases()[0]

    async def unexpected_provider_call(**_kwargs):
        raise AssertionError("open turn must not call Provider or Pi")

    monkeypatch.setattr(
        replay_module,
        "run_realtime_coach_routed",
        unexpected_provider_call,
    )
    config = LlmConfig(
        base_url="https://provider.example.test",
        api_key="test-only-key",
        model="test-model",
    )

    async def replay_open_turn() -> list[dict[str, Any]]:
        async with replay_module.httpx.AsyncClient() as client:
            return await replay_module.replay_mode(
                [case],
                runtime_name="pi",
                config=config,
                client=client,
                pi_runtime=object(),
            )

    record = asyncio.run(replay_open_turn())[0]

    assert record["sequence_id"] == case["sequence_id"]
    assert record["turn_index"] == 1
    assert record["sequence_stage"] == "open"
    assert record["state_revision"] == 1
    assert record["production_triggered"] is False
    assert record["decision_attempted"] is False


def test_replay_enforces_the_same_total_deadline_for_direct_and_pi(monkeypatch) -> None:
    case = deepcopy(_load_fixture_cases()[0])
    case["expected"]["deadline_ms"] = 20
    observed_provider_deadlines: list[int | None] = []

    async def slow_provider_call(**kwargs):
        observed_provider_deadlines.append(kwargs.get("max_provider_timeout_ms"))
        await asyncio.sleep(0.1)
        raise AssertionError("the replay deadline should cancel this call")

    monkeypatch.setattr(
        replay_module,
        "run_realtime_coach_routed",
        slow_provider_call,
    )
    config = LlmConfig(
        base_url="https://provider.example.test",
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=30,
    )

    async def replay_timed_runtime(runtime_name: str) -> dict[str, Any]:
        async with replay_module.httpx.AsyncClient() as client:
            records = await replay_module.replay_mode(
                [case],
                runtime_name=runtime_name,
                config=config,
                client=client,
                pi_runtime=object(),
            )
        return records[0]

    direct = asyncio.run(replay_timed_runtime("direct"))
    pi = asyncio.run(replay_timed_runtime("pi"))

    assert observed_provider_deadlines == [20, 20]
    for record in (direct, pi):
        assert record["deadline_ms"] == 20
        assert record["decision_attempted"] is True
        assert record["status"] == "timed_out"
        assert record["prediction"]["action"] == "silent"
        assert record["prediction"]["status"] == "timed_out"
        assert record["latency_ms"] < 100


def test_both_runtime_replay_keeps_pairs_adjacent_and_alternates_first_arm(
    monkeypatch,
) -> None:
    cases = deepcopy(_load_fixture_cases()[:5])
    calls: list[tuple[str, str, int]] = []

    monkeypatch.setattr(replay_module, "load_dataset", lambda _path: cases)
    monkeypatch.setattr(
        replay_module,
        "load_replay_config",
        lambda: LlmConfig(
            base_url="https://provider.example.test",
            api_key="test-only-key",
            model="test-model",
        ),
    )

    class FakePiRuntime:
        def close(self) -> None:
            return None

    monkeypatch.setattr(replay_module, "PiCoachSidecar", FakePiRuntime)

    async def fake_replay_mode(
        replay_cases,
        *,
        runtime_name,
        config,
        client,
        pi_runtime,
        decision_case_ids,
    ):
        del config, client, pi_runtime
        assert len(replay_cases) == 1
        case = replay_cases[0]
        calls.append((str(case["case_id"]), runtime_name, id(decision_case_ids)))
        decision_case_ids[str(case["case_id"])] = runtime_name
        return [
            {
                "case_id": str(case["case_id"]),
                "coach_skill_id": str(case.get("coach_skill_id") or "general"),
            }
        ]

    monkeypatch.setattr(replay_module, "replay_mode", fake_replay_mode)
    monkeypatch.setattr(
        replay_module,
        "score_predictions",
        lambda records: {"case_count": len(list(records))},
    )
    monkeypatch.setattr(
        replay_module,
        "acceptance_from_score",
        lambda score: {"passed": score["case_count"] == len(cases)},
    )

    report = asyncio.run(
        replay_module.run(SimpleNamespace(dataset=DATASET_PATH, runtime="both"))
    )

    expected_calls = []
    for index, case in enumerate(cases):
        arm_order = ("direct", "pi") if index % 2 == 0 else ("pi", "direct")
        expected_calls.extend((case["case_id"], runtime) for runtime in arm_order)
    assert [(case_id, runtime) for case_id, runtime, _state_id in calls] == expected_calls
    assert [record["case_id"] for record in report["results"]["direct"]["records"]] == [
        case["case_id"] for case in cases
    ]
    assert [record["case_id"] for record in report["results"]["pi"]["records"]] == [
        case["case_id"] for case in cases
    ]
    assert len({state_id for _, runtime, state_id in calls if runtime == "direct"}) == 1
    assert len({state_id for _, runtime, state_id in calls if runtime == "pi"}) == 1
    assert report["paired_execution"] == {
        "policy": replay_module.PAIRED_RUNTIME_ORDER_POLICY,
        "pair_adjacency": True,
        "first_arm_counts": {"direct": 3, "pi": 2},
        "schedule": [
            {
                "case_id": str(case["case_id"]),
                "arm_order": ["direct", "pi"]
                if index % 2 == 0
                else ["pi", "direct"],
            }
            for index, case in enumerate(cases)
        ],
    }
