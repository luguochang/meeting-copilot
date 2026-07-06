# Public Audio and Synthetic Meeting ASR Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the next validation slice for Meeting Copilot: public licensed audio and synthetic Chinese technical meetings drive local ASR/event simulation before the user performs final real microphone meeting validation.

**Architecture:** Keep validation data in three layers: public licensed audio for ASR/runtime behavior, synthetic technical meeting scripts for product value and gap detection, and user real microphone meetings only for final acceptance. The implementation adds safe planning/reporting tools first, then bounded sample extraction and synthetic script/audio generation, then local ASR event generation, without reading private audio or enabling paid remote ASR.

**Tech Stack:** Python 3.11, pytest, JSON manifests, existing `code/asr_runtime` and `code/asr_bakeoff` provider contracts, ignored local artifact roots under `data/asr_eval/public_raw/` and `artifacts/tmp/`.

---

## Product Value Guardrails

This plan must not drift into a pure ASR benchmark. The output is useful only if it can prove or disprove the realtime Copilot thesis:

```text
audio
  -> ASR partial/final/revision
  -> EvidenceSpan
  -> meeting state
  -> gap candidate
  -> suggestion card candidate
  -> feedback-ready card timeline
```

Required product metrics:

- `transcript_only_baseline`: same script evaluated without state/gap/card logic.
- `summary_only_baseline`: same script evaluated as a post-meeting summary only.
- `copilot_detection_window`: whether the gap was detected while it was still useful to ask.
- `would_have_asked`: whether a human host would plausibly have asked the card's suggested question.
- `changed_or_would_have_changed_meeting_behavior`: whether the card changed or should have changed the meeting.
- `card_latency_ms`: time from related final/revision segment to card candidate.
- `too_late`, `too_intrusive`, `wrong`, `dismissed`, `useful`: feedback labels.

Real-time rule:

- A realtime suggestion must appear within 10-30 seconds after the related final/revision segment.
- Anything after that window can only count as a post-meeting confirmation item.

Entity quality rule:

- `>=80%` technical entity recall is only the minimum threshold to enter a first real-microphone pilot.
- `>=90%` precision/recall remains the MVP/product goal before claiming the ASR layer is production-useful.

## Scope Boundary

This plan is the complete execution plan for the transcription validation stage. It does not replace the desktop runtime plan; it feeds M4/M5 in `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`.

Allowed inputs:

- Public source metadata from `data/asr_eval/public_sources.json`.
- Public licensed datasets after a separate bounded extraction plan approves exact source, target root, size cap and cleanup behavior.
- Synthetic Chinese technical meeting scripts committed as text/JSON.
- Synthetic audio generated into ignored artifact roots.

Forbidden inputs and side effects:

- Do not read `configs/local/`.
- Do not read `data/asr_eval/local_samples/`.
- Do not read real user audio.
- Do not call remote ASR.
- Do not call LLM during ASR validation.
- Do not commit raw public audio, generated audio, private audio, model cache, local runtime chunks or absolute local paths.

Web-checked source facts on 2026-07-02:

- OpenSLR SLR111 / AISHELL-4: CC BY-SA 4.0, Mandarin multi-channel meeting corpus, test package is 5.2G, full corpus is large; use only through bounded sample extraction.
- OpenSLR SLR33 / AISHELL-1: Apache License v2.0, Mandarin read speech, `data_aishell.tgz` is 15G and only useful for smoke/model sanity, not meeting value.
- MagicHub ASR-CCMeetingSC: 202MB web meeting corpus and attractive for small-scale meeting smoke, but requires sign-in and has more restrictive licensing; keep as a manual candidate, not default automatic whitelist.
- OpenSLR SLR119 / AliMeeting remains an approved known candidate in the existing whitelist, but any first download must re-check the official page and record exact artifact URL/size before extraction.

## File Structure

- Modify: `tests/test_public_audio_source_whitelist.py`
  - Responsibility: avoid private-looking audio names in test source while preserving no-leak assertions.
- Create: `tests/test_public_audio_sample_extraction_plan.py`
  - Responsibility: TDD coverage for bounded public sample extraction plan behavior.
- Create: `tools/public_audio_sample_extraction_plan.py`
  - Responsibility: produce an explicit no-download sample extraction plan for whitelisted public audio sources.
- Create: `data/asr_eval/public_sample_plan.example.json`
  - Responsibility: committed example request with no private path and no enabled download.
- Create: `tests/test_synthetic_meeting_scripts.py`
  - Responsibility: validate synthetic Chinese technical meeting scripts, entities, expected gaps and non-engineering zero-card control.
