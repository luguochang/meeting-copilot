import builtins
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import urllib.request

from fastapi.testclient import TestClient

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.repository import JsonFileSessionRepository


REPO_ROOT = Path(__file__).resolve().parents[4]

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
    original_urlopen = urllib.request.urlopen

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



def test_create_asr_live_session_events_json_and_sse_use_asr_boundary():
    client = TestClient(create_app())

    create_response = client.post("/live/asr/mock/sessions", json=_asr_live_payload())

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["session_id"] == "local_asr_stream_review"
    assert created["event_source"] == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "local_mock_asr",
        "is_mock": True,
    }
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
    assert created["event_source"] == {
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "transport": "sse",
        "provider": "sherpa_onnx_streaming",
        "is_mock": False,
    }
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
    assert response.json() == {
        "session_id": "local_asr_execution_disabled_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "executor_mode": "disabled",
        "run_count": 0,
        "runs": [],
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
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_no_cfg"),
    )
    response = client.post(
        "/live/asr/sessions/local_asr_execution_enabled_no_cfg/llm-execution-runs",
        json={"mode": "enabled"},
    )
    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "not configured" in response.text


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
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_enabled_run"),
    )
    assert create_response.status_code == 201
    response = client.post(
        "/live/asr/sessions/local_asr_execution_enabled_run/llm-execution-runs",
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
    assert run["llm_usage"]["total_tokens"] == 130
    assert fake.calls == body["run_count"]


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


def test_create_app_uses_json_repository_when_data_dir_is_provided(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/sessions", json=_payload())

    assert response.status_code == 201
    assert (tmp_path / "sessions" / "meeting_001.json").is_file()
