#!/usr/bin/env python3
"""Run the approved real-microphone mainline self-test through local ASR and Web handoff."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import real_mic_shadow_test_report_schema  # noqa: E402


REPORT_MODE = "real_mic_full_chain_runner"
APPROVED_AUDIO_CHUNK_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
APPROVED_ASR_EVENTS_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUN_ROOT = "artifacts/tmp/real_mic_shadow_tests"
APPROVED_REPORT_ROOT = "artifacts/tmp/real_mic_shadow_reports"
DEFAULT_PROVIDER = "real_mic_local_sherpa_onnx"
DEFAULT_RECORD_SECONDS = 45
DEFAULT_AUDIO_DEVICE_INDEX = 0
DEFAULT_CHUNK_MS = 500
DEFAULT_NUM_THREADS = 2
DEFAULT_PROMPT_TEXT = (
    "我们现在做真实麦克风 shadow test。recommendation-service 本周四灰度百分之五，"
    "rollback checklist 由张三负责。如果 P99 超过九百毫秒，先降级 feature flag，"
    "再回滚 payment-gateway。Redis cluster 缓存穿透需要李四确认监控 owner，"
    "Kafka lag 超过三分钟要通知值班群。这个方案还有没有风险？"
)
TECHNICAL_TERMS = [
    "recommendation-service",
    "rollback",
    "P99",
    "feature flag",
    "payment-gateway",
    "Redis",
    "cluster",
    "Kafka",
    "lag",
    "owner",
]
DEFAULT_SHERPA_MODEL_DIR = (
    REPO_ROOT
    / "code"
    / "asr_runtime"
    / "models"
    / "sherpa-onnx"
    / "sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01"
)
DEFAULT_SHERPA_PYTHON = REPO_ROOT / "code" / "asr_runtime" / ".venv-sherpa" / "bin" / "python"
SHERPA_SCRIPT = REPO_ROOT / "code" / "asr_runtime" / "scripts" / "transcribe_sherpa_onnx.py"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


Recorder = Callable[..., dict[str, Any]]
Transcriber = Callable[..., dict[str, Any]]
WebHandoff = Callable[..., dict[str, Any]]


def run_real_mic_full_chain(
    *,
    session_id: str,
    record_seconds: int = DEFAULT_RECORD_SECONDS,
    repo_root: Path = REPO_ROOT,
    recorder: Recorder | None = None,
    transcriber: Transcriber | None = None,
    web_handoff: WebHandoff | None = None,
    audio_device_index: int = DEFAULT_AUDIO_DEVICE_INDEX,
    provider: str = DEFAULT_PROVIDER,
    reference_text: str = DEFAULT_PROMPT_TEXT,
    chunk_ms: int = DEFAULT_CHUNK_MS,
    num_threads: int = DEFAULT_NUM_THREADS,
    model_dir: Path | None = None,
) -> dict[str, Any]:
    validation_errors = _session_id_errors(session_id)
    if validation_errors:
        return {
            "report_mode": REPORT_MODE,
            "runner_status": "blocked_by_session_id_validation",
            "session_id": "<redacted_invalid_session_id>",
            "validation_errors": validation_errors,
            "privacy_cost_flags": _privacy_cost_flags(),
        }

    active_recorder = recorder or record_microphone_to_wav
    active_transcriber = transcriber or transcribe_with_sherpa
    active_web_handoff = web_handoff or ingest_events_into_web
    resolved_model_dir = model_dir or DEFAULT_SHERPA_MODEL_DIR

    paths = _artifact_paths(repo_root=repo_root, session_id=session_id)
    _ensure_artifact_dirs(paths)

    capture = active_recorder(
        audio_path=paths["audio_path"],
        record_seconds=record_seconds,
        audio_device_index=audio_device_index,
    )
    capture = {
        **capture,
        "audio_path": _display_path(paths["audio_path"], repo_root),
    }

    asr_started = time.monotonic()
    asr_result = active_transcriber(
        audio_path=paths["audio_path"],
        events_output=paths["events_path"],
        model_dir=resolved_model_dir,
        chunk_ms=chunk_ms,
        num_threads=num_threads,
    )
    asr_elapsed_ms = int((time.monotonic() - asr_started) * 1000)
    streaming_events = _load_streaming_events(paths["events_path"])
    web_streaming_events = _write_web_handoff_events(
        raw_events=streaming_events,
        web_events_path=paths["web_events_path"],
    )
    event_counts = _streaming_event_counts(streaming_events)
    asr_summary = _asr_summary(
        asr_result=asr_result,
        events_path=paths["events_path"],
        web_events_path=paths["web_events_path"],
        repo_root=repo_root,
        elapsed_ms=asr_elapsed_ms,
        event_counts=event_counts,
    )

    web_result = active_web_handoff(
        session_id=session_id,
        provider=provider,
        events_path=paths["web_events_path"],
        data_dir=paths["web_data_dir"],
        repo_root=repo_root,
    )
    _write_text(paths["draft_markdown_path"], str(web_result.get("draft_markdown", "")))

    candidate_report = build_candidate_report(
        session_id=session_id,
        capture=capture,
        asr_result=asr_result,
        streaming_events=web_streaming_events,
        web_result=web_result,
        reference_text=reference_text,
    )
    _write_json(paths["candidate_report_path"], candidate_report)
    schema_report = real_mic_shadow_test_report_schema.build_real_mic_shadow_test_report_schema(
        candidate_report=candidate_report,
    )

    runner_status = _runner_status(
        capture=capture,
        asr_result=asr_result,
        event_counts=event_counts,
        web_result=web_result,
        schema_report=schema_report,
    )
    summary = {
        "report_mode": REPORT_MODE,
        "runner_status": runner_status,
        "session_id": session_id,
        "provider": provider,
        "execution_boundary": "real_mic_local_asr_web_handoff_no_remote_asr_no_llm",
        "reference_text_status": "prompted_short_technical_meeting_script",
        "capture": capture,
        "asr": asr_summary,
        "web_handoff": _web_summary(web_result),
        "report": {
            "candidate_report_path": _display_path(paths["candidate_report_path"], repo_root),
            "candidate_report_validation_status": schema_report.get(
                "candidate_report_validation_status"
            ),
            "candidate_report_validation_errors": schema_report.get(
                "candidate_report_validation_errors",
                [],
            ),
            "candidate_report_summary": schema_report.get("candidate_report_summary"),
        },
        "artifacts": {
            "summary_path": str(paths["summary_path"]),
            "candidate_report_path": str(paths["candidate_report_path"]),
            "draft_markdown_path": str(paths["draft_markdown_path"]),
            "audio_path": str(paths["audio_path"]),
            "events_path": str(paths["events_path"]),
        },
        "privacy_cost_flags": _privacy_cost_flags(),
        "known_limitations": [
            "real microphone capture is recorded to an ignored local artifact root for review",
            "ASR is local CPU inference; no remote ASR or LLM is called in this runner",
            "streaming ASR is file-replayed over captured microphone audio, not live low-latency microphone streaming",
            "single-speaker scripted self-test does not prove multi-speaker meeting quality",
        ],
    }
    _write_json(paths["summary_path"], summary)
    return summary


def record_microphone_to_wav(
    *,
    audio_path: Path,
    record_seconds: int,
    audio_device_index: int,
) -> dict[str, Any]:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    print(
        "\n请准备朗读测试语料。3 秒后开始录音；如 macOS 弹出麦克风权限，请点允许。\n",
        file=sys.stderr,
        flush=True,
    )
    for remaining in (3, 2, 1):
        print(f"{remaining}...", file=sys.stderr, flush=True)
        time.sleep(1)
    started = time.monotonic()
    command = _ffmpeg_record_command(
        audio_path=audio_path,
        record_seconds=record_seconds,
        audio_device_index=audio_device_index,
    )
    timeout_seconds = record_seconds + 10
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "capture_status": "blocked_by_microphone_capture_timeout",
            "record_seconds": record_seconds,
            "elapsed_ms": elapsed_ms,
            "timeout_seconds": timeout_seconds,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": audio_path.stat().st_size if audio_path.exists() else 0,
            "error_summary": "ffmpeg avfoundation capture timed out before producing a complete WAV",
        }
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        return {
            "capture_status": "blocked_by_microphone_capture_error",
            "record_seconds": record_seconds,
            "elapsed_ms": elapsed_ms,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": audio_path.stat().st_size if audio_path.exists() else 0,
            "ffmpeg_returncode": completed.returncode,
            "error_summary": _summarize_process_error(completed.stderr),
        }
    return {
        "capture_status": "recorded_from_real_microphone",
        "record_seconds": record_seconds,
        "elapsed_ms": elapsed_ms,
        "audio_device_index": audio_device_index,
        "audio_file_size_bytes": audio_path.stat().st_size if audio_path.exists() else 0,
    }


def transcribe_with_sherpa(
    *,
    audio_path: Path,
    events_output: Path,
    model_dir: Path,
    chunk_ms: int,
    num_threads: int,
) -> dict[str, Any]:
    command = [
        str(DEFAULT_SHERPA_PYTHON),
        str(SHERPA_SCRIPT),
        str(audio_path),
        "--model-dir",
        str(model_dir),
        "--chunk-ms",
        str(chunk_ms),
        "--num-threads",
        str(num_threads),
        "--events-output",
        str(events_output),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        events_output.parent.mkdir(parents=True, exist_ok=True)
        events_output.write_text(
            json.dumps(
                [
                    {
                        "event_type": "error",
                        "segment_id": "sherpa_process_error",
                        "text": _summarize_process_error(completed.stderr),
                        "start_ms": 0,
                        "end_ms": 0,
                        "received_at_ms": 0,
                    },
                    {
                        "event_type": "end_of_stream",
                        "segment_id": "sherpa_eos",
                        "text": "",
                        "start_ms": 0,
                        "end_ms": 0,
                        "received_at_ms": 0,
                    },
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "status": "blocked_by_local_sherpa_process",
            "text": "",
            "latency_ms": 0,
            "audio_duration_seconds": 0.0,
            "rtf": 0.0,
            "segments": [],
            "raw": {"error_summary": _summarize_process_error(completed.stderr)},
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "status": "blocked_by_invalid_sherpa_output",
            "text": "",
            "latency_ms": 0,
            "audio_duration_seconds": 0.0,
            "rtf": 0.0,
            "segments": [],
            "raw": {"stdout_prefix": completed.stdout[:200]},
        }
    return payload if isinstance(payload, dict) else {"status": "blocked_by_invalid_sherpa_output"}


def ingest_events_into_web(
    *,
    session_id: str,
    provider: str,
    events_path: Path,
    data_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    app_module = _load_web_app_module()
    original_repo_root = app_module.REPO_ROOT
    try:
        app_module.REPO_ROOT = repo_root
        client = TestClient(app_module.create_app(data_dir=data_dir))
        create_response = client.post(
            "/live/asr/local-event-files/sessions",
            json={
                "session_id": session_id,
                "provider": provider,
                "events_path": _display_path(events_path, repo_root),
            },
        )
        result: dict[str, Any] = {
            "handoff_status": "web_live_asr_ingested"
            if create_response.status_code == 201
            else "blocked_by_web_handoff_response",
            "status_code": create_response.status_code,
            "create_response": create_response.json(),
        }
        if create_response.status_code == 201:
            result["draft"] = client.get(f"/live/asr/sessions/{session_id}/draft").json()
            result["draft_markdown"] = client.get(
                f"/live/asr/sessions/{session_id}/draft.md"
            ).text
            result["suggestion_candidates"] = client.get(
                f"/live/asr/sessions/{session_id}/suggestion-candidates"
            ).json()
        return result
    finally:
        app_module.REPO_ROOT = original_repo_root


def build_candidate_report(
    *,
    session_id: str,
    capture: dict[str, Any],
    asr_result: dict[str, Any],
    streaming_events: list[dict[str, Any]],
    web_result: dict[str, Any],
    reference_text: str,
) -> dict[str, Any]:
    live_events = _live_events(web_result)
    segments = _report_segments(asr_result, streaming_events)
    candidate_cards = _candidate_cards(live_events)
    evidence_spans = _evidence_spans(live_events, candidate_cards, segments)
    if segments and not candidate_cards:
        candidate_cards = [_fallback_candidate_card(segments, evidence_spans)]
    if evidence_spans and candidate_cards:
        first_candidate_id = candidate_cards[0]["candidate_id"]
        for evidence in evidence_spans:
            evidence["supports_candidate_id"] = evidence.get("supports_candidate_id") or first_candidate_id
        for card in candidate_cards:
            if not card["evidence_ids"]:
                card["evidence_ids"] = [evidence["evidence_id"] for evidence in evidence_spans]
    states = _state_timeline(live_events, evidence_spans)
    metrics = _asr_metrics(
        asr_result=asr_result,
        streaming_events=streaming_events,
        reference_text=reference_text,
    )
    return {
        "schema_version": real_mic_shadow_test_report_schema.SCHEMA_VERSION,
        "session_id": session_id,
        "meeting_profile": {
            "meeting_type": "chinese_technical_review",
            "duration_minutes": round(metrics["duration_seconds"] / 60, 3),
            "participant_count": 1,
            "language": "zh-CN",
            "domain_tags": ["real-mic-self-test", "software-engineering", "asr"],
        },
        "transcript": {
            "segment_count": len(segments),
            "segments": segments,
        },
        "asr_metrics": metrics,
        "evidence_span_timeline": evidence_spans,
        "state_timeline": states,
        "candidate_card_timeline": candidate_cards,
        "feedback_summary": _empty_feedback_summary(),
        "final_decision": {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": (
                "Real microphone main flow was exercised, but this scripted single-speaker "
                "self-test is not enough for product Go evidence."
            ),
        },
        "privacy_cost_flags": _privacy_cost_flags(),
        "audio_retention": {
            "audio_chunk_root": real_mic_shadow_test_report_schema.APPROVED_PRE_PILOT_AUDIO_CHUNK_ROOT,
            "audio_chunk_write_status": "written_by_user_approved_shadow_test"
            if capture.get("capture_status") == "recorded_from_real_microphone"
            else "not_written",
            "audio_delete_status": "retained_in_ignored_artifact_root_for_user_review"
            if capture.get("capture_status") == "recorded_from_real_microphone"
            else "not_applicable_no_audio_written",
            "retention_policy": "delete_audio_chunks_before_session_discard",
        },
        "known_limitations": [
            "single speaker scripted self-test cannot validate multi-speaker diarization",
            "local ASR streaming events are replayed from the captured microphone WAV file",
            "remote LLM suggestion quality is not measured in this no-cost local runner",
        ],
    }


def _ffmpeg_record_command(
    *,
    audio_path: Path,
    record_seconds: int,
    audio_device_index: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "avfoundation",
        "-i",
        f":{audio_device_index}",
        "-t",
        str(record_seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(audio_path),
    ]


def _artifact_paths(*, repo_root: Path, session_id: str) -> dict[str, Path]:
    return {
        "audio_path": repo_root / APPROVED_AUDIO_CHUNK_ROOT / session_id / "audio.wav",
        "events_path": repo_root / APPROVED_ASR_EVENTS_ROOT / f"{session_id}.sherpa.events.json",
        "web_events_path": repo_root / APPROVED_ASR_EVENTS_ROOT / f"{session_id}.web.events.json",
        "run_root": repo_root / APPROVED_RUN_ROOT / session_id,
        "summary_path": repo_root / APPROVED_RUN_ROOT / session_id / "full_chain_summary.json",
        "draft_markdown_path": repo_root / APPROVED_RUN_ROOT / session_id / "live_asr_draft.md",
        "web_data_dir": repo_root / APPROVED_RUN_ROOT / session_id / "web_data",
        "candidate_report_path": repo_root / APPROVED_REPORT_ROOT / f"{session_id}.json",
    }


def _ensure_artifact_dirs(paths: dict[str, Path]) -> None:
    for key in (
        "audio_path",
        "events_path",
        "web_events_path",
        "summary_path",
        "draft_markdown_path",
        "candidate_report_path",
    ):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    paths["web_data_dir"].mkdir(parents=True, exist_ok=True)


def _load_web_app_module():
    for path in (WEB_BACKEND_ROOT, CORE_ROOT):
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)
    from meeting_copilot_web_mvp import app as app_module

    return app_module


def _session_id_errors(session_id: str) -> list[str]:
    if not session_id or not SESSION_ID_PATTERN.fullmatch(session_id):
        return ["session_id must contain only letters, numbers, underscore, or hyphen"]
    return []


def _display_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(repo_root.resolve(strict=False)).as_posix()
    except ValueError:
        return "<redacted_invalid_path>"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_streaming_events(events_path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(events_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _write_web_handoff_events(
    *,
    raw_events: list[dict[str, Any]],
    web_events_path: Path,
) -> list[dict[str, Any]]:
    web_events: list[dict[str, Any]] = []
    for event in raw_events:
        copied = dict(event)
        end_ms = copied.get("end_ms")
        received_at_ms = copied.get("received_at_ms")
        if isinstance(end_ms, (int, float)) and isinstance(received_at_ms, (int, float)):
            copied["received_at_ms"] = int(max(received_at_ms, end_ms))
        web_events.append(copied)
    web_events_path.parent.mkdir(parents=True, exist_ok=True)
    web_events_path.write_text(
        json.dumps(web_events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return web_events


def _streaming_event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "partial": 0,
        "final": 0,
        "revision": 0,
        "error": 0,
        "end_of_stream": 0,
    }
    for event in events:
        event_type = str(event.get("event_type", ""))
        if event_type in counts:
            counts[event_type] += 1
    return counts


def _asr_summary(
    *,
    asr_result: dict[str, Any],
    events_path: Path,
    web_events_path: Path,
    repo_root: Path,
    elapsed_ms: int,
    event_counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "status": asr_result.get("status"),
        "provider": asr_result.get("raw", {}).get("provider", "sherpa-onnx")
        if isinstance(asr_result.get("raw"), dict)
        else "sherpa-onnx",
        "text": asr_result.get("text", ""),
        "latency_ms": asr_result.get("latency_ms", elapsed_ms),
        "runner_elapsed_ms": elapsed_ms,
        "audio_duration_seconds": asr_result.get("audio_duration_seconds", 0.0),
        "rtf": asr_result.get("rtf", 0.0),
        "events_path": _display_path(events_path, repo_root),
        "web_events_path": _display_path(web_events_path, repo_root),
        "event_counts": event_counts,
    }


def _web_summary(web_result: dict[str, Any]) -> dict[str, Any]:
    create_response = web_result.get("create_response")
    if not isinstance(create_response, dict):
        create_response = {}
    return {
        "handoff_status": web_result.get("handoff_status"),
        "status_code": web_result.get("status_code"),
        "live_event_counts": create_response.get("live_event_counts", {}),
        "candidate_count": web_result.get("suggestion_candidates", {}).get("candidate_count")
        if isinstance(web_result.get("suggestion_candidates"), dict)
        else None,
    }


def _runner_status(
    *,
    capture: dict[str, Any],
    asr_result: dict[str, Any],
    event_counts: dict[str, int],
    web_result: dict[str, Any],
    schema_report: dict[str, Any],
) -> str:
    if capture.get("capture_status") != "recorded_from_real_microphone":
        return "blocked_by_microphone_capture"
    if asr_result.get("status") != "ok" or event_counts.get("final", 0) <= 0:
        return "blocked_by_local_asr"
    if web_result.get("handoff_status") != "web_live_asr_ingested":
        return "blocked_by_web_handoff"
    if schema_report.get("candidate_report_validation_status") != "passed":
        return "blocked_by_report_schema_validation"
    return "main_flow_passed"


def _live_events(web_result: dict[str, Any]) -> list[dict[str, Any]]:
    create_response = web_result.get("create_response")
    if not isinstance(create_response, dict):
        return []
    events = create_response.get("live_events")
    return events if isinstance(events, list) else []


def _report_segments(
    asr_result: dict[str, Any],
    streaming_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    segments = asr_result.get("segments")
    if isinstance(segments, list) and segments:
        return [
            {
                "segment_id": str(segment.get("id") or segment.get("segment_id") or f"seg-{index:03d}"),
                "speaker_label": "speaker_1",
                "start_ms": int(segment.get("start_ms", 0) or 0),
                "end_ms": int(segment.get("end_ms", 0) or 0),
                "text": str(segment.get("text", "")),
                "source_event_id": str(segment.get("id") or segment.get("segment_id") or f"seg-{index:03d}"),
            }
            for index, segment in enumerate(segments, start=1)
            if isinstance(segment, dict) and str(segment.get("text", "")).strip()
        ]
    finals = [event for event in streaming_events if event.get("event_type") in {"final", "revision"}]
    return [
        {
            "segment_id": str(event.get("segment_id") or f"seg-{index:03d}"),
            "speaker_label": "speaker_1",
            "start_ms": int(event.get("start_ms", 0) or 0),
            "end_ms": int(event.get("end_ms", 0) or 0),
            "text": str(event.get("text", "")),
            "source_event_id": str(event.get("segment_id") or f"seg-{index:03d}"),
        }
        for index, event in enumerate(finals, start=1)
        if str(event.get("text", "")).strip()
    ]


def _candidate_cards(live_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for index, event in enumerate(
        [item for item in live_events if item.get("event_type") == "suggestion_candidate_event"],
        start=1,
    ):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        candidate_id = str(
            payload.get("candidate_id")
            or payload.get("target_candidate_id")
            or f"cand-{index:03d}"
        )
        text = str(
            payload.get("candidate_text")
            or payload.get("suggested_prompt")
            or payload.get("trigger_reason")
            or "Review this ASR-derived meeting gap."
        )
        cards.append(
            {
                "candidate_id": candidate_id,
                "card_type": str(payload.get("candidate_type") or "state_gap_review"),
                "created_at_ms": int(payload.get("created_at_ms") or event.get("at_ms") or 0),
                "latency_ms": int(payload.get("candidate_latency_ms") or 0),
                "evidence_ids": [str(item) for item in payload.get("evidence_span_ids") or []],
                "text": text,
            }
        )
    return cards


def _evidence_spans(
    live_events: list[dict[str, Any]],
    candidate_cards: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_by_evidence = {
        evidence_id: card["candidate_id"]
        for card in candidate_cards
        for evidence_id in card.get("evidence_ids", [])
    }
    spans: list[dict[str, Any]] = []
    for event in live_events:
        if event.get("event_type") not in {"transcript_final", "transcript_revision"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for evidence in payload.get("evidence_spans") or []:
            if not isinstance(evidence, dict):
                continue
            evidence_id = str(evidence.get("id") or f"ev-{len(spans) + 1:03d}")
            spans.append(
                {
                    "evidence_id": evidence_id,
                    "segment_id": str(evidence.get("segment_id") or payload.get("segment_id") or ""),
                    "start_ms": int(evidence.get("start_ms", payload.get("start_ms", 0)) or 0),
                    "end_ms": int(evidence.get("end_ms", payload.get("end_ms", 0)) or 0),
                    "text": str(evidence.get("quote") or payload.get("text") or ""),
                    "supports_candidate_id": candidate_by_evidence.get(evidence_id, ""),
                }
            )
    if spans:
        return spans
    return [
        {
            "evidence_id": f"ev-{index:03d}",
            "segment_id": segment["segment_id"],
            "start_ms": int(segment["start_ms"]),
            "end_ms": int(segment["end_ms"]),
            "text": str(segment["text"]),
            "supports_candidate_id": candidate_cards[0]["candidate_id"] if candidate_cards else "",
        }
        for index, segment in enumerate(segments, start=1)
    ]


def _fallback_candidate_card(
    segments: list[dict[str, Any]],
    evidence_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    max_end_ms = max((int(segment["end_ms"]) for segment in segments), default=0)
    return {
        "candidate_id": "real_mic_transcript_review_candidate",
        "card_type": "transcript_review",
        "created_at_ms": max_end_ms,
        "latency_ms": 0,
        "evidence_ids": [evidence["evidence_id"] for evidence in evidence_spans],
        "text": "Review ASR transcript quality and extract meeting risks manually.",
    }


def _state_timeline(
    live_events: list[dict[str, Any]],
    evidence_spans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_ids = [evidence["evidence_id"] for evidence in evidence_spans]
    first_evidence_id = evidence_ids[0] if evidence_ids else ""
    states = []
    for index, event in enumerate(
        [item for item in live_events if item.get("event_type") == "state_event"],
        start=1,
    ):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        payload_evidence_ids = payload.get("evidence_span_ids") or []
        evidence_id = str(payload_evidence_ids[0]) if payload_evidence_ids else first_evidence_id
        if not evidence_id:
            continue
        states.append(
            {
                "state_id": str(payload.get("event_id") or f"state-{index:03d}"),
                "state_type": str(payload.get("target_type") or "unknown").lower(),
                "at_ms": int(event.get("at_ms") or 0),
                "evidence_id": evidence_id,
            }
        )
    return states


def _asr_metrics(
    *,
    asr_result: dict[str, Any],
    streaming_events: list[dict[str, Any]],
    reference_text: str,
) -> dict[str, Any]:
    hypothesis = str(asr_result.get("text", ""))
    duration_seconds = float(asr_result.get("audio_duration_seconds") or _events_duration_seconds(streaming_events))
    partial_latencies = [
        int(event.get("received_at_ms", 0) or 0)
        for event in streaming_events
        if event.get("event_type") == "partial"
    ]
    final_latencies = [
        int(event.get("received_at_ms", 0) or 0)
        for event in streaming_events
        if event.get("event_type") in {"final", "revision"}
    ]
    raw_cer = _cer(reference_text, hypothesis) if reference_text else 0.0
    normalized_cer = _cer(_normalize_text(reference_text), _normalize_text(hypothesis)) if reference_text else 0.0
    recall, precision = _technical_term_metrics(reference_text, hypothesis)
    counts = _streaming_event_counts(streaming_events)
    return {
        "duration_seconds": round(duration_seconds, 6),
        "first_partial_latency_ms": min(partial_latencies or final_latencies or [0]),
        "final_latency_p95_ms": _p95(final_latencies),
        "rtf": float(asr_result.get("rtf") or 0.0),
        "raw_cer": raw_cer,
        "normalized_cer": normalized_cer,
        "raw_technical_entity_recall": recall,
        "normalized_technical_entity_recall": recall,
        "technical_entity_precision": precision,
        "error_event_count": counts["error"],
        "end_of_stream_event_count": counts["end_of_stream"],
    }


def _events_duration_seconds(events: list[dict[str, Any]]) -> float:
    return max((int(event.get("end_ms", 0) or 0) for event in events), default=0) / 1000


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * 0.95)))
    return sorted_values[index]


def _cer(reference: str, hypothesis: str) -> float:
    if not reference:
        return 0.0
    distance = _levenshtein(reference, hypothesis)
    return round(distance / max(1, len(reference)), 6)


def _levenshtein(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _technical_term_metrics(reference_text: str, hypothesis: str) -> tuple[float, float]:
    reference_terms = [term for term in TECHNICAL_TERMS if term.casefold() in reference_text.casefold()]
    if not reference_terms:
        return 0.0, 0.0
    recognized_terms = [term for term in reference_terms if term.casefold() in hypothesis.casefold()]
    recall = round(len(recognized_terms) / len(reference_terms), 6)
    precision = round(len(recognized_terms) / max(1, len(TECHNICAL_TERMS)), 6)
    return recall, precision


def _empty_feedback_summary() -> dict[str, Any]:
    return {
        "labels": {
            "useful": 0,
            "would_have_asked": 0,
            "wrong": 0,
            "too_late": 0,
            "too_intrusive": 0,
            "dismissed": 0,
        },
        "useful_or_would_have_asked_count": 0,
        "negative_feedback_count": 0,
    }


def _privacy_cost_flags() -> dict[str, bool]:
    return {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    }


def _summarize_process_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "process exited without stderr"
    return " | ".join(lines[-4:])[:800]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", default=f"real_mic_{int(time.time())}")
    parser.add_argument("--record-seconds", type=int, default=DEFAULT_RECORD_SECONDS)
    parser.add_argument("--audio-device-index", type=int, default=DEFAULT_AUDIO_DEVICE_INDEX)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--reference-text", default=DEFAULT_PROMPT_TEXT)
    parser.add_argument("--chunk-ms", type=int, default=DEFAULT_CHUNK_MS)
    parser.add_argument("--num-threads", type=int, default=DEFAULT_NUM_THREADS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_SHERPA_MODEL_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = run_real_mic_full_chain(
        session_id=args.session_id,
        record_seconds=args.record_seconds,
        audio_device_index=args.audio_device_index,
        provider=args.provider,
        reference_text=args.reference_text,
        chunk_ms=args.chunk_ms,
        num_threads=args.num_threads,
        model_dir=args.model_dir,
    )
    json.dump(result, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if result.get("runner_status") == "main_flow_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