- Create: `data/asr_eval/synthetic_meetings/scripts/*.json`
  - Responsibility: committed text-only meeting scripts and expected product-value annotations.
- Create: `tools/synthetic_meeting_script_report.py`
  - Responsibility: summarize script coverage and gate readiness before audio generation.
- Create: `tests/test_synthetic_audio_generation_plan.py`
  - Responsibility: validate synthetic audio generation plan is local-only and writes to ignored roots.
- Create: `tools/synthetic_audio_generation_plan.py`
  - Responsibility: create a no-audio-generation plan describing how local TTS/audio mixing will be executed later.
- Modify: `docs/asr-evaluation-dataset.md`
  - Responsibility: record public/manual/rejected source tiers and the synthetic meeting strategy.
- Modify: `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`
  - Responsibility: link this execution plan and lock the user-real-mic-final-validation boundary.
- Modify: `docs/decision-log.md`
  - Responsibility: record the explicit decision that I use public/synthetic audio first; the user performs final real microphone meeting validation.

## Task 1: Remove Private-Looking Test Literal

**Files:**

- Modify: `tests/test_public_audio_source_whitelist.py`

- [ ] **Step 1: Write the failing intent check**

Confirm the test source no longer contains any private-looking audio title:

```bash
rg 'private_audio_title_marker' tests/test_public_audio_source_whitelist.py
```

Expected:

```text
no matches
```

- [ ] **Step 2: Keep the no-leak assertion generic**

Use:

```python
assert "private_audio_marker" not in report_json
```

- [ ] **Step 3: Run focused test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py -q -p no:cacheprovider
```

Expected:

```text
3 passed
```

## Task 2: Public Sample Extraction Plan Tool

**Files:**

- Create: `tests/test_public_audio_sample_extraction_plan.py`
- Create: `tools/public_audio_sample_extraction_plan.py`
- Create: `data/asr_eval/public_sample_plan.example.json`

- [ ] **Step 1: Write failing tests**

Create `tests/test_public_audio_sample_extraction_plan.py` with tests covering:

```python
def test_sample_plan_defaults_to_no_download_and_whitelisted_source_only():
    report = tool.build_public_sample_extraction_plan(
        source_id="aishell4_openslr_slr111",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
    )
    assert report["plan_status"] == "ready_for_manual_download_review"
    assert report["plan_version"] == "public_audio_sample_extraction_plan.v1"
    assert report["safe_to_download_now"] is False
    assert report["download_status"] == "not_started"
    assert report["source_id"] == "aishell4_openslr_slr111"
    assert report["source_license"] == "CC BY-SA 4.0"
    assert report["source_snapshot_date"] == "2026-07-02"
    assert report["source_split"] == "test"
    assert report["target_root"] == "artifacts/tmp/public_audio"
    assert report["max_duration_seconds"] == 180
    assert report["max_download_bytes"] == 600_000_000
    assert report["checksum_algorithm"] == "sha256"
    assert report["derived_artifact_policy"] == "do_not_commit_raw_or_large_audio"
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
```

Also cover:

```python
def test_sample_plan_rejects_unapproved_source_id():
    report = tool.build_public_sample_extraction_plan(
        source_id="random_video_site",
        target_root="artifacts/tmp/public_audio",
        max_duration_seconds=180,
        max_download_bytes=600_000_000,
    )
    assert report["plan_status"] == "blocked"
    assert "source_id is not approved" in report["validation_errors"]
```

And:

```python
def test_sample_plan_rejects_forbidden_target_roots():
    for target_root in ["configs/local", "data/asr_eval/local_samples", "outputs"]:
        report = tool.build_public_sample_extraction_plan(
            source_id="aishell4_openslr_slr111",
            target_root=target_root,
            max_duration_seconds=180,
            max_download_bytes=600_000_000,
        )
        assert report["plan_status"] == "blocked"
        assert report["safe_to_download_now"] is False
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_sample_extraction_plan.py -q -p no:cacheprovider
```

Expected:

```text
FAIL because tools/public_audio_sample_extraction_plan.py does not exist
```

- [ ] **Step 3: Implement minimal tool**

Create `tools/public_audio_sample_extraction_plan.py` with:

```python
APPROVED_SOURCE_IDS = {
    "aishell4_openslr_slr111",
    "alimeeting_openslr_slr119",
    "aishell1_openslr_slr33",
}

ALLOWED_TARGET_ROOTS = {
    "data/asr_eval/public_raw",
    "artifacts/tmp/public_audio",
}

