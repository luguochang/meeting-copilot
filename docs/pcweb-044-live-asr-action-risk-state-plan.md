# PCWEB-044 Live ASR Action/Risk State Plan

> Date: 2026-07-01  
> Scope: PC Web MVP, local Live ASR state extraction skeleton only.

## Goal

Expand the local deterministic Live ASR state extraction skeleton from `DecisionCandidate` and `OpenQuestion` to all four PC-1 state lanes: `DecisionCandidate`, `ActionItem`, `Risk`, and `OpenQuestion`.

## Requirements

- Continue emitting `DecisionCandidate` when final/revision text contains `灰度`.
- Continue emitting `OpenQuestion` when final/revision text contains the existing Chinese question markers.
- Emit `ActionItem` when final/revision text has explicit assignment signals such as a Chinese person token plus action verbs like `负责` or `补充`.
- Emit `Risk` when final/revision text has conditional risk/rollback signals such as `如果` plus `超过` or `风险`.
- Every emitted state must:
  - include deterministic ids
  - include `evidence_span_ids`
  - include `source=live_asr_stream`
  - include `state_origin=local_deterministic_asr_skeleton`
  - be followed by a no-LLM `scheduler_event`
- The order for multiple states from one transcript event remains adjacent state/scheduler pairs.
- Frontend Live ASR sample must show action item and risk text in the state board.
- Draft review must include the new state candidates.

## Boundaries

- No LLM calls.
- No suggestion cards.
- No LLM schema results.
- No formal gated report.
- No semantic deduplication, closure, or confirmation.
- No claim that the deterministic rules are production semantic extraction.

## Initial Rules

This skeleton uses transparent, replaceable heuristics:

- Action item:
  - text contains an action marker such as `负责`、`补充`、`推进`、`跟进`、`处理` or `整理`
  - the local skeleton only treats it as an action when it has at least one assignment signal: a short owner before the action marker, a relative deadline, or assignment wording such as `由`、`请`、`让`、`麻烦`、`安排`
  - plain confirmation wording such as `我们先确认一下影响范围。` is not enough to create an `ActionItem`
  - facilitation wording such as `请大家先确认一下影响范围。` is not enough to create an `ActionItem`
  - noun forms such as `负责人` are not treated as the `负责` action marker for owner extraction
  - optional owner is a short Chinese 2-3 character name immediately before the deadline/action marker boundary
  - optional deadline is a relative date phrase such as `今天`、`明天`、`下周三`
- Risk:
  - text contains `如果` and `超过`
  - or text contains `风险`
  - explicit negation/resolution wording such as `没有风险`、`无风险`、`风险可控` or `风险已解除` is checked before threshold rules and does not create an open `Risk`
  - optional mitigation is kept empty unless the same sentence includes `回滚`

These rules are deliberately conservative and evidence-backed. They are a stepping stone toward the production state engine.
