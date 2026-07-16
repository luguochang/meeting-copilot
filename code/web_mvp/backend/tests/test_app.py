import asyncio
import builtins
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import urllib.request

from fastapi.testclient import TestClient
import pytest

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.asr_live_repository import JsonFileAsrLiveSessionRepository
from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller
from meeting_copilot_web_mvp.repository import JsonFileSessionRepository
from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository, SqliteSessionRepository


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_create_app_rejects_multi_worker_llm_runtime(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    with pytest.raises(RuntimeError, match="single worker"):
        create_app()


def test_runtime_app_factory_uses_sqlite_when_data_dir_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path))

    runtime_app = app_module.create_runtime_app()

    assert isinstance(runtime_app.state.asr_live_repository, SqliteAsrLiveSessionRepository)
    assert isinstance(runtime_app.state.session_repository, SqliteSessionRepository)
    assert (tmp_path / "meeting_copilot.db").is_file()


def test_runtime_app_factory_uses_sqlite_default_when_env_is_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("MEETING_COPILOT_DATA_DIR", raising=False)
    monkeypatch.setattr(app_module, "DEFAULT_RUNTIME_DATA_DIR", tmp_path)

    runtime_app = app_module.create_runtime_app()

    assert isinstance(runtime_app.state.asr_live_repository, SqliteAsrLiveSessionRepository)
    assert isinstance(runtime_app.state.session_repository, SqliteSessionRepository)
    assert (tmp_path / "meeting_copilot.db").is_file()


def test_runtime_app_prewarms_resident_funasr_during_startup(monkeypatch, tmp_path):
    lifecycle_calls = []
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module.asr_stream,
        "prewarm_funasr_resident_manager",
        lambda: lifecycle_calls.append("prewarm") or True,
    )
    monkeypatch.setattr(
        app_module.asr_stream,
        "shutdown_funasr_resident_manager",
        lambda: lifecycle_calls.append("shutdown"),
    )

    with TestClient(app_module.create_runtime_app()) as client:
        assert client.get("/health").status_code == 200

    assert lifecycle_calls == ["prewarm", "shutdown"]


def test_packaged_runtime_fails_startup_when_resident_funasr_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    monkeypatch.setattr(app_module.asr_stream, "prewarm_funasr_resident_manager", lambda: False)

    with pytest.raises(RuntimeError, match="failed to become ready"):
        with TestClient(app_module.create_runtime_app()):
            pass


def test_asr_runtime_status_reports_real_resident_readiness(monkeypatch):
    monkeypatch.setattr(app_module.asr_stream, "funasr_realtime_available", lambda: True)
    monkeypatch.setattr(app_module.asr_stream, "_funasr_resident_enabled", lambda: True)
    monkeypatch.setattr(
        app_module.asr_stream,
        "funasr_resident_status",
        lambda: {
            "schema_version": "funasr_resident_status.v1",
            "spawned": True,
            "process_running": True,
            "process_ready": True,
            "pid": 123,
            "generation": 1,
            "active_session_id": None,
            "process_start_count": 1,
            "completed_session_count": 0,
            "last_exit_code": None,
            "last_error": None,
        },
    )

    response = TestClient(create_app()).get("/providers/asr/runtime")

    assert response.status_code == 200
    assert response.json()["resident"]["process_ready"] is True
    assert response.json()["resident"]["pid"] == 123


def test_execution_preview_uses_locally_normalized_final_as_llm_evidence():
    record = {
        "session_id": "normalized_preview",
        "events": [
            {
                "id": "transcript_final:s1",
                "event_type": "transcript_final",
                "sequence": 1,
                "payload": {
                    "segment_id": "s1",
                    "text": "ment gate 和 t九九",
                    "normalized_text": "payment-gateway 和 P99",
                    "evidence_spans": [{
                        "id": "asr_ev_s1",
                        "segment_id": "s1",
                        "quote": "ment gate 和 t九九",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "status": "active",
                    }],
                },
            },
            {
                "id": "llm_request_draft:c1",
                "event_type": "llm_request_draft_event",
                "sequence": 2,
                "payload": {
                    "request_id": "c1",
                    "target_candidate_id": "candidate_1",
                    "target_type": "Risk",
                    "target_id": "risk_1",
                    "gap_rule_id": "risk.rollback.validation",
                    "evidence_span_ids": ["asr_ev_s1"],
                    "segment_batch": ["s1"],
                },
            },
        ],
    }

    preview = app_module._execution_previews_from_record(record)[0]

    assert preview["evidence_spans"][0]["quote"] == "payment-gateway 和 P99"
    assert "ment gate" not in preview["evidence_context"]

TAURI_NOOP_COMMANDS = [
    ("runtime.get_status", "runtime_get_status"),
    ("session.prepare", "session_prepare"),
    ("asr_worker.health", "asr_worker_health"),
    ("mic_adapter.prepare", "mic_adapter_prepare"),
    ("mic_adapter.status", "mic_adapter_status"),
    ("mic_adapter.start", "mic_adapter_start"),
    ("mic_adapter.pause", "mic_adapter_pause"),
    ("mic_adapter.resume", "mic_adapter_resume"),
    ("mic_adapter.stop", "mic_adapter_stop"),
    ("mic_adapter.delete_audio_chunks", "mic_adapter_delete_audio_chunks"),
]


def _valid_tauri_noop_run_result() -> dict[str, object]:
    return {
        "run_result_version": "desktop_tauri_noop_run_result.v1",
        "run_id": "workbench-tauri-noop-review",
        "run_environment": "tauri_webview",
        "explicit_tauri_run_approval_recorded": True,
        "web_app_url_status": "local_dev_url_loaded",
        "ipc_transport_status": "tauri_ipc_available",
        "command_results": [
            {
                "command_id": command_id,
                "command_name": command_name,
                "invoke_status": "returned",
                "result": {
                    "command_id": command_id,
                    "command_status": "noop_bound",
                    "implementation_status": "noop_only",
                    "transport_status": "tauri_ipc_bound",
                    "side_effect_status": "none",
                    "safe_to_invoke_noop": True,
                    "safe_to_execute_real_action": False,
                    "captures_audio": False,
                    "spawns_process": False,
                    "calls_remote_provider": False,
                    "writes_local_files": False,
                },
            }
            for command_id, command_name in TAURI_NOOP_COMMANDS
        ],
    }


def _expected_suggestion_card_schema_outline_preview():
    return {
        "name": "SuggestionCardV1",
        "strict": True,
        "schema_outline_status": "outline_only",
        "schema_outline_source": "local_contract_preview",
        "schema_outline": {
            "type": "object",
            "required": [
                "id",
                "type",
                "evidence_span_ids",
                "state_refs",
                "state_event_ids",
                "gap_rule_id",
                "trigger_reason",
                "trigger_source",
                "final_segment_at_ms",
                "state_event_at_ms",
                "card_created_at_ms",
                "latency_ms",
                "prompt_version",
                "model",
                "usage",
                "schema_result",
                "show_or_silence_decision",
                "segment_batch",
                "status",
            ],
            "optional": [
                "title",
                "suggested_question",
            ],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string"},
                "evidence_span_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "state_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "state_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "gap_rule_id": {"type": "string"},
                "trigger_reason": {"type": "string"},
                "trigger_source": {"type": "string"},
                "final_segment_at_ms": {"type": "integer", "minimum": 0},
                "state_event_at_ms": {"type": "integer", "minimum": 0},
                "card_created_at_ms": {"type": "integer", "minimum": 0},
                "latency_ms": {"type": "integer", "minimum": 0},
                "prompt_version": {"type": "string"},
                "model": {"type": "string"},
                "usage": {"type": "object"},
                "schema_result": {"type": "string"},
                "show_or_silence_decision": {"type": "string"},
                "segment_batch": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "status": {"type": "string", "default": "new"},
                "title": {"type": ["string", "null"]},
                "suggested_question": {"type": ["string", "null"]},
            },
            "additional_properties_status": "allowed_by_local_contract_extra",
        },
    }


def _payload():
    return {
        "session_id": "meeting_001",
        "transcript_report": {
            "provider": "funasr",
            "latency_ms": 1800,
            "rtf": 0.42,
            "text": "payment-gateway 先灰度 10%。还没有确认回滚负责人。",
            "normalized_text": "payment-gateway 先灰度 10%。还没有确认回滚负责人。",
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "payment-gateway 先灰度 10%。",
                    "confidence": 0.91,
                },
                {
                    "id": "seg_002",
                    "start_ms": 5000,
                    "end_ms": 9000,
                    "text": "还没有确认回滚负责人。",
                    "confidence": 0.88,
                },
            ],
            "evidence_spans": [
                {
                    "id": "ev_001",
                    "segment_id": "seg_001",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "quote": "payment-gateway 先灰度 10%。",
                },
                {
                    "id": "ev_002",
                    "segment_id": "seg_002",
                    "start_ms": 5000,
                    "end_ms": 9000,
                    "quote": "还没有确认回滚负责人。",
                },
            ],
        },
        "analysis": {
            "summary": "讨论 payment-gateway 灰度发布。",
            "meeting_context": {
                "is_engineering_meeting": True,
                "reason": "包含灰度、回滚负责人等发布评审内容。",
            },
            "states": {
                "decision_candidates": [
                    {
                        "id": "decision_001",
                        "statement": "payment-gateway 先灰度 10%",
                        "evidence_span_id": "ev_001",
                    }
                ],
                "action_items": [],
                "risks": [],
                "open_questions": [
                    {
                        "id": "question_001",
                        "question": "谁负责回滚？",
                        "evidence_span_ids": ["ev_002"],
                    }
                ],
            },
            "suggestion_cards": [
                {
                    "id": "card_001",
                    "type": "owner_gap",
                    "suggested_question": "是否需要确认回滚负责人？",
                    "evidence_span_id": "ev_002",
                    "state_refs": ["open_question:question_001"],
                    "state_event_ids": ["event_001"],
                    "gap_rule_id": "owner.required",
                    "trigger_reason": "候选灰度决策缺少回滚负责人",
                    "trigger_source": "state_gap_detector",
                    "final_segment_at_ms": 9000,
                    "state_event_at_ms": 9600,
                    "card_created_at_ms": 13800,
                    "latency_ms": 4800,
                    "prompt_version": "suggestion-card.v1",
                    "model": "gpt-5.5",
                    "usage": {"total_tokens": 321},
                    "schema_result": "valid",
                    "show_or_silence_decision": "show",
                    "segment_batch": ["seg_002"],
                }
            ],
        },
        "state_events": [
            {
                "id": "event_001",
                "target_type": "OpenQuestion",
                "target_id": "question_001",
                "event_type": "created",
                "created_at_ms": 9600,
                "evidence_span_ids": ["ev_001"],
            }
        ],
        "llm_usage": {
            "model": "gpt-5.5",
            "call_count": 1,
            "usage": {"total_tokens": 1234},
        },
    }


def test_audio_check_distinguishes_file_asr_and_realtime_asr(monkeypatch, tmp_path):
    fake_funasr_python = tmp_path / "funasr-python"
    fake_funasr_worker = tmp_path / "funasr-stream-worker.py"
    fake_funasr_model = tmp_path / "funasr-online-model"
    fake_funasr_python.write_text("# executable placeholder", encoding="utf-8")
    fake_funasr_worker.write_text("# worker placeholder", encoding="utf-8")
    fake_funasr_model.mkdir()
    (fake_funasr_model / "model.pt").write_bytes(b"model")
    (fake_funasr_model / "config.yaml").write_text("model: local\n", encoding="utf-8")

    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(app_module.asr_stream, "_FUNASR_VENV_PY", fake_funasr_python)
    monkeypatch.setattr(app_module.asr_stream, "_FUNASR_WORKER", fake_funasr_worker)
    monkeypatch.setattr(app_module.asr_stream, "_FUNASR_MODEL_DIR", fake_funasr_model)
    monkeypatch.setattr(app_module.asr_stream, "_SHERPA_VENV_PY", tmp_path / "missing-sherpa-python")
    monkeypatch.setattr(app_module.asr_stream, "_SHERPA_WORKER", tmp_path / "missing-sherpa-worker.py")
    monkeypatch.setattr(app_module.asr_stream, "_SHERPA_MODEL", tmp_path / "missing-sherpa-model")

    body = TestClient(create_app()).get("/audio/check").json()

    assert body["file_asr_available"] is True
    assert body["realtime_asr_available"] is True
    assert body["realtime_asr_providers"] == ["funasr_realtime"]
    assert body["asr_readiness_summary"] == "realtime_ready"
    assert body["funasr_available"] is True


def _shadow_candidate_report_for_feedback_ingestion(audio_written=True):
    return {
        "schema_version": "real_mic_shadow_test_report.v1",
        "session_id": "shadow-test-api-review-038",
        "meeting_profile": {
            "meeting_type": "chinese_technical_review",
            "duration_minutes": 24,
            "participant_count": 4,
            "language": "zh-CN",
            "domain_tags": ["api", "release"],
        },
        "transcript": {
            "segment_count": 2,
            "segments": [
                {
                    "segment_id": "seg-001",
                    "speaker_label": "speaker_1",
                    "start_ms": 0,
                    "end_ms": 4200,
                    "text": "这个接口的 request_id 和 rollback owner 还没定。",
                    "source_event_id": "event-final-001",
                },
                {
                    "segment_id": "seg-002",
                    "speaker_label": "speaker_2",
                    "start_ms": 4300,
                    "end_ms": 7600,
                    "text": "P99 和 40012 的监控也要补。",
                    "source_event_id": "event-final-002",
                },
            ],
        },
        "asr_metrics": {
            "duration_seconds": 1440,
            "first_partial_latency_ms": 420,
            "final_latency_p95_ms": 1800,
            "rtf": 0.18,
            "raw_cer": 0.12,
            "normalized_cer": 0.08,
            "raw_technical_entity_recall": 0.72,
            "normalized_technical_entity_recall": 0.84,
            "technical_entity_precision": 0.9,
            "error_event_count": 0,
            "end_of_stream_event_count": 1,
        },
        "evidence_span_timeline": [
            {
                "evidence_id": "ev-001",
                "segment_id": "seg-001",
                "start_ms": 0,
                "end_ms": 4200,
                "text": "request_id 和 rollback owner 还没定",
                "supports_candidate_id": "cand-001",
            },
            {
                "evidence_id": "ev-002",
                "segment_id": "seg-002",
                "start_ms": 4300,
                "end_ms": 7600,
                "text": "P99 和 40012 的监控也要补",
                "supports_candidate_id": "cand-002",
            },
        ],
        "state_timeline": [
            {
                "state_id": "state-001",
                "state_type": "open_question",
                "at_ms": 4300,
                "evidence_id": "ev-001",
            },
            {
                "state_id": "state-002",
                "state_type": "risk",
                "at_ms": 7600,
                "evidence_id": "ev-002",
            },
        ],
        "candidate_card_timeline": [
            {
                "candidate_id": "cand-001",
                "card_type": "engineering_gap",
                "created_at_ms": 6200,
                "latency_ms": 2000,
                "evidence_ids": ["ev-001"],
                "text": "确认 rollback owner 和 request_id 监控负责人。",
            },
            {
                "candidate_id": "cand-002",
                "card_type": "engineering_gap",
                "created_at_ms": 9200,
                "latency_ms": 1600,
                "evidence_ids": ["ev-002"],
                "text": "补齐 P99 和 40012 的监控阈值。",
            },
        ],
        "feedback_summary": {
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
        },
        "final_decision": {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Feedback has not been collected yet.",
        },
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
        "audio_retention": {
            "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
            "audio_chunk_write_status": "written_by_user_approved_shadow_test"
            if audio_written
            else "not_written",
            "audio_delete_status": "deleted_after_review"
            if audio_written
            else "not_applicable_no_audio_written",
            "retention_policy": "delete_audio_chunks_before_session_discard",
        },
        "known_limitations": [
            "single shadow test cannot prove product-market fit",
        ],
    }