FORBIDDEN_TARGET_ROOTS = {
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
}
```

Return a JSON-serializable report with:

```python
{
    "plan_mode": "public_audio_sample_extraction_plan_only",
    "plan_id": "public-audio-sample-extraction-2026-07-02",
    "plan_version": "public_audio_sample_extraction_plan.v1",
    "plan_status": "ready_for_manual_download_review" or "blocked",
    "review_status": "requires_manual_review",
    "source_snapshot_date": "2026-07-02",
    "source_split": source_split,
    "selection_criteria": selection_criteria,
    "sample_budget_count": sample_budget_count,
    "sample_budget_minutes": sample_budget_minutes,
    "download_status": "not_started",
    "download_mode": "manual_review_only",
    "download_command": None,
    "extract_command": None,
    "transcode_command": None,
    "safe_to_download_now": False,
    "safe_to_extract_now": False,
    "safe_to_read_user_audio": False,
    "safe_to_read_configs_local": False,
    "safe_to_call_remote_asr": False,
    "safe_to_call_llm": False,
    "safe_to_commit_raw_audio": False,
    "source_id": source_id,
    "source_url": source_url,
    "source_license": source_license,
    "target_root": target_root,
    "allowed_roots": sorted(ALLOWED_TARGET_ROOTS),
    "forbidden_roots": sorted(FORBIDDEN_TARGET_ROOTS),
    "max_duration_seconds": max_duration_seconds,
    "max_download_bytes": max_download_bytes,
    "max_clip_seconds": max_clip_seconds,
    "max_total_bytes": max_download_bytes,
    "derived_artifact_policy": "do_not_commit_raw_or_large_audio",
    "attribution_policy": "retain source id, URL, license and citation in reports",
    "checksum_algorithm": "sha256",
    "cleanup_policy": "delete artifacts under target_root after validation or keep ignored local only",
    "retention_policy": "reports only in repo; audio artifacts ignored",
    "abort_thresholds": {
        "download_bytes_exceeds_max": True,
        "target_root_not_allowed": True,
        "source_not_whitelisted": True
    },
    "validation_errors": errors,
    "next_action": "manual_source_artifact_review" or "fix_validation_errors",
}
```

- [ ] **Step 4: Add committed example**

Create `data/asr_eval/public_sample_plan.example.json`:

```json
{
  "source_id": "aishell4_openslr_slr111",
  "source_snapshot_date": "2026-07-02",
  "source_split": "test",
  "selection_criteria": "first manually reviewed meeting clips that fit the byte and duration budget",
  "sample_budget_count": 3,
  "sample_budget_minutes": 9,
  "target_root": "artifacts/tmp/public_audio",
  "max_duration_seconds": 180,
  "max_download_bytes": 600000000,
  "download_status": "not_started",
  "safe_to_download_now": false
}
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py -q -p no:cacheprovider
```

Expected:

```text
all tests pass
```

## Task 3: Synthetic Chinese Technical Meeting Script Gate

**Files:**

- Create: `tests/test_synthetic_meeting_scripts.py`
- Create: `tools/synthetic_meeting_script_report.py`
- Create: `data/asr_eval/synthetic_meetings/scripts/api-review.json`
- Create: `data/asr_eval/synthetic_meetings/scripts/release-review.json`
- Create: `data/asr_eval/synthetic_meetings/scripts/incident-review.json`
- Create: `data/asr_eval/synthetic_meetings/scripts/architecture-review.json`
- Create: `data/asr_eval/synthetic_meetings/scripts/non-engineering-control.json`

- [ ] **Step 1: Write failing tests**

Require the five scripts and validate each contains:

```python
REQUIRED_KEYS = {
    "script_id",
    "scenario",
    "language",
    "turns",
    "technical_entities",
    "expected_state_events",
    "expected_gap_candidates",
    "expected_suggestion_cards",
    "baseline_expectations",
    "expected_engineering_card_count_min",
    "expected_engineering_card_count_max",
}
```

Gate behavior:

```python
assert scenarios == {
    "api_review",
    "release_review",
    "incident_review",
    "architecture_review",
    "non_engineering_control",
}
assert non_engineering["expected_engineering_card_count_max"] == 0
assert all(script["language"] == "zh-CN" for script in scripts)
assert any("P99" in script["technical_entities"] for script in scripts)
assert any("rollback" in script["expected_gap_candidates"] for script in scripts)
for script in scripts:
    for card in script["expected_suggestion_cards"]:
        assert card["gap_type"] in {
            "owner",
            "deadline",
            "rollback",
            "test_verification",
            "metric_monitoring",
        }
        assert card["evidence_span_required"] is True
        assert card["trigger_window_seconds"]["max"] <= 30
        assert "suggested_question" in card
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_meeting_scripts.py -q -p no:cacheprovider
```

Expected:

```text
FAIL because synthetic_meetings scripts and report tool do not exist
```

- [ ] **Step 3: Create scripts**

Each script uses text-only turns:

```json
{
  "script_id": "api-review-001",
  "scenario": "api_review",
  "language": "zh-CN",
  "turns": [
    {"speaker": "A", "text": "我们先看 payment-gateway 的创建订单接口，字段 request_id 要兼容旧客户端。"},
    {"speaker": "B", "text": "错误码先沿用 40012，但是灰度期间需要看 P99 延迟。"}
  ],
  "technical_entities": ["payment-gateway", "request_id", "40012", "P99"],
  "expected_state_events": [
    {"event_type": "topic.created", "target_type": "Topic"},
    {"event_type": "risk.created", "target_type": "Risk"}
  ],
  "expected_gap_candidates": ["rollback", "test_verification", "metric_monitoring"],
  "expected_suggestion_cards": [
    {
      "card_id": "api-review-001-rollback-gap",
      "gap_type": "rollback",
      "suggested_question": "是否需要补一句这个接口灰度失败时的回滚条件和负责人？",
      "evidence_span_required": true,
      "trigger_window_seconds": {"min": 0, "max": 30},
      "should_show": true
    }
  ],
  "baseline_expectations": {
    "transcript_only_detects_gap": false,
    "summary_only_detects_within_window": false,
    "copilot_should_detect_within_window": true
  },
  "expected_engineering_card_count_min": 1,
  "expected_engineering_card_count_max": 3
}
```

The non-engineering control uses no engineering entities and:

```json
{
  "expected_gap_candidates": [],
  "expected_suggestion_cards": [],
  "baseline_expectations": {
    "transcript_only_detects_gap": false,
    "summary_only_detects_within_window": false,
    "copilot_should_detect_within_window": false
  },
  "expected_engineering_card_count_min": 0,
  "expected_engineering_card_count_max": 0
}
```

- [ ] **Step 4: Implement script report**

Create `tools/synthetic_meeting_script_report.py` returning:

```python
{
    "report_mode": "synthetic_meeting_script_coverage",
    "script_count": 5,
    "coverage_status": "passed",
    "scenarios": sorted(scenarios),
    "required_product_annotations": [
        "expected_state_events",
        "expected_gap_candidates",
        "expected_suggestion_cards",
        "baseline_expectations",
    ],
    "safe_to_generate_audio_now": False,
    "safe_to_read_user_audio": False,
    "safe_to_call_remote_asr": False,
    "safe_to_call_llm": False,
    "next_action": "create_synthetic_audio_generation_plan",
}
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_meeting_scripts.py -q -p no:cacheprovider
```

Expected:

```text
all tests pass
```

## Task 4: Synthetic Audio Generation Plan

**Files:**

- Create: `tests/test_synthetic_audio_generation_plan.py`
- Create: `tools/synthetic_audio_generation_plan.py`

- [ ] **Step 1: Write failing tests**

Cover:

```python
def test_synthetic_audio_plan_is_local_only_and_no_generation_by_default():
    report = tool.build_synthetic_audio_generation_plan(
        script_id="api-review-001",
        tts_engine="macos_say",
        target_root="artifacts/tmp/synthetic_audio",
        max_duration_seconds=240,
    )
    assert report["plan_status"] == "ready_for_manual_generation_review"
    assert report["safe_to_generate_audio_now"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_call_remote_tts"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["target_root"] == "artifacts/tmp/synthetic_audio"
```

And reject:

```python
assert tool.build_synthetic_audio_generation_plan(
    script_id="api-review-001",
    tts_engine="remote_tts",
    target_root="artifacts/tmp/synthetic_audio",
    max_duration_seconds=240,
)["plan_status"] == "blocked"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_audio_generation_plan.py -q -p no:cacheprovider
```

Expected:

```text
FAIL because tools/synthetic_audio_generation_plan.py does not exist
```

- [ ] **Step 3: Implement no-generation plan**

Allowed engines for the plan stage:

```python
ALLOWED_LOCAL_TTS_ENGINES = {"macos_say", "offline_tts_placeholder"}
ALLOWED_TARGET_ROOTS = {"artifacts/tmp/synthetic_audio", "data/asr_eval/public_raw"}
```

Return:

```python
{
    "plan_mode": "synthetic_audio_generation_plan_only",
    "plan_status": "ready_for_manual_generation_review" or "blocked",
    "generation_status": "not_started",
    "safe_to_generate_audio_now": False,
    "safe_to_read_user_audio": False,
    "safe_to_call_remote_tts": False,
    "safe_to_call_remote_asr": False,
    "safe_to_call_llm": False,
    "safe_to_commit_generated_audio": False,
    "script_id": script_id,
    "tts_engine": tts_engine,
    "target_root": target_root,
    "max_duration_seconds": max_duration_seconds,
    "validation_errors": errors,
    "next_action": "manual_local_tts_smoke" or "fix_validation_errors",
}
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_audio_generation_plan.py tests/test_synthetic_meeting_scripts.py -q -p no:cacheprovider
```

Expected:

```text
all tests pass
```

## Task 5: ASR Event Generation Gate

**Files:**

- Create: `tests/test_asr_event_generation_from_public_or_synthetic_audio.py`
- Create: `tools/asr_event_generation_plan.py`
- Later modify, only after Task 2-4 pass: existing `code/asr_runtime` scripts if the current command-provider adapter cannot emit the required metadata.

- [ ] **Step 1: Write failing tests for event plan only**

Require:

```python
assert report["input_layer"] in {"public_audio_sample", "synthetic_audio"}
assert report["event_contract"] == "partial_final_revision_error_eos"
assert report["safe_to_run_asr_now"] is False
assert report["safe_to_call_remote_asr"] is False
assert report["safe_to_call_llm"] is False
assert report["metrics_required"] == [
    "duration_seconds",
    "rtf",
    "first_partial_latency_ms",
    "final_latency_p95_ms",
    "segment_count",
    "raw_cer",
    "normalized_cer",
    "raw_technical_entity_recall",
    "technical_entity_recall",
    "technical_entity_precision",
    "cpu_peak_percent",
    "memory_peak_mb",
]
```

- [ ] **Step 2: Implement plan report**

Create `tools/asr_event_generation_plan.py` as no-ASR execution plan, with source ID, audio path kind, provider candidate and output event path under `artifacts/tmp/`.

- [ ] **Step 3: Execute ASR only after local audio artifact exists**

Run local-only providers:

```bash
cd code/asr_runtime
PYTHONDONTWRITEBYTECODE=1 python3 scripts/transcribe_funasr.py --help
PYTHONDONTWRITEBYTECODE=1 python3 scripts/transcribe_sherpa_onnx.py --help
```

Expected:

```text
commands print help without reading private audio or calling remote ASR
```

Then use only approved public/synthetic audio paths under ignored roots.

## Task 6: Documentation Sync and Stage Report

**Files:**

- Modify: `docs/asr-evaluation-dataset.md`
- Modify: `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/project-stage-status-and-next-work-2026-07-02.md`

- [ ] **Step 1: Record source and simulation decision**

Add a decision log entry:

```text
DEC-093: Public/synthetic audio first, user real microphone final validation later
```

It must state:

```text
Public/synthetic audio is for ASR and product-value preflight.
Real microphone meetings are final validation and are initiated by the user.
No remote ASR or private audio is enabled by this decision.
```

- [ ] **Step 2: Add traceability IDs**

Add RTM rows:

```text
DRV-003 public sample extraction plan
DRV-004 synthetic meeting script gate
DRV-005 synthetic audio generation plan
DRV-006 ASR event generation gate
```

- [ ] **Step 3: Run docs/source focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py -q -p no:cacheprovider
```

Expected:

```text
tests pass
```

## Go / No-Go for Real Microphone Validation

Do not ask the user to run a real microphone meeting until all are true:

- Mac desktop shell has a real window and IPC path.
- Local microphone capture can start/pause/resume/stop manually.
- Public or synthetic audio has produced ASR event JSON with `partial/final/revision`.
- Synthetic technical meeting scripts produce expected gap candidates.
- Non-engineering control produces zero engineering card candidates.
- ASR technical entity recall reaches at least 80% on synthetic scripts or has a clear provider/normalizer path to 80%.
- The 80% threshold is only a first-pilot threshold; the product target remains 90% precision/recall on core technical entities.
- Synthetic scripts include expected state events, expected gap candidates and expected suggestion cards.
- Copilot beats transcript-only and summary-only baselines on detecting at least one gap inside the useful question window.
- P95 card latency target remains 30 seconds or less after ASR + candidate generation + controlled LLM preview.
- Equivalent card frequency remains 3-8 cards per 60 minutes.

Stop or downgrade the realtime Copilot route if:

- ASR cannot preserve key technical entities after normalizer/hotword work.
- The system mostly produces transcript/summary value and not meeting intervention value.
- Candidate cards are mostly late, wrong or too intrusive.
- Any stage requires hidden paid ASR to be usable.
