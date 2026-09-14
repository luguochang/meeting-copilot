"""Prepare and score a blinded Direct/Pi paired evaluation.

The replay tool deliberately keeps engineering metadata in its report.  This
module creates the separate artifact that can be handed to human annotators:
runtime names, model/provider details, expected labels, and internal failure
reasons live only in the unblind mapping.  No provider call is made here.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any, Iterable, Mapping, Sequence

# Support both ``python -m tools.realtime_coach_eval.paired_blind`` and direct
# execution from any working directory, matching the replay tool's CLI entry.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.realtime_coach_eval.replay import (  # noqa: E402
    SHARED_REPLAY_DEADLINE_POLICY,
    case_input_fingerprint,
    load_dataset,
)


SCHEMA_VERSION = "talktrace.realtime_coach_paired_blind.v2"
RATING_SPECS: dict[str, dict[str, Any]] = {
    "evidence_valid": {"kind": "binary", "minimum": 0, "maximum": 1},
    "helpful": {"kind": "ordinal", "minimum": 0, "maximum": 2},
    "actionable": {"kind": "ordinal", "minimum": 0, "maximum": 2},
    "timing": {"kind": "ordinal", "minimum": 0, "maximum": 2},
    "incremental_value": {"kind": "ordinal", "minimum": 0, "maximum": 2},
    "restatement_only": {"kind": "binary", "minimum": 0, "maximum": 1},
    "wrong_context": {"kind": "binary", "minimum": 0, "maximum": 1},
    "duplicate": {"kind": "binary", "minimum": 0, "maximum": 1},
    "retracted": {"kind": "binary", "minimum": 0, "maximum": 1},
    "willing_to_use": {"kind": "binary", "minimum": 0, "maximum": 1},
    "adopted": {"kind": "binary", "minimum": 0, "maximum": 1},
}
ORDINAL_RATINGS = ("helpful", "actionable", "timing", "incremental_value")
INTERVENTION_RATINGS = frozenset(
    {
        "evidence_valid",
        "helpful",
        "actionable",
        "timing",
        "incremental_value",
        "restatement_only",
        "willing_to_use",
        "adopted",
    }
)
REQUIRED_VALUE_RATINGS = (
    "evidence_valid",
    "helpful",
    "actionable",
    "timing",
    "incremental_value",
    "restatement_only",
)
VALUE_GATE_THRESHOLDS: dict[str, float | int] = {
    "minimum_pi_intervention_count": 20,
    "minimum_pi_incremental_value_rate": 0.70,
    "minimum_pi_grounded_incremental_move_rate": 0.60,
    "maximum_pi_restatement_only_rate": 0.05,
}
P0_VALUE_SLICES = ("question_radar", "commitment_firewall", "goal_guardian")
BLINDABLE_STATUS_ACTIONS = {
    "intervention": "intervention",
    "not_triggered": "silent",
    "protected_silent": "silent",
    "stale": "silent",
    # Retain compatibility with synthetic/legacy direct records that used the
    # decision action as the terminal status. Reliability failures are still
    # rejected explicitly below.
    "silent": "silent",
}
RELIABILITY_FAILURE_STATUSES = frozenset(
    {
        "cancelled",
        "deadline_exceeded",
        "error",
        "failed",
        "provider_failed",
        "timed_out",
        "timeout",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _case_index(cases: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("dataset case is missing case_id")
        if case_id in index:
            raise ValueError(f"duplicate dataset case_id: {case_id}")
        index[case_id] = case
    return index


def _records_by_case(
    records: Iterable[Mapping[str, Any]], runtime: str
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        case_id = str(record.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(f"{runtime} record is missing case_id")
        if case_id in result:
            raise ValueError(f"duplicate {runtime} record for case_id={case_id}")
        result[case_id] = record
    return result


def _extract_results(
    report: Mapping[str, Any],
) -> tuple[Iterable[Mapping[str, Any]], Iterable[Mapping[str, Any]]]:
    results = report.get("results")
    if not isinstance(results, Mapping):
        raise ValueError("replay report must contain results.direct and results.pi")
    direct = results.get("direct")
    pi = results.get("pi")
    if not isinstance(direct, Mapping) or not isinstance(pi, Mapping):
        raise ValueError("replay report must contain both direct and pi results")
    direct_records = direct.get("records")
    pi_records = pi.get("records")
    if not isinstance(direct_records, list) or not isinstance(pi_records, list):
        raise ValueError("replay report results must contain records arrays")
    return direct_records, pi_records


def audit_paired_report(
    cases: Sequence[Mapping[str, Any]],
    direct_records: Iterable[Mapping[str, Any]],
    pi_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a non-mutating eligibility audit for a Direct/Pi replay.

    Preparation deliberately raises on the first invalid record because an
    annotator bundle must be fail-closed.  This audit keeps the same contract
    but collects every reason so an experiment owner can see exactly why a
    real-provider report cannot enter the human value gate.
    """

    dataset = _case_index(cases)
    reports = {
        "direct": list(direct_records),
        "pi": list(pi_records),
    }
    details: dict[str, Any] = {}
    valid_case_ids: dict[str, set[str]] = {}
    for runtime, records in reports.items():
        by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for record in records:
            case_id = str(record.get("case_id") or "").strip()
            by_case[case_id].append(record)
        duplicate_case_ids = sorted(
            case_id for case_id, rows in by_case.items() if case_id and len(rows) > 1
        )
        missing_case_ids = sorted(set(dataset) - set(by_case))
        unexpected_case_ids = sorted(
            case_id for case_id in by_case if case_id not in dataset
        )
        failures: list[dict[str, str]] = []
        valid_ids: set[str] = set()
        for case_id, rows in sorted(by_case.items()):
            if not case_id or case_id not in dataset or len(rows) != 1:
                continue
            try:
                _validate_record_contract(
                    dataset[case_id],
                    rows[0],
                    runtime,
                    case_input_fingerprint(dataset[case_id]),
                )
            except ValueError as exc:
                failures.append({"case_id": case_id, "reason": str(exc)})
            else:
                valid_ids.add(case_id)
        valid_case_ids[runtime] = valid_ids
        details[runtime] = {
            "record_count": len(records),
            "valid_case_count": len(valid_ids),
            "duplicate_case_ids": duplicate_case_ids,
            "missing_case_ids": missing_case_ids,
            "unexpected_case_ids": unexpected_case_ids,
            "contract_failures": failures,
        }

    paired_valid_case_ids = sorted(valid_case_ids["direct"] & valid_case_ids["pi"])
    pi_by_case = {
        str(record.get("case_id")): record
        for record in reports["pi"]
        if str(record.get("case_id") or "") in paired_valid_case_ids
    }
    pi_intervention_case_ids = sorted(
        case_id
        for case_id, record in pi_by_case.items()
        if isinstance(record.get("prediction"), Mapping)
        and record["prediction"].get("action") == "intervention"
    )
    reasons: list[str] = []
    for runtime in ("direct", "pi"):
        if details[runtime]["duplicate_case_ids"]:
            reasons.append(f"{runtime}: duplicate case IDs")
        if details[runtime]["missing_case_ids"]:
            reasons.append(f"{runtime}: missing case IDs")
        if details[runtime]["unexpected_case_ids"]:
            reasons.append(f"{runtime}: unexpected case IDs")
        if details[runtime]["contract_failures"]:
            reasons.append(f"{runtime}: reliability or contract failures")
    if not paired_valid_case_ids:
        reasons.append("no Direct/Pi pair satisfies the reliability contract")
    if len(pi_intervention_case_ids) < int(VALUE_GATE_THRESHOLDS["minimum_pi_intervention_count"]):
        reasons.append(
            "fewer than 20 valid Pi interventions; human value gate is incomplete"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": "paired_blind_eligibility_audit",
        "eligible_for_blind_preparation": not any(
            reason
            for reason in reasons
            if "human value gate is incomplete" not in reason
        ),
        "value_gate_status": (
            "ready_for_annotation"
            if len(pi_intervention_case_ids)
            >= int(VALUE_GATE_THRESHOLDS["minimum_pi_intervention_count"])
            else "incomplete"
        ),
        "paired_valid_case_count": len(paired_valid_case_ids),
        "paired_valid_case_ids": paired_valid_case_ids,
        "valid_pi_intervention_count": len(pi_intervention_case_ids),
        "valid_pi_intervention_case_ids": pi_intervention_case_ids,
        "value_gate_thresholds": VALUE_GATE_THRESHOLDS,
        "runtime_details": details,
        "reasons": reasons,
    }


