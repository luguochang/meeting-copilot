from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.realtime_coach_eval.score import score_predictions


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
