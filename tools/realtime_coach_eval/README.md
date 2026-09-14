# Realtime coach A/B replay

This tool evaluates the direct structured-LLM coach and the optional Pi Agent runtime on source-aware transcript fixtures. It selects the configured `realtime_model`, matching the production realtime lane rather than the general-purpose model. The latest formal 200-case result is an engineering replay with an explicit No-Go gate; see `docs/pi-agent-stage0-execution-update-20260820.md` for current evidence and the human-evaluation boundary.

It does not capture or play audio. `system_audio` in a fixture represents an event already emitted by the loopback/ASR path. The report therefore covers transcript routing and the decision layer, not audio capture, ASR quality, persistence, UI projection, or a physical-microphone E2E.

Each case uses `trigger_type=delta` (the backwards-compatible default) or
`transcript_delta`, `task_due`, or `user_request`. Delta triggers require at
least one `new_paragraphs` item. `task_due` may have no new paragraphs but must
include `work_item_id` and persisted evidence in `context_paragraphs` or
`retrieval_paragraphs`; the production handler performs a local recheck when no
new evidence exists. `user_request` may have no new paragraphs and must include
the explicit `user_request` text. These fields are part of the replay input
fingerprint so different trigger contracts cannot share a cached identity.

## Run

Configure the same OpenAI-compatible gateway used by Talktrace:

```powershell
$env:LLM_GATEWAY_BASE_URL = "https://gateway.example"
$env:LLM_GATEWAY_API_KEY = "..."
$env:LLM_GATEWAY_MODEL = "general-model"
$env:LLM_GATEWAY_REALTIME_MODEL = "low-latency-model"
$env:LLM_GATEWAY_API_STYLE = "chat_completions"

cd code/web_mvp/backend
uv run --frozen python ../../../tools/realtime_coach_eval/replay.py ../../../tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl --runtime pi --output ../../../artifacts/realtime-coach-eval.json --enforce-gates
```

`--enforce-gates` still writes the report, then returns exit code `2` when any Pi acceptance check fails. That exit code is an expected quality-gate rejection, not a replay crash.

Use `--runtime both` for a paired decision-layer exploration. The comparison is intentionally not symmetric end to end: Pi first executes the production `should_run_realtime_coach` gate and receives production candidate events, while direct evaluates the decision layer. Do not label `both` as a production E2E A/B without a separate host-routing comparison and human usefulness review.

For Pi, install the pinned bridge dependencies once:

```powershell
cd code/agent_runtime/pi_coach_bridge
npm ci --ignore-scripts --no-audit --no-fund
```

## Production Contract

For Pi records, the replay applies the real production trigger before calling the Provider. A closed gate records `status=not_triggered`, does not call the Provider, does not enter the latency distribution, and is not a fallback. `protected_silent`, `timed_out`, `failed`, `not_triggered`, and `error` remain distinct. Each attempted failure also receives a stable `failure_class` (`timeout`, `rate_limit`, `provider_5xx`, `transport_error`, `validation_error`, `execution_error`, or `runtime_fallback`). A timeout or failure is a reliability error and never counts as a correct silence, even if the transport envelope is `protected_silent`; the report exposes this as `failed_open_silent_count`.

The report includes production trigger precision/recall, Agent-decision recall, end-to-end recall, intervention precision/recall, silent accuracy, required-evidence accuracy, deadline pass rate, P50/P95/max attempted-decision latency, status counts, Agent turns, fallback count, and reliability errors. A faux-provider smoke proves SDK wiring only; it is not product-quality evidence.

The latest authoritative Pi exploration report is `artifacts/tmp/pi-agent-ab-20260820/final-20-case/post-terminal-required/report.json`. It contains 20 cases (10 expected interventions and 10 expected silences): 10/10 intervention and 10/10 silence decisions are correct, with 0 false positives, 0 false negatives, 0 timeouts, 0 failed decisions, 0 fallbacks, and 0 reliability errors. Attempted-decision latency is P50 `3479.18 ms`, P95/max `5028.64 ms`; the Stage 0 evaluator fails only the P50 and P95 latency checks. Earlier repeated runs, including a timeout and a missing-terminal failure, remain preserved in `docs/pi-agent-stage0-repeated-replay-20260820.md`; they are reliability evidence, not discarded runs.