def _validate_record_contract(
    case: Mapping[str, Any], record: Mapping[str, Any], runtime: str, fingerprint: str
) -> None:
    case_id = str(case["case_id"])
    if str(record.get("case_id") or "") != case_id:
        raise ValueError(f"{runtime} record case mismatch for {case_id}")
    if str(record.get("runtime_requested") or "") != runtime:
        raise ValueError(f"{runtime} runtime_requested differs for {case_id}")
    expected = case.get("expected") if isinstance(case.get("expected"), Mapping) else {}
    record_expected = record.get("expected")
    if isinstance(record_expected, Mapping) and _canonical(
        record_expected
    ) != _canonical(expected):
        raise ValueError(f"{runtime} expected label/deadline differs for {case_id}")
    record_fingerprint = record.get("input_fingerprint")
    if not isinstance(record_fingerprint, str) or record_fingerprint != fingerprint:
        raise ValueError(f"{runtime} input fingerprint differs for {case_id}")
    record_deadline = record.get("deadline_ms")
    if record_deadline != expected.get("deadline_ms"):
        raise ValueError(f"{runtime} deadline differs for {case_id}")
    if record.get("decision_deadline_policy") != SHARED_REPLAY_DEADLINE_POLICY:
        raise ValueError(
            f"{runtime} record for {case_id} does not enforce the shared total deadline; "
            "regenerate the replay"
        )

    prediction = record.get("prediction")
    if not isinstance(prediction, Mapping):
        raise ValueError(f"{runtime} record for {case_id} is missing prediction")
    action = str(prediction.get("action") or "").strip()
    record_status = str(record.get("status") or "").strip()
    prediction_status = str(prediction.get("status") or "").strip()
    observed_statuses = {record_status, prediction_status, action} - {""}
    reliability_failures = sorted(observed_statuses & RELIABILITY_FAILURE_STATUSES)
    if reliability_failures or record.get("error") is not None:
        failure = ", ".join(reliability_failures) or "error payload"
        raise ValueError(
            f"{runtime} record for {case_id} has reliability failure {failure}; "
            "paired-blind preparation requires two successful arms"
        )
    fallback_error_code = str(record.get("fallback_error_code") or "").strip()
    if fallback_error_code:
        raise ValueError(
            f"{runtime} record for {case_id} used fallback {fallback_error_code}; "
            "paired-blind preparation requires the requested runtime in both arms"
        )
    if not record_status or not prediction_status:
        raise ValueError(
            f"{runtime} record for {case_id} is missing terminal status; "
            "regenerate the replay"
        )
    if record_status != prediction_status:
        raise ValueError(
            f"{runtime} record for {case_id} has conflicting terminal statuses "
            f"({record_status!r} vs {prediction_status!r}); regenerate the replay"
        )
    expected_action = BLINDABLE_STATUS_ACTIONS.get(record_status)
    if expected_action is None:
        raise ValueError(
            f"{runtime} record for {case_id} has unsupported terminal status "
            f"{record_status!r}; paired-blind preparation fails closed"
        )
    if action != expected_action:
        raise ValueError(
            f"{runtime} record for {case_id} has action {action!r} inconsistent with "
            f"terminal status {record_status!r}"
        )
    if record.get("decision_attempted", True) is not False:
        runtime_used = str(record.get("runtime_used") or "").strip()
        if runtime_used != runtime:
            raise ValueError(
                f"{runtime} record for {case_id} did not use the requested runtime; "
                "paired-blind preparation fails closed"
            )