def _asr_live_payload(session_id: str = "local_asr_stream_review"):
    return {
        "session_id": session_id,
        "provider": "local_mock_asr",
        "streaming_events": [
            {
                "event_type": "partial",
                "segment_id": "asr_seg_001",
                "text": "先灰度",
                "start_ms": 0,
                "end_ms": 1200,
                "received_at_ms": 1300,
                "confidence": 0.72,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_001",
                "text": "先灰度 10%。",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3500,
                "confidence": 0.91,
            },
            {
                "event_type": "revision",
                "segment_id": "asr_seg_001_rev1",
                "revision_of": "asr_seg_001",
                "text": "先灰度 5%，不是 10%。",
                "start_ms": 0,
                "end_ms": 3400,
                "received_at_ms": 5200,
                "confidence": 0.94,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_002",
                "text": "谁负责回滚？",
                "start_ms": 3400,
                "end_ms": 6100,
                "received_at_ms": 7000,
                "confidence": 0.9,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_003",
                "text": "如果错误率超过 0.1% 就回滚。",
                "start_ms": 6100,
                "end_ms": 8200,
                "received_at_ms": 8800,
                "confidence": 0.9,
            },
            {
                "event_type": "final",
                "segment_id": "asr_seg_004",
                "text": "张三下周三补充兼容性测试用例。",
                "start_ms": 8200,
                "end_ms": 10400,
                "received_at_ms": 11200,
                "confidence": 0.9,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 10400,
                "end_ms": 11400,
                "received_at_ms": 11400,
            },
        ],
    }


def _write_asr_events_file(root: Path, relative_path: str, events: list[dict]) -> str:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return relative_path


def _valid_schema_validation_candidate_response():
    return {
        "id": "card_dry_run_001",
        "type": "owner_gap",
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001"],
        "state_event_ids": ["asr_state_event_asr_seg_001"],
        "gap_rule_id": "release.rollback.owner.required",
        "trigger_reason": "dry-run validation sample",
        "trigger_source": "llm_schema_validation_dry_run",
        "final_segment_at_ms": 3500,
        "state_event_at_ms": 3500,
        "card_created_at_ms": 3700,
        "latency_ms": 200,
        "prompt_version": "suggestion-card-execution-preview.v1",
        "model": "not_called",
        "usage": {"total_tokens": 0},
        "schema_result": "valid",
        "show_or_silence_decision": "show",
        "segment_batch": ["asr_seg_001"],
        "status": "new",
        "title": "确认回滚负责人",
        "suggested_question": "这次发布的回滚负责人是谁？",
    }


def _card_lifecycle_append_idempotency_key(
    session_id: str,
    event_type: str,
    card_id: str = "card_dry_run_001",
    request_id: str = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    ),
) -> str:
    return (
        "live_asr_card_lifecycle_append:"
        f"{session_id}:"
        f"{request_id}:"
        f"{event_type}:{card_id}"
    )


