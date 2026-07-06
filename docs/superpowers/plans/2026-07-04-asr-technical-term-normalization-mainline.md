# ASR Technical Term Normalization Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve deterministic recovery of Chinese technical-meeting ASR terms using existing FunASR synthetic smoke artifacts, then rerun postprocess and quality gates without expanding into new boundary tests.

**Architecture:** Keep the fix in the transcript normalization layer, because DEC-199 showed the runner, event bridge, and DRV-046/032 intake already work. The normalizer may canonicalize observed ASR near-misses, but must not hallucinate entities from `<unk>` or completely absent text.

**Tech Stack:** Python, pytest, existing `code/asr_runtime/scripts/transcript_normalizer.py`, `data/asr_eval/glossaries/technical-terms.zh.json`, DRV-046/DRV-032 CLI tools.

---

### Task 1: Recover Observed Technical Term Near-Misses

**Files:**
- Modify: `code/asr_runtime/tests/test_transcript_normalizer.py`
- Modify: `code/asr_runtime/scripts/transcript_normalizer.py`
- Modify: `data/asr_eval/glossaries/technical-terms.zh.json`

- [x] **Step 1: Write failing tests**

Add tests for observed FunASR near-misses:

```python
def test_committed_technical_glossary_recovers_funasr_observed_near_misses_without_guessing_unseen_terms():
    result = normalize_transcript_text(
        "paymentgateway 字段 request 错误码 40012 需要看 p九九。"
        "新的 dationservice 依赖 featurestore 和 REDIScost。"
        "凌晨 autoker 消费堆积 lag 最高到了八万。"
        "check koutservice 灰度先看 errorate pp九。",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "payment-gateway" in result.text
    assert "request_id" in result.text
    assert "P99" in result.text
    assert "recommendation-service" in result.text
    assert "feature-store" in result.text
    assert "redis" in result.text
    assert "order-worker" in result.text
    assert "checkout-service" in result.text
    assert "error_rate" in result.text
    assert "timeout" not in result.text
    assert "监控阈值" not in result.text
```

Run:

```bash
cd code/asr_runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_transcript_normalizer.py::test_committed_technical_glossary_recovers_funasr_observed_near_misses_without_guessing_unseen_terms -q -p no:cacheprovider
```

Expected: FAIL because current aliases do not recover those near-misses.

- [x] **Step 2: Implement minimal deterministic aliases and context rules**

Add glossary aliases for observed contiguous/garbled but still attributable strings:

```json
paymentgateway -> payment-gateway
featurestore -> feature-store
dationservice -> recommendation-service
errorate -> error_rate
check koutservice -> checkout-service
autoker -> order-worker
```

Add context-sensitive code rules only where the surrounding words prove intent:

```text
字段 request -> 字段 request_id
p九九 / pp九 / pP99 -> P99
REDIScost -> redis
```

Do not add rules that create `timeout` or `监控阈值` from missing text.

- [x] **Step 3: Run focused tests**

```bash
cd code/asr_runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_transcript_normalizer.py tests/test_transcript_report.py -q -p no:cacheprovider
```

Expected: PASS.

### Task 2: Rerun Existing Postprocess Chain Only

**Files:**
- Regenerate ignored artifacts under `artifacts/tmp/asr_reports/**`

- [x] **Step 1: Rerun transcript reports for existing provider JSON**

Use the existing DRV-045 packet postprocess commands or equivalent direct transcript-report commands. Do not rerun FunASR provider commands.

- [x] **Step 2: Rerun single-result builder for five scenarios**

Regenerate:

```text
artifacts/tmp/asr_reports/*.funasr.smoke-report.json
```

- [x] **Step 3: Rerun DRV-046 and DRV-032**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/funasr_synthetic_smoke_batch_evidence_assembler.py \
  --execution-packet-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json \
  > artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly.json

PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py \
  --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json \
  --funasr-smoke-assembly-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly.json \
  > artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision.json
```

Expected: technical recall improves; RTF may remain blocked.

### Task 3: Document Result And Keep Mainline Honest

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/current-plan-and-validation-report-2026-07-04.md`
- Modify: `docs/requirements-traceability-matrix.md`

- [x] **Step 1: Record the exact postprocess rerun result**

Record whether recall reached DRV-044 threshold. If RTF still blocks, state that ASR quality remains `not_exited`.

- [x] **Step 2: Verify no secret/local path leakage**

```bash
rg -n "<project-sensitive-token-regex>|<gateway-host>|<real-audio-name>|<local-model-cache-path>" \
  README.md docs/*.md docs/superpowers/plans/*.md tools/*.py tests/*.py \
  code/web_mvp/backend/**/*.py code/web_mvp/e2e/*.mjs \
  code/asr_runtime/scripts/*.py code/asr_runtime/tests/*.py || true
```

Expected: no output.

## Execution Result

- Deterministic normalizer improved main-chain recall to `1.00 / 0.80 / 0.50 / 0.75` for the four engineering scenarios.
- DRV-046/DRV-032 still block ASR quality because `incident-review-001` and `release-review-001` remain below recall threshold and original per-file RTF remains above threshold.
- Added `transcribe_streaming_batch()` to measure long-running/batch runtime without counting model load per file.
- Batch `chunk_size=[0,10,5]` reduced transcribe-only RTF to `0.679`, still above threshold.
- Batch `chunk_size=[0,20,10]` reduced transcribe-only RTF to `0.358`, but recall regressed, so it is not a quality replacement.
