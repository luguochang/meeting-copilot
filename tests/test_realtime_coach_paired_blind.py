from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tools.realtime_coach_eval.paired_blind import (
    audit_paired_report,
    build_blind_bundle,
    case_input_fingerprint,
    score_blind_annotations,
    validate_annotations,
)
from tools.realtime_coach_eval.replay import (
    SHARED_REPLAY_DEADLINE_POLICY,
    load_dataset,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET = REPO_ROOT / "tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl"


def _case(case_id: str = "paired-1") -> dict:
    return {
        "case_id": case_id,
        "session_id": f"session-{case_id}",
        "coach_skill_id": "general",
        "difficulty": ["question_radar"],
        "new_paragraphs": [{"id": "p1", "text": "请确认负责人和截止时间。"}],
        "context_paragraphs": [],
        "expected": {
            "action": "intervention",
            "event_types": ["question_to_user"],
            "required_evidence_ids": ["p1"],
            "deadline_ms": 3500,
        },
    }


def _record(
    case: dict,
    *,
    text: str,
    runtime: str = "direct",
    latency: float = 900,
    action: str = "intervention",
) -> dict:
    record = {
        "case_id": case["case_id"],
        "session_id": case["session_id"],
        "expected": deepcopy(case["expected"]),
        "runtime_requested": runtime,
        "runtime_used": runtime,
        "status": action,
        "model_requested": "shared-model",
        "api_style_requested": "chat_completions",
        "latency_ms": latency,
        "prediction": {
            "action": action,
            "status": action,
            "event_type": "question_to_user",
            "title": text,
            "recommendation": text,
            "say_this": text,
            "why_now": "这条建议需要在当前决策窗口内处理。",
            "evidence_segment_ids": ["p1"],
            "evidence_quote": "请确认负责人和截止时间。",
        },
    }
    record["deadline_ms"] = case["expected"]["deadline_ms"]
    record["decision_deadline_policy"] = SHARED_REPLAY_DEADLINE_POLICY
    record["input_fingerprint"] = case_input_fingerprint(case)
    return record


@pytest.mark.parametrize(
    ("record_field", "failure_value"),
    [
        ("status", "timed_out"),
        ("status", "failed"),
        ("status", "error"),
        ("prediction.status", "deadline_exceeded"),
        ("prediction.action", "error"),
        ("error", {"class": "ProviderError"}),
    ],
)
def test_build_blind_bundle_rejects_reliability_failures(
    record_field: str, failure_value: object
) -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    if record_field.startswith("prediction."):
        pi["prediction"][record_field.removeprefix("prediction.")] = failure_value
    else:
        pi[record_field] = failure_value

    with pytest.raises(ValueError, match="reliability failure|fails closed"):
        build_blind_bundle([case], [direct], [pi])


def test_audit_paired_report_collects_failures_and_keeps_value_gate_incomplete() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    pi["status"] = "timed_out"
    pi["prediction"]["status"] = "timed_out"
    pi["prediction"]["action"] = "silent"
    audit = audit_paired_report([case], [direct], [pi])

    assert audit["eligible_for_blind_preparation"] is False
    assert audit["value_gate_status"] == "incomplete"
    assert audit["paired_valid_case_count"] == 0
    assert audit["valid_pi_intervention_count"] == 0
    assert audit["runtime_details"]["pi"]["contract_failures"]
    assert any("reliability" in reason for reason in audit["reasons"])


def test_audit_paired_report_distinguishes_valid_small_sample_from_failure() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    audit = audit_paired_report([case], [direct], [pi])

    assert audit["eligible_for_blind_preparation"] is True
    assert audit["value_gate_status"] == "incomplete"
    assert audit["paired_valid_case_count"] == 1
    assert audit["valid_pi_intervention_count"] == 1


def test_build_blind_bundle_rejects_fallback_or_runtime_substitution() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    pi["fallback_error_code"] = "pi_sidecar_unavailable"

    with pytest.raises(ValueError, match="used fallback"):
        build_blind_bundle([case], [direct], [pi])

    pi["fallback_error_code"] = None
    pi["runtime_used"] = "direct"
    with pytest.raises(ValueError, match="did not use the requested runtime"):
        build_blind_bundle([case], [direct], [pi])


def test_build_blind_bundle_accepts_stale_evidence_as_a_valid_silent_decision() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi", action="silent")
    pi["status"] = "stale"
    pi["prediction"]["status"] = "stale"
    pi["prediction"].pop("event_type")
    pi["prediction"].pop("title")
    pi["prediction"].pop("recommendation")
    pi["prediction"].pop("say_this")
    pi["prediction"].pop("why_now")

    manifest, _unblind, _template = build_blind_bundle([case], [direct], [pi])

    assert manifest["pairs"][0]["variants"]["A"]["action"] in {"silent", "intervention"}
    assert any(
        variant["action"] == "silent"
        for variant in manifest["pairs"][0]["variants"].values()
    )


def _completed_annotations(
    template: list[dict],
    unblind: dict,
    *,
    ratings_by_runtime: dict[str, dict] | None = None,
) -> list[dict]:
    pair_by_id = {pair["blind_pair_id"]: pair for pair in unblind["pairs"]}
    ratings = {
        "direct": {
            "helpful": 1,
            "actionable": 1,
            "timing": 1,
            "incremental_value": 1,
            "restatement_only": 0,
        },
        "pi": {
            "helpful": 2,
            "actionable": 2,
            "timing": 2,
            "incremental_value": 2,
            "restatement_only": 0,
        },
    }
    for runtime, overrides in (ratings_by_runtime or {}).items():
        ratings[runtime].update(overrides)

    completed = []
    for labeler_id in ("r1", "r2"):
        for original in template:
            row = deepcopy(original)
            pair = pair_by_id[row["blind_pair_id"]]
            runtime = pair["variant_runtime"][row["variant"]]
            action = pair["variant_action"][row["variant"]]
            row.update(
                {
                    "labeler_id": labeler_id,
                    "expected_action": action,
                    "wrong_context": 0,
                    "duplicate": 0,
                    "retracted": 0,
                    "reason": "独立标注理由。",
                }
            )
            if action == "intervention":
                row.update(
                    {
                        "evidence_valid": 1,
                        "willing_to_use": 1,
                        "adopted": 1,
                        **ratings[runtime],
                    }
                )
            completed.append(row)
    return completed


def test_build_blind_bundle_keeps_runtime_and_expected_labels_private() -> None:
    case = _case()
    manifest, unblind, template = build_blind_bundle(
        [case],
        [_record(case, text="方案建议")],
        [_record(case, text="另一条方案建议", runtime="pi")],
        seed=3,
    )

    assert "variant_runtime" not in manifest
    assert "runtime" not in manifest
    assert all("expected" not in pair for pair in manifest["pairs"])
    assert manifest["schema_version"] == "talktrace.realtime_coach_paired_blind.v2"
    assert "incremental_value" in manifest["annotation_contract"]["ordinal_scale"]
    assert "restatement_only" in manifest["annotation_contract"]["binary_scale"]
    assert (
        "communicative function"
        not in manifest["annotation_contract"]["restatement_decision_rule"]["test"]
    )
    assert (
        "沟通功能"
        in manifest["annotation_contract"]["restatement_decision_rule"]["test"]
    )
    assert manifest["pairs"][0]["variants"]["A"]["say_this"]
    assert manifest["pairs"][0]["variants"]["A"]["why_now"]
    assert unblind["pairs"][0]["variant_runtime"] in (
        {"A": "direct", "B": "pi"},
        {"A": "pi", "B": "direct"},
    )
    assert unblind["pairs"][0]["variant_action"] == {
        "A": "intervention",
        "B": "intervention",
    }
    assert unblind["pairs"][0]["difficulty"] == ["question_radar"]
    assert unblind["pairs"][0]["expected"]["deadline_ms"] == 3500
    assert {row["variant"] for row in template} == {"A", "B"}
    assert all(row["labeler_id"] is None for row in template)
    assert all(row["expected_action"] is None for row in template)
    assert all(row["incremental_value"] is None for row in template)
    assert all(row["restatement_only"] is None for row in template)


def test_build_blind_bundle_rejects_different_input_or_deadline() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    pi["deadline_ms"] = 5000
    with pytest.raises(ValueError, match="deadline differs"):
        build_blind_bundle([case], [direct], [pi])

    pi.pop("deadline_ms")
    pi["input_fingerprint"] = "wrong"
    with pytest.raises(ValueError, match="input fingerprint differs"):
        build_blind_bundle([case], [direct], [pi])


def test_build_blind_bundle_requires_real_deadline_and_model_parity() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    direct.pop("decision_deadline_policy")

    with pytest.raises(ValueError, match="does not enforce the shared total deadline"):
        build_blind_bundle([case], [direct], [pi])

    direct["decision_deadline_policy"] = SHARED_REPLAY_DEADLINE_POLICY
    pi["model_requested"] = "different-model"
    with pytest.raises(ValueError, match="model_requested differs or is missing"):
        build_blind_bundle([case], [direct], [pi])


def test_build_blind_bundle_requires_the_full_product_card() -> None:
    case = _case()
    direct = _record(case, text="direct")
    pi = _record(case, text="pi", runtime="pi")
    pi["prediction"].pop("why_now")

    with pytest.raises(ValueError, match="blind stimulus matches the product card"):
        build_blind_bundle([case], [direct], [pi])


def test_input_fingerprint_changes_when_stimulus_or_deadline_changes() -> None:
    case = _case()
    changed = deepcopy(case)
    changed["new_paragraphs"][0]["text"] = "换了一句输入。"
    assert case_input_fingerprint(case) != case_input_fingerprint(changed)
    changed = deepcopy(case)
    changed["expected"]["deadline_ms"] = 4000
    assert case_input_fingerprint(case) != case_input_fingerprint(changed)


def test_validate_annotations_rejects_duplicate_rows_and_bad_rating() -> None:
    case = _case()
    _, unblind, rows = build_blind_bundle(
        [case], [_record(case, text="direct")], [_record(case, text="pi", runtime="pi")]
    )
    rows[0].update({"labeler_id": "r1", "helpful": 2})
    rows[1].update({"labeler_id": "r1", "helpful": 3})
    with pytest.raises(ValueError, match="between 0 and 2"):
        validate_annotations(rows, unblind)
    rows[1]["helpful"] = 2
    with pytest.raises(ValueError, match="duplicate annotation"):
        validate_annotations([rows[0], rows[0]], unblind)


def test_score_blind_annotations_reports_arm_rates_and_paired_delta() -> None:
    case = _case()
    _, unblind, template = build_blind_bundle(
        [case],
        [_record(case, text="direct")],
        [_record(case, text="pi", runtime="pi")],
        seed=5,
    )
    completed = _completed_annotations(template, unblind)

    score = score_blind_annotations(completed, unblind, bootstrap_seed=11)
    assert score["pair_count"] == 1
    assert score["arms"]["direct"]["helpful_rate"] == 1.0
    assert score["arms"]["pi"]["helpful_rate"] == 1.0
    assert score["paired_deltas"]["helpful"]["paired_count"] == 1
    assert score["paired_deltas"]["helpful"]["mean_pi_minus_direct"] == 1.0
    assert score["arms"]["pi"]["incremental_value_rate"] == 1.0
    assert score["arms"]["pi"]["incremental_value_strong_rate"] == 1.0
    assert score["arms"]["direct"]["incremental_value_strong_rate"] == 0.0
    assert score["arms"]["pi"]["restatement_only_rate"] == 0.0
    assert score["arms"]["pi"]["grounded_incremental_move_rate"] == 1.0
    assert score["paired_deltas"]["incremental_value"]["mean_pi_minus_direct"] == 1.0
    assert (
        score["paired_deltas"]["grounded_incremental_move"]["mean_pi_minus_direct"]
        == 0.0
    )
    assert score["value_gate"]["status"] == "incomplete"
    assert score["skill_summaries"]["general"]["value_gate"]["status"] == "incomplete"
    assert (
        score["p0_value_slice_summaries"]["question_radar"]["value_gate"]["status"]
        == "incomplete"
    )
    assert score["p0_value_slice_summaries"]["commitment_firewall"]["pair_count"] == 0
    assert score["value_gate"]["checks"]["minimum_pi_intervention_count"] == {
        "actual": 1,
        "threshold": 20,
        "passed": False,
    }
    # A one-pair sample cannot support a confidence interval.
    assert score["paired_deltas"]["helpful"]["ci95"] is None


def test_score_requires_two_independent_labelers_by_default() -> None:
    case = _case()
    _, unblind, template = build_blind_bundle(
        [case], [_record(case, text="direct")], [_record(case, text="pi", runtime="pi")]
    )
    for row in template:
        row.update({"labeler_id": "only-one"})
    with pytest.raises(ValueError, match="at least 2 independent labelers"):
        score_blind_annotations(template, unblind)


def test_score_rejects_legacy_bundle_without_value_contract() -> None:
    case = _case()
    _, unblind, template = build_blind_bundle(
        [case], [_record(case, text="direct")], [_record(case, text="pi", runtime="pi")]
    )
    unblind["schema_version"] = "talktrace.realtime_coach_paired_blind.v1"

    with pytest.raises(ValueError, match="regenerate the blind bundle"):
        score_blind_annotations(_completed_annotations(template, unblind), unblind)


@pytest.mark.parametrize(
    ("incremental_value", "restatement_only", "reason", "match"),
    [
        (
            1,
            None,
            "有增量",
            "must rate incremental_value and restatement_only together",
        ),
        (1, 1, "同一个问题", "restatement_only=1 must set incremental_value=0"),
        (0, 1, None, "must explain the duplicated communicative function"),
    ],
)
def test_value_annotations_fail_closed_on_inconsistent_restatement_labels(
    incremental_value: int,
    restatement_only: int | None,
    reason: str | None,
    match: str,
) -> None:
    case = _case()
    _, unblind, rows = build_blind_bundle(
        [case], [_record(case, text="direct")], [_record(case, text="pi", runtime="pi")]
    )
    rows[0].update(
        {
            "labeler_id": "r1",
            "incremental_value": incremental_value,
            "restatement_only": restatement_only,
            "reason": reason,
        }
    )

    with pytest.raises(ValueError, match=match):
        validate_annotations([rows[0]], unblind)


def test_unresolved_two_labeler_value_disagreement_requires_adjudication() -> None:
    case = _case()
    _, unblind, template = build_blind_bundle(
        [case], [_record(case, text="direct")], [_record(case, text="pi", runtime="pi")]
    )
    completed = _completed_annotations(template, unblind)
    pi_variant = next(
        variant
        for variant, runtime in unblind["pairs"][0]["variant_runtime"].items()
        if runtime == "pi"
    )
    first_pi = next(
        row
        for row in completed
        if row["variant"] == pi_variant and row["labeler_id"] == "r1"
    )
    first_pi.update(
        {
            "incremental_value": 0,
            "restatement_only": 1,
            "reason": "建议只是把原问题换一种说法。",
        }
    )

    with pytest.raises(ValueError, match="add an independent adjudicator"):
        score_blind_annotations(completed, unblind)


def test_silent_variant_does_not_require_intervention_value_ratings() -> None:
    case = _case()
    _, unblind, template = build_blind_bundle(
        [case],
        [_record(case, text="direct", action="silent")],
        [_record(case, text="pi", runtime="pi")],
    )
    completed = _completed_annotations(template, unblind)

    score = score_blind_annotations(completed, unblind)

    assert score["arms"]["direct"]["rendered_intervention_count"] == 0
    assert score["arms"]["direct"]["incremental_value_count"] == 0
    assert score["arms"]["direct"]["grounded_incremental_move_rate"] is None
    assert score["arms"]["pi"]["rendered_intervention_count"] == 1


def test_value_gate_passes_only_with_sufficient_non_restatement_sample() -> None:
    cases = [_case(f"paired-{index:02d}") for index in range(20)]
    direct = [_record(case, text=f"direct-{index}") for index, case in enumerate(cases)]
    pi = [
        _record(case, text=f"pi-{index}", runtime="pi")
        for index, case in enumerate(cases)
    ]
    _, unblind, template = build_blind_bundle(cases, direct, pi, seed=17)

    score = score_blind_annotations(_completed_annotations(template, unblind), unblind)

    assert score["value_gate"]["status"] == "passed"
    assert all(check["passed"] for check in score["value_gate"]["checks"].values())
    assert score["skill_summaries"]["general"]["value_gate"]["status"] == "passed"
    assert (
        score["p0_value_slice_summaries"]["question_radar"]["value_gate"]["status"]
        == "passed"
    )
    assert (
        score["p0_value_slice_summaries"]["goal_guardian"]["value_gate"]["status"]
        == "incomplete"
    )


def test_value_gate_fails_when_restatement_rate_exceeds_five_percent() -> None:
    cases = [_case(f"paired-{index:02d}") for index in range(20)]
    direct = [_record(case, text=f"direct-{index}") for index, case in enumerate(cases)]
    pi = [
        _record(case, text=f"pi-{index}", runtime="pi")
        for index, case in enumerate(cases)
    ]
    _, unblind, template = build_blind_bundle(cases, direct, pi, seed=19)
    completed = _completed_annotations(template, unblind)
    first_pair = unblind["pairs"][0]
    pi_variant = next(
        variant
        for variant, runtime in first_pair["variant_runtime"].items()
        if runtime == "pi"
    )
    for row in completed:
        if (
            row["blind_pair_id"] == first_pair["blind_pair_id"]
            and row["variant"] == pi_variant
        ):
            row.update(
                {
                    "incremental_value": 0,
                    "restatement_only": 1,
                    "reason": "建议重新询问了原话已经要求的相同字段。",
                }
            )

    score = score_blind_annotations(completed, unblind)

    assert score["arms"]["pi"]["restatement_only_rate"] == 0.05
    assert score["value_gate"]["status"] == "passed"
    for row in completed:
        if row["blind_pair_id"] == unblind["pairs"][1]["blind_pair_id"]:
            pair = unblind["pairs"][1]
            if pair["variant_runtime"][row["variant"]] == "pi":
                row.update(
                    {
                        "incremental_value": 0,
                        "restatement_only": 1,
                        "reason": "建议重新询问了原话已经要求的相同字段。",
                    }
                )

    failed = score_blind_annotations(completed, unblind)

    assert failed["arms"]["pi"]["restatement_only_rate"] == 0.1
    assert failed["value_gate"]["status"] == "failed"
    assert not failed["value_gate"]["checks"]["maximum_pi_restatement_only_rate"][
        "passed"
    ]


def test_real_fixture_can_build_synthetic_pair_bundle() -> None:
    cases = load_dataset(DATASET)
    direct = []
    pi = []
    for case in cases:
        direct.append(
            _record(
                {**case, "session_id": case.get("session_id") or case["case_id"]},
                text="direct",
            )
        )
        direct[-1]["case_id"] = case["case_id"]
        direct[-1]["expected"] = deepcopy(case["expected"])
        pi_record = deepcopy(direct[-1])
        pi_record["runtime_requested"] = "pi"
        pi_record["runtime_used"] = "pi"
        pi.append(pi_record)
    manifest, unblind, template = build_blind_bundle(cases, direct, pi, seed=9)
    assert manifest["pair_count"] == len(cases)
    assert unblind["pair_count"] == len(cases)
    assert len(template) == len(cases) * 2