def _append_persisted_lifecycle_event(
    record: dict,
    *,
    session_id: str,
    event_type: str,
    sequence: int,
    idempotency_key: str | None = None,
    card_id: str = "card_dry_run_001",
    event_id: str | None = None,
    payload_extra: dict | None = None,
):
    payload = {
        "card_id": card_id,
        "idempotency_key": idempotency_key
        if idempotency_key is not None
        else _card_lifecycle_append_idempotency_key(
            session_id,
            event_type,
            card_id,
        ),
        "request_id": (
            "asr_llm_request_draft_"
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
    }
    if event_type == "suggestion_card":
        payload["card"] = {"id": card_id}
    if payload_extra:
        payload.update(payload_extra)
    record["events"].append(
        {
            "id": event_id or f"{event_type}:{card_id}",
            "event_type": event_type,
            "at_ms": 3700 + sequence,
            "sequence": sequence,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": payload,
        }
    )


def _install_no_llm_config_or_secret_read_guards(monkeypatch, tmp_path, label: str):
    config_path = tmp_path / f"{label}.local.json"
    configs_local_dir = tmp_path / "configs" / "local"
    configs_local_dir.mkdir(parents=True)
    configs_local_path = configs_local_dir / f"{label}.json"
    configs_local_path.write_text(
        json.dumps({"api_key": f"TEST_{label.upper()}_CONFIGS_LOCAL_SECRET"}),
        encoding="utf-8",
    )
    config_url = f"https://{label}-read-sentinel.invalid"
    config_secret = f"TEST_{label.upper()}_CONFIG_SECRET"
    config_model = f"{label}-config-model"
    config_bearer = f"{label.upper()}_CONFIG_BEARER"
    env_openai_key = f"TEST_{label.upper()}_ENV_OPENAI_KEY"
    env_meeting_key = f"TEST_{label.upper()}_ENV_MEETING_KEY"
    config_path.write_text(
        json.dumps(
            {
                "base_url": config_url,
                "api_key": config_secret,
                "model": config_model,
                "authorization": f"Bearer {config_bearer}",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", env_openai_key)
    monkeypatch.setenv("MEETING_COPILOT_LLM_API_KEY", env_meeting_key)
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_path_open = Path.open
    original_path_exists = Path.exists
    original_path_is_file = Path.is_file
    original_path_stat = Path.stat
    original_builtin_open = builtins.open
    original_os_stat = os.stat
    original_getenv = os.getenv
    original_environ_get = os.environ.get
    original_environ_getitem = os.environ.__class__.__getitem__
    def is_llm_config_path(path) -> bool:
        try:
            candidate = Path(path)
        except TypeError:
            return False
        if candidate == config_path:
            return True
        return "configs" in candidate.parts and "local" in candidate.parts

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not read config files")
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not read config bytes")
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not open config files")
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(f"{label} must not open config files")
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not check config existence")
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not check config file type")
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not stat config files")
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(f"{label} must not stat config files")
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(f"{label} must not read env secrets")
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(f"{label} must not read env secrets")
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_secret_environ_getitem(environ, key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(f"{label} must not read env secrets")
        return original_environ_getitem(environ, key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(f"{label} must not load llm gateway config")

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError(f"{label} must not access keychain")

    def reject_outbound_llm_http(*args, **kwargs):
        raise AssertionError(f"{label} must not make outbound llm/http calls")

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_llm_config_read_bytes)
    monkeypatch.setattr(Path, "open", reject_llm_config_path_open)
    monkeypatch.setattr(Path, "exists", reject_llm_config_exists)
    monkeypatch.setattr(Path, "is_file", reject_llm_config_is_file)
    monkeypatch.setattr(Path, "stat", reject_llm_config_path_stat)
    monkeypatch.setattr(builtins, "open", reject_llm_config_builtin_open)
    monkeypatch.setattr(os, "stat", reject_llm_config_os_stat)
    monkeypatch.setattr(os, "getenv", reject_llm_secret_getenv)
    monkeypatch.setattr(os.environ, "get", reject_llm_secret_environ_get)
    monkeypatch.setattr(
        os.environ.__class__,
        "__getitem__",
        reject_llm_secret_environ_getitem,
    )
    monkeypatch.setattr(urllib.request, "urlopen", reject_outbound_llm_http)
    monkeypatch.setattr(
        app_module,
        "requests",
        type(
            "NoRequestsAllowed",
            (),
            {
                "get": staticmethod(reject_outbound_llm_http),
                "post": staticmethod(reject_outbound_llm_http),
                "request": staticmethod(reject_outbound_llm_http),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "httpx",
        type(
            "NoHttpxAllowed",
            (),
            {
                "get": staticmethod(reject_outbound_llm_http),
                "post": staticmethod(reject_outbound_llm_http),
                "request": staticmethod(reject_outbound_llm_http),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "load_llm_gateway_config",
        reject_llm_gateway_config_load,
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "load_keychain_secret",
        reject_keychain_access,
        raising=False,
    )
    return [
        str(config_path),
        str(configs_local_path),
        config_url,
        config_secret,
        config_model,
        config_bearer,
        env_openai_key,
        env_meeting_key,
        f"TEST_{label.upper()}_CONFIGS_LOCAL_SECRET",
        "Bearer",
        "sk-",
    ]


def _install_no_native_audio_or_process_guards(monkeypatch, label: str):
    blocked_modules = {
        "AudioToolbox",
        "AVFoundation",
        "CoreAudio",
        "multiprocessing",
        "pyaudio",
        "ScreenCaptureKit",
        "soundcard",
        "sounddevice",
        "subprocess",
        "wasapi",
        "wave",
    }
    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def is_blocked_module(name: str) -> bool:
        root_name = name.split(".", 1)[0]
        return name in blocked_modules or root_name in blocked_modules

    def reject_native_import(name, *args, **kwargs):
        if is_blocked_module(name):
            raise AssertionError(f"{label} must not import native audio/process APIs")
        return original_import(name, *args, **kwargs)

    def reject_native_import_module(name, *args, **kwargs):
        if is_blocked_module(name):
            raise AssertionError(f"{label} must not import native audio/process APIs")
        return original_import_module(name, *args, **kwargs)

    def reject_process_or_native_probe(*args, **kwargs):
        raise AssertionError(f"{label} must not spawn processes or probe native audio")

    monkeypatch.setattr(builtins, "__import__", reject_native_import)
    monkeypatch.setattr(importlib, "import_module", reject_native_import_module)
    monkeypatch.setattr(subprocess, "Popen", reject_process_or_native_probe)
    monkeypatch.setattr(subprocess, "run", reject_process_or_native_probe)
    monkeypatch.setattr(subprocess, "check_call", reject_process_or_native_probe)
    monkeypatch.setattr(subprocess, "check_output", reject_process_or_native_probe)
    monkeypatch.setattr(multiprocessing, "Process", reject_process_or_native_probe)
    monkeypatch.setattr(os, "system", reject_process_or_native_probe)
    monkeypatch.setattr(os, "popen", reject_process_or_native_probe)
    monkeypatch.setattr(
        app_module,
        "subprocess",
        type(
            "NoSubprocessAllowed",
            (),
            {
                "Popen": staticmethod(reject_process_or_native_probe),
                "run": staticmethod(reject_process_or_native_probe),
                "check_call": staticmethod(reject_process_or_native_probe),
                "check_output": staticmethod(reject_process_or_native_probe),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "multiprocessing",
        type(
            "NoMultiprocessingAllowed",
            (),
            {"Process": staticmethod(reject_process_or_native_probe)},
        )(),
        raising=False,
    )


def _asr_live_payload_without_revision(session_id: str):
    payload = _asr_live_payload(session_id=session_id)
    payload["streaming_events"] = [
        event
        for event in payload["streaming_events"]
        if event.get("event_type") != "revision"
    ]
    return payload


def _asr_live_payload_with_low_confidence_candidate(session_id: str):
    payload = _asr_live_payload_without_revision(session_id=session_id)
    for event in payload["streaming_events"]:
        if event.get("segment_id") == "asr_seg_001":
            event["confidence"] = 0.5
    return payload


def _valid_llm_provider_config_validation_payload():
    return {
        "provider_protocol": "openai_compatible_chat_completions",
        "base_url": "https://provider-validation.example.invalid/v1",
        "api_key": "TEST_PROVIDER_VALIDATION_SECRET_VALUE",
        "model": "gpt-5.5",
        "timeout_seconds": 30,
        "ca_bundle_path": "certs/root-ca.pem",
    }


def _valid_llm_provider_config_loader_preflight_payload(config_path: str):
    return {
        "loader_mode": "preflight_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_path": config_path,
        "requested_fields": [
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "ca_bundle_path",
        ],
        "authorization": {
            "user_confirmed_local_config_access": True,
            "allow_secret_read": False,
            "allow_llm_call": False,
        },
    }


def _valid_llm_provider_config_reader_dry_run_payload(config_path: str):
    return {
        "reader_mode": "dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_path": config_path,
        "secret_reference": {
            "reference_type": "keychain_item_reference",
            "reference_id": "meeting-copilot/provider-config-reader-secret",
        },
        "authorization": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
        },
    }


def _valid_llm_provider_masked_status_loader_dry_run_payload(config_path: str):
    return {
        "loader_mode": "masked_status_dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_path": config_path,
        "secret_reference": {
            "reference_type": "keychain_item_reference",
            "reference_id": "meeting-copilot/provider-masked-status-secret",
        },
        "requested_display_fields": [
            "base_url_origin",
            "model",
            "timeout_seconds",
            "ca_bundle_name",
            "api_key",
        ],
        "authorization": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
            "allow_status_value_inference": False,
        },
    }


def _assert_config_reader_dry_run_response_redacts_submitted_values(
    response,
    *values: str,
) -> None:
    response_text = response.text
    for value in values:
        assert value not in response_text
    assert "Bearer" not in response_text
    assert "sk-" not in response_text


def _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
    response,
    *values: str,
) -> None:
    response_text = response.text
    for value in values:
        assert value not in response_text
    assert "Bearer" not in response_text
    assert "sk-" not in response_text


def _assert_llm_provider_secret_storage_policy_body(body: dict, session_id: str):
    assert body == {
        "session_id": session_id,
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "policy_kind": "provider_secret_storage",
        "policy_status": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "not_read",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "safe_to_read_secret": False,
        "recommended_storage_order": [
            "os_keychain",
            "enterprise_secret_provider",
            "environment_variable_for_development_only",
        ],
        "allowed_secret_references": [
            "keychain_item_reference",
            "enterprise_secret_reference",
            "env_var_name_reference",
        ],
        "forbidden_storage_locations": [
            "repository_files",
            "configs_local_plaintext_api_key",
            "session_json",
            "live_asr_audit_events",
            "logs",
            "reports",
            "browser_local_storage",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "required_loader_guards": [
            "explicit_user_authorization",
            "path_privacy_redaction",
            "secret_value_redaction",
            "no_secret_in_error_response",
            "no_secret_in_audit_event",
            "no_secret_in_logs",
            "no_secret_in_browser_storage",
        ],
        "block_reasons": [
            "template_only_policy",
            "secret_storage_adapter_not_connected",
            "provider_config_not_loaded",
            "credentials_not_read",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "authorized_config_file_reader",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }


def test_health_endpoint_reports_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "meeting-copilot-web-mvp"}


def test_backend_allows_tauri_packaged_origin_for_local_api_probe():
    client = TestClient(create_app())

    response = client.options(
        "/health",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"


def test_provider_health_endpoint_masks_llm_secret_and_disables_remote_asr_by_default(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-provider-health-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-provider-health")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(app_module, "_realtime_asr_providers", lambda: ["sherpa_onnx_realtime"])

    response = TestClient(create_app()).get("/providers/health")

    assert response.status_code == 200
    body = response.json()
    assert body["llm"] == {
        "configured": True,
        "provider": "openai_compatible_gateway",
        "model": "gpt-provider-health",
        "is_mock": False,
        "credential_configured": True,
    }
    assert body["asr"]["file_provider"] == "local_funasr_batch"
    assert body["asr"]["file_asr_available"] is True
    assert body["asr"]["realtime_providers"] == ["sherpa_onnx_realtime"]
    assert body["remote_asr"] == {
        "default_enabled": False,
        "enabled": False,
        "providers": [],
        "adapter_contract": "optional_openai_compatible_or_vendor_adapter_disabled_by_default",
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "sk-provider-health-secret" not in serialized
    assert "api_key" not in serialized


def test_asr_live_sessions_list_endpoint_hides_mock_sessions_by_default(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    first = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="history_review_a"),
    )
    second = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id="history_review_b"),
    )

    response = client.get("/live/asr/sessions")
    demo_response = client.get("/live/asr/sessions?include_demo=true")

    assert first.status_code == 201
    assert second.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_count"] == 0
    assert body["sessions"] == []
    assert demo_response.status_code == 200
    body = demo_response.json()
    assert body["session_count"] == 2
    sessions = {item["session_id"]: item for item in body["sessions"]}
    assert set(sessions) == {"history_review_a", "history_review_b"}
    assert sessions["history_review_a"]["provider"] == "local_mock_asr"
    assert sessions["history_review_a"]["event_count"] >= 1
    assert sessions["history_review_a"]["final_count"] >= 1
    assert sessions["history_review_a"]["suggestion_candidate_count"] >= 1
    assert sessions["history_review_a"]["suggestion_card_count"] == 0
    assert sessions["history_review_a"]["approach_card_count"] == 0
    assert sessions["history_review_a"]["has_minutes"] is False


def test_asr_live_session_summary_exposes_recovery_authority_fields():
    summary = app_module._asr_live_session_summary({
        "session_id": "recoverable_real_session",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "created_at_epoch_ms": 1_700_000_000_100,
        "last_activity_at_epoch_ms": 1_700_000_000_900,
        "audio": {"saved": True},
        "events": [{
            "event_type": "transcript_final",
            "at_ms": 1000,
            "payload": {"segment_id": "seg_1", "normalized_text": "已经确认的会议文字"},
        }],
    })

    assert summary["created_at_ms"] == 1_700_000_000_100
    assert summary["last_activity_at_ms"] == 1_700_000_000_900
    assert summary["has_transcript"] is True
    assert summary["has_audio"] is True
    assert summary["recoverable"] is True


def test_asr_live_session_summary_never_marks_mock_or_empty_session_recoverable():
    mock_summary = app_module._asr_live_session_summary({
        "session_id": "mock_session",
        "provider": "local_mock_asr",
        "provider_mode": "mock",
        "is_mock": True,
        "last_activity_at_epoch_ms": 1_700_000_000_900,
        "events": [{"event_type": "transcript_final", "payload": {"text": "演示文字"}}],
    })
    empty_summary = app_module._asr_live_session_summary({
        "session_id": "empty_real_session",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "last_activity_at_epoch_ms": 1_700_000_001_000,
        "events": [],
    })

    assert mock_summary["recoverable"] is False
    assert empty_summary["has_transcript"] is False
    assert empty_summary["has_audio"] is False
    assert empty_summary["recoverable"] is False


def test_asr_live_sessions_list_is_sorted_by_wall_clock_activity_not_session_id(tmp_path):
    repository = JsonFileAsrLiveSessionRepository(tmp_path)
    base_record = {
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "source": "asr_live_event_source",
        "trace_kind": "asr_live_trace",
        "events": [{
            "event_type": "transcript_final",
            "payload": {"segment_id": "seg_1", "normalized_text": "真实会议文字"},
        }],
    }
    repository.create({
        **base_record,
        "session_id": "aaa_older",
        "created_at_epoch_ms": 1_700_000_000_000,
        "last_activity_at_epoch_ms": 1_700_000_001_000,
    })
    repository.create({
        **base_record,
        "session_id": "zzz_newer",
        "created_at_epoch_ms": 1_700_000_002_000,
        "last_activity_at_epoch_ms": 1_700_000_003_000,
    })

    response = TestClient(create_app(data_dir=tmp_path)).get("/live/asr/sessions")

    assert response.status_code == 200
    assert [item["session_id"] for item in response.json()["sessions"]] == [
        "zzz_newer",
        "aaa_older",
    ]


def test_mock_asr_live_session_persists_mock_boundary_with_custom_provider(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            **_asr_live_payload(session_id="custom_mock_provider_review"),
            "provider": "custom_provider_label",
        },
    )
    events_response = client.get("/live/asr/sessions/custom_mock_provider_review/events")
    list_response = client.get("/live/asr/sessions?include_demo=true")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["event_source"]["is_mock"] is True
    assert created["event_source"]["provider_mode"] == "mock"
    assert created["event_source"]["ingest_mode"] == "mock_asr_session"
    assert created["event_source"]["input_source"] == "mock"
    assert created["event_source"]["acceptance_eligible"] is False
    assert "mock_or_demo_session" in created["event_source"]["acceptance_blockers"]
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["is_mock"] is True
    assert body["provider_mode"] == "mock"
    assert body["event_source"]["is_mock"] is True
    assert body["event_source"]["provider_mode"] == "mock"
    assert body["event_source"]["ingest_mode"] == "mock_asr_session"
    assert body["event_source"]["input_source"] == "mock"
    assert body["event_source"]["acceptance_eligible"] is False
    sessions = {item["session_id"]: item for item in list_response.json()["sessions"]}
    assert sessions["custom_mock_provider_review"]["is_mock"] is True
    assert sessions["custom_mock_provider_review"]["provider_mode"] == "mock"
    assert sessions["custom_mock_provider_review"]["event_source"]["ingest_mode"] == "mock_asr_session"
    assert sessions["custom_mock_provider_review"]["event_source"]["acceptance_eligible"] is False


def test_asr_live_event_metadata_rechecks_persisted_transcript_quality(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session_id = "persisted_quality_policy_review"
    bad_text = (
        "下能脱稿画出a卷的全链路能说出每一个组件的位置和作用被属黑准的主循环"
        "request到contest xt moden downtwo calling to methoc ine ofdel背熟midiwell"
        "le的六值和位置背书三三状态一个短期机一个常见机一外一个任务状态"
    )
    events = app_module.build_asr_live_events(
        session_id=session_id,
        provider="sherpa_onnx_realtime",
        streaming_events=[{
            "event_type": "final",
            "segment_id": "quality_seg_1",
            "text": bad_text,
            "start_ms": 0,
            "end_ms": 3_000,
            "received_at_ms": 3_000,
            "confidence": 0.9,
        }],
        is_mock=False,
    )
    app.state.asr_live_repository.create({
        "session_id": session_id,
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "input_source": "real_mic",
        "degradation_reasons": [],
        # Simulate a session persisted before the v3 quality policy existed.
        "asr_semantic_quality": {
            "schema_version": "asr_semantic_quality.v1",
            "policy_version": "general_chinese_technical_meeting.v2",
            "status": "passed",
            "blocker": None,
        },
        "suggestion_cards": [{"card_id": "stale_card"}],
        "approach_cards": [{"card_id": "stale_approach"}],
        "minutes": {"minutes_md": "旧纪要"},
        "events": events,
    })

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    quality = body["event_source"]["asr_semantic_quality"]
    assert quality["policy_version"] == "general_chinese_technical_meeting.v3"
    assert quality["status"] == "blocked"
    assert "mixed_language_fragmentation" in quality["quality_failure_reasons"]
    assert "asr_semantic_quality_blocked" in body["event_source"]["acceptance_blockers"]
    assert body["event_source"]["acceptance_eligible"] is False
    assert body["formal_derivation_status"] == "suppressed_by_asr_semantic_quality"
    assert body["suggestion_cards"] == []
    assert body["approach_cards"] == []
    assert body["minutes"] == {}
    assert body["stored_formal_derivation_counts"] == {
        "suggestion_cards": 1,
        "approach_cards": 1,
        "minutes": 1,
    }


def test_asr_live_quality_migration_clears_stale_semantic_degradation(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session_id = "stale_semantic_degradation_migration"
    events = app_module.build_asr_live_events(
        session_id=session_id,
        provider="funasr_realtime",
        streaming_events=[{
            "event_type": "final",
            "segment_id": "general_seg_1",
            "text": "今天聊聊天气，下午一起散步。",
            "start_ms": 0,
            "end_ms": 3_000,
            "received_at_ms": 3_000,
            "confidence": 0.9,
        }],
        is_mock=False,
    )
    app.state.asr_live_repository.create({
        "session_id": session_id,
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "input_source": "browser_live_mic",
        "degradation_reasons": ["asr_semantic_quality_blocked", "degraded_asr_session"],
        "asr_semantic_quality": {
            "policy_version": "general_chinese_technical_meeting.v2",
            "status": "blocked",
            "blocker": "asr_semantic_quality_blocked",
        },
        "events": events,
        "suggestion_cards": [],
        "approach_cards": [],
        "minutes": {},
    })

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    assert body["degradation_reasons"] == []
    assert body["event_source"]["degradation_reasons"] == []
    assert body["event_source"]["asr_semantic_quality"]["status"] == "warning"
    assert body["event_source"]["acceptance_blockers"] == []


def test_asr_live_events_response_includes_canonical_transcript_snapshot(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "canonical_snapshot_review"

    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    assert create_response.status_code == 201

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    snapshot = body["canonical_transcript"]
    assert snapshot["schema_version"] == "canonical-transcript.v1"
    assert snapshot["session_id"] == session_id
    assert snapshot["segments"]
    assert snapshot["committed_char_count"] > 0
    assert snapshot["full_text"] == snapshot["committed_text"] + (
        snapshot["active_tail"]["display_text"] if snapshot["active_tail"] else ""
    )


def test_asr_live_events_exposes_non_secret_llm_evidence_from_runtime_ledger(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-runtime-evidence-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session_id = "runtime_llm_evidence_review"

    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    assert create_response.status_code == 201
    app.state.settings_usage_repository.record_usage(
        session_id=session_id,
        purpose="formal_suggestion",
        provider="team_gateway",
        model="gpt-5.5",
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        timestamp_ms=1_000,
    )

    response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert response.status_code == 200
    body = response.json()
    assert body["llm_evidence"] == {
        "schema_version": "llm-session-evidence.v1",
        "source": "runtime_config_and_usage_ledger",
        "configured": True,
        "provider": "team_gateway",
        "model": "gpt-5.5",
        "is_mock": False,
        "gateway_base_url_kind": "remote",
        "llm_called": True,
        "llm_call_count": 1,
        "llm_usage_total_tokens": 150,
    }
    assert "sk-runtime-evidence-secret" not in response.text
    assert "gateway.example" not in response.text


def test_create_asr_live_session_events_json_and_sse_use_asr_boundary():
    client = TestClient(create_app())

    create_response = client.post("/live/asr/mock/sessions", json=_asr_live_payload())

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["session_id"] == "local_asr_stream_review"
    assert {
        key: created["event_source"][key]
        for key in ["source", "trace_kind", "transport", "provider", "is_mock"]
    } == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "local_mock_asr",
        "is_mock": True,
    }
    assert created["event_source"]["provider_mode"] == "mock"
    assert created["event_source"]["ingest_mode"] == "mock_asr_session"
    assert [event["event_type"] for event in created["live_events"]] == [
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_revision",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]

    json_response = client.get("/live/asr/sessions/local_asr_stream_review/events")
    sse_response = client.get("/live/asr/sessions/local_asr_stream_review/events.sse")

    assert json_response.status_code == 200
    body = json_response.json()
    assert body["session_id"] == "local_asr_stream_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    events = body["events"]
    assert {event["source"] for event in events} == {"live_asr_stream"}
    assert {event["trace_kind"] for event in events} == {"live_event"}
    assert events[-1]["event_type"] == "evaluation_summary"
    assert events[-1]["payload"]["provider"] == "local_mock_asr"
    assert events[-1]["payload"]["final_event_count"] == 4
    assert events[-1]["payload"]["revision_event_count"] == 1
    assert "suggestion_card" not in [event["event_type"] for event in events]
    suggestion_candidates = [
        event for event in events if event["event_type"] == "suggestion_candidate_event"
    ]
    assert [event["payload"]["gap_rule_id"] for event in suggestion_candidates] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {event["payload"]["llm_call_status"] for event in suggestion_candidates} == {
        "not_called"
    }
    assert {event["payload"]["card_status"] for event in suggestion_candidates} == {
        "not_created"
    }
    request_drafts = [
        event for event in events if event["event_type"] == "llm_request_draft_event"
    ]
    assert [event["payload"]["gap_rule_id"] for event in request_drafts] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {event["payload"]["request_status"] for event in request_drafts} == {
        "draft_only"
    }
    assert {event["payload"]["llm_call_status"] for event in request_drafts} == {
        "not_called"
    }
    assert {event["payload"]["schema_status"] for event in request_drafts} == {
        "not_generated"
    }
    assert {event["payload"]["card_status"] for event in request_drafts} == {
        "not_created"
    }
    assert request_drafts[0]["payload"]["target_candidate_id"] == suggestion_candidates[0]["payload"]["candidate_id"]
    state_events = [event for event in events if event["event_type"] == "state_event"]
    assert [event["payload"]["target_type"] for event in state_events] == [
        "DecisionCandidate",
        "DecisionCandidate",
        "OpenQuestion",
        "Risk",
        "ActionItem",
    ]
    assert state_events[0]["payload"]["state_item"]["source"] == "live_asr_stream"
    assert state_events[2]["payload"]["target_id"] == "asr_question_asr_seg_002"
    assert state_events[2]["payload"]["state_item"] == {
        "id": "asr_question_asr_seg_002",
        "question": "谁负责回滚？",
        "evidence_span_ids": ["asr_ev_asr_seg_002"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert state_events[3]["payload"]["state_item"] == {
        "id": "asr_risk_asr_seg_003",
        "description": "如果错误率超过 0.1% 就回滚。",
        "impact": "condition_exceeded",
        "mitigation": "回滚",
        "status": "open",
        "evidence_span_ids": ["asr_ev_asr_seg_003"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    assert state_events[4]["payload"]["state_item"] == {
        "id": "asr_action_asr_seg_004",
        "description": "张三下周三补充兼容性测试用例。",
        "owner": "张三",
        "deadline": "下周三",
        "status": "candidate",
        "evidence_span_ids": ["asr_ev_asr_seg_004"],
        "source": "live_asr_stream",
        "state_origin": "local_deterministic_asr_skeleton",
    }
    scheduler_event = next(
        event for event in events if event["event_type"] == "scheduler_event"
    )
    assert scheduler_event["payload"]["scheduler_event_type"] == "llm_candidate_queued"
    assert scheduler_event["payload"]["decision_reason"] == "state_change"
    assert scheduler_event["payload"]["would_call_llm"] is True
    assert scheduler_event["payload"]["llm_call_status"] == "not_called"
    assert scheduler_event["payload"]["budget_remaining"] == 79
    assert scheduler_event["payload"]["model"] == "not-called"
    skipped_scheduler_event = [
        event for event in events if event["event_type"] == "scheduler_event"
    ][1]
    assert skipped_scheduler_event["payload"]["scheduler_event_type"] == "llm_candidate_skipped"
    assert skipped_scheduler_event["payload"]["decision_reason"] == "cooldown"
    assert skipped_scheduler_event["payload"]["would_call_llm"] is False
    assert skipped_scheduler_event["payload"]["cooldown_remaining_ms"] == 8300

    assert sse_response.status_code == 200
    assert sse_response.headers["content-type"].startswith("text/event-stream")
    assert "event: transcript_partial" in sse_response.text
    assert "event: transcript_final" in sse_response.text
    assert "event: state_event" in sse_response.text
    assert "event: scheduler_event" in sse_response.text
    assert "event: suggestion_candidate_event" in sse_response.text
    assert "event: llm_request_draft_event" in sse_response.text
    assert "event: transcript_revision" in sse_response.text
    assert "event: evaluation_summary" in sse_response.text
    assert "谁负责回滚？" in sse_response.text
    assert "not-called" in sse_response.text
    assert "llm_candidate_queued" in sse_response.text
    assert "llm_candidate_skipped" in sse_response.text
    assert "not_called" in sse_response.text
    assert "action.owner.deadline.confirmation" in sse_response.text
    assert "asr-candidate-policy.v1" in sse_response.text
    assert "local_deterministic_heuristic" in sse_response.text
    assert '"confidence_level":"high"' in sse_response.text
    assert "not_created" in sse_response.text
    assert "draft_only" in sse_response.text
    assert "not_generated" in sse_response.text
    sse_events = [
        json.loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events


def test_create_asr_live_session_from_local_event_file_uses_worker_handoff_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
        _asr_live_payload()["streaming_events"],
    )
    client = TestClient(create_app())

    create_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "local_asr_file_handoff_review",
            "provider": "sherpa_onnx_streaming",
            "events_path": events_path,
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["session_id"] == "local_asr_file_handoff_review"
    assert created["ingest_mode"] == "local_asr_event_file"
    assert created["events_path"] == "artifacts/tmp/asr_events/api-review-001.sherpa.events.json"
    assert {
        key: created["event_source"][key]
        for key in ("source", "trace_kind", "transport", "provider", "is_mock")
    } == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "sherpa_onnx_streaming",
        "is_mock": False,
    }
    assert created["event_source"]["provider_mode"] == "real"
    assert created["event_source"]["ingest_mode"] == "local_asr_event_file"
    assert created["event_source"]["asr_fallback_used"] is False
    assert created["event_source"]["degradation_reasons"] == []
    assert created["event_source"]["input_source"] == "local_event_file"
    assert created["event_source"]["acceptance_eligible"] is False
    assert "local_event_file_not_real_input" in created["event_source"]["acceptance_blockers"]
    assert created["safe_to_call_llm_now"] is False
    assert created["safe_to_call_remote_asr_now"] is False
    assert created["safe_to_read_user_audio_now"] is False
    assert created["safe_to_read_configs_local_now"] is False
    assert created["safe_to_capture_microphone_now"] is False
    assert created["live_event_counts"]["transcript_final"] == 4
    assert created["live_event_counts"]["transcript_revision"] == 1
    assert created["live_event_counts"]["suggestion_card"] == 0
    assert created["all_llm_statuses"] == ["not_called"]

    json_response = client.get("/live/asr/sessions/local_asr_file_handoff_review/events")
    sse_response = client.get("/live/asr/sessions/local_asr_file_handoff_review/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert events == created["live_events"]
    assert {event["source"] for event in events} == {"live_asr_stream"}
    assert "event: transcript_final" in sse_response.text
    assert "event: suggestion_candidate_event" in sse_response.text
    assert "not_called" in sse_response.text


def test_create_asr_live_session_from_local_event_file_rejects_forbidden_paths_before_reading(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    forbidden_path = _write_asr_events_file(
        tmp_path,
        "configs/local/private-asr-events.json",
        _asr_live_payload()["streaming_events"],
    )
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "blocked_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": forbidden_path,
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["ingest_status"] == "blocked_by_path_validation"
    assert body["detail"]["events_path"] == "<redacted_invalid_path>"
    assert body["detail"]["validation_errors"] == [
        "events path is blocked: configs/local",
    ]
    assert body["detail"]["safe_to_call_llm_now"] is False
    assert body["detail"]["safe_to_read_configs_local_now"] is False


def test_create_asr_live_session_from_local_event_file_rejects_symlink_to_forbidden_root(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    visible_root = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_root = tmp_path / "outside" / "configs" / "local"
    visible_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    target = forbidden_root / "events.json"
    target.write_text(
        json.dumps(_asr_live_payload()["streaming_events"], ensure_ascii=False),
        encoding="utf-8",
    )
    link = visible_root / "linked.events.json"
    link.symlink_to(target)
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "blocked_symlink_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": "artifacts/tmp/asr_events/linked.events.json",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["validation_errors"] == [
        "events path is blocked: configs/local",
    ]


def test_create_asr_live_session_from_local_event_file_rejects_invalid_json_shapes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    cases = [
        (
            "bad-json.events.json",
            "{not-json",
            "blocked_by_invalid_events_file",
            "ASR events file must contain valid JSON",
        ),
        (
            "non-list.events.json",
            json.dumps({"event_type": "final"}),
            "blocked_by_invalid_events_file",
            "ASR events JSON must be a list",
        ),
        (
            "non-object-item.events.json",
            json.dumps([{"event_type": "partial"}, "not-an-object"]),
            "blocked_by_invalid_events_file",
            "ASR events JSON items must be objects",
        ),
    ]
    client = TestClient(create_app())

    for filename, file_text, expected_status, expected_error in cases:
        events_file = tmp_path / "artifacts" / "tmp" / "asr_events" / filename
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(file_text, encoding="utf-8")

        response = client.post(
            "/live/asr/local-event-files/sessions",
            json={
                "session_id": f"blocked_{filename.replace('.', '_')}",
                "provider": "sherpa_onnx_streaming",
                "events_path": f"artifacts/tmp/asr_events/{filename}",
            },
        )

        assert response.status_code == 422, filename
        detail = response.json()["detail"]
        assert detail["ingest_status"] == expected_status
        assert detail["validation_errors"] == [expected_error]
        assert detail["events_path"] == f"artifacts/tmp/asr_events/{filename}"
        assert detail["safe_to_call_llm_now"] is False
        assert detail["safe_to_capture_microphone_now"] is False
        assert detail["safe_to_download_models_now"] is False


def test_create_asr_live_session_from_local_event_file_rejects_event_contract_errors(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    base_event = {
        "event_type": "final",
        "segment_id": "asr_seg_contract",
        "text": "API 回滚负责人还没确认。",
        "start_ms": 0,
        "end_ms": 2400,
        "received_at_ms": 2500,
        "confidence": 0.9,
    }
    cases = [
        (
            "unknown-type.events.json",
            [{**base_event, "event_type": "draft"}],
            "unsupported ASR streaming event_type: draft",
        ),
        (
            "missing-segment.events.json",
            [{key: value for key, value in base_event.items() if key != "segment_id"}],
            "ASR final event missing segment_id",
        ),
        (
            "empty-final.events.json",
            [{**base_event, "text": " "}],
            "ASR final event text must be non-empty",
        ),
        (
            "empty-revision.events.json",
            [
                {
                    **base_event,
                    "event_type": "revision",
                    "segment_id": "asr_seg_rev",
                    "revision_of": "asr_seg_contract",
                    "text": " ",
                }
            ],
            "ASR revision event text must be non-empty",
        ),
        (
            "negative-timestamp.events.json",
            [{**base_event, "start_ms": -1}],
            "ASR final event start_ms must be a non-negative number",
        ),
        (
            "bad-time-order.events.json",
            [{**base_event, "start_ms": 3000, "end_ms": 2000}],
            "ASR final event end_ms must be greater than or equal to start_ms",
        ),
        (
            "bad-confidence.events.json",
            [{**base_event, "confidence": 1.5}],
            "ASR final event confidence must be between 0 and 1",
        ),
        (
            "revision-missing-base.events.json",
            [{**base_event, "event_type": "revision", "segment_id": "asr_seg_rev"}],
            "ASR revision event missing revision_of",
        ),
    ]
    client = TestClient(create_app())

    for filename, events, expected_error in cases:
        events_path = _write_asr_events_file(
            tmp_path,
            f"artifacts/tmp/asr_events/{filename}",
            events,
        )

        response = client.post(
            "/live/asr/local-event-files/sessions",
            json={
                "session_id": f"contract_{filename.replace('.', '_')}",
                "provider": "sherpa_onnx_streaming",
                "events_path": events_path,
            },
        )

        assert response.status_code == 422, filename
        detail = response.json()["detail"]
        assert detail["ingest_status"] == "blocked_by_event_contract"
        assert detail["validation_errors"] == [expected_error]
        assert detail["events_path"] == f"artifacts/tmp/asr_events/{filename}"
        assert detail["safe_to_call_remote_asr_now"] is False
        assert detail["safe_to_read_user_audio_now"] is False
        assert detail["safe_to_download_models_now"] is False


def test_create_asr_live_session_from_local_event_file_handles_absolute_and_missing_paths(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    inside_absolute_path = (
        tmp_path / "artifacts" / "tmp" / "asr_events" / "inside-absolute.events.json"
    )
    inside_absolute_path.parent.mkdir(parents=True, exist_ok=True)
    inside_absolute_path.write_text(
        json.dumps(_asr_live_payload()["streaming_events"], ensure_ascii=False),
        encoding="utf-8",
    )
    outside_path = tmp_path.parent / "outside-asr-events.json"
    outside_path.write_text(
        json.dumps(_asr_live_payload()["streaming_events"], ensure_ascii=False),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    inside_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "inside_absolute_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": str(inside_absolute_path),
        },
    )
    outside_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "outside_absolute_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": str(outside_path),
        },
    )
    missing_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "missing_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": "artifacts/tmp/asr_events/missing.events.json",
        },
    )

    assert inside_response.status_code == 201
    assert inside_response.json()["events_path"] == (
        "artifacts/tmp/asr_events/inside-absolute.events.json"
    )
    assert outside_response.status_code == 422
    outside_detail = outside_response.json()["detail"]
    assert outside_detail["ingest_status"] == "blocked_by_path_validation"
    assert outside_detail["events_path"] == "<redacted_invalid_path>"
    assert outside_detail["validation_errors"] == [
        "events path is not under approved ASR events root",
    ]
    assert str(outside_path) not in outside_response.text
    assert missing_response.status_code == 422
    missing_detail = missing_response.json()["detail"]
    assert missing_detail["ingest_status"] == "blocked_by_invalid_events_file"
    assert missing_detail["events_path"] == "artifacts/tmp/asr_events/missing.events.json"
    assert missing_detail["validation_errors"] == [
        "ASR events file could not be read",
    ]
    assert str(tmp_path) not in missing_response.text


def test_create_asr_live_session_from_local_event_file_rejects_duplicate_session_without_mutation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    first_events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/duplicate-first.events.json",
        _asr_live_payload()["streaming_events"],
    )
    replacement_events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/duplicate-replacement.events.json",
        [
            {
                "event_type": "final",
                "segment_id": "asr_seg_replacement",
                "text": "API 替换内容不应该污染已有 session。",
                "start_ms": 0,
                "end_ms": 2000,
                "received_at_ms": 2100,
                "confidence": 0.9,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 2000,
                "end_ms": 2100,
                "received_at_ms": 2100,
            },
        ],
    )
    client = TestClient(create_app())

    first_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "duplicate_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": first_events_path,
        },
    )
    duplicate_response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "duplicate_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": replacement_events_path,
        },
    )
    read_response = client.get(
        "/live/asr/sessions/duplicate_local_asr_file_handoff/events"
    )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 422
    detail = duplicate_response.json()["detail"]
    assert detail["ingest_status"] == "blocked_by_duplicate_session"
    assert detail["events_path"] == "artifacts/tmp/asr_events/duplicate-replacement.events.json"
    assert detail["validation_errors"] == [
        "ASR live session already exists: duplicate_local_asr_file_handoff",
    ]
    assert detail["safe_to_call_llm_now"] is False
    assert read_response.status_code == 200
    read_text = read_response.text
    assert "替换内容不应该污染" not in read_text
    assert read_response.json()["events"] == first_response.json()["live_events"]


def test_create_asr_live_session_from_local_event_file_persists_across_app_instances(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/persisted-handoff.events.json",
        _asr_live_payload()["streaming_events"],
    )
    first_client = TestClient(create_app(data_dir=tmp_path / "repo-data"))

    create_response = first_client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": "persisted_local_asr_file_handoff",
            "provider": "sherpa_onnx_streaming",
            "events_path": events_path,
        },
    )

    second_client = TestClient(create_app(data_dir=tmp_path / "repo-data"))
    json_response = second_client.get(
        "/live/asr/sessions/persisted_local_asr_file_handoff/events"
    )
    sse_response = second_client.get(
        "/live/asr/sessions/persisted_local_asr_file_handoff/events.sse"
    )

    assert create_response.status_code == 201
    assert json_response.status_code == 200
    assert json_response.json()["events"] == create_response.json()["live_events"]
    assert json_response.json()["source"] == "live_asr_stream"
    assert json_response.json()["trace_kind"] == "live_event"
    assert sse_response.status_code == 200
    assert "event: transcript_final" in sse_response.text
    assert "not_called" in sse_response.text


def test_create_asr_live_session_keeps_multi_state_scheduler_pairs_at_api_boundary():
    client = TestClient(create_app())
    payload = {
        "session_id": "local_asr_multi_state_review",
        "provider": "local_mock_asr",
        "streaming_events": [
            {
                "event_type": "final",
                "segment_id": "asr_seg_multi_001",
                "text": "先灰度 10%，谁负责回滚？",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3500,
                "confidence": 0.9,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "asr_eos",
                "text": "",
                "start_ms": 3600,
                "end_ms": 3600,
                "received_at_ms": 3600,
            },
        ],
    }

    create_response = client.post("/live/asr/mock/sessions", json=payload)
    json_response = client.get("/live/asr/sessions/local_asr_multi_state_review/events")
    sse_response = client.get("/live/asr/sessions/local_asr_multi_state_review/events.sse")

    assert create_response.status_code == 201
    events = json_response.json()["events"]
    assert [event["event_type"] for event in events] == [
        "transcript_final",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "state_event",
        "scheduler_event",
        "suggestion_candidate_event",
        "llm_request_draft_event",
        "evaluation_summary",
    ]
    assert events[1]["payload"]["target_type"] == "DecisionCandidate"
    assert events[2]["payload"]["source_event_ids"] == [
        "asr_state_event_asr_seg_multi_001"
    ]
    assert events[3]["payload"]["target_type"] == "DecisionCandidate"
    assert events[3]["payload"]["gap_rule_id"] == "release.rollback.owner.required"
    assert events[4]["payload"]["target_candidate_id"] == events[3]["payload"]["candidate_id"]
    assert events[5]["payload"]["target_type"] == "OpenQuestion"
    assert events[5]["payload"]["state_item"]["question"] == "先灰度 10%，谁负责回滚？"
    assert events[6]["payload"]["source_event_ids"] == [
        "asr_question_event_asr_seg_multi_001"
    ]
    assert events[7]["payload"]["target_type"] == "OpenQuestion"
    assert events[7]["payload"]["gap_rule_id"] == "open.question.followup"
    assert events[8]["payload"]["target_candidate_id"] == events[7]["payload"]["candidate_id"]

    sse_events = [
        json.loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events


def test_asr_live_suggestion_candidates_endpoint_returns_only_candidate_queue():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_candidate_query_review"),
    )

    response = client.get(
        "/live/asr/sessions/local_asr_candidate_query_review/suggestion-candidates"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_candidate_query_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["candidate_count"] == 5
    candidates = body["candidates"]
    assert [candidate["event_type"] for candidate in candidates] == [
        "suggestion_candidate_event",
        "suggestion_candidate_event",
        "suggestion_candidate_event",
        "suggestion_candidate_event",
        "suggestion_candidate_event",
    ]
    assert [candidate["sequence"] for candidate in candidates] == [
        5,
        10,
        15,
        20,
        25,
    ]
    assert "llm_request_draft_event" not in [
        candidate["event_type"] for candidate in candidates
    ]
    assert [candidate["payload"]["gap_rule_id"] for candidate in candidates] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {candidate["payload"]["candidate_policy_version"] for candidate in candidates} == {
        "asr-candidate-policy.v1"
    }
    assert {candidate["payload"]["confidence_source"] for candidate in candidates} == {
        "local_deterministic_heuristic"
    }
    assert {candidate["payload"]["llm_call_status"] for candidate in candidates} == {
        "not_called"
    }
    assert {candidate["payload"]["card_status"] for candidate in candidates} == {
        "not_created"
    }
    assert candidates[0]["event_id"] == "suggestion_candidate:asr_state_event_asr_seg_001"
    assert candidates[0]["at_ms"] == 3500
    assert "payload" in candidates[0]


def test_asr_live_suggestion_candidates_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_candidate_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.get(
        "/live/asr/sessions/local_asr_candidate_empty_review/suggestion-candidates"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_candidate_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "candidate_count": 0,
        "candidates": [],
    }


def test_asr_live_suggestion_candidates_endpoint_reads_persisted_record_across_app_instances(tmp_path):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_candidate_query_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_candidate_query_review/suggestion-candidates"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_count"] == 5
    assert body["candidates"] == [
        {
            "sequence": event["sequence"],
            "event_id": event["id"],
            "event_type": event["event_type"],
            "at_ms": event["at_ms"],
            "payload": event["payload"],
        }
        for event in create_response.json()["live_events"]
        if event["event_type"] == "suggestion_candidate_event"
    ]


def test_asr_live_suggestion_candidates_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get("/live/asr/sessions/missing_asr_review/suggestion-candidates")

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_request_drafts_endpoint_returns_only_request_draft_queue():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_request_draft_query_review"),
    )

    response = client.get(
        "/live/asr/sessions/local_asr_request_draft_query_review/llm-request-drafts"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_request_draft_query_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["request_draft_count"] == 5
    drafts = body["request_drafts"]
    assert [draft["event_type"] for draft in drafts] == [
        "llm_request_draft_event",
        "llm_request_draft_event",
        "llm_request_draft_event",
        "llm_request_draft_event",
        "llm_request_draft_event",
    ]
    assert [draft["sequence"] for draft in drafts] == [6, 11, 16, 21, 26]
    assert "suggestion_candidate_event" not in [
        draft["event_type"] for draft in drafts
    ]
    assert {draft["payload"]["request_status"] for draft in drafts} == {
        "draft_only"
    }
    assert {draft["payload"]["llm_call_status"] for draft in drafts} == {
        "not_called"
    }
    assert {draft["payload"]["schema_status"] for draft in drafts} == {
        "not_generated"
    }
    assert {draft["payload"]["card_status"] for draft in drafts} == {
        "not_created"
    }
    assert drafts[0]["event_id"] == "llm_request_draft:asr_state_event_asr_seg_001"
    assert drafts[0]["at_ms"] == 3500
    assert "payload" in drafts[0]


def test_asr_live_llm_request_drafts_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_request_draft_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.get(
        "/live/asr/sessions/local_asr_request_draft_empty_review/llm-request-drafts"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_request_draft_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "request_draft_count": 0,
        "request_drafts": [],
    }


def test_asr_live_llm_request_drafts_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_request_draft_query_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_request_draft_query_review/llm-request-drafts"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["request_draft_count"] == 5
    assert body["request_drafts"] == [
        {
            "sequence": event["sequence"],
            "event_id": event["id"],
            "event_type": event["event_type"],
            "at_ms": event["at_ms"],
            "payload": event["payload"],
        }
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]


def test_asr_live_llm_request_drafts_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get("/live/asr/sessions/missing_asr_review/llm-request-drafts")

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text



def test_asr_live_llm_execution_previews_endpoint_returns_preview_queue_without_calling_llm():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_preview_review"),
    )

    response = client.get(
        "/live/asr/sessions/local_asr_execution_preview_review/llm-execution-previews"
    )
    events_response = client.get(
        "/live/asr/sessions/local_asr_execution_preview_review/events"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_execution_preview_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["execution_preview_count"] == 5
    previews = body["execution_previews"]
    assert [preview["request_draft_event_id"] for preview in previews] == [
        event["id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert [preview["request_draft_sequence"] for preview in previews] == [
        6,
        11,
        16,
        21,
        26,
    ]
    assert previews[0] == {
        "execution_id": (
            "asr_llm_execution_preview_"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "execution_status": "preview_only",
        "request_id": (
            "asr_llm_request_draft_"
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "request_draft_sequence": 6,
        "request_type": "llm_suggestion_card_draft",
        "target_candidate_id": (
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "prompt_version": "suggestion-card-execution-preview.v1",
        "provider": "not_configured",
        "model": "not_called",
        "llm_call_status": "not_called",
        "schema_name": "SuggestionCardV1",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "idempotency_key": (
            "live_asr_execution_preview:local_asr_execution_preview_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "evidence_spans": [
            {
                "id": "asr_ev_asr_seg_001",
                "segment_id": "asr_seg_001",
                "start_ms": 0,
                "end_ms": 3200,
                "quote": "先灰度 10%。",
                "status": "active",
            }
        ],
        "evidence_context": "[00:00-00:03] 先灰度 10%。",
        "segment_batch": ["asr_seg_001"],
        "candidate_confidence": 0.9,
        "candidate_confidence_level": "high",
        "candidate_degradation_reasons": [],
        "input_summary": "DecisionCandidate asr_decision_asr_seg_001 from asr_seg_001 using asr_ev_asr_seg_001",
        "suggested_prompt": "确认决策是否包含 owner、回滚条件和监控口径。",
    }
    assert {preview["execution_status"] for preview in previews} == {"preview_only"}
    assert {preview["llm_call_status"] for preview in previews} == {"not_called"}
    assert {preview["schema_status"] for preview in previews} == {"not_generated"}
    assert {preview["card_status"] for preview in previews} == {"not_created"}
    assert {preview["cost_status"] for preview in previews} == {"not_estimated"}
    assert {preview["provider"] for preview in previews} == {"not_configured"}
    assert {preview["model"] for preview in previews} == {"not_called"}
    assert all(preview["source_event_ids"] for preview in previews)
    assert all(preview["evidence_span_ids"] for preview in previews)
    assert all(preview["segment_batch"] for preview in previews)
    assert events_response.status_code == 200
    assert events_response.json()["events"] == create_response.json()["live_events"]
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_response.json()["events"]
    ]


def test_asr_live_llm_execution_previews_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_execution_preview_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.get(
        "/live/asr/sessions/local_asr_execution_preview_empty_review/llm-execution-previews"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_execution_preview_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "execution_preview_count": 0,
        "execution_previews": [],
    }


def test_asr_live_llm_execution_previews_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_execution_preview_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_execution_preview_review/llm-execution-previews"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["execution_preview_count"] == 5
    assert [preview["request_id"] for preview in body["execution_previews"]] == [
        event["payload"]["request_id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert body["execution_previews"][0]["idempotency_key"] == (
        "live_asr_execution_preview:persisted_asr_execution_preview_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )


def test_asr_live_llm_execution_previews_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get(
        "/live/asr/sessions/missing_asr_review/llm-execution-previews"
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_returns_skipped_runs_without_calling_llm():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_disabled_run_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_execution_disabled_run_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_disabled_run_review/llm-execution-runs",
        json={"mode": "disabled"},
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_execution_disabled_run_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_execution_disabled_run_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["executor_mode"] == "disabled"
    assert body["run_count"] == 5
    runs = body["runs"]
    assert [run["request_draft_event_id"] for run in runs] == [
        event["id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert [run["request_draft_sequence"] for run in runs] == [
        6,
        11,
        16,
        21,
        26,
    ]
    assert runs[0] == {
        "run_id": (
            "asr_llm_execution_run_disabled_"
            "asr_llm_execution_preview_"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "run_status": "skipped",
        "skip_reason": "llm_executor_disabled",
        "execution_id": (
            "asr_llm_execution_preview_"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "execution_status": "preview_only",
        "request_id": (
            "asr_llm_request_draft_"
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "request_draft_sequence": 6,
        "request_type": "llm_suggestion_card_draft",
        "target_candidate_id": (
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "prompt_version": "suggestion-card-execution-preview.v1",
        "provider": "not_configured",
        "model": "not_called",
        "llm_call_status": "not_called",
        "schema_name": "SuggestionCardV1",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "idempotency_key": (
            "live_asr_execution_run:disabled:"
            "local_asr_execution_disabled_run_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "evidence_spans": [
            {
                "id": "asr_ev_asr_seg_001",
                "segment_id": "asr_seg_001",
                "start_ms": 0,
                "end_ms": 3200,
                "quote": "先灰度 10%。",
                "status": "active",
            }
        ],
        "evidence_context": "[00:00-00:03] 先灰度 10%。",
        "segment_batch": ["asr_seg_001"],
        "candidate_confidence": 0.9,
        "candidate_confidence_level": "high",
        "candidate_degradation_reasons": [],
        "input_summary": "DecisionCandidate asr_decision_asr_seg_001 from asr_seg_001 using asr_ev_asr_seg_001",
        "suggested_prompt": "确认决策是否包含 owner、回滚条件和监控口径。",
    }
    assert {run["run_status"] for run in runs} == {"skipped"}
    assert {run["skip_reason"] for run in runs} == {"llm_executor_disabled"}
    assert {run["llm_call_status"] for run in runs} == {"not_called"}
    assert {run["schema_status"] for run in runs} == {"not_generated"}
    assert {run["card_status"] for run in runs} == {"not_created"}
    assert {run["cost_status"] for run in runs} == {"not_estimated"}
    assert {run["provider"] for run in runs} == {"not_configured"}
    assert {run["model"] for run in runs} == {"not_called"}
    assert all(run["source_event_ids"] for run in runs)
    assert all(run["evidence_span_ids"] for run in runs)
    assert all(run["segment_batch"] for run in runs)
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_execution_runs_disabled_endpoint_returns_empty_runs_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_execution_disabled_empty_review",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_transcript_only_001",
                    "text": "今天我们同步一下背景信息。",
                    "start_ms": 0,
                    "end_ms": 2400,
                    "received_at_ms": 2500,
                    "confidence": 0.9,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "asr_eos",
                    "text": "",
                    "start_ms": 2600,
                    "end_ms": 2600,
                    "received_at_ms": 2600,
                },
            ],
        },
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_disabled_empty_review/llm-execution-runs",
        json={"mode": "disabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert {
        key: body[key]
        for key in ("session_id", "source", "trace_kind", "executor_mode", "run_count", "runs")
    } == {
        "session_id": "local_asr_execution_disabled_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "executor_mode": "disabled",
        "run_count": 0,
        "runs": [],
    }
    assert body["llm_provider"] == {
        "provider": "not_configured",
        "model": "not_called",
        "configured_from_env": False,
        "is_mock": False,
    }


def test_asr_live_llm_execution_runs_disabled_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_execution_disabled_run_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_execution_disabled_run_review/llm-execution-runs",
        json={"mode": "disabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["executor_mode"] == "disabled"
    assert body["run_count"] == 5
    assert [run["request_id"] for run in body["runs"]] == [
        event["payload"]["request_id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert body["runs"][0]["idempotency_key"] == (
        "live_asr_execution_run:disabled:persisted_asr_execution_disabled_run_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )


def _create_acceptance_eligible_asr_live_session(tmp_path, session_id: str) -> TestClient:
    client = TestClient(create_app(data_dir=tmp_path))
    repo = app_module.SqliteAsrLiveSessionRepository(tmp_path)
    events = app_module.build_asr_live_events(
        session_id=session_id,
        provider="sherpa_onnx_realtime",
        streaming_events=[
            {
                "event_type": "final",
                "segment_id": "asr_seg_001",
                "text": "先灰度 10%。谁负责回滚？",
                "start_ms": 0,
                "end_ms": 3200,
                "received_at_ms": 3500,
                "confidence": 0.91,
            }
        ],
        is_mock=False,
    )
    repo.create(
        {
            "session_id": session_id,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "sherpa_onnx_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "real_mic",
            "asr_fallback_used": False,
            "degradation_reasons": [],
            "events": events,
        }
    )
    return client


def test_asr_live_production_derivation_endpoints_reject_mock_llm_provider(monkeypatch, tmp_path):
    session_id = "production_mock_llm_provider_blocked"
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "true")
    client = _create_acceptance_eligible_asr_live_session(tmp_path, session_id)

    endpoints = [
        f"/live/asr/sessions/{session_id}/llm-execution-runs",
        f"/live/asr/sessions/{session_id}/approach-cards",
        f"/live/asr/sessions/{session_id}/minutes",
        f"/live/asr/sessions/{session_id}/minutes.json",
    ]
    responses = [client.post(endpoint, json={"mode": "enabled"}) for endpoint in endpoints]

    for response in responses:
        assert response.status_code == 409
        assert "mock LLM provider cannot create production derivations" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/sessions/missing_asr_review/llm-execution-runs",
        json={"mode": "disabled"},
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_execution_runs_endpoint_rejects_unsupported_mode():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_mode_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_mode_review/llm-execution-runs",
        json={"mode": "foo"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "unsupported llm execution mode: foo" in response.text


def test_asr_live_llm_execution_runs_enabled_without_config_returns_422(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_no_cfg"),
    )
    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_enabled_no_cfg/llm-execution-runs",
        json={"mode": "enabled"},
    )
    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "not configured" in response.text


def test_asr_live_llm_execution_runs_enabled_rejects_mock_session_without_explicit_demo_allowance(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_mock_blocked"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_enabled_mock_blocked/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 409
    assert "not eligible for enabled LLM execution" in response.text
    assert "mock_or_demo_session" in response.text


def test_asr_live_enabled_approach_and_minutes_reject_mock_session_without_explicit_demo_allowance(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_enabled_derivatives_mock_blocked"),
    )

    approach = client.post(
        "/live/asr/sessions/local_asr_enabled_derivatives_mock_blocked/approach-cards",
        json={"mode": "enabled"},
    )
    minutes = client.post(
        "/live/asr/sessions/local_asr_enabled_derivatives_mock_blocked/minutes",
        json={"mode": "enabled"},
    )
    minutes_json = client.post(
        "/live/asr/sessions/local_asr_enabled_derivatives_mock_blocked/minutes.json",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    for response in (approach, minutes, minutes_json):
        assert response.status_code == 409
        assert "not eligible for enabled LLM execution" in response.text
        assert "mock_or_demo_session" in response.text


def test_asr_live_production_derivation_endpoints_reject_non_acceptance_bypass_field(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="public_bypass_field_blocked"),
    )

    endpoints = [
        "/live/asr/sessions/public_bypass_field_blocked/llm-execution-runs",
        "/live/asr/sessions/public_bypass_field_blocked/approach-cards",
        "/live/asr/sessions/public_bypass_field_blocked/minutes",
        "/live/asr/sessions/public_bypass_field_blocked/minutes.json",
    ]
    responses = [
        client.post(endpoint, json={"mode": "enabled", "allow_non_acceptance_execution": True})
        for endpoint in endpoints
    ]

    assert create_response.status_code == 201
    for response in responses:
        assert response.status_code == 422
        assert "allow_non_acceptance_execution" in response.text


def test_demo_derivation_endpoint_can_execute_mock_session_without_public_bypass_field(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [{"message": {"content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="demo_derivation_run"),
    )

    response = client.post(
        "/live/asr/demo/sessions/demo_derivation_run/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executor_mode"] == "enabled"
    assert body["execution_boundary"] == "demo_non_acceptance_execution"
    assert body["run_count"] >= 1
    assert body["runs"][0]["card"]["suggestion_text"] == "建议确认 owner"


def test_asr_live_llm_execution_runs_enabled_calls_llm_and_creates_real_cards(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            return {
                "choices": [{"message": {"content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    fake = FakeClient()
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    raw_base_url = "https://private-gateway.example/internal"
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", raw_base_url)
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_run"),
    )
    assert create_response.status_code == 201
    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_enabled_run/llm-execution-runs",
        json={"mode": "enabled"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executor_mode"] == "enabled"
    assert body["run_count"] >= 1
    run = body["runs"][0]
    assert run["run_status"] == "completed"
    assert run["llm_call_status"] == "called"
    assert run["card_status"] == "new"
    assert run["card"]["card_status"] == "new"
    assert run["card"]["suggestion_text"]
    assert run["card"]["llm_trace"]["model"] == "test-model"
    assert run["provider"] == "team_gateway"
    assert run["card"]["llm_trace"]["provider"] == "team_gateway"
    assert run["llm_usage"]["total_tokens"] == 130
    assert fake.calls == body["run_count"]
    assert raw_base_url not in response.text
    persisted = client.get("/live/asr/sessions/local_asr_execution_enabled_run/events")
    assert persisted.status_code == 200
    assert raw_base_url not in persisted.text


def test_asr_live_llm_execution_runs_enabled_caps_long_meeting_candidates(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"suggestion_text":"建议先处理最高价值的现场提醒",'
                                '"confidence":0.82,'
                                '"trigger_reason":"长会议候选较多，需要限流"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    fake = FakeClient()
    streaming_events = []
    for index in range(8):
        start_ms = index * 10_000
        streaming_events.append(
            {
                "event_type": "final",
                "segment_id": f"asr_long_seg_{index:03d}",
                "text": (
                    f"第 {index} 个接口发布先灰度 5%，如果错误率超过 0.1% 就回滚，"
                    "谁负责回滚？"
                ),
                "start_ms": start_ms,
                "end_ms": start_ms + 7_000,
                "received_at_ms": start_ms + 7_500,
                "confidence": 0.91,
            }
        )
    streaming_events.append(
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_long_eos",
            "text": "",
            "start_ms": 90_000,
            "end_ms": 91_000,
            "received_at_ms": 91_000,
        }
    )
    payload = {
        "session_id": "local_asr_execution_long_candidate_cap",
        "provider": "local_mock_asr",
        "streaming_events": streaming_events,
    }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    monkeypatch.setenv("LLM_EXECUTION_MAX_CANDIDATES_PER_RUN", "3")
    client = TestClient(create_app())
    create_response = client.post("/live/asr/mock/sessions", json=payload)
    assert create_response.status_code == 201
    candidate_count = sum(
        1
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    )
    assert candidate_count > 3

    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_long_candidate_cap/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    selection = body["candidate_selection"]
    assert body["run_count"] == 3
    assert fake.calls == 3
    assert selection["policy_version"] == "llm-execution-candidate-selection.v1"
    assert selection["total_candidates"] == candidate_count
    assert selection["max_candidates"] == 3
    assert selection["selected_count"] == 3
    assert selection["skipped_count"] == candidate_count - 3
    assert selection["selection_applied"] is True
    assert len(selection["selected_candidate_ids"]) == 3
    assert len(selection["skipped_candidate_ids"]) == candidate_count - 3


def test_asr_live_llm_execution_runs_enabled_honors_request_candidate_budget(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"suggestion_text":"建议先生成一条最高价值建议",'
                                '"confidence":0.84,'
                                '"trigger_reason":"整理会议快路径"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    fake = FakeClient()
    payload = _asr_live_payload(session_id="local_asr_execution_request_budget")
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    monkeypatch.setenv("LLM_EXECUTION_MAX_CANDIDATES_PER_RUN", "5")
    client = TestClient(create_app())
    create_response = client.post("/live/asr/mock/sessions", json=payload)
    assert create_response.status_code == 201

    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_request_budget/llm-execution-runs",
        json={"mode": "enabled", "max_candidates": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    selection = body["candidate_selection"]
    assert body["run_count"] == 1
    assert fake.calls == 1
    assert selection["max_candidates"] == 1
    assert selection["requested_max_candidates"] == 1
    assert selection["selection_reason"] == "request_max_candidates"
    assert selection["selected_count"] == 1
    assert selection["skipped_count"] >= 1


def test_asr_live_llm_execution_runs_enabled_persists_cards_for_history(monkeypatch):
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"suggestion_text":"建议确认 owner",'
                                '"confidence":0.8,'
                                '"trigger_reason":"owner 缺失"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_persist_cards"),
    )

    response = client.post(
        "/live/asr/demo/sessions/local_asr_execution_persist_cards/llm-execution-runs",
        json={"mode": "enabled"},
    )
    fetched = client.get("/live/asr/sessions/local_asr_execution_persist_cards/events")
    history = client.get("/live/asr/sessions?include_demo=true")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["suggestion_cards"]
    assert record["suggestion_cards"][0]["suggestion_text"] == "建议确认 owner"
    assert record["suggestion_cards"][0]["evidence_span_ids"]
    indexed = {
        item["session_id"]: item
        for item in history.json()["sessions"]
    }
    assert indexed["local_asr_execution_persist_cards"]["suggestion_card_count"] >= 1


def test_asr_live_llm_execution_runs_disabled_endpoint_requires_explicit_mode():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_missing_mode_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_missing_mode_review/llm-execution-runs",
        json={},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "mode" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_rejects_empty_body():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_empty_body_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_empty_body_review/llm-execution-runs"
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "Field required" in response.text


def test_asr_live_llm_execution_runs_disabled_endpoint_rejects_extra_fields():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_extra_field_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_extra_field_review/llm-execution-runs",
        json={"mode": "disabled", "api_key": "ignored-test-value"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "Extra inputs are not permitted" in response.text



def test_asr_live_session_persists_json_events_across_app_instances(tmp_path):
    first_client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="persisted_asr_live_review")

    create_response = first_client.post("/live/asr/mock/sessions", json=payload)

    assert create_response.status_code == 201
    second_client = TestClient(create_app(data_dir=tmp_path))
    json_response = second_client.get("/live/asr/sessions/persisted_asr_live_review/events")
    sse_response = second_client.get("/live/asr/sessions/persisted_asr_live_review/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert events == create_response.json()["live_events"]
    assert json_response.json()["source"] == "live_asr_stream"
    assert json_response.json()["trace_kind"] == "live_event"
    assert sse_response.status_code == 200
    assert "event: state_event" in sse_response.text
    assert "谁负责回滚？" in sse_response.text


def test_delete_session_removes_persisted_asr_live_audit_record(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="delete_asr_live_review")
    create_response = client.post("/live/asr/mock/sessions", json=payload)

    delete_response = client.delete("/sessions/delete_asr_live_review")
    json_response = client.get("/live/asr/sessions/delete_asr_live_review/events")

    assert create_response.status_code == 201
    assert delete_response.status_code == 204
    assert json_response.status_code == 404
    assert "ASR live session not found: delete_asr_live_review" in json_response.text


def test_delete_asr_live_session_reports_exact_delete_scope_without_overclaiming(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="delete_scope_review")
    create_response = client.post("/live/asr/mock/sessions", json=payload)

    delete_response = client.delete("/live/asr/sessions/delete_scope_review")
    json_response = client.get("/live/asr/sessions/delete_scope_review/events")

    assert create_response.status_code == 201
    assert delete_response.status_code == 200
    body = delete_response.json()
    assert body["deleted"] is True
    assert body["session_record_deleted"] is True
    assert body["delete_scope"] == {
        "session_record": "deleted",
        "transcript_events": "deleted_with_session_record",
        "suggestion_cards": "deleted_with_session_record",
        "approach_cards": "deleted_with_session_record",
        "minutes": "deleted_with_session_record",
        "audio": "not_present",
        "exports": "not_tracked_by_live_session_repo",
        "evidence_bundle": "not_tracked_by_live_session_repo",
    }
    assert "cascade" not in body
    assert json_response.status_code == 404


@pytest.mark.parametrize(
    "read_error_type",
    [sqlite3.OperationalError, OSError],
    ids=["sqlite_error", "os_error"],
)
def test_delete_asr_live_session_initial_read_failure_is_structured_and_fail_closed(
    monkeypatch,
    tmp_path,
    read_error_type,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_live_initial_read_failure"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    original_get = app_module.SqliteAsrLiveSessionRepository.get
    audio_delete_called = False

    def fail_initial_read(self, candidate_session_id):
        if candidate_session_id == session_id:
            raise read_error_type("DO_NOT_LEAK_INITIAL_READ_DETAIL")
        return original_get(self, candidate_session_id)

    def track_audio_delete(*args, **kwargs):
        nonlocal audio_delete_called
        audio_delete_called = True
        return "deleted"

    monkeypatch.setattr(
        app_module.SqliteAsrLiveSessionRepository,
        "get",
        fail_initial_read,
    )
    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", track_audio_delete)

    delete_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert delete_response.status_code == 500
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is False
    assert body["delete_scope"]["session_record"] == "read_failed"
    assert body["delete_scope"]["audio"] == "retained_not_attempted"
    assert body["errors"] == [
        {
            "scope": "session_record",
            "code": "read_failed",
            "error_type": read_error_type.__name__,
        }
    ]
    assert "DO_NOT_LEAK_INITIAL_READ_DETAIL" not in delete_response.text
    assert audio_delete_called is False
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "read_error_type",
    [sqlite3.OperationalError, OSError],
    ids=["sqlite_error", "os_error"],
)
def test_delete_session_initial_live_read_failure_is_structured_and_fail_closed(
    monkeypatch,
    tmp_path,
    read_error_type,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_combined_initial_read_failure"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    original_get = app_module.SqliteAsrLiveSessionRepository.get
    audio_delete_called = False

    def fail_initial_read(self, candidate_session_id):
        if candidate_session_id == session_id:
            raise read_error_type("DO_NOT_LEAK_INITIAL_READ_DETAIL")
        return original_get(self, candidate_session_id)

    def track_audio_delete(*args, **kwargs):
        nonlocal audio_delete_called
        audio_delete_called = True
        return "deleted"

    monkeypatch.setattr(
        app_module.SqliteAsrLiveSessionRepository,
        "get",
        fail_initial_read,
    )
    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", track_audio_delete)

    delete_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert delete_response.status_code == 500
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is False
    assert body["live_session_record_deleted"] is False
    assert body["delete_scope"] == {
        "session_record": "retained_not_attempted",
        "live_session_record": "read_failed",
        "audio": "retained_not_attempted",
    }
    assert body["errors"] == [
        {
            "scope": "live_session_record",
            "code": "read_failed",
            "error_type": read_error_type.__name__,
        }
    ]
    assert "DO_NOT_LEAK_INITIAL_READ_DETAIL" not in delete_response.text
    assert audio_delete_called is False
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1


def test_delete_asr_live_session_keeps_audio_when_record_delete_fails(
    monkeypatch,
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_record_failure_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )
    audio_delete_called = False

    def fail_record_delete(self, candidate_session_id):
        if candidate_session_id == session_id:
            raise OSError("synthetic database delete failure")
        return {}

    def track_audio_delete(*args, **kwargs):
        nonlocal audio_delete_called
        audio_delete_called = True
        return "deleted"

    monkeypatch.setattr(
        app_module.SqlitePersistenceCoordinator,
        "delete_live_session",
        fail_record_delete,
    )
    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", track_audio_delete)

    delete_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert delete_response.status_code == 500
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is False
    assert body["delete_scope"]["session_record"] == "retained_after_rollback"
    assert body["delete_scope"]["audio"] == "retained_not_attempted"
    assert audio_delete_called is False
    assert audio_path.is_file()
    assert client.get(f"/live/asr/sessions/{session_id}/events").status_code == 200


def test_delete_asr_live_session_reports_partial_failure_after_audio_delete_error(
    monkeypatch,
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_audio_failure_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )

    def fail_audio_delete(*args, **kwargs):
        raise OSError("synthetic audio delete failure")

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_audio_delete)

    delete_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert delete_response.status_code == 207
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is True
    assert body["delete_scope"]["session_record"] == "deleted"
    assert body["delete_scope"]["audio"] == "cleanup_pending"
    assert body["audio_cleanup_pending"] is True
    assert audio_path.is_file()
    assert client.get(f"/live/asr/sessions/{session_id}/events").status_code == 404


def test_delete_session_reports_partial_failure_without_retaining_database_records(
    monkeypatch,
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_combined_audio_failure_review"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )

    def fail_audio_delete(*args, **kwargs):
        raise OSError("synthetic audio delete failure")

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_audio_delete)

    delete_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert delete_response.status_code == 207
    body = delete_response.json()
    assert body["deleted"] is False
    assert body["session_record_deleted"] is True
    assert body["live_session_record_deleted"] is True
    assert body["delete_scope"] == {
        "session_record": "deleted",
        "live_session_record": "deleted",
        "audio": "cleanup_pending",
    }
    assert body["audio_cleanup_pending"] is True
    assert audio_path.is_file()
    assert client.get(f"/sessions/{session_id}").status_code == 404
    assert client.get(f"/live/asr/sessions/{session_id}/events").status_code == 404


def test_delete_asr_live_session_persists_cleanup_job_and_retries_idempotently(
    monkeypatch,
    tmp_path,
):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "delete_live_cleanup_retry"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
                "original_filename": "DO_NOT_PERSIST_SECRET_NAME.wav",
                "sha256": "DO_NOT_PERSIST_SECRET_HASH",
            },
        },
    )
    original_delete = app_module.audio_assets.delete_audio_asset
    attempts = 0

    def fail_once(data_dir, audio):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic cleanup failure")
        return original_delete(data_dir, audio)

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_once)

    first_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert create_response.status_code == 201
    assert first_response.status_code == 207
    assert first_response.json()["delete_scope"]["audio"] == "cleanup_pending"
    assert first_response.json()["audio_cleanup_pending"] is True
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        pending_json = connection.execute(
            "SELECT audio_json FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
    assert json.loads(pending_json) == {
        "relative_path": f"audio_assets/{session_id}/audio.wav"
    }
    assert "DO_NOT_PERSIST_SECRET" not in pending_json
    assert audio_path.is_file()

    retry_response = client.delete(f"/live/asr/sessions/{session_id}")

    assert retry_response.status_code == 200
    assert retry_response.json()["deleted"] is True
    assert retry_response.json()["delete_scope"]["audio"] == "deleted"
    assert attempts == 2
    assert not audio_path.exists()
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_delete_session_persists_cleanup_job_and_retries_idempotently(
    monkeypatch,
    tmp_path,
):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "delete_bundle_cleanup_retry"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    audio_path = tmp_path / "audio_assets" / session_id / "audio.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"synthetic-audio")
    app_module.SqliteAsrLiveSessionRepository(tmp_path).update(
        session_id,
        lambda record: {
            **record,
            "audio": {
                "saved": True,
                "relative_path": str(audio_path.relative_to(tmp_path)),
            },
        },
    )
    original_delete = app_module.audio_assets.delete_audio_asset
    attempts = 0

    def fail_once(data_dir, audio):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic cleanup failure")
        return original_delete(data_dir, audio)

    monkeypatch.setattr(app_module.audio_assets, "delete_audio_asset", fail_once)

    first_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert first_response.status_code == 207
    assert first_response.json()["delete_scope"]["audio"] == "cleanup_pending"
    assert first_response.json()["audio_cleanup_pending"] is True
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1

    retry_response = client.delete(f"/sessions/{session_id}")

    assert retry_response.status_code == 204
    assert attempts == 2
    assert not audio_path.exists()
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_delete_session_rolls_back_both_rows_and_cleanup_job_when_live_delete_fails(
    tmp_path,
):
    client = TestClient(
        create_app(data_dir=tmp_path),
        raise_server_exceptions=False,
    )
    session_id = "delete_bundle_atomic_rollback"
    session_payload = _payload()
    session_payload["session_id"] = session_id
    session_create_response = client.post("/sessions", json=session_payload)
    live_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        connection.execute(
            "CREATE TRIGGER fail_live_delete BEFORE DELETE ON asr_live_sessions "
            "WHEN OLD.session_id = 'delete_bundle_atomic_rollback' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic live delete failure'); END"
        )

    delete_response = client.delete(f"/sessions/{session_id}")

    assert session_create_response.status_code == 201
    assert live_create_response.status_code == 201
    assert delete_response.status_code == 500
    assert delete_response.json()["delete_scope"] == {
        "session_record": "retained_after_rollback",
        "live_session_record": "retained_after_rollback",
        "audio": "retained_not_attempted",
    }
    with sqlite3.connect(tmp_path / "meeting_copilot.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM asr_live_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_audio_cleanup WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_create_asr_live_session_rejects_unsafe_session_id_with_json_persistence(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="../bad")

    response = client.post("/live/asr/mock/sessions", json=payload)

    assert response.status_code == 422
    assert "unsafe session_id" in response.text


def test_asr_live_draft_review_json_summarizes_audit_record_without_llm(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="live_asr_draft_review"),
    )

    response = client.get("/live/asr/sessions/live_asr_draft_review/draft")

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "live_asr_draft_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["review_type"] == "asr_live_draft"
    assert body["is_formal_report"] is False
    assert body["llm_call_status"] == "not_called"
    assert body["transcript_text"] == "先灰度 10%。先灰度 5%，不是 10%。谁负责回滚？如果错误率超过 0.1% 就回滚。张三下周三补充兼容性测试用例。"
    assert [segment["id"] for segment in body["transcript_segments"]] == [
        "asr_seg_001",
        "asr_seg_001_rev1",
        "asr_seg_002",
        "asr_seg_003",
        "asr_seg_004",
    ]
    assert [item["target_type"] for item in body["state_candidates"]] == [
        "DecisionCandidate",
        "DecisionCandidate",
        "OpenQuestion",
        "Risk",
        "ActionItem",
    ]
    assert body["state_candidates"][2]["state_item"]["question"] == "谁负责回滚？"
    assert body["state_candidates"][3]["state_item"]["description"] == "如果错误率超过 0.1% 就回滚。"
    assert body["state_candidates"][4]["state_item"]["description"] == "张三下周三补充兼容性测试用例。"
    assert [item["scheduler_event_type"] for item in body["scheduler_decisions"]] == [
        "llm_candidate_queued",
        "llm_candidate_skipped",
        "llm_candidate_skipped",
        "llm_candidate_skipped",
        "llm_candidate_skipped",
    ]
    assert {item["llm_call_status"] for item in body["scheduler_decisions"]} == {
        "not_called"
    }
    assert [item["gap_rule_id"] for item in body["suggestion_candidates"]] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {item["candidate_policy_version"] for item in body["suggestion_candidates"]} == {
        "asr-candidate-policy.v1"
    }
    assert {item["confidence_source"] for item in body["suggestion_candidates"]} == {
        "local_deterministic_heuristic"
    }
    assert [item["confidence"] for item in body["suggestion_candidates"]] == [
        0.9,
        0.9,
        0.9,
        0.9,
        0.9,
    ]
    assert [item["confidence_level"] for item in body["suggestion_candidates"]] == [
        "high",
        "high",
        "high",
        "high",
        "high",
    ]
    assert [item["degradation_reasons"] for item in body["suggestion_candidates"]] == [
        [],
        [],
        [],
        [],
        [],
    ]
    assert {item["llm_call_status"] for item in body["suggestion_candidates"]} == {
        "not_called"
    }
    assert {item["card_status"] for item in body["suggestion_candidates"]} == {
        "not_created"
    }
    assert [item["gap_rule_id"] for item in body["llm_request_drafts"]] == [
        "release.rollback.owner.required",
        "release.rollback.owner.required",
        "open.question.followup",
        "risk.rollback.validation",
        "action.owner.deadline.confirmation",
    ]
    assert {item["request_status"] for item in body["llm_request_drafts"]} == {
        "draft_only"
    }
    assert {item["schema_status"] for item in body["llm_request_drafts"]} == {
        "not_generated"
    }
    assert {item["llm_call_status"] for item in body["llm_request_drafts"]} == {
        "not_called"
    }
    assert {item["card_status"] for item in body["llm_request_drafts"]} == {
        "not_created"
    }
    assert body["llm_request_drafts"][0]["candidate_confidence_level"] == "high"
    assert body["llm_request_drafts"][0]["candidate_degradation_reasons"] == []
    assert body["evaluation_summary"]["final_event_count"] == 4
    assert body["evaluation_summary"]["revision_event_count"] == 1
    assert body["suggestion_cards"] == []
    assert body["llm_schema_results"] == []
    assert "Draft only" in body["warnings"][0]


def test_asr_live_draft_review_markdown_is_marked_as_non_formal(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="live_asr_draft_review"),
    )

    response = client.get("/live/asr/sessions/live_asr_draft_review/draft.md")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# Live ASR Draft Review: live_asr_draft_review" in response.text
    assert "Draft only; not a formal gated meeting report." in response.text
    assert "## Transcript Draft" in response.text
    assert "先灰度 5%，不是 10%。" in response.text
    assert "## State Candidates" in response.text
    assert "OpenQuestion" in response.text
    assert "谁负责回滚？" in response.text
    assert "Risk" in response.text
    assert "如果错误率超过 0.1% 就回滚。" in response.text
    assert "ActionItem" in response.text
    assert "张三下周三补充兼容性测试用例。" in response.text
    assert "## Scheduler Decisions" in response.text
    assert "llm_candidate_queued" in response.text
    assert "llm_candidate_skipped" in response.text
    assert "not_called" in response.text
    assert "## Suggestion Candidates" in response.text
    assert "confidence high/0.9" in response.text
    assert "asr-candidate-policy.v1" in response.text
    assert "local_deterministic_heuristic" in response.text
    assert "risk.rollback.validation" in response.text
    assert "action.owner.deadline.confirmation" in response.text
    assert "not_created" in response.text
    assert "## LLM Request Drafts" in response.text
    assert "draft_only" in response.text
    assert "not_generated" in response.text
    assert "llm_suggestion_card_draft" in response.text
    assert "asr_suggestion_candidate_asr_action_event_asr_seg_004" in response.text
    assert "asr_action_event_asr_seg_004" in response.text
    assert "asr_ev_asr_seg_004" in response.text
    assert "asr_seg_004" in response.text
    assert "ActionItem asr_action_asr_seg_004 from asr_seg_004 using asr_ev_asr_seg_004" in response.text
    assert "## Stream Summary" in response.text


def test_create_asr_live_session_rejects_unknown_streaming_event_type():
    client = TestClient(create_app())
    payload = _asr_live_payload()
    payload["streaming_events"][1]["event_type"] = "draft"

    response = client.post("/live/asr/mock/sessions", json=payload)

    assert response.status_code == 422
    assert "unsupported ASR streaming event_type: draft" in response.text



def test_list_demo_fixtures_exposes_engineering_and_boundary_metadata():
    client = TestClient(create_app())

    response = client.get("/demo/fixtures")

    assert response.status_code == 200
    fixture_ids = {fixture["id"] for fixture in response.json()["fixtures"]}
    assert {
        "api-review",
        "release-review",
        "business-sync",
        "product-priority",
        "mixed-terms-sync",
        "schema-degradation-review",
    }.issubset(fixture_ids)
    release = next(
        fixture
        for fixture in response.json()["fixtures"]
        if fixture["id"] == "release-review"
    )
    assert release["source"] == "fixture"
    assert release["scenario_type"] == "release_review"
    assert release["is_engineering_meeting"] is True
    assert release["expected_gap_rule_count"] == 2
    assert "AC-PCWEB-009" in release["expected_gate_tags"]

    mixed = next(
        fixture
        for fixture in response.json()["fixtures"]
        if fixture["id"] == "mixed-terms-sync"
    )
    assert mixed["is_engineering_meeting"] is False
    assert mixed["expected_gap_rule_count"] == 0
    assert "AC-PCWEB-014" in mixed["expected_gate_tags"]

    degradation = next(
        fixture
        for fixture in response.json()["fixtures"]
        if fixture["id"] == "schema-degradation-review"
    )
    assert degradation["is_engineering_meeting"] is True
    assert degradation["expected_gap_rule_count"] == 1
    assert "AC-PCWEB-019" in degradation["expected_gate_tags"]


def test_create_session_from_demo_fixture_returns_evaluation_summary():
    client = TestClient(create_app())

    response = client.post(
        "/demo/fixtures/release-review/sessions",
        json={"session_id": "demo_release_review_custom"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"] == {
        "fixture_id": "release-review",
        "source": "fixture",
        "replay_mode": "demo_fixture",
    }
    snapshot = body["snapshot"]
    assert snapshot["session_id"] == "demo_release_review_custom"
    assert snapshot["quality"]["suggestion_card_count"] == 2
    assert snapshot["suggestion_cards"][0]["state_event_ids"] == ["event_003", "event_004"]
    assert snapshot["suggestion_cards"][0]["latency_ms"] <= 30000
    evaluation = body["evaluation_summary"]
    assert evaluation["source"] == "fixture"
    assert evaluation["gate_version"] == "web_mvp_fixture.v1"
    assert evaluation["is_engineering_meeting"] is True
    assert evaluation["passes_minimum_gate"] is True
    assert evaluation["failures"] == []
    assert evaluation["state_counts"]["action_items"] == 1
    assert evaluation["effective_card_count"] == 2
    assert evaluation["gap_rule_count"] == 2
    assert set(evaluation["gap_rule_ids"]) == {
        "release.rollback.owner.required",
        "release.rollback.drill.required",
    }


def test_demo_fixture_session_exposes_replay_event_timeline():
    client = TestClient(create_app())
    created = client.post(
        "/demo/fixtures/release-review/sessions",
        json={"session_id": "demo_release_review_events"},
    )

    response = client.get("/sessions/demo_release_review_events/events")

    assert created.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "demo_release_review_events"
    assert body["source"] == "replay_snapshot"
    events = body["events"]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["at_ms"] for event in events] == sorted(event["at_ms"] for event in events)
    assert {event["event_type"] for event in events} >= {
        "transcript_final",
        "state_event",
        "suggestion_card",
        "evaluation_summary",
    }
    transcript_event = next(event for event in events if event["event_type"] == "transcript_final")
    assert transcript_event["payload"]["segment_id"] == "seg_001"
    card_event = next(
        event
        for event in events
        if event["event_type"] == "suggestion_card"
        and event["payload"]["card_id"] == "card_001"
    )
    assert card_event["at_ms"] == 23100
    assert card_event["payload"]["gap_rule_id"] == "release.rollback.owner.required"
    evaluation_event = events[-1]
    assert evaluation_event["event_type"] == "evaluation_summary"
    assert evaluation_event["payload"]["passes_minimum_gate"] is True


def test_demo_fixture_session_exposes_llm_scheduler_trace_events():
    client = TestClient(create_app())
    created = client.post(
        "/demo/fixtures/release-review/sessions",
        json={"session_id": "demo_release_review_llm_trace"},
    )

    response = client.get("/sessions/demo_release_review_llm_trace/events")

    snapshot = created.json()["snapshot"]
    assert response.status_code == 200
    events = response.json()["events"]
    event_types = [event["event_type"] for event in events]
    cards = snapshot["suggestion_cards"]
    assert event_types.count("llm_scheduled") == len(cards)
    assert event_types.count("llm_schema_result") == len(cards)

    events_by_type_and_card = {
        (event["event_type"], event["payload"].get("card_id")): event
        for event in events
        if event["event_type"] in {"llm_scheduled", "llm_schema_result", "suggestion_card"}
    }
    state_events_by_id = {
        event["payload"]["event_id"]: event
        for event in events
        if event["event_type"] == "state_event"
    }
    for card in cards:
        card_id = card["id"]
        scheduled = events_by_type_and_card[("llm_scheduled", card_id)]
        schema_result = events_by_type_and_card[("llm_schema_result", card_id)]
        suggestion_event = events_by_type_and_card[("suggestion_card", card_id)]
        assert scheduled["at_ms"] == card["state_event_at_ms"]
        assert scheduled["payload"] == {
            "card_id": card_id,
            "gap_rule_id": card["gap_rule_id"],
            "trigger_source": card["trigger_source"],
            "trigger_reason": card["trigger_reason"],
            "segment_batch": card["segment_batch"],
            "state_event_ids": card["state_event_ids"],
            "prompt_version": card["prompt_version"],
            "model": card["model"],
        }
        assert schema_result["at_ms"] == card["card_created_at_ms"]
        assert schema_result["payload"] == {
            "card_id": card_id,
            "schema_result": card["schema_result"],
            "show_or_silence_decision": card["show_or_silence_decision"],
            "usage": card["usage"],
            "latency_ms": card["latency_ms"],
        }
        latest_state_event = max(
            state_events_by_id[event_id]
            for event_id in card["state_event_ids"]
            if state_events_by_id[event_id]["at_ms"] == scheduled["at_ms"]
        )
        assert latest_state_event["sequence"] < scheduled["sequence"]
        if schema_result["at_ms"] == suggestion_event["at_ms"]:
            assert schema_result["sequence"] < suggestion_event["sequence"]


def test_replay_events_carry_source_boundary_in_json_and_sse():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/api-review/sessions",
        json={"session_id": "demo_api_review_event_boundary"},
    )

    json_response = client.get("/sessions/demo_api_review_event_boundary/events")
    sse_response = client.get("/sessions/demo_api_review_event_boundary/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert {event["source"] for event in events} == {"replay_snapshot"}
    assert {event["trace_kind"] for event in events} == {"replay_derived"}

    sse_events = [
        __import__("json").loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events


def test_create_mock_live_session_from_fixture_returns_live_event_source_boundary():
    client = TestClient(create_app())

    response = client.post(
        "/live/mock/fixtures/release-review/sessions",
        json={"session_id": "live_release_review_custom"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"] == {
        "fixture_id": "release-review",
        "source": "fixture",
        "replay_mode": "demo_fixture",
        "live_mode": "mock_fixture_stream",
    }
    assert body["snapshot"]["session_id"] == "live_release_review_custom"
    assert body["event_source"] == {
        "source": "live_mock_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "is_mock": True,
    }
    events = body["live_events"]
    assert {event["source"] for event in events} == {"live_mock_stream"}
    assert {event["trace_kind"] for event in events} == {"live_event"}
    assert {event["event_type"] for event in events} >= {
        "transcript_partial",
        "transcript_final",
        "state_event",
        "scheduler_event",
        "llm_schema_result",
        "suggestion_card",
        "evaluation_summary",
    }
    assert all(event["source"] != "replay_snapshot" for event in events)
    partial = next(event for event in events if event["event_type"] == "transcript_partial")
    assert partial["payload"]["is_final"] is False
    scheduler = next(event for event in events if event["event_type"] == "scheduler_event")
    assert scheduler["payload"]["scheduler_event_type"] == "llm_scheduled"


def test_mock_live_session_events_json_and_sse_use_live_boundary():
    client = TestClient(create_app())
    client.post(
        "/live/mock/fixtures/api-review/sessions",
        json={"session_id": "live_api_review_events"},
    )

    json_response = client.get("/live/sessions/live_api_review_events/events")
    sse_response = client.get("/live/sessions/live_api_review_events/events.sse")

    assert json_response.status_code == 200
    body = json_response.json()
    assert body["session_id"] == "live_api_review_events"
    assert body["source"] == "live_mock_stream"
    assert body["trace_kind"] == "live_event"
    events = body["events"]
    assert {event["source"] for event in events} == {"live_mock_stream"}
    assert "transcript_partial" in [event["event_type"] for event in events]
    assert "transcript_revision" in [event["event_type"] for event in events]
    assert "suggestion_invalidated" in [event["event_type"] for event in events]
    assert "scheduler_event" in [event["event_type"] for event in events]
    revision = next(event for event in events if event["event_type"] == "transcript_revision")
    assert revision["payload"]["segment_id"] == "seg_002_rev1"
    assert revision["payload"]["supersedes_segment_id"] == "seg_002"
    assert revision["payload"]["evidence_spans"] == [
        {
            "id": "ev_002_rev1",
            "segment_id": "seg_002_rev1",
            "start_ms": 6200,
            "end_ms": 10500,
            "quote": "老版本调用方只需要兼容 v2，不再兼容两个版本。",
            "status": "active",
            "revision_of": "ev_002",
        }
    ]
    assert revision["payload"]["superseded_evidence_spans"] == [
        {
            "id": "ev_002",
            "segment_id": "seg_002",
            "start_ms": 6200,
            "end_ms": 10500,
            "quote": "老版本调用方要兼容两个版本。",
            "status": "superseded",
            "replaced_by": "ev_002_rev1",
        }
    ]
    invalidated = next(event for event in events if event["event_type"] == "suggestion_invalidated")
    assert invalidated["payload"]["card_id"] == "card_002"
    assert invalidated["payload"]["reason"] == "stale_evidence"
    assert invalidated["payload"]["invalidated_by_event_id"] == "transcript_revision:seg_002_rev1"
    assert invalidated["payload"]["stale_evidence_span_ids"] == ["ev_002"]
    assert invalidated["payload"]["replacement_evidence_span_ids"] == ["ev_002_rev1"]
    assert invalidated["payload"]["card"]["show_or_silence_decision"] == "silence"
    assert invalidated["payload"]["card"]["invalidation_reason"] == "stale_evidence"

    assert sse_response.status_code == 200
    assert sse_response.headers["content-type"].startswith("text/event-stream")
    sse_events = [
        json.loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events
    assert "event: transcript_partial" in sse_response.text
    assert "event: transcript_revision" in sse_response.text
    assert "event: suggestion_invalidated" in sse_response.text
    assert "event: scheduler_event" in sse_response.text


def test_api_review_fixture_preserves_revision_segment_after_core_gate():
    client = TestClient(create_app())

    response = client.post(
        "/live/mock/fixtures/api-review/sessions",
        json={"session_id": "live_api_review_revision_snapshot"},
    )

    assert response.status_code == 201
    snapshot = response.json()["snapshot"]
    revision_segment = next(
        segment
        for segment in snapshot["transcript"]["segments"]
        if segment["id"] == "seg_002_rev1"
    )
    revision_evidence = next(
        evidence
        for evidence in snapshot["transcript"]["evidence_spans"]
        if evidence["id"] == "ev_002_rev1"
    )
    assert revision_segment["revision_of"] == "seg_002"
    assert revision_evidence["revision_of"] == "ev_002"
    assert "replaced_by" not in revision_evidence


def test_mock_live_session_unknown_fixture_returns_404():
    client = TestClient(create_app())

    response = client.post("/live/mock/fixtures/missing/sessions", json={})

    assert response.status_code == 404
    assert "fixture not found" in response.text


def test_demo_fixture_session_exposes_sse_replay_stream():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/api-review/sessions",
        json={"session_id": "demo_api_review_sse"},
    )

    response = client.get("/sessions/demo_api_review_sse/events.sse")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: transcript_final" in response.text
    assert "event: llm_scheduled" in response.text
    assert "event: llm_schema_result" in response.text
    assert "event: suggestion_card" in response.text
    assert "event: evaluation_summary" in response.text
    assert '"gap_rule_id":"api.change.monitoring.required"' in response.text
    assert '"schema_result":"valid"' in response.text
    assert '"total_tokens":336' in response.text


def test_schema_degradation_fixture_records_failures_without_strong_suggestions():
    client = TestClient(create_app())

    response = client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation"},
    )

    assert response.status_code == 201
    body = response.json()
    snapshot = body["snapshot"]
    evaluation = body["evaluation_summary"]
    cards = snapshot["suggestion_cards"]
    blocking_cards = [
        card
        for card in cards
        if card["schema_result"] in {"failed", "timeout", "invalid"}
    ]
    assert len(blocking_cards) == 3
    assert all(card["show_or_silence_decision"] != "show" for card in blocking_cards)
    assert evaluation["passes_minimum_gate"] is True
    assert evaluation["effective_card_count"] == 1
    assert evaluation["schema_blocked_count"] == 3
    assert evaluation["silenced_card_count"] == 3
    assert evaluation["schema_result_counts"] == {
        "failed": 1,
        "invalid": 1,
        "timeout": 1,
        "valid": 1,
    }

    events = body["replay_events"]
    silenced_events = [
        event for event in events if event["event_type"] == "suggestion_silenced"
    ]
    shown_events = [
        event for event in events if event["event_type"] == "suggestion_card"
    ]
    assert len(silenced_events) == 3
    assert len(shown_events) == 1
    assert {
        event["payload"]["schema_result"]
        for event in silenced_events
    } == {"failed", "timeout", "invalid"}
    assert all(
        event["payload"]["show_or_silence_decision"] != "show"
        for event in silenced_events
    )


def test_schema_degradation_replay_stream_exposes_silenced_events_in_json_and_sse():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_events"},
    )

    json_response = client.get("/sessions/demo_schema_degradation_events/events")
    sse_response = client.get("/sessions/demo_schema_degradation_events/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    event_types = [event["event_type"] for event in events]
    assert event_types.count("llm_schema_result") == 4
    assert event_types.count("suggestion_silenced") == 3
    assert event_types.count("suggestion_card") == 1
    for event in events:
        if event["event_type"] == "suggestion_silenced":
            card_id = event["payload"]["card_id"]
            schema_event = next(
                item
                for item in events
                if item["event_type"] == "llm_schema_result"
                and item["payload"]["card_id"] == card_id
            )
            assert schema_event["sequence"] < event["sequence"]

    sse_events = [
        json.loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert sse_events == events
    assert "event: suggestion_silenced" in sse_response.text
    assert '"schema_result":"timeout"' in sse_response.text


def test_schema_degradation_replay_evaluation_preserves_fixture_gate_metadata():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_gate_metadata"},
    )

    response = client.get("/sessions/demo_schema_degradation_gate_metadata/events")

    assert response.status_code == 200
    evaluation_event = next(
        event
        for event in response.json()["events"]
        if event["event_type"] == "evaluation_summary"
    )
    assert evaluation_event["payload"]["source"] == "replay_snapshot"
    assert evaluation_event["payload"]["expected_gap_rule_count"] == 1
    assert evaluation_event["payload"]["gap_rule_count"] == 1
    assert evaluation_event["payload"]["passes_minimum_gate"] is True
    assert evaluation_event["payload"]["failures"] == []


def test_update_card_status_rejects_silenced_schema_card_without_mutating_record():
    client = TestClient(create_app())
    created = client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_status_guard"},
    )
    silenced_card = next(
        card
        for card in created.json()["snapshot"]["suggestion_cards"]
        if card["schema_result"] == "failed"
    )

    rejected = client.patch(
        f"/sessions/demo_schema_degradation_status_guard/cards/{silenced_card['id']}/status",
        json={"status": "kept"},
    )
    fetched = client.get("/sessions/demo_schema_degradation_status_guard")

    assert rejected.status_code == 422
    assert "silenced suggestion card cannot be updated" in rejected.text
    assert fetched.status_code == 200
    still_silenced = next(
        card
        for card in fetched.json()["suggestion_cards"]
        if card["id"] == silenced_card["id"]
    )
    assert still_silenced["status"] == "new"


def test_engineering_demo_fixtures_cover_multiple_gap_rules():
    client = TestClient(create_app())

    for fixture_id in ("api-review", "release-review"):
        response = client.post(f"/demo/fixtures/{fixture_id}/sessions", json={})

        assert response.status_code == 201
        body = response.json()
        evaluation = body["evaluation_summary"]
        assert evaluation["is_engineering_meeting"] is True
        assert evaluation["passes_minimum_gate"] is True
        assert evaluation["effective_card_count"] >= 2
        assert evaluation["gap_rule_count"] >= 2
        assert body["snapshot"]["quality"]["suggestion_card_count"] >= 2


def test_non_engineering_demo_fixtures_do_not_emit_engineering_cards():
    client = TestClient(create_app())

    for fixture_id in ("business-sync", "product-priority", "mixed-terms-sync"):
        response = client.post(f"/demo/fixtures/{fixture_id}/sessions", json={})

        assert response.status_code == 201
        body = response.json()
        evaluation = body["evaluation_summary"]
        assert evaluation["is_engineering_meeting"] is False
        assert evaluation["passes_minimum_gate"] is True
        assert evaluation["suggestion_card_count"] == 0
        assert evaluation["effective_card_count"] == 0
        assert evaluation["gap_rule_count"] == 0
        assert body["snapshot"]["suggestion_cards"] == []
        assert body["snapshot"]["quality"]["is_engineering_meeting"] is False


def test_create_session_from_unknown_demo_fixture_returns_404():
    client = TestClient(create_app())

    response = client.post("/demo/fixtures/missing/sessions", json={})

    assert response.status_code == 404
    assert "fixture not found" in response.text


def test_create_and_read_session_snapshot():
    client = TestClient(create_app())

    created = client.post("/sessions", json=_payload())

    assert created.status_code == 201
    snapshot = created.json()
    assert snapshot["session_id"] == "meeting_001"
    assert snapshot["suggestion_cards"][0]["status"] == "new"
    assert snapshot["quality"]["suggestion_card_count"] == 1

    fetched = client.get("/sessions/meeting_001")

    assert fetched.status_code == 200
    assert fetched.json() == snapshot


def test_create_rejects_invalid_session_without_persisting_bad_record():
    client = TestClient(create_app())
    payload = _payload()
    payload["analysis"]["suggestion_cards"][0].pop("state_refs")

    rejected = client.post("/sessions", json=payload)
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert "card_001 missing state_refs" in rejected.text
    assert fetched.status_code == 404


def test_create_rejects_strong_card_when_degraded_without_persisting_record():
    client = TestClient(create_app())
    payload = _payload()
    payload["degradation_reasons"] = ["asr_low_confidence"]

    rejected = client.post("/sessions", json=payload)
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert "degradation blocks strong suggestion card" in rejected.text
    assert fetched.status_code == 404


def test_create_rejects_strong_card_using_stale_evidence_without_persisting_record():
    client = TestClient(create_app())
    payload = _payload()
    payload["transcript_report"]["evidence_spans"][1]["status"] = "stale"
    payload["transcript_report"]["evidence_spans"][1]["replaced_by"] = "ev_002_rev"

    rejected = client.post("/sessions", json=payload)
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert "card_001 references stale evidence_span_id: ev_002" in rejected.text
    assert fetched.status_code == 404


def test_create_allows_non_strong_audit_card_using_stale_evidence():
    client = TestClient(create_app())
    payload = _payload()
    payload["transcript_report"]["evidence_spans"][1]["status"] = "stale"
    payload["transcript_report"]["evidence_spans"][1]["replaced_by"] = "ev_002_rev"
    card = payload["analysis"]["suggestion_cards"][0]
    card["show_or_silence_decision"] = "draft"
    card["status"] = "dismissed"

    created = client.post("/sessions", json=payload)

    assert created.status_code == 201
    snapshot = created.json()
    evidence = next(
        item
        for item in snapshot["transcript"]["evidence_spans"]
        if item["id"] == "ev_002"
    )
    assert evidence["status"] == "stale"
    assert evidence["replaced_by"] == "ev_002_rev"
    assert snapshot["suggestion_cards"][0]["show_or_silence_decision"] == "draft"


def test_update_card_status_updates_snapshot():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    response = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "kept"},
    )

    assert response.status_code == 200
    assert response.json()["suggestion_cards"][0]["status"] == "kept"


def test_update_card_status_blocks_overwriting_negative_feedback_without_mutating_record():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    marked_late = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "too_late"},
    )
    rejected = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "kept"},
    )
    fetched = client.get("/sessions/meeting_001")

    assert marked_late.status_code == 200
    assert rejected.status_code == 409
    assert "card status transition not allowed" in rejected.text
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "too_late"


def test_update_card_status_rejects_unknown_status():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    response = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "snoozed"},
    )

    assert response.status_code == 422
    assert "unsupported suggestion card status" in response.text


def test_update_card_status_rejects_unknown_status_without_mutating_record():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    rejected = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "snoozed"},
    )
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 422
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "new"


def test_update_card_status_rejects_unknown_card_id_without_mutating_record():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    rejected = client.patch(
        "/sessions/meeting_001/cards/card_missing/status",
        json={"status": "dismissed"},
    )
    fetched = client.get("/sessions/meeting_001")

    assert rejected.status_code == 404
    assert "card not found" in rejected.text
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "new"


def test_shadow_report_feedback_ingestion_api_updates_report_readiness():
    client = TestClient(create_app())

    response = client.post(
        "/shadow-reports/feedback-ingestions",
        json={
            "candidate_report": _shadow_candidate_report_for_feedback_ingestion(),
            "feedback_entries": [
                {"candidate_id": "cand-001", "label": "useful"},
                {"candidate_id": "cand-002", "label": "would_have_asked"},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["drv_id"] == "DRV-038"
    assert payload["feedback_ingestion_status"] == "shadow_report_feedback_ingested"
    assert payload["updated_candidate_report"]["feedback_summary"]["useful_or_would_have_asked_count"] == 2
    assert payload["updated_candidate_report"]["final_decision"]["decision"] == "go"
    assert payload["readiness_report"]["final_decision_readiness_status"] == "go_supported_by_feedback"
    assert payload["readiness_report"]["export_readiness_status"] == "ready_for_shadow_test_export"
    assert payload["safe_to_access_microphone_now"] is False
    assert payload["safe_to_call_remote_asr_now"] is False
    assert payload["safe_to_call_llm_now"] is False


def test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path():
    client = TestClient(create_app())

    response = client.post(
        "/shadow-reports/feedback-ingestions",
        json={
            "candidate_report_path": "configs/local/shadow-report.json",
            "feedback_entries": [
                {"candidate_id": "cand-001", "label": "useful"},
            ],
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["feedback_ingestion_status"] == "blocked_by_path_guard"
    assert "candidate_report_path is blocked: configs/local" in payload["detail"]["validation_errors"]


def test_export_markdown_report():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    response = client.get("/sessions/meeting_001/report.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "Meeting meeting_001" in response.text
    assert "evidence: ev_001" in response.text


def test_export_markdown_report_separates_silenced_schema_records():
    client = TestClient(create_app())
    client.post(
        "/demo/fixtures/schema-degradation-review/sessions",
        json={"session_id": "demo_schema_degradation_report"},
    )

    response = client.get("/sessions/demo_schema_degradation_report/report.md")

    assert response.status_code == 200
    assert "## Suggestion Cards" in response.text
    assert "补齐回滚演练安排" in response.text
    assert "## Silenced Suggestion Records" in response.text
    assert "[silence; schema: failed]" in response.text
    assert "[silence; schema: timeout]" in response.text
    assert "[silence; schema: invalid]" in response.text


class _GateFailingRepository:
    def snapshot(self, session_id):
        raise ValueError(f"stored session failed gate: {session_id}")


def test_read_session_converts_stored_gate_failure_to_422():
    client = TestClient(create_app(repository=_GateFailingRepository()))

    response = client.get("/sessions/bad_session")

    assert response.status_code == 422
    assert "stored session failed gate" in response.text


def test_export_report_converts_stored_gate_failure_to_422():
    client = TestClient(create_app(repository=_GateFailingRepository()))

    response = client.get("/sessions/bad_session/report.md")

    assert response.status_code == 422
    assert "stored session failed gate" in response.text


def test_delete_session_removes_it():
    client = TestClient(create_app())
    client.post("/sessions", json=_payload())

    deleted = client.delete("/sessions/meeting_001")
    missing = client.get("/sessions/meeting_001")

    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_json_repository_persists_session_and_card_status_across_instances(tmp_path):
    repository = JsonFileSessionRepository(tmp_path)
    client = TestClient(create_app(repository=repository))
    client.post("/sessions", json=_payload())

    updated = client.patch(
        "/sessions/meeting_001/cards/card_001/status",
        json={"status": "kept"},
    )
    reloaded_client = TestClient(
        create_app(repository=JsonFileSessionRepository(tmp_path))
    )
    fetched = reloaded_client.get("/sessions/meeting_001")

    assert updated.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["suggestion_cards"][0]["status"] == "kept"
    assert (tmp_path / "sessions" / "meeting_001.json").is_file()


def test_json_repository_delete_removes_session_file(tmp_path):
    client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))
    client.post("/sessions", json=_payload())
    session_file = tmp_path / "sessions" / "meeting_001.json"

    deleted = client.delete("/sessions/meeting_001")
    missing = client.get("/sessions/meeting_001")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert not session_file.exists()


def test_json_repository_delete_rejects_unsafe_session_id(tmp_path):
    client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))

    rejected = client.delete("/sessions/..escape")

    assert rejected.status_code == 422
    assert "unsafe session_id" in rejected.text


def test_json_repository_rejects_unsafe_session_id_without_writing_outside_data_dir(
    tmp_path,
):
    payload = _payload()
    payload["session_id"] = "../escape"
    client = TestClient(create_app(repository=JsonFileSessionRepository(tmp_path)))

    rejected = client.post("/sessions", json=payload)

    assert rejected.status_code == 422
    assert "unsafe session_id" in rejected.text
    assert list((tmp_path / "sessions").glob("*.json")) == []
    assert not (tmp_path.parent / "escape.json").exists()


def test_create_app_uses_single_sqlite_database_when_data_dir_is_provided(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/sessions", json=_payload())

    assert response.status_code == 201
    assert (tmp_path / "meeting_copilot.db").is_file()
    assert not (tmp_path / "meeting_copilot.db").is_dir()

    reloaded = TestClient(create_app(data_dir=tmp_path)).get("/sessions/meeting_001")
    assert reloaded.status_code == 200


def test_create_app_closes_all_created_sqlite_repositories_on_shutdown(tmp_path):
    app = create_app(data_dir=tmp_path)
    repositories = app.state.sqlite_repositories

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert all(repository.closed is False for repository in repositories)

    assert repositories
    assert all(repository.closed is True for repository in repositories)
    db_path = tmp_path / "meeting_copilot.db"
    moved_path = tmp_path / "meeting_copilot.after_shutdown.db"
    db_path.replace(moved_path)
    moved_path.unlink()
    assert not moved_path.exists()


def test_create_app_shuts_down_process_resident_funasr_worker(monkeypatch):
    shutdown_calls = []
    monkeypatch.setattr(
        app_module.asr_stream,
        "shutdown_funasr_resident_manager",
        lambda: shutdown_calls.append("shutdown"),
    )

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200

    assert shutdown_calls == ["shutdown"]


def test_shutdown_cancels_active_capture_tasks_and_clears_registry():
    async def scenario():
        active_tasks = {}
        started = asyncio.Event()

        async def active_capture():
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(active_capture())
        active_tasks["meeting-shutdown"] = {task}
        await started.wait()

        cancelled_count = await app_module._cancel_active_capture_tasks(active_tasks)

        assert cancelled_count == 1
        assert task.done()
        assert task.cancelled()
        assert active_tasks == {}

    asyncio.run(scenario())


def test_new_app_does_not_inherit_previous_runtime_degradation():
    controller = get_degradation_controller()
    controller.set_level(3, "asr_sidecar_crashed: synthetic previous app")

    with TestClient(create_app()) as client:
        response = client.get("/degradation/status")

    assert response.status_code == 200
    assert response.json()["level"] == 0


def test_create_app_fails_closed_when_sqlite_migration_fails(monkeypatch, tmp_path):
    def fail_migration(*args, **kwargs):
        raise OSError("migration exploded")

    monkeypatch.setattr(app_module, "migrate_json_to_sqlite", fail_migration)

    with pytest.raises(RuntimeError, match="SQLite migration failed: migration exploded"):
        create_app(data_dir=tmp_path)