The checked-in Stage 0 gates are:

| Check | Threshold |
| --- | ---: |
| Intervention precision | `>= 0.70` |
| Intervention recall | `>= 0.80` |
| Silent accuracy | `>= 0.80` |
| Required-evidence accuracy | `= 1.00` |
| Production trigger recall | `>= 0.80` |
| Attempted-decision P50 | `<= 2,500 ms` |
| Attempted-decision P95 | `<= 5,000 ms` |
| Attempted-decision maximum | `<= 10,000 ms` |
| Reliability errors | `= 0` |
| Pi fallbacks | `= 0` |

Each case may set `coach_skill_id` to `general`, `decision`, `project`, `interview`, or `brainstorm`; the report includes overall and per-skill scores. The checked-in `private_coach_smoke.jsonl` and the authoritative report are only a 20-case exploration set (10 intervention, 10 silent), not formal acceptance. The per-skill sample is only `general=12` and 2 cases for each other skill. Formal acceptance still requires at least 20 intervention and 20 silence cases per skill, ordered multi-turn scenarios, repeated runs, direct/Pi paired blind review, and blinded human ratings for helpfulness, actionability, timing, willingness to use, and adoption rate.

## Paired blind A/B

`--runtime both` is an engineering replay and keeps the runtime names in its
report. Use `paired_blind.py` after a direct/Pi report is available to create a
separate annotator artifact. It makes no Provider calls.

Before preparing an annotator bundle from a real replay, run the non-mutating
eligibility audit. It collects missing-case and reliability reasons and returns
an explicit `incomplete`/`ineligible` result instead of letting timeouts enter
the human-value denominator:

```bash
cd code/web_mvp/backend
PYTHONPATH=../../.. uv run --frozen python ../../../tools/realtime_coach_eval/paired_blind.py audit \
  --dataset ../../../tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl \
  --report ../../../artifacts/realtime-coach-eval.json \
  --output ../../../artifacts/paired-blind/eligibility-audit.json
```

The `both` replay runs the two arms next to each other for every case and
alternates which arm goes first (`direct, pi`, then `pi, direct`). The report's
`paired_execution` section records that schedule and its first-arm counts. This
prevents a full Direct batch and a later full Pi batch from being compared
across different Provider load windows while preserving case order within each
runtime, including ordered lifecycle scenarios.

```bash
cd code/web_mvp/backend
PYTHONPATH=../../.. uv run --frozen python ../../../tools/realtime_coach_eval/paired_blind.py prepare \
  --dataset ../../../tools/realtime_coach_eval/fixtures/stage0_formal_balanced_v1.jsonl \
  --report ../../../artifacts/realtime-coach-eval.json \
  --output-dir ../../../artifacts/paired-blind/run-001 \
  --seed 20260827
```

The output directory contains:

- `manifest.json`: public stimulus and variants `A`/`B`; it contains no
  runtime names, expected labels, model/provider details, or internal error
  reasons. Give this file and `annotations.template.jsonl` to annotators.
- `unblind.json`: private case-to-runtime mapping and expected labels. Keep it
  with the experiment owner until annotation is locked.
- `annotations.template.jsonl`: one blank row per variant. Create one copy per
  labeler, fill `labeler_id`, `expected_action`, and the ratings, and merge the
  copies only after independent review.

After at least two independent labelers complete the rows, score the result:

```bash
cd code/web_mvp/backend
PYTHONPATH=../../.. uv run --frozen python ../../../tools/realtime_coach_eval/paired_blind.py score \
  --manifest ../../../artifacts/paired-blind/run-001/manifest.json \
  --unblind ../../../artifacts/paired-blind/run-001/unblind.json \
  --annotations ../../../artifacts/paired-blind/run-001/annotations.completed.jsonl \
  --output ../../../artifacts/paired-blind/run-001/score.json \
  --seed 20260827
```

