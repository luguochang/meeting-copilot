"""Build the checked-in 48-case Stage 0C Commitment Firewall pilot.

The pilot deliberately reuses the reviewed commitment boundary cases from the
larger formal set, then adds harder missing-field and ordered lifecycle cases.
It contains exactly 24 intervention and 24 silence/lifecycle decisions.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.realtime_coach_eval.replay import (  # noqa: E402
    load_dataset,
    validate_ordered_lifecycle_cases,
)


DEFAULT_SOURCE = (
    REPO_ROOT / "tools/realtime_coach_eval/fixtures/stage0_formal_balanced_v1.jsonl"
)
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "tools/realtime_coach_eval/fixtures/stage0c_commitment_firewall_pilot_v1.jsonl"
)
PILOT_DEADLINE_MS = 2_500


def _paragraph(
    paragraph_id: str,
    text: str,
    *,
    start_ms: int,
    end_ms: int,
    source_track: str,
) -> dict[str, Any]:
    return {
        "id": paragraph_id,
        "text": text,
        "revision": 1,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "source_track": source_track,
        "role_hint": "remote_mix" if source_track == "system_audio" else "self_or_room",
    }


def _reviewed_boundary_cases(source: Path) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for source_case in load_dataset(source):
        difficulty = [str(value) for value in source_case.get("difficulty") or []]
        if "commitment_firewall" not in difficulty:
            continue
        case = deepcopy(source_case)
        action = str(case["expected"]["action"])
        case["expected"]["deadline_ms"] = PILOT_DEADLINE_MS
        case["difficulty"] = list(
            dict.fromkeys(
                [
                    *difficulty,
                    "stage0c_pilot",
                    "static_boundary",
                    "pilot_intervention" if action == "intervention" else "pilot_silence",
                ]
            )
        )
        selected.append(case)
    if len(selected) != 32:
        raise ValueError(
            "the reviewed source must contribute exactly 32 Commitment Firewall cases"
        )
    return selected


def _hard_positive_cases() -> list[dict[str, Any]]:
    specs = (
        (
            "owner",
            "general",
            "客户数据回填",
            "好，客户数据回填我们保证周五十八点全部完成。",
            "目前还没有明确由哪个团队和哪位负责人执行回填。",
            "补齐明确 owner 后再承诺客户数据回填结果。",
        ),
        (
            "acceptance",
            "decision",
            "账单切换",
            "账单切换我承诺明天下午直接发布，肯定没问题。",
            "双方还没有定义对账误差和失败订单的验收标准。",
            "补齐可检查的验收条件后再承诺账单切换结果。",
        ),
        (
            "rollback",
            "project",
            "搜索索引重建",
            "搜索索引重建本周六一定全量上线，不会延期。",
            "全量切换后的回滚阈值和恢复方案还没有确认。",
            "补齐回滚边界后再承诺搜索索引重建上线。",
        ),
        (
            "scope",
            "interview",
            "企业权限改造",
            "企业权限改造下周二保证一次性交付正式环境。",
            "本次交付是否包含历史角色迁移和海外租户仍未定。",
            "先限定范围，再承诺企业权限改造的交付日期。",
        ),
    )
    cases: list[dict[str, Any]] = []
    for index, (missing, skill, topic, utterance, context, goal) in enumerate(
        specs, start=1
    ):
        prefix = f"stage0c-cf-hard-pos-{index:02d}-{missing}"
        new_id = f"{prefix}-new-1"
        context_id = f"{prefix}-context-1"
        cases.append(
            {
                "case_id": prefix,
                "session_id": f"{prefix}-session",
                "coach_skill_id": skill,
                "state_revision": 1,
                "difficulty": [
                    "formal_stage0",
                    "stage0c_pilot",
                    "commitment_firewall",
                    "boundary_positive",
                    "pilot_intervention",
                    "stage0c_hard",
                    f"missing_{missing}",
                ],
                "new_paragraphs": [
                    _paragraph(
                        new_id,
                        utterance,
                        start_ms=10_000,
                        end_ms=13_000,
                        source_track="microphone",
                    )
                ],
                "context_paragraphs": [
                    _paragraph(
                        context_id,
                        context,
                        start_ms=6_000,
                        end_ms=9_000,
                        source_track="system_audio",
                    )
                ],
                "rolling_state": {
                    "topic": topic,
                    "open_items": [{"text": context, "status": "open"}],
                },
                "meeting_goal": goal,
                "expected": {
                    "action": "intervention",
                    "event_types": ["commitment_risk"],
                    "required_evidence_ids": [new_id],
                    "deadline_ms": PILOT_DEADLINE_MS,
                },
            }
        )
    return cases


def _lifecycle_cases() -> list[dict[str, Any]]:
    specs = (
        ("project", "支付灰度", "陈工", "周五十八点", "零个P0缺陷"),
        ("decision", "主库切换", "李工", "周六十点", "对账误差低于万分之一"),
        ("general", "报价方案", "王经理", "下周一中午", "法务和财务书面通过"),
        ("brainstorm", "智能质检试点", "赵工", "下周三十八点", "抽检准确率至少百分之九十五"),
    )
    cases: list[dict[str, Any]] = []
    for index, (skill, topic, owner, deadline, acceptance) in enumerate(specs, start=1):
        sequence_id = f"stage0c-cf-lifecycle-{index:02d}"
        session_id = f"{sequence_id}-session"
        open_case_id = f"{sequence_id}-open"
        intervention_case_id = f"{sequence_id}-intervention"
        resolving_case_id = f"{sequence_id}-resolving"
        open_id = f"{open_case_id}-new-1"
        intervention_id = f"{intervention_case_id}-new-1"
        resolving_id = f"{resolving_case_id}-new-1"
        common = {
            "session_id": session_id,
            "sequence_id": sequence_id,
            "coach_skill_id": skill,
            "meeting_goal": f"只在 {topic} 的 owner、期限、验收和回滚边界齐备后确认承诺。",
        }
        open_paragraph = _paragraph(
            open_id,
            f"我们先核对{topic}的依赖，具体交付条件还在讨论。",
            start_ms=1_000,
            end_ms=3_200,
            source_track="system_audio",
        )
        intervention_paragraph = _paragraph(
            intervention_id,
            f"不用再等条件了，{topic}我保证按期全部交付。",
            start_ms=4_000,
            end_ms=6_200,
            source_track="microphone",
        )
        resolving_paragraph = _paragraph(
            resolving_id,
            (
                f"补充一下，{topic}由{owner}负责，截止{deadline}；验收标准是{acceptance}，"
                "前置检查不通过就暂停并回滚。"
            ),
            start_ms=7_000,
            end_ms=11_000,
            source_track="microphone",
        )
        cases.extend(
            [
                {
                    **common,
                    "case_id": open_case_id,
                    "turn_index": 1,
                    "sequence_stage": "open",
                    "state_revision": 1,
                    "difficulty": [
                        "formal_stage0",
                        "stage0c_pilot",
                        "commitment_firewall",
                        "ordered_lifecycle",
                        "stage0c_hard",
                        "open",
                        "pilot_silence",
                        "should_stay_silent",
                    ],
                    "new_paragraphs": [open_paragraph],
                    "context_paragraphs": [],
                    "rolling_state": {
                        "topic": topic,
                        "open_items": [
                            {
                                "text": "补齐 owner、期限、验收和回滚边界",
                                "status": "open",
                            }
                        ],
                    },
                    "expected": {
                        "action": "silent",
                        "deadline_ms": PILOT_DEADLINE_MS,
                    },
                },
                {
                    **common,
                    "case_id": intervention_case_id,
                    "turn_index": 2,
                    "sequence_stage": "intervention",
                    "state_revision": 2,
                    "difficulty": [
                        "formal_stage0",
                        "stage0c_pilot",
                        "commitment_firewall",
                        "ordered_lifecycle",
                        "stage0c_hard",
                        "pilot_intervention",
                    ],
                    "new_paragraphs": [intervention_paragraph],
                    "context_paragraphs": [open_paragraph],
                    "rolling_state": {
                        "topic": topic,
                        "open_items": [
                            {
                                "text": "补齐 owner、期限、验收和回滚边界",
                                "status": "open",
                            }
                        ],
                    },
                    "expected": {
                        "action": "intervention",
                        "event_types": ["commitment_risk"],
                        "required_evidence_ids": [intervention_id],
                        "deadline_ms": PILOT_DEADLINE_MS,
                        "lifecycle_action": "retain",
                    },
                },
                {
                    **common,
                    "case_id": resolving_case_id,
                    "turn_index": 3,
                    "sequence_stage": "resolving_evidence",
                    "state_revision": 3,
                    "difficulty": [
                        "formal_stage0",
                        "stage0c_pilot",
                        "commitment_firewall",
                        "ordered_lifecycle",
                        "stage0c_hard",
                        "resolving_evidence",
                        "pilot_silence",
                        "should_stay_silent",
                    ],
                    "new_paragraphs": [resolving_paragraph],
                    "context_paragraphs": [intervention_paragraph],
                    "rolling_state": {
                        "topic": topic,
                        "summary": "owner、期限、验收标准和回滚条件已经明确。",
                        "open_items": [],
                    },
                    "expected": {
                        "action": "silent",
                        "deadline_ms": PILOT_DEADLINE_MS,
                        "lifecycle_action": "deprioritize",
                        "supersedes_case_id": intervention_case_id,
                    },
                },
            ]
        )
    return cases


def build_fixture(source: Path = DEFAULT_SOURCE) -> list[dict[str, Any]]:
    cases = [
        *_reviewed_boundary_cases(source),
        *_hard_positive_cases(),
        *_lifecycle_cases(),
    ]
    validate_ordered_lifecycle_cases(cases)
    actions = [str(case["expected"]["action"]) for case in cases]
    if len(cases) != 48 or actions.count("intervention") != 24 or actions.count("silent") != 24:
        raise ValueError("Stage 0C fixture must contain 24 intervention and 24 silence cases")
    if len({str(case["case_id"]) for case in cases}) != len(cases):
        raise ValueError("Stage 0C fixture case_id values must be unique")
    if any("commitment_firewall" not in (case.get("difficulty") or []) for case in cases):
        raise ValueError("every Stage 0C case must be a Commitment Firewall case")
    return cases


def encode_fixture(cases: list[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(dict(case), ensure_ascii=False, separators=(",", ":")) + "\n"
        for case in cases
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in output instead of rewriting it",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    encoded = encode_fixture(build_fixture(args.source))
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != encoded:
            print(f"fixture is stale: {args.output}", file=sys.stderr)
            return 2
        print(f"fixture is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(f"wrote 48 cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