def _prediction_for_display(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return fields safe for an annotator; omit runtime and internal reasons."""

    prediction = (
        record.get("prediction")
        if isinstance(record.get("prediction"), Mapping)
        else {}
    )
    action = str(prediction.get("action") or "silent")
    is_card = action == "intervention"
    say_this = str(
        prediction.get("say_this") or prediction.get("recommendation") or ""
    ).strip()
    why_now = str(prediction.get("why_now") or prediction.get("reason") or "").strip()
    if is_card and not say_this:
        raise ValueError(
            "rendered intervention is missing say_this/recommendation; regenerate the replay"
        )
    if is_card and not why_now:
        raise ValueError(
            "rendered intervention is missing why_now/reason; regenerate the replay so the "
            "blind stimulus matches the product card"
        )
    output: dict[str, Any] = {
        "action": "intervention" if is_card else "silent",
        "rendered": is_card,
        "event_type": str(prediction.get("event_type") or "") if is_card else None,
        "title": str(prediction.get("title") or "") if is_card else None,
        "recommendation": say_this if is_card else None,
        "say_this": say_this if is_card else None,
        "why_now": why_now if is_card else None,
        "evidence_segment_ids": [
            str(value)
            for value in (prediction.get("evidence_segment_ids") or [])
            if str(value)
        ]
        if is_card
        else [],
        "evidence_quote": str(prediction.get("evidence_quote") or "")
        if is_card
        else None,
    }
    latency = record.get("latency_ms")
    if (
        isinstance(latency, (int, float))
        and not isinstance(latency, bool)
        and latency >= 0
    ):
        output["latency_ms"] = round(float(latency), 2)
    return output


def _stimulus_for_display(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "new_paragraphs": case.get("new_paragraphs") or [],
        "context_paragraphs": case.get("context_paragraphs") or [],
        "semantic_windows": case.get("semantic_windows") or [],
        "rolling_state": case.get("rolling_state") or {},
        "meeting_goal": case.get("meeting_goal"),
        "coach_skill_id": case.get("coach_skill_id") or "general",
        "deadline_ms": case["expected"]["deadline_ms"],
    }


def build_blind_bundle(
    cases: Sequence[Mapping[str, Any]],
    direct_records: Iterable[Mapping[str, Any]],
    pi_records: Iterable[Mapping[str, Any]],
    *,
    seed: int = 0,
    experiment_id: str = "realtime-coach-stage0",
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build public manifest, private mapping, and blank annotation rows."""

    dataset = _case_index(cases)
    direct = _records_by_case(direct_records, "direct")
    pi = _records_by_case(pi_records, "pi")
    if set(direct) != set(dataset) or set(pi) != set(dataset):
        raise ValueError(
            "dataset and both runtime record sets must contain exactly the same case IDs"
        )

    rng = random.Random(seed)
    pairs: list[dict[str, Any]] = []
    mapping_pairs: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    for position, case_id in enumerate(sorted(dataset), start=1):
        case = dataset[case_id]
        fingerprint = case_input_fingerprint(case)
        _validate_record_contract(case, direct[case_id], "direct", fingerprint)
        _validate_record_contract(case, pi[case_id], "pi", fingerprint)
        for field in ("model_requested", "api_style_requested"):
            direct_value = str(direct[case_id].get(field) or "").strip()
            pi_value = str(pi[case_id].get(field) or "").strip()
            if not direct_value or direct_value != pi_value:
                raise ValueError(
                    f"direct/Pi {field} differs or is missing for {case_id}; "
                    "regenerate both arms in one replay"
                )
        variants = [("direct", direct[case_id]), ("pi", pi[case_id])]
        rng.shuffle(variants)
        blind_pair_id = f"pair-{position:04d}"
        public_variants: dict[str, Any] = {}
        private_variant_runtime: dict[str, str] = {}
        private_variant_action: dict[str, str] = {}
        for label, (runtime, record) in zip(("A", "B"), variants):
            display_prediction = _prediction_for_display(record)
            public_variants[label] = display_prediction
            private_variant_runtime[label] = runtime
            private_variant_action[label] = str(display_prediction["action"])
            annotations.append(
                {
                    "blind_pair_id": blind_pair_id,
                    "variant": label,
                    "labeler_id": None,
                    # The human labeler independently decides whether an
                    # intervention is warranted. The fixture's expected
                    # label stays in the private mapping.
                    "expected_action": None,
                    **{name: None for name in RATING_SPECS},
                    "reason": None,
                }
            )
        pairs.append(
            {
                "blind_pair_id": blind_pair_id,
                "input_fingerprint": fingerprint,
                "stimulus": _stimulus_for_display(case),
                "variants": public_variants,
            }
        )
        expected = dict(case.get("expected") or {})
        mapping_pairs.append(
            {
                "blind_pair_id": blind_pair_id,
                "case_id": case_id,
                "input_fingerprint": fingerprint,
                "deadline_ms": expected.get("deadline_ms"),
                "coach_skill_id": case.get("coach_skill_id") or "general",
                "difficulty": [str(value) for value in case.get("difficulty") or []],
                "expected": expected,
                "variant_runtime": private_variant_runtime,
                "variant_action": private_variant_action,
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact": "public_blind_manifest",
        "experiment_id": experiment_id,
        "seed": seed,
        "pair_count": len(pairs),
        "annotation_contract": {
            "ratings": RATING_SPECS,
            "ordinal_scale": {
                "helpful": {
                    "0": "泛化、错误或无用",
                    "1": "方向正确但需要重写",
                    "2": "指出真实损失且可理解",
                },
                "actionable": {
                    "0": "只有总结/评价",
                    "1": "需要二次组织",
                    "2": "一句话可立即说/做",
                },
                "timing": {
                    "0": "已过期或打断无意义",
                    "1": "略晚但仍可澄清",
                    "2": "仍在可改变结果的窗口",
                },
                "incremental_value": {
                    "0": "只是复制、改写原话或泛化口号，没有增加决策/行动指导",
                    "1": "把已有信息转成一个真实但仍需润色的行动推进",
                    "2": "增加有证据的回答姿态、边界条件、缺失维度、闭环动作或冲突化解",
                },
            },
            "binary_scale": {
                "restatement_only": {
                    "0": "建议增加了不可由原话直接替代的行动或决策指导",
                    "1": "把建议换回最新原话也不损失任何行动或决策指导",
                },
            },
            "restatement_decision_rule": {
                "test": (
                    "判断沟通功能、对象和已要求字段是否都相同；不要用字符或关键词相似度代替人工判断"
                ),
                "restatement_example": (
                    "原话已问‘谁跟进、几点给结果’，建议仍问‘具体谁负责、几点反馈’"
                ),
                "increment_example": (
                    "原话只说负责人未定，建议把状态转换为现场指定负责人和期限的闭环问题"
                ),
                "response_stance_example": (
                    "原话问谁跟进、几点给结果，建议基于已知事实说明当前状态并承诺核实后回复"
                ),
                "duplicate_distinction": (
                    "restatement_only 判断同一轮是否复述输入；duplicate 判断是否跨轮重复旧卡"
                ),
            },
            "derived_metric": {
                "grounded_incremental_move": (
                    "evidence_valid=1 且 incremental_value>=1 且 restatement_only=0 "
                    "且 actionable>=1 且 timing>=1"
                )
            },
            "field_scope": {
                "intervention_only": sorted(INTERVENTION_RATINGS),
                "all_variants": sorted(set(RATING_SPECS) - INTERVENTION_RATINGS),
            },
            "value_gate": VALUE_GATE_THRESHOLDS,
        },
        "pairs": pairs,
    }
    unblind = {
        "schema_version": SCHEMA_VERSION,
        "artifact": "private_unblind_mapping",
        "experiment_id": experiment_id,
        "seed": seed,
        "pair_count": len(mapping_pairs),
        "pairs": mapping_pairs,
    }
    return manifest, unblind, annotations


def _validate_rating(name: str, value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"annotation {name} must be an integer or null")
    spec = RATING_SPECS[name]
    if not spec["minimum"] <= value <= spec["maximum"]:
        raise ValueError(
            f"annotation {name} must be between {spec['minimum']} and {spec['maximum']}"
        )
    return value


def _validate_expected_action(value: Any) -> str | None:
    if value is None or value == "":
        return None
    action = str(value).strip()
    if action not in {"intervention", "silent"}:
        raise ValueError(
            "annotation expected_action must be intervention, silent, or null"
        )
    return action


def validate_annotations(
    annotations: Iterable[Mapping[str, Any]], mapping: Mapping[str, Any]
) -> list[dict[str, Any]]:
    mapping_pairs = mapping.get("pairs")
    if not isinstance(mapping_pairs, list):
        raise ValueError("unblind mapping is missing pairs")
    allowed = {
        (str(pair.get("blind_pair_id")), variant)
        for pair in mapping_pairs
        if isinstance(pair, Mapping)
        for variant in ("A", "B")
    }
    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in annotations:
        row = dict(raw)
        pair_id = str(row.get("blind_pair_id") or "")
        variant = str(row.get("variant") or "")
        labeler_id = str(row.get("labeler_id") or "").strip()
        if (pair_id, variant) not in allowed:
            raise ValueError(
                f"annotation references unknown pair/variant: {pair_id}/{variant}"
            )
        if not labeler_id:
            raise ValueError(f"annotation {pair_id}/{variant} is missing labeler_id")
        key = (pair_id, variant, labeler_id)
        if key in seen:
            raise ValueError(
                f"duplicate annotation row: {pair_id}/{variant}/{labeler_id}"
            )
        seen.add(key)
        normalized = {
            "blind_pair_id": pair_id,
            "variant": variant,
            "labeler_id": labeler_id,
            "expected_action": _validate_expected_action(row.get("expected_action")),
        }
        for name in RATING_SPECS:
            normalized[name] = _validate_rating(name, row.get(name))
        normalized["reason"] = str(row.get("reason") or "").strip() or None
        incremental_value = normalized["incremental_value"]
        restatement_only = normalized["restatement_only"]
        if (incremental_value is None) != (restatement_only is None):
            raise ValueError(
                f"annotation {pair_id}/{variant}/{labeler_id} must rate "
                "incremental_value and restatement_only together"
            )
        if restatement_only == 1 and incremental_value != 0:
            raise ValueError(
                f"annotation {pair_id}/{variant}/{labeler_id} with restatement_only=1 "
                "must set incremental_value=0"
            )
        if (
            incremental_value is not None
            and incremental_value >= 1
            and restatement_only != 0
        ):
            raise ValueError(
                f"annotation {pair_id}/{variant}/{labeler_id} with incremental_value>=1 "
                "must set restatement_only=0"
            )
        if restatement_only == 1 and normalized["reason"] is None:
            raise ValueError(
                f"annotation {pair_id}/{variant}/{labeler_id} with restatement_only=1 "
                "must explain the duplicated communicative function in reason"
            )
        validated.append(normalized)
    return validated


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _bootstrap_ci(
    values: Sequence[float], *, seed: int, samples: int = 1000
) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        means.append(
            sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        )
    means.sort()
    return [
        round(means[int(samples * 0.025)], 4),
        round(means[int(samples * 0.975) - 1], 4),
    ]


def _field_seed(field: str) -> int:
    """Use a stable seed; Python's hash() is randomized across processes."""

    return int.from_bytes(hashlib.sha256(field.encode("utf-8")).digest()[:4], "big")


def _success_rate(field: str, values: Sequence[int]) -> float | None:
    if not values:
        return None
    if field in ORDINAL_RATINGS:
        return _mean([float(value >= 1) for value in values])
    return _mean([float(value == 1) for value in values])


def score_blind_annotations(
    annotations: Iterable[Mapping[str, Any]],
    mapping: Mapping[str, Any],
    *,
    bootstrap_seed: int = 0,
    minimum_labelers: int = 2,
) -> dict[str, Any]:
    """Resolve blinded rows and report per-arm rates plus paired deltas."""

    if minimum_labelers < 1:
        raise ValueError("minimum_labelers must be at least 1")
    if mapping.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unblind mapping must use {SCHEMA_VERSION}; regenerate the blind bundle"
        )
    rows = validate_annotations(annotations, mapping)
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key[(row["blind_pair_id"], row["variant"])].append(row)
    runtime_by_key: dict[tuple[str, str], str] = {}
    action_by_key: dict[tuple[str, str], str] = {}
    pair_metadata: dict[str, Mapping[str, Any]] = {}
    for pair in mapping.get("pairs", []):
        pair_id = str(pair.get("blind_pair_id"))
        pair_metadata[pair_id] = pair
        for variant, runtime in (pair.get("variant_runtime") or {}).items():
            runtime_by_key[(pair_id, str(variant))] = str(runtime)
        for variant, action in (pair.get("variant_action") or {}).items():
            normalized_action = str(action)
            if normalized_action not in {"intervention", "silent"}:
                raise ValueError(f"invalid variant action for {pair_id}/{variant}")
            action_by_key[(pair_id, str(variant))] = normalized_action

    expected_keys = set(runtime_by_key)
    if set(action_by_key) != expected_keys:
        raise ValueError(
            "unblind mapping must contain variant_action for every A/B runtime"
        )
    missing_keys = expected_keys - set(by_key)
    if missing_keys:
        missing = ", ".join(
            f"{pair}/{variant}" for pair, variant in sorted(missing_keys)
        )
        raise ValueError(f"missing annotation rows: {missing}")
    insufficient = {
        key: {str(row.get("labeler_id")) for row in key_rows}
        for key, key_rows in by_key.items()
        if len({str(row.get("labeler_id")) for row in key_rows}) < minimum_labelers
    }
    if insufficient:
        details = ", ".join(
            f"{pair}/{variant} ({len(labelers)}/{minimum_labelers})"
            for (pair, variant), labelers in sorted(insufficient.items())
        )
        raise ValueError(
            f"each variant needs at least {minimum_labelers} independent labelers: {details}"
        )

    def consensus(rows_for_key: Sequence[Mapping[str, Any]], field: str) -> Any:
        values = [row[field] for row in rows_for_key if row.get(field) is not None]
        if not values:
            return None
        # A second labeler is expected in production.  Majority consensus is
        # deterministic; ties remain unresolved instead of inventing a label.
        counts = {value: values.count(value) for value in set(values)}
        max_count = max(counts.values())
        winners = [value for value, count in counts.items() if count == max_count]
        return winners[0] if len(winners) == 1 else None

    resolved: list[dict[str, Any]] = []
    for key, key_rows in sorted(by_key.items()):
        pair_id, variant = key
        runtime = runtime_by_key.get(key)
        if runtime is None:
            raise ValueError(f"missing runtime mapping for {pair_id}/{variant}")
        resolved.append(
            {
                "blind_pair_id": pair_id,
                "variant": variant,
                "runtime": runtime,
                "rendered": action_by_key[key] == "intervention",
                "coach_skill_id": str(
                    pair_metadata[pair_id].get("coach_skill_id") or "general"
                ),
                "difficulty": [
                    str(value)
                    for value in pair_metadata[pair_id].get("difficulty") or []
                ],
                "expected_action": consensus(key_rows, "expected_action"),
                **{name: consensus(key_rows, name) for name in RATING_SPECS},
            }
        )

    unresolved_value_ratings = [
        f"{row['blind_pair_id']}/{row['variant']}/{field}"
        for row in resolved
        if row["rendered"]
        for field in REQUIRED_VALUE_RATINGS
        if row.get(field) is None
    ]
    if unresolved_value_ratings:
        preview = ", ".join(unresolved_value_ratings[:8])
        suffix = "..." if len(unresolved_value_ratings) > 8 else ""
        raise ValueError(
            "rendered interventions require consensus for every core value rating; "
            f"add an independent adjudicator for: {preview}{suffix}"
        )

    for row in resolved:
        row["grounded_incremental_move"] = (
            int(
                row["evidence_valid"] == 1
                and row["incremental_value"] >= 1
                and row["restatement_only"] == 0
                and row["actionable"] >= 1
                and row["timing"] >= 1
            )
            if row["rendered"]
            else None
        )

    def arm_summary(
        runtime: str, source_rows: Sequence[Mapping[str, Any]] = resolved
    ) -> dict[str, Any]:
        arm = [row for row in source_rows if row["runtime"] == runtime]
        intervention = [row for row in arm if row["rendered"]]
        summary: dict[str, Any] = {
            "variant_count": len(arm),
            "rendered_intervention_count": len(intervention),
            "annotated_intervention_count": len(intervention),
            "core_value_annotation_coverage": (
                _mean(
                    [
                        float(row.get(field) is not None)
                        for row in intervention
                        for field in REQUIRED_VALUE_RATINGS
                    ]
                )
                if intervention
                else None
            ),
        }
        for field in RATING_SPECS:
            eligible = intervention if field in INTERVENTION_RATINGS else arm
            values = [int(row[field]) for row in eligible if row.get(field) is not None]
            summary[f"{field}_rate"] = _success_rate(field, values)
            summary[f"{field}_mean_score"] = _mean([float(value) for value in values])
            summary[f"{field}_count"] = len(values)
            summary[f"{field}_ci95"] = _bootstrap_ci(
                [
                    float(value >= 1) if field in ORDINAL_RATINGS else float(value == 1)
                    for value in values
                ],
                seed=bootstrap_seed + _field_seed(field),
            )
        timing_values = [
            row["timing"] for row in intervention if row.get("timing") is not None
        ]
        summary["too_late_rate"] = _mean([float(value == 0) for value in timing_values])
        grounded_values = [
            int(row["grounded_incremental_move"]) for row in intervention
        ]
        summary["grounded_incremental_move_rate"] = _mean(
            [float(value == 1) for value in grounded_values]
        )
        summary["grounded_incremental_move_count"] = len(grounded_values)
        summary["grounded_incremental_move_ci95"] = _bootstrap_ci(
            [float(value == 1) for value in grounded_values],
            seed=bootstrap_seed + _field_seed("grounded_incremental_move"),
        )
        incremental_values = [int(row["incremental_value"]) for row in intervention]
        summary["incremental_value_strong_rate"] = _mean(
            [float(value == 2) for value in incremental_values]
        )
        summary["incremental_value_strong_count"] = len(incremental_values)
        return summary

    def build_value_gate(pi_arm: Mapping[str, Any], *, scope: str) -> dict[str, Any]:
        minimum_count = int(VALUE_GATE_THRESHOLDS["minimum_pi_intervention_count"])
        incremental_value_rate = pi_arm["incremental_value_rate"]
        grounded_incremental_move_rate = pi_arm["grounded_incremental_move_rate"]
        restatement_only_rate = pi_arm["restatement_only_rate"]
        checks = {
            "minimum_pi_intervention_count": {
                "actual": pi_arm["rendered_intervention_count"],
                "threshold": minimum_count,
                "passed": pi_arm["rendered_intervention_count"] >= minimum_count,
            },
            "minimum_pi_incremental_value_rate": {
                "actual": incremental_value_rate,
                "threshold": VALUE_GATE_THRESHOLDS["minimum_pi_incremental_value_rate"],
                "passed": incremental_value_rate is not None
                and incremental_value_rate
                >= VALUE_GATE_THRESHOLDS["minimum_pi_incremental_value_rate"],
            },
            "minimum_pi_grounded_incremental_move_rate": {
                "actual": grounded_incremental_move_rate,
                "threshold": VALUE_GATE_THRESHOLDS[
                    "minimum_pi_grounded_incremental_move_rate"
                ],
                "passed": grounded_incremental_move_rate is not None
                and grounded_incremental_move_rate
                >= VALUE_GATE_THRESHOLDS["minimum_pi_grounded_incremental_move_rate"],
            },
            "maximum_pi_restatement_only_rate": {
                "actual": restatement_only_rate,
                "threshold": VALUE_GATE_THRESHOLDS["maximum_pi_restatement_only_rate"],
                "passed": restatement_only_rate is not None
                and restatement_only_rate
                <= VALUE_GATE_THRESHOLDS["maximum_pi_restatement_only_rate"],
            },
        }
        has_minimum_sample = checks["minimum_pi_intervention_count"]["passed"]
        return {
            "status": (
                "incomplete"
                if not has_minimum_sample
                else "passed"
                if all(check["passed"] for check in checks.values())
                else "failed"
            ),
            "scope": scope,
            "thresholds": VALUE_GATE_THRESHOLDS,
            "checks": checks,
            "release_gate": False,
            "release_gate_reason": (
                "This anti-restatement gate complements but does not replace engineering, "
                "per-skill, adoption, real-device, and provider-SLA gates."
            ),
        }

    arms = {runtime: arm_summary(runtime) for runtime in ("direct", "pi")}
    deltas: dict[str, Any] = {}
    pair_ids = sorted(pair_metadata)
    for field in RATING_SPECS:
        paired_values: list[float] = []
        for pair_id in pair_ids:
            direct = next(
                (
                    row
                    for row in resolved
                    if row["blind_pair_id"] == pair_id and row["runtime"] == "direct"
                ),
                None,
            )
            pi = next(
                (
                    row
                    for row in resolved
                    if row["blind_pair_id"] == pair_id and row["runtime"] == "pi"
                ),
                None,
            )
            if (
                direct is None
                or pi is None
                or direct.get(field) is None
                or pi.get(field) is None
            ):
                continue
            paired_values.append(float(pi[field]) - float(direct[field]))
        deltas[field] = {
            "paired_count": len(paired_values),
            "mean_pi_minus_direct": _mean(paired_values),
            "ci95": _bootstrap_ci(
                paired_values, seed=bootstrap_seed + 1000 + _field_seed(field)
            ),
        }
    grounded_paired_values: list[float] = []
    for pair_id in pair_ids:
        direct = next(
            (
                row
                for row in resolved
                if row["blind_pair_id"] == pair_id and row["runtime"] == "direct"
            ),
            None,
        )
        pi = next(
            (
                row
                for row in resolved
                if row["blind_pair_id"] == pair_id and row["runtime"] == "pi"
            ),
            None,
        )
        if (
            direct is None
            or pi is None
            or direct.get("grounded_incremental_move") is None
            or pi.get("grounded_incremental_move") is None
        ):
            continue
        grounded_paired_values.append(
            float(pi["grounded_incremental_move"] - direct["grounded_incremental_move"])
        )
    deltas["grounded_incremental_move"] = {
        "paired_count": len(grounded_paired_values),
        "mean_pi_minus_direct": _mean(grounded_paired_values),
        "ci95": _bootstrap_ci(
            grounded_paired_values,
            seed=bootstrap_seed + 1000 + _field_seed("grounded_incremental_move"),
        ),
    }

    value_gate = build_value_gate(
        arms["pi"], scope="human_rated_rendered_pi_interventions"
    )
    skill_summaries: dict[str, Any] = {}
    for skill_id in sorted({str(row["coach_skill_id"]) for row in resolved}):
        skill_rows = [row for row in resolved if row["coach_skill_id"] == skill_id]
        skill_arms = {
            runtime: arm_summary(runtime, skill_rows) for runtime in ("direct", "pi")
        }
        skill_summaries[skill_id] = {
            "pair_count": len({str(row["blind_pair_id"]) for row in skill_rows}),
            "arms": skill_arms,
            "value_gate": build_value_gate(
                skill_arms["pi"], scope=f"coach_skill:{skill_id}"
            ),
        }
    p0_slice_summaries: dict[str, Any] = {}
    for slice_id in P0_VALUE_SLICES:
        slice_rows = [row for row in resolved if slice_id in row["difficulty"]]
        slice_arms = {
            runtime: arm_summary(runtime, slice_rows) for runtime in ("direct", "pi")
        }
        p0_slice_summaries[slice_id] = {
            "pair_count": len({str(row["blind_pair_id"]) for row in slice_rows}),
            "arms": slice_arms,
            "value_gate": build_value_gate(
                slice_arms["pi"], scope=f"p0_value_slice:{slice_id}"
            ),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": "blind_annotation_score",
        "pair_count": len(pair_metadata),
        "annotation_row_count": len(rows),
        "arms": arms,
        "paired_deltas": deltas,
        "value_gate": value_gate,
        "skill_summaries": skill_summaries,
        "p0_value_slice_summaries": p0_slice_summaries,
        "resolved": resolved,
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{path}:{line_number} must be an object")
        rows.append(dict(value))
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def prepare_command(args: argparse.Namespace) -> int:
    report = _read_json(args.report)
    cases = load_dataset(args.dataset)
    direct_records, pi_records = _extract_results(report)
    manifest, unblind, annotations = build_blind_bundle(
        cases,
        direct_records,
        pi_records,
        seed=args.seed,
        experiment_id=args.experiment_id,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(args.output_dir / "manifest.json", manifest)
    _write_json(args.output_dir / "unblind.json", unblind)
    (args.output_dir / "annotations.template.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in annotations),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"output_dir": str(args.output_dir), "pair_count": len(annotations) // 2},
            ensure_ascii=False,
        )
    )
    return 0


def audit_command(args: argparse.Namespace) -> int:
    """Explain replay eligibility without constructing an annotator bundle."""

    report = _read_json(args.report)
    cases = load_dataset(args.dataset)
    direct_records, pi_records = _extract_results(report)
    audit = audit_paired_report(cases, direct_records, pi_records)
    _write_json(args.output, audit)
    print(json.dumps(audit, ensure_ascii=False))
    # An audit is a diagnostic artifact.  An ineligible report is an expected
    # quality result, so it must not be confused with a CLI crash.
    return 0


def score_command(args: argparse.Namespace) -> int:
    manifest = _read_json(args.manifest)
    unblind = _read_json(args.unblind)
    annotations = _read_jsonl(args.annotations)
    if manifest.get("schema_version") != unblind.get("schema_version"):
        raise ValueError("manifest and unblind schema versions differ")
    score = score_blind_annotations(
        annotations,
        unblind,
        bootstrap_seed=args.seed,
        minimum_labelers=args.minimum_labelers,
    )
    _write_json(args.output, score)
    print(
        json.dumps(
            {"output": str(args.output), "pair_count": score["pair_count"]},
            ensure_ascii=False,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare", help="create a public manifest and private runtime mapping"
    )
    prepare.add_argument("--dataset", type=Path, required=True)
    prepare.add_argument("--report", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--seed", type=int, default=0)
    prepare.add_argument("--experiment-id", default="realtime-coach-stage0")
    prepare.set_defaults(handler=prepare_command)
    audit = subparsers.add_parser(
        "audit", help="explain whether a replay can enter paired-blind evaluation"
    )
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--report", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.set_defaults(handler=audit_command)
    score = subparsers.add_parser("score", help="score completed annotation JSONL")
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--unblind", type=Path, required=True)
    score.add_argument("--annotations", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--seed", type=int, default=0)
    score.add_argument(
        "--minimum-labelers",
        type=int,
        default=2,
        help="minimum independent labelers required for every A/B variant (default: 2)",
    )
    score.set_defaults(handler=score_command)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