The generated bundle uses schema `talktrace.realtime_coach_paired_blind.v2`.
For every rendered card, annotators see the same product fields needed to judge
the experience: `title`, `say_this`, `why_now`, timing, and quote-level
evidence. Bundle preparation fails if an intervention record is missing
`say_this`/`recommendation` or `why_now`/`reason`. Regenerate older replay
reports instead of filling the missing product field by hand.
Preparation also fails closed when either arm timed out, failed, returned an
error, used a fallback runtime, omitted/conflicted on terminal status, or used
an unknown status. Reliability failures must be resolved by a fresh replay;
they are never converted into an annotator-visible `silent` variant.

The score reports per-arm rates and paired `Pi - direct` deltas for
`helpful`, `actionable`, `timing`, `incremental_value`, `restatement_only`,
`evidence_valid`, `willing_to_use`, and `adopted`, plus `too_late_rate` and
deterministic bootstrap 95% intervals. It also reports overall, per-skill, and
P0 value slices for `question_radar`, `commitment_firewall`, and
`goal_guardian`, so one strong skill cannot hide another skill's repeated
restatements.

Use these two fields to distinguish a valid event from a useful card:

- `incremental_value=0`: the recommendation copies, paraphrases, or re-asks
  the source, or gives a generic slogan without changing the user's next
  communication action.
- `incremental_value=1`: the recommendation makes a real but limited action
  conversion that still needs rewriting.
- `incremental_value=2`: the recommendation adds a grounded response stance,
  boundary or condition, missing decision dimension, closure action, or
  conflict resolution.
- `restatement_only=1`: replacing `say_this` with the latest source utterance
  would lose no action or decision guidance. Judge the communicative function,
  target, and fields already requested, not character or keyword similarity.

For example, re-asking `谁跟进，几点给结果？` as `具体谁负责，几点反馈？`
is a restatement. Turning `负责人还没定` into an immediate assignment/closure
question is an incremental action. Asking which step and impact are involved
after a vague problem statement adds a missing dimension. A grounded response
such as “目前只确认日志超时；负责人和反馈时间我核实后回复” adds a response
stance and next action instead of re-asking the other party's question.

`restatement_only=1` requires `incremental_value=0` and a written reason;
`incremental_value>=1` requires `restatement_only=0`. `duplicate` remains a
separate cross-turn old-card metric. The derived metric
`grounded_incremental_move` requires valid evidence, nonzero incremental value,
no restatement, actionable `>=1`, and timing `>=1`.

Ordinal ratings use `0/1/2`; success rates count `>=1` for ordinal fields and
`1` for binary fields. Two independent labelers are mandatory. Any tie or
missing core value rating on a rendered intervention fails closed and requires
an independent adjudicator; disputed rows cannot silently disappear from the
denominator. Silent variants do not require intervention-only ratings. A
one-case smoke is supported, but confidence intervals require two paired cases
and the value gate remains `incomplete` below the minimum sample.

The locked anti-restatement thresholds are:

| Check | Threshold |
| --- | ---: |
| Human-rated Pi interventions | `>= 20` |
| Pi incremental-value rate (`>=1`) | `>= 0.70` |
| Pi grounded-incremental-move rate | `>= 0.60` |
| Pi restatement-only rate | `<= 0.05` |

These thresholds are evaluated overall and on each available skill/P0 slice.
They are not a standalone release gate: engineering correctness, real-device
capture, Provider P95/P99, per-skill sample sizes, and the existing adoption or
difficult-subset Pi-vs-direct increment must all pass as well.

The tool rejects duplicate/missing case IDs, changed input fingerprints,
different deadlines, incomplete product cards, malformed or internally
inconsistent ratings, unresolved core-rating ties, legacy v1 scoring bundles,
and annotation rows that do not map to the private bundle. The public manifest
is safe to hand to a blind reviewer; the private mapping must never be
published alongside it before labels are locked.
