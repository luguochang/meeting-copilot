# FunASR Hotword Parameter Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a controlled, auditable FunASR hotword manifest path and use it in synthetic batch runtime tests to see whether speed and technical-term recall can improve without paid providers or microphone access.

**Architecture:** Hotwords live in a repo-tracked manifest under approved ASR evaluation roots. `transcribe_funasr.py` loads and validates that manifest, passes terms to FunASR `generate()`, and records only count/hash/status in provider JSON. Main-chain quality still depends on DRV-046/DRV-032; no threshold is relaxed.

**Tech Stack:** Python, pytest, FunASR `AutoModel.generate(**cfg)`, existing synthetic audio artifacts.

---

### Task 1: Hotword Manifest Contract

**Files:**
- Create: `data/asr_eval/glossaries/funasr-hotwords.zh.json`
- Modify: `code/asr_runtime/tests/test_transcribe_funasr.py`
- Modify: `code/asr_runtime/scripts/transcribe_funasr.py`

- [x] **Step 1: Write failing tests**

Add tests proving:

```text
- a repo-approved hotword manifest is loaded and passed to generate()
- provider raw records hotword_status/count/hash but not local paths
- forbidden paths are blocked before model construction
```

Run:

```bash
cd code/asr_runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_transcribe_funasr.py -q -p no:cacheprovider
```

Expected: FAIL before implementation because hotword manifest support is missing.

- [x] **Step 2: Implement manifest loading and generate kwargs**

Implement `load_hotword_manifest()` and pass both `hotword` and `hotwords` to FunASR runtime when enabled. Keep forbidden roots blocked: `configs/local`, `data/asr_eval/local_samples`, `data/asr_eval/samples`, `data/local_runtime`, `outputs`, `.m4a`, and paths outside the repo.

- [x] **Step 3: Run focused tests**

Expected: PASS.

### Task 2: Synthetic Runtime Self-Test

**Files:**
- Regenerate ignored artifacts under `artifacts/tmp/asr_reports/**`

- [x] **Step 1: Run batch `chunk_size=[0,10,5]` with hotwords**

Record RTF and technical recall. Do not read microphone or user audio.

- [x] **Step 2: Run batch `chunk_size=[0,20,10]` with hotwords**

Record speed/quality trade-off. Do not promote this path if recall regresses.

- [x] **Step 3: Rerun postprocess for any hotword provider outputs**

Use existing transcript report and single-result builder.

### Task 3: Documentation And Gate Result

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `docs/current-mainline-index.md`
- Modify: `docs/current-plan-and-validation-report-2026-07-04.md`
- Modify: `docs/requirements-traceability-matrix.md`

- [x] **Step 1: Record exact result**

Document whether hotwords improved recall, RTF, both, or neither.

- [ ] **Step 2: Verify no secret/local path leakage**

Run the repository sensitive scan and expect no output.

### Result

DEC-201 result: controlled FunASR hotword support is implemented and the main-chain self-test was executed with existing synthetic Chinese technical meeting audio. Hotwords did not materially improve the current quality exit.

| Candidate | RTF result | Normalized recall result | DRV-046/DRV-032 result |
| --- | ---: | ---: | --- |
| `chunk10_hotword` | `0.668-0.694` | engineering min `0.25`; `api=1.00`, `architecture=0.80`, `incident=0.25`, `release=0.50` | blocked |
| `chunk20_hotword` | `0.355-0.363` | engineering min `0.25`; `api=0.50`, `architecture=0.60`, `incident=0.25`, `release=0.50` | blocked |

Product-side replay using real FunASR events created suggestion-card previews for 3/4 engineering scenarios and correctly blocked the non-engineering control, but `incident-review-001` failed to create a candidate timeline because ASR output lost enough incident context. Mock control still passes 4/4 engineering scenarios plus negative control. Therefore the bottleneck remains ASR quality, not the product replay/card pipeline.
